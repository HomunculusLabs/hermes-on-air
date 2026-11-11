"""Observed boxes and safe stack layout, exercised with the OBS double."""
import pytest

from onair.presentation import agent, boxes
from test_boxes import box_call
from test_obs_control import call, served


@pytest.mark.parametrize('width,height', [(1920, 1080), (1280, 720)])
@pytest.mark.parametrize('corner', ['bottom-right', 'top-right', 'top-left', 'bottom-left'])
def test_stack_corners_stay_clear_of_caption_band_and_each_other(width, height, corner):
    geo = boxes.geometry('stack', width, height, {'main': 'slides', 'sub1': 'scene', 'sub2': 'window'}, corner)
    a, b = geo['sub1'], geo['sub2']
    for rect in (a, b):
        assert rect['x'] >= 0 and rect['y'] >= 0
        assert rect['x'] + rect['w'] <= width
        assert rect['y'] + rect['h'] <= height * 830 / 1080
        assert rect['w'] < width * .27
        assert (rect['x'] < width / 2) == corner.endswith('left')
    assert a['y'] + a['h'] <= b['y'] or b['y'] + b['h'] <= a['y']
    assert (a['y'] > b['y']) == corner.startswith('bottom')


def test_readback_move_and_stored_arrangement(obs):
    ctx = served(obs, scene_view=True)
    assert call(ctx, 'onair_obs_setup', {'action': 'configure'})['ok']
    initial = box_call(ctx, 'set', main='slides', sub1='scene', sub2='empty', arrangement='stack', inset_corner='top-left')
    assert initial['ok'], initial
    state = initial['boxes']
    assert state['canvas'] == {'width': 1920, 'height': 1080}
    assert state['overlay_topmost'] is True
    assert state['arrangement_source'] == 'readback' and state['inset_corner'] == 'top-left'
    assert state['geometry']['main']['source'] == 'slides'
    assert state['geometry']['main']['bounds'] == {'x': 0.0, 'y': 0.0, 'width': 1920.0, 'height': 1080.0}
    assert state['geometry']['main']['content'] == state['geometry']['main']['bounds']
    assert state['geometry']['sub1']['enabled'] is True
    assert state['geometry']['sub1']['z_index'] > state['geometry']['main']['z_index']
    assert state['geometry']['sub2'] == {'source': 'empty', 'enabled': False, 'bounds': None, 'content': None, 'z_index': None}
    moved = box_call(ctx, 'put', slot='sub2', source='scene')
    assert moved['ok'] and moved['move'] == {'source': 'scene', 'from': 'sub1', 'to': 'sub2'}
    assert moved['message'] == 'Scene moved from Sub 1 to Sub 2; Sub 1 is now empty.'
    emptied = box_call(ctx, 'put', slot='sub2', source='empty')
    assert emptied['ok'] and emptied['boxes']['arrangement'] == 'stack'
    assert emptied['boxes']['inset_corner'] == 'top-left'
    assert emptied['boxes']['arrangement_source'] == 'stored'
    read = box_call(ctx, 'boxes')['boxes']
    assert read['arrangement_source'] == 'stored' and read['inset_corner'] == 'top-left'
    assert not box_call(ctx, 'arrangement', arrangement='stack', inset_corner='middle')['ok']


def test_stored_side_and_corner_survive_empty_readback(obs):
    ctx = served(obs, scene_view=True)
    assert call(ctx, 'onair_obs_setup', {'action': 'configure'})['ok']
    out = box_call(ctx, 'set', main='slides', sub1='empty', sub2='empty', arrangement='side', inset_corner='bottom-left')
    assert out['ok'], out
    assert out['boxes']['arrangement_ambiguous'] is True
    state = box_call(ctx, 'boxes')['boxes']
    assert (state['arrangement'], state['arrangement_source']) == ('side', 'stored')
    assert (state['inset_corner'], state['inset_corner_source']) == ('bottom-left', 'stored')
    out = box_call(ctx, 'put', slot='sub1', source='scene')
    assert out['ok'] and out['boxes']['arrangement'] == 'side'
    assert out['boxes']['arrangement_source'] == 'readback'
    assert out['boxes']['inset_corner_source'] == 'stored'


