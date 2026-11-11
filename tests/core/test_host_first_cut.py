"""Owner cut-in retains only bounded, owner-origin input across the epoch fence."""
from onair.stage import StageConfig, StageController, PendingLine
from onair.types import Verdict


def stage():
    c = StageController(StageConfig(host_turns=True, turn_gap_s=0), monotonic=lambda: 100.0,
                        guard=lambda line, ctx: Verdict(True, 'ok', ''))
    c.set_mode('approval')
    c._last_loosening = 0
    return c


def test_cut_carries_queued_owner_in_order_but_drops_relay_and_cohost():
    c = stage()
    ep = c.state.epoch
    for text, via in [('one', 'typed'), ('relay', 'relay'), ('two', 'typed')]:
        assert c.queue_host_turn(text, ep, text, via=via)['ok']
    c._approval['p'] = PendingLine('p', ep, 'old', 'draft', c.clock(), 'beat')
    c._air_q.put_nowait(PendingLine('a', ep, 'old', 'approved', c.clock(), 'chat'))
    c._clips['clip'] = object()
    c.cut_in(ep)
    assert not c._approval and c._air_q.empty() and not c._clips
    assert [(x.text, x.epoch) for x in c._host_pending] == [('one', ep + 1), ('two', ep + 1)]
    rows = [r for r in c.receipts.tail() if r.get('kind') == 'host']
    assert [(r['id'], r['disposition'], r.get('reason')) for r in rows[-3:]] == [
        ('one', 'carried', 'cut_in'), ('relay', 'dropped', 'cut_in'), ('two', 'carried', 'cut_in')]
    t = c.tick(); assert t.host_text == 'one'
    c.turn_done(t.turn_id)
    assert c.tick().host_text == 'two'


def test_cut_reasks_inflight_once_and_old_reply_never_airs():
    c = stage(); ep = c.state.epoch
    assert c.queue_host_turn('question', ep)['ok']
    old = c.tick()
    c.cut_in(ep)
    assert c.deliver_reply(old.turn_id, 'Old reply.') == ['withheld']
    assert not c.pending_lines() and c._air_q.empty()
    new = c.tick(); assert new.host_text == 'question' and new.epoch == ep + 1
    c.cut_in(c.state.epoch)
    assert c.deliver_reply(new.turn_id, 'Late reply.') == ['withheld']
    assert not c._host_pending and c.tick() is None


def test_cut_isolates_new_dispatch_from_delayed_session_cancellation():
    import asyncio

    async def run():
        from onair.adapter import OnAirAdapter
        entered = asyncio.Event(); release_cancel = asyncio.Event(); cancelled_sessions = []
        class AdapterStub:
            def __init__(self):
                self._session_keys = {}
            async def interrupt_session_activity(self, key, chat_id):
                entered.set()
                await release_cancel.wait()
            async def cancel_session_processing(self, key):
                cancelled_sessions.append(key)
        adapter = AdapterStub()
        c = stage(); c._cancel_turn = lambda turn: OnAirAdapter._cancel_turn(adapter, turn)
        c.queue_host_turn('question', c.state.epoch)
        old = c.tick()
        adapter._session_keys[old.turn_id] = old.session
        c.cut_in(c.state.epoch)
        await entered.wait()
        new = c.tick()
        assert new and new.session != old.session
        release_cancel.set(); await asyncio.sleep(0)
        assert cancelled_sessions == [old.session] and new.session not in cancelled_sessions
        c.turn_done(new.turn_id)
        await c.shutdown()
    asyncio.run(run())


def test_async_old_dispatch_cannot_air_and_retry_requires_approval():
    import asyncio

    async def run():
        entered = asyncio.Event()
        release = asyncio.Event()
        old_outcome = []
        async def dispatch(turn):
            if turn.host_retry == 0:
                entered.set()
                await release.wait()  # deliberately ignores the cancel hook
                old_outcome.extend(c.deliver_reply(turn.turn_id, 'Stale reply.'))
            else:
                assert c.deliver_reply(turn.turn_id, 'Fresh reply.') == ['queued']
                c.turn_done(turn.turn_id)
        c = stage()
        c._dispatch = dispatch
        c.queue_host_turn('question', c.state.epoch)
        old = c.tick(); await entered.wait()
        c.cut_in(c.state.epoch)
        new = c.tick(); assert new and new.turn_id != old.turn_id
        await asyncio.sleep(0)
        release.set(); await asyncio.sleep(0)
        assert old_outcome == ['withheld']
        assert [line['text'] for line in c.pending_lines()] == ['Fresh reply.']
        assert c._air_q.empty()
        assert not any(event['type'] in ('caption', 'audio') for event in c.hub.history)
        await c.shutdown()
    asyncio.run(run())


def test_cut_does_not_reask_when_newer_owner_waits_and_relay_never_reasked():
    c = stage(); ep = c.state.epoch
    c.queue_host_turn('first', ep); old = c.tick()
    c.queue_host_turn('newer', ep)
    c.cut_in(ep)
    assert c.tick().host_text == 'newer'
    c.stop(); c.set_mode('approval'); c._last_loosening = 0
    c.queue_host_turn('relay', c.state.epoch, via='relay'); relay = c.tick()
    assert relay and relay.host_text == 'relay'
    c.cut_in(c.state.epoch)
    assert not c._host_pending
