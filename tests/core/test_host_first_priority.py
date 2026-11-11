"""Host intake displaces filler without bypassing approval or cutting output."""
from onair.stage import StageConfig, StageController, PendingLine, Turn
from onair.types import Verdict


def controller():
    c = StageController(StageConfig(host_turns=True, turn_gap_s=0), guard=lambda line, ctx: Verdict(True, 'ok', ''))
    c.set_mode('approval')
    return c


def test_owner_withdraws_beats_and_reply_precedes_chat_without_auto_approval():
    c = controller(); ep = c.state.epoch
    for source in ('beat', 'chat', 'beat'):
        line = PendingLine(str(len(c._approval)), ep, 'old', source, c.clock(), source)
        c._approval[line.line_id] = line
    assert c.queue_host_turn('question', ep)['ok']
    assert [p.source for p in c._approval.values()] == ['chat']
    assert len([r for r in c.receipts.tail() if r.get('kind') == 'line_dropped' and r.get('reason') == 'superseded_by_host']) == 2
    t = c.tick(); assert t and t.kind == 'host'
    assert c.deliver_reply(t.turn_id, 'Answer one. Answer two.') == ['queued', 'queued']
    assert [p.source for p in c._approval.values()] == ['host', 'host', 'chat']
    assert c._air_q.empty()


def test_relay_gets_no_priority_and_does_not_withdraw_beats():
    c = controller(); ep = c.state.epoch; c._last_loosening = None
    c._approval['b'] = PendingLine('b', ep, 'old', 'beat', c.clock(), 'beat')
    assert c.queue_host_turn('relay', ep, via='relay')['ok']
    assert c.tick() is None and 'b' in c._approval
    assert c.queue_host_turn('owner', ep)['ok']
    assert c.tick().host_text == 'owner'
    assert [h.text for h in c._host_pending] == ['relay']


def test_owner_does_not_interrupt_approved_airing():
    c = controller(); ep = c.state.epoch
    line = PendingLine('a', ep, 'old', 'approved', c.clock(), 'chat')
    c._airing = line; c._audible = True
    c.queue_host_turn('question', ep)
    turn = c.tick()
    assert turn and turn.host_text == 'question'
    assert c._airing is line and c._audible and c.state.epoch == ep
    assert c.deliver_reply(turn.turn_id, 'A fresh answer.') == ['queued']
    assert c._air_q.empty() and c.pending_lines()[0]['source'] == 'host'


def test_inflight_beat_draft_withheld_when_owner_waiting():
    c = controller(); ep = c.state.epoch
    beat = Turn('beat', ep, 'beat', started_at=c.clock()); c.turn = beat
    c.queue_host_turn('question', ep)
    assert c.offer_line(beat.turn_id, 'Too late.', Verdict(True, 'ok', '')) == 'withheld'
    assert not c.pending_lines()
    c.turn_done(beat.turn_id)
    assert c.tick().host_text == 'question'


def test_rejecting_host_reply_restarts_quiet_timer():
    now = [100.0]
    c = StageController(StageConfig(host_turns=True, turn_gap_s=0), clock=lambda: now[0], guard=lambda line, ctx: Verdict(True, 'ok', ''))
    c.set_mode('approval'); c.queue_host_turn('question', c.state.epoch)
    t = c.tick(); c.deliver_reply(t.turn_id, 'Answer.'); c.turn_done(t.turn_id)
    now[0] = 1000.0
    c.reject(c.pending_lines()[0]['line_id'], c.state.epoch)
    assert c.quiet_for() == 0
