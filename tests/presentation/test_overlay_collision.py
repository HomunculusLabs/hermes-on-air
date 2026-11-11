"""Migration from an independently configured captions source."""
from onair.presentation import agent
from test_obs_control import call, served
from test_boxes import box_call


def test_setup_explains_existing_overlay_instead_of_creating_another(obs):
    obs.add_input('Scene', 'Existing captions', 'browser_source', {'url': 'http://127.0.0.1:18790/overlay'})
    ctx = served(obs, scene_view=True)
    result = call(ctx, 'onair_obs_setup', {'action': 'configure'})
    assert not result['ok']
    assert 'Existing captions' in result['error']
    assert 'remove' in result['next_human_action'].lower()
    assert 'Existing captions' in result['next_human_action']
    assert not obs.requests('CreateInput') and not obs.requests('CreateScene')


def test_boxes_names_both_overlay_inputs_and_gives_a_fix(obs):
    ctx = served(obs, scene_view=True)
    assert call(ctx, 'onair_obs_setup', {'action': 'configure'})['ok']
    cfg = agent.runtime().obs.cfg
    assert box_call(ctx, 'set', main='scene', sub1='empty', sub2='empty', arrangement='stack')['ok']
    obs.add_input('Scene', 'Existing captions', 'browser_source', {'url': cfg.overlay_url, 'reroute_audio': True})
    before = len(obs.requests('CreateSceneItem'))
    result = box_call(ctx, 'set', main='scene', sub1='empty', sub2='empty', arrangement='stack')
    assert not result['ok']
    assert 'Existing captions' in result['next_human_action']
    assert cfg.overlay_input in result['next_human_action']
    assert 'remove' in result['next_human_action'].lower()
    assert len(obs.requests('CreateSceneItem')) == before
