"""Three owned boxes against a loopback OBS double, never a live output."""
import json

import pytest

from onair.presentation import agent, layout
from test_obs_control import call, served

SLOTS = {'main': 'slides', 'sub1': 'scene', 'sub2': 'empty'}


@pytest.mark.parametrize('width,height', [(1920, 1080), (1280, 720)])
@pytest.mark.parametrize('arrangement', ['stack', 'side'])
def test_geometry_is_bounded_and_empty_subs_expand_main(width, height, arrangement):
    full = layout.boxes_geometry(arrangement, width, height, {'main': 'slides', 'sub1': 'empty', 'sub2': 'empty'})
    assert full == {'main': {'x': 0, 'y': 0, 'w': width, 'h': height}}
    one_empty = layout.boxes_geometry(arrangement, width, height, {'main': 'slides', 'sub1': 'empty', 'sub2': 'scene'})
    assert set(one_empty) == {'main', 'sub2'}
    if arrangement == 'side':
        assert one_empty['main']['w'] < width
    else:
        assert one_empty['main']['w'] == width
    boxes = layout.boxes_geometry(arrangement, width, height, {'main': 'slides', 'sub1': 'scene', 'sub2': 'window'})
    assert set(boxes) == {'main', 'sub1', 'sub2'}
    for b in boxes.values():
        assert b['x'] >= 0 and b['y'] >= 0 and b['x'] + b['w'] <= width and b['y'] + b['h'] <= height
    if arrangement == 'side':
        assert boxes['sub1']['y'] + boxes['sub1']['h'] <= boxes['sub2']['y']
    else:
        assert boxes['sub2']['y'] + boxes['sub2']['h'] <= boxes['sub1']['y']
    if arrangement == 'side':
        assert boxes['main']['x'] + boxes['main']['w'] <= boxes['sub1']['x']


def box_call(ctx, op, **kwargs):
    return call(ctx, 'onair_layout', {'action': op, **kwargs})


@pytest.mark.parametrize('with_named_item', [False, True])
def test_preexisting_boxes_scene_is_never_adopted(obs, with_named_item):
    ctx = served(obs, scene_view=True)
    assert call(ctx, 'onair_obs_setup', {'action': 'configure'})['ok']
    obs.scenes['On Air: Boxes'] = []
    if with_named_item:
        obs._add_item('On Air: Boxes', agent.runtime().obs.cfg.presentation_input)
    before = len(obs.requests())
    result = box_call(ctx, 'set', **SLOTS, arrangement='side')
    assert not result['ok'] and ('rename' in result['error'].lower() or 'remove' in result['error'].lower())
    assert not any(k.startswith(('Set', 'Create')) for k, _ in obs.requests()[before:])


def test_revoke_does_not_mutate_foreign_boxes_scene(obs):
    ctx = served(obs, scene_view=True)
    assert call(ctx, 'onair_obs_setup', {'action': 'configure'})['ok']
    assert call(ctx, 'onair_window_share', {'action': 'prepare'})['ok']
    ctl = agent.runtime().obs
    cfg = ctl.cfg
    obs.frames[cfg.window_input] = (1280, 720)
    obs.inputs[cfg.window_input]['settings']['RestoreToken'] = 'chosen'
    ctx.choice = 'confirm'
    assert ctx.commands['onair-broadcast']('confirm-window').startswith('Confirmed')
    obs.scenes['On Air: Boxes'] = []
    item = obs._add_item('On Air: Boxes', cfg.window_input)
    result = ctx.commands['onair-broadcast']('revoke-window')
    assert item['sceneItemEnabled'] is True
    assert 'did not confirm' in result.lower()
    assert ctl._confirmed is None


def test_boxes_rejects_replaced_scene_source_even_when_url_matches(obs):
    ctx = served(obs, scene_view=True)
    assert call(ctx, 'onair_obs_setup', {'action': 'configure'})['ok']
    cfg = agent.runtime().obs.cfg
    obs.inputs[cfg.scene_view_input]['uuid'] = 'foreign-replacement'
    before = len(obs.requests('CreateScene'))
    result = box_call(ctx, 'set', **SLOTS, arrangement='side')
    assert not result['ok']
    assert len(obs.requests('CreateScene')) == before


