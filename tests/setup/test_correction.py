"""Corrections for the parent screen of 95d35142: owner surfaces, rename refusal, repeat resume and one
owner for the running preview across engine copies. Real engine, real private preview runtime (loopback,
ephemeral ports); the host session context is the disclosed ``FakeCtx`` fixture (its surface string)."""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import textwrap
from pathlib import Path
from urllib.parse import urlparse

import pytest

from onair import setup_agent as sa
from onair import setup_engine as se

from test_quick_setup import SECRET, FakeCtx, owner, wait_done  # noqa: F401


def _listening(url: str) -> bool:
    port = urlparse(url).port
    if not port:
        return False
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _create(eng, tmp_path):
    plan = eng.plan({"channel": "cozy_fox", "target": os.fspath(tmp_path / "stage")})
    eng.apply(plan["digest"], se.owner_consent(plan["digest"], "dashboard"))
    job = wait_done(eng)
    assert job["state"] == "done", job
    return job


@pytest.mark.parametrize("surface,allowed", [("cli", True), ("tui", True), ("desktop", True),
                                             ("discord", False), ("telegram", False), ("acp", False)])
def test_only_local_owner_surfaces_reach_setup(owner, surface, allowed):
    eng = se.SetupEngine(owner)
    handlers = sa.make_handlers(FakeCtx(surface=surface), get_engine=lambda: eng)
    for name in ("onair_setup_status", "onair_setup_plan"):
        out = json.loads(handlers[name]({}))
        assert out.get("refused", False) is (not allowed), (surface, name, out)
    if not allowed:
        assert "messaging chat cannot prove you are the owner" in out["error"]
        assert not eng.dir.exists() or not (eng.dir / "plan.json").exists()


def test_resume_refuses_renaming_instead_of_dropping_it(owner, tmp_path, monkeypatch):
    monkeypatch.setattr(se, "_ENGINES", {})
    eng = se.SetupEngine(owner)
    job = _create(eng, tmp_path)
    eng.stop_preview()
    assert job["next_action"].startswith("Private preview only. The real show is not set up yet:"), job
    out = eng.plan({"agent_name": "Someone Else"})
    assert out["ok"] is False and "renaming is not supported" in out["errors"]["agent_name"], out
    assert eng.plan({"use_model": False})["kind"] == "resume"


def test_published_running_job_already_has_live_worker(owner, tmp_path, monkeypatch):
    import threading
    eng = se.SetupEngine(owner)
    observer = se.SetupEngine(owner)
    plan = eng.plan({"channel": "cozy_fox", "target": os.fspath(tmp_path / "stage")})
    written, release = threading.Event(), threading.Event()
    original = eng._write
    def hold_initial(name, value):
        original(name, value)
        if name == "job.json" and value.get("state") == "running" and not written.is_set():
            written.set()
            assert release.wait(10)
    monkeypatch.setattr(eng, "_write", hold_initial)
    result = []
    thread = threading.Thread(target=lambda: result.append(eng.apply(
        plan["digest"], se.owner_consent(plan["digest"], "dashboard"))))
    thread.start()
    try:
        assert written.wait(10)
        assert observer.job()["state"] == "running"
    finally:
        release.set()
        thread.join(10)
        if eng._thread:
            eng._thread.join(30)
        eng.stop_preview()
    assert result and result[0]["ok"]
    assert observer.job()["state"] == "done"


def test_stop_resume_stop_resume_really_restarts(owner, tmp_path, monkeypatch):
    monkeypatch.setattr(se, "_ENGINES", {})
    eng = se.SetupEngine(owner)
    _create(eng, tmp_path)
    try:
        for round_ in range(2):
            eng.stop_preview()
            facts = eng.preview_facts()
            assert facts["running"] is False
            plan = eng.plan({"use_model": False})
            assert plan["kind"] == "resume", plan
            consent = se.owner_consent(plan["digest"], "dashboard")
            first = eng.apply(plan["digest"], consent)
            assert first["ok"] and not first.get("duplicate"), (round_, first)
            again = eng.apply(plan["digest"], consent)  # repeated click while it runs: same job
            assert again["ok"] and (again.get("duplicate") or again["job"]["id"] == first["job"]["id"]), again
            job = wait_done(eng)
            assert job["state"] == "done", job
            facts = eng.preview_facts()
            assert facts["running"] is True and _listening(facts["overlay_url"]), (round_, facts)
            dup = eng.apply(plan["digest"], consent)  # unchanged plan while the preview runs: no second start
            assert dup.get("duplicate") is True, dup
    finally:
        eng.stop_preview()


