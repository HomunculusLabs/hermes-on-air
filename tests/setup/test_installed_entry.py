"""The installed plugin through the REAL Hermes loader, not FakeCtx.

Each check copies the plugin into a throwaway HERMES_HOME and runs ``hermes_cli.plugins.discover_plugins()``
in a clean subprocess (no repo on ``sys.path``), then reads the host's own registries:

* owner home: the six setup tools (toolset ``onair_setup``), ``/onair-setup`` and the ``on-air:setup`` skill
  are registered; the test also records whether the CLI platform offers that toolset by default.
* stage home made by the setup engine: none of them exist, and the real adapter's settings path
  (platform extra -> plugin settings -> env) builds a StageController whose ``cfg.select`` and
  ``cfg.never_say`` carry the saved ``reply_pacing`` / ``avoid_topics``. ``_build`` is called directly
  (not ``connect()``) so no overlay/control port is bound and no Twitch reader starts.
* the same saved stage through the real ``connect()`` / ``disconnect()`` (with and without the stage's own
  .env loaded as the gateway loads it), and the full ARMED chain: arm passes the unmodified preflight, a
  faked Twitch message, the real agent loop + runtime gate against a loopback model, the real guard holding
  the draft (or blocking never_say), desk approval and a subscribed overlay receiving the caption.
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
import yaml

from onair import setup_engine as se
from onair.setup_agent import SCHEMAS

from test_quick_setup import FakeCtx, owner, wait_done  # noqa: F401

ROOT = Path(__file__).resolve().parents[2]
EXPECTED_TOOLS = set(SCHEMAS)


def _install_copy(home: Path) -> None:
    plugin_dir = home / "plugins" / "on-air"
    if plugin_dir.exists():
        shutil.rmtree(plugin_dir)
    shutil.copytree(ROOT / "onair", plugin_dir / "onair", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for name in ("__init__.py", "plugin.yaml"):
        shutil.copy(ROOT / name, plugin_dir / name)


def _run(tmp_path: Path, home: Path, body: str, prelude: str = "") -> dict:
    keep = [p for p in os.environ.get("PYTHONPATH", "").split(os.pathsep) if p and Path(p).resolve() != ROOT]
    env = dict(os.environ, HERMES_HOME=str(home), HOME=str(tmp_path / "user"), PYTHONDONTWRITEBYTECODE="1",
               HERMES_DISABLE_LAZY_INSTALLS="1", PYTHONPATH=os.pathsep.join(keep))
    (tmp_path / "user").mkdir(exist_ok=True)
    script = tmp_path / "probe.py"
    script.write_text(textwrap.dedent("""
        import importlib, json, sys
        from pathlib import Path
        REPO = Path(sys.argv[1]).resolve()
        sys.path[:] = [p for p in sys.path if Path(p or ".").resolve() != REPO]
    """) + textwrap.dedent(prelude) + textwrap.dedent("""
        from hermes_cli.plugins import discover_plugins, get_plugin_commands, get_plugin_manager
        discover_plugins()
        from tools.registry import registry
        from gateway.platform_registry import platform_registry
        entry = platform_registry.get("on_air")
        assert entry is not None, "On Air platform not registered"
        pkg = entry.adapter_factory.__module__.rsplit(".", 1)[0]
        assert pkg.split(".")[0] != "onair", pkg
        sa = importlib.import_module(pkg + ".setup_agent")
        names = sorted(n for n in sa.SCHEMAS if registry.get_entry(n) is not None)
        out = {"pkg": pkg, "toolset": sa.TOOLSET, "tools": names,
               "toolsets": sorted({registry.get_entry(n).toolset for n in names}),
               "commands": sorted(n for n in get_plugin_commands() if "onair" in n),
               "skills": get_plugin_manager().list_plugin_skills("on-air")}
    """) + textwrap.dedent(body) + "\nprint('RESULT ' + json.dumps(out))\n")
    proc = subprocess.run([sys.executable, "-B", str(script), str(ROOT)], env=env, cwd=tmp_path,
                          capture_output=True, text=True, timeout=300)
    lines = [ln for ln in proc.stdout.splitlines() if ln.startswith("RESULT ")]
    assert proc.returncode == 0 and lines, proc.stdout[-2000:] + proc.stderr[-4000:]
    return json.loads(lines[-1][len("RESULT "):])


def test_owner_home_registers_the_setup_entry_in_the_real_registries(tmp_path):
    pytest.importorskip("hermes_cli.plugins")
    from hermes_cli.config_defaults import DEFAULT_CONFIG

    home = tmp_path / "owner"
    _install_copy(home)
    (home / "config.yaml").write_text(json.dumps({"_config_version": DEFAULT_CONFIG["_config_version"],
                                                  "plugins": {"enabled": ["on-air"]}}))
    out = _run(tmp_path, home, """
        import yaml
        from hermes_cli.tools_config import _get_platform_tools
        cfg = yaml.safe_load((Path(sys.argv[0]).parent / "owner" / "config.yaml").read_text())
        out["cli_tools_default"] = sorted(_get_platform_tools(cfg, "cli"))
    """)
    assert out["tools"] == sorted(EXPECTED_TOOLS) and len(EXPECTED_TOOLS) == 15, out  # 6 setup + 5 real stage + 4 private show
    assert out["toolsets"] == [out["toolset"]] == ["onair_setup"], out
    # the integrated entry also registers the presentation slice's owner command (tests/test_integration_join.py)
    assert out["commands"] == ["onair-broadcast", "onair-setup", "onair-stage"], out
    assert out["skills"] == ["avatar", "first-stream", "host", "produce", "scene", "scene-build", "seed-bank", "setup", "show-day", "troubleshoot", "views"], out
    # Record (not assume) whether a CLI session gets the toolset without extra enablement.
    offered = out["toolset"] in out["cli_tools_default"]
    print("cli_default_offers_onair_setup=" + json.dumps(offered))
    assert offered, "documented: the onair_setup toolset is offered to CLI sessions by default once the plugin is enabled"


def test_manifest_declares_exactly_what_register_records(tmp_path):
    """The SDK's own admission check (``validate_plugin_dir``: capability probe + declared tools/hooks/
    middleware) on an installed copy, with the prepared interpreter; no deps, no bootstrap."""
    pytest.importorskip("hermes_cli.plugin_validate")
    from hermes_cli.config_defaults import DEFAULT_CONFIG

    home = tmp_path / "owner"
    _install_copy(home)
    (home / "config.yaml").write_text(json.dumps({"_config_version": DEFAULT_CONFIG["_config_version"],
                                                  "plugins": {"enabled": ["on-air"]}}))
    out = _run(tmp_path, home, """
        from hermes_cli.plugin_validate import validate_plugin_dir
        report = validate_plugin_dir(Path(sys.argv[0]).parent / "owner" / "plugins" / "on-air")
        out["checks"] = [list(c) for c in report.checks]
        out["warnings"] = list(report.warnings)
    """)
    checks = {name: (ok, detail) for name, ok, detail in out["checks"]}
    print(json.dumps(out["checks"]))
    for kind in ("tools", "hooks", "middleware"):
        assert checks.get("declared " + kind, (False, "missing"))[0] is True, (kind, out)
    assert not [w for w in out["warnings"] if "provides_tools" in w], out["warnings"]


def test_stage_home_registers_no_setup_entry_and_the_real_adapter_consumes_saved_style(owner, tmp_path, monkeypatch):
    pytest.importorskip("hermes_cli.plugins")
    monkeypatch.setattr(se, "_ENGINES", {})
    eng = se.SetupEngine(owner)
    plan = eng.plan({"channel": "cozy_fox", "target": os.fspath(tmp_path / "stage"),
                     "style": {"reply_pacing": "less_often", "avoid_topics": ["politics", "crypto"]}})
    assert plan.get("digest"), plan
    eng.apply(plan["digest"], se.owner_consent(plan["digest"], "dashboard"))
    home = Path(wait_done(eng)["home"])
    eng.stop_preview()
    cfg = yaml.safe_load((home / "config.yaml").read_text())
    saved = cfg["plugins"]["entries"]["on-air"]["settings"]
    assert saved["reply_pacing"] == "less_often" and {"politics", "crypto"} <= set(saved["never_say"])
    # the real gateway passes the stage's platform block; it carries no style keys that could shadow these
    platform_block = (cfg.get("gateway") or {}).get("platforms", {}).get("on_air") or {}
    assert not ({"reply_pacing", "never_say"} & set(platform_block.get("extra") or {})), platform_block
    _install_copy(home)
    data = tmp_path / "stage-data"
    out = _run(tmp_path, home, f"""
        from gateway.config import PlatformConfig
        adapter = entry.adapter_factory(PlatformConfig(enabled=True, extra={{}}))
        s = adapter._build(Path({str(data)!r}))  # builds the controller; starts no server and no chat reader
        c = adapter.controller.cfg
        out["settings_pacing"] = s["reply_pacing"]
        out["select"] = [c.select.max_worthy_per_turn, c.select.routine_gap_s]
        out["never_say"] = list(c.never_say)
        out["overlay_started"] = bool(getattr(adapter, "_overlay_started", False))
    """)
    assert out["tools"] == [] and out["commands"] == [] and out["skills"] == [], out
    assert out["settings_pacing"] == "less_often", out
    assert out["select"] == [1, 300.0], out  # style.PACING["less_often"]
    assert {"politics", "crypto"} <= set(out["never_say"]), out
    assert out["overlay_started"] is False


# The real gateway's startup resolver for THIS stage home's own generated, nonsecret .env
# (gateway/run.py: load_hermes_dotenv(hermes_home=...)). Deliberately WITHOUT the SDK checkout's project
# .env and without external secret sources: nothing from the owner/default home or any credential store.
STAGE_ENV_PRELUDE = """
import os
from hermes_cli.env_loader import load_hermes_dotenv
LOADED_ENV = [str(p) for p in load_hermes_dotenv(hermes_home=os.environ["HERMES_HOME"], load_external_secrets=False)]
"""

_FIXTURE_TWITCH = '''
# Only the reader CLASS is replaced, on the REAL module: normalize_channel and the rest stay real (preflight
# validates the channel with them). A stand-in module without them made preflight fail closed with "could not
# safely inspect the configuration" (run 1641 diagnosis: ImportError inside preflight, a fixture defect).
_fx = importlib.import_module(pkg + ".sources.twitch_irc")
SEEN = {"started": 0, "stopped": 0}

class FixtureTwitchChatSource:
    """DISCLOSED FIXTURE for the external Twitch edge only: no network. It is what the adapter's own dynamic
    ``from .sources.twitch_irc import TwitchChatSource`` receives; everything else is the real plugin."""
    def __init__(self, channel, on_message, *, on_moderation=None, on_support=None, on_ignored_notice=None):
        SEEN.update(channel=channel, on_message=on_message, on_moderation=on_moderation, on_support=on_support)
    def start(self):
        SEEN["started"] += 1
    async def stop(self):
        SEEN["stopped"] += 1

_fx.TwitchChatSource = FixtureTwitchChatSource
'''


@pytest.mark.parametrize("stage_env", [False, True])
def test_saved_stage_real_adapter_connect_and_disconnect_with_only_twitch_faked(owner, tmp_path, monkeypatch, stage_env):
    """``stage_env``: True loads the stage home's own generated nonsecret .env the way the gateway starts
    (``load_hermes_dotenv``), so arming passes the unmodified preflight; False is the retained negative control
    (preflight refuses with exactly the allowed_users reason). Parent finding A: the SAVED stage, discovered by the real ``discover_plugins()``, goes through the real
    adapter ``connect()`` / ``disconnect()``: real overlay + desk-control servers on loopback, the saved
    selector and never_say in the live controller, the chat reader bound to that controller, chat consumed
    only through the gate (off air = nothing taken), and everything released on disconnect. Only the Twitch
    reader is a disclosed fixture; connecting needs no model, so no model edge is touched."""
    pytest.importorskip("hermes_cli.plugins")
    import socket
    monkeypatch.setattr(se, "_ENGINES", {})
    eng = se.SetupEngine(owner)
    plan = eng.plan({"channel": "cozy_fox", "target": os.fspath(tmp_path / "stage"),
                     "style": {"reply_pacing": "less_often", "avoid_topics": ["politics", "crypto"]}})
    eng.apply(plan["digest"], se.owner_consent(plan["digest"], "dashboard"))
    home = Path(wait_done(eng)["home"])
    eng.stop_preview()
    real = eng.status()["real_stage"]  # the remaining real-show steps themselves, readable by the agent
    assert real["set_up"] is False and real["steps"], real
    assert all(s["title"] and (s["command"] or s["template"] or s["note"]) for s in real["steps"]), real
    # preflight + gateway are the agent's approved stage start (test_stage_runtime.py); the rest carry the
    # precise reason the agent does not do them (no plain checklist)
    assert all((s["agent_can_do"] is True and "/onair-setup stage approve" in s["agent_action"])
               if s["key"] in ("preflight", "gateway") else
               (s["agent_can_do"] is False and s["why_not_here"] != "Not a step this setup performs.")
               for s in real["steps"]), real
    assert [s["key"] for s in real["steps"]] == ["plugin", "model", "preflight", "gateway", "desk"], real
    _install_copy(home)
    with socket.socket() as s:  # a free loopback port instead of the stage's OBS port (gateway extra)
        s.bind(("127.0.0.1", 0))
        free = s.getsockname()[1]
    prelude = STAGE_ENV_PRELUDE if stage_env else ""
    out = _run(tmp_path, home, _FIXTURE_TWITCH + f"""
