"""Registration contract, owner authority, surface producer/readback and teardown (loopback only)."""
import http.client
import json
import socket
import threading
from urllib.parse import urlsplit

import pytest

from conftest import make
from onair.presentation import agent
from onair.presentation.deck import SAMPLE_DECK, validate_deck

EXPECTED_TOOLS = ["onair_work_share", "onair_present_status", "onair_present_publish", "onair_present_control", "onair_obs_setup",
                  "onair_obs_scene", "onair_window_share", "onair_broadcast", "onair_layout"]


def call(ctx, name, args=None):
    return json.loads(ctx.tools[name]["handler"](args or {}))


def get(url, path, host=None):
    u = urlsplit(url)
    conn = http.client.HTTPConnection(u.hostname, u.port, timeout=5)
    conn.request("GET", path, headers={"Host": host or f"127.0.0.1:{u.port}"})
    r = conn.getresponse()
    body = r.read()
    conn.close()
    return r.status, dict(r.getheaders()), body


def test_register_exact_contract_and_nothing_starts(obs):
    ctx, done = make(obs.url)
    assert done == ["tool:" + n for n in EXPECTED_TOOLS] + ["command:onair-broadcast", "command:onair-present"]
    assert list(ctx.tools) == EXPECTED_TOOLS and list(ctx.commands) == ["onair-broadcast", "onair-present"]
    for name, t in ctx.tools.items():
        assert t["toolset"] == "onair_present"
        params = t["schema"]["parameters"]
        assert params["additionalProperties"] is False and t["schema"]["name"] == name
        assert not {"caller", "owner", "approved", "user"} & set(params["properties"])
    rt = agent.runtime()
    assert rt.surface is None and obs.connections == 0  # registering serves nothing and contacts no OBS


def test_stage_home_registers_nothing(obs, owner_home):
    from onair.onboarding import MARKER
    (owner_home / MARKER).parent.mkdir(parents=True, exist_ok=True)
    (owner_home / MARKER).write_text("stage")
    ctx, done = make(obs.url)
    assert done == [] and ctx.tools == {}


@pytest.mark.parametrize("surface", ["telegram", "discord", "on_air", "", "acp"])
def test_non_owner_surfaces_refused(obs, surface):
    ctx, _ = make(obs.url, surface="cli")
    ctx.surface = surface
    for name in EXPECTED_TOOLS:
        out = call(ctx, name, {"action": "status"} if name == "onair_broadcast" else {})
        assert out["ok"] is False and out["refused"] is True
    assert "not" in ctx.commands["onair-broadcast"]("status").lower() or "only" in ctx.commands["onair-broadcast"]("status")
    assert obs.connections == 0


def test_extra_arguments_refused_not_read(obs):
    ctx, _ = make(obs.url)
    out = call(ctx, "onair_present_publish", {"sample": True, "caller": "owner"})
    assert out["ok"] is False and "caller" in out["error"]
    assert agent.runtime().surface is None


