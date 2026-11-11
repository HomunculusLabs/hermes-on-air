import asyncio
import json
import httpx
from fastapi import FastAPI
from dashboard import plugin_api


def test_cut_proxy_is_fixed_and_requires_strict_body(monkeypatch):
    calls = []
    monkeypatch.setattr(plugin_api, '_forward', lambda *args: (calls.append(args) or (200, {'ok': True})))
    app = FastAPI()
    app.include_router(plugin_api.router, prefix='/api/plugins/on-air')
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            url = '/api/plugins/on-air/cut-in'
            for body in ({}, {'epoch': True, 'boot': 'a'*32}, {'epoch': 1},
                         {'epoch': 1, 'boot': 'bad'}, {'epoch': 1, 'boot': 'a'*32, 'extra': 1}):
                assert (await client.post(url, json=body)).status_code == 400
            assert (await client.post(url, content=b'[["epoch",1],["boot","' + b'a'*32 + b'"]]')).status_code == 400
            assert (await client.post(url, content=b'{"epoch":1,"epoch":2,"boot":"' + b'a'*32 + b'"}')).status_code == 400
            assert not calls
            assert (await client.post(url, json={'epoch': 1, 'boot': 'a'*32})).status_code == 200
            assert (await client.post(url + '/elsewhere', json={})).status_code == 404
            assert (await client.get(url)).status_code == 405
    asyncio.run(run())
    assert len(calls) == 1 and calls[0][:3] == ('POST', '/cut-in', '')
    assert json.loads(calls[0][3]) == {'epoch': 1, 'boot': 'a'*32}