import asyncio, http.client, os, socket, time
from urllib.parse import urlparse
from gateway.config import PlatformConfig
TypesMod = importlib.import_module(pkg + ".types")

def listening(url):
    port = urlparse(url).port
    with socket.socket() as so:
        so.settimeout(0.5)
        return bool(port) and so.connect_ex(("127.0.0.1", port)) == 0

def desk(adapter, method, path, body=None):
    u = urlparse(adapter.control.url)
    conn = http.client.HTTPConnection("127.0.0.1", u.port, timeout=5)
    conn.request(method, path, json.dumps(body).encode() if body is not None else None,
                 {{"Authorization": "Bearer " + adapter.control.token, "Content-Type": "application/json"}})
    r = conn.getresponse()
    data = json.loads(r.read() or b"{{}}")
    conn.close()
    return r.status, data

async def main():
    adapter = entry.adapter_factory(PlatformConfig(enabled=True, extra={{"overlay_port": {free}}}))
    out["connected"] = await adapter.connect()
    c = adapter.controller
    out["preflight_failed"] = [[k.get("name"), k.get("detail")] for k in (c.preflight or {{}}).get("checks", [])
                               if k.get("status") == "fail"]
    out["env_allowed"] = os.environ.get("ONAIR_ALLOWED_USERS")
    out["select"] = [c.cfg.select.max_worthy_per_turn, c.cfg.select.routine_gap_s]
    out["never_say"] = list(c.cfg.never_say)
    out["channel"] = SEEN.get("channel")
    out["reader_bound"] = [getattr(SEEN.get(k), "__self__", None) is c for k in ("on_message", "on_moderation", "on_support")]
    out["reader_started"] = SEEN["started"]
    out["overlay_url"], out["control_url"] = adapter.overlay.overlay_url, adapter.control.url
    out["listening"] = [listening(adapter.overlay.overlay_url), listening(adapter.control.url)]
    out["control_json"] = sorted(p.name for p in data_dir_fn().iterdir()) if data_dir_fn else []
    st, state = await asyncio.to_thread(desk, adapter, "GET", "/state")
    out["desk_state"] = [st, (state.get("state") or state).get("mode")]
    msg = TypesMod.ChatMsg(platform="twitch", channel="cozy_fox", msg_id="m1", user_id="u1",
                           display_name="viewer_one", text="hi! how is the stream going today?", ts=time.time())
    verdict = SEEN["on_message"](msg)  # what the Twitch reader would hand over
    out["off_air_verdict"] = getattr(verdict, "kind", None)
    out["off_air_waiting"] = c.snapshot()["chat_waiting"]
    st, armed = await asyncio.to_thread(desk, adapter, "POST", "/mode", {{"mode": "approval"}})
    out["arm"] = [st, (armed.get("state") or {{}}).get("mode"), armed.get("error", "")]
    if st == 200:
        msg2 = TypesMod.ChatMsg(platform="twitch", channel="cozy_fox", msg_id="m2", user_id="u2",
                                display_name="viewer_two", text="what game is this?", ts=time.time())
        SEEN["on_message"](msg2)
        out["armed_waiting"] = c.snapshot()["chat_waiting"]
    await adapter.disconnect()
    out["reader_stopped"] = SEEN["stopped"]
    out["after"] = [listening(out["overlay_url"]), listening(out["control_url"])]

