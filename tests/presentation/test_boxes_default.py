"""First box operations use a safe default instead of an unknown readback."""
import pytest
from onair.presentation import agent
from test_boxes import box_call
from test_obs_control import call, served


def test_first_put_uses_scene_default_and_moves_slides_to_sub(obs):
    ctx = served(obs, scene_view=True)
    assert call(ctx, 'onair_obs_setup', {'action': 'configure'})['ok']
    result = box_call(ctx, 'put', slot='sub1', source='slides')
    assert result['ok'], result
    assert result['boxes']['slots'] == {'main':'scene', 'sub1':'slides', 'sub2':'empty'}


@pytest.mark.parametrize('action,args', [('arrangement', {'arrangement':'side'}), ('swap', {'a':'sub1', 'b':'sub2'})])
def test_first_change_uses_slides_when_scene_unavailable(obs, action, args):
    ctx = served(obs)
    assert call(ctx, 'onair_obs_setup', {'action':'configure'})['ok']
    result = box_call(ctx, action, **args)
    assert result['ok'], result
    assert result['boxes']['slots'] == {'main':'slides', 'sub1':'empty', 'sub2':'empty'}


def test_first_swap_rejects_empty_main_with_friendly_message(obs):
    ctx = served(obs)
    assert call(ctx, 'onair_obs_setup', {'action':'configure'})['ok']
    result = box_call(ctx, 'swap', a='main', b='sub1')
    assert not result['ok'] and 'Main can never be empty' in result['error']
    assert 'On Air: Boxes' not in obs.scenes


def test_set_empty_main_reaches_friendly_validation(obs):
    ctx = served(obs)
    assert call(ctx, 'onair_obs_setup', {'action':'configure'})['ok']
    result = box_call(ctx, 'set', main='empty', sub1='slides', sub2='empty', arrangement='stack')
    assert not result['ok'] and 'Main can never be empty' in result['error']
    assert 'empty' in agent.SCHEMAS['onair_layout']['parameters']['properties']['main']['enum']
