"""Stage approval stability, install isolation and stop truth, through the REGISTERED setup entry.

1. Status is read-only: it never issues or replaces the approval code; only an explicit plan does; the start
   compares and consumes exactly the shown code under the setup lock, after the owner's card returned.
3. Before ANY Hermes CLI process the start checks, by file metadata only, the startup secret planes a Hermes
   process loads besides its own home (installation project ``.env``, administrator-managed scope) and refuses
   with the exact reason. Negative edges use SYNTHETIC canary roots in tmp (the real installation file is never
   touched or read); the positive case is the real installation (no project .env, no managed scope here).
4. A stop is complete only when no process of the stage's own session remains and every endpoint it announced is
   closed. Otherwise the record and its endpoints are kept, status says so and no new start replaces it.

DISCLOSED FIXTURES: ``FakeCtx`` (owner CLI surface + approval card); the user-completed prerequisites of
test_stage_runtime (drop-in plugin copy, loopback model entry); for item 4 an OWNED INERT fixture "stage": a
Python process started in its own session (like the real start) whose child keeps the announced port open
and ignores SIGTERM until the test releases it; its runtime record is written in the real format for that real
process (it is not a success descriptor). Where a test needs to show that a start got PAST a gate without
running Hermes, ``effective_home`` (the first CLI step) is replaced by a recorder that refuses: a unit seam,
not native-runner proof (the native runner is test_stage_runtime's real ``hermes gateway run`` lifecycle).
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from onair import setup_engine as se
from onair import stage_runtime as sr

from test_quick_setup import FakeCtx, owner  # noqa: F401
from test_stage_runtime import _made_stage, _prepare

pytestmark = pytest.mark.skipif(sr.hermes_command() is None, reason="no hermes CLI beside this interpreter")


def _code(plan: dict) -> str:
    return plan["approve_with"]["chat"].split()[-1]


def _stop_at_target(monkeypatch) -> list:
    """Unit seam: record each time a start reaches its FIRST CLI step, and refuse there (nothing runs)."""
    calls = []

    def fake(argv0, env, home):
        calls.append((list(argv0), env.get("HERMES_HOME"), env.get("HOME")))
        return False, "fixture-stop-at-target"
    monkeypatch.setattr(sr, "effective_home", fake)
    return calls


def _no_cli(monkeypatch) -> None:
    def boom(*a, **k):
        raise AssertionError(f"a CLI process was started: {a!r}")
    monkeypatch.setattr(sr.subprocess, "run", boom)
    monkeypatch.setattr(sr.subprocess, "Popen", boom)


def test_status_never_mints_and_the_shown_code_starts_exactly_once(owner, tmp_path, monkeypatch):
    eng, home, ctx, call, command = _made_stage(owner, tmp_path, monkeypatch)
    _prepare(home)
    plan = call("onair_stage_plan")
    assert plan["ok"] is True, plan
    shown = _code(plan)
    stored = eng._path(sr.PLAN_FILE).read_bytes()
    for _ in range(3):  # repeated read-only status (the 1645 reproduction rotated the code here)
        st = call("onair_stage_status")
        assert st["startable"] is True and "approve_with" not in st, st
        assert command("stage status").startswith("Real stage: stopped"), command("stage status")
    assert eng._path(sr.PLAN_FILE).read_bytes() == stored  # byte-identical: status wrote nothing
    calls = _stop_at_target(monkeypatch)
    reply = command(f"stage approve {shown}")
    assert len(ctx.asked) == 1 and ctx.asked[0][0].startswith("Start the real On Air stage?"), ctx.asked
    assert "fixture-stop-at-target" in reply and len(calls) == 1, (reply, calls)  # exactly one start attempt
    assert calls[0][1] == str(home) and calls[0][2] == str(home / "plugin-data" / "on-air" / "home"), calls
    assert command(f"stage approve {shown}").startswith("No current stage plan")  # used once
    assert len(calls) == 1 and len(ctx.asked) == 1
    # a newer explicit plan invalidates the older code (no card, nothing started)
    older, newer = _code(call("onair_stage_plan")), _code(call("onair_stage_plan"))
    assert older != newer
    assert command(f"stage approve {older}").startswith("No current stage plan") and len(ctx.asked) == 1
    # forged / missing / a tool cannot approve
    assert command("stage approve").startswith("No current stage plan")
    assert command("stage approve zzzzzz").startswith("No current stage plan")
    digest = json.loads(eng._path(sr.PLAN_FILE).read_text())["digest"]
    assert sr.start(eng, digest, "approved", newer)["refused"] is True
    assert sr.start(eng, digest, se.owner_consent(digest, "dashboard"), "000000")["refused"] is True
    assert len(calls) == 1
    # the stored code is consumed by start() itself, not by the slash text check alone
    assert sr.start(eng, digest, se.owner_consent(digest, "dashboard"), newer).get("step") == "target"
    assert sr.start(eng, digest, se.owner_consent(digest, "dashboard"), newer)["refused"] is True
    assert len(calls) == 2
    assert not eng._path(sr.RUNTIME_FILE).exists()


def test_a_plan_replaced_while_the_card_is_open_starts_nothing(owner, tmp_path, monkeypatch):
    eng, home, ctx, call, command = _made_stage(owner, tmp_path, monkeypatch)
    _prepare(home)
    shown = _code(call("onair_stage_plan"))
    calls = _stop_at_target(monkeypatch)
    replaced = []

    def slow_card(question, choices):  # while the owner's card is open, someone plans again
        ctx.asked.append((question, choices))
        replaced.append(_code(call("onair_stage_plan")))
        return "start"
    ctx.ask_choice = slow_card
    reply = command(f"stage approve {shown}")
    assert "no longer current" in reply and calls == [], reply  # compared under the lock AFTER the card
    assert command(f"stage approve {shown}").startswith("No current stage plan")
    assert json.loads(eng._path(sr.PLAN_FILE).read_text())["code"] == replaced[0]  # the newer code survives
    ctx.ask_choice = lambda q, c: (ctx.asked.append((q, c)), "start")[1]
    assert "fixture-stop-at-target" in command(f"stage approve {replaced[0]}") and len(calls) == 1


def test_isolation_is_checked_before_any_cli_process(owner, tmp_path, monkeypatch):
    eng, home, ctx, call, command = _made_stage(owner, tmp_path, monkeypatch)
    _prepare(home)
    assert sr.install_isolation(sr.hermes_command()) == [], "the REAL installation here: no project .env/managed"
    assert call("onair_stage_status")["startable"] is True
    plan = call("onair_stage_plan")
    digest, shown = plan["digest"], _code(plan)
    # SYNTHETIC canary root (the real installation file is never touched): a project .env appears
    root = tmp_path / "synthetic-install"
    root.mkdir()
    (root / ".env").write_text("CANARY_NOT_A_SECRET=1\n")
    monkeypatch.setattr(sr, "_install_roots", lambda: ({root}, []))
    st = call("onair_stage_status")
    assert st["startable"] is False and [m["key"] for m in st["missing"]] == ["isolation"], st
    assert str(root / ".env") in st["missing"][0]["detail"] and "did not open it" in st["missing"][0]["detail"]
    refused = call("onair_stage_plan")
    assert refused["ok"] is False and "approve_with" not in refused, refused
    _no_cli(monkeypatch)
    out = sr.start(eng, digest, se.owner_consent(digest, "dashboard"), shown)
    assert out["ok"] is False and "not prepared" in out["error"] and "isolation" in out["error"], out
    # the start re-checks under its lock too (canary appears after readiness)
    monkeypatch.setattr(sr, "_install_roots", lambda: (set(), []))
    # Isolate the second metadata check; no CLI process may run in this seam.
    monkeypatch.setattr(sr, "_stage_preflight", lambda *_: {"ok": True, "missing": []})
    plan = call("onair_stage_plan")
    flips = iter([[], [{"key": "isolation", "ok": False, "agent_can_do": False, "detail": "canary appeared",
                        "next_action": ""}]])
    monkeypatch.setattr(sr, "install_isolation", lambda argv0: next(flips))
    out = sr.start(eng, plan["digest"], se.owner_consent(plan["digest"], "dashboard"), _code(plan))
    assert out["step"] == "isolation" and "canary appeared" in out["error"], out
    monkeypatch.undo()
    # administrator-managed scope (synthetic dir) and a CLI from another installation are refused too
    managed = tmp_path / "synthetic-etc-hermes"
    managed.mkdir()
    monkeypatch.setattr(sr, "_MANAGED_DIR", managed)
    assert "administrator-managed" in sr.install_isolation(sr.hermes_command())[0]["detail"]
    monkeypatch.setattr(sr, "_MANAGED_DIR", tmp_path / "absent")
    assert "not the installation this plugin runs in" in sr.install_isolation(["/opt/other/hermes"])[0]["detail"]
    assert sr.install_isolation(sr.hermes_command()) == []
    # the child environment: allowlist + stage-owned HOME, never the owner's HOME or keys
    monkeypatch.setenv("OPENROUTER_API_KEY", "owner-key-not-inherited")
    env = sr.child_env(home)
    assert env["HOME"] == str(home / "plugin-data" / "on-air" / "home") and env["HOME"] != os.environ.get("HOME")
    assert "OPENROUTER_API_KEY" not in env and env["HERMES_HOME"] == str(home)


def test_unattributable_package_origins_refuse_before_any_cli_process(owner, tmp_path, monkeypatch):
    """Carry C: both hermes_cli and gateway must resolve to files of this installation (one shared root is
    fine); a missing/namespace/unlocatable origin is UNVERIFIABLE and refuses before any CLI process."""
    eng, home, ctx, call, command = _made_stage(owner, tmp_path, monkeypatch)
    _prepare(home)
    roots, unknown = sr._install_roots()  # the REAL installation: both attributable (metadata only)
    assert unknown == [] and len(roots) >= 1, (roots, unknown)
    plan = call("onair_stage_plan")
    digest, shown = plan["digest"], _code(plan)
    real = sr._package_origin
    for missing in (["gateway"], ["hermes_cli"], ["hermes_cli", "gateway"]):
        monkeypatch.setattr(sr, "_package_origin", lambda name, m=missing: None if name in m else real(name))
        problems = sr.install_isolation(sr.hermes_command())
        assert [p["key"] for p in problems] == ["isolation"], problems
        assert "could not locate" in problems[0]["detail"] and all(n in problems[0]["detail"] for n in missing)
        st = call("onair_stage_status")
        assert st["startable"] is False and [m["key"] for m in st["missing"]] == ["isolation"], st
    _no_cli(monkeypatch)
    out = sr.start(eng, digest, se.owner_consent(digest, "dashboard"), shown)
    assert out["ok"] is False and "isolation" in out["error"], out
    # a namespace/unlocatable spec is unattributable too (not a real file)
    import importlib.util
    class _Spec:
        origin = "namespace"
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: _Spec())
    assert sr._package_origin("gateway") is None
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: (_ for _ in ()).throw(ValueError("boom")))
    assert sr._package_origin("gateway") is None
    monkeypatch.undo()
    # both packages may share ONE canonical root (no demand for two directories): a synthetic shared root
    shared = tmp_path / "synthetic-one-root"
    for pkg in ("hermes_cli", "gateway"):
        (shared / pkg).mkdir(parents=True)
        (shared / pkg / "__init__.py").write_text("")
    monkeypatch.setattr(sr, "_package_origin", lambda name: (shared / name / "__init__.py").resolve())
    assert sr._install_roots() == ({shared.resolve()}, [])
    assert sr.install_isolation(sr.hermes_command()) == []
    (shared / ".env").write_text("CANARY_NOT_A_SECRET=1\n")
    assert str(shared.resolve() / ".env") in sr.install_isolation(sr.hermes_command())[0]["detail"]


_FIXTURE_STAGE = r"""
import json, os, signal, socket, sys, time
stage_dir, release = sys.argv[1], sys.argv[2]
s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); s.bind(("127.0.0.1", 0)); s.listen(8)
port = s.getsockname()[1]
if os.fork() == 0:  # the port holder: same session, ignores SIGTERM until released
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    while not os.path.exists(release):
        time.sleep(0.05)
    os._exit(0)