adapter_mod = importlib.import_module(pkg + ".adapter")
data_dir_fn = getattr(adapter_mod, "data_dir", None)
asyncio.run(main())
""", prelude=prelude)

    if os.environ.get("ONAIR_EVIDENCE_DIR"):
        (Path(os.environ["ONAIR_EVIDENCE_DIR"]) / "saved-stage-connect.json").write_text(json.dumps(out, indent=1))
    assert out["tools"] == [] and out["commands"] == [], out  # stage home: no setup entry
    assert out["connected"] is True, out
    assert out["select"] == [1, 300.0] and {"politics", "crypto"} <= set(out["never_say"]), out
    assert out["channel"] == "cozy_fox" and out["reader_bound"] == [True, True, True] and out["reader_started"] == 1
    assert out["listening"] == [True, True] and str(free) in out["overlay_url"], out
    assert "control.json" in out["control_json"], out  # the real desk can discover the real stage
    assert out["desk_state"] == [200, "off_air"], out  # connects off air: nothing is read until armed
    assert out["off_air_waiting"] == 0, out  # gate: chat is not taken in while off air
    if stage_env:  # started the way the gateway starts it: arming passes the unmodified preflight
        assert out["env_allowed"] == "onair-stage" and out["preflight_failed"] == [], out
        assert out["arm"] == [200, "approval", ""] and out["armed_waiting"] >= 1, out
    else:  # retained NEGATIVE control: without the stage's own .env, preflight refuses to arm, exactly why
        assert out["env_allowed"] is None, out
        assert out["preflight_failed"] == [["allowed_users", "ONAIR_ALLOWED_USERS must be exactly onair-stage."]], out
        assert out["arm"][0] == 409 and "ONAIR_ALLOWED_USERS" in out["arm"][2], out
    assert out["reader_stopped"] == 1 and out["after"] == [False, False], out


_ARMED_CHAIN = '''
import asyncio, http.client, socket, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse
from gateway.config import PlatformConfig
TypesMod = importlib.import_module(pkg + ".types")
CASE = "__CASE__"
CLEAN = "Thanks for asking, viewer two! The stream is going really well today."
BLOCKED = "Honestly the best thing to talk about today is crypto."

_real_connect = socket.socket.connect
REFUSED = []
def _loopback_only(self, address):  # no genuine provider/Twitch/network: only loopback fixtures answer
    host = address[0] if isinstance(address, tuple) else ""
    if host not in ("127.0.0.1", "::1", "localhost") and not isinstance(address, str):
        REFUSED.append(str(host))
        raise OSError("non-loopback connections are disabled in this fixture")
    return _real_connect(self, address)
socket.socket.connect = _loopback_only

class LoopbackModel(BaseHTTPRequestHandler):
    """DISCLOSED FIXTURE for the external model edge only: an OpenAI-compatible loopback server."""
    def log_message(self, *a):
        pass
    def _json(self, payload):
        body = json.dumps(payload).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
    def do_GET(self):
        self._json({"object": "list", "data": [{"id": "fixture-model", "object": "model"}]})
    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode("utf-8", "replace")
        if not self.path.endswith("/chat/completions"):
            return self._json({})
        req = json.loads(raw or "{}")
        self.server.requests.append({"tools": bool(req.get("tools")), "stream": bool(req.get("stream"))})
        reply = BLOCKED if CASE == "never_say" else CLEAN
        if req.get("stream"):
            self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.end_headers()
            for delta, finish in (({"role": "assistant", "content": reply}, None), ({}, "stop")):
                chunk = {"id": "c1", "object": "chat.completion.chunk", "created": 1, "model": "fixture-model",
                         "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
                self.wfile.write(b"data: " + json.dumps(chunk).encode() + b"\\n\\n")
            self.wfile.write(b"data: [DONE]\\n\\n")
            return
        self._json({"id": "c1", "object": "chat.completion", "created": 1, "model": "fixture-model",
                    "choices": [{"index": 0, "message": {"role": "assistant", "content": reply}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})

model = ThreadingHTTPServer(("127.0.0.1", 0), LoopbackModel)
model.requests = []
threading.Thread(target=model.serve_forever, daemon=True).start()
BASE_URL = "http://127.0.0.1:%d/v1" % model.server_address[1]

def listening(url):
    port = urlparse(url).port
    with socket.socket() as so:
        so.settimeout(0.5)
        return bool(port) and so.connect_ex(("127.0.0.1", port)) == 0

def desk(adapter, method, path, body=None):
    u = urlparse(adapter.control.url)
    conn = http.client.HTTPConnection("127.0.0.1", u.port, timeout=5)
    conn.request(method, path, json.dumps(body).encode() if body is not None else None,
                 {"Authorization": "Bearer " + adapter.control.token, "Content-Type": "application/json"})
    r = conn.getresponse(); data = json.loads(r.read() or b"{}"); conn.close()
    return r.status, data

class OverlayClient(threading.Thread):
    """A real overlay subscriber (the SSE stream OBS's browser source reads), connected BEFORE arming."""
    def __init__(self, url):
        super().__init__(daemon=True)
        self.port = urlparse(url).port
        self.events = []
        self.subscribed = threading.Event()
    def run(self):  # off the event loop: the overlay server runs on it
        with __import__("contextlib").suppress(Exception):
            conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=90)
            conn.request("GET", "/events")
            resp = conn.getresponse()
            while True:
                line = resp.fp.readline()
                if not line:
                    return
                if line.startswith(b"data: "):
                    self.events.append(json.loads(line[6:]))
                    self.subscribed.set()  # a new subscriber first receives the current state

