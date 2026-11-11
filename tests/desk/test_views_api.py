import asyncio
import json
import sys
from pathlib import Path

import httpx
from fastapi import FastAPI
from dashboard import plugin_api

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'presentation'))
from fake_obs import FakeObs
from onair.presentation.obs_control import ObsConfig, ObsControl

BASE = '/api/plugins/on-air'


def test_views_fixed_routes_unreachable_and_no_password(monkeypatch, caplog):
    secret = 'password-canary-397'
    monkeypatch.setenv('ONAIR_OBS_PASSWORD', secret)
    fake = FakeObs(password='different').start()
    monkeypatch.setattr(plugin_api, '_views_controller', lambda: ObsControl(ObsConfig(url=fake.url, password=lambda: secret), lambda: ''))
    monkeypatch.setattr(plugin_api, '_views_stage_home', lambda: False)
    app = FastAPI()
    app.include_router(plugin_api.router, prefix=BASE)
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as c:
            state = await c.get(BASE + '/views/state')
            assert not state.json()['ok'] and not state.json()['connected']
            assert 'password' in state.json()['error']
            for path, body in [('scene', {'scene': 'no'}), ('layout', {'preset': 'no'}),
                               ('window', {'action': 'prepare'}), ('boxes', {'op': 'swap', 'a': 'main', 'b': 'sub1'})]:
                r = await c.post(BASE + '/views/' + path, json=body)
                assert secret not in r.text
            assert (await c.post(BASE + '/views/confirm', json={})).status_code == 404
            assert (await c.get(BASE + '/views/scene')).status_code == 405
            assert secret not in state.text
    try:
        asyncio.run(run())
        assert secret not in caplog.text
    finally:
        fake.stop()


def test_views_actions_call_owned_runtime_and_read_back(monkeypatch):
    fake = FakeObs().start()
    module = plugin_api._onair('presentation.obs_control')
    ctl = module.ObsControl(module.ObsConfig(url=fake.url), lambda: 'http://127.0.0.1:12345/present')
    monkeypatch.setattr(plugin_api, '_views_controller', lambda: ctl)
    monkeypatch.setattr(plugin_api, '_views_stage_home', lambda: False)
    app = FastAPI()
    app.include_router(plugin_api.router, prefix=BASE)
    async def run():
        assert (await ctl.configure())['ok']
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as c:
            applied = (await c.post(BASE + '/views/boxes', json={'op':'set', 'main':'slides', 'sub1':'empty',
                                                                  'sub2':'empty', 'arrangement':'stack'})).json()
            assert applied['ok'] and applied['boxes']['slots']['main'] == 'slides'
            corner = (await c.post(BASE + '/views/boxes', json={'op':'arrangement', 'arrangement':'stack',
                                                                 'inset_corner':'top-left'})).json()
            assert corner['ok'] and corner['boxes']['inset_corner'] == 'top-left', corner
            bad = await c.post(BASE + '/views/boxes', json={'op':'arrangement', 'arrangement':'stack',
                                                            'inset_corner':'middle'})
            assert not bad.json()['ok']
            extra = await c.post(BASE + '/views/boxes', json={'op':'swap', 'a':'main', 'b':'sub1',
                                                              'inset_corner':'top-left'})
            assert extra.status_code == 400
            switched = (await c.post(BASE + '/views/scene', json={'scene':'On Air: Boxes'})).json()
            assert switched['ok'] and switched['program_scene'] == fake.program == 'On Air: Boxes'
            preset = (await c.post(BASE + '/views/layout', json={'preset':'presentation'})).json()
            assert preset['ok'] and preset['applied']
    try:
        asyncio.run(run())
        assert not fake.requests('StartStream') and not fake.requests('StopStream')
    finally:
        fake.stop()


def test_views_incomplete_obs_readback_is_not_success(monkeypatch):
    fake = FakeObs().start()
    module = plugin_api._onair('presentation.obs_control')
    ctl = module.ObsControl(module.ObsConfig(url=fake.url), lambda: '')
    monkeypatch.setattr(plugin_api, '_views_controller', lambda: ctl)
    app = FastAPI()
    app.include_router(plugin_api.router, prefix=BASE)
    fake.refuse['GetStreamStatus'] = 701
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as c:
            state = (await c.get(BASE + '/views/state')).json()
            assert not state['ok'] and state['connected'] is True
            assert 'refused' in state['error'] and 'stream' not in state
    try:
        asyncio.run(run())
    finally:
        fake.stop()


def test_views_refuse_confirmation_bypass_and_extra_fields(monkeypatch):
    fake = FakeObs().start()
    module = plugin_api._onair('presentation.obs_control')
    ctl = module.ObsControl(module.ObsConfig(url=fake.url), lambda: '')
    monkeypatch.setattr(plugin_api, '_views_controller', lambda: ctl)
    monkeypatch.setattr(plugin_api, '_views_stage_home', lambda: False)
    app = FastAPI()
    app.include_router(plugin_api.router, prefix=BASE)
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as c:
            state = (await c.get(BASE + '/views/state')).json()
            assert state['connected'] and state['stream']['active'] is False
            assert state['window_state'] == 'none'
            fake.add_input(ctl.cfg.window_scene, ctl.cfg.window_input, 'pipewire-screen-capture-source',
                           {'RestoreToken': 'selected'}, frames=(1280, 720))
            fake.program = ctl.cfg.window_scene
            showing = (await c.get(BASE + '/views/state')).json()
            assert showing['window_state'] == 'showing'
            fake.program = 'Scene'
            chosen = (await c.get(BASE + '/views/state')).json()
            assert chosen['window_state'] == 'chosen_confirmation_unknown'
            refusal = (await c.post(BASE + '/views/window', json={'action':'show'})).json()
            assert not refusal['ok'] and 'confirm-window' in refusal['next_human_action']
            assert not (await c.post(BASE + '/views/scene', json={'scene': 'not approved'})).json()['ok']
            for action in ('prepare', 'open_chooser'):
                r = (await c.post(BASE + '/views/window', json={'action': action})).json()
                assert not r['ok'] and 'onair_window_share' in r['error']
            r = await c.post(BASE + '/views/window', json={'action': 'show', 'approved': True})
            assert r.status_code == 400
            assert not (await c.post(BASE + '/views/boxes', json={'op': 'confirm'})).json()['ok']
    try:
        asyncio.run(run())
        assert not fake.requests('StartStream') and not fake.requests('StopStream')
        assert not fake.requests('SetCurrentProgramScene')
    finally:
        fake.stop()