s.close()
os.makedirs(stage_dir, exist_ok=True)
tmp = os.path.join(stage_dir, "control.json.tmp")
with open(tmp, "w") as fh:
    json.dump({"pid": os.getpid(), "url": "http://127.0.0.1:%d" % port,
               "overlay_url": "http://127.0.0.1:%d/overlay" % port, "token_file": "desk.token"}, fh)
os.replace(tmp, os.path.join(stage_dir, "control.json"))
while True:
    time.sleep(1)
"""


def test_stop_stays_unconfirmed_while_the_stage_session_or_its_port_remains(owner, tmp_path, monkeypatch):
    eng, home, ctx, call, command = _made_stage(owner, tmp_path, monkeypatch)
    _prepare(home)
    monkeypatch.setattr(sr, "STOP_WAIT_S", 1.5)
    stage_dir = home / "plugin-data" / "on-air"
    release = tmp_path / "release-port-holder"
    leader = subprocess.Popen([sys.executable, "-B", "-c", _FIXTURE_STAGE, str(stage_dir), str(release)],
                              start_new_session=True, stdin=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not (stage_dir / "control.json").exists():
            time.sleep(0.05)
        info = json.loads((stage_dir / "control.json").read_text())
        port_url = info["url"]
        sr._CHILDREN[leader.pid] = leader
        eng._write(sr.RUNTIME_FILE, {"home": str(home), "pid": leader.pid, "ticks": sr._ticks(leader.pid),
                                     "gen": "fixture-gen-1", "endpoints": [], "argv": ["fixture-stage"],
                                     "started": time.time(), "log": "", "approved_via": "fixture"})
        assert call("onair_stage_status")["state"] == "unanswered"  # running; desk gives no mode: not 'off air'
        shown = _code(call("onair_stage_plan"))  # approval prepared before the stop becomes unconfirmed
        first = call("onair_stage_safety", {"action": "stop"})  # leader exits, the port holder stays
        assert first["ok"] is False and first["running"] is False and first["open"] == [port_url], first
        assert first["leftover"]["session_processes"], first
        rec = eng._read(sr.RUNTIME_FILE)
        assert rec["endpoints"] == [port_url] and rec["stop_unconfirmed"] is True, rec  # persisted, identity-bound
        # carry B: a dead leader with live session members/open endpoints is NOT an observed off-air mode
        off = call("onair_stage_safety", {"action": "off_air"})
        assert off["ok"] is False and off["state"] == "stop_unconfirmed" and "mode" not in off, off
        assert command("stage off").startswith("NOT confirmed off air"), command("stage off")
        os.unlink(stage_dir / "control.json")  # announcements gone too: the SAVED observation still counts
        for _ in range(2):  # repeated registered stop / status / slash stop: never a success from empty info
            again = call("onair_stage_safety", {"action": "stop"})
            assert again["ok"] is False and port_url in again["open"], again
            st = call("onair_stage_status")
            assert st["state"] == "stop_unconfirmed" and st["startable"] is False and st["running"] is False, st
            assert command("stage stop").startswith("The stage is NOT confirmed stopped"), command("stage status")
            assert "NOT confirmed" in command("stage status")
        calls = _stop_at_target(monkeypatch)
        blocked_plan = call("onair_stage_plan")
        assert not blocked_plan["ok"] and "approve_with" not in blocked_plan, blocked_plan
        refused = command("stage approve " + shown)
        assert "previous stage is NOT confirmed stopped" in refused and calls == [], refused
        assert eng._read(sr.RUNTIME_FILE)["gen"] == "fixture-gen-1"  # record kept, not replaced
        release.touch()  # the owned port holder ends
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and sr._session_members(leader.pid):
            time.sleep(0.05)
        done = call("onair_stage_safety", {"action": "stop"})
        assert done["ok"] is True and done["endpoints_checked"] == [port_url], done
        assert not eng._path(sr.RUNTIME_FILE).exists()
        assert call("onair_stage_status")["state"] == "stopped"
        gone = call("onair_stage_safety", {"action": "off_air"})  # positively nothing running: a no-op, no mode
        assert gone["ok"] is True and gone["stopped"] is True and "mode" not in gone, gone
        assert "off air (its desk confirmed" not in command("stage off") and "nothing to take off air" in command("stage off")
        assert "fixture-stop-at-target" in command("stage approve " + _code(call("onair_stage_plan")))
        assert len(calls) == 1  # the gate released: the next approved start proceeds to its first CLI step
    finally:
        release.touch()
        if leader.poll() is None:
            leader.kill()
        leader.wait(5)


def test_stop_compare_and_delete_keeps_a_newer_generation(owner, tmp_path, monkeypatch):
    """Carry B: the stop's off-air POST and its signals are bound to the generation it captured. A replacement
    recorded before the off-air step, or between off-air's own read and its request, receives NO POST and NO
    signal; the record of the newer generation is kept."""
    eng, home, ctx, call, command = _made_stage(owner, tmp_path, monkeypatch)
    sleeper = subprocess.Popen([sys.executable, "-B", "-c", "import time; time.sleep(30)"], start_new_session=True)
    try:
        record = {"home": str(home), "pid": sleeper.pid, "ticks": sr._ticks(sleeper.pid), "gen": "g1", "endpoints": []}
        newer = dict(record, gen="g2")
        posts = []
        monkeypatch.setattr(sr, "desk_call", lambda h, info, method, path, body=None: (
            posts.append((method, path, info.get("url"))), (200, {"mode": "off_air"}))[1])
        for race_at in (1, 2, 3):  # _control call 1: stop's capture; 2: off_air's read; 3: off_air's locked recheck
            eng._write(sr.RUNTIME_FILE, record)
            seen = []

            def control(h, pid, race_at=race_at):
                seen.append(pid)
                if len(seen) == race_at:  # a different generation is recorded during this step
                    eng._write(sr.RUNTIME_FILE, newer)
                return {"pid": pid, "url": "http://127.0.0.1:9"}
            monkeypatch.setattr(sr, "_control", control)
            out = sr.stop(eng)
            assert out["ok"] is False and "changed during the stop" in out["error"], (race_at, out)
            assert posts == [], (race_at, posts)  # NO off-air POST reached the replacement generation
            assert sleeper.poll() is None  # nothing was signalled
            assert eng._read(sr.RUNTIME_FILE)["gen"] == "g2"
        # control: without a race the same fixture DOES send exactly one off-air POST to its own generation
        eng._write(sr.RUNTIME_FILE, record)
        monkeypatch.setattr(sr, "_control", lambda h, pid: {"pid": pid, "url": "http://127.0.0.1:9"})
        went = sr.off_air(eng, expect=record)
        assert went["ok"] is True and went["mode"] == "off_air" and posts == [("POST", "/mode", "http://127.0.0.1:9")]
        assert sr.off_air(eng, expect=newer)["ok"] is False and len(posts) == 1  # a stale expectation sends nothing
    finally:
        sleeper.kill()
        sleeper.wait(5)


_FIXTURE_NEVER_CONNECTS = r"""
import os, signal, sys, time
release = sys.argv[1]
if os.fork() == 0:  # a member of the stage's own session that ignores SIGTERM until released
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    while not os.path.exists(release):
        time.sleep(0.05)
    os._exit(0)
