"""Privacy proof follows the configured overlay's real event stream."""
import asyncio
import json
import time
from urllib.request import urlopen

from conftest import make
from onair.overlay.server import EventHub, OverlayServer, ev_state
from onair.presentation import agent


def wait_for(predicate):
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.02)
    assert predicate()


def test_overlay_privacy_and_disconnect_hide_work(obs):
    ctx, _ = make(obs.url)
    rt = agent.runtime()
    hub = EventHub()
    state = {'live': True, 'privacy': False}
    hub.snapshot = lambda: ev_state(state['live'] and not state['privacy'], 'approval' if state['live'] else 'off_air', state['privacy'], False)
    overlay = OverlayServer(hub, lambda _id: None, port=0)
    rt.call(overlay.start())
    rt.obs.cfg.overlay_url = overlay.overlay_url
    handler = ctx.tools['onair_work_share']['handler']
    shown = json.loads(handler({'action': 'show', 'title': 'Fix test', 'panes': [
        {'kind': 'text', 'label': 'Output', 'content': 'public example'}]}))
    assert shown['ok']
    wait_for(lambda: rt.program.public()['mode'] == 'work')
    with urlopen(shown['surface']['url'] + '/state') as response:
        assert json.load(response)['work']['panes'][0]['content'] == 'public example'

    async def change(privacy):
        state['privacy'] = privacy
        hub.publish(ev_state(not privacy, 'approval', privacy, False), urgent=privacy)
    rt.call(change(True))
    wait_for(lambda: rt.program.public()['mode'] == 'privacy')
    with urlopen(shown['surface']['url'] + '/state') as response:
        snapshot = json.load(response)
        assert snapshot['mode'] == 'privacy' and snapshot['work'] is None
    rt.call(change(False))
    wait_for(lambda: rt.program.public()['mode'] == 'work')
    original = rt.obs.cfg.overlay_url
    rt.obs.cfg.overlay_url = 'http://127.0.0.1:9/overlay'
    read = json.loads(handler({'action': 'read'}))
    assert read['program']['mode'] == 'privacy' and read['program']['work'] is None
    rt.obs.cfg.overlay_url = original
    wait_for(lambda: rt.program.public()['mode'] == 'work')
    rt.call(overlay.stop())
    wait_for(lambda: rt.program.public()['mode'] == 'privacy')
    assert rt.program.public()['work'] is None
