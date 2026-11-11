"""Stage controller: modes, epochs, approval queue, operator controls mid-turn, dead air,
beats once per show, receipts, and no replay on restart."""

from __future__ import annotations

import asyncio
import json
import os
import stat

import pytest

from onair.beats import SeedBank
from onair.stage import ControlError, StageConfig, StageController, render_turn_prompt, split_spoken_lines
from onair.tts import FakeTTS
from onair.types import ChatMsg, Verdict

T = 1_800_000_000.0
BANK = {
    "version": 1,
    "segments": [
        {
            "name": "opening",
            "beats": [
                {"id": "b-one", "text": "We are building a lamp tonight.", "provenance": "rundown 1"},
                {"id": "b-two", "text": "The lamp dims slowly over three seconds.", "provenance": "notes"},
            ],
        },
        {"name": "wrap", "beats": [{"id": "b-three", "text": "Next time it gets a case.", "provenance": "rundown 9"}]},
    ],
}


def mk_ctrl(tmp_path, clock, guard, **kw):
    cfg = StageConfig(
        dead_air_seconds=kw.pop("dead_air", 45.0),
        turn_gap_s=0.0,
        air_pace=0.0,
        host_name="Host",
        agent_names=("Nova",),
        never_say=("banana split",),
    )
    dispatched = []

    async def dispatch(turn):
        dispatched.append(turn)

    c = StageController(
        cfg,
        data_dir=tmp_path,
        guard=guard,
        clock=clock,
        dispatch=kw.pop("dispatch", dispatch),
        seed_bank=kw.pop("bank", SeedBank.from_data(BANK)),
        **kw,
    )
    return c, dispatched


def q(text, name, ts, first=False):
    q.n = getattr(q, "n", 0) + 1
    return ChatMsg("twitch", "chan", f"s{q.n}", f"u-{name}", name, text, ts, first_msg=first)


def returning(c, name, ts):
    c.ingest(q("hello again friends", name, ts - 300))


def test_starts_off_air_and_arms_into_approval(tmp_path, clock, guard):
    c, _ = mk_ctrl(tmp_path, clock, guard)
    assert c.state.mode == "off_air" and not c.live
    with pytest.raises(ControlError):
        c.set_mode("autonomous")  # arming always enters approval first
    c.set_mode("approval")
    assert c.state.mode == "approval" and c.live
    c.set_mode("autonomous")
    assert c.state.mode == "autonomous"
    with pytest.raises(ControlError):
        c.set_mode("party")


def test_no_turns_off_air_paused_or_in_privacy(tmp_path, clock, guard):
    c, sent = mk_ctrl(tmp_path, clock, guard)
    c.ingest(q("how does the overlay work?", "a", clock.t, first=True))
    assert c.tick() is None  # off air: chat isn't even pooled
    c.set_mode("approval")
    c.set_mode("paused")
    c.ingest(q("how does the caption timing work?", "b", clock.t, first=True))
    assert c.tick() is None
    c.set_mode("approval")
    c.set_privacy(True)
    assert c.tick() is None
    c.set_privacy(False)
    assert c.tick() is None  # the cut cleared what was waiting: nothing from before it is answered
    c.ingest(q("how loud is the fan now?", "c", clock.t, first=True))
    assert c.tick() is not None


def test_arming_never_skips_approval(tmp_path, clock, guard):
    """Invariant 3: no route from off_air reaches autonomous without entering approval."""
    c, _ = mk_ctrl(tmp_path, clock, guard)
    for target in ("paused", "autonomous"):
        with pytest.raises(ControlError) as e:
            c.set_mode(target)
        assert e.value.status == 409 and c.state.mode == "off_air"
    c.set_mode("approval")
    c.set_mode("paused")
    c.set_mode("autonomous")  # allowed: this show has been in approval
    assert c.state.mode == "autonomous"
    c.stop()
    assert c.state.mode == "paused"
    c.set_mode("autonomous")  # resuming after Stop all within the same show
    c.set_mode("off_air")
    with pytest.raises(ControlError):
        c.set_mode("paused")  # a new show starts in approval again
    with pytest.raises(ControlError):
        c.set_mode("autonomous")
    # defence in depth: even a forged paused state can't skip approval in a fresh show
    c.state.mode = "paused"
    with pytest.raises(ControlError):
        c.set_mode("autonomous")


