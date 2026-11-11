"""A busy presentation port: the default falls back to a free loopback port; a chosen port is refused clearly."""
import json
import socket

import pytest

import onair.presentation as pres
from conftest import FakeCtx
from onair.presentation import agent
from test_contract_surface import call, get


@pytest.fixture
def busy_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    yield s.getsockname()[1]
    s.close()


def test_busy_default_port_falls_back_to_a_free_loopback_port(obs, busy_port, monkeypatch):
    monkeypatch.delenv("ONAIR_PRESENT_PORT", raising=False)
    monkeypatch.setattr(agent, "DEFAULT_PRESENT_PORT", busy_port)
    ctx = FakeCtx()
    pres.register(ctx, config={"obs_url": obs.url})  # nobody chose a port
    out = call(ctx, "onair_present_publish", {"sample": True})
    assert out["ok"] is True, out
    surface = out["surface"]
    assert surface["serving"] is True and surface["url"].startswith("http://127.0.0.1:")
    assert f":{busy_port}/" not in surface["url"]
    assert str(busy_port) in surface["port_note"] and surface["url"] in surface["port_note"]
    status, _headers, body = get(surface["url"], "/present/state")
    assert status == 200 and json.loads(body)
    assert call(ctx, "onair_present_status", {})["surface"]["port_note"] == surface["port_note"]


@pytest.mark.parametrize("how", ["setting", "env"])
def test_busy_chosen_port_is_refused_with_the_owner_action(obs, busy_port, monkeypatch, how):
    monkeypatch.delenv("ONAIR_PRESENT_PORT", raising=False)
    config = {"obs_url": obs.url}
    if how == "setting":
        config["present_port"] = busy_port
    else:
        monkeypatch.setenv("ONAIR_PRESENT_PORT", str(busy_port))
    ctx = FakeCtx()
    pres.register(ctx, config=config)
    out = call(ctx, "onair_present_publish", {"sample": True})
    assert out["ok"] is False and "in use" in out["error"] and "present_port" in out["error"]
    assert agent.runtime().surface is None  # nothing was started on another port


def test_free_default_port_has_no_port_note(obs, monkeypatch):
    monkeypatch.delenv("ONAIR_PRESENT_PORT", raising=False)
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    free = s.getsockname()[1]
    s.close()
    monkeypatch.setattr(agent, "DEFAULT_PRESENT_PORT", free)
    ctx = FakeCtx()
    pres.register(ctx, config={"obs_url": obs.url})
    out = call(ctx, "onair_present_publish", {"sample": True})
    assert out["ok"] is True and out["surface"]["url"] == f"http://127.0.0.1:{free}/present"
    assert "port_note" not in out["surface"]
