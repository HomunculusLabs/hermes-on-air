"""Opt-in OBS scene view, using only the loopback OBS double."""
import pytest

from conftest import make
from test_obs_control import call, served

SCENE = 'On Air: 3D scene'
SOURCE = 'On Air 3D scene (browser)'
OVERLAY = 'On Air captions & voice (browser)'


def test_scene_view_is_opt_in_and_idempotent(obs):
    ctx = served(obs, scene_view=True, overlay_url='http://127.0.0.1:12345/overlay?style=one')
    for _ in range(2):
        result = call(ctx, 'onair_obs_setup', {'action': 'configure'})
        assert result['ok'], result
        assert obs.items(SCENE) == [SOURCE, OVERLAY]
    assert obs.inputs[SOURCE]['settings']['url'] == 'http://127.0.0.1:12345/scene/'
    assert obs.inputs[SOURCE]['settings']['reroute_audio'] is False
    assert len([r for r in obs.requests('CreateInput') if r[1]['inputName'] == SOURCE]) == 1
    state = call(ctx, 'onair_obs_setup', {'action': 'inspect'})
    assert SCENE in state['approved_scenes']
    assert call(ctx, 'onair_obs_scene', {'scene': SCENE})['ok']
    assert not obs.requests('StartStream')


def test_unowned_scene_and_later_foreign_capture_cannot_be_switched(obs):
    obs.add_input(SCENE, 'Private capture', 'pipewire-screen-capture-source', {})
    ctx = served(obs, scene_view=True)
    before = obs.program
    result = call(ctx, 'onair_obs_scene', {'scene': SCENE})
    assert not result['ok'] and obs.program == before
    assert not call(ctx, 'onair_obs_setup', {'action': 'configure'})['ok']


def test_tampered_configured_scene_cannot_be_switched(obs):
    ctx = served(obs, scene_view=True)
    assert call(ctx, 'onair_obs_setup', {'action': 'configure'})['ok']
    obs.add_input(SCENE, 'Private capture', 'pipewire-screen-capture-source', {})
    result = call(ctx, 'onair_obs_scene', {'scene': SCENE})
    assert not result['ok'] and obs.program == 'Scene'


@pytest.mark.parametrize('tamper', ['duplicate', 'order', 'hidden', 'url', 'size', 'source_uuid', 'item_source_uuid', 'scene_uuid', 'item_id'])
def test_scene_view_requires_current_exact_identities_and_settings(obs, tamper):
    ctx = served(obs, scene_view=True)
    assert call(ctx, 'onair_obs_setup', {'action': 'configure'})['ok']
    if tamper == 'duplicate':
        obs._add_item(SCENE, SOURCE)
    elif tamper == 'order':
        obs.scenes[SCENE].reverse()
    elif tamper == 'hidden':
        obs.item(SCENE, OVERLAY)['sceneItemEnabled'] = False
    elif tamper == 'url':
        obs.inputs[SOURCE]['settings']['url'] = 'http://example.invalid/'
    elif tamper == 'size':
        obs.inputs[SOURCE]['settings']['width'] = 1
    elif tamper == 'source_uuid':
        obs.inputs[SOURCE]['uuid'] = 'different'
    elif tamper == 'item_source_uuid':
        obs.item(SCENE, SOURCE)['_sourceUuid'] = 'different'
    elif tamper == 'scene_uuid':
        obs.scene_uuids[SCENE] = 'different'
    else:
        obs.item(SCENE, SOURCE)['sceneItemId'] += 100
    assert not call(ctx, 'onair_obs_scene', {'scene': SCENE})['ok']
    assert obs.program == 'Scene'


def test_scene_view_refuses_preexisting_same_named_overlay(obs):
    obs.add_input('Scene', OVERLAY, 'browser_source', {'url': 'http://example.invalid/'})
    ctx = served(obs, scene_view=True)
    result = call(ctx, 'onair_obs_setup', {'action': 'configure'})
    assert not result['ok']
    assert OVERLAY in result['error'] and 'remove or rename' in result['error']
    assert result['next_human_action'] == result['error']
    assert not obs.requests('CreateScene') and not obs.requests('SetInputSettings')


def test_scene_view_rejects_second_speaking_overlay_added_later(obs):
    ctx = served(obs, scene_view=True)
    assert call(ctx, 'onair_obs_setup', {'action': 'configure'})['ok']
    obs.add_input('Scene', 'Second captions', 'browser_source', {'url': 'http://127.0.0.1:18790/overlay', 'reroute_audio': True})
    result = call(ctx, 'onair_obs_scene', {'scene': SCENE})
    assert not result['ok'] and obs.program == 'Scene'


def test_approved_name_cannot_bypass_disabled_scene_view_guard(obs):
    obs.add_input(SCENE, 'Foreign browser', 'browser_source', {'url': 'http://example.invalid/'})
    ctx = served(obs, approved_scenes=[SCENE])
    result = call(ctx, 'onair_obs_scene', {'scene': SCENE})
    assert not result['ok'] and obs.program == 'Scene'


def test_default_does_not_create_scene_view(obs):
    ctx = served(obs)
    assert call(ctx, 'onair_obs_setup', {'action': 'configure'})['ok']
    assert SCENE not in obs.scenes and SOURCE not in obs.inputs
    assert SCENE not in call(ctx, 'onair_obs_setup', {'action': 'inspect'})['approved_scenes']


@pytest.mark.parametrize('url', ['http://example.com/overlay', 'http://127.0.0.1.evil/overlay',
                                 'http://user:pass@localhost/overlay', 'file:///overlay'])
def test_non_loopback_overlay_refused_before_writes(obs, url):
    ctx = served(obs, scene_view=True, overlay_url=url)
    result = call(ctx, 'onair_obs_setup', {'action': 'configure'})
    assert not result['ok'] and 'loopback' in result['error'].lower()
    assert not obs.requests('CreateScene') and not obs.requests('CreateInput')


def test_scene_view_readback_mismatch_not_success(obs):
    ctx = served(obs, scene_view=True)
    obs.transform_sticky = True
    result = call(ctx, 'onair_obs_setup', {'action': 'configure'})
    assert not result['ok'] and not result['configured']
    assert any(row.get('source') == SOURCE and not row['fitted'] for row in result['fit']['items'])


def test_foreign_collision_refused_before_writes(obs):
    obs.add_input('Scene', SOURCE, 'browser_source', {'url': 'http://example.com/'})
    ctx = served(obs, scene_view=True)
    result = call(ctx, 'onair_obs_setup', {'action': 'configure'})
    assert not result['ok']
    assert not obs.requests('CreateScene') and not obs.requests('SetInputSettings')
