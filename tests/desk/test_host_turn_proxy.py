import asyncio
import json
import os

import httpx
from fastapi import FastAPI
from dashboard import plugin_api


def test_host_turn_is_fixed_proxy_and_preserves_stage_answer(monkeypatch):
    calls = []
    def forward(method, path, query, body):
        calls.append((method, path, query, json.loads(body)))
        return 409, {'ok': False, 'reason': 'stale_epoch', 'message': 'The show changed.'}
    monkeypatch.setattr(plugin_api, '_forward', forward)
    app = FastAPI()
    app.include_router(plugin_api.router, prefix='/api/plugins/on-air')
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as c:
            base = '/api/plugins/on-air'
            r = await c.post(base + '/host/turn', json={'text': 'Hello', 'epoch': 7})
            assert r.status_code == 409 and r.json()['message'] == 'The show changed.'
            assert (await c.get(base + '/host/turn')).status_code == 405
            assert (await c.post(base + '/host/other', json={})).status_code == 404
    asyncio.run(run())
    assert calls == [('POST', '/host/turn', '', {'text': 'Hello', 'epoch': 7})]


def test_host_turn_reaches_stub_stage_and_returns_message(tmp_path, monkeypatch):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    token = 'stub-stage-token'
    captured = []
    async def handle(reader, writer):
        headers = await reader.readuntil(b'\r\n\r\n')
        length = next(int(line.split(b':', 1)[1]) for line in headers.split(b'\r\n')
                      if line.lower().startswith(b'content-length:'))
        body = await reader.readexactly(length)
        captured.append((headers.decode(), json.loads(body)))
        payload = json.dumps({'ok': False, 'reason': 'stale_epoch', 'message': 'The show changed.'}).encode()
        writer.write(b'HTTP/1.1 409 Conflict\r\nContent-Type: application/json\r\nContent-Length: ' +
                     str(len(payload)).encode() + b'\r\nConnection: close\r\n\r\n' + payload)
        await writer.drain()
        writer.close()
        await writer.wait_closed()
    async def run():
        server = await asyncio.start_server(handle, '127.0.0.1', 0)
        directory = tmp_path / 'plugin-data/on-air'
        directory.mkdir(parents=True)
        (directory / 'desk.token').write_text(token)
        (directory / 'control.json').write_text(json.dumps({'url': f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}",
                                                            'token_file': 'desk.token', 'pid': os.getpid()}))
        app = FastAPI()
        app.include_router(plugin_api.router, prefix='/api/plugins/on-air')
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as c:
                r = await c.post('/api/plugins/on-air/host/turn', json={'text': 'Hello', 'epoch': 7})
                assert r.status_code == 409 and r.json()['message'] == 'The show changed.' and token not in r.text
        finally:
            server.close()
            await server.wait_closed()
    asyncio.run(run())
    assert captured[0][0].startswith('POST /host/turn HTTP/1.1')
    assert 'Bearer ' + token in captured[0][0]
    assert captured[0][1] == {'text': 'Hello', 'epoch': 7}
