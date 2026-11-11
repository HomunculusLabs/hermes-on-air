import pytest

from onair.stage import StageConfig, StageController


def test_relay_settles_after_loosening_not_mute_and_typed_bypasses():
    now = [100.0]
    c = StageController(StageConfig(host_turns=True, host_relay_settle_seconds=20), monotonic=lambda: now[0])
    c.set_mode('approval')
    result = c.queue_host_turn('before cut', c.state.epoch, via='relay')
    assert result['reason'] == 'settling' and '20' in result['message'] and 'type' in result['message'].lower()
    assert c.queue_host_turn('typed now', c.state.epoch)['ok']
    now[0] += 20
    assert c.queue_host_turn('relay', c.state.epoch, via='relay')['ok']
    c.set_privacy(True)
    assert c.snapshot()['host_turns']['waiting'] == 0
    c.set_privacy(False)
    assert c.queue_host_turn('late', c.state.epoch, via='relay')['reason'] == 'settling'
    now[0] += 20
    c.set_muted(True)
    c.set_muted(False)
    assert c.queue_host_turn('after mute', c.state.epoch, via='relay')['ok']
    assert any(r.get('via') == 'relay' for r in c.receipts.tail() if r.get('kind') == 'host')
    c.stop()
    c.set_mode('approval')
    assert c.queue_host_turn('after stop', c.state.epoch, via='relay')['reason'] == 'settling'


@pytest.mark.parametrize('value', [4, 121, True, float('nan')])
def test_settle_config_rejects_out_of_bounds(value):
    with pytest.raises(ValueError):
        StageConfig(host_relay_settle_seconds=value)


def test_autonomous_relay_requires_explicit_opt_in_and_typed_still_works():
    now = [100.0]
    c = StageController(StageConfig(host_turns=True), monotonic=lambda: now[0])
    c.set_mode('approval')
    now[0] += 20
    c.set_mode('autonomous')
    now[0] += 20
    refused = c.queue_host_turn('relay', c.state.epoch, via='relay')
    assert refused['reason'] == 'needs_approval_mode'
    assert 'approval' in refused['message'].lower()
    assert c.queue_host_turn('typed', c.state.epoch)['ok']


def test_relay_queued_or_in_flight_in_approval_does_not_air_after_mode_switch():
    from onair.types import Verdict
    now = [100.0]
    c = StageController(StageConfig(host_turns=True, turn_gap_s=0), monotonic=lambda: now[0],
                        guard=lambda line, ctx: Verdict(True, 'ok', ''))
    c.set_mode('approval')
    now[0] += 20
    assert c.queue_host_turn('in flight', c.state.epoch, via='relay')['ok']
    turn = c.tick()
    assert turn and turn.kind == 'host'
    assert c.queue_host_turn('waiting', c.state.epoch, via='relay')['ok']
    assert c.queue_host_turn('typed', c.state.epoch)['ok']
    c.set_mode('autonomous')
    assert c.deliver_reply(turn.turn_id, 'late reply') != ['released']
    assert c.snapshot()['host_turns']['waiting'] == 1
    assert c.tick().host_text == 'typed'


def test_opted_in_relay_settles_after_each_loosening_and_boot():
    now = [100.0]
    c = StageController(StageConfig(host_turns=True, host_relay_autonomous=True), monotonic=lambda: now[0])
    assert c._last_loosening == 100.0
    c.set_mode('approval')
    assert c.queue_host_turn('arm', c.state.epoch, via='relay')['reason'] == 'settling'
    now[0] += 20
    c.set_mode('autonomous')
    assert c.queue_host_turn('mode', c.state.epoch, via='relay')['reason'] == 'settling'
    now[0] += 20
    assert c.queue_host_turn('allowed', c.state.epoch, via='relay')['ok']
    c.set_mode('paused')
    now[0] += 20
    c.set_mode('autonomous')
    assert c.queue_host_turn('resume', c.state.epoch, via='relay')['reason'] == 'settling'
    now[0] += 20
    c.set_privacy(True)
    c.set_privacy(False)
    assert c.queue_host_turn('privacy', c.state.epoch, via='relay')['reason'] == 'settling'
    now[0] += 20
    c.stop()
    c.set_mode('approval')
    assert c.queue_host_turn('rearm', c.state.epoch, via='relay')['reason'] == 'settling'
    restarted = StageController(StageConfig(host_turns=True), monotonic=lambda: now[0])
    assert restarted._last_loosening == now[0]


