"""Exercise the plugin routes beneath the real Hermes dashboard HTTP middleware."""
import asyncio

import httpx
import pytest
from fastapi import FastAPI

from dashboard import plugin_api


def test_views_routes_inherit_hermes_auth_host_and_cors(monkeypatch):
    web = pytest.importorskip('hermes_cli.web_server')
    app = web.app
    holder = FastAPI()
    holder.include_router(plugin_api.router, prefix='/api/plugins/on-air')
    paths = [r for r in holder.router.routes if getattr(r, 'path', '').startswith('/api/plugins/on-air/')]
    monkeypatch.setattr(app.router, 'routes', paths + list(app.router.routes))
    monkeypatch.setattr(app.state, 'bound_host', '127.0.0.1', raising=False)
    monkeypatch.setattr(app.state, 'auth_required', False, raising=False)
    async def admitted(_name): return False
    async def no_secondary(_request, _name): return None
    async def views(_fn): return {'ok': True}
    monkeypatch.setattr(web, '_plugin_api_request_refused', admitted)
    monkeypatch.setattr(web, 'forward_secondary_plugin_request', no_secondary)
    monkeypatch.setattr(plugin_api, '_views_run', views)
    monkeypatch.setattr(plugin_api, '_views_stage_home', lambda: False)
    monkeypatch.setattr(plugin_api, '_forward', lambda *args: (200, {'ok': True, 'queued': True, 'position': 1}))
    requests = [('GET', 'views/state', None), ('POST', 'views/scene', {'scene':'test'}),
                ('POST', 'views/layout', {'preset':'presentation'}), ('POST', 'views/window', {'action':'discover'}),
                ('POST', 'views/boxes', {'op':'swap', 'a':'main', 'b':'sub1'}),
                ('POST', 'host/turn', {'text':'Hello', 'epoch':1})]
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1') as c:
            for method, path, body in requests:
                url = '/api/plugins/on-air/' + path
                assert (await c.request(method, url, json=body)).status_code == 401, path
                headers = {web._SESSION_HEADER_NAME: web._SESSION_TOKEN}
                assert (await c.request(method, url, json=body, headers={**headers, 'Host':'example.invalid'})).status_code == 400, path
                assert (await c.request(method, url, json=body, headers=headers)).status_code == 200, path
                preflight = await c.options(url, headers={'Origin':'https://example.invalid', 'Access-Control-Request-Method':method,
                                                         'Access-Control-Request-Headers':web._SESSION_HEADER_NAME})
                assert preflight.status_code == 400, path
    asyncio.run(run())
