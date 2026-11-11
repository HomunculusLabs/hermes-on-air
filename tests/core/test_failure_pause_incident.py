"""Failure containment with a fake clock and fake stage, without a provider or media."""
from __future__ import annotations

from onair.beats import SeedBank
from onair.stage import StageConfig, StageController
from onair.system_text import system_text_category


def controller(clock, guard):
    bank = SeedBank.from_data({'segments': [{'name': 'opening', 'beats': [
        {'id': f'b-{i}', 'text': 'A quiet beat.', 'provenance': 'fixture'} for i in range(12)]}]})
    c = StageController(StageConfig(dead_air_seconds=5, turn_gap_s=0, turn_timeout_s=10,
                                    host_turns=True), clock=clock, guard=guard, seed_bank=bank)
    c.set_mode('approval')
    c.set_mode('autonomous')
    return c


def test_timeout_failures_back_off_then_pause_without_epoch_bump(clock, guard):
    c = controller(clock, guard)
    epoch, session = c.state.epoch, c.session_id
    delays = []
    for _ in range(3):
        clock.advance(1000)
        turn = c.tick()
        assert turn and turn.kind == 'beat'
        clock.advance(11)
        c.tick()
        assert c.turn is None
        delays.append(c.snapshot()['model_backoff_seconds'])
        if c.state.mode != 'paused':
            assert c.tick() is None
    assert delays == [30, 60, 120]
    assert c.state.mode == 'paused' and c.state.epoch == epoch and c.session_id == session
    state = c.snapshot()
    assert state['paused_reason'] == 'timeout' and state['next_action']
    assert any(r['kind'] == 'model_paused' and r['reason'] == 'timeout' for r in c.receipts.tail(100))
    clock.advance(10000)
    assert c.tick() is None
    assert c.queue_host_turn('Try once', epoch)['ok']
    assert c.tick().kind == 'host'


def test_quiet_beats_wait_for_the_exponential_backoff(clock, guard):
    c = controller(clock, guard)
    clock.advance(10)
    turn = c.tick()
    assert turn
    c.model_failed(turn.turn_id, 'provider_error')
    clock.advance(34.9)  # 30 second backoff plus the five-second quiet interval
    assert c.tick() is None
    clock.advance(.2)
    turn = c.tick()
    assert turn and turn.kind == 'beat'
    c.model_failed(turn.turn_id, 'provider_error')
    clock.advance(64.9)  # next delay doubles to 60 seconds
    assert c.tick() is None
    clock.advance(.2)
    assert c.tick().kind == 'beat'


def test_usage_limit_pauses_immediately_with_sanitized_reason(clock, guard):
    c = controller(clock, guard)
    clock.advance(10)
    turn = c.tick()
    assert turn
    assert system_text_category('HTTP 429: Usage limit reached for 5 hour') == 'usage_limit'
    c.model_failed(turn.turn_id, 'usage_limit')
    state = c.snapshot()
    assert state['mode'] == 'paused' and state['paused_reason'] == 'usage_limit'
    assert state['next_action'] and c.model_failure_count == 1
    assert any(r['kind'] == 'model_paused' and r['reason'] == 'usage_limit' for r in c.receipts.tail(100))
    assert '5 hour' not in repr(state) + repr(c.receipts.tail(100))


def test_backoff_caps_at_five_minutes_for_failed_owner_probes(clock, guard):
    c = controller(clock, guard)
    clock.advance(10)
    turn = c.tick()
    c.model_failed(turn.turn_id, 'usage_limit')
    delays = []
    for _ in range(6):
        assert c.queue_host_turn('Try once', c.state.epoch)['ok']
        turn = c.tick()
        assert turn and turn.kind == 'host'
        c.model_failed(turn.turn_id, 'provider_error')
        delays.append(c.snapshot()['model_backoff_seconds'])
    assert delays == [60, 120, 240, 300, 300, 300]
    assert c.snapshot()['paused_reason'] == 'usage_limit'
