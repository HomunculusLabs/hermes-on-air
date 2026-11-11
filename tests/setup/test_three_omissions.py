"""Run 1651 (review 1650's three omissions). Owned inert fixtures only: no live show, no unrelated PID/service.

1. ``/onair-setup stop|cancel`` uses the same ``cancel_all`` result as the tool and the dashboard: never
   'Nothing is running.' beside an unconfirmed part, and 'stopped' only on ok:true.
2. A missing saved target is not a stopped lifetime: off_air keeps a recorded runtime truthful and sends nothing.
3. A DIFFERENT (or unknown) lifetime at the recorded pid receives NO group signal. This is a MODELED pid-reuse
   negative (an owned process recorded with other start ticks), not actual kernel pid reuse.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys

from onair import stage_runtime as sr

from test_quick_setup import FakeCtx, owner  # noqa: F401
from test_stage_runtime import _made_stage


def _unconfirmed(text: str) -> None:
    assert "Nothing is running" not in text and "nothing is running" not in text, text
    assert "preview is stopped" not in text and "stop again" in text, text
    assert "NOT confirmed stopped" in text or "NOT finished cancelling" in text, text


def test_slash_stop_and_cancel_use_cancel_all(owner, tmp_path, monkeypatch):
    eng, home, ctx, call, command = _made_stage(owner, tmp_path, monkeypatch)
    real_job = eng.job
    draining = {"ok": False, "running": False, "draining": True, "quiescent": False, "pending": 0,
                "error": "a model request is still finishing; the stop is not confirmed complete yet."}
    monkeypatch.setattr(eng, "stop_preview", lambda: dict(draining))
    for verb in ("stop", "cancel"):  # draining preview, no active job
        _unconfirmed(command(verb))
    monkeypatch.setattr(eng, "stop_preview", lambda: {"ok": False, "error": "the preview's owner did not answer."})
    _unconfirmed(command("stop"))  # unanswered routed owner, running:false, no draining flag
    monkeypatch.setattr(eng, "stop_preview", lambda: {"ok": True, "running": False, "quiescent": True})
    monkeypatch.setattr(eng, "job", lambda: {"id": "j-active", "state": "running", "steps": []})
    text = command("stop")  # an accepted cancel while the job still runs is NOT a completed stop
    _unconfirmed(text)
    assert "NOT finished cancelling" in text, text
    monkeypatch.setattr(eng, "job", real_job)
    ok = command("stop")  # ordinary control: job terminal/none AND preview quiescent
    assert ok.endswith("The private preview is stopped.") and "NOT confirmed" not in ok, ok


def test_missing_saved_target_is_not_a_stopped_lifetime(owner, tmp_path, monkeypatch):
    eng, home, ctx, call, command = _made_stage(owner, tmp_path, monkeypatch)
    monkeypatch.setattr(sr, "STOP_WAIT_S", 3.0)
    posts = []
    monkeypatch.setattr(sr, "desk_call", lambda h, info, method, path, body=None: (
        posts.append((method, path)), (200, {"mode": "off_air"}))[1])
    inert = subprocess.Popen([sys.executable, "-B", "-c", "import time; time.sleep(30)"], start_new_session=True)
    try:
        sr._CHILDREN[inert.pid] = inert
        eng._write(sr.RUNTIME_FILE, {"home": str(home), "pid": inert.pid, "ticks": sr._ticks(inert.pid),
                                     "gen": "inert-1", "endpoints": []})
        saved = eng._read("saved.json")
        for broken in ({}, dict(saved, home=str(tmp_path / "nowhere"))):  # no selection; marker folder missing
            eng._write("saved.json", broken)
            off = call("onair_stage_safety", {"action": "off_air"})
            assert off["ok"] is False and off["state"] == "target_missing" and not off.get("stopped"), off
            assert off["running"] is True and "mode" not in off, off
            assert command("stage off").startswith("NOT confirmed off air"), command("stage off")
            assert posts == [] and inert.poll() is None
            assert eng._read(sr.RUNTIME_FILE)["gen"] == "inert-1"
        done = call("onair_stage_safety", {"action": "stop"})  # stop acts on the owned RECORD, not a default
        assert done["ok"] is True and inert.poll() is not None, done
        gone = call("onair_stage_safety", {"action": "off_air"})  # genuinely stopped control: a no-op, no mode
        assert gone["ok"] is True and gone["stopped"] is True and "mode" not in gone, gone
    finally:
        if inert.poll() is None:
            inert.kill()
        inert.wait(5)


def test_stop_vetoes_signals_to_a_different_lifetime_at_the_recorded_pid(owner, tmp_path, monkeypatch):
    eng, home, ctx, call, command = _made_stage(owner, tmp_path, monkeypatch)
    monkeypatch.setattr(sr, "STOP_WAIT_S", 3.0)
    later = subprocess.Popen([sys.executable, "-B", "-c", "import time; time.sleep(30)"], start_new_session=True)
    signals = []
    real_killpg = os.killpg

    def captured(pid, sig):  # signal seam: recorded; delivered only to the owned fixture
        signals.append((pid, sig))
        if pid == later.pid:
            real_killpg(pid, sig)

    monkeypatch.setattr(sr.os, "killpg", captured)
    try:
        ticks = sr._ticks(later.pid)
        base = {"home": str(home), "pid": later.pid, "gen": "stale-1", "endpoints": []}
        for stale in (str(int(ticks) - 1), None):  # a detectably different, then an unknown recorded lifetime
            eng._write(sr.RUNTIME_FILE, dict(base, ticks=stale))
            for _ in range(2):
                out = call("onair_stage_safety", {"action": "stop"})
                assert out["ok"] is False and out["state"] == "foreign_lifetime" and out["signalled"] is False, out
                assert command("stage stop").startswith("The stage is NOT confirmed stopped")
                assert signals == [] and later.poll() is None
            rec = eng._read(sr.RUNTIME_FILE)
            assert rec["gen"] == "stale-1" and rec["stop_unconfirmed"] is True, rec
        sr._CHILDREN[later.pid] = later  # same-lifetime control: the recorded ticks match -> one group signal
        eng._write(sr.RUNTIME_FILE, dict(base, ticks=ticks, gen="same-1"))
        out = call("onair_stage_safety", {"action": "stop"})
        assert out["ok"] is True and signals == [(later.pid, signal.SIGTERM)] and later.poll() is not None, out
        assert not eng._path(sr.RUNTIME_FILE).exists()
    finally:
        if later.poll() is None:
            later.kill()
        later.wait(5)
