"""Browser-DRIVER harness self-check (no browser is started): the opt-in held model of
``tests/setup/browser/server.py`` binds to the EXACT dashboard module copy before any engine exists, is really
entered by the preview's draft, and a Stop through the real dashboard routes is genuinely draining until the
fixture releases it; only then does Stop succeed. This proves the harness for the reviewer's GUI journey and the
backend facts the GUI shows (running:false, draining:true, quiescent:false); it is NOT GUI pixels.

DISCLOSED FIXTURES: the held model client (host-owned ctx.llm edge, no network/provider) and a placeholder SDK
shim path (only served at /sdk.js, unused here). Everything else is the real plugin_api router + setup engine.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent / "browser"
ROOT = Path(__file__).resolve().parents[2]


def _http(base: str, method: str, path: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"} if data else {})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def _until(fn, timeout=30.0, step=0.1):
    deadline = time.monotonic() + timeout
    value = fn()
    while not value and time.monotonic() < deadline:
        time.sleep(step)
        value = fn()
    return value


def test_held_model_binds_to_the_dashboard_copy_and_stop_drains_until_released(tmp_path):
    pytest.importorskip("fastapi")
    pytest.importorskip("uvicorn")
    home, user = tmp_path / "home", tmp_path / "user"
    home.mkdir(), user.mkdir()
    (home / "config.yaml").write_text("model:\n  provider: fixture-provider\n  default: fixture-model\n")
    shim = tmp_path / "shim.js"
    shim.write_text("// placeholder: not used by this harness check\n")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = dict(os.environ, HOME=str(user), HERMES_HOME=str(home), PYTHONDONTWRITEBYTECODE="1",
               HERMES_DISABLE_LAZY_INSTALLS="1", ONAIR_SDK_SHIM=str(shim), ONAIR_HELD_MODEL="1",
               ONAIR_HELD_MODEL_MAX_S="60")
    log = open(tmp_path / "server.log", "w")
    server = subprocess.Popen([sys.executable, "-B", str(HERE / "server.py"), str(ROOT), str(port)], env=env,
                              stdout=log, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    api = "/api/plugins/on-air/"
    try:
        def up():
            with socket.socket() as s:
                return s.connect_ex(("127.0.0.1", port)) == 0
        assert _until(up, 20), (tmp_path / "server.log").read_text()
        held = _http(base, "GET", "/fixture/held")
        assert held == {"module": "_onair_desk_pkg.setup_engine", "engines": 0, "engines_bound": False,
                        "calls": [], "probes": [], "entered": False, "released": False}, held  # bound BEFORE any engine
        status = _http(base, "GET", api + "setup/quick/status")
        assert status["model_connection"]["usable_here"] is False and status["model_connection"]["state"] == "untested", status["model_connection"]
        assert _http(base, "GET", "/fixture/held")["engines_bound"] is True  # the engine the routes use
        plan = _http(base, "POST", api + "setup/quick/plan", {"answers": {"channel": "cozy_fox", "use_model": True}})
        assert plan["ok"] is True, plan
        assert len(_http(base, "GET", "/fixture/held")["probes"]) == 1
        applied = _http(base, "POST", api + "setup/quick/apply", {"digest": plan["digest"], "confirm": True})
        assert applied["ok"] is True, applied
        assert _until(lambda: _http(base, "GET", "/fixture/held")["entered"], 60), _http(base, "GET", "/fixture/held")
        held = _http(base, "GET", "/fixture/held")
        assert len(held["calls"]) == 1 and held["calls"][0]["purpose"] == "onair.setup-preview", held
        assert _until(lambda: (_http(base, "GET", api + "setup/quick/status").get("preview") or {}).get("running"), 30)
        first = _http(base, "POST", api + "setup/quick/cancel", {})
        assert first["ok"] is False and first["partial"] is True and first["draining"] is True, first
        facts = _http(base, "GET", api + "setup/quick/status")["preview"]
        assert facts["running"] is False and facts["draining"] is True and facts["quiescent"] is False, facts
        again = _http(base, "POST", api + "setup/quick/cancel", {})  # the GUI retry reaches the backend
        assert again["ok"] is False and again["draining"] is True, again
        assert len(_http(base, "GET", "/fixture/held")["calls"]) == 1
        _http(base, "POST", "/fixture/release")
        done = _until(lambda: (lambda r: r if r.get("ok") else None)(_http(base, "POST", api + "setup/quick/cancel", {})), 30, 0.3)
        assert done and done["ok"] is True, done  # complete only after the request really ended
        facts = _http(base, "GET", api + "setup/quick/status")["preview"]
        assert facts.get("running") is False and not facts.get("draining"), facts
        if os.environ.get("ONAIR_EVIDENCE_DIR"):
            (Path(os.environ["ONAIR_EVIDENCE_DIR"]) / "held-harness.json").write_text(json.dumps(
                {"held": _http(base, "GET", "/fixture/held"), "first_cancel": first, "draining_facts_seen": True,
                 "final": done}, indent=1, default=str))
    finally:
        server.terminate()
        server.wait(20)
        log.close()