while True:  # the leader never writes control.json: its endpoints are never observed
    time.sleep(1)
"""


def test_unobserved_endpoints_stay_unknown_until_the_whole_session_is_gone(owner, tmp_path, monkeypatch):
    """Carry D: a stage that never announced (control.json missing before the first observation) has UNKNOWN
    endpoints, not 'none'. Its stop is confirmed only by the session-scoped proof (leader exited and a COMPLETE
    scan finds no process of its session); an incomplete scan proves nothing. The ordinary recovery still works:
    unconfirmed stop -> release -> registered stop ok (endpoints_observed False) -> a new approved start."""
    eng, home, ctx, call, command = _made_stage(owner, tmp_path, monkeypatch)
    _prepare(home)
    monkeypatch.setattr(sr, "STOP_WAIT_S", 1.0)
    release = tmp_path / "release-member"
    leader = subprocess.Popen([sys.executable, "-B", "-c", _FIXTURE_NEVER_CONNECTS, str(release)],
                              start_new_session=True, stdin=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and len(sr._session_members(leader.pid)) < 2:
            time.sleep(0.05)
        sr._CHILDREN[leader.pid] = leader
        eng._write(sr.RUNTIME_FILE, {"home": str(home), "pid": leader.pid, "ticks": sr._ticks(leader.pid),
                                     "gen": "fixture-gen-u", "endpoints": [], "argv": ["fixture-stage"],
                                     "started": time.time(), "log": "", "approved_via": "fixture"})
        assert call("onair_stage_status")["state"] == "starting"  # running, never connected: mode unknown
        shown = _code(call("onair_stage_plan"))
        first = call("onair_stage_safety", {"action": "stop"})  # leader exits, the member stays
        assert first["ok"] is False and first["endpoints_observed"] is False, first
        assert first["leftover"]["session_processes"] and first["leftover"]["unobserved_endpoints_proof"] is None
        assert "never announced" not in json.dumps(first)
        st = call("onair_stage_status")
        assert st["state"] == "stop_unconfirmed" and st["startable"] is False, st
        assert call("onair_stage_safety", {"action": "off_air"})["ok"] is False
        calls = _stop_at_target(monkeypatch)
        blocked_plan = call("onair_stage_plan")
        assert not blocked_plan["ok"] and "approve_with" not in blocked_plan, blocked_plan
        assert "previous stage is NOT confirmed stopped" in command("stage approve " + shown)
        assert calls == []
        release.touch()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and sr._session_members(leader.pid):
            time.sleep(0.05)
        real_scan = sr._session_scan
        monkeypatch.setattr(sr, "_session_scan", lambda sid: ([], False))  # an INCOMPLETE scan is not 'empty'
        partial = call("onair_stage_safety", {"action": "stop"})
        assert partial["ok"] is False and "could not be fully checked" in partial["error"], partial
        assert call("onair_stage_status")["state"] == "stop_unconfirmed"
        monkeypatch.setattr(sr, "_session_scan", real_scan)
        done = call("onair_stage_safety", {"action": "stop"})
        assert done["ok"] is True and done["endpoints_observed"] is False and done["endpoints_checked"] == [], done
        assert done["unobserved_endpoints_proof"].startswith("session_gone"), done
        assert "never observed, so none were checked" in done["note"] and "never announced" not in done["note"]
        assert not eng._path(sr.RUNTIME_FILE).exists()
        assert "fixture-stop-at-target" in command("stage approve " + _code(call("onair_stage_plan")))
        assert len(calls) == 1  # recovered: the next approved start proceeds to its first CLI step
    finally:
        release.touch()
        if leader.poll() is None:
            leader.kill()
        leader.wait(5)
