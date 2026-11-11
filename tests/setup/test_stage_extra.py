"""Readiness uses the actual stage preflight, not just port probes."""
import socket

import pytest

from onair import stage_runtime as sr
from test_quick_setup import owner  # noqa: F401
from test_stage_runtime import _made_stage, _prepare

pytestmark = pytest.mark.skipif(sr.hermes_command() is None, reason="no Hermes CLI")


def test_other_preflight_failure_blocks_status_and_plan(owner, tmp_path, monkeypatch):
    eng, home, ctx, call, command = _made_stage(owner, tmp_path, monkeypatch)
    _prepare(home)
    path = home / "config.yaml"
    path.write_text(path.read_text().replace("on_air: [no_mcp]", "on_air: []"))
    status, plan = call("onair_stage_status"), call("onair_stage_plan")
    assert not status["startable"] and "toolsets" in [p["key"] for p in status["missing"]], status
    assert not plan["ok"] and "approve_with" not in plan, plan


def test_same_explicit_ports_are_not_startable(owner, tmp_path, monkeypatch):
    eng, home, ctx, call, command = _made_stage(owner, tmp_path, monkeypatch)
    _prepare(home)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    path = home / "config.yaml"
    import re
    text = re.sub(r"overlay_port: \d+", f"overlay_port: {port}\n        control_port: {port}", path.read_text())
    path.write_text(text)
    status = call("onair_stage_status")
    assert not status["startable"] and "control_port" in status["next_action"], status
