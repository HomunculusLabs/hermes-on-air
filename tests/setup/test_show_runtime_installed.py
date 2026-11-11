"""The private agent-hosted show through the INSTALLED plugin on the supported Python 3.14 environment.

One clean subprocess, run with the prepared Hermes 3.14 interpreter (ruamel.yaml present, PyYAML absent),
loads a copy of the plugin through the real Hermes loader (``discover_plugins``) in a throwaway HERMES_HOME
and drives the show through the plugin's own registered modules:

* the host's real ``ctx.llm`` (``agent.plugin_llm.PluginLlm`` -> Hermes ``call_llm``) answers every turn; its
  provider edge is a DISCLOSED loopback OpenAI-compatible fixture (no genuine provider request);
* the host's real ``ctx.dispatch_tool("text_to_speech")`` speaks every released line; its provider edge is a
  DISCLOSED ``type: command`` TTS fixture that writes a short silent WAV (no genuine voice synthesis);
* the real StageController, render_turn_prompt, output guard, HermesTTS and OverlayServer run the show.

The human's approvals are the test's stand-in: ``owner_consent`` is minted exactly where the dashboard
click / slash-command card would mint it (disclosed). The registered TOOL handler is also called with no
genuine owner session and must refuse: ownership is never fabricated.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
def _hermes_env() -> tuple[Path, Path]:
    """The Hermes source tree and its Python 3.14: ONAIR_HERMES_SDK / ONAIR_PY314, else a standard install."""
    sdk = Path(os.environ.get("ONAIR_HERMES_SDK") or Path.home() / ".hermes" / "hermes-agent")
    configured = os.environ.get("ONAIR_PY314")
    if configured:
        return sdk, Path(configured)
    found = sorted((Path.home() / ".hermes" / "installs").glob("*/environments/*/venv/bin/python"),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    return sdk, (found[0] if found else sdk / "venv" / "bin" / "python")


SDK, PY314 = _hermes_env()

PROBE = r'''
import json, os, socket, sys, threading, time, importlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import importlib.util as iu
REPO = Path(sys.argv[1]).resolve()
sys.path[:] = [p for p in sys.path if Path(p or ".").resolve() != REPO]
out = {"python": sys.version.split()[0], "pyyaml": bool(iu.find_spec("yaml")), "ruamel": bool(iu.find_spec("ruamel.yaml"))}
REFUSED = []
_real_connect = socket.socket.connect
def _loopback_only(self, address):
    host = address[0] if isinstance(address, tuple) else ""
    if host not in ("127.0.0.1", "::1", "localhost") and not isinstance(address, str):
        REFUSED.append(str(host)); raise OSError("non-loopback connections are disabled in this fixture")
    return _real_connect(self, address)
socket.socket.connect = _loopback_only

PROMPTS = []
class LoopbackModel(BaseHTTPRequestHandler):
    """DISCLOSED FIXTURE for the external model edge only."""
    def log_message(self, *a): pass
    def _json(self, payload):
        body = json.dumps(payload).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
    def do_GET(self):
        self._json({"object": "list", "data": [{"id": "fixture-model", "object": "model"}]})
    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        if not self.path.endswith("/chat/completions"):
            return self._json({})
        msgs = req.get("messages") or []
        user = next((m.get("content") for m in reversed(msgs) if m.get("role") == "user"), "")
        system = next((m.get("content") for m in msgs if m.get("role") == "system"), "")
        PROMPTS.append({"user": user, "system_has_persona": "Pixel" in str(system), "tools": bool(req.get("tools"))})
        if "<show_note" in str(user) and "opening" in str(user):
            reply = "Hey everyone, I am Pixel and today we are wiring a desk lamp that dims when chat goes quiet!"
        elif "<show_note" in str(user):
            reply = "Quick soldering thought: tin the tip first and the joint basically makes itself."
        elif "lamp" in str(user):
            reply = "Great question! The lamp listens to chat and fades a little every quiet minute."
        else:
            reply = ""
        self._json({"id": "c1", "object": "chat.completion", "created": 1, "model": "fixture-model",
                    "choices": [{"index": 0, "message": {"role": "assistant", "content": reply}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})

model = ThreadingHTTPServer(("127.0.0.1", 0), LoopbackModel)
threading.Thread(target=model.serve_forever, daemon=True).start()
home = Path(os.environ["HERMES_HOME"])
cfg = {"_config_version": int(sys.argv[2]), "plugins": {"enabled": ["on-air"]},
       "model": {"provider": "custom", "default": "fixture-model",
                 "base_url": "http://127.0.0.1:%d/v1" % model.server_address[1], "api_key": "fixture-not-a-secret"},
       "tts": {"provider": "fixturevoice", "providers": {"fixturevoice": {
           "type": "command", "command": sys.executable + " " + sys.argv[3] + " {output_path} {input_path}"}}}}
(home / "config.yaml").write_text(json.dumps(cfg))

import model_tools  # the host's built-in tools (text_to_speech) are loaded in every real agent session
from hermes_cli.plugins import discover_plugins
discover_plugins()
from tools.registry import registry
from gateway.platform_registry import platform_registry
pkg = platform_registry.get("on_air").adapter_factory.__module__.rsplit(".", 1)[0]
se = importlib.import_module(pkg + ".setup_engine")
sr = importlib.import_module(pkg + ".show_runtime")
sa = importlib.import_module(pkg + ".setup_agent")
out["tools"] = sorted(n for n in sa.SCHEMAS if registry.get_entry(n) is not None)
_mc = getattr(se._MODEL_CLIENT, "_inner", se._MODEL_CLIENT)  # integration: show requests use the onair_show slot
out["model_client"] = type(_mc).__module__ + "." + type(_mc).__name__
out["model_client_wrapper"] = type(se._MODEL_CLIENT).__name__
out["tts_dispatch"] = getattr(se._TTS_DISPATCH, "__qualname__", repr(se._TTS_DISPATCH))
# no genuine owner session here: the registered tool handler must refuse (never a fabricated owner)
out["tool_without_session"] = json.loads(registry.get_entry("onair_show_plan").handler({}))

eng = se.engine()
# the AGENT plans (plugin module instance) ...
plan = eng.plan({"channel": "cozy_fox", "agent_name": "Pixel", "use_model": False, "target": str(home.parent / "stage"),
                 "style": {"tone": "upbeat", "topics": ["desk lamps", "soldering"],
                           "persona": "a cheerful maker fox who loves tiny electronics"}})
assert plan.get("ok"), plan
out["setup_effects"] = plan["effects"]
# ... and the DASHBOARD (its own module instance, as in the separate dashboard process) renders and approves
# exactly that persisted plan. The POST stands in for the owner's click (disclosed).
from fastapi import FastAPI
from fastapi.testclient import TestClient
spec = iu.spec_from_file_location("onair_dashboard_api", home / "plugins" / "on-air" / "dashboard" / "plugin_api.py")
api = iu.module_from_spec(spec); spec.loader.exec_module(api)
app = FastAPI(); app.include_router(api.router, prefix="/api/plugins/on-air")
gui = TestClient(app)
qs = gui.get("/api/plugins/on-air/setup/quick/status").json()
out["gui_pending_plan"] = qs.get("pending_plan")
assert qs["pending_plan"]["digest"] == plan["digest"], qs.get("pending_plan")
applied = gui.post("/api/plugins/on-air/setup/quick/apply", json={"digest": qs["pending_plan"]["digest"], "confirm": True}).json()
assert applied.get("ok"), applied
deadline = time.monotonic() + 60
while time.monotonic() < deadline:
    job = gui.get("/api/plugins/on-air/setup/quick/job").json()["job"]
    if job["state"] not in ("running", "cancelling"):
        break
    time.sleep(0.2)
out["setup_job"] = job["state"]; out["setup_job_approved_by"] = applied["job"].get("kind")
saved_style = gui.get("/api/plugins/on-air/setup/quick/status").json()["saved_stage"]
out["saved_stage"] = {"agent_name": saved_style["agent_name"], "style": saved_style["style"]}
out["gui_cancel"] = gui.post("/api/plugins/on-air/setup/quick/cancel", json={}).json().get("ok")
# the dashboard process has no host model client: it must say so, never fall back to a canned show
out["gui_show_plan_without_model"] = gui.post("/api/plugins/on-air/setup/quick/show/plan", json={"answers": {}}).json()
_gs0 = gui.get("/api/plugins/on-air/setup/quick/show/status").json()
out["gui_show_status_without_model"] = {k: _gs0.get(k) for k in ("running", "start_here", "start_next_action")}

sp = sr.plan(eng, {"brief": "building a desk lamp that dims when chat goes quiet", "policy": "auto", "quiet_seconds": 10})
assert sp.get("ok"), sp
out["show_effects"] = sp["effects"]; out["show_voice"] = sp["voice"]
started = sr.start(eng, sp["digest"], se.owner_consent(sp["digest"], "slash-command"), sp["code"])  # card stand-in
assert started.get("ok"), started
seen = []
def wait(pred, timeout=40):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        st = sr.status(eng)["show"]
        if st.get("activity") not in (seen[-1:] or [None]):
            seen.append(st.get("activity"))
        if pred(st):
            return st
        time.sleep(0.1)
    return sr.status(eng)["show"]
s1 = wait(lambda s: any(l.get("audio") for l in s.get("lines", [])))
out["opening_lines"] = s1.get("lines")
chat = sr.control(eng, "say", {"text": "how does the lamp know chat is quiet?"})
out["chat"] = chat
s2 = wait(lambda s: len([l for l in s.get("lines", []) if l.get("audio")]) >= 2 and s.get("activity") == "listening")
out["after_chat"] = {"lines": s2.get("lines"), "chat": s2.get("chat"), "model": s2.get("model"), "voice": s2.get("voice")}
ignored = sr.control(eng, "say", {"text": "ok"})
s3 = wait(lambda s: any("tin the tip" in l["text"] for l in s.get("lines", [])), timeout=40)  # quiet-time beat
out["quiet_beat_lines"] = s3.get("lines")
# the dashboard (other module instance) consumes the agent-hosted show over its loopback API
gst = gui.get("/api/plugins/on-air/setup/quick/show/status").json()
out["gui_show"] = {"running": gst["running"], "activity": gst["show"]["activity"], "lines": len(gst["show"]["lines"])}
out["gui_say"] = gui.post("/api/plugins/on-air/setup/quick/show/say", json={"text": "hi from the dashboard"}).json()
out["stop"] = gui.post("/api/plugins/on-air/setup/quick/show/stop", json={}).json()
out["after_stop"] = sr.status(eng)["show"]["activity"]
out["resume"] = gui.post("/api/plugins/on-air/setup/quick/show/resume", json={}).json()
out["after_resume"] = sr.status(eng)["show"]["mode"]
out["end"] = sr.control(eng, "end"); out["after_end"] = sr.status(eng)["running"]
out["activities_seen"] = seen
out["prompts"] = PROMPTS
out["refused"] = REFUSED
print("RESULT " + json.dumps(out, default=str))
'''

FIXTURE_TTS = r'''
import struct, sys
n = 4000
data = b"\x00\x00" * n
fmt = struct.pack("<HHIIHH", 1, 1, 8000, 16000, 2, 16)
wav = b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVE" + b"fmt " + struct.pack("<I", 16) + fmt + b"data" + struct.pack("<I", len(data)) + data
open(sys.argv[1], "wb").write(wav)
'''


def test_private_show_runs_through_installed_plugin_real_llm_and_tts_on_py314(tmp_path):
    if not PY314.is_file() or not (SDK / "hermes_cli").is_dir():
        pytest.skip("the prepared Hermes Python 3.14 environment is not on this machine")
    home = tmp_path / "owner"
    plugin = home / "plugins" / "on-air"
    shutil.copytree(ROOT / "onair", plugin / "onair", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for name in ("__init__.py", "plugin.yaml"):
        shutil.copy(ROOT / name, plugin / name)
    shutil.copytree(ROOT / "dashboard", plugin / "dashboard", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    (tmp_path / "user").mkdir()
    probe, tts = tmp_path / "probe.py", tmp_path / "fixture_tts.py"
    probe.write_text(textwrap.dedent(PROBE))
    tts.write_text(FIXTURE_TTS)
    version = subprocess.run([str(PY314), "-B", "-c", "from hermes_cli.config_defaults import DEFAULT_CONFIG as D;"
                              "print(D['_config_version'])"], cwd=SDK, capture_output=True, text=True,
                             env={"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(SDK)}).stdout.strip()
    env = {"PATH": os.environ.get("PATH", ""), "HOME": str(tmp_path / "user"), "HERMES_HOME": str(home),
           "PYTHONPATH": str(SDK), "PYTHONDONTWRITEBYTECODE": "1", "HERMES_DISABLE_LAZY_INSTALLS": "1",
           "TMPDIR": str(tmp_path), "HERMES_GATEWAY_PROFILE_PIN": "1"}
    proc = subprocess.run([str(PY314), "-B", str(probe), str(ROOT), version, str(tts)], env=env, cwd=tmp_path,
                          capture_output=True, text=True, timeout=240)
    lines = [ln for ln in proc.stdout.splitlines() if ln.startswith("RESULT ")]
    assert proc.returncode == 0 and lines, proc.stdout[-3000:] + proc.stderr[-6000:]
    out = json.loads(lines[-1][len("RESULT "):])
    print(json.dumps(out, indent=1)[:12000])
    assert out["python"].startswith("3.14") and out["ruamel"] and not out["pyyaml"], out  # the supported env
    assert {"onair_show_plan", "onair_show_status", "onair_show_chat", "onair_show_safety"} <= set(out["tools"])
    assert out["model_client"] == "agent.plugin_llm.PluginLlm", out["model_client"]
    assert out["model_client_wrapper"] == "_ShowModel", out
    assert out["tool_without_session"].get("refused") is True
    assert out["setup_job"] == "done"
    pending = out["gui_pending_plan"]  # the agent's exact plan, as the dashboard reads it
    assert pending["answers"]["agent_name"] == "Pixel" and pending["answers"]["style"]["topics"] == ["desk lamps", "soldering"]
    assert out["saved_stage"]["agent_name"] == "Pixel", out["saved_stage"]
    assert out["saved_stage"]["style"]["topics"] == ["desk lamps", "soldering"] and out["saved_stage"]["style"]["tone"] == "upbeat"
    assert out["gui_cancel"] is True
    no_model = out["gui_show_plan_without_model"]
    assert no_model["ok"] is False and no_model["missing"] == ["model"] and "canned" in no_model["error"], no_model
    # the show's own next step: agent chat, never the setup preview's canned stand-in offer
    assert "canned" not in no_model["next_action"].lower() and "agent chat" in no_model["next_action"], no_model
    gs0 = out["gui_show_status_without_model"]
    assert gs0["running"] is False and gs0["start_here"] is False, gs0
    assert "agent chat" in gs0["start_next_action"] and "canned" not in gs0["start_next_action"].lower(), gs0
    assert out["gui_show"]["running"] is True and out["gui_show"]["lines"] >= 2, out["gui_show"]
    assert out["gui_say"]["ok"] is True, out["gui_say"]
    gate = out["gui_say"]["chat"]  # selective: the real gate decides; a set-aside message says so plainly
    assert gate["answer"] == "considering" if gate["verdict"] in ("worthy", "routine") else gate["answer"].startswith("not answered"), gate
    assert out["show_voice"]["usable_here"] is True
    opening = [ln for ln in out["opening_lines"] if "Pixel" in ln["text"]]
    assert opening and opening[0]["audio"] is True, out["opening_lines"]  # model-authored opening, spoken
    assert out["chat"]["ok"] and out["chat"]["chat"]["verdict"] != "drop", out["chat"]
    answered = [ln for ln in out["after_chat"]["lines"] if "lamp listens" in ln["text"]]
    assert answered and answered[0]["audio"], out["after_chat"]
    assert out["after_chat"]["chat"][0]["answer"] == "answered"
    assert any("tin the tip" in ln["text"] for ln in out["quiet_beat_lines"]), out["quiet_beat_lines"]
    assert {"thinking", "speaking", "listening"} <= set(out["activities_seen"]), out["activities_seen"]
    assert out["stop"]["ok"] and out["after_stop"] == "paused", out["stop"]
    assert out["resume"]["ok"] and out["after_resume"] == "autonomous" and out["after_end"] is False
    prompts = out["prompts"]
    assert prompts and all(p["system_has_persona"] and not p["tools"] for p in prompts), prompts
    assert any("<show_note" in p["user"] for p in prompts) and any("<live_chat" in p["user"] for p in prompts)
    assert not out["refused"], out["refused"]
