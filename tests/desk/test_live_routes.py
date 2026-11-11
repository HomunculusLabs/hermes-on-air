"""Capture Live UI requests and replay its writes at the existing dashboard routes."""
import asyncio
import json
import os
from pathlib import Path
import shutil
import subprocess
from urllib.parse import urlsplit

import httpx
import pytest
from fastapi import FastAPI

from dashboard import plugin_api
from onair.control import ControlServer
from onair.stage import StageConfig, StageController

ROOT = Path(__file__).resolve().parents[2]


def test_live_requests_are_fixed_and_valid(tmp_path, monkeypatch):
    node = shutil.which('node')
    sdk = os.environ.get('ONAIR_HERMES_SDK')
    if not node or not sdk or not all((Path(sdk) / 'node_modules' / m).is_dir() for m in ('react', 'react-dom', 'jsdom')):
        pytest.skip('Node and dashboard dependencies required')
    receipt = tmp_path / 'calls.json'
    env = dict(os.environ, NODE_PATH=str(Path(sdk) / 'node_modules'), ONAIR_LIVE_ROUTE_RECEIPT=str(receipt))
    subprocess.run([node, str(ROOT / 'tests/desk/test_live_routes.cjs')], cwd=ROOT, env=env, check=True, timeout=45)
    calls = json.loads(receipt.read_text())
    shapes = {'approve': {'line_id', 'epoch'}, 'reject': {'line_id', 'epoch'}, 'mode': {'mode'},
              'privacy': {'on'}, 'mute': {'on'}, 'stop': set(), 'cut-in': {'epoch', 'boot'},
              'host/turn': {'text', 'epoch', 'boot', 'id'}, 'views/boxes': {'op', 'a', 'b'}}
    gets = {'state', 'pending', 'receipts', 'views/state', 'host/mic', 'host/mic/mode'}
    assert {c['route'] for c in calls if c['method'] == 'POST'} == set(shapes)
    assert {urlsplit(c['route']).path for c in calls if c['method'] == 'GET'} == gets
    declared = {(method, route.path.lstrip('/')) for route in plugin_api.router.routes for method in route.methods}
    for call in calls:
        assert (call['method'], urlsplit(call['route']).path) in declared
        if call['method'] == 'POST':
            assert set(call['body']) == shapes[call['route']]
    assert next(c['body'] for c in calls if c['route'] == 'views/boxes') == {'op':'swap','a':'main','b':'sub1'}
    app = FastAPI()
    app.include_router(plugin_api.router, prefix='/api/plugins/on-air')
    async def views_stub(fn):
        return {'ok': True}
    monkeypatch.setattr(plugin_api, '_views_run', views_stub)
    monkeypatch.setattr(plugin_api, '_views_stage_home', lambda: False)

    async def replay():
        controller = StageController(StageConfig(host_turns=True))
        controller.set_mode('approval')
        server = ControlServer(controller, None)
        await server.start()
        monkeypatch.setattr(plugin_api, '_discovery', lambda: (server.http.port, server.token, ''))
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
                for call in calls:
                    if call['method'] != 'POST':
                        continue
                    response = await client.post('/api/plugins/on-air/' + call['route'], json=call['body'])
                    # A stale binding or missing pending line is expected when replayed against a fresh stage.
                    assert response.status_code not in (400, 405, 422, 503), (call, response.status_code, response.text)
                    if response.status_code == 404:
                        assert call['route'] in ('approve', 'reject') and 'pending' in response.text
                controller.set_privacy(False)
                controller.set_mode('approval')
                host = next(c['body'] for c in calls if c['route'] == 'host/turn').copy()
                host['epoch'], host['boot'] = controller.state.epoch, controller.voice_boot_id
                first = (await client.post('/api/plugins/on-air/host/turn', json=host)).json()
                second = (await client.post('/api/plugins/on-air/host/turn', json=host)).json()
                assert first['queued'] is True and second['reason'] == 'duplicate'
        finally:
            await server.stop()
    asyncio.run(replay())