def test_preview_has_one_owner_across_engine_copies(owner, tmp_path, monkeypatch):
    """Agent-start -> other copy stops it, and the other way round. A second SetupEngine object on the same
    home stands for the dashboard's separately imported module copy (no shared in-memory state)."""
    monkeypatch.setattr(se, "_ENGINES", {})
    agent, dashboard = se.SetupEngine(owner), se.SetupEngine(owner)
    _create(agent, tmp_path)
    try:
        url = agent.preview_facts()["overlay_url"]
        assert dashboard.preview_facts()["running"] is True  # the other copy sees the real owner
        out = dashboard.stop_preview()
        assert out == {**out, "ok": True, "running": False, "routed_to_owner": True}, out
        # the owner let go on the acknowledged stop itself, with no later status read
        assert agent.preview is None and not _listening(url)
        assert agent.preview_facts()["running"] is False
        assert dashboard.preview_facts()["running"] is False
        # the other way round: the dashboard copy starts it, the agent copy stops it
        plan = dashboard.plan({"use_model": False})
        dashboard.apply(plan["digest"], se.owner_consent(plan["digest"], "dashboard"))
        assert wait_done(dashboard)["state"] == "done"
        url = dashboard.preview_facts()["overlay_url"]
        assert agent.preview_facts()["running"] is True
        assert agent.stop_preview()["routed_to_owner"] is True
        assert dashboard.preview_facts()["running"] is False and not _listening(url)
    finally:
        agent.stop_preview()
        dashboard.stop_preview()


def _resume(eng):
    plan = eng.plan({"use_model": False})
    assert plan["kind"] == "resume", plan
    out = eng.apply(plan["digest"], se.owner_consent(plan["digest"], "dashboard"))
    assert out["ok"] and not out.get("duplicate"), out
    job = wait_done(eng)
    assert job["state"] == "done", job
    return eng.preview_facts()


def _discovery(eng):
    info = json.loads((eng.dir / "preview.json").read_text())
    return info["job"], info["pid"], (eng.dir / "preview.token").read_text().strip()


@pytest.mark.parametrize("direction", ["agent_first", "dashboard_first"])
def test_old_owner_cannot_forget_or_falsely_stop_a_resumed_preview(owner, tmp_path, monkeypatch, direction):
    """Review 1634 ordering: start #1 -> routed stop -> the OTHER copy resumes #2 -> the old owner reads its
    status and stops. #2 must stay discoverable, the old owner's status must report #2, its stop must really
    stop #2 (ports closed, nothing pending) and a delayed cleanup of #1 must not delete #2's files."""
    monkeypatch.setattr(se, "_ENGINES", {})
    agent, dashboard = se.SetupEngine(owner), se.SetupEngine(owner)
    first, second = (agent, dashboard) if direction == "agent_first" else (dashboard, agent)
    _create(first, tmp_path)
    try:
        old_show = first.preview
        url1 = first.preview_facts()["overlay_url"]
        out = second.stop_preview()
        assert out["ok"] and out["running"] is False and out["routed_to_owner"] and out["pending"] == 0, out
        assert first.preview is None and not _listening(url1)
        facts2 = _resume(second)
        url2 = facts2["overlay_url"]
        assert facts2["running"] is True and _listening(url2)
        ident2 = _discovery(second)
        # a delayed cleanup of the OLD show (compare-and-delete) leaves #2's discovery alone
        first._release_own(old_show)
        first._clear_stale()
        assert _discovery(second) == ident2
        status = first.preview_facts()  # old owner's status reflects the CURRENT owner, live
        assert status["running"] is True and status["owner"] == "other_entry" and status["mode"] == "approval", status
        assert _discovery(second) == ident2 and second.preview is not None and _listening(url2)
        pend = se.preview_call(second.dir, "GET", "/pending")
        assert pend[0] == 200
        stop = first.stop_preview()  # old owner's stop is routed to #2 and is true when it says stopped
        assert stop["ok"] is True and stop["running"] is False and stop["routed_to_owner"] is True, stop
        assert second.preview is None and not _listening(url2)
        assert not (second.dir / "preview.json").exists() and not (second.dir / "preview.token").exists()
        assert first.preview_facts()["running"] is False and second.preview_facts()["running"] is False
        assert second.stop_preview() == {"ok": True, "running": False, "pending": 0, "quiescent": True}
    finally:
        agent.stop_preview()
        dashboard.stop_preview()


