"""Unknown CURRENT identity with NO descendant is not absence in ANY consuming path (review 1655 correction).

MODELED, owned fixtures only: an owned inert leader (own session, no descendant). ONLY its /proc/<pid>/stat
observation is made unreadable or malformed by a test Path wrapper (synthetic; not hidepid/kernel pid reuse).
On a424 _leftover called this quiescent, so status/off_air said 'gone' and start() would clear the record.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from onair import stage_runtime as sr

from test_quick_setup import FakeCtx, owner  # noqa: F401
from test_stage_runtime import _made_stage, _prepare
from test_stage_truth import _code, _stop_at_target

pytestmark = pytest.mark.skipif(sr.hermes_command() is None, reason="no hermes CLI beside this interpreter")


@pytest.mark.parametrize("observation", ["unreadable", "malformed"])
def test_unknown_identity_without_descendant_blocks_replacement_and_is_not_gone(owner, tmp_path, monkeypatch,
                                                                                observation):
    eng, home, ctx, call, command = _made_stage(owner, tmp_path, monkeypatch)
    _prepare(home)
    monkeypatch.setattr(sr, "STOP_WAIT_S", 1.0)
    posts, signals = [], []
    monkeypatch.setattr(sr, "desk_call", lambda h, info, method, path, body=None: (
        posts.append((method, path)), (200, {"mode": "off_air"}))[1])
    leader = subprocess.Popen([sys.executable, "-B", "-c", "import sys; sys.stdin.read()"], start_new_session=True,
                              stdin=subprocess.PIPE)
    real_killpg = os.killpg
    deliver = {"on": False}

    def captured_killpg(pid, sig):
        signals.append((pid, sig))
        if deliver["on"] and pid == leader.pid:
            real_killpg(pid, sig)

    try:
        ticks = sr._ticks(leader.pid)
        assert ticks is not None and sr._session_members(leader.pid) == [leader.pid]
        eng._write(sr.RUNTIME_FILE, {"home": str(home), "pid": leader.pid, "ticks": ticks, "gen": "own-1",
                                     "endpoints": []})
        shown = _code(call("onair_stage_plan"))  # before identity becomes unknown
        target = f"/proc/{leader.pid}/stat"

        class Observed(type(Path())):
            def read_text(self, *a, **k):
                if str(self) == target:
                    if observation == "unreadable":
                        raise PermissionError(13, "Permission denied (modeled)", target)
                    return f"{leader.pid} (python3) garbage"
                return super().read_text(*a, **k)

        calls = _stop_at_target(monkeypatch)
        with monkeypatch.context() as m:
            m.setattr(sr, "Path", Observed)
            m.setattr(sr.os, "killpg", captured_killpg)
            out = call("onair_stage_safety", {"action": "stop"})
            assert out["state"] == "identity_unknown" and signals == [] and posts == [], (out, signals, posts)
            rec = eng._read(sr.RUNTIME_FILE)
            assert rec["stop_unconfirmed"] is True and rec["gen"] == "own-1", rec
            left = sr._leftover(rec)
            assert left["quiescent"] is False, ("unknown holder counted as quiescent: start() would replace it", left)
            st = call("onair_stage_status")
            assert st.get("state") == "identity_unknown" and "is gone" not in str(st.get("note")) and "are gone" not in str(st.get("note")), st
            assert left.get("leader_identity") == "unknown", left
            text = command("stage status")
            assert "is gone" not in text and "are gone" not in text, text
            off = call("onair_stage_safety", {"action": "off_air"})
            assert off["ok"] is False and off.get("stopped") is not True and posts == [], off
            assert "is gone" not in str(off), off
            blocked = call("onair_stage_plan")
            assert not blocked["ok"] and "approve_with" not in blocked, blocked
            reply = command(f"stage approve {shown}")
            assert ctx.asked == []  # refuse before asking the owner to approve an impossible start
            assert "NOT confirmed stopped" in reply and calls == [], (reply, calls)  # 0 CLI probes/spawns
            assert eng._read(sr.RUNTIME_FILE) == rec, "the exact record must be retained"
            assert signals == [] and posts == []
            assert "no off-air operation was attempted" in out["error"], out  # diagnostic follows actual went_off

        # release the synthetic fault: identity known (same lifetime) -> real owned stop clears the generation
        deliver["on"] = True
        monkeypatch.setattr(sr.os, "killpg", captured_killpg)
        out = call("onair_stage_safety", {"action": "stop"})
        assert out["ok"] is True and signals == [(leader.pid, signal.SIGTERM)], (out, signals)
        assert not eng._path(sr.RUNTIME_FILE).exists()
        shown = _code(call("onair_stage_plan"))
        reply = command(f"stage approve {shown}")
        assert "fixture-stop-at-target" in reply and len(calls) == 1, (reply, calls)  # new approved start proceeds
    finally:
        try:
            os.kill(leader.pid, signal.SIGKILL)
        except OSError:
            pass
        leader.stdin.close()
        leader.wait(5)


def test_sticky_stop_unconfirmed_blocks_start_until_a_real_stop_clears_it(owner, tmp_path, monkeypatch):
    eng, home, ctx, call, command = _made_stage(owner, tmp_path, monkeypatch)
    _prepare(home)
    gone = subprocess.Popen([sys.executable, "-B", "-c", "pass"], start_new_session=True)
    gone.wait(5)
    assert sr._identity(gone.pid)[0] == "absent"
    shown = _code(call("onair_stage_plan"))
    eng._write(sr.RUNTIME_FILE, {"home": str(home), "pid": gone.pid, "ticks": "1", "gen": "own-2",
                                 "endpoints": [], "stop_unconfirmed": True})
    calls = _stop_at_target(monkeypatch)
    blocked = call("onair_stage_plan")
    assert not blocked["ok"] and "approve_with" not in blocked, blocked
    reply = command(f"stage approve {shown}")
    assert "stop is still unconfirmed" in reply and calls == [], reply
    assert eng._read(sr.RUNTIME_FILE)["gen"] == "own-2"
    out = call("onair_stage_safety", {"action": "stop"})
    assert out["ok"] is True and not eng._path(sr.RUNTIME_FILE).exists(), out
    reply = command(f"stage approve {_code(call('onair_stage_plan'))}")
    assert "fixture-stop-at-target" in reply and len(calls) == 1, reply
