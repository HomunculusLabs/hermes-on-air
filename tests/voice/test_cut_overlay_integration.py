"""Cut-in drives the shipped overlay from real stage controller events."""
import asyncio
import json
from pathlib import Path
import shutil

import pytest

from onair.stage import ControlError, PendingLine, StageConfig, StageController
from onair.tts import Clip


def test_cut_stops_the_real_stage_overlay_path():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is required for the program player integration')

    async def run():
        class Voice:
            available = True
            async def synth(self, text):
                return Clip(b'stub', 'audio/wav', 30)

        c = StageController(StageConfig(host_turns=True), tts=Voice())
        c.set_mode('approval')
        old = c.state.epoch
        line = PendingLine('airing', old, 'turn', 'A safe reply', c.clock())
        loop = asyncio.create_task(c._air_loop())
        c._air_q.put_nowait(line)
        proc = await asyncio.create_subprocess_exec(
            node, str(Path(__file__).with_name('cut_overlay_driver.cjs')),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE)

        async def deliver(events):
            proc.stdin.write(json.dumps(events).encode() + b'\n')
            await proc.stdin.drain()
            return json.loads(await asyncio.wait_for(proc.stdout.readline(), 5))

        try:
            for _ in range(100):
                if any(e['type'] == 'audio' for e in c.hub.history):
                    break
                await asyncio.sleep(.01)
            before = list(c.hub.history)
            assert any(e['type'] == 'audio' for e in before)
            assert c._airing is line
            view = await deliver(before)
            assert view['caption'] == line.text and not view['hidden']
            assert len(view['players']) == 1 and not view['players'][0]['paused']
            queued = PendingLine('queued', old, 'turn', 'waiting', c.clock())
            pending = PendingLine('pending', old, 'turn', 'approval', c.clock())
            c._air_q.put_nowait(queued)
            c._approval[pending.line_id] = pending
            assert c.queue_host_turn('host waiting', old, 'host-id')['ok']
            unchanged = (c.state.mode, c.state.privacy, c.state.muted)
            result = c.cut_in(old)
            cut_events = list(c.hub.history)[len(before):]
            assert [e['type'] for e in cut_events] == ['caption_clear']
            scene = await asyncio.create_subprocess_exec(
                node, str(Path(__file__).with_name('cut_scene_driver.cjs')),
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE)
            out, err = await asyncio.wait_for(scene.communicate(json.dumps({'before': before, 'cut': cut_events}).encode()), 5)
            assert scene.returncode == 0, err.decode()
            rendered = json.loads(out)
            assert rendered['before']['speaking'] == 'true' and rendered['before']['mouth'] > .14
            assert rendered['after'] == {'speaking': 'false', 'mouth': .14, 'dataset': '0'}
            view = await deliver(cut_events)
            assert view['players'][0]['paused'] and view['players'][0]['src'] == ''
            assert view['hidden'] and view['caption'] == ''
            assert not c._approval and c._air_q.empty() and not c._clips
            assert [(h.id, h.epoch) for h in c._host_pending] == [('host-id', old + 1)]
            assert (c.state.mode, c.state.privacy, c.state.muted) == unchanged
            assert result['epoch'] == old + 1
            await asyncio.sleep(.05)
            assert any(r.get('line_id') == 'airing' and r.get('disposition') == 'interrupted'
                       for r in c.receipts.tail())
            with pytest.raises(ControlError):
                c.cut_in(old)
            assert c.state.epoch == result['epoch']
            # A delayed old audio event cannot restart the cleared caption/player.
            old_audio = next(e for e in before if e['type'] == 'audio')
            view = await deliver([dict(old_audio, seq=cut_events[-1]['seq'] + 1)])
            assert len(view['players']) == 1 and view['players'][0]['paused']
            assert not any(e['type'] in ('audio', 'caption') for e in list(c.hub.history)[len(before):])
        finally:
            proc.stdin.close()
            await asyncio.wait_for(proc.wait(), 5)
            loop.cancel()
            try:
                await loop
            except asyncio.CancelledError:
                pass
    asyncio.run(run())
