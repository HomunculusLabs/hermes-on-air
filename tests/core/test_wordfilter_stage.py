import codecs
import asyncio
import json

import pytest

from onair.stage import AlertsConfig, StageConfig, StageController
from onair.types import ChatMsg, Verdict


def word(s):
    return codecs.decode(s, 'rot13')


def chat(c, text, name='viewer'):
    c.ingest(ChatMsg('twitch', 'chan', 'm', 'u', name, text, 1_800_000_000, first_msg=True))
    return [e for e in c.hub.history if e['type'] == 'chat']


@pytest.mark.parametrize('mode, expected', [('mask', 'hello [filtered] friend'), ('block', None), ('allow', 'hello ' + word('shpx') + ' friend')])
def test_chat_profanity_modes(mode, expected):
    c = StageController(StageConfig(profanity=mode), clock=lambda: 1_800_000_000)
    c.set_mode('approval')
    events = chat(c, 'hello ' + word('shpx') + ' friend')
    assert [e['text'] for e in events] == ([] if expected is None else [expected])


def test_default_masks_and_slurs_never_display():
    c = StageController(clock=lambda: 1_800_000_000)
    c.set_mode('approval')
    assert chat(c, 'hello ' + word('shpx') + ' friend')[0]['text'] == 'hello [filtered] friend'
    assert len(chat(c, 'hello ' + word('avttre') + ' friend')) == 1


def test_name_guard_and_alert_fallback():
    c = StageController(clock=lambda: 1_800_000_000)
    c.set_mode('approval')
    assert chat(c, 'hello friend', word('shpx'))[0]['name'] == 'someone'
    assert c._alert_name(word('shpx')) == 'someone'
    assert c._alert_name(word('avttre')) == 'someone'
    assert c._alert_name('friendly_viewer') == 'friendly viewer'


def test_other_guard_checks_run_on_sanitized_text():
    seen = []
    def guard(text, ctx):
        seen.append(text)
        return Verdict('BLOCKME' not in text, 'test', 'test')
    c = StageController(guard=guard, clock=lambda: 1_800_000_000)
    c.set_mode('approval')
    assert chat(c, 'hello ' + word('shpx') + ' BLOCKME') == []
    assert 'hello [filtered] BLOCKME' in seen


def test_invalid_profanity_config_rejected():
    with pytest.raises(ValueError):
        StageConfig(profanity='typo')


@pytest.mark.parametrize('canary', ['avttre', 'ergneq'])
@pytest.mark.parametrize('mode', ['mask', 'block', 'allow'])
def test_slurs_hidden_in_every_mode(mode, canary):
    from onair.chat.gate import classify
    from onair.guard import check_line
    from onair.types import GuardContext

    text = 'hello ' + word(canary) + ' friend'
    msg = ChatMsg('twitch', 'chan', 'm', 'u', 'viewer', text, 1_800_000_000, first_msg=True)
    assert classify(msg, []).rule == 'slur'
    verdict = check_line(text, GuardContext(), profanity=mode)
    assert not verdict.release and verdict.rule == 'slur'
    c = StageController(StageConfig(profanity=mode), clock=lambda: 1_800_000_000)
    c.set_mode('approval')
    assert chat(c, text) == []


def test_fake_show_feeds_to_overlay_and_alert(monkeypatch):
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("onair_dev_fake_show", Path(__file__).resolve().parents[2] / "tools" / "dev_fake_show.py")
    show = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(show)

    async def run():
        c = StageController(StageConfig(alerts=AlertsConfig(quiet_s=0)))
        queue = c.hub.subscribe()
        queue.get_nowait()  # snapshot
        c.set_mode('approval')
        monkeypatch.setattr(show, 'SCRIPT', [(0, 'viewer', 'hello ' + word('shpx') + ' friend', True, ())])
        monkeypatch.setattr(show, 'SUPPORT_SCRIPT', [(0, 'sub', word('avttre'), 0, 1, '')])
        await show.chat_feed(c, False)
        await show.support_feed(c, False)
        c.tick()
        frames = []
        while not queue.empty():
            _, frame = queue.get_nowait()
            frames.append(json.loads(frame.split('data: ', 1)[1]))
        assert [e['text'] for e in frames if e['type'] == 'chat'] == ['hello [filtered] friend']
        assert [e['name'] for e in frames if e['type'] == 'alert'] == ['someone']

    asyncio.run(run())