def test_selected_model_connection_is_per_entry_and_never_claimed_where_unavailable(owner, tmp_path, monkeypatch):
    """Model parity across entries: only an entry that HAS the host's model client may report it usable or
    draft with it; a copy without it (dashboard module, separate process) says unavailable, and a preview
    drafted elsewhere is reported with the OWNER's actual reply source. No credentials in any setup file."""
    monkeypatch.setattr(se, "_ENGINES", {})
    calls = []

    class LoopbackModel:  # disclosed fixture for the host-owned ctx.llm edge: no network, no credentials
        def complete(self, messages, **kw):
            calls.append(kw)
            return type("R", (), {"text": "Yes, it's on! Welcome to the stream.", "provider": "fixture-provider",
                                  "model": "fixture-model"})()

    agent = se.SetupEngine(owner, model_client=LoopbackModel())
    dashboard = se.SetupEngine(owner)
    assert agent.status()["model_connection"]["state"] == "untested"
    assert agent.status()["model_connection"]["usable_here"] is False
    assert dashboard.status()["model_connection"]["usable_here"] is False
    plan = dashboard.plan({"channel": "cozy_fox", "target": os.fspath(tmp_path / "stage"), "use_model": True})
    assert not plan["ok"] and plan["next_action"] == se.MODEL_UNAVAILABLE  # no silent downgrade
    plan = agent.plan({"channel": "cozy_fox", "target": os.fspath(tmp_path / "stage"), "use_model": True})
    assert plan["uses_model"] is True
    agent.apply(plan["digest"], se.owner_consent(plan["digest"], "slash-command"))
    try:
        job = wait_done(agent)
        assert job["state"] == "done", job
        assert len(calls) == 2 and calls[0]["max_tokens"] == 4 and calls[1]["max_tokens"] > 4
        seen = dashboard.preview_facts()
        assert seen["running"] is True and seen["owner"] == "other_entry" and seen["reply_source"] == "model", seen
        for f in list(agent.dir.iterdir()) + list((tmp_path / "stage").rglob("*")):
            if f.is_file():
                assert SECRET not in f.read_text(errors="ignore"), f
    finally:
        agent.stop_preview()


_OWNER_PROC = textwrap.dedent('''
    import json, sys, time
    from pathlib import Path
    sys.path.insert(0, sys.argv[1])
    from onair import setup_engine as se
    eng = se.SetupEngine(Path(sys.argv[2]))
    def wait():
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline and (eng.job() or {}).get("state") in (None, "running", "cancelling"):
            time.sleep(0.05)
    for line in sys.stdin:
        cmd = line.strip()
        if cmd == "create":
            plan = eng.plan({"channel": "cozy_fox", "target": sys.argv[3], "use_model": False})
            eng.apply(plan["digest"], se.owner_consent(plan["digest"], "dashboard")); wait()
            out = {"job": (eng.job() or {}).get("state"), "facts": eng.preview_facts()}
        elif cmd == "resume":
            plan = eng.plan({"use_model": False})
            eng.apply(plan["digest"], se.owner_consent(plan["digest"], "dashboard")); wait()
            out = {"job": (eng.job() or {}).get("state"), "facts": eng.preview_facts()}
        elif cmd == "status":
            out = {"facts": eng.preview_facts(), "own": eng.preview is not None}
        elif cmd == "stop":
            out = {"stop": eng.stop_preview(), "own": eng.preview is not None}
        else:
            break
        print(json.dumps(out), flush=True)
    eng.stop_preview()
''')


