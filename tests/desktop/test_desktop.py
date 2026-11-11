"""Offline Desktop contract checks; no running Desktop or backend is used."""
import ast
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_node_owner():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is not installed; Desktop module tests need Node')
    subprocess.run([node, '--experimental-vm-modules', str(ROOT / 'tests/desktop/owner.cjs')], check=True, timeout=30)


def test_node_ui():
    node = shutil.which('node')
    sdk = os.environ.get('ONAIR_HERMES_SDK')
    if not node:
        pytest.skip('Node is not installed; Desktop UI tests need Node')
    if not sdk or not all((Path(sdk) / 'node_modules' / name).exists() for name in ('react', 'react-dom', 'jsdom')):
        pytest.skip('Desktop UI tests need React and jsdom in ONAIR_HERMES_SDK')
    subprocess.run([node, '--experimental-vm-modules', str(ROOT / 'tests/desktop/ui.cjs')], check=True, timeout=30)


def test_visual_harness():
    # Opt-in: launching Chromium is only exercised when the runner environment explicitly allows it
    # (ONAIR_VISUAL=1 plus ONAIR_CHROME/ONAIR_PLAYWRIGHT); otherwise the harness is a manual tool.
    node = shutil.which('node')
    sdk = os.environ.get('ONAIR_HERMES_SDK')
    chrome = os.environ.get('ONAIR_CHROME')
    playwright_dir = os.environ.get('ONAIR_PLAYWRIGHT') or (str(Path(sdk) / 'node_modules') if sdk else None)
    if (os.environ.get('ONAIR_VISUAL') != '1' or not node or not sdk or not chrome or not playwright_dir
            or not Path(chrome).exists() or not (Path(playwright_dir) / 'playwright').exists()
            or not (Path(sdk) / 'node_modules' / 'esbuild').exists()):
        pytest.skip('Visual harness needs ONAIR_VISUAL=1, Node, ONAIR_HERMES_SDK with esbuild, a playwright install and ONAIR_CHROME')
    out = Path(os.environ.get('TMPDIR', '.')) / 'onair-visual-shots'
    subprocess.run([node, str(ROOT / 'tests/desktop/visual_harness.cjs'), str(out)], check=True, timeout=300)
    report = json.loads((out / 'report.json').read_text())
    files = {Path(r['file']).name for r in report['records']}
    assert len(report['records']) == 28 == len(files)
    assert all((out / name).exists() for name in files)
    assert len(report['checks']) == 10
    for record in report['records']:
        m = record['metrics']
        assert m['scroll'] <= m['w']
        assert m['root']['height'] >= m['h'] - 1
        if len(m['queue']) > 1:
            assert max(x['x'] for x in m['queue']) - min(x['x'] for x in m['queue']) < 1


def test_desktop_routes_exist_in_backend():
    tree = ast.parse((ROOT / 'dashboard/plugin_api.py').read_text())
    routes = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) and node.func.value.id == 'router' and node.args and isinstance(node.args[0], ast.Constant):
            routes.add((node.func.attr.upper(), node.args[0].value))
    expected = {('GET', '/state'), ('GET', '/pending'), ('GET', '/views/state'), *{('POST', p) for p in ('/approve', '/reject', '/mode', '/privacy', '/mute', '/stop', '/cut-in', '/host/turn', '/views/boxes', '/views/scene', '/views/layout', '/views/window')}}
    assert expected <= routes
    source = (ROOT / 'desktop/plugin.js').read_text()
    for _, route in expected:
        if route in ('/approve', '/reject', '/privacy', '/mute', '/stop'):
            continue  # dynamically composed by the show controls
        assert repr(route) in source
    assert not re.search(r'https?://|\b(?:token|port)\b', source, re.I)


def test_desktop_requests_against_real_routes(tmp_path, monkeypatch):
    """Replay actual UI requests through the dashboard and its control validator."""
    import asyncio
    import httpx
    from fastapi import FastAPI
    from dashboard import plugin_api
    from onair.control import ControlServer
    from onair.stage import StageConfig, StageController

    node = shutil.which('node')
    sdk = os.environ.get('ONAIR_HERMES_SDK')
    if not node or not sdk or not all((Path(sdk) / 'node_modules' / name).exists() for name in ('react', 'react-dom', 'jsdom')):
        pytest.skip('Desktop UI request capture needs Node, React and jsdom')
    receipt = tmp_path / 'calls.json'
    env = {**os.environ, 'ONAIR_DESKTOP_CALLS': str(receipt)}
    subprocess.run([node, '--experimental-vm-modules', str(ROOT / 'tests/desktop/ui.cjs')], env=env, check=True, timeout=30)
    calls = json.loads(receipt.read_text())
    posts = [c for c in calls if c['method'] == 'POST']
    shapes = {'/approve': {'line_id', 'epoch'}, '/reject': {'line_id', 'epoch'},
              '/mode': {'mode'}, '/privacy': {'on'}, '/mute': {'on'}, '/stop': set(), '/cut-in': {'epoch', 'boot'},
              '/host/turn': {'text', 'epoch', 'boot', 'id'}, '/views/scene': {'scene'},
              '/views/layout': {'preset'}, '/views/window': {'action'}}
    assert set(shapes) | {'/views/boxes'} == {c['p'] for c in posts}
    for call in posts:
        if call['p'] in shapes:
            assert set(call['body']) == shapes[call['p']]
    app = FastAPI()
    app.include_router(plugin_api.router, prefix='/api/plugins/on-air')
    async def views_stub(fn):
        return {'ok': True}
    monkeypatch.setattr(plugin_api, '_views_run', views_stub)
    monkeypatch.setattr(plugin_api, '_views_stage_home', lambda: False)

    async def run():
        controller = StageController(StageConfig(host_turns=True))
        controller.set_mode('approval')
        server = ControlServer(controller, None)
        await server.start()
        monkeypatch.setattr(plugin_api, '_discovery', lambda: (server.http.port, server.token, ''))
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
                for call in calls:
                    response = await client.request(call['method'], '/api/plugins/on-air' + call['p'],
                                                    json=call.get('body') if call['method'] == 'POST' else None)
                    assert response.status_code not in (400, 404, 405, 422, 503), (call, response.status_code, response.text)
                # The captured id, replayed twice against the real queue, admits one turn.
                controller.set_privacy(False)
                controller.set_mode('approval')
                body = next(c['body'] for c in posts if c['p'] == '/host/turn').copy()
                body['epoch'] = controller.state.epoch
                body['boot'] = controller.voice_boot_id
                first = await client.post('/api/plugins/on-air/host/turn', json=body)
                second = await client.post('/api/plugins/on-air/host/turn', json=body)
                assert first.json()['queued'] is True
                assert second.json()['reason'] == 'duplicate'
                assert controller.snapshot()['host_turns']['waiting'] == 1
                bad = await client.post('/api/plugins/on-air/views/boxes', json={'op':'swap','a':'main','b':'sub1','inset_corner':'top-left'})
                assert bad.status_code == 400
        finally:
            await server.stop()
    asyncio.run(run())
