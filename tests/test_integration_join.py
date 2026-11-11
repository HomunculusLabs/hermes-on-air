"""The integrated plugin: setup/show (cohost) + presentation/OBS output, registered by ONE real entry.

One clean subprocess per home, run with the prepared Hermes 3.14 interpreter (ruamel.yaml, no PyYAML),
loads a drop-in copy of the plugin through the real Hermes loader (``discover_plugins``) and checks:

* an owner home registers the setup/show tools, the nine presentation tools (incl. ``onair_work_share``),
  ``/onair-setup``, ``/onair-present`` and ``/onair-broadcast``, and every ``provides_tools`` entry of plugin.yaml is actually registered;
* the ``on-air:setup`` and ``on-air:produce`` skills are registered by the plugin and the host's own skill
  viewer serves ``on-air:produce`` with exactly the source file's bytes;
* ``presentation.teardown`` is wired to the host's real unload (``on_unload``): unloading closes the page port;
* a presentation tool called with no owner session refuses (no fabricated owner);
* the overlay seam: with no configured overlay, the OBS overlay source follows the RUNNING private show's
  own captions+voice page (a disclosed loopback stand-in answers the show's /facts route);
* a stage home (the public co-host) registers none of the private tools or commands.

No provider, TTS, OBS or browser is contacted. This is a drop-in copy, NOT a plugin-manager install witness.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
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
import json, os, socket, sys, importlib, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import importlib.util as iu
REPO = Path(sys.argv[1]).resolve()
sys.path[:] = [p for p in sys.path if Path(p or ".").resolve() != REPO]
out = {"python": sys.version.split()[0], "pyyaml": bool(iu.find_spec("yaml"))}
home = Path(os.environ["HERMES_HOME"])
cfg = {"_config_version": int(sys.argv[2]), "plugins": {"enabled": ["on-air"]}}
if sys.argv[3] in ("voice-disabled", "voice-disabled-legacy"):
    cfg["plugins"]["entries"] = {"on-air": {"config" if sys.argv[3] == "voice-disabled-legacy" else "settings": {"voice": False}}}
(home / "config.yaml").write_text(json.dumps(cfg))
import model_tools  # noqa: F401  (the host's built-in tools, as in a real session)
from hermes_cli.plugins import discover_plugins, get_plugin_manager
discover_plugins()
from tools.registry import registry
mgr = get_plugin_manager()
cmds = sorted(getattr(mgr, "_plugin_commands", {}) or {})
out["commands"] = [c for c in cmds if c.startswith("onair")]
out["aux_tasks"] = sorted(k for k in (getattr(mgr, "_aux_tasks", {}) or {}) if k.startswith("onair"))
from ruamel.yaml import YAML
manifest = YAML(typ="safe").load((home / "plugins" / "on-air" / "plugin.yaml").read_text())
out["manifest_tools"] = manifest["provides_tools"]
out["registered"] = sorted(n for n in manifest["provides_tools"] if registry.get_entry(n) is not None)
out["present_tools"] = sorted(n for n in registry.get_all_tool_names() if n.startswith(("onair_present", "onair_work_share", "onair_obs", "onair_window", "onair_broadcast", "onair_layout"))) \
    if hasattr(registry, "get_all_tool_names") else None
out["skills"] = mgr.list_plugin_skills("on-air")
import hashlib
out["scene_skill_sha"] = {}
for _name in ("scene", "scene-build", "avatar"):
    _path = mgr.find_plugin_skill("on-air:" + _name)
    out["scene_skill_sha"][_name] = hashlib.sha256(_path.read_bytes()).hexdigest() if _path else None
_installed = home / "plugins" / "on-air" / "onair" / "scene_starter"
out["scene_templates"] = {n: hashlib.sha256((_installed / n).read_bytes()).hexdigest()
                          for n in ("index.html", "scene.js", "room.js", "character.js", "lighting.js", "events.js")}
_produce = mgr.find_plugin_skill("on-air:produce")
out["produce_sha"] = hashlib.sha256(_produce.read_bytes()).hexdigest() if _produce else None
out["produce_in_install"] = bool(_produce) and str(_produce).startswith(str(home / "plugins" / "on-air"))
if _produce:
    from tools.skills_tool import skill_view
    _view = json.loads(skill_view("on-air:produce"))
    out["produce_view_ok"] = bool(_view.get("success")) and "# Produce an On Air show" in str(_view.get("content", ""))
if sys.argv[3] in ("owner", "voice-disabled", "voice-disabled-legacy"):
    from gateway.platform_registry import platform_registry
    pkg0 = platform_registry.get("on_air").adapter_factory.__module__.rsplit(".", 1)[0]
    sr0 = importlib.import_module(pkg0 + ".show_runtime")
    se0 = importlib.import_module(pkg0 + ".setup_engine")
    out["voice_status"] = sr0.tts_connection(se0.engine().tts_dispatch)
if sys.argv[3] == "owner":
    # no owner session: must refuse, nothing served
    out["no_session"] = json.loads(registry.get_entry("onair_present_status").handler({"include_obs": False}))
    from gateway.platform_registry import platform_registry
    pkg = platform_registry.get("on_air").adapter_factory.__module__.rsplit(".", 1)[0]
    pa = importlib.import_module(pkg + ".presentation.agent")
    ad = importlib.import_module(pkg + ".adapter")
    se = importlib.import_module(pkg + ".setup_engine")
    sr = importlib.import_module(pkg + ".show_runtime")
    rt = pa.runtime()
    out["default_overlay"] = rt.obs.cfg.overlay_url
    # DISCLOSED stand-in for a running private show's control API: answers /facts with its overlay url
    class Facts(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_GET(self):
            if self.headers.get("Authorization") != "Bearer " + "t" * 32:
                self.send_error(401); return
            body = json.dumps({"ok": True, "running": True, "activity": "listening", "owner_pid": os.getpid(),
                               "overlay_url": "http://127.0.0.1:%d/overlay" % self.server.server_address[1]}).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Facts)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    eng = se.engine()
    eng.dir.mkdir(parents=True, exist_ok=True)
    tok = "t" * 32
    (eng.dir / sr.TOKEN_FILE).write_text(tok)
    eng._write(sr.SHOW_FILE, {"url": "http://127.0.0.1:%d" % srv.server_address[1], "pid": os.getpid(),
                              "token_sha": sr._sha(tok),
                              "overlay_url": "http://127.0.0.1:%d/overlay" % srv.server_address[1]})
    out["show_status_running"] = sr.status(eng).get("running")
    # the wrapped handler syncs before it runs (and still refuses without an owner session)
    out["wrapped_no_session"] = json.loads(registry.get_entry("onair_present_status").handler({"include_obs": False}))
    out["followed_overlay"] = rt.obs.cfg.overlay_url
    out["expected_overlay"] = "http://127.0.0.1:%d/overlay" % srv.server_address[1]
    srv.shutdown()
    # teardown is wired to the host's real unload
    served = rt.serve()
    port = int(served["surface"]["url"].split(":")[2].split("/")[0])
    s = socket.socket(); out["port_open_before_unload"] = s.connect_ex(("127.0.0.1", port)) == 0; s.close()
    out["unloaded"] = bool(mgr.unload("on-air"))
    s = socket.socket(); out["port_open_after_unload"] = s.connect_ex(("127.0.0.1", port)) == 0; s.close()
    out["runtime_after_unload"] = pa.runtime() is None
print("RESULT " + json.dumps(out, default=str))
'''


def _run(tmp_path: Path, kind: str) -> dict:
    home = tmp_path / kind
    plugin = home / "plugins" / "on-air"
    shutil.copytree(ROOT / "onair", plugin / "onair", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for name in ("__init__.py", "plugin.yaml"):
        shutil.copy(ROOT / name, plugin / name)
    if kind == "stage":
        (home / ".onair-stage-home").write_text("stage\n")
    (tmp_path / ("user-" + kind)).mkdir()
    probe = tmp_path / ("probe-" + kind + ".py")
    probe.write_text(textwrap.dedent(PROBE))
    version = subprocess.run([str(PY314), "-B", "-c", "from hermes_cli.config_defaults import DEFAULT_CONFIG as D;"
                              "print(D['_config_version'])"], cwd=SDK, capture_output=True, text=True,
                             env={"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(SDK)}).stdout.strip()
    env = {"PATH": os.environ.get("PATH", ""), "HOME": str(tmp_path / ("user-" + kind)), "HERMES_HOME": str(home),
           "PYTHONPATH": str(SDK), "PYTHONDONTWRITEBYTECODE": "1", "HERMES_DISABLE_LAZY_INSTALLS": "1",
           "TMPDIR": str(tmp_path), "HERMES_GATEWAY_PROFILE_PIN": "1", "NO_PROXY": "127.0.0.1,localhost",
           "ONAIR_PRESENT_PORT": "0"}
    proc = subprocess.run([str(PY314), "-B", str(probe), str(ROOT), version, kind], env=env, cwd=tmp_path,
                          capture_output=True, text=True, timeout=180)
    lines = [ln for ln in proc.stdout.splitlines() if ln.startswith("RESULT ")]
    assert proc.returncode == 0 and lines, proc.stdout[-3000:] + proc.stderr[-6000:]
    return json.loads(lines[-1][len("RESULT "):])


PRESENT = {"onair_work_share", "onair_present_status", "onair_present_publish", "onair_present_control", "onair_obs_setup",
           "onair_obs_scene", "onair_window_share", "onair_broadcast", "onair_layout"}


@pytest.fixture(scope="module")
def _skip_without_sdk():
    if not PY314.is_file() or not (SDK / "hermes_cli").is_dir():
        pytest.skip("the prepared Hermes Python 3.14 environment is not on this machine")


def test_owner_home_registers_the_whole_join_and_unload_tears_down(tmp_path, _skip_without_sdk):
    out = _run(tmp_path, "owner")
    print(json.dumps(out, indent=1))
    assert out["python"].startswith("3.14") and not out["pyyaml"], out
    assert set(out["registered"]) == set(out["manifest_tools"]), out  # every declared tool is real
    assert PRESENT <= set(out["manifest_tools"]) and {"onair_show_plan", "onair_show_safety"} <= set(out["registered"])
    assert set(out["present_tools"]) == PRESENT, out["present_tools"]
    assert out["skills"] == ["avatar", "first-stream", "host", "produce", "scene", "scene-build", "seed-bank", "setup", "show-day", "troubleshoot", "views"], out["skills"]
    for name, digest in out["scene_skill_sha"].items():
        assert digest == hashlib.sha256((ROOT / 'onair' / 'skills' / name / 'SKILL.md').read_bytes()).hexdigest()
    for name, digest in out['scene_templates'].items():
        assert digest == hashlib.sha256((ROOT / 'onair' / 'scene_starter' / name).read_bytes()).hexdigest()
    produce_src = hashlib.sha256((ROOT / "onair" / "skills" / "produce" / "SKILL.md").read_bytes()).hexdigest()
    assert out["produce_sha"] == produce_src and out["produce_in_install"] is True, out
    assert out["produce_view_ok"] is True, out
    assert {"onair-setup", "onair-broadcast", "onair-present"} <= set(out["commands"]), out["commands"]
    assert out["aux_tasks"] == ["onair_show"], out  # the show's own model slot (reasoning effort is configurable)
    assert out["voice_status"]["usable_here"] is True, out["voice_status"]  # registered dispatcher, voice enabled
    assert out["no_session"].get("ok") is False and out["wrapped_no_session"].get("ok") is False, out
    assert out["default_overlay"].endswith(":18790/overlay"), out
    assert out["show_status_running"] is True
    assert out["followed_overlay"] == out["expected_overlay"], out  # OBS overlay follows the running show
    assert out["port_open_before_unload"] is True and out["unloaded"] is True, out
    assert out["port_open_after_unload"] is False and out["runtime_after_unload"] is True, out


@pytest.mark.parametrize("kind", ["voice-disabled", "voice-disabled-legacy"])
def test_disabled_voice_gives_its_actual_reason_in_the_registered_entry(tmp_path, _skip_without_sdk, kind):
    out = _run(tmp_path, kind)
    voice = out["voice_status"]
    assert voice["usable_here"] is False
    assert "disabled" in voice["next_action"].lower(), voice
    assert "no hermes text_to_speech dispatcher" not in voice["next_action"].lower(), voice


def test_stage_home_registers_no_private_tools_or_commands(tmp_path, _skip_without_sdk):
    out = _run(tmp_path, "stage")
    assert out["registered"] == [] and out["commands"] == [] and out["aux_tasks"] == [], out
    assert out["skills"] == [] and out["produce_sha"] is None, out    # the public stage never gets the skills


class _Surface:
    def __init__(self, surface):
        self._surface = surface

    def session_surface(self):
        return self._surface


@pytest.mark.parametrize("surface,allowed", [("cli", True), ("tui", True), ("desktop", True), ("local", True),
                                              ("discord", False), ("telegram", False), ("on_air", False), ("", False)])
def test_owner_surfaces_include_the_gateway_local_platform(surface, allowed, tmp_path, monkeypatch):
    """Classic-CLI slash commands run on the gateway's Local platform on the supported SDK (observed: the
    approve command was refused there before this). Messaging chats and the public stage stay refused."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from onair import setup_agent
    from onair.presentation import agent as present
    assert setup_agent.owner_session(_Surface(surface))[0] is allowed
    assert present.owner_session(_Surface(surface))[0] is allowed
