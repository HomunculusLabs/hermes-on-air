"""The desk's per-home release preference is private and locked by owner settings."""
import asyncio
import json
import os

import httpx
from fastapi import FastAPI
from dashboard import plugin_api


def test_release_mode_is_per_home_strict_and_locked(tmp_path, monkeypatch):
    home = tmp_path / 'one'
    other = tmp_path / 'two'
    home.mkdir(); other.mkdir()
    monkeypatch.setenv('HERMES_HOME', str(home))
    setting = {'host_mic_review':False}
    monkeypatch.setattr(plugin_api._ViewsConfig, 'get_config', lambda self, key: setting.get(key))
    app = FastAPI(); app.include_router(plugin_api.router, prefix='/api/plugins/on-air')
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as c:
            path = '/api/plugins/on-air/host/mic/mode'
            state = (await c.get(path)).json()
            assert state['mode'] == 'review' and not state['locked']
            duplicate_type = [('content-type','application/json'), ('content-type','application/json')]
            assert (await c.post(path, content=b'{"mode":"send"}', headers=duplicate_type)).status_code == 400
            for raw in (b'{}', b'{"mode":"send","extra":true}', b'{"mode":"send","mode":"review"}',
                        b'{"mode":false}', b'{"mode":"handsfree"}', b'[]', b'not json'):
                assert (await c.post(path, content=raw, headers={'content-type':'application/json'})).status_code == 400
            assert (await c.post(path, json={'mode':'send'})).json()['mode'] == 'send'
            stored = home / 'plugin-data/on-air/host-mic-mode.json'
            assert json.loads(stored.read_text()) == {'mode':'send'}
            assert os.stat(stored).st_mode & 0o077 == 0
            monkeypatch.setenv('HERMES_HOME', str(other))
            assert (await c.get(path)).json()['mode'] == 'review'
            monkeypatch.setenv('HERMES_HOME', str(home))
            setting['host_mic_review'] = True
            locked = (await c.get(path)).json()
            assert locked['mode'] == 'review' and locked['locked'] and locked['reason']
            assert (await c.post(path, json={'mode':'send'})).status_code == 403
            assert json.loads(stored.read_text()) == {'mode':'send'}
            setting['host_mic_review'] = False
            assert (await c.get(path)).json()['mode'] == 'send'
            stored.write_text('{"mode":"review","mode":"send"}')
            assert (await c.get(path)).json()['mode'] == 'review'
    asyncio.run(run())
