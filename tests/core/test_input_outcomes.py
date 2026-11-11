"""The owner sees a bounded receipt-backed intake summary, not raw history."""
from onair.stage import StageConfig, StageController
from onair.types import ChatMsg


def msg(c, ident, text):
    return ChatMsg('twitch', 'channel', ident, ident, 'Viewer', text, c.clock())


def test_inputs_are_bounded_updated_and_privacy_stop_clear():
    c = StageController(StageConfig(host_turns=True, turn_gap_s=0)); c.set_mode('approval')
    for n in range(6):
        assert c.queue_host_turn('x' * 100 + str(n), c.state.epoch, str(n))['ok']
        t = c.tick(); c.turn_done(t.turn_id)
    rows = c.snapshot()['inputs']
    assert len(rows) == 5 and all(set(r) == {'kind', 'text', 'outcome', 'reason'} for r in rows)
    assert all(r['kind'] == 'host' and r['outcome'] == 'answering' and len(r['text']) <= 80 for r in rows)
    c.queue_host_turn('carry', c.state.epoch, 'carry')
    c.cut_in(c.state.epoch)
    assert c.snapshot()['inputs'][0]['outcome'] == 'carried'
    assert c.snapshot()['inputs'][0]['reason'] == 'cut_in'
    c.set_privacy(True); assert c.snapshot()['inputs'] == []
    c.ingest(msg(c, 'private', 'private text')); assert c.snapshot()['inputs'] == []
    c.set_privacy(False); c.queue_host_turn('after', c.state.epoch)
    c.stop(); assert c.snapshot()['inputs'] == []


def test_chat_summary_distinguishes_queued_from_skipped_and_is_updated_on_expiry():
    now = [1000.0]
    c = StageController(clock=lambda: now[0]); c.set_mode('approval')
    c.ingest(msg(c, 'one', 'What is this?'))
    c.ingest(msg(c, 'two', 'helllooooo'))
    row = c.snapshot()['inputs'][0]
    assert row == {'kind': 'chat', 'who': 'Viewer', 'text': 'helllooooo', 'outcome': 'queued', 'reason': 'routine'}
    now[0] += c.cfg.select.max_age_s + 1
    c.tick()
    assert c.snapshot()['inputs'][0]['outcome'] == 'skipped'
    assert c.snapshot()['inputs'][0]['reason'] == 'routine'


def test_busy_owner_and_cancelled_inflight_are_recorded():
    c = StageController(StageConfig(host_turns=True, turn_gap_s=0)); c.set_mode('approval')
    c.queue_host_turn('first', c.state.epoch, 'first'); first = c.tick()
    for n in range(3):
        assert c.queue_host_turn(str(n), c.state.epoch, str(n))['ok']
    assert c.queue_host_turn('overflow', c.state.epoch)['reason'] == 'full'
    assert c.snapshot()['inputs'][0] == {'kind': 'host', 'text': 'overflow', 'outcome': 'dropped', 'reason': 'busy'}
    c.cut_in(c.state.epoch)
    row = next(row for row in c.snapshot()['inputs'] if row['text'] == 'first')
    assert row['outcome'] == 'dropped' and row['reason'] == 'cut_in'
    assert c.deliver_reply(first.turn_id, 'Too late.') == ['withheld']


def test_relay_is_not_an_owner_input_and_off_air_clears():
    c = StageController(StageConfig(host_turns=True)); c.set_mode('approval'); c._last_loosening = None
    c.queue_host_turn('relay', c.state.epoch, via='relay')
    assert c.snapshot()['inputs'] == []
    c.queue_host_turn('typed', c.state.epoch)
    c.set_mode('off_air'); assert c.snapshot()['inputs'] == []