@pytest.mark.parametrize("direction", ["other_process_first", "this_process_first"])
def test_resumed_preview_ownership_across_os_processes(owner, tmp_path, direction):
    """The same 1634 ordering with the two engines in SEPARATE OS processes, both directions."""
    script = tmp_path / "owner_proc.py"
    script.write_text(_OWNER_PROC)
    root = Path(__file__).resolve().parents[2]
    proc = subprocess.Popen([sys.executable, "-B", str(script), str(root), str(owner), str(tmp_path / "stage")],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, env=dict(os.environ))

    def other(cmd):
        proc.stdin.write(cmd + "\n")
        proc.stdin.flush()
        return json.loads(proc.stdout.readline())

    here = se.SetupEngine(owner)
    try:
        if direction == "other_process_first":
            first = other("create")
            assert first["job"] == "done" and first["facts"]["running"] is True, first
            url1 = first["facts"]["overlay_url"]
            assert here.preview_facts()["running"] is True
            out = here.stop_preview()  # routed: the other process lets go on the acknowledged stop
            assert out["ok"] and out["running"] is False and out["routed_to_owner"], out
            assert not _listening(url1)
            facts2 = _resume(here)
            url2 = facts2["overlay_url"]
            status = other("status")  # old owner's status sees #2, live, and forgets nothing
            assert status["facts"]["running"] is True and status["facts"]["owner"] == "other_entry", status
            assert (here.dir / "preview.json").exists() and here.preview is not None and _listening(url2)
            stop = other("stop")
            assert stop["stop"]["ok"] and stop["stop"]["running"] is False and stop["stop"]["routed_to_owner"], stop
            assert here.preview is None and not _listening(url2)
            assert here.preview_facts()["running"] is False
        else:
            _create(here, tmp_path)
            url1 = here.preview_facts()["overlay_url"]
            assert other("status")["facts"]["running"] is True
            stop = other("stop")
            assert stop["stop"]["ok"] and stop["stop"]["routed_to_owner"], stop
            assert here.preview is None and not _listening(url1)
            res = other("resume")
            assert res["job"] == "done" and res["facts"]["running"] is True, res
            url2 = res["facts"]["overlay_url"]
            status = here.preview_facts()
            assert status["running"] is True and status["owner"] == "other_entry", status
            assert (here.dir / "preview.json").exists() and _listening(url2)
            out = here.stop_preview()
            assert out["ok"] and out["running"] is False and out["routed_to_owner"] and out["pending"] == 0, out
            assert not _listening(url2)
            assert other("status")["facts"]["running"] is False
    finally:
        here.stop_preview()
        if proc.stdin and not proc.stdin.closed:
            proc.stdin.close()
        try:
            proc.wait(30)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(10)


