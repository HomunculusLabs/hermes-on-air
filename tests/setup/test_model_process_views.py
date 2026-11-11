"""A dashboard process does not inherit the agent's model probe result."""
import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

from onair import setup_engine as se
from test_quick_setup import owner  # noqa: F401

ROOT = Path(__file__).resolve().parents[2]
SERVER = ROOT / "tests" / "setup" / "browser" / "server.py"


def test_separate_dashboard_is_untested_after_agent_probe_fails(owner, tmp_path):
    pytest.importorskip("uvicorn")
    class FailedModel:
        def complete(self, **kwargs):
            raise RuntimeError("fixture failure")

    agent = se.SetupEngine(owner, model_client=FailedModel())
    assert agent.status()["model_connection"]["state"] == "untested"
    result = agent.plan({"channel": "fixture", "target": str(tmp_path / "stage"), "use_model": True})
    assert not result["ok"] and result["next_action"] == se.MODEL_UNAVAILABLE
    assert agent.status()["model_connection"]["state"] == "unavailable"

    shim = tmp_path / "shim.js"
    shim.write_text("// fixture\n")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = dict(os.environ, HERMES_HOME=str(owner), ONAIR_SDK_SHIM=str(shim))
    env.pop("ONAIR_HELD_MODEL", None)
    env.pop("ONAIR_SHOW_FIXTURE", None)
    with open(tmp_path / "server.log", "w") as log:
        server = subprocess.Popen([sys.executable, "-B", str(SERVER), str(ROOT), str(port)],
                                  env=env, stdout=log, stderr=subprocess.STDOUT)
        try:
            url = f"http://127.0.0.1:{port}/api/plugins/on-air/setup/quick/status"
            deadline = time.monotonic() + 20
            while True:
                try:
                    with urllib.request.urlopen(url, timeout=2) as response:
                        status = json.load(response)
                    break
                except OSError:
                    if time.monotonic() >= deadline or server.poll() is not None:
                        raise
                    time.sleep(.1)
            connection = status["model_connection"]
            assert connection["state"] == "untested" and connection["usable_here"] is False
            assert "Not tested from this page" in connection["next_action"]
            assert "cannot reach" not in connection["next_action"]
        finally:
            server.send_signal(signal.SIGINT)
            server.wait(timeout=20)
