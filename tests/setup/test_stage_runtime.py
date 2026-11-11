"""Product A: the owner's agent operates the REAL prepared stage through the REGISTERED setup entry.

``setup_agent.register`` -> the registered ``onair_stage_*`` tools and ``/onair-setup stage ...`` command ->
``stage_runtime`` -> the public Hermes CLI in the saved stage home (``hermes onair preflight``, then
``hermes gateway run``: the REAL gateway runner, the real plugin load, adapter, runtime gate, guard, desk and
overlay) -> observed state -> stop -> start again (resume) -> stop.

Disclosed fixtures (nothing else is faked):
- external Twitch edge: a loopback IRC server; ``tests/setup/fixture_site/sitecustomize.py`` redirects the real
  ``TwitchChatSource``'s TLS connection to it and refuses every non-loopback connection in the stage process;
- external model edge: a loopback OpenAI-compatible server named in the stage's own config;
- user-completed prerequisites, done the way the user would but without the plugin manager or ``hermes model``
  (both may download/ask for keys): the plugin is a DROP-IN COPY (not plugin-manager acceptance), the model
  entry names the loopback fixture with a non-secret placeholder key, and the OBS overlay port is a free one;
- the owner's host context (``FakeCtx``: CLI session surface + the approval card answered "start").
The owner acting on the stage's own desk (arming approval mode, approving a held line) uses the stage's real
token-authenticated desk API: those are the owner's controls, not the agent's.
"""
from __future__ import annotations

import contextlib
import http.client
import json
import os
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import pytest

from onair import setup_agent as sa
from onair import setup_engine as se
from onair import stage_runtime as sr

from test_installed_entry import _install_copy
from test_quick_setup import SECRET, FakeCtx, owner, wait_done  # noqa: F401

FIXTURE_SITE = Path(__file__).resolve().parent / "fixture_site"
REPLY = "Thanks for asking, viewer two!"