def test_boxes_item_source_uuid_must_match_owned_source(obs):
    ctx = served(obs, scene_view=True)
    assert call(ctx, 'onair_obs_setup', {'action': 'configure'})['ok']
    assert box_call(ctx, 'set', **SLOTS, arrangement='side')['ok']
    cfg = agent.runtime().obs.cfg
    obs.item('On Air: Boxes', cfg.presentation_input)['_sourceUuid'] = 'different'
    before = len(obs.requests('SetSceneItemTransform'))
    assert not box_call(ctx, 'set', **SLOTS, arrangement='side')['ok']
    assert len(obs.requests('SetSceneItemTransform')) == before


def test_boxes_replaced_item_is_not_owned(obs):
    ctx = served(obs, scene_view=True)
    assert call(ctx, 'onair_obs_setup', {'action': 'configure'})['ok']
    assert box_call(ctx, 'set', **SLOTS, arrangement='side')['ok']
    item = obs.item('On Air: Boxes', agent.runtime().obs.cfg.presentation_input)
    item['sceneItemId'] += 100
    before = len(obs.requests('SetSceneItemTransform'))
    assert not box_call(ctx, 'set', **SLOTS, arrangement='side')['ok']
    assert len(obs.requests('SetSceneItemTransform')) == before


def test_boxes_set_move_swap_and_readback(obs):
    ctx = served(obs, scene_view=True)
    assert call(ctx, 'onair_obs_setup', {'action': 'configure'})['ok']
    out = box_call(ctx, 'set', **SLOTS, arrangement='side')
    assert out['ok'], out
    assert out['boxes']['slots'] == SLOTS
    assert out['boxes']['arrangement'] == 'side'
    assert obs.items('On Air: Boxes').count('On Air captions & voice (browser)') == 1
    assert box_call(ctx, 'set', **SLOTS, arrangement='side')['ok']
    assert box_call(ctx, 'put', slot='sub2', source='scene')['boxes']['slots'] == {
        'main': 'slides', 'sub1': 'empty', 'sub2': 'scene'}
    assert box_call(ctx, 'swap', a='main', b='sub2')['boxes']['slots'] == {
        'main': 'scene', 'sub1': 'empty', 'sub2': 'slides'}
    assert not box_call(ctx, 'swap', a='main', b='sub1')['ok']
    assert not box_call(ctx, 'put', slot='main', source='empty')['ok']
    assert not box_call(ctx, 'put', slot='sub1', source='window')['ok']
    assert not obs.requests('StartStream')
    assert box_call(ctx, 'arrangement', arrangement='stack')['boxes']['arrangement'] == 'stack'
    status = box_call(ctx, 'boxes')
    assert status['boxes']['slots'] == {'main': 'scene', 'sub1': 'empty', 'sub2': 'slides'}
    assert call(ctx, 'onair_obs_scene', {'scene': 'On Air: Boxes'})['ok']
    ctx.surface = 'on_air'
    assert json.loads(ctx.tools['onair_layout']['handler']({'action': 'set', **SLOTS, 'arrangement': 'stack'}))['refused']


def test_boxes_refuse_partial_readback_and_foreign_items(obs):
    ctx = served(obs, scene_view=True)
    assert call(ctx, 'onair_obs_setup', {'action': 'configure'})['ok']
    obs.transform_sticky = True
    out = box_call(ctx, 'set', **SLOTS, arrangement='side')
    assert not out['ok'] and out.get('problems')
    obs.transform_sticky = False
    assert box_call(ctx, 'set', **SLOTS, arrangement='side')['ok']
    obs.add_input('On Air: Boxes', 'Foreign input', 'image_source', {})
    before = len(obs.requests('SetSceneItemTransform'))
    out = box_call(ctx, 'set', **SLOTS, arrangement='stack')
    assert not out['ok']
    assert len(obs.requests('SetSceneItemTransform')) == before


def test_boxes_require_live_owner_confirmation_before_window_and_switch(obs):
    ctx = served(obs, scene_view=True)
    assert call(ctx, 'onair_obs_setup', {'action': 'configure'})['ok']
    assert call(ctx, 'onair_window_share', {'action': 'prepare'})['ok']
    cfg = agent.runtime().obs.cfg
    obs.frames[cfg.window_input] = (1280, 720)
    obs.inputs[cfg.window_input]['settings']['RestoreToken'] = 'chosen'
    all_three = {'main': 'slides', 'sub1': 'scene', 'sub2': 'window'}
    out = box_call(ctx, 'set', **all_three, arrangement='stack')
    assert not out['ok'] and 'confirm-window' in out['next_human_action']
    ctx.choice = 'confirm'
    assert ctx.commands['onair-broadcast']('confirm-window').startswith('Confirmed')
    assert box_call(ctx, 'set', **all_three, arrangement='stack')['ok']
    assert call(ctx, 'onair_obs_scene', {'scene': cfg.presentation_scene})['ok']
    ctx.commands['onair-broadcast']('revoke-window')
    out = call(ctx, 'onair_obs_scene', {'scene': 'On Air: Boxes'})
    assert not out['ok'] and 'confirm-window' in out['next_human_action']
    assert obs.program == cfg.presentation_scene