def test_stop_is_not_reported_complete_while_a_model_request_is_still_in_flight(owner, tmp_path, monkeypatch):
    """Blocked fixture model: Stop during an in-flight draft is honest (off air, fenced, draining, ok false) through
    the engine, the registered agent tool and the typed command; once the request ends, status says quiescent and
    the late reply is never shown. Also: a changed plan is refused while another copy's preview runs."""
    import threading
    monkeypatch.setattr(se, "_ENGINES", {})
    gate, entered = threading.Event(), threading.Event()

    class BlockedModel:  # disclosed fixture for the host-owned ctx.llm edge: blocks until released, no network
        def complete(self, messages, **kw):
            if kw.get("max_tokens") == 4:
                return type("R", (), {"text": "OK"})()
            entered.set()
            gate.wait(30)
            return type("R", (), {"text": "Yes, it's on! Welcome to the stream.", "provider": "fixture-provider",
                                  "model": "fixture-model"})()

    eng = se.SetupEngine(owner, model_client=BlockedModel())
    other = se.SetupEngine(owner)
    plan = eng.plan({"channel": "cozy_fox", "target": os.fspath(tmp_path / "stage"), "use_model": True})
    eng.apply(plan["digest"], se.owner_consent(plan["digest"], "slash-command"))
    try:
        assert entered.wait(20)
        show = eng.preview
        assert show is not None and show.drafting == 1
        # a changed plan from the other copy may not replace a preview it does not own
        changed = other.plan({"style": {"tone": "upbeat"}})
        assert changed.get("ok") and changed.get("digest"), changed
        refused = other.apply(changed["digest"], se.owner_consent(changed["digest"], "dashboard"))
        assert refused["ok"] is False and "still running" in refused["error"], refused  # the foreign-owner refusal itself
        handlers = sa.make_handlers(FakeCtx(surface="cli"), get_engine=lambda: eng)
        out = json.loads(handlers["onair_preview_safety"]({"action": "stop"}))
        assert out["ok"] is False and out["preview"]["draining"] is True and out["preview"]["running"] is False, out
        assert out["preview"]["off_air"] is True and out["preview"]["pending"] == 0
        assert not _listening(show.overlay.overlay_url)
        text = sa.make_command(FakeCtx(surface="cli"), get_engine=lambda: eng)("stop")
        assert "NOT confirmed stopped" in text and "model request is still finishing" in text, text
        assert eng.preview_facts()["quiescent"] is False
        gate.set()
        import time
        for _ in range(100):
            if show.drafting == 0:
                break
            time.sleep(0.05)
        assert show.drafting == 0
        for _ in range(100):  # the owner's drain watcher records quiescence shortly after the request ends
            facts = eng.preview_facts()
            if facts.get("quiescent", True) is True:
                break
            time.sleep(0.05)
        assert facts["running"] is False and facts.get("quiescent", True) is True, facts
        assert show.captions == []  # the late reply was never shown
        assert eng.stop_preview() == {"ok": True, "running": False, "pending": 0, "quiescent": True}
        wait_done(eng)
    finally:
        gate.set()
        eng.stop_preview()


def test_changed_plan_never_replaces_another_copys_running_preview(owner, tmp_path, monkeypatch):
    monkeypatch.setattr(se, "_ENGINES", {})
    agent, dashboard = se.SetupEngine(owner), se.SetupEngine(owner)
    _create(agent, tmp_path)
    try:
        url = agent.preview_facts()["overlay_url"]
        changed = dashboard.plan({"use_model": False, "style": {"tone": "upbeat"}})
        assert changed.get("ok") and changed.get("digest"), changed
        out = dashboard.apply(changed["digest"], se.owner_consent(changed["digest"], "dashboard"))
        assert out["ok"] is False and "still running" in out["error"], out
        assert agent.preview is not None and _listening(url)  # untouched, still the one owner
        assert dashboard.stop_preview()["ok"] is True and not _listening(url)
        out = dashboard.apply(changed["digest"], se.owner_consent(changed["digest"], "dashboard"))
        assert out["ok"] is True, out
        assert wait_done(dashboard)["state"] == "done"
        assert dashboard.preview_facts()["style_in_effect"]["tone"] == "upbeat"
    finally:
        agent.stop_preview()
        dashboard.stop_preview()


class _GatedModel:  # disclosed fixture for the host-owned ctx.llm edge: blocks until released, no network
    def __init__(self, released, entered=None):
        self.released, self.entered = released, entered

    def complete(self, messages, **kw):
        if kw.get("max_tokens") == 4:
            return type("R", (), {"text": "OK"})()
        if self.entered is not None:
            self.entered.set()
        self.released()
        return type("R", (), {"text": "Yes, it's on! Welcome to the stream.", "provider": "fixture-provider",
                              "model": "fixture-model"})()


def _assert_non_owner_never_reports_complete(eng, rounds=2):
    """Repeated registered safety tool, typed command and status calls from the NON-owner copy while the
    owner's model request is still in flight: never ok, never 'stopped', never quiescent."""
    handlers = sa.make_handlers(FakeCtx(surface="cli"), get_engine=lambda: eng)
    command = sa.make_command(FakeCtx(surface="cli"), get_engine=lambda: eng)
    seen = []
    for _ in range(rounds):
        out = json.loads(handlers["onair_preview_safety"]({"action": "stop"}))
        assert out["ok"] is False and out["preview"]["quiescent"] is False and out["preview"]["draining"] is True, out
        text = command("stop")
        assert "NOT confirmed stopped" in text and "is stopped." not in text, text
        prog = json.loads(handlers["onair_setup_progress"]({}))
        assert prog["preview"]["quiescent"] is False and prog["preview"]["draining"] is True, prog
        assert prog["preview"]["running"] is False, prog
        facts = eng.preview_facts()
        assert facts["quiescent"] is False and facts.get("draining") is True, facts
        assert eng.stop_preview()["ok"] is False
        seen.append(out)
    return seen