def test_privacy_and_mute_take_only_real_booleans(tmp_path, clock, guard):
    c, _ = mk_ctrl(tmp_path, clock, guard)
    c.set_privacy(True)
    for bad in (None, "cover", 0, 1, "", {}, []):
        with pytest.raises(ControlError) as e:
            c.set_privacy(bad)  # type: ignore[arg-type]
        assert e.value.status == 400
        with pytest.raises(ControlError):
            c.set_muted(bad)  # type: ignore[arg-type]
    assert c.state.privacy is True and c.state.muted is False


def test_regular_never_becomes_a_newcomer_again_after_busy_chat(tmp_path, clock, guard):
    """Newcomer status is per show (seen set), not read from the 500-message / 900 s window."""
    c, _ = mk_ctrl(tmp_path, clock, guard)
    c.set_mode("approval")
    assert c.ingest(q("hello all", "regular_rae", clock.t)).rule == "newcomer"
    for i in range(c.cfg.window_max + 1):  # count-based expiry of the window
        clock.advance(0.15)
        c.ingest(q(f"line number {i} from the crowd", f"crowd_{i % 40}", clock.t))
    assert not any(m.display_name == "regular_rae" for m in c.chat_window())
    v = c.ingest(q("the lamp idea is cool honestly", "regular_rae", clock.t))
    assert (v.kind, v.rule) == ("routine", "chatter")


def test_regular_stays_seen_after_the_window_times_out(tmp_path, clock, guard):
    c, _ = mk_ctrl(tmp_path, clock, guard)
    c.set_mode("approval")
    c.ingest(q("hello all", "regular_rae", clock.t))
    for i in range(40):  # continuous chat for longer than window_s
        clock.advance(c.cfg.window_s / 30)
        c.ingest(q(f"still chatting {i} about the build", f"crowd_{i % 7}", clock.t))
    assert not any(m.display_name == "regular_rae" for m in c.chat_window())
    v = c.ingest(q("the lamp idea is cool honestly", "regular_rae", clock.t))
    assert (v.kind, v.rule) == ("routine", "chatter")
    # a new show (arming from off air) starts with nobody seen
    c.set_mode("off_air")
    c.set_mode("approval")
    assert c.ingest(q("back again for the next show", "regular_rae", clock.t + 60)).rule == "newcomer"


def test_channel_wide_clear_drops_all_waiting_chat(tmp_path, clock, guard):
    c, _ = mk_ctrl(tmp_path, clock, guard)
    c.set_mode("approval")
    c.ingest(q("how does the overlay work?", "a", clock.t, first=True))
    c.ingest(q("what's the lamp made of?", "b", clock.t, first=True))
    assert c.moderate(user_id=None, msg_id=None) == 0  # a targetless call alone drops nothing
    assert c.moderate(clear_all=True) == 2
    assert c.tick() is None


def test_every_show_gets_a_fresh_agent_session(tmp_path, clock, guard):
    c, _ = mk_ctrl(tmp_path, clock, guard)
    c.set_mode("approval")
    c.ingest(q("how does the overlay work?", "a", clock.t, first=True))
    t1 = c.tick()
    c.turn_done(t1.turn_id)
    c.set_mode("off_air")
    c.set_mode("approval")
    c.ingest(q("what are we building?", "b", clock.t, first=True))
    t2 = c.tick()
    assert t1.session.startswith(f"stage-{c.boot_id}-") and t2.session.startswith(f"stage-{c.boot_id}-")
    assert t1.session != t2.session
    c2, _ = mk_ctrl(tmp_path, clock, guard)  # and a restart never reuses one
    c2.set_mode("approval")
    assert c2.session_id not in (t1.session, t2.session)


