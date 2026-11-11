"""Host mic inherits the real dashboard's authentication and origin boundary."""
import asyncio

import httpx
import pytest
from fastapi import FastAPI
from dashboard import plugin_api


def test_mic_auth_host_and_origin(monkeypatch):
    web = pytest.importorskip('hermes_cli.web_server')
    app = web.app
    holder = FastAPI(); holder.include_router(plugin_api.router, prefix='/api/plugins/on-air')
    routes = [r for r in holder.router.routes if getattr(r, 'path', '').startswith('/api/plugins/on-air/')]
    monkeypatch.setattr(app.router, 'routes', routes + list(app.router.routes))
    monkeypatch.setattr(app.state, 'bound_host', '127.0.0.1', raising=False)
    monkeypatch.setattr(app.state, 'auth_required', False, raising=False)
    async def admitted(_name): return False
    async def no_secondary(_request, _name): return None
    monkeypatch.setattr(web, '_plugin_api_request_refused', admitted)
    monkeypatch.setattr(web, 'forward_secondary_plugin_request', no_secondary)
    monkeypatch.setattr(plugin_api._ViewsConfig, 'get_config', lambda self, key: None)
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1') as c:
            url = '/api/plugins/on-air/host/mic'
            assert (await c.post(url, content=b'audio')).status_code == 401
            headers = {web._SESSION_HEADER_NAME:web._SESSION_TOKEN}
            assert (await c.post(url, content=b'audio', headers={**headers,'Host':'example.invalid'})).status_code == 400
            assert (await c.post(url, content=b'audio', headers=headers)).json()['reason'] == 'disabled'
            bearer = {'Authorization':'Bearer ' + web._SESSION_TOKEN}
            assert (await c.post(url, content=b'audio', headers={'Authorization':'Bearer invalid'})).status_code == 401
            assert (await c.post(url, content=b'audio', headers=bearer)).json()['reason'] == 'disabled'
            assert (await c.post(url, content=b'audio', headers={**bearer,'Host':'example.invalid'})).status_code == 400
            result = await c.options(url, headers={'Origin':'https://example.invalid', 'Access-Control-Request-Method':'POST',
                                                   'Access-Control-Request-Headers':web._SESSION_HEADER_NAME})
            assert result.status_code == 400
            mode = '/api/plugins/on-air/host/mic/mode'
            assert (await c.get(mode)).status_code == 401
            assert (await c.post(mode, json={'mode':'send'})).status_code == 401
            assert (await c.get(mode, headers={**headers,'Host':'example.invalid'})).status_code == 400
            assert (await c.get(mode, headers=headers)).json()['locked'] is True
            assert (await c.post(mode, json={'mode':'send'}, headers=headers)).status_code == 403
            result = await c.options(mode, headers={'Origin':'https://example.invalid', 'Access-Control-Request-Method':'POST',
                                                   'Access-Control-Request-Headers':web._SESSION_HEADER_NAME})
            assert result.status_code == 400
            cut = '/api/plugins/on-air/cut-in'
            payload = {'epoch': 1, 'boot': 'a'*32}
            monkeypatch.setattr(plugin_api, '_forward', lambda *args: (200, {'ok': True}))
            assert (await c.post(cut, json=payload)).status_code == 401
            assert (await c.post(cut, json=payload, headers={**headers, 'Host': 'example.invalid'})).status_code == 400
            assert (await c.post(cut, json=payload, headers=headers)).json()['ok']
            result = await c.options(cut, headers={'Origin':'https://example.invalid', 'Access-Control-Request-Method':'POST',
                                                   'Access-Control-Request-Headers':web._SESSION_HEADER_NAME})
            assert result.status_code == 400
    asyncio.run(run())
