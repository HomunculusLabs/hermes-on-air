"""The show's missing-model next step never offers the setup preview's canned stand-in (a show has none)."""
import pytest

hc = pytest.importorskip("hermes_cli.config", reason="needs the Hermes agent runtime on the path")

from onair import setup_engine as se  # noqa: E402
from onair import show_runtime as sr  # noqa: E402


def _conn(monkeypatch, cfg, client=None):
    monkeypatch.setattr(hc, "load_config_readonly", lambda: cfg)
    return se.model_connection(client)


def test_configured_but_unreachable_points_to_agent_chat(monkeypatch):
    model = _conn(monkeypatch, {"model": {"provider": "custom", "default": "fixture-model"}})
    assert model["configured"] is True and model["usable_here"] is False
    assert model["next_action"] == se.MODEL_UNAVAILABLE
    nxt = sr.show_model_next_action(model)
    assert "canned" not in nxt.lower() and "agent chat" in nxt and "/onair-setup show approve" in nxt


def test_not_configured_points_to_hermes_model(monkeypatch):
    model = _conn(monkeypatch, {})
    assert model["configured"] is False
    nxt = sr.show_model_next_action(model)
    assert "canned" not in nxt.lower() and "hermes model" in nxt
