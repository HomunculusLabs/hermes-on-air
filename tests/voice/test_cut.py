"""Owner cut-in must fence old work without arming or resuming the stage."""
import asyncio
import json

import pytest
async def http(port, method, path, *, body=None, headers=None):
    reader, writer = await asyncio.open_connection('127.0.0.1', port)
    data = json.dumps(body).encode() if body is not None else b''
    fields = {'Host': f'127.0.0.1:{port}', 'Content-Length': str(len(data)), **(headers or {})}
    request = f'{method} {path} HTTP/1.1\r\n' + ''.join(f'{k}: {v}\r\n' for k, v in fields.items()) + '\r\n'
    writer.write(request.encode() + data)
    await writer.drain()
    raw = await asyncio.wait_for(reader.read(), 5)
    writer.close()
    await writer.wait_closed()
    head, _, payload = raw.partition(b'\r\n\r\n')
    return int(head.split()[1]), {}, payload
from onair.control import ControlServer
from onair.stage import ControlError, PendingLine, StageConfig, StageController


def test_cut_fences_and_drops_work_without_resuming():
    c = StageController(StageConfig(host_turns=True))
    c.set_mode('approval')
    old = c.state.epoch
    c.queue_host_turn('before cut', old, 'h1')
    c._clips['clip'] = object()
    c._air_q.put_nowait(PendingLine('line', old, 'turn', 'reply', c.clock()))
    result = c.cut_in(old)
    assert result['epoch'] == old + 1
    assert c.state.mode == 'approval' and c.state.muted is False
    assert c._air_q.empty() and not c._clips and not c._host_pending
    assert c.hub.history[-1]['type'] == 'caption_clear'
    assert c.queue_host_turn('old', old)['reason'] == 'stale_epoch'
    assert c.queue_host_turn('new', result['epoch'])['ok']


def test_cut_refuses_privacy_stale_and_off_air():
    c = StageController()
    with pytest.raises(ControlError):
        c.cut_in(c.state.epoch)
    c.set_mode('approval')
    old = c.state.epoch
    c.set_privacy(True)
    for epoch in (old, c.state.epoch, True, '1'):
        with pytest.raises(ControlError):
            c.cut_in(epoch)
    c.set_privacy(False)
    with pytest.raises(ControlError):
        c.cut_in(old)
    assert c.state.privacy is False and c.state.mode == 'approval'


def test_cut_during_synthesis_never_emits_audio_or_caption():
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        class SlowVoice:
            available = True
            async def synth(self, text):
                entered.set()
                await release.wait()
                return object()
        c = StageController(StageConfig(host_turns=True), tts=SlowVoice())
        c.set_mode('approval')
        old = c.state.epoch
        line = PendingLine('line', old, 'turn', 'safe words', c.clock())
        task = asyncio.create_task(c.air_one(line))
        await entered.wait()
        c.cut_in(old)
        release.set()
        assert await task is False
        assert not any(event['type'] in ('audio', 'caption') for event in c.hub.history)
        assert c.hub.history[-1]['type'] == 'caption_clear'
    asyncio.run(run())


def test_cut_route_auth_and_strict_payload():
    async def run():
        c = StageController(StageConfig(host_turns=True))
        c.set_mode('approval')
        server = ControlServer(c, None)
        await server.start()
        try:
            port = server.http.port
            auth = {'Authorization': 'Bearer ' + server.token}
            old = c.state.epoch
            boot = c.voice_boot_id
            other_boot = 'f' * 32 if boot != 'f' * 32 else 'e' * 32
            assert (await http(port, 'POST', '/cut-in', body={'epoch': old, 'boot': boot}))[0] == 401
            for payload in ({}, {'epoch': True, 'boot': boot}, {'epoch': old}, {'epoch': old, 'boot': boot, 'extra': 1}):
                assert (await http(port, 'POST', '/cut-in', headers=auth, body=payload))[0] == 400
            assert (await http(port, 'POST', '/cut-in', headers=auth, body={'epoch':old, 'boot':other_boot}))[0] == 409
            status, _, payload = await http(port, 'POST', '/cut-in', headers=auth, body={'epoch': old, 'boot':boot})
            assert status == 200 and json.loads(payload)['state']['epoch'] == old + 1
            assert (await http(port, 'POST', '/cut-in', headers=auth, body={'epoch': old, 'boot':boot}))[0] == 409
            assert (await http(port, 'POST', '/host/turn', headers=auth, body={'epoch': old + 1, 'boot':other_boot, 'text':'late'}))[0] == 409
            c.set_privacy(True)
            assert (await http(port, 'POST', '/host/turn', headers=auth, body={'epoch': old + 1, 'boot':boot, 'text':'private'}))[0] == 409
            c.set_privacy(False)
            status, _, raw = await http(port, 'POST', '/host/turn', headers=auth, body={'epoch':old + 1, 'boot':boot, 'text':'old'})
            assert status == 409 and json.loads(raw)['reason'] == 'stale_epoch'
            assert (await http(port, 'POST', '/host/turn', headers=auth, body={'epoch': c.state.epoch, 'boot':boot, 'text':'fresh'}))[0] == 200
        finally:
            await server.stop()
    asyncio.run(run())


