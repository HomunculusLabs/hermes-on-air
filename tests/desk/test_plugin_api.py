import asyncio
import json
import os

import pytest

fastapi = pytest.importorskip("fastapi", reason="dashboard stack not installed")
httpx = pytest.importorskip("httpx", reason="dashboard test client not installed")
FastAPI = fastapi.FastAPI

from dashboard.plugin_api import router  # noqa: E402
from onair.control import ControlServer
from onair.stage import ControlError, StageConfig, StageController


def app():
    a = FastAPI()
    a.include_router(router, prefix='/api/plugins/on-air')
    return a


def test_real_control_server(tmp_path, monkeypatch):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    async def run():
        data = tmp_path / 'plugin-data' / 'on-air'
        controller = StageController(StageConfig(), data_dir=data)
        server = ControlServer(controller, data)
        await server.start()
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app()), base_url='http://test') as c:
                base = '/api/plugins/on-air'
                state = await c.get(base + '/state')
                assert state.status_code == 200
                assert server.token not in state.text
                r = await c.post(base + '/privacy', json={'on': True})
                assert r.status_code == 200 and r.json()['state']['privacy'] is True
                assert (await c.get(base + '/pending')).status_code == 200
                assert (await c.get(base + '/receipts?limit=3')).status_code == 200
                assert (await c.post(base + '/mute', json={'on': 'true'})).status_code == 400
                assert (await c.get(base + '/alerts')).status_code == 200
                updated = await c.post(base + '/alerts', json={'min_bits': 50})
                assert updated.status_code == 200 and updated.json()['alerts']['min_bits'] == 50
                assert (await c.get(base + '/alerts/recent?limit=2')).status_code == 200
                assert (await c.post(base + '/mode', json={'mode': 'approval'})).status_code == 200
                for action in ('approve', 'reject'):
                    r = await c.post(base + '/' + action, json={'line_id': 'missing', 'epoch': 0})
                    assert r.status_code == 409 and server.token not in r.text
                assert (await c.post(base + '/mute', json={'on': True})).status_code == 200
                assert (await c.post(base + '/stop', json={})).status_code == 200
                assert (await c.get(base + '/mode')).status_code == 405
        finally:
            await server.stop()
    asyncio.run(run())


def test_runtime_clear_is_a_fixed_proxied_route(tmp_path, monkeypatch):
    """The desk button's route reaches the real control server's /runtime/clear with the token:
    a bare controller answers 404, an attached clear its 409 or its state; GET stays 405."""
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    async def run():
        data = tmp_path / 'plugin-data' / 'on-air'
        controller = StageController(StageConfig(), data_dir=data)
        server = ControlServer(controller, data)
        await server.start()
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app()), base_url='http://test') as c:
                url = '/api/plugins/on-air/runtime/clear'
                r = await c.post(url, json={})
                assert r.status_code == 404 and r.json() == {'ok': False, 'error': 'not found'}
                calls = []
                def clear():
                    calls.append(1)
                    if len(calls) == 1:
                        raise ControlError('the runtime gate is not latched', 409)
                    return controller.snapshot()
                controller.clear_runtime_violation = clear
                r = await c.post(url, json={})
                assert r.status_code == 409 and r.json()['error'] == 'the runtime gate is not latched'
                assert (await c.post(url, json={'force': True})).status_code == 400
                r = await c.post(url, json={})
                assert r.status_code == 200 and r.json()['ok'] is True and r.json()['state']['mode'] == 'off_air'
                assert server.token not in r.text
                assert (await c.get(url)).status_code == 405
                assert (await c.post('/api/plugins/on-air/runtime/other', json={})).status_code in (404, 405)
                assert len(calls) == 2
        finally:
            await server.stop()
    asyncio.run(run())


def test_missing_and_dead_control(tmp_path, monkeypatch):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app()), base_url='http://test') as c:
            assert (await c.get('/api/plugins/on-air/state')).status_code == 503
            d = tmp_path / 'plugin-data/on-air'
            d.mkdir(parents=True)
            (d / 'desk.token').write_text('secret-token')
            (d / 'control.json').write_text(json.dumps({'url':'http://127.0.0.1:1234','token_file':'desk.token','pid':99999999}))
            r = await c.get('/api/plugins/on-air/state')
            assert r.status_code == 503 and 'secret-token' not in r.text
    asyncio.run(run())