def run_agent(prompt):
    """The gateway's agent loop for this platform (as tests/cli/_agent_loop_driver.py runs it): a real
    AIAgent on the stage's own on_air toolsets, so the plugin's llm_execution gate sees the request."""
    from hermes_cli.config import load_config
    from hermes_cli.tools_config import _get_platform_tools
    from run_agent import AIAgent
    toolsets = sorted(_get_platform_tools(load_config(), "on_air"))
    out.setdefault("toolsets", toolsets)
    agent = AIAgent(api_key="fixture-not-a-secret", base_url=BASE_URL, provider="custom", model="fixture-model",
                    platform="on_air", enabled_toolsets=toolsets, quiet_mode=True, skip_context_files=True,
                    skip_memory=True, max_iterations=2)
    try:
        return agent.run_conversation(prompt).get("final_response")
    finally:
        agent.close()

async def main():
    adapter = entry.adapter_factory(PlatformConfig(enabled=True, extra={"overlay_port": __FREE__}))
    out["connected"] = await adapter.connect()
    c = adapter.controller
    out["preflight_failed"] = [k.get("name") for k in (c.preflight or {}).get("checks", []) if k.get("status") == "fail"]
    out["select"] = [c.cfg.select.max_worthy_per_turn, c.cfg.select.routine_gap_s]
    out["never_say"] = list(c.cfg.never_say)
    out["overlay_url"], out["control_url"] = adapter.overlay.overlay_url, adapter.control.url
    out["listening"] = [listening(adapter.overlay.overlay_url), listening(adapter.control.url)]
    viewer = OverlayClient(adapter.overlay.overlay_url); viewer.start()
    for _ in range(200):
        if viewer.subscribed.is_set():
            break
        await asyncio.sleep(0.05)
    out["overlay_subscribed_before_arm"] = viewer.subscribed.is_set()

    async def handler(event):
        # Match the gateway worker: copy the turn's context into the executor.
        from contextvars import copy_context
        return await asyncio.get_running_loop().run_in_executor(None, copy_context().run, run_agent, event.text)
    adapter.set_message_handler(handler)

    st, armed = await asyncio.to_thread(desk, adapter, "POST", "/mode", {"mode": "approval"})
    out["arm"] = [st, (armed.get("state") or {}).get("mode"), armed.get("error", "")]
    msg = TypesMod.ChatMsg(platform="twitch", channel="cozy_fox", msg_id="m1", user_id="u1",
                           display_name="viewer_two", text="hi! how is the stream going today?", ts=time.time(),
                           first_msg=True)
    out["ingest"] = getattr(SEEN["on_message"](msg), "kind", None)  # delivered by the faked Twitch reader
    deadline = time.monotonic() + 60
    turn_id = None
    while time.monotonic() < deadline and turn_id is None:
        turn = c.turn or (c.tick() if c.can_start_turn() else None)
        turn_id = getattr(turn, "turn_id", None)
        await asyncio.sleep(0.05)
    out["turn"] = bool(turn_id)
    while time.monotonic() < deadline and not c.pending_lines() and (c.turn is not None):
        await asyncio.sleep(0.05)
    await asyncio.sleep(0.3)
    pending = c.pending_lines()
    out["pending"] = [p.get("text") for p in pending]
    out["captions_before_approval"] = [e.get("text") for e in viewer.events if e.get("type") == "caption"]
    out["approve"] = []
    for line in pending:  # the trusted fixture owner approves each held line through the desk API, in order
        st, res = await asyncio.to_thread(desk, adapter, "POST", "/approve", {"line_id": line["line_id"], "epoch": line["epoch"]})
        out["approve"].append(st)
        while time.monotonic() < deadline and not any(e.get("type") == "caption" and e.get("text") == line["text"]
                                                      for e in viewer.events):
            await asyncio.sleep(0.05)
    out["captions"] = [e.get("text") for e in viewer.events if e.get("type") == "caption"]
    out["line_rules"] = [r.get("rule") or r.get("disposition") for r in c.receipts.tail(500)
                         if r.get("kind") == "line" and r.get("turn_id") == turn_id]
    out["model_requests"] = model.requests
    out["non_loopback_refused"] = sorted(set(REFUSED))
    out["gate_passed"] = importlib.import_module(pkg + ".runtime_guard").plugin_guard().passed()
    out["gate_violation"] = importlib.import_module(pkg + ".runtime_guard").plugin_guard().violation()
    await adapter.disconnect()
    out["reader_stopped"] = SEEN["stopped"]
    out["after"] = [listening(out["overlay_url"]), listening(out["control_url"])]
    model.shutdown(); model.server_close()