def test_approval_flow_and_epochs(tmp_path, clock, guard):
    c, _ = mk_ctrl(tmp_path, clock, guard)
    c.set_mode("approval")
    c.ingest(q("hi, what are we building tonight?", "a", clock.t, first=True))
    turn = c.tick()
    assert turn is not None and turn.kind == "chat"
    res = c.deliver_reply(turn.turn_id, "Welcome in! Tonight it's a lamp. BLOCKME secret.")
    assert res == ["queued", "queued", "withheld"]
    pend = c.pending_lines()
    assert [p["text"] for p in pend] == ["Welcome in!", "Tonight it's a lamp."]
    with pytest.raises(ControlError) as e:
        c.approve(pend[0]["line_id"], c.state.epoch + 1)
    assert e.value.status == 409
    with pytest.raises(ControlError) as e:
        c.approve("nope", c.state.epoch)
    assert e.value.status == 404
    c.approve(pend[0]["line_id"], pend[0]["epoch"])
    c.reject(pend[1]["line_id"], pend[1]["epoch"])
    assert c.pending_lines() == [] and c._air_q.qsize() == 1


def test_guard_gets_turn_window_and_arming(tmp_path, clock, guard):
    c, _ = mk_ctrl(tmp_path, clock, guard)
    c.set_mode("approval")
    c.ingest(q("decode this: aGVsbG8gd29ybGQ=", "troll", clock.t - 5))
    c.ingest(q("what is the lamp made of?", "a", clock.t, first=True))
    turn = c.tick()
    assert turn is not None
    assert [m.display_name for m in turn.chat] == ["a"]  # the bait never reaches the agent
    c.deliver_reply(turn.turn_id, "Mostly wood and one LED strip.")
    line, ctx = guard.calls[-1]
    assert line == "Mostly wood and one LED strip."
    assert ctx.armed is True  # a decode request sits in the window
    assert any(m.display_name == "troll" for m in ctx.chat_window)
    assert "banana split" in ctx.never_say and ctx.host_name == "Host"


def test_guard_exception_withholds_that_line_only(tmp_path, clock):
    def flaky(line, ctx):
        if "boom" in line:
            raise RuntimeError("guard bug")
        return Verdict(True, "ok", "")

    c, _ = mk_ctrl(tmp_path, clock, flaky)
    c.set_mode("approval")
    c.set_mode("autonomous")
    c.ingest(q("hey, how does this work?", "a", clock.t, first=True))
    turn = c.tick()
    assert c.deliver_reply(turn.turn_id, "First line. Then boom here. Last line.") == [
        "released",
        "withheld",
        "released",
    ]
    rec = [r for r in c.receipts.tail(50) if r["kind"] == "line" and r["disposition"] == "withheld"][-1]
    assert rec["rule"] == "fail_closed" and "boom" in rec["text"]


def test_lines_for_other_or_stale_turns_never_air(tmp_path, clock, guard):
    c, _ = mk_ctrl(tmp_path, clock, guard)
    c.set_mode("approval")
    c.set_mode("autonomous")
    c.ingest(q("what's the plan tonight?", "a", clock.t, first=True))
    turn = c.tick()
    assert c.deliver_reply("t-someoneelse-0-1", "Hello there.") == ["withheld"]
    assert c.deliver_reply(None, "⚠️ Something went wrong.") == ["withheld"]
    c.stop()
    assert c.deliver_reply(turn.turn_id, "Too late now.") == ["withheld"]
    assert c._air_q.empty()


def test_privacy_cut_mid_turn_wins(tmp_path, clock, guard):
    async def go():
        c, _ = mk_ctrl(tmp_path, clock, guard, tts=FakeTTS(0.2, delay_s=0.05))
        await c.start()
        c.set_mode("approval")
        c.set_mode("autonomous")
        c.ingest(q("how is the lamp powered?", "a", clock.t, first=True))
        turn = c.tick()
        assert turn is not None and c.turn is turn
        old_epoch = c.state.epoch
        c.set_privacy(True)  # model still "thinking"
        assert c.turn is None and c.state.epoch == old_epoch + 1
        assert c.deliver_reply(turn.turn_id, "It runs on USB power.") == ["withheld"]
        await asyncio.sleep(0.2)
        assert not [e for e in c.hub.history if e["type"] in ("caption", "audio")]
        ev = [e for e in c.hub.history if e["type"] == "state"][-1]
        assert ev["privacy"] is True and ev["live"] is False
        assert c.hub.history[-1]["type"] == "caption_clear"
        await c.shutdown()

    asyncio.run(go())


