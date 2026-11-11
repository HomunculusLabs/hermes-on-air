"""Real-browser journey + preview geometry (desktop 1280, phone 390) against the real plugin_api and engine.

Opt-in (it starts Chromium once): ONAIR_BROWSER_JOURNEY=1 with ONAIR_SDK_SHIM (host SDK shim bundle, a disclosed
fixture), DESK_TEST_CHROMIUM (existing Chromium executable) and ONAIR_PLAYWRIGHT (existing playwright package).
Use a SHORT TMPDIR: Chromium's profile socket path must fit the unix-socket limit. Nothing is downloaded."""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent / "browser"
ROOT = HERE.parents[2]


def test_browser_journey_and_preview_geometry(tmp_path):
    if os.environ.get("ONAIR_BROWSER_JOURNEY") != "1":
        pytest.skip("opt-in: starts a browser (ONAIR_BROWSER_JOURNEY=1)")
    for var in ("ONAIR_SDK_SHIM", "DESK_TEST_CHROMIUM", "ONAIR_PLAYWRIGHT"):
        if not Path(os.environ.get(var, "")).exists():
            pytest.skip(var + " missing")
    out = Path(os.environ.get("ONAIR_EVIDENCE_DIR") or tmp_path / "out")
    home, user = tmp_path / "home", tmp_path / "user"
    home.mkdir(), user.mkdir()
    (home / "config.yaml").write_text("model:\n  provider: fixture-provider\n  default: fixture-model\n")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = dict(os.environ, HOME=str(user), HERMES_HOME=str(home), PYTHONDONTWRITEBYTECODE="1",
               HERMES_DISABLE_LAZY_INSTALLS="1")
    server = subprocess.Popen([sys.executable, "-B", str(HERE / "server.py"), str(ROOT), str(port)], env=env,
                              stdout=open(tmp_path / "server.log", "w"), stderr=subprocess.STDOUT)
    try:
        for _ in range(100):
            with socket.socket() as s:
                if s.connect_ex(("127.0.0.1", port)) == 0:
                    break
            time.sleep(0.1)
        head = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        node = subprocess.run(["node", "--max-old-space-size=1536", str(HERE / "journey.cjs")], cwd=HERE,
                              env=dict(os.environ, ONAIR_BASE=f"http://127.0.0.1:{port}", ONAIR_OUT=str(out),
                                       SRC_HEAD=head),
                              capture_output=True, text=True, timeout=300)
        (out / "journey.log").write_text(node.stdout + node.stderr)
    finally:
        server.terminate()
        server.wait(20)
    report = json.loads((out / "RESULT.json").read_text())
    assert report["legs"] == {"desktop-1280": "ran", "phone-390": "ran"}, report["failures"]
    assert report["failures"] == [], report["failures"]
    labels = {g["label"] for g in report["geometry"]}
    assert {"desktop-preview", "desktop-shown", "phone-preview", "phone-shown"} <= labels
    assert [o["receivedAfterApproval"] for o in report["overlay"]] == [True, True], report["overlay"]