def test_publish_navigate_standby_readback_and_sse(obs):
    ctx, _ = make(obs.url)
    out = call(ctx, "onair_present_publish", {"sample": True})
    assert out["ok"] and out["surface"]["serving"] and out["program"]["mode"] == "slides"
    url = out["surface"]["url"]
    assert url.startswith("http://127.0.0.1:") and url.endswith("/present")
    status, headers, body = get(url, "/present")
    assert status == 200 and b"/present/app.js" in body and "media-src 'none'" in headers["Content-Security-Policy"]
    assert headers["Cache-Control"] == "no-store"  # a Browser Source must not keep old assets after an upgrade
    status, _, body = get(url, "/present/state")
    snap = json.loads(body)
    assert snap["slide"]["heading"] == SAMPLE_DECK["slides"][0]["heading"] and snap["count"] == 4
    assert "slides" not in snap and "headings" not in snap  # only the current slide is public

    # SSE: snapshot on connect, then one frame per change
    u = urlsplit(url)
    sock = socket.create_connection((u.hostname, u.port), timeout=5)
    sock.sendall(f"GET /present/events HTTP/1.1\r\nHost: 127.0.0.1:{u.port}\r\n\r\n".encode())
    state = {"buf": b"", "head": False}
    frames = []

    def read_frames(n):
        while len(frames) < n:
            state["buf"] += sock.recv(65536)
            if not state["head"] and b"\r\n\r\n" in state["buf"]:
                head, _, state["buf"] = state["buf"].partition(b"\r\n\r\n")
                assert head.startswith(b"HTTP/1.1 200") and b"text/event-stream" in head
                state["head"] = True
            while state["head"] and b"\n\n" in state["buf"]:
                chunk, _, state["buf"] = state["buf"].partition(b"\n\n")
                if chunk.startswith(b"data: "):
                    frames.append(json.loads(chunk[6:]))
    read_frames(1)
    assert frames[0]["index"] == 0
    rev = frames[0]["revision"]
    assert call(ctx, "onair_present_control", {"action": "next"})["program"]["index"] == 1
    read_frames(2)
    assert frames[1]["index"] == 1 and frames[1]["revision"] == rev + 1 and frames[1]["slide"]["kind"] == "bullets"
    out = call(ctx, "onair_present_control", {"action": "standby", "title": "Back in five", "note": "stretch break"})
    read_frames(3)
    assert frames[2]["mode"] == "standby" and frames[2]["slide"] is None and frames[2]["standby"]["title"] == "Back in five"
    assert out["surface"]["pages_connected"] == 1
    assert call(ctx, "onair_present_control", {"action": "goto", "index": 9})["ok"] is False
    assert call(ctx, "onair_present_control", {"action": "resume"})["program"]["mode"] == "slides"
    sock.close()


def test_surface_refuses_writes_and_foreign_hosts(obs):
    ctx, _ = make(obs.url)
    url = call(ctx, "onair_present_publish", {"sample": True})["surface"]["url"]
    u = urlsplit(url)
    assert get(url, "/present/state", host="evil.example:80")[0] == 421
    conn = http.client.HTTPConnection(u.hostname, u.port, timeout=5)
    conn.request("POST", "/present/state", body=b"{}", headers={"Host": f"127.0.0.1:{u.port}"})
    assert conn.getresponse().status == 405
    conn.close()
    assert get(url, "/present/../../etc/passwd")[0] == 404


@pytest.mark.parametrize("text", ["see /home/someone/notes.txt", "ssh to " + ".".join(("192", "168", "1", "20")), "token sk-ABCDEFGHIJKLMNOPQRSTUV",
                                  "bad\x07bell"])
def test_deck_validation_refuses_leak_shapes(text):
    deck, errors = validate_deck({"title": "t", "slides": [{"kind": "text", "heading": "h", "body": text}]})
    assert deck is None and errors


def test_deck_bounds_and_literal_text():
    assert validate_deck({"title": "t", "slides": [{"kind": "title", "heading": "x"}] * 41})[0] is None
    assert validate_deck({"title": "t", "slides": [{"kind": "bullets", "heading": "x", "bullets": ["a"] * 9}]})[0] is None
    assert validate_deck({"title": "t", "slides": [{"kind": "text", "heading": "x", "body": "b", "html": "<b>"}]})[0] is None
    deck, _ = validate_deck({"title": "t", "slides": [{"kind": "text", "heading": "<script>x</script>", "body": "a\nb"}]})
    assert deck["slides"][0]["heading"] == "<script>x</script>"  # kept as text; the page renders textContent only


def test_teardown_closes_port_and_loop(obs):
    import onair.presentation as pres
    ctx, _ = make(obs.url)
    url = call(ctx, "onair_present_publish", {"sample": True})["surface"]["url"]
    rt = agent.runtime()
    thread = rt._thread
    pres.teardown()
    u = urlsplit(url)
    with pytest.raises(OSError):
        socket.create_connection((u.hostname, u.port), timeout=1).close()
    thread.join(2)
    assert not thread.is_alive() and agent.runtime() is None
    assert not [t for t in threading.enumerate() if t.name == "onair-presentation"]
