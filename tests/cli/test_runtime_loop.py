"""The runtime gate in the real Hermes agent loop, against a loopback provider that counts requests.

A subprocess (so importing ``run_agent`` cannot re-launch the test process) installs the plugin
into an isolated HERMES_HOME, loads it through Hermes's own plugin discovery, connects the real
adapter and runs real ``AIAgent`` turns (``_agent_loop_driver.py``). Only Twitch is faked; the
provider is this test's HTTP server, which speaks the OpenAI chat-completions API (streaming too).
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
REPLY = "fake provider reply"
REFUSAL = "\U0001f6d1 On Air runtime gate: request refused before the provider was called."

if importlib.util.find_spec("run_agent") is None or importlib.util.find_spec("hermes_cli") is None:
    pytest.skip("needs the Hermes agent runtime on the path", allow_module_level=True)


class Provider(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _json(self, payload):
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.server.requests.append(("GET", self.path, ""))
        self._json({"object": "list", "data": [{"id": "fake-model", "object": "model"}]})

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode("utf-8", "replace")
        self.server.requests.append(("POST", self.path, raw))
        try:
            req = json.loads(raw)
        except ValueError:
            req = {}
        if not self.path.endswith("/chat/completions"):
            return self._json({})
        if getattr(self.server, "fail_model", False):
            payload = json.dumps({"error": {"message": "quota reached", "type": "insufficient_quota", "code": "insufficient_quota"}}).encode()
            self.send_response(429)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        if req.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for delta, finish in (({"role": "assistant", "content": REPLY}, None), ({}, "stop")):
                chunk = {"id": "c1", "object": "chat.completion.chunk", "created": 1, "model": "fake-model",
                         "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
                self.wfile.write(b"data: " + json.dumps(chunk).encode() + b"\n\n")
            self.wfile.write(b"data: [DONE]\n\n")
            return
        self._json({"id": "c1", "object": "chat.completion", "created": 1, "model": "fake-model",
                    "choices": [{"index": 0, "message": {"role": "assistant", "content": REPLY},
                                 "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})


@pytest.fixture
def provider():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    server.requests = []
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server
    server.shutdown()
    server.server_close()


def run_driver(tmp_path, provider, *args, stage_home):
    """Install the plugin into a fresh home, run the driver there and return its cases by name."""
    home = tmp_path / "hermes"
    shutil.copytree(ROOT / "onair", home / "plugins" / "on-air" / "onair",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for name in ("__init__.py", "plugin.yaml"):
        shutil.copy(ROOT / name, home / "plugins" / "on-air" / name)
    if stage_home:
        # The stage runs in a marked stage home. There the plugin registers the co-host only: its owner tools
        # (setup, presentation) are for the owner's own home and would be a tool surface in a co-host request.
        from onair.onboarding import MARKER
        (home / MARKER).write_text("test stage home\n", encoding="utf-8")
    env = dict(os.environ, HERMES_HOME=str(home), HOME=str(tmp_path), PYTHONDONTWRITEBYTECODE="1",
               HERMES_DISABLE_LAZY_INSTALLS="1", ONAIR_ALLOWED_USERS="onair-stage")
    base_url = f"http://127.0.0.1:{provider.server_address[1]}/v1"
    proc = subprocess.run([sys.executable, str(Path(__file__).with_name("_agent_loop_driver.py")), str(home), base_url, *args],
                          env=env, cwd=tmp_path, capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, proc.stderr[-4000:]
    return {c["case"]: c for c in (json.loads(line[5:]) for line in proc.stdout.splitlines() if line.startswith("CASE "))}, proc


def test_an_owner_home_cannot_arm_the_stage(tmp_path, provider):
    """The same real plugin load in a home that is NOT a stage home: the owner tools are registered there, so
    preflight refuses to arm (and names the stage home) instead of letting every reply be refused on air."""
    cases, _ = run_driver(tmp_path, provider, "arm-only", stage_home=False)
    result = cases["arm"]["result"]
    assert result.startswith("refused: preflight failed"), result
    assert "owner tools (onair_present, onair_setup)" in result and "--create-home" in result, result
    assert not [path for _, path, _ in provider.requests if path.endswith("/chat/completions")]


def test_real_agent_loop_gate(tmp_path, provider):
    cases, proc = run_driver(tmp_path, provider, stage_home=True)
    assert set(cases) == {"unarmed", "arm", "clean", "tools", "rearm_latched", "clear", "rearm", "stage_clean",
                          "stage_other_platform", "clear2", "rearm2", "memory"}, proc.stdout

    inference = [body for method, path, body in provider.requests if path.endswith("/chat/completions")]
    # (a) Clean, armed, no tools: exactly one provider request per clean turn, in order, and each is
    # that turn's: the direct clean turn, the stage turn, and the mismatched-platform stage turn
    # (the gate does not see that one, so the provider does; its reply is withheld below).
    assert len(inference) == 3, [body[:80] for body in inference]
    for body, tag in zip(inference, ("case-clean", "case-stage-clean", "case-stage-other-platform")):
        assert tag in body
        assert '"tools"' not in body and "personal notes" not in body
    assert cases["clean"]["final"] == REPLY and cases["clean"]["violation"] is None
    # Hermes's first-message note (introduce yourself, mention /help, offer a profile) was on that prompt and
    # is not in the request the provider received.
    assert "case-clean hello" in inference[0]
    assert "very first message ever" not in inference[0] and "/help" not in inference[0]
    assert cases["clean"]["toolsets"] == []
    # Every other request the run made (model metadata probes) carries no conversation content.
    for method, path, body in provider.requests:
        for tag in ("case-unarmed", "case-tools", "case-memory", "private note"):
            assert tag not in body, (path, tag)

    # (d) Not armed: refused before inference (pre_llm_call), no latch, no cut, no receipt.
    unarmed = cases["unarmed"]
    assert unarmed["final"] == REFUSAL and unarmed["api_calls"] == 0
    assert unarmed["violation"] is None and unarmed["receipts"] == []
    assert cases["arm"]["result"] == "approval"

    # (b) Tools in the request: zero provider requests, one bounded attempt, latched and cut to OFF AIR.
    tools = cases["tools"]
    assert tools["toolsets"] == ["todo"] and tools["final"] == REFUSAL and tools["api_calls"] <= 1
    assert tools["violation"] == "tool_surface" and tools["mode"] == "off_air"
    assert [(r["kind"], r["rule"]) for r in tools["receipts"]] == [("runtime_refused", "tool_surface")]
    # Latched: the fixed profile still cannot arm until an explicit clear; then it can.
    assert cases["rearm_latched"]["result"].startswith("refused: preflight failed: runtime_refused:tool_surface")
    assert cases["clear"]["result"] == "off_air" and cases["rearm"]["result"] == "approval"

    # (e) Through the real adapter: the stage dispatches a live chat turn, a real AIAgent answers it.
    # With the gate's platform key, the gate passes the request and the reply airs as a caption.
    stage = cases["stage_clean"]
    assert stage["turn"] and stage["captions"] == [REPLY] and stage["line_rules"] == ["released"]
    assert stage["violation"] is None and stage["mode"] == "autonomous" and stage["passed"] == 2
    # With a platform key the gate does not match, the gate never sees the request: the reply is
    # withheld at send(), the show latches gate_not_observed and cuts. Nothing airs.
    other = cases["stage_other_platform"]
    assert other["turn"] and other["captions"] == [] and other["audio"] == 0
    assert other["line_rules"] == ["runtime_refused"] and other["passed"] == 2
    assert other["violation"] == "gate_not_observed" and other["mode"] == "off_air"
    assert cases["clear2"]["result"] == "off_air" and cases["rearm2"]["result"] == "approval"

    # (c) Hermes's memory block in the system prompt: zero provider requests, latched, cut.
    memory = cases["memory"]
    assert memory["final"] == REFUSAL and memory["api_calls"] <= 1
    assert memory["violation"] == "private_context" and memory["mode"] == "off_air"
    assert [(r["kind"], r["rule"]) for r in memory["receipts"]] == [
        ("runtime_refused", "tool_surface"), ("runtime_cleared", "tool_surface"),
        ("runtime_refused", "gate_not_observed"), ("runtime_cleared", "gate_not_observed"),
        ("runtime_refused", "private_context")]
    assert "private note" not in proc.stdout
    # Bounded: each turn finished promptly (no retry storm against the refusal).
    for case in ("unarmed", "clean", "tools", "stage_clean", "stage_other_platform", "memory"):
        assert cases[case]["seconds"] < 30


def test_real_agent_quota_failure_does_not_air(tmp_path, provider):
    provider.fail_model = True
    cases, _ = run_driver(tmp_path, provider, "stage-error", stage_home=True)
    case = cases["stage_error"]
    assert case["turn"] and case["captions"] == [] and case["audio"] == 0
    assert case["failures"] and case["failures"][-1] == "rate_limited"
    assert "quota reached" not in json.dumps(case)