def test_privacy_cut_during_synthesis_and_clip_404(tmp_path, clock, guard):
    async def go():
        tts = FakeTTS(0.3, delay_s=0.15)
        c, _ = mk_ctrl(tmp_path, clock, guard, tts=tts)
        await c.start()
        c.set_mode("approval")
        c.set_mode("autonomous")
        c.ingest(q("what's that sound?", "a", clock.t, first=True))
        turn = c.tick()
        assert c.deliver_reply(turn.turn_id, "That was the fan. It's quiet now.") == ["released", "released"]
        for _ in range(100):
            await asyncio.sleep(0.01)
            if any(e["type"] == "audio" for e in c.hub.history):
                break
        audio = [e for e in c.hub.history if e["type"] == "audio"][0]
        clip_id = audio["id"]
        assert c.clip(clip_id) is not None  # released clip is servable
        captions = [e for e in c.hub.history if e["type"] == "caption"]
        assert audio["caption_id"] == captions[0]["id"] and audio["url"] == f"/audio/{clip_id}.wav"
        c.set_privacy(True)  # second line is still synthesizing
        assert c.clip(clip_id) is None  # every clip 404s after the cut
        await asyncio.sleep(0.3)
        assert len([e for e in c.hub.history if e["type"] == "caption"]) == 1
        assert any(r["kind"] in ("line_dropped", "epoch") for r in c.receipts.tail(50))
        await c.shutdown()

    asyncio.run(go())


def test_mute_and_stop(tmp_path, clock, guard):
    async def go():
        c, _ = mk_ctrl(tmp_path, clock, guard, tts=FakeTTS(0.05))
        await c.start()
        c.set_mode("approval")
        c.set_mode("autonomous")
        c.set_muted(True)
        c.ingest(q("hello, what are we doing?", "a", clock.t, first=True))
        turn = c.tick()
        c.deliver_reply(turn.turn_id, "Building a lamp.")
        await asyncio.sleep(0.1)
        assert [e["type"] for e in c.hub.history if e["type"] in ("caption", "audio")] == ["caption"]
        c.stop()
        assert c.state.mode == "paused" and c.turn is None
        assert c.hub.history[-1]["type"] == "caption_clear"
        await c.shutdown()

    asyncio.run(go())


def test_dead_air_fills_with_beats_once_per_show(tmp_path, clock, guard):
    bank = SeedBank.from_data({**BANK, "exhaustion_policy": "stop", "reset_on_rearm": False})
    c, sent = mk_ctrl(tmp_path, clock, guard, dead_air=30, bank=bank)
    c.set_mode("approval")
    c.set_mode("autonomous")
    assert c.tick() is None
    clock.advance(31)
    t1 = c.tick()
    assert t1 is not None and t1.kind == "beat" and t1.beat.beat_id == "b-one"
    assert "<show_note" in render_turn_prompt(t1)
    c.turn_done(t1.turn_id)
    assert c.tick() is None  # quiet timer restarted
    clock.advance(31)
    t2 = c.tick()
    c.turn_done(t2.turn_id)
    clock.advance(31)
    t3 = c.tick()
    c.turn_done(t3.turn_id)
    assert [t.beat.beat_id for t in (t1, t2, t3)] == ["b-one", "b-two", "b-three"]
    clock.advance(31)
    assert c.tick() is None  # bank exhausted: each beat once
    usage = json.loads((tmp_path / "seedbank-usage.json").read_text())
    assert sorted(usage["used"]) == ["b-one", "b-three", "b-two"]
    c2, _ = mk_ctrl(tmp_path, clock, guard, dead_air=30,
                    bank=SeedBank.from_data({**BANK, "exhaustion_policy": "stop", "reset_on_rearm": False}))
    c2.set_mode("approval")
    clock.advance(31)
    assert c2.tick() is None


def test_worthy_chat_beats_a_beat_and_no_beat_while_paused(tmp_path, clock, guard):
    c, _ = mk_ctrl(tmp_path, clock, guard, dead_air=10)
    c.set_mode("approval")
    c.set_mode("autonomous")
    clock.advance(20)
    c.ingest(q("how long did this take to build?", "a", clock.t, first=True))
    t = c.tick()
    assert t is not None and t.kind == "chat"
    c.turn_done(t.turn_id)
    c.set_mode("paused")
    clock.advance(60)
    assert c.tick() is None
    c.stop()
    clock.advance(60)
    assert c.tick() is None