class FakeIrc(threading.Thread):
    """DISCLOSED FIXTURE: anonymous Twitch IRC on loopback (welcome, join, then the lines a test sends)."""
    def __init__(self, channel):
        super().__init__(daemon=True)
        self.channel, self.lines, self.conns = channel, [], []
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(4)
        self.port = self.sock.getsockname()[1]

    def run(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            self.conns.append(conn)
            conn.sendall(f":tmi.twitch.tv 001 justinfan :Welcome\r\n:justinfan!justinfan@justinfan.tmi.twitch.tv "
                         f"JOIN #{self.channel}\r\n".encode())
            threading.Thread(target=self._read, args=(conn,), daemon=True).start()

    def _read(self, conn):
        with contextlib.suppress(OSError):
            for raw in conn.makefile("rb"):
                self.lines.append(raw.decode(errors="replace").strip())

    def say(self, user, text, msg_id):
        line = (f"@badge-info=;badges=;display-name={user};id={msg_id};mod=0;subscriber=0;tmi-sent-ts="
                f"{int(time.time() * 1000)};user-id=u-{user} :{user}!{user}@{user}.tmi.twitch.tv PRIVMSG "
                f"#{self.channel} :{text}\r\n").encode()
        for conn in list(self.conns):
            with contextlib.suppress(OSError):
                conn.sendall(line)

    def close(self):
        with contextlib.suppress(OSError):
            self.sock.close()
        for conn in self.conns:
            with contextlib.suppress(OSError):
                conn.close()


class _Model(BaseHTTPRequestHandler):
    """DISCLOSED FIXTURE for the external model edge: an OpenAI-compatible loopback server."""
    def log_message(self, *a):
        pass

    def _send(self, code, payload, ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        self._send(200, json.dumps({"data": [{"id": "fixture-model", "object": "model"}]}).encode())

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        # bounded, REDACTED provenance (no message content, no auth header values): when, what kind, from what
        msgs = req.get("messages") if isinstance(req.get("messages"), list) else []
        self.server.requests.append({"path": self.path, "tools": bool(req.get("tools")), "stream": bool(req.get("stream")),
                                     "t": round(time.time(), 3), "roles": [m.get("role") for m in msgs if isinstance(m, dict)],
                                     "max_tokens": req.get("max_tokens") or req.get("max_completion_tokens"),
                                     "user_agent": (self.headers.get("User-Agent") or "")[:80],
                                     "header_names": sorted(h for h in self.headers.keys() if h.lower() != "authorization"),
                                     "auth_present": bool(self.headers.get("Authorization"))})
        if req.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for delta, finish in (({"role": "assistant", "content": REPLY}, None), ({}, "stop")):
                chunk = {"id": "fx", "object": "chat.completion.chunk", "model": "fixture-model",
                         "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
                self.wfile.write(b"data: " + json.dumps(chunk).encode() + b"\n\n")
            self.wfile.write(b"data: [DONE]\n\n")
            return
        self._send(200, json.dumps({"id": "fx", "object": "chat.completion", "model": "fixture-model",
                                    "choices": [{"index": 0, "finish_reason": "stop",
                                                 "message": {"role": "assistant", "content": REPLY}}],
                                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}).encode())


class OverlayClient(threading.Thread):
    """A real overlay subscriber (the SSE stream OBS reads), connected BEFORE the owner arms."""
    def __init__(self, url):
        super().__init__(daemon=True)
        self.port, self.events, self.subscribed = urlparse(url).port, [], threading.Event()

    def run(self):
        with contextlib.suppress(Exception):
            conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=120)
            conn.request("GET", "/events")
            resp = conn.getresponse()
            while True:
                line = resp.fp.readline()
                if not line:
                    return
                if line.startswith(b"data: "):
                    self.events.append(json.loads(line[6:]))
                    self.subscribed.set()


def owner_desk(home: Path, method: str, path: str, body: dict | None = None):
    """The OWNER on the stage's own desk (token-authenticated), e.g. arming approval mode or approving a line."""
    info = json.loads((home / "plugin-data" / "on-air" / "control.json").read_text())
    token = (home / "plugin-data" / "on-air" / info["token_file"]).read_text().strip()
    conn = http.client.HTTPConnection("127.0.0.1", urlparse(info["url"]).port, timeout=5)
    try:
        conn.request(method, path, json.dumps(body).encode() if body is not None else None,
                     {"Authorization": "Bearer " + token, "Content-Type": "application/json"})
        resp = conn.getresponse()
        return resp.status, json.loads(resp.read() or b"{}")
    finally:
        conn.close()


def _listening(url: str) -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", urlparse(url).port)) == 0


def _made_stage(owner, tmp_path, monkeypatch):
    monkeypatch.setattr(se, "_ENGINES", {})
    eng = se.engine()
    plan = eng.plan({"channel": "cozy_fox", "target": os.fspath(tmp_path / "stage"),
                     "style": {"reply_pacing": "less_often", "avoid_topics": ["politics", "crypto"]}})
    eng.apply(plan["digest"], se.owner_consent(plan["digest"], "dashboard"))
    home = Path(wait_done(eng)["home"])
    assert eng.stop_preview()["ok"] is True
    ctx = FakeCtx(choice="start")
    registered = sa.register(ctx)
    assert {"tool:onair_stage_status", "tool:onair_stage_plan", "tool:onair_stage_safety"} <= set(registered)
    tools = {name: entry[2] for name, entry in ctx.tools.items()}
    call = lambda name, args=None: json.loads(tools[name](args or {}))  # noqa: E731
    return eng, home, ctx, call, ctx.commands["onair-setup"]


def test_unprepared_stage_names_exact_prerequisites_and_starts_nothing(owner, tmp_path, monkeypatch):
    eng, home, ctx, call, command = _made_stage(owner, tmp_path, monkeypatch)
    status = call("onair_stage_status")
    assert status["running"] is False and status["startable"] is False, status
    out = call("onair_stage_plan")
    assert out["ok"] is False and out["prepared"] is False and "approve_with" not in out, out
    assert [m["key"] for m in out["missing"]] == ["plugin", "model"], out
    assert "hermes plugins install" in out["next_action"] and f"HERMES_HOME={home} hermes model" in out["next_action"]
    assert all(m["agent_can_do"] is False for m in out["missing"]), out
    steps = {p["key"]: p for p in out["prerequisites"]}
    assert steps["preflight"]["agent_can_do"] is True and steps["gateway"]["agent_can_do"] is True, steps
    assert command("stage approve abc123").startswith("No current stage plan"), ctx.asked
    assert ctx.asked == []  # no card: there is nothing to approve
    refused = sr.start(eng, "x", consent="approved")  # an agent/tool cannot mint the owner's approval
    assert refused["refused"] is True
    assert not eng._path(sr.RUNTIME_FILE).exists()
    assert call("onair_stage_safety", {"action": "stop"})["ok"] is True  # nothing running: honest no-op
    # public stage / messaging surfaces are refused before anything is read
    bad = FakeCtx(surface="on_air")
    sa.register(bad)
    assert json.loads(bad.tools["onair_stage_plan"][2]({}))["refused"] is True
    assert bad.commands["onair-setup"]("stage status") == "On Air setup is not available to the public stage."


@pytest.mark.skipif(sr.hermes_command() is None, reason="no hermes CLI beside this interpreter or on PATH")
def test_registered_agent_action_runs_the_prepared_stage_lifecycle(owner, tmp_path, monkeypatch):
    pytest.importorskip("gateway.run")
    eng, home, ctx, call, command = _made_stage(owner, tmp_path, monkeypatch)
    irc = FakeIrc("cozy_fox")
    irc.start()
    model = ThreadingHTTPServer(("127.0.0.1", 0), _Model)
    model.requests = []
    threading.Thread(target=model.serve_forever, daemon=True).start()
    # user-completed prerequisites (disclosed above): drop-in plugin copy, loopback model entry, free OBS port
    _install_copy(home)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        free = s.getsockname()[1]
    cfg = (home / "config.yaml").read_text().replace("overlay_port: 18790", f"overlay_port: {free}")
    cfg += (f"model:\n  provider: custom\n  base_url: http://127.0.0.1:{model.server_address[1]}/v1\n"
            "  default: fixture-model\n  api_key: fixture-not-a-secret\n")
    (home / "config.yaml").write_text(cfg)
    (tmp_path / "onair-fixture.json").write_text(json.dumps({"irc_port": irc.port, "planted_secret": SECRET}))
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join([str(FIXTURE_SITE)] + [
        p for p in os.environ.get("PYTHONPATH", "").split(os.pathsep) if p]))
    monkeypatch.setenv("OPENROUTER_API_KEY", SECRET)  # the owner's own key in the owner's process environment
    pids = []
    try:
        before = call("onair_stage_status")
        assert before["running"] is False and before["startable"] is True, before
        # round 0: a start that does not connect in time KEEPS its owned process and says so; the registered
        # safety stop recovers it, then a NEW approval starts again (failed start -> owned stop -> new start)
        monkeypatch.setattr(sr, "START_WAIT_S", 0.05)
        code0 = call("onair_stage_plan")["approve_with"]["chat"].split()[-1]
        failed = command(f"stage approve {code0}")
        assert failed.startswith("The stage did NOT start correctly") and "STILL RUNNING" in failed, failed
        kept = call("onair_stage_status")
        assert kept["running"] is True, kept
        pids.append(kept["pid"])
        recovered = call("onair_stage_safety", {"action": "stop"})
        assert recovered["ok"] is True and call("onair_stage_status")["state"] == "stopped", recovered
        # endpoints known -> checked; never observed -> only the complete session-gone proof, labelled as such
        assert recovered["endpoints_observed"] or str(recovered["unobserved_endpoints_proof"]).startswith(
            "session_gone"), recovered
        monkeypatch.setattr(sr, "START_WAIT_S", 45.0)
        planned = call("onair_stage_plan")
        assert planned["ok"], planned
        with socket.socket() as busy:
            busy.bind(("127.0.0.1", free))
            busy.listen()
            refused = command("stage approve " + planned["approve_with"]["chat"].split()[-1])
            assert str(free) in refused and "overlay_port" in refused, refused
        assert call("onair_stage_status")["last_refusal"]["reason"]
        for round_no in (1, 2):  # start, stop, then start again (resume) and stop again
            plan = call("onair_stage_plan")
            assert plan["ok"] is True and plan["prepared"] is True, plan
            assert any("hermes gateway run" in e for e in plan["effects"]) and any("OFF AIR" in e for e in plan["effects"])
            code = plan["approve_with"]["chat"].split()[-1]
            for _ in range(3):  # read-only status never rotates the shown code (1645 reproduction)
                assert call("onair_stage_status")["startable"] is True
            assert json.loads(eng._path(sr.PLAN_FILE).read_text())["code"] == code
            reply = command(f"stage approve {code}")
            assert ctx.asked and ctx.asked[-1][0].startswith("Start the real On Air stage?"), ctx.asked
            assert "running OFF AIR" in reply, reply + (eng._path(sr.LOG_FILE).read_text()[-3000:])
            st = call("onair_stage_status")
            assert st["running"] is True and st["state"] == "connected" and st["mode"] == "off_air", st
            assert "last_refusal" not in st, st
            assert command(f"stage approve {code}").startswith("No current stage plan")  # one approval, one start
            assert "No current stage plan" in call("onair_stage_status")["last_refusal"]["reason"]
            pids.append(st["pid"])
            info = json.loads((home / "plugin-data" / "on-air" / "control.json").read_text())
            assert info["pid"] == st["pid"]  # the observed stage IS the process this action started
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline and f"JOIN #cozy_fox" not in irc.lines:
                time.sleep(0.1)
            assert "JOIN #cozy_fox" in irc.lines, irc.lines  # the real Twitch reader joined (loopback fixture)
            if round_no == 1:
                viewer = OverlayClient(st["overlay_url"])
                viewer.start()
                assert viewer.subscribed.wait(10)
                assert owner_desk(home, "POST", "/mode", {"mode": "approval"})[0] == 200  # the OWNER arms
                irc.say("viewer_two", "hi! how is the stream going today?", "m1")
                deadline, pending = time.monotonic() + 90, []
                while time.monotonic() < deadline and not pending:
                    pending = owner_desk(home, "GET", "/pending")[1].get("pending") or []
                    time.sleep(0.3)
                assert [p.get("text") for p in pending] == [REPLY], (pending, model.requests,
                                                                     eng._path(sr.LOG_FILE).read_text()[-4000:])
                assert model.requests and not any(r["tools"] for r in model.requests), model.requests
                assert not [e for e in viewer.events if e.get("type") == "caption"]  # nothing aired before approval
                for line in pending:
                    assert owner_desk(home, "POST", "/approve", {"line_id": line["line_id"], "epoch": line["epoch"]})[0] == 200
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline and not any(e.get("type") == "caption" for e in viewer.events):
                    time.sleep(0.1)
                assert [e.get("text") for e in viewer.events if e.get("type") == "caption"] == [REPLY]
                # an approval while it already runs starts nothing and reports the OBSERVED mode (not 'OFF AIR')
                again = command("stage approve " + call("onair_stage_plan")["approve_with"]["chat"].split()[-1])
                assert again.startswith("The stage was already running") and "'approval'" in again, again
                assert "NOT off air" in again and "running OFF AIR" not in again, again
                real_desk = sr.desk_call
                monkeypatch.setattr(sr, "desk_call", lambda *a, **k: (503, {"error": "fixture: desk unanswered"}))
                unanswered = command("stage approve " + call("onair_stage_plan")["approve_with"]["chat"].split()[-1])
                assert "did not answer" in unanswered and "NOT confirmed off air" in unanswered, unanswered
                assert "NOT confirmed off air" in command("stage status")
                monkeypatch.setattr(sr, "desk_call", real_desk)
                off = call("onair_stage_safety", {"action": "off_air"})  # agent safety control
                assert off["ok"] is True and off["mode"] == "off_air", off
                stopped = call("onair_stage_safety", {"action": "stop"})
            else:
                stopped_text = command("stage stop")
                assert stopped_text.startswith("The real stage is stopped"), stopped_text
                stopped = {"ok": True}
            assert stopped["ok"] is True, stopped
            assert not _listening(info["url"]) and not _listening(info["overlay_url"])
            assert not (home / "plugin-data" / "on-air" / "control.json").exists()
            after = call("onair_stage_status")
            assert after["running"] is False and after["state"] == "stopped", after
        assert len(set(pids)) == 3
        envs = [json.loads(p.read_text()) for p in tmp_path.glob("onair-fixture-env.*.json")]
        assert envs and not any(e["planted_secret_present"] or "OPENROUTER_API_KEY" in e["names"] for e in envs), envs
        assert all(e["names"] and "HERMES_HOME" in e["names"] and e["home_inside_stage"] for e in envs), envs
        if os.environ.get("ONAIR_EVIDENCE_DIR"):
            (Path(os.environ["ONAIR_EVIDENCE_DIR"]) / "stage-lifecycle.json").write_text(json.dumps(
                {"pids": pids, "irc_lines": irc.lines, "model_requests": model.requests, "env_names": envs,
                 "asked": [a[0] for a in ctx.asked]}, indent=1))
    finally:
        with contextlib.suppress(Exception):
            sr.stop(eng)
        model.shutdown()
        model.server_close()
        irc.close()


def _prepare(home: Path, model_port: int = 9) -> None:
    """User-completed prerequisites (disclosed in the module docstring): drop-in plugin copy + loopback model."""
    _install_copy(home)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    config = home / "config.yaml"
    config.write_text(config.read_text().replace("overlay_port: 18790", f"overlay_port: {port}") + (
        f"model:\n  provider: custom\n  base_url: http://127.0.0.1:{model_port}/v1\n  default: fixture-model\n"
        "  api_key: fixture-not-a-secret\n"))


@pytest.mark.skipif(sr.hermes_command() is None, reason="no hermes CLI beside this interpreter or on PATH")
def test_a_sticky_profile_cannot_redirect_the_stage_start(owner, tmp_path, monkeypatch):
    """Exact-target seam (hermes_cli/main.py _apply_profile_override): a HERMES_HOME outside ``profiles/`` is
    re-homed to that root's sticky ``active_profile``. A conflicting sticky profile is planted in the stage's
    own root (isolated fixture). Unpinned, Hermes resolves the OTHER home and the start refuses BEFORE any
    preflight/gateway effect; with the shipped environment (profile pin) Hermes resolves the stage itself."""
    eng, home, ctx, call, command = _made_stage(owner, tmp_path, monkeypatch)
    _prepare(home)
    other = home / "profiles" / "other"
    other.mkdir(parents=True)
    (other / "config.yaml").write_text("model:\n  provider: custom\n  default: other-model\n")
    (home / "active_profile").write_text("other\n")
    argv0 = sr.hermes_command()
    same, resolved = sr.effective_home(argv0, sr.child_env(home), home)
    assert same is True and Path(resolved) == home / "config.yaml", resolved  # the pin keeps the stage
    plan = call("onair_stage_plan")
    assert plan["ok"] is True, plan
    shipped = sr.child_env
    monkeypatch.setattr(sr, "child_env", lambda h: {k: v for k, v in shipped(h).items()
                                                    if k != "HERMES_GATEWAY_PROFILE_PIN"})
    status = call("onair_stage_status")
    assert status["startable"] is False and status["missing"][0]["key"] == "target", status
    # Simulate a target change after readiness so the final start check is exercised too.
    monkeypatch.setattr(sr, "_stage_preflight", lambda *_: {"ok": True, "missing": []})
    reply = command("stage approve " + plan["approve_with"]["chat"].split()[-1])
    assert reply.startswith("The stage did NOT start correctly: Hermes would not run the saved stage folder"), reply
    assert str(other / "config.yaml") in reply, reply  # the redirect Hermes would have made, named
    assert not eng._path(sr.RUNTIME_FILE).exists() and not eng._path(sr.LOG_FILE).exists()
    assert not (home / "plugin-data" / "on-air" / "control.json").exists()
    assert call("onair_stage_status")["running"] is False
