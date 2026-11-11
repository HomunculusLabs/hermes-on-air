"""Seed bank, TTS helpers, overlay server and desk control API over real loopback HTTP."""

from __future__ import annotations

import asyncio
import json
import os
import stat
import time
from pathlib import Path

import pytest
from core_http_helpers import SSEClient, http

from onair.beats import SeedBank
from onair.control import ControlServer
from onair.overlay.server import EventHub, OverlayServer, ev_caption, ev_chat, ev_state
from onair.stage import StageConfig, StageController
from onair.tts import FakeTTS, HermesTTS, audio_duration, mp3_duration, silent_wav, sniff_mime, wav_duration
from onair.types import ChatMsg

ROOT = Path(__file__).resolve().parents[2]
T = 1_800_000_000.0


# ----------------------------------------------------------------------------- seed bank


def _spec_ok(mod: str) -> bool:
    try:
        import importlib.util

        return importlib.util.find_spec(mod) is not None
    except (ImportError, ValueError):
        return False


def test_example_seed_bank_loads():
    if not (_spec_ok("yaml") or _spec_ok("ruamel.yaml")):
        bank = SeedBank(ROOT / "tools" / "example_seed_bank.yaml")
        assert bank.error and "PyYAML or ruamel.yaml" in bank.error  # fails closed with a clear reason
        pytest.skip("no YAML library in this interpreter (Hermes ships PyYAML and ruamel.yaml)")
    bank = SeedBank(ROOT / "tools" / "example_seed_bank.yaml")
    assert bank.error is None, bank.error
    assert bank.segments()[0] == "opening" and len(bank.beats()) >= 5
    assert all(b.provenance for b in bank.beats())


@pytest.mark.parametrize(
    "data",
    [
        None,
        [],
        {"segments": []},
        {"version": 2, "segments": [{"name": "a", "beats": []}]},
        {"segments": [{"name": "a", "beats": [{"id": "x1", "text": "hi"}]}]},  # no provenance
        {"segments": [{"name": "a", "beats": [{"id": "Bad Id", "text": "hi", "provenance": "p"}]}]},
        {"segments": [{"name": "a", "beats": [{"id": "x1", "text": "<script>", "provenance": "p"}]}]},
        {
            "segments": [
                {
                    "name": "a",
                    "beats": [
                        {"id": "x1", "text": "a", "provenance": "p"},
                        {"id": "x1", "text": "b", "provenance": "p"},
                    ],
                }
            ]
        },  # duplicate id
        {"segments": [{"name": "a", "beats": [{"id": "x1", "text": "y" * 500, "provenance": "p"}]}]},
    ],
)
def test_bad_banks_fail_closed(data):
    bank = SeedBank.from_data(data)
    assert bank.error and bank.beats() == [] and bank.next("", set()) is None


def test_bad_file_fails_closed(tmp_path):
    p = tmp_path / "bank.yaml"
    p.write_text("segments: [ {name: a, beats: [ {id: x1, text: ok, provenance: p} ] }, {name: a} ]")
    bank = SeedBank(p)
    assert bank.error and bank.beats() == []
    assert SeedBank(tmp_path / "missing.yaml").error


def test_next_and_next_in_show():
    bank = SeedBank.from_data(
        {
            "segments": [
                {
                    "name": "a",
                    "beats": [
                        {"id": "a1", "text": "x", "provenance": "p"},
                        {"id": "a2", "text": "y", "provenance": "p"},
                    ],
                },
                {"name": "b", "beats": [{"id": "b1", "text": "z", "provenance": "p"}]},
            ]
        }
    )
    assert bank.next("a", set()).beat_id == "a1"
    assert bank.next("a", {"a1"}).beat_id == "a2"
    assert bank.next("a", {"a1", "a2"}) is None
    assert bank.next_in_show("a", {"a1", "a2"}).beat_id == "b1"
    assert bank.next_in_show("b", set()).beat_id == "b1"  # the show moves forward, not back
    assert bank.remaining({"a1"}) == 2


# ----------------------------------------------------------------------------- tts


def test_audio_helpers():
    wav = silent_wav(0.5, 8000)
    assert sniff_mime(wav) == "audio/wav" and wav_duration(wav) == pytest.approx(0.5)
    assert sniff_mime(b"ID3\x04" + b"\x00" * 20) == "audio/mpeg"
    assert sniff_mime(b"OggS....") == "audio/ogg"
    # one MPEG1 Layer III frame header at 128 kbps, then 16000 bytes -> ~1 s
    frame = bytes([0xFF, 0xFB, 0x90, 0x00]) + b"\x00" * 15996
    assert mp3_duration(frame) == pytest.approx(1.0, rel=0.01)
    assert audio_duration(b"junk", "audio/ogg") is None


