"""Automatic failures show a fixed standby while keeping the work view private."""
import asyncio

import pytest

from onair.overlay.server import EventHub, ev_state
from onair.presentation.deck import Program
from onair.presentation.surface import PresentationSurface
from onair.beats import SeedBank
from onair.stage import ControlError, PendingLine, StageConfig, StageController


def controller(clock, guard):
    bank = SeedBank.from_data({'segments': [{'name': 'opening', 'beats': [
        {'id': 'b-1', 'text': 'A quiet beat.', 'provenance': 'fixture'}]}]})
    c = StageController(StageConfig(dead_air_seconds=5, turn_gap_s=0, host_turns=True),
                        clock=clock, guard=guard, seed_bank=bank)
    c.set_mode('approval')
    c.set_mode('autonomous')
    return c


def test_startup_off_air_does_not_attribute_an_owner_action(clock, guard):
    c = StageController(clock=clock, guard=guard)
    assert c.snapshot()['mode'] == 'off_air'
    assert c.snapshot()['off_air_reason'] is None


def test_failure_standby_is_fixed_and_clips_revoked(clock, guard):
    c = controller(clock, guard)
    clock.advance(10)
    turn = c.tick()
    c._clips['old'] = object()
    c.model_failed(turn.turn_id, 'usage_limit')
    assert c.snapshot()['standby'] == {'active': True, 'reason': 'technical_difficulties'}
    assert c._state_event()['standby'] == {'active': True, 'reason': 'technical_difficulties'}
    assert c.clip('old') is None
    row = next(r for r in c.receipts.tail(40) if r['kind'] == 'clips_revoked')
    assert row['reason'] == 'model_failure' and row['count'] == 1
    # Even an explicitly approved line cannot air while paused; it remains held.
    line = PendingLine('line', c.state.epoch, 'test', 'A safe line.', clock())
    c._approval[line.line_id] = line
    with pytest.raises(ControlError, match='paused'):
        c.approve('line', c.state.epoch)
    assert c.pending_lines() and not asyncio.run(c.air_one(line))
    c.stop()
    assert c.snapshot()['standby'] == {'active': True, 'reason': 'technical_difficulties'}
    c.set_mode('approval')
    assert c.snapshot()['standby'] == {'active': False, 'reason': None}


def test_runtime_guard_off_air_standby_survives_latch_clear_until_rearm(clock, guard):
    c = controller(clock, guard)
    c.set_mode('off_air', reason='runtime_guard')
    assert c.snapshot()['standby'] == {'active': True, 'reason': 'technical_difficulties'}
    c.set_mode('approval')
    assert c.snapshot()['standby'] == {'active': False, 'reason': None}


def test_fixed_standby_never_exposes_work_and_restores_after_rearm():
    p = Program()
    assert p.work_action({'action': 'show', 'title': 'Private work', 'panes': [
        {'kind': 'text', 'label': 'Log', 'content': 'private output'}]})[0]
    p.work_visibility(True)
    surf = PresentationSurface(p, overlay_url=lambda: 'http://127.0.0.1:1/overlay')
    surf._trusted_overlay = 'http://127.0.0.1:1/overlay'
    surf._visibility(False, standby=True)
    snap = p.public()
    assert snap['mode'] == 'standby' and snap['standby'] == {'title': "We'll be right back", 'note': ''}
    assert snap['work'] is None and snap['slide'] is None and 'private output' not in repr(snap)
    surf._visibility(True, standby=False)
    assert p.public()['mode'] == 'work'
    surf._visibility(False, standby=False)
    assert p.public()['mode'] == 'privacy' and p.public()['work'] is None


def test_owner_pause_revokes_clips_without_exposing_a_standby(clock, guard):
    from onair.tts import Clip
    c = controller(clock, guard)
    c._clips['old'] = Clip(b'fixture', 'audio/wav', 1)
    c.set_mode('paused')
    assert c.clip('old') is None
    assert c.snapshot()['standby'] == {'active': False, 'reason': None}
    assert any(r.get('reason') == 'paused' and r.get('count') == 1
               for r in c.receipts.tail(30) if r['kind'] == 'clips_revoked')


def test_pause_during_synthesis_never_emits_caption_or_audio(clock, guard):
    from onair.tts import Clip
    async def run(resume_early):
        c = controller(clock, guard)
        started, release = asyncio.Event(), asyncio.Event()
        class DelayedVoice:
            available = True
            async def synth(self, text):
                started.set()
                await release.wait()
                return Clip(b'fixture', 'audio/wav', 1)
        c.tts = DelayedVoice()
        clock.advance(10)
        turn = c.tick()
        assert turn
        line = PendingLine('line', c.state.epoch, turn.turn_id, 'A safe line.', clock())
        airing = asyncio.create_task(c.air_one(line))
        await started.wait()
        c.model_failed(turn.turn_id, 'usage_limit')
        if resume_early:
            c.set_mode('approval')
        release.set()
        assert await airing is False
        assert not [e for e in c.hub.history if e['type'] in ('caption', 'audio')]
        assert not c._clips
    asyncio.run(run(False))
    asyncio.run(run(True))


def test_standby_state_is_a_bounded_overlay_event():
    hub = EventHub()
    state = ev_state(False, 'paused', False, False,
                     standby={'active': True, 'reason': 'technical_difficulties'})
    frame = hub.publish(state)
    assert frame['standby'] == {'active': True, 'reason': 'technical_difficulties'}
    for invalid in ({'active': 1, 'reason': 'technical_difficulties'},
                    {'active': True, 'reason': 'private detail'}, {'active': True, 'reason': 'technical_difficulties', 'text': 'bad'}):
        with pytest.raises(ValueError, match='standby'):
            hub.publish(ev_state(False, 'paused', False, False, standby=invalid))