asyncio.run(main())
'''


@pytest.mark.parametrize("case", ["clean", "never_say"])
def test_saved_stage_armed_chain_through_real_preflight_guard_and_overlay(owner, tmp_path, monkeypatch, case):
    """Parent finding A, the armed consuming leg, UNCONDITIONALLY: the engine-created stage home, started the way
    the gateway starts it (its own generated nonsecret .env through the SDK's startup resolver), discovered by
    the real ``discover_plugins()`` and connected by the real adapter; arming passes the UNMODIFIED preflight;
    the faked Twitch reader hands over a viewer message; the real agent loop asks a loopback model fixture; the
    real guard holds the safe draft for approval (or blocks a never_say reply); the trusted fixture owner
    approves through the token-authenticated desk API; a real overlay subscriber receives the caption; disconnect
    closes every port. Faked: TwitchChatSource and the provider (loopback). Fixture-completed: none of the
    stage's own config/env is edited; nothing is read from the owner's/default home or any credential store."""
    pytest.importorskip("run_agent")
    import socket
    monkeypatch.setattr(se, "_ENGINES", {})
    eng = se.SetupEngine(owner)
    plan = eng.plan({"channel": "cozy_fox", "target": os.fspath(tmp_path / "stage"),
                     "style": {"reply_pacing": "less_often", "avoid_topics": ["politics", "crypto"]}})
    eng.apply(plan["digest"], se.owner_consent(plan["digest"], "dashboard"))
    home = Path(wait_done(eng)["home"])
    eng.stop_preview()
    before = {p.name: p.read_bytes() for p in home.iterdir() if p.is_file()}
    _install_copy(home)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        free = s.getsockname()[1]
    out = _run(tmp_path, home, _FIXTURE_TWITCH + _ARMED_CHAIN.replace("__CASE__", case).replace("__FREE__", str(free)),
               prelude=STAGE_ENV_PRELUDE)
    if os.environ.get("ONAIR_EVIDENCE_DIR"):
        (Path(os.environ["ONAIR_EVIDENCE_DIR"]) / f"armed-chain-{case}.json").write_text(json.dumps(out, indent=1))
    print("CHAIN " + json.dumps({k: out.get(k) for k in ("arm", "turn", "pending", "captions", "line_rules",
                                                           "model_requests", "gate_passed", "toolsets",
                                                           "non_loopback_refused")}))
    after = {p.name: p.read_bytes() for p in home.iterdir() if p.is_file()}
    assert {k: v for k, v in after.items() if k in before} == before  # the stage's config/.env/SOUL untouched
    assert out["tools"] == [] and out["commands"] == [], out  # stage home: no setup entry
    assert out["connected"] is True and out["preflight_failed"] == [], out
    assert out["arm"] == [200, "approval", ""], out  # arming PASSES the unmodified preflight
    assert out["select"] == [1, 300.0] and {"politics", "crypto"} <= set(out["never_say"]), out
    assert out["listening"] == [True, True] and str(free) in out["overlay_url"], out
    assert out["turn"] is True, out
    assert len(out["model_requests"]) >= 1 and not any(r["tools"] for r in out["model_requests"]), out
    assert out["gate_passed"] >= 1 and out["gate_violation"] is None, out  # the runtime gate saw the request
    assert out["overlay_subscribed_before_arm"] is True, out
    assert out["captions_before_approval"] == [], out  # nothing airs before the owner approves
    if case == "clean":
        assert out["pending"] == CLEAN_LINES, out  # every line held for approval by the real guard
        assert out["approve"] == [200] * len(CLEAN_LINES), out
        assert out["captions"] == CLEAN_LINES, out  # the subscribed overlay received exactly the approved captions
    else:
        assert out["pending"] == [] and out["captions"] == [], out
        assert "never_say" in out["line_rules"], out  # blocked by the saved avoid_topics -> never_say
    assert out["reader_stopped"] == 1 and out["after"] == [False, False], out


CLEAN_LINES = ["Thanks for asking, viewer two!", "The stream is going really well today."]  # the stage splits lines