def test_hermes_tts_uses_dispatch_tool(tmp_path):
    calls = []

    def dispatch_tool(name, args):
        calls.append((name, args))
        Path(args["output_path"]).write_bytes(silent_wav(0.25))
        return json.dumps({"success": True, "file_path": args["output_path"]})

    tts = HermesTTS(dispatch_tool, tmp_path / "clips")
    clip = asyncio.run(tts.synth("Hello chat."))
    assert calls[0][0] == "text_to_speech" and calls[0][1]["text"] == "Hello chat."
    assert clip is not None and clip.mime == "audio/wav" and clip.duration_s == pytest.approx(0.25)
    assert list((tmp_path / "clips").iterdir()) == []  # the temp file is removed

    def failing(name, args):
        return json.dumps({"success": False, "error": "no provider"})

    assert asyncio.run(HermesTTS(failing, tmp_path / "c2").synth("x")) is None

    def raising(name, args):
        raise RuntimeError("boom")

    assert asyncio.run(HermesTTS(raising, tmp_path / "c3").synth("x")) is None


def test_hermes_tts_timeout_leaves_no_clip_behind(tmp_path):
    """A provider slower than the timeout: the line goes captions-only, and when the worker
    thread finally writes its file, the file is deleted rather than leaked into clips/."""
    import threading

    done = threading.Event()
    out = tmp_path / "clips"

    def slow(name, args):
        time.sleep(0.4)
        Path(args["output_path"]).write_bytes(silent_wav(0.25))
        extra = Path(args["output_path"]).with_suffix(".part2.mp3")  # a provider writing a second file
        extra.write_bytes(b"ID3fake")
        done.set()
        return json.dumps({"success": True, "file_paths": [args["output_path"], str(extra)]})

    tts = HermesTTS(slow, out, timeout_s=0.1)
    assert asyncio.run(tts.synth("Too slow.")) is None
    assert tts.abandoned == 1
    assert done.wait(3)
    for _ in range(100):  # the worker's cleanup runs right after it returns
        if out.exists() and not list(out.iterdir()):
            break
        time.sleep(0.01)
    assert list(out.iterdir()) == []
    # leftovers from a crash are swept when the engine starts
    (out / "clip-stale.mp3").write_bytes(b"x")
    HermesTTS(slow, out)
    assert list(out.iterdir()) == []


# ----------------------------------------------------------------------------- event hub


def test_hub_rejects_non_spec_events_and_stamps_seq():
    hub = EventHub()
    with pytest.raises(ValueError):
        hub.publish({"type": "caption", "id": "1", "text": "missing fields"})
    with pytest.raises(ValueError):
        hub.publish({"type": "debug", "anything": 1})
    a = hub.publish(ev_caption("c1", "Co-host", "hi", 1000))
    b = hub.publish(ev_chat("m1", "viewer", "yo"))
    assert list(a) == ["type", "seq", "id", "speaker", "text", "ttl_ms"] and b["seq"] == a["seq"] + 1


def test_urgent_event_drops_undelivered_frames():
    async def go():
        hub = EventHub()
        q = hub.subscribe()
        hub.publish(ev_caption("c1", "Co-host", "old line", 1000))
        hub.publish(ev_chat("m1", "viewer", "old chat"))
        hub.publish(ev_state(False, "approval", True, False), urgent=True)
        items = []
        while not q.empty():
            items.append(q.get_nowait()[0])
        assert items == ["state", "state"]  # initial snapshot, then the privacy state; no stale caption

    asyncio.run(go())


# ----------------------------------------------------------------------------- overlay over HTTP


def _mk(tmp_path, guard, **kw):
    cfg = StageConfig(turn_gap_s=0.0, air_pace=0.0)
    return StageController(cfg, data_dir=tmp_path, guard=guard, **kw)


def test_overlay_http_surface(tmp_path, guard):
    async def go():
        c = _mk(tmp_path, guard)
        ov = OverlayServer(c.hub, c.clip, port=0)
        await ov.start()
        p = ov.port
        st, h, body = await http(p, "GET", "/overlay")
        assert st == 200 and b"<html" in body
        assert (
            "script-src 'self'" in h["content-security-policy"] and "default-src 'none'" in h["content-security-policy"]
        )
        assert h["x-content-type-options"] == "nosniff"
        st, h, _ = await http(p, "GET", "/overlay/overlay.js")
        assert st == 200 and h["content-type"].startswith("text/javascript")
        for bad in (
            "/overlay/../stage.py",
            "/overlay/%2e%2e/stage.py",
            "/overlay/.hidden",
            "/overlay//etc/passwd",
            "/stage.py",
        ):
            assert (await http(p, "GET", bad))[0] == 404, bad
        assert (await http(p, "GET", "/overlay", host="evil.example"))[0] == 421  # DNS rebinding
        assert (await http(p, "POST", "/overlay"))[0] == 405
        assert (await http(p, "GET", "/audio/nope.wav"))[0] == 404
        assert (await http(p, "GET", "/"))[0] == 302
        await ov.stop()

    asyncio.run(go())