@pytest.mark.parametrize('url', ['http://localhost:1234', 'http://127.0.0.2:1234', 'http://127.0.0.1:1234/x', 'http://127.0.0.1:1234?x=y', 'http://user@127.0.0.1:1234', 'https://127.0.0.1:1234', 'http://127.0.0.1:1234#x'])
def test_bad_discovery_refused(tmp_path, monkeypatch, url):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    d = tmp_path / 'plugin-data/on-air'
    d.mkdir(parents=True)
    (d / 'desk.token').write_text('secret-token')
    (d / 'control.json').write_text(json.dumps({'url':url,'token_file':'desk.token','pid':os.getpid()}))
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app()), base_url='http://test') as c:
            r = await c.get('/api/plugins/on-air/state')
            assert r.status_code == 503 and 'secret-token' not in r.text
    asyncio.run(run())


def test_token_path_traversal_refused(tmp_path, monkeypatch):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    d = tmp_path / 'plugin-data/on-air'
    d.mkdir(parents=True)
    (d / 'control.json').write_text(json.dumps({'url':'http://127.0.0.1:1234','token_file':'../../secret','pid':os.getpid()}))
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app()), base_url='http://test') as c:
            assert (await c.get('/api/plugins/on-air/state')).status_code == 503
    asyncio.run(run())


def test_upstream_redaction_and_request_isolation(tmp_path, monkeypatch):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    async def run():
        captured = []
        token = 'test-secret-token'
        async def handle(reader, writer):
            raw = await reader.readuntil(b'\r\n\r\n')
            captured.append(raw.decode('ascii'))
            payload = json.dumps({'error': 'echo ' + token, token: [token]}).encode()
            writer.write(b'HTTP/1.1 409 Conflict\r\nContent-Type: application/json\r\nContent-Length: ' + str(len(payload)).encode() + b'\r\nConnection: close\r\n\r\n' + payload)
            await writer.drain()
            writer.close()
            await writer.wait_closed()
        server = await asyncio.start_server(handle, '127.0.0.1', 0)
        port = server.sockets[0].getsockname()[1]
        d = tmp_path / 'plugin-data/on-air'
        d.mkdir(parents=True)
        (d / 'desk.token').write_text(token)
        (d / 'control.json').write_text(json.dumps({'url':f'http://127.0.0.1:{port}','token_file':'desk.token','pid':os.getpid()}))
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app()), base_url='http://test') as c:
                r = await c.get('/api/plugins/on-air/receipts?limit=7', headers={'Origin':'http://evil', 'Authorization':'Bearer browser-secret'})
                assert r.status_code == 409 and token not in r.text
                assert r.json()['error'] == 'echo [redacted]'
                assert captured[0].startswith('GET /receipts?limit=7 HTTP/1.1')
                assert 'Origin:' not in captured[0] and 'browser-secret' not in captured[0]
                assert 'Bearer ' + token in captured[0]
                assert (await c.get('/api/plugins/on-air/arbitrary')).status_code == 404
        finally:
            server.close()
            await server.wait_closed()
    asyncio.run(run())


def test_bad_pid_and_symlink_token(tmp_path, monkeypatch):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    d = tmp_path / 'plugin-data/on-air'
    d.mkdir(parents=True)
    secret = tmp_path / 'private'
    secret.write_text('secret-token')
    (d / 'desk.token').symlink_to(secret)
    discovery = d / 'control.json'
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app()), base_url='http://test') as c:
            for pid in (True, 0, -1, '1', os.getpid()):
                discovery.write_text(json.dumps({'url':'http://127.0.0.1:1234','token_file':'desk.token','pid':pid}))
                r = await c.get('/api/plugins/on-air/state')
                assert r.status_code == 503 and 'secret-token' not in r.text
    asyncio.run(run())