def test_conditional_cut_preserves_waiting_work_if_airing_has_ended():
    async def run():
        c = StageController(StageConfig(host_turns=True))
        c.set_mode('approval')
        epoch = c.state.epoch
        c._approval['draft'] = PendingLine('draft', epoch, 'turn', 'waiting reply', c.clock())
        server = ControlServer(c, None); await server.start()
        try:
            auth = {'Authorization': 'Bearer ' + server.token}
            body = {'epoch': epoch, 'boot': c.voice_boot_id, 'only_if_airing': True}
            status, _, payload = await http(server.http.port, 'POST', '/cut-in', headers=auth, body=body)
            answer = json.loads(payload)
            assert status == 200 and answer['ok'] and answer['cut'] is False
            assert c.state.epoch == epoch and 'draft' in c._approval
            assert not any(r.get('action') == 'cut_in' for r in c.receipts.tail(100))
            # voice still being prepared: the line is taken but nothing is out yet -> no cut
            c._airing = PendingLine('airing', epoch, 'turn', 'airing reply', c.clock())
            status, _, payload = await http(server.http.port, 'POST', '/cut-in', headers=auth, body=body)
            answer = json.loads(payload)
            assert status == 200 and answer['cut'] is False
            assert c.state.epoch == epoch and 'draft' in c._approval and c._airing is not None
            c._audible = True
            status, _, payload = await http(server.http.port, 'POST', '/cut-in', headers=auth, body=body)
            answer = json.loads(payload)
            assert status == 200 and answer['cut'] is True
            assert c.state.epoch == epoch + 1 and not c._approval
            for value in (False, 1, 'yes', None):
                bad = dict(body, epoch=c.state.epoch, only_if_airing=value)
                assert (await http(server.http.port, 'POST', '/cut-in', headers=auth, body=bad))[0] == 400
        finally:
            await server.stop()
    asyncio.run(run())


def test_talk_over_cut_waits_for_the_voice_to_be_out():
    """Speaking while an approved line is still in synthesis must not discard it."""
    async def run():
        gate = asyncio.Event()

        class SlowTTS:
            available = True

            async def synth(self, text):
                await gate.wait()
                return None

        c = StageController(StageConfig(host_turns=True))
        c.tts = SlowTTS()
        c.set_mode('approval')
        epoch = c.state.epoch
        line = PendingLine('l1', epoch, 'turn', 'approved reply', c.clock())
        c._airing = line
        task = asyncio.ensure_future(c.air_one(line))
        await asyncio.sleep(0.05)
        assert c._audible is False
        c.cut_in(epoch, only_if_airing=True)
        assert c.state.epoch == epoch            # nothing cut during synthesis
        gate.set()
        for _ in range(100):
            if c._audible:
                break
            await asyncio.sleep(0.01)
        assert c._audible is True
        c.cut_in(epoch, only_if_airing=True)
        assert c.state.epoch == epoch + 1        # audible -> cut
        assert await task is True
        assert c._audible is False
        # the explicit owner cut still cuts at any time
        c._airing = PendingLine('l2', c.state.epoch, 'turn', 'x', c.clock())
        before = c.state.epoch
        c.cut_in(before)
        assert c.state.epoch == before + 1
    asyncio.run(run())