def test_readback_content_unknown_without_frames(obs):
    ctx = served(obs, scene_view=True)
    assert call(ctx, 'onair_obs_setup', {'action': 'configure'})['ok']
    cfg = agent.runtime().obs.cfg
    obs.frames[cfg.scene_view_input] = (0, 0)
    out = box_call(ctx, 'set', main='slides', sub1='scene', sub2='empty', arrangement='stack')
    assert out['ok'] and out['boxes']['geometry']['sub1']['content'] is None


def test_readback_content_uses_observed_source_aspect(obs):
    ctx = served(obs, scene_view=True)
    assert call(ctx, 'onair_obs_setup', {'action': 'configure'})['ok']
    cfg = agent.runtime().obs.cfg
    obs.frames[cfg.scene_view_input] = (4, 3)
    out = box_call(ctx, 'set', main='slides', sub1='scene', sub2='empty', arrangement='stack')
    assert out['ok'], out
    item = out['boxes']['geometry']['sub1']
    bounds, content = item['bounds'], item['content']
    assert content['width'] / content['height'] == pytest.approx(4 / 3, abs=.01)
    assert content['x'] >= bounds['x'] and content['y'] >= bounds['y']
    assert content['x'] + content['width'] <= bounds['x'] + bounds['width'] + .01


def test_window_refusals_describe_actual_cause(obs):
    ctx = served(obs, scene_view=True)
    assert call(ctx, 'onair_obs_setup', {'action': 'configure'})['ok']
    obs.kinds = ['browser_source']
    out = box_call(ctx, 'set', main='slides', sub1='window', sub2='empty', arrangement='stack')
    assert not out['ok'] and 'xcomposite_input' in out['next_human_action'] and 'PipeWire' in out['next_human_action']
    obs.kinds = ['browser_source', 'pipewire-screen-capture-source']
    assert call(ctx, 'onair_window_share', {'action': 'prepare'})['ok']
    out = box_call(ctx, 'set', main='slides', sub1='window', sub2='empty', arrangement='stack')
    assert not out['ok'] and 'window' in out['next_human_action'].lower() and 'dialog' in out['next_human_action'].lower()
    cfg = agent.runtime().obs.cfg
    obs.frames[cfg.window_input] = (1280, 720)
    obs.inputs[cfg.window_input]['settings']['RestoreToken'] = 'chosen'
    out = box_call(ctx, 'set', main='slides', sub1='window', sub2='empty', arrangement='stack')
    assert not out['ok'] and 'confirm-window' in out['next_human_action']
    obs.platform = 'other'
    obs.inputs.pop(cfg.window_input)
    out = box_call(ctx, 'set', main='slides', sub1='window', sub2='empty', arrangement='stack')
    assert not out['ok'] and 'xcomposite_input' in out['next_human_action']
    assert 'supported' in out['next_human_action'] and 'onair_present_publish' in out['next_human_action']


def test_swap_places_incoming_main_below_outgoing_before_resizing(obs):
    ctx = served(obs, scene_view=True)
    assert call(ctx, 'onair_obs_setup', {'action': 'configure'})['ok']
    assert box_call(ctx, 'set', main='slides', sub1='scene', sub2='empty', arrangement='stack')['ok']
    cfg = agent.runtime().obs.cfg
    incoming = obs.item(boxes.SCENE, cfg.scene_view_input)['sceneItemId']
    outgoing = obs.item(boxes.SCENE, cfg.presentation_input)['sceneItemId']
    start = len(obs.requests())
    assert box_call(ctx, 'swap', a='main', b='sub1')['ok']
    requests = obs.requests()[start:]
    below = next(i for i, (k, d) in enumerate(requests) if k == 'SetSceneItemIndex' and d.get('sceneItemId') == incoming and d.get('sceneItemIndex') == 0)
    expand = next(i for i, (k, d) in enumerate(requests) if k == 'SetSceneItemTransform' and d.get('sceneItemId') == incoming)
    shrink = next(i for i, (k, d) in enumerate(requests) if k == 'SetSceneItemTransform' and d.get('sceneItemId') == outgoing)
    assert below < expand < shrink
    assert not any(k == 'SetSceneItemEnabled' and d.get('sceneItemId') in (incoming, outgoing) and not d['sceneItemEnabled'] for k, d in requests)