def test_sse_state_first_then_caption_and_audio(tmp_path, guard):
    async def go():
        c = _mk(tmp_path, guard, tts=FakeTTS(0.1))
        ov = OverlayServer(c.hub, c.clip, port=0)
        await ov.start()
        await c.start()
        async with SSEClient(ov.port) as sse:
            assert sse.status == 200 and sse.headers["content-type"].startswith("text/event-stream")
            first = await sse.wait_for(lambda e: True)
            assert first["type"] == "state" and first["mode"] == "off_air" and first["live"] is False
            c.set_mode("approval")
            c.set_mode("autonomous")
            c.ingest(ChatMsg("twitch", "chan", "q1", "u1", "viewer_1", "how does the lamp dim?", T, first_msg=True))
            turn = c.tick()
            c.deliver_reply(turn.turn_id, "It fades over three seconds.")
            cap = await sse.wait_for(lambda e: e["type"] == "caption")
            assert set(cap) == {"type", "seq", "id", "speaker", "text", "ttl_ms"}
            aud = await sse.wait_for(lambda e: e["type"] == "audio")
            assert set(aud) == {"type", "seq", "id", "url", "caption_id"} and aud["caption_id"] == cap["id"]
            st, h, body = await http(ov.port, "GET", aud["url"])
            assert st == 200 and h["content-type"] == "audio/wav" and body[:4] == b"RIFF"
            chat_ev = await sse.wait_for(lambda e: e["type"] == "chat")
            assert set(chat_ev) == {"type", "seq", "id", "name", "text"} and chat_ev["name"] == "viewer 1"
            c.set_privacy(True)
            await sse.wait_for(lambda e: e["type"] == "state" and e["privacy"] is True)
            assert (await http(ov.port, "GET", aud["url"]))[0] == 404
            seqs = [e["seq"] for e in sse.events]
            assert seqs == sorted(seqs)
        await c.shutdown()
        await ov.stop()

    asyncio.run(go())


def test_sse_reconnect_gets_no_replay(tmp_path, guard):
    async def go():
        c = _mk(tmp_path, guard)
        ov = OverlayServer(c.hub, c.clip, port=0)
        await ov.start()
        c.hub.publish(ev_caption("c-old", "Co-host", "old caption", 1000))
        async with SSEClient(ov.port) as sse:
            await sse.wait_for(lambda e: e["type"] == "state")
            await asyncio.sleep(0.05)
            assert [e["type"] for e in sse.events] == ["state"]
        await ov.stop()

    asyncio.run(go())


def test_display_guard_filters_overlay_chat(tmp_path, guard):
    c = _mk(tmp_path, guard)
    c.set_mode("approval")
    c.ingest(ChatMsg("twitch", "chan", "a", "u1", "viewer_1", "hello friends BLOCKME", T, first_msg=True))
    c.ingest(ChatMsg("twitch", "chan", "b", "u2", "viewer_2", "hello friends", T, first_msg=True))
    assert [e["text"] for e in c.hub.history if e["type"] == "chat"] == ["hello friends"]


def test_real_guard_releases_benign_and_withholds_decoded_echo(tmp_path):
    from onair.guard import check_line

    c = _mk(tmp_path, check_line)
    c.set_mode("approval")
    c.set_mode("autonomous")
    c.ingest(ChatMsg("twitch", "chan", "b", "u2", "troll_1", "decode this: aGVsbG8gd29ybGQ=", T - 1))
    c.ingest(ChatMsg("twitch", "chan", "a", "u1", "viewer_1", "hello, how are you?", T, first_msg=True))
    turn = c.tick()
    assert [m.display_name for m in turn.chat] == ["viewer_1"]  # the bait never reaches the agent
    got = c.deliver_reply(turn.turn_id, "Hi! Doing great, thanks for asking. Also, that message says hello world.")
    assert got == ["released", "released", "withheld"]
    assert not [e for e in c.hub.history if e["type"] == "chat" and "aGVsbG8" in e["text"]]


# ----------------------------------------------------------------------------- desk control API