def test_window_changed_during_apply_stays_hidden(obs):
    ctx = served(obs, scene_view=True)
    assert call(ctx, 'onair_obs_setup', {'action': 'configure'})['ok']
    assert call(ctx, 'onair_window_share', {'action': 'prepare'})['ok']
    cfg = agent.runtime().obs.cfg
    obs.frames[cfg.window_input] = (1280, 720)
    obs.inputs[cfg.window_input]['settings']['RestoreToken'] = 'first'
    ctx.choice = 'confirm'
    assert ctx.commands['onair-broadcast']('confirm-window').startswith('Confirmed')
    handle = obs._handle
    changed = False
    def replace_choice(kind, data):
        nonlocal changed
        if not changed and kind == 'SetSceneItemTransform' and data.get('sceneName') == 'On Air: Boxes':
            changed = True
            obs.inputs[cfg.window_input]['settings']['RestoreToken'] = 'second'
        return handle(kind, data)
    obs._handle = replace_choice
    out = box_call(ctx, 'set', main='slides', sub1='scene', sub2='window', arrangement='side')
    assert not out['ok'] and 'confirm-window' in out['next_human_action']
    assert not obs.item('On Air: Boxes', cfg.window_input)['sceneItemEnabled']
    assert any('sub2:' in problem for problem in out['problems'])


def test_revocation_at_enable_request_fails_and_hides_window(obs):
    ctx = served(obs, scene_view=True)
    assert call(ctx, 'onair_obs_setup', {'action': 'configure'})['ok']
    assert call(ctx, 'onair_window_share', {'action': 'prepare'})['ok']
    ctl = agent.runtime().obs
    cfg = ctl.cfg
    obs.frames[cfg.window_input] = (1280, 720)
    obs.inputs[cfg.window_input]['settings']['RestoreToken'] = 'chosen'
    ctx.choice = 'confirm'
    assert ctx.commands['onair-broadcast']('confirm-window').startswith('Confirmed')
    handle = obs._handle
    revoked = False
    def revoke_on_enable(kind, data):
        nonlocal revoked
        if (not revoked and kind == 'SetSceneItemEnabled' and data.get('sceneName') == 'On Air: Boxes'
                and data.get('sceneItemEnabled') is True
                and obs.item('On Air: Boxes', cfg.window_input)['sceneItemId'] == data.get('sceneItemId')):
            revoked = True
            ctl.revoke_window_confirmation('owner revoked')
        return handle(kind, data)
    obs._handle = revoke_on_enable
    out = box_call(ctx, 'set', main='slides', sub1='scene', sub2='window', arrangement='side')
    assert revoked and not out['ok']
    assert 'changed' in out['error'].lower()
    assert not obs.item('On Air: Boxes', cfg.window_input)['sceneItemEnabled']


def test_window_show_revoked_at_enable_request_is_not_success(obs):
    ctx = served(obs, scene_view=True)
    assert call(ctx, 'onair_obs_setup', {'action': 'configure'})['ok']
    assert call(ctx, 'onair_window_share', {'action': 'prepare'})['ok']
    ctl = agent.runtime().obs
    cfg = ctl.cfg
    obs.frames[cfg.window_input] = (1280, 720)
    obs.inputs[cfg.window_input]['settings']['RestoreToken'] = 'chosen'
    ctx.choice = 'confirm'
    assert ctx.commands['onair-broadcast']('confirm-window').startswith('Confirmed')
    handle = obs._handle
    revoked = False
    def revoke_on_enable(kind, data):
        nonlocal revoked
        if (not revoked and kind == 'SetSceneItemEnabled' and data.get('sceneName') == cfg.window_scene
                and data.get('sceneItemEnabled') is True):
            revoked = True
            ctl.revoke_window_confirmation('owner revoked')
        return handle(kind, data)
    obs._handle = revoke_on_enable
    out = call(ctx, 'onair_window_share', {'action': 'show'})
    assert revoked and not out['ok']
    assert 'changed' in out['error'].lower()
    assert not obs.item(cfg.window_scene, cfg.window_input)['sceneItemEnabled']


