"""Exercise word filtering through the real fake-show entry point and seed turns."""
import asyncio
import codecs
import importlib.util
from pathlib import Path
import sys

import pytest

from onair.beats import SeedBank
from onair.stage import StageConfig, StageController


def word(value):
    return codecs.decode(value, 'rot13')


def load_show():
    path = Path(__file__).resolve().parents[2] / 'tools' / 'dev_fake_show.py'
    spec = importlib.util.spec_from_file_location('onair_dev_fake_show_integration', path)
    show = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(show)
    return show


@pytest.mark.parametrize('mode,encoded,expected', [
    ('mask', 'avttre', None),
    ('allow', 'ergneq', None),
    ('block', 'shpx', None),
    ('mask', 'shpx', 'hello [filtered] friend'),
    ('allow', 'shpx', 'raw'),
    ('block', None, 'hello lovely friend'),
])
def test_fake_show_main_flag_drives_real_overlay(monkeypatch, tmp_path, mode, encoded, expected):
    show = load_show()
    text = 'hello ' + (word(encoded) if encoded else 'lovely') + ' friend'
    if expected == 'raw':
        expected = text
    monkeypatch.setattr(show, 'SCRIPT', [(0, 'viewer', text, True, ())])
    events = []
    overlay_class = show.OverlayServer

    class RecordingOverlay(overlay_class):
        def __init__(self, hub, *args, **kwargs):
            hub.add_listener(events.append)
            super().__init__(hub, *args, **kwargs)

    monkeypatch.setattr(show, 'OverlayServer', RecordingOverlay)
    monkeypatch.setattr(sys, 'argv', [
        'dev_fake_show.py', '--profanity', mode, '--guard', 'real', '--port', '0',
        '--control-port', '0', '--data-dir', str(tmp_path), '--duration', '0.1',
        '--no-alerts', '--once',
    ])
    assert asyncio.run(show.main()) == 0
    assert [e['text'] for e in events if e['type'] == 'chat'] == ([] if expected is None else [expected])


@pytest.mark.parametrize('mode,encoded,allowed', [
    ('mask', 'ergneq', False), ('block', 'ergneq', False), ('allow', 'ergneq', False),
    ('mask', 'shpx', False), ('block', 'shpx', False), ('allow', 'shpx', True),
    ('mask', None, True),
])
def test_fake_agent_seed_reply_passes_real_guard(mode, encoded, allowed):
    show = load_show()
    text = 'A lovely lamp' if encoded is None else 'A ' + word(encoded) + ' lamp'
    bank = SeedBank.from_data({'version': 1, 'segments': [{'name': 'opening', 'beats': [
        {'id': 'beat-one', 'text': text, 'provenance': 'test notes'},
    ]}]})
    assert bank.error is None and len(bank.beats()) == 1
    c = StageController(StageConfig(profanity=mode), seed_bank=bank, clock=lambda: 1_800_000_000)
    c.set_mode('approval')
    turn = c._start_beat_turn(bank.beats()[0], c.clock())
    asyncio.run(show.fake_agent(c, turn, 0))
    pending = c.pending_lines()
    # The canned introduction is a separate, benign sentence and remains queued.
    assert any(line['text'] == 'Quick one while chat is quiet.' for line in pending)
    assert any(text in line['text'] for line in pending) is allowed