def _eventually_complete(eng):
    import time
    for _ in range(200):
        out = eng.stop_preview()
        if out.get("ok"):
            break
        time.sleep(0.05)
    assert out == {"ok": True, "running": False, "pending": 0, "quiescent": True}, out
    handlers = sa.make_handlers(FakeCtx(surface="cli"), get_engine=lambda: eng)
    out = json.loads(handlers["onair_preview_safety"]({"action": "stop"}))
    assert out["ok"] is True and out["preview"]["quiescent"] is True, out
    assert sa.make_command(FakeCtx(surface="cli"), get_engine=lambda: eng)("stop").endswith("The private preview is stopped.")
    facts = eng.preview_facts()
    assert facts["running"] is False and facts.get("quiescent", True) is True and not facts.get("draining"), facts


def test_routed_draining_stop_is_honoured_by_repeated_non_owner_calls(owner, tmp_path, monkeypatch):
    """Review 1639 B (orv1639/probe/routed-drain.json): after a routed stop reports draining, repeated
    non-owner tool / command / status calls must keep honouring the LIVE owner's published quiescent:false
    even though preview.json and the control port are gone; success only after the fixture model is released."""
    import threading
    monkeypatch.setattr(se, "_ENGINES", {})
    gate, entered = threading.Event(), threading.Event()
    owner_eng = se.SetupEngine(owner, model_client=_GatedModel(lambda: gate.wait(30), entered))
    other = se.SetupEngine(owner)
    plan = owner_eng.plan({"channel": "cozy_fox", "target": os.fspath(tmp_path / "stage"), "use_model": True})
    owner_eng.apply(plan["digest"], se.owner_consent(plan["digest"], "slash-command"))
    try:
        assert entered.wait(20)
        show = owner_eng.preview
        assert show is not None and show.drafting == 1
        url = show.overlay.overlay_url
        first = other.stop_preview()  # routed to the owner, which lets go but is still drafting
        assert first["ok"] is False and first["draining"] is True and first["routed_to_owner"] is True, first
        assert other._owner_info() is None  # the owner released discovery; only its published facts remain
        assert not _listening(url)
        _assert_non_owner_never_reports_complete(other)
        # every CANCEL surface propagates the unconfirmed stop: registered tool and the dashboard route (its
        # own module copy, a third engine), each an honest partial/draining result, never ok
        handlers = sa.make_handlers(FakeCtx(surface="cli"), get_engine=lambda: other)
        cancelled = json.loads(handlers["onair_setup_cancel"]({}))
        assert cancelled["ok"] is False and cancelled["partial"] is True and cancelled["draining"] is True, cancelled
        assert cancelled["preview"]["quiescent"] is False and "NOT confirmed stopped" in cancelled["error"], cancelled
        import asyncio
        from dashboard import plugin_api
        quick = json.loads(asyncio.run(plugin_api.quick_cancel()).body)
        assert quick["ok"] is False and quick["partial"] is True and quick["preview"]["draining"] is True, quick
        # no REPLACEMENT before positive quiescence: once the cancelled job has ended (so no job refusal applies),
        # a new plan from this copy is refused as DRAINING while the old preview's request is still in flight
        import time
        for _ in range(200):
            if (other.job() or {}).get("state") not in ("running", "cancelling"):
                break
            time.sleep(0.05)
        assert (other.job() or {}).get("state") == "cancelled", other.job()
        changed = other.plan({"channel": "cozy_fox", "target": os.fspath(tmp_path / "stage2"), "use_model": False})
        assert changed.get("ok") and changed.get("digest"), changed
        refused = other.apply(changed["digest"], se.owner_consent(changed["digest"], "dashboard"))
        assert refused["ok"] is False and refused["draining"] is True and "still finishing" in refused["error"], refused
        assert other.preview is None and owner_eng.preview is None
        assert show.drafting == 1  # the fixture model really is still in flight
        gate.set()
        _eventually_complete(other)
        assert show.captions == []  # the late reply was never shown
        assert json.loads(handlers["onair_setup_cancel"]({}))["ok"] is True  # complete now, so cancel is ok
        again = other.apply(changed["digest"], se.owner_consent(changed["digest"], "dashboard"))
        assert again["ok"] is True, again  # after positive quiescence the changed plan may start
        assert wait_done(other)["state"] == "done"
    finally:
        gate.set()
        owner_eng.stop_preview()
        other.stop_preview()