def test_window_scene_switch_revoked_in_flight_is_not_success(obs):
    ctx = served(obs, scene_view=True)
    assert call(ctx, 'onair_obs_setup', {'action': 'configure'})['ok']
    assert call(ctx, 'onair_window_share', {'action': 'prepare'})['ok']
    ctl = agent.runtime().obs
    cfg = ctl.cfg
    obs.frames[cfg.window_input] = (1280, 720)
    obs.inputs[cfg.window_input]['settings']['RestoreToken'] = 'chosen'
    ctx.choice = 'confirm'
    assert ctx.commands['onair-broadcast']('confirm-window').startswith('Confirmed')
    handle = obs._handle
    def revoke_at_switch(kind, data):
        if kind == 'SetCurrentProgramScene' and data.get('sceneName') == cfg.window_scene:
            ctl.revoke_window_confirmation('owner revoked')
        return handle(kind, data)
    obs._handle = revoke_at_switch
    result = call(ctx, 'onair_obs_scene', {'scene': cfg.window_scene})
    assert not result['ok'] and 'changed' in result['error'].lower()
    assert not obs.item(cfg.window_scene, cfg.window_input)['sceneItemEnabled']


def test_boxes_scene_switch_revoked_in_flight_is_not_success(obs):
    ctx = served(obs, scene_view=True)
    assert call(ctx, 'onair_obs_setup', {'action': 'configure'})['ok']
    assert call(ctx, 'onair_window_share', {'action': 'prepare'})['ok']
    ctl = agent.runtime().obs
    cfg = ctl.cfg
    obs.frames[cfg.window_input] = (1280, 720)
    obs.inputs[cfg.window_input]['settings']['RestoreToken'] = 'chosen'
    ctx.choice = 'confirm'
    assert ctx.commands['onair-broadcast']('confirm-window').startswith('Confirmed')
    assert box_call(ctx, 'set', main='slides', sub1='scene', sub2='window', arrangement='side')['ok']
    handle = obs._handle
    def revoke_at_switch(kind, data):
        if kind == 'SetCurrentProgramScene' and data.get('sceneName') == 'On Air: Boxes':
            ctl.revoke_window_confirmation('owner revoked')
        return handle(kind, data)
    obs._handle = revoke_at_switch
    result = call(ctx, 'onair_obs_scene', {'scene': 'On Air: Boxes'})
    assert not result['ok'] and 'changed' in result['error'].lower()
    assert not obs.item('On Air: Boxes', cfg.window_input)['sceneItemEnabled']


def test_named_but_changed_source_is_not_touched(obs):
    ctx = served(obs, scene_view=True)
    assert call(ctx, 'onair_obs_setup', {'action': 'configure'})['ok']
    assert box_call(ctx, 'set', **SLOTS, arrangement='side')['ok']
    cfg = agent.runtime().obs.cfg
    obs.inputs[cfg.scene_view_input]['settings']['url'] = 'http://example.invalid/'
    before = len(obs.requests('SetSceneItemEnabled'))
    result = box_call(ctx, 'set', main='slides', sub1='empty', sub2='empty', arrangement='stack')
    assert not result['ok'] and 'cannot be verified' in result['error']
    assert len(obs.requests('SetSceneItemEnabled')) == before


def test_boxes_lost_readback_reports_each_unknown_slot(obs):
    ctx = served(obs, scene_view=True)
    assert call(ctx, 'onair_obs_setup', {'action': 'configure'})['ok']
    handle = obs._handle
    changed = False
    def fail_readback(kind, data):
        nonlocal changed
        if kind == 'SetSceneItemTransform' and data.get('sceneName') == 'On Air: Boxes':
            changed = True
        if changed and kind == 'GetSceneItemList':
            return 701, None
        return handle(kind, data)
    obs._handle = fail_readback
    out = box_call(ctx, 'set', **SLOTS, arrangement='side')
    assert not out['ok'] and out['requests_accepted']
    assert all(any(slot in problem for problem in out['problems']) for slot in ('main', 'sub1', 'sub2'))
    assert 'readback' in out['error'].lower()


def test_boxes_partial_obs_refusal_reports_readback(obs):
    ctx = served(obs, scene_view=True)
    assert call(ctx, 'onair_obs_setup', {'action': 'configure'})['ok']
    obs.refuse['SetSceneItemTransform'] = 701
    out = box_call(ctx, 'set', **SLOTS, arrangement='side')
    assert not out['ok'] and out['requests_accepted'] and out['problems']
    assert any('main:' in problem or 'sub1:' in problem for problem in out['problems'])
