"""Unknown CURRENT identity is not absence (parent correction after review 1653).

MODELED negative, owned fixtures only: an owned inert leader (own session) with an owned inert descendant in that
session. ONLY the leader's /proc/<pid>/stat observation is made unreadable (PermissionError) or malformed by a
test Path wrapper -- synthetic unreadability and modeled pid ownership, NOT actual hidepid or kernel pid reuse.
The 069f source read that as 'no current holder', found the descendant in the session and reached killpg; the
fix must deliver ZERO signals and ZERO off-air POSTs, keep the record stop_unconfirmed and block a replacement.
Controls: an ordinary same-lifetime stop, and a positively ABSENT leader whose surviving descendant is cleaned up.
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
from test_stage_runtime import _made_stage

_LEADER = ("import subprocess, sys\n"
           "d = subprocess.Popen([sys.executable, '-B', '-c', 'import time; time.sleep(30)'])\n"
           "print(d.pid, flush=True)\n"
           "sys.stdin.read()\n")


def _owned_session():
    leader = subprocess.Popen([sys.executable, "-B", "-c", _LEADER], start_new_session=True,
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    child = int(leader.stdout.readline())
    return leader, child


def _reap(leader, child):
    for pid in (leader.pid, child):
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    leader.stdin.close()
    leader.wait(5)


@pytest.mark.parametrize("observation", ["unreadable", "malformed"])
def test_unknown_current_identity_vetoes_group_signal_and_control(owner, tmp_path, monkeypatch, observation):
    eng, home, ctx, call, command = _made_stage(owner, tmp_path, monkeypatch)
    monkeypatch.setattr(sr, "STOP_WAIT_S", 1.0)
    posts, signals, probes = [], [], []
    monkeypatch.setattr(sr, "desk_call", lambda h, info, method, path, body=None: (
        posts.append((method, path)), (200, {"mode": "off_air"}))[1])
    leader, child = _owned_session()
    owned = {leader.pid}
    real_killpg, real_kill = os.killpg, os.kill
    deliver = {"on": False}

    def captured_killpg(pid, sig):  # the signal seam: recorded; delivered only in the control phase, owned only
        signals.append((pid, sig))
        if deliver["on"] and pid in owned:
            real_killpg(pid, sig)

    def captured_kill(pid, sig):
        (probes if sig == 0 else signals).append((pid, sig))
        return real_kill(pid, sig)

    try:
        ticks = sr._ticks(leader.pid)
        assert ticks is not None and sr._session_members(leader.pid) == sorted([leader.pid, child])
        eng._write(sr.RUNTIME_FILE, {"home": str(home), "pid": leader.pid, "ticks": ticks, "gen": "own-1",
                                     "endpoints": []})
        target = f"/proc/{leader.pid}/stat"

        class Observed(type(Path())):  # ONLY the owned leader's identity observation fails
            def read_text(self, *a, **k):
                if str(self) == target:
                    if observation == "unreadable":
                        raise PermissionError(13, "Permission denied (modeled)", target)
                    return f"{leader.pid} (python3) garbage"
                return super().read_text(*a, **k)

        with monkeypatch.context() as m:
            m.setattr(sr, "Path", Observed)
            m.setattr(sr.os, "killpg", captured_killpg)
            m.setattr(sr.os, "kill", captured_kill)
            for _ in range(2):
                out = call("onair_stage_safety", {"action": "stop"})
                assert signals == [], ("a group signal reached an unidentified holder", signals, out)
                assert posts == [], posts
                assert out["ok"] is False and out["state"] == "identity_unknown" and out["signalled"] is False, out
                text = command("stage stop")
                assert signals == [] and posts == [], (signals, text)
                assert "NOT confirmed" in text, text
            rec = eng._read(sr.RUNTIME_FILE)
            assert rec["gen"] == "own-1" and rec["stop_unconfirmed"] is True, rec
            assert leader.poll() is None and sr._identity(child)[0] == "live"
            assert sr._identity(leader.pid) == ("unknown", None)
            assert sr._leftover(rec)["quiescent"] is False  # what start() consults: a replacement is refused
        # the record still blocks a replacement while the holder is unidentified
        assert eng._read(sr.RUNTIME_FILE)["stop_unconfirmed"] is True

        # control 1: the identity is readable again and is the SAME lifetime -> exactly one group SIGTERM
        deliver["on"] = True
        monkeypatch.setattr(sr.os, "killpg", captured_killpg)
        out = call("onair_stage_safety", {"action": "stop"})
        assert signals == [(leader.pid, signal.SIGTERM)], (signals, out)
        assert out["ok"] is True, out
        assert not eng._path(sr.RUNTIME_FILE).exists()
    finally:
        _reap(leader, child)


def test_positively_absent_leader_descendant_is_still_cleaned(owner, tmp_path, monkeypatch):
    eng, home, ctx, call, command = _made_stage(owner, tmp_path, monkeypatch)
    monkeypatch.setattr(sr, "STOP_WAIT_S", 3.0)
    signals = []
    real_killpg = os.killpg
    leader, child = _owned_session()

    def captured(pid, sig):
        signals.append((pid, sig))
        if pid == leader.pid:
            real_killpg(pid, sig)

    try:
        ticks = sr._ticks(leader.pid)
        eng._write(sr.RUNTIME_FILE, {"home": str(home), "pid": leader.pid, "ticks": ticks, "gen": "own-2",
                                     "endpoints": []})
        leader.stdin.close()
        leader.wait(5)  # the leader is reaped: affirmatively absent; its descendant survives in the old session
        assert not os.path.exists(f"/proc/{leader.pid}")
        with pytest.raises(ProcessLookupError):
            os.kill(leader.pid, 0)
        if hasattr(sr, "_identity"):
            assert sr._identity(leader.pid) == ("absent", None)
        assert sr._session_members(leader.pid) == [child]
        monkeypatch.setattr(sr.os, "killpg", captured)
        out = call("onair_stage_safety", {"action": "stop"})
        assert signals == [(leader.pid, signal.SIGTERM)], (signals, out)
        assert out["ok"] is True, out
        assert sr._session_members(leader.pid) == []
    finally:
        try:
            os.kill(child, signal.SIGKILL)
        except OSError:
            pass
        if leader.poll() is None:
            leader.kill()
            leader.wait(5)