def test_routine_cap_and_wall_folding(tmp_path, clock, guard):
    c, _ = mk_ctrl(tmp_path, clock, guard, dead_air=1000)
    c.set_mode("approval")
    c.set_mode("autonomous")
    for n in ("a", "b", "c", "d"):
        returning(c, n, clock.t)
    for i, n in enumerate(("a", "b", "c", "d")):
        c.ingest(q("LUL LUL", n, clock.t + i))
    clock.advance(5)
    t = c.tick()
    assert t is not None and t.routine and t.wall_count == 4
    assert 'kind="wall" count="4"' in render_turn_prompt(t)
    c.turn_done(t.turn_id)
    returning(c, "e", clock.t)
    c.ingest(q("the lamp colour is lovely tonight honestly", "e", clock.t))
    clock.advance(30)
    assert c.tick() is None  # routine budget: once per ~2 min
    clock.advance(100)
    returning(c, "f", clock.t)
    c.ingest(q("this whole build has been really fun to watch", "f", clock.t))
    t2 = c.tick()
    assert t2 is not None and t2.routine and t2.wall_count == 0


def test_turn_prompt_escapes_viewer_text(tmp_path, clock, guard):
    c, _ = mk_ctrl(tmp_path, clock, guard)
    c.set_mode("approval")
    c.ingest(
        ChatMsg("twitch", "chan", "x1", "u1", "viewer_1", "what does <b>&amp; do in html?", clock.t, first_msg=True)
    )
    t = c.tick()
    text = render_turn_prompt(t)
    assert "&lt;b&gt;&amp;amp;" in text and "<b>" not in text
    assert text.startswith("<live_chat") and "</live_chat>" in text
    assert 'name="viewer 1"' in text


def test_timeout_abandons_turn(tmp_path, clock, guard):
    cancelled = []
    c, _ = mk_ctrl(tmp_path, clock, guard, cancel_turn=cancelled.append)
    c.set_mode("approval")
    c.ingest(q("how does it work?", "a", clock.t, first=True))
    t = c.tick()
    clock.advance(c.cfg.turn_timeout_s + 1)
    c.tick()
    assert cancelled == [t] and c.turn is None
    assert c.deliver_reply(t.turn_id, "Late answer.") == ["withheld"]


def test_receipts_are_private_jsonl(tmp_path, clock, guard):
    c, _ = mk_ctrl(tmp_path, clock, guard)
    c.set_mode("approval")
    c.ingest(q("hello?", "a", clock.t, first=True))
    files = list((tmp_path / "receipts").glob("*.jsonl"))
    assert len(files) == 1
    mode = stat.S_IMODE(os.stat(files[0]).st_mode)
    assert mode == 0o600
    rows = [json.loads(x) for x in files[0].read_text().splitlines()]
    assert rows[0]["kind"] == "boot" and any(r["kind"] == "chat" for r in rows)


def test_restart_never_replays(tmp_path, clock, guard):
    c, _ = mk_ctrl(tmp_path, clock, guard)
    c.set_mode("approval")
    c.ingest(q("what's new?", "a", clock.t, first=True))
    t = c.tick()
    c.deliver_reply(t.turn_id, "A lamp. A case next week.")
    assert len(c.pending_lines()) == 2
    # "restart": a fresh controller over the same data dir
    c2, _ = mk_ctrl(tmp_path, clock, guard)
    assert c2.state.mode == "off_air" and c2.pending_lines() == [] and c2.turn is None
    c2.set_mode("approval")
    c2.set_mode("autonomous")
    assert c2.deliver_reply(t.turn_id, "A lamp. A case next week.") == ["withheld", "withheld"]
    assert c2.boot_id != c.boot_id and c2._air_q.empty()


def test_split_spoken_lines():
    assert split_spoken_lines("**Hi** chat! Welcome in.\n\n- one more\n⏳ Working...") == [
        "Hi chat!",
        "Welcome in.",
        "one more",
        "⏳ Working...",
    ]
    long = "word " * 80
    assert all(len(x) <= 221 for x in split_spoken_lines(long))
