"""Boxes reuse the presentation browser input for work, not another OBS source."""
import json
import time
from urllib.request import urlopen

from onair.overlay.server import EventHub, OverlayServer, ev_state
from onair.presentation import agent
from test_obs_control import call, served


def test_slides_sub_box_contains_the_work_surface(obs):
    ctx = served(obs, scene_view=True)
    rt = agent.runtime()
    hub = EventHub()
    hub.snapshot = lambda: ev_state(True, 'approval', False, False)
    overlay = OverlayServer(hub, lambda _id: None, port=0)
    rt.call(overlay.start())
    rt.obs.cfg.overlay_url = overlay.overlay_url
    try:
        work = call(ctx, 'onair_work_share', {'action': 'show', 'title': 'Working', 'panes': [
            {'kind': 'text', 'label': 'Output', 'content': 'safe sample'}]})
        assert work['ok']
        deadline = time.monotonic() + 4
        while rt.program.public()['mode'] != 'work' and time.monotonic() < deadline:
            time.sleep(.02)
        assert call(ctx, 'onair_obs_setup', {'action': 'configure'})['ok']
        result = call(ctx, 'onair_layout', {'action': 'set', 'main': 'scene', 'sub1': 'slides',
                                             'sub2': 'empty', 'arrangement': 'side'})
        assert result['ok'] and result['boxes']['slots']['sub1'] == 'slides'
        source = obs.inputs[rt.obs.cfg.presentation_input]['settings']
        assert source['url'] == work['surface']['url']
        with urlopen(source['url'] + '/state') as response:
            snapshot = json.load(response)
        assert snapshot['mode'] == 'work' and snapshot['work']['panes'][0]['content'] == 'safe sample'
    finally:
        rt.call(overlay.stop())
