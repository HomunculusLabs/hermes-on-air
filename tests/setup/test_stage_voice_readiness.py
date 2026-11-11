"""Voice readiness through the registered owner tools, with a stubbed safety check."""
import json

import pytest

from onair import stage_runtime as sr
from onair.preflight import run_preflight
from onair.setup_wizard import readiness as wizard_readiness
from onair.yamlio import safe_load
from test_quick_setup import owner  # noqa: F401
from test_stage_runtime import _made_stage, _prepare


@pytest.mark.parametrize("provider", ["edge", "custom-speech"])
def test_configured_voice_is_startable_and_secret_free(owner, tmp_path, monkeypatch, provider):
    eng, home, _, call, _ = _made_stage(owner, tmp_path, monkeypatch)
    _prepare(home)
    config = home / "config.yaml"
    config.write_text(config.read_text().replace("voice: false", "voice: true") +
                      f"\ntts:\n  provider: {provider}\n  providers:\n    custom-speech:\n"
                      "      command: 'secret-command-line'\n      token: secret-token-value\n")
    monkeypatch.setattr(sr, "hermes_command", lambda: ["hermes"])
    monkeypatch.setattr(sr, "install_isolation", lambda _: [])
    monkeypatch.setattr(sr, "_stage_preflight", lambda *_: {"ok": True, "missing": []})
    status, plan = call("onair_stage_status"), call("onair_stage_plan")
    assert status["startable"] is True and plan["ok"] is True and plan["approve_with"]
    expected = f"Voice is ON ({provider}): configured, not sound-tested by this check: listen to one line before going live."
    voice = next(p for p in status["prerequisites"] if p["key"] == "voice")
    assert voice["ok"] and voice["detail"] == expected
    assert expected in plan["effects"]
    assert "secret-command-line" not in json.dumps([status, plan])
    assert "secret-token-value" not in json.dumps([status, plan])
    ready = wizard_readiness(str(home))
    row = next(r for r in ready["rows"] if r["key"] == "voice")
    assert row["state"] == "done" and row["detail"] == expected
    assert not any(s["key"] == "voice" for s in ready["steps"])
    cfg = safe_load(config.read_text())
    checks = run_preflight(config=cfg, settings=cfg["plugins"]["entries"]["on-air"]["settings"])["checks"]
    assert next(check for check in checks if check["name"] == "tts")["status"] == "warn"


@pytest.mark.parametrize("provider", [None, "", "   ", "absent"])
def test_voice_without_provider_refuses_with_remedy(owner, tmp_path, monkeypatch, provider):
    eng, home, _, call, _ = _made_stage(owner, tmp_path, monkeypatch)
    _prepare(home)
    config = home / "config.yaml"
    value = "null" if provider is None else json.dumps(provider)
    config.write_text(config.read_text().replace("voice: false", "voice: true") +
                      ("" if provider == "absent" else f"\ntts:\n  provider: {value}\n"))
    monkeypatch.setattr(sr, "hermes_command", lambda: ["hermes"])
    monkeypatch.setattr(sr, "install_isolation", lambda _: [])
    status, plan = call("onair_stage_status"), call("onair_stage_plan")
    assert status["startable"] is False and plan["ok"] is False and "approve_with" not in plan
    assert [p["key"] for p in plan["missing"]] == ["voice"]
    assert f"HERMES_HOME={home} hermes tools" in plan["next_action"]
    assert "captions only" in plan["next_action"]
    ready = wizard_readiness(str(home))
    row = next(r for r in ready["rows"] if r["key"] == "voice")
    assert row["state"] == "action"


def test_voice_off_remains_captions_only(owner, tmp_path, monkeypatch):
    eng, home, _, call, _ = _made_stage(owner, tmp_path, monkeypatch)
    _prepare(home)
    monkeypatch.setattr(sr, "hermes_command", lambda: ["hermes"])
    monkeypatch.setattr(sr, "install_isolation", lambda _: [])
    monkeypatch.setattr(sr, "_stage_preflight", lambda *_: {"ok": True, "missing": []})
    status, plan = call("onair_stage_status"), call("onair_stage_plan")
    assert status["startable"] and plan["ok"]
    assert not any(p["key"] == "voice" for p in status["prerequisites"])
    assert not any("Voice is ON" in effect for effect in plan["effects"])