_DRAIN_OWNER_PROC = textwrap.dedent('''
    import json, sys, time
    from pathlib import Path
    sys.path.insert(0, sys.argv[1])
    from onair import setup_engine as se
    release = Path(sys.argv[4])
    class GatedModel:  # disclosed fixture: blocks until the release file exists, no network
        def complete(self, messages, **kw):
            if kw.get("max_tokens") == 4:
                return type("R", (), {"text": "OK"})()
            Path(sys.argv[4] + ".entered").write_text("1")
            deadline = time.monotonic() + 60
            while not release.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            return type("R", (), {"text": "Yes, it's on!", "provider": "fixture-provider", "model": "fixture-model"})()
    eng = se.SetupEngine(Path(sys.argv[2]), model_client=GatedModel())
    for line in sys.stdin:
        cmd = line.strip()
        if cmd == "create":
            plan = eng.plan({"channel": "cozy_fox", "target": sys.argv[3], "use_model": True})
            eng.apply(plan["digest"], se.owner_consent(plan["digest"], "slash-command"))
            deadline = time.monotonic() + 30
            while not Path(sys.argv[4] + ".entered").exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            show = eng.preview
            out = {"drafting": getattr(show, "drafting", None), "facts": eng.preview_facts()}
        elif cmd == "drafting":
            out = {"drafting": [getattr(d, "drafting", None) for d in eng._draining] or getattr(eng.preview, "drafting", None)}
        else:
            break
        print(json.dumps(out), flush=True)
    deadline = time.monotonic() + 30
    while eng._still_draining() and time.monotonic() < deadline:
        time.sleep(0.05)
    eng.stop_preview()
''')


def test_routed_draining_stop_is_honoured_across_os_processes(owner, tmp_path):
    """The same routed-drain ordering with the owner in a SEPARATE OS process."""
    script = tmp_path / "drain_owner.py"
    script.write_text(_DRAIN_OWNER_PROC)
    release = tmp_path / "release"
    root = Path(__file__).resolve().parents[2]
    proc = subprocess.Popen([sys.executable, "-B", str(script), str(root), str(owner), str(tmp_path / "stage"),
                             str(release)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
                            env=dict(os.environ))

    def ask(cmd):
        proc.stdin.write(cmd + "\n")
        proc.stdin.flush()
        return json.loads(proc.stdout.readline())

    here = se.SetupEngine(owner)
    try:
        started = ask("create")
        assert started["drafting"] == 1 and started["facts"]["running"] is True, started
        url = started["facts"]["overlay_url"]
        first = here.stop_preview()
        assert first["ok"] is False and first["draining"] is True and first["routed_to_owner"] is True, first
        assert not _listening(url)
        _assert_non_owner_never_reports_complete(here)
        cancelled = json.loads(sa.make_handlers(FakeCtx(surface="cli"), get_engine=lambda: here)["onair_setup_cancel"]({}))
        assert cancelled["ok"] is False and cancelled["partial"] is True and cancelled["draining"] is True, cancelled
        assert ask("drafting")["drafting"] == [1]  # still in flight in the owner process
        release.write_text("1")
        _eventually_complete(here)
    finally:
        release.write_text("1")
        if proc.stdin and not proc.stdin.closed:
            proc.stdin.close()
        try:
            proc.wait(60)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(10)
        here.stop_preview()