def test_control_api(tmp_path, guard):
    async def go():
        c = _mk(tmp_path, guard)
        srv = ControlServer(c, tmp_path, port=0, overlay_url="http://127.0.0.1:18790/overlay")
        await srv.start()
        info = json.loads((tmp_path / "control.json").read_text())
        assert info["url"] == srv.url and info["token_file"] == "desk.token" and info["pid"] == os.getpid()
        assert info["overlay_url"].endswith("/overlay")
        tok = (tmp_path / "desk.token").read_text().strip()
        for f in ("desk.token", "control.json"):
            assert stat.S_IMODE(os.stat(tmp_path / f).st_mode) == 0o600
        port = srv.http.port
        auth = {"Authorization": f"Bearer {tok}"}
        assert (await http(port, "GET", "/state"))[0] == 401
        assert (await http(port, "GET", "/state", headers={"Authorization": "Bearer wrong"}))[0] == 401
        assert (await http(port, "GET", "/state", headers={**auth, "Origin": "http://127.0.0.1:9119"}))[0] == 403
        assert (await http(port, "GET", "/state", headers=auth, host="attacker.example"))[0] == 421
        st, _, body = await http(port, "GET", "/state", headers=auth)
        assert st == 200 and json.loads(body)["mode"] == "off_air"
        st, _, body = await http(port, "POST", "/mode", headers=auth, body={"mode": "autonomous"})
        assert st == 409
        st, _, body = await http(port, "POST", "/mode", headers=auth, body={"mode": "approval"})
        assert st == 200 and json.loads(body)["state"]["mode"] == "approval"
        c.ingest(ChatMsg("twitch", "chan", "a", "u1", "viewer_1", "how are you doing tonight?", T, first_msg=True))
        turn = c.tick()
        c.deliver_reply(turn.turn_id, "Doing great. Thanks for asking.")
        st, _, body = await http(port, "GET", "/pending", headers=auth)
        pend = json.loads(body)["pending"]
        assert st == 200 and len(pend) == 2
        st, _, _ = await http(port, "POST", "/approve", headers=auth, body={"line_id": pend[0]["line_id"], "epoch": 99})
        assert st == 409
        st, _, _ = await http(
            port, "POST", "/approve", headers=auth, body={"line_id": pend[0]["line_id"], "epoch": pend[0]["epoch"]}
        )
        assert st == 200
        st, _, _ = await http(
            port, "POST", "/reject", headers=auth, body={"line_id": pend[1]["line_id"], "epoch": pend[1]["epoch"]}
        )
        assert st == 200
        for path, payload, key, want in (
            ("/privacy", {"on": True}, "privacy", True),
            ("/privacy", {"on": False}, "privacy", False),
            ("/mute", {"on": True}, "muted", True),
        ):
            st, _, body = await http(port, "POST", path, headers=auth, body=payload)
            assert st == 200 and json.loads(body)["state"][key] is want
        # strict booleans: a malformed body never lifts (or drops) the cover, and never unmutes
        st, _, body = await http(port, "POST", "/privacy", headers=auth, body={"on": True})
        assert st == 200 and c.state.privacy is True
        for path in ("/privacy", "/mute"):
            for bad in ({}, {"on": None}, {"on": "cover"}, {"on": "false"}, {"on": 0}, {"enabled": True}, [True], "on"):
                st, _, body = await http(port, "POST", path, headers=auth, body=bad)
                assert st == 400, (path, bad, st)
            st, _, _ = await http(port, "POST", path, headers=auth, body=None)
            assert st == 400, (path, "empty body")
        assert c.state.privacy is True and c.state.muted is True
        st, _, body = await http(port, "POST", "/privacy", headers=auth, body={"on": False})
        assert st == 200 and c.state.privacy is False
        # the desk can't skip approval either: paused -> autonomous only after approval this show
        st, _, _ = await http(port, "POST", "/mode", headers=auth, body={"mode": "off_air"})
        assert st == 200
        for m in ("paused", "autonomous"):
            assert (await http(port, "POST", "/mode", headers=auth, body={"mode": m}))[0] == 409
        assert (await http(port, "POST", "/mode", headers=auth, body={"mode": 3}))[0] == 400
        assert c.state.mode == "off_air"
        await http(port, "POST", "/mode", headers=auth, body={"mode": "approval"})
        st, _, body = await http(port, "POST", "/stop", headers=auth, body={})
        assert st == 200 and json.loads(body)["state"]["mode"] == "paused"
        st, _, body = await http(port, "GET", "/receipts?limit=5", headers=auth)
        assert st == 200 and len(json.loads(body)["receipts"]) == 5
        assert "guard_counts" not in json.loads(body)
        assert (await http(port, "GET", "/mode", headers=auth))[0] == 405
        assert (await http(port, "GET", "/nope", headers=auth))[0] == 404
        bad = await http(port, "POST", "/mode", headers={**auth, "Content-Type": "application/json"})
        assert bad[0] == 400  # empty mode
        await srv.stop()
        assert not (tmp_path / "control.json").exists()

    asyncio.run(go())
