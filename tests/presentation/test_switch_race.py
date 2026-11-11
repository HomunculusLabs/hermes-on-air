"""Switch-time mutation regressions against the loopback OBS double."""
import pytest

from onair.presentation import agent
from test_boxes import box_call
from test_obs_control import call, served


@pytest.mark.parametrize('target', ['scene', 'boxes', 'presentation', 'extra'])
def test_foreign_capture_added_at_switch_is_detected_and_rolled_back(obs, target):
    ctx = served(obs, scene_view=True, approved_scenes=['Approved intro'])
    assert call(ctx, 'onair_obs_setup', {'action': 'configure'})['ok']
    cfg = agent.runtime().obs.cfg
    assert box_call(ctx, 'set', main='slides', sub1='scene', sub2='empty', arrangement='side')['ok']
    obs.scenes['Approved intro'] = []
    scene = {'scene': cfg.scene_view_scene, 'boxes': 'On Air: Boxes',
             'presentation': cfg.presentation_scene, 'extra': 'Approved intro'}[target]
    safe = cfg.scene_view_scene if target == 'presentation' else cfg.presentation_scene
    assert call(ctx, 'onair_obs_scene', {'scene': safe})['ok']
    handle = obs._handle
    def inject(kind, data):
        if kind == 'SetCurrentProgramScene' and data.get('sceneName') == scene:
            obs.add_input(scene, 'Foreign capture', 'pipewire-screen-capture-source', {})
        return handle(kind, data)
    obs._handle = inject
    out = call(ctx, 'onair_obs_scene', {'scene': scene})
    assert not out['ok'], out
    assert out['program_scene'] == safe == obs.program
    assert 'changed' in out['error'].lower()


@pytest.mark.parametrize('target', ['scene', 'boxes', 'presentation', 'extra'])
def test_scene_identity_replaced_at_switch_is_detected(obs, target):
    ctx = served(obs, scene_view=True, approved_scenes=['Approved intro'])
    assert call(ctx, 'onair_obs_setup', {'action': 'configure'})['ok']
    cfg = agent.runtime().obs.cfg
    assert box_call(ctx, 'set', main='slides', sub1='scene', sub2='empty', arrangement='side')['ok']
    obs.scenes['Approved intro'] = []
    scene = {'scene': cfg.scene_view_scene, 'boxes': 'On Air: Boxes',
             'presentation': cfg.presentation_scene, 'extra': 'Approved intro'}[target]
    safe = cfg.scene_view_scene if target == 'presentation' else cfg.presentation_scene
    assert call(ctx, 'onair_obs_scene', {'scene': safe})['ok']
    handle = obs._handle
    def replace(kind, data):
        if kind == 'SetCurrentProgramScene' and data.get('sceneName') == scene:
            obs.scene_uuids[scene] = 'replacement'
        return handle(kind, data)
    obs._handle = replace
    out = call(ctx, 'onair_obs_scene', {'scene': scene})
    assert not out['ok'] and out['program_scene'] == safe == obs.program


def test_unapproved_previous_scene_uses_verified_presentation_fallback(obs):
    ctx = served(obs, scene_view=True)
    assert call(ctx, 'onair_obs_setup', {'action': 'configure'})['ok']
    cfg = agent.runtime().obs.cfg
    assert obs.program == 'Scene' and 'Scene' not in cfg.approved_scenes
    handle = obs._handle
    def inject(kind, data):
        if kind == 'SetCurrentProgramScene' and data.get('sceneName') == cfg.scene_view_scene:
            obs.scene_uuids[cfg.scene_view_scene] = 'replacement'
        return handle(kind, data)
    obs._handle = inject
    out = call(ctx, 'onair_obs_scene', {'scene': cfg.scene_view_scene})
    assert not out['ok'] and out['program_scene'] == cfg.presentation_scene == obs.program


def test_window_show_detects_capture_added_at_switch_and_restores_previous(obs):
    ctx = served(obs, scene_view=True)
    assert call(ctx, 'onair_obs_setup', {'action': 'configure'})['ok']
    cfg = agent.runtime().obs.cfg
    assert call(ctx, 'onair_obs_scene', {'scene': cfg.presentation_scene})['ok']
    assert call(ctx, 'onair_window_share', {'action': 'prepare'})['ok']
    obs.frames[cfg.window_input] = (1280, 720)
    obs.inputs[cfg.window_input]['settings']['RestoreToken'] = 'chosen'
    ctx.choice = 'confirm'
    assert ctx.commands['onair-broadcast']('confirm-window').startswith('Confirmed')
    handle = obs._handle
    def inject(kind, data):
        if kind == 'SetCurrentProgramScene' and data.get('sceneName') == cfg.window_scene:
            obs.add_input(cfg.window_scene, 'Foreign capture', 'pipewire-screen-capture-source', {})
        return handle(kind, data)
    obs._handle = inject
    out = call(ctx, 'onair_window_share', {'action': 'show'})
    assert not out['ok'] and out['program_scene'] == cfg.presentation_scene == obs.program


def test_switch_rollback_failure_is_reported_without_claiming_success(obs):
    ctx = served(obs, scene_view=True)
    assert call(ctx, 'onair_obs_setup', {'action': 'configure'})['ok']
    cfg = agent.runtime().obs.cfg
    assert call(ctx, 'onair_obs_scene', {'scene': cfg.presentation_scene})['ok']
    handle = obs._handle
    def inject_and_disconnect(kind, data):
        if kind == 'SetCurrentProgramScene' and data.get('sceneName') == cfg.scene_view_scene:
            obs.add_input(cfg.scene_view_scene, 'Foreign capture', 'pipewire-screen-capture-source', {})
        elif kind == 'SetCurrentProgramScene' and data.get('sceneName') == cfg.presentation_scene:
            raise ConnectionError('test disconnect')
        return handle(kind, data)
    obs._handle = inject_and_disconnect
    out = call(ctx, 'onair_obs_scene', {'scene': cfg.scene_view_scene})
    assert not out['ok']
    assert out['program_scene'] is None
    assert 'could not confirm' in out['error'].lower()