def test_host_waits_for_turn_and_airing_then_beats_chat():
    from onair.types import ChatMsg, Verdict
    c = StageController(StageConfig(host_turns=True, turn_gap_s=0, air_pace=0),
                        guard=lambda line, ctx: Verdict(True, 'ok', ''))
    c.set_mode('approval')
    c.set_mode('autonomous')
    now = c.clock()
    c.ingest(ChatMsg('twitch', 'channel', 'first', 'viewer', 'Viewer', 'What is the plan?', now, first_msg=True))
    first = c.tick()
    assert first and first.kind == 'chat'
    assert c.queue_host_turn('Host question', c.state.epoch)['ok']
    c.ingest(ChatMsg('twitch', 'channel', 'second', 'other', 'Other', 'What is next?', now, first_msg=True))
    assert c.tick() is None and c.turn is first
    assert c.deliver_reply(first.turn_id, 'A safe reply.') == ['released']
    c.turn_done(first.turn_id)
    assert c.tick() is None  # queued air must finish first
    line = c._air_q.get_nowait()
    c._airing = line
    assert c.tick() is None  # also blocked while the line is actively airing
    c._airing = None
    turn = c.tick()
    assert turn and turn.kind == 'host' and turn.host_text == 'Host question'
    assert c._pending  # viewer chat remains waiting


def test_host_waits_while_real_air_loop_is_synthesizing():
    import asyncio
    from onair.types import ChatMsg, Verdict

    async def go():
        started, finish = asyncio.Event(), asyncio.Event()
        class HeldTTS:
            available = True
            async def synth(self, text):
                started.set()
                await finish.wait()
                return None
        c = StageController(StageConfig(host_turns=True, turn_gap_s=0, air_pace=0), tts=HeldTTS(),
                            guard=lambda line, ctx: Verdict(True, 'ok', ''))
        c.set_mode('approval')
        c.set_mode('autonomous')
        c.ingest(ChatMsg('twitch', 'channel', 'first', 'viewer', 'Viewer', 'What is the plan?', c.clock(), first_msg=True))
        turn = c.tick()
        assert turn and c.deliver_reply(turn.turn_id, 'A safe reply.') == ['released']
        c.turn_done(turn.turn_id)
        task = asyncio.create_task(c._air_loop())
        try:
            await asyncio.wait_for(started.wait(), 2)
            assert c.queue_host_turn('Host question', c.state.epoch)['ok']
            assert c.tick() is None and c._airing is not None
            finish.set()
            for _ in range(100):
                if c._airing is None:
                    break
                await asyncio.sleep(0.001)
            assert c._airing is None
            assert c.tick().kind == 'host'
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
    asyncio.run(go())


@pytest.mark.parametrize('cut', ['privacy', 'stop', 'off_air'])
def test_cut_purges_all_host_origins_and_records_via(cut):
    now = [0.0]
    c = StageController(StageConfig(host_turns=True), monotonic=lambda: now[0])
    c.set_mode('approval')
    now[0] += 20
    assert c.queue_host_turn('typed', c.state.epoch, via='typed')['ok']
    assert c.queue_host_turn('relay', c.state.epoch, via='relay')['ok']
    if cut == 'privacy':
        c.set_privacy(True)
    elif cut == 'stop':
        c.stop()
    else:
        c.set_mode('off_air')
    assert c.snapshot()['host_turns']['waiting'] == 0
    assert {r['via'] for r in c.receipts.tail() if r.get('kind') == 'host' and r.get('disposition') == 'dropped'} == {'typed', 'relay'}


def test_relay_http_requires_exact_origin_and_obeys_settling():
    import asyncio
    import json
    from core_http_helpers import http
    from onair.control import ControlServer

    async def go():
        c = StageController(StageConfig(host_turns=True), monotonic=lambda: 100.0)
        c.set_mode('approval')
        server = ControlServer(c, None)
        await server.start()
        try:
            auth = {'Authorization': 'Bearer ' + server.token}
            body = {'text': 'question', 'epoch': c.state.epoch}
            for via in ('typed', '', True, None, 'RELAY'):
                status, _, _ = await http(server.http.port, 'POST', '/host/turn', headers=auth, body={**body, 'via': via})
                assert status == 400
            status, _, payload = await http(server.http.port, 'POST', '/host/turn', headers=auth, body={**body, 'via': 'relay'})
            assert status == 409 and json.loads(payload)['reason'] == 'settling'
            status, _, payload = await http(server.http.port, 'POST', '/host/turn', headers=auth, body=body)
            assert status == 200 and json.loads(payload)['queued']
        finally:
            await server.stop()
    asyncio.run(go())
