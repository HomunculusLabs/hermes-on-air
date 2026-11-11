"""``POST /runtime/clear`` through the real desk control server of a connected adapter.

Real pieces: the plugin's ``register(ctx)`` on a real Hermes ``PluginContext``, ``OnAirAdapter``
``connect()`` (overlay and desk control servers on loopback), the bearer token the adapter wrote to
``desk.token``, the registered runtime gate, the stage controller and its receipts. Faked: only the
Twitch chat source and the TTS engine, as in ``test_runtime_adapter``.
"""
from __future__ import annotations

import asyncio
import json
import threading

import pytest

pytest.importorskip("gateway.platforms.base")
pytest.importorskip("hermes_cli.middleware")

from gateway.config import PlatformConfig

from onair.tts import FakeTTS


class FakeTwitch:
    def __init__(self, *args, **kwargs):
        pass

    def start(self):
        pass

    async def stop(self):
        pass


@pytest.fixture
def env(tmp_path, monkeypatch, safe_home):
    import onair.adapter as adapter
    from onair.sources import twitch_irc

    monkeypatch.setattr(twitch_irc, "TwitchChatSource", FakeTwitch)
    monkeypatch.setattr(adapter, "data_dir", lambda: tmp_path / "data")
    (tmp_path / "data").mkdir()
    return tmp_path / "data"


async def connected(env):
    from onair.adapter import OnAirAdapter

    ad = OnAirAdapter(PlatformConfig(enabled=True, extra={"overlay_port": 0, "control_port": 0}))
    ad.tts_engine = FakeTTS(seconds=0.01)
    assert await ad.connect()
    token = (env / "desk.token").read_text().strip()
    info = json.loads((env / "control.json").read_text())
    assert info["url"] == ad.control.url
    return ad, ad.control.http.port, {"Authorization": f"Bearer {token}"}


async def call(port, method, path, *, body=None, raw=None, headers=None):
    """One raw HTTP/1.1 request to the loopback control server; returns (status, parsed JSON)."""
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else b"")
    hdrs = {"Host": f"127.0.0.1:{port}", "Content-Length": str(len(data)), **(headers or {})}
    head = f"{method} {path} HTTP/1.1\r\n" + "".join(f"{k}: {v}\r\n" for k, v in hdrs.items()) + "\r\n"
    writer.write(head.encode() + data)
    await writer.drain()
    response = await asyncio.wait_for(reader.read(), timeout=5)
    writer.close()
    status_line, _, payload = response.partition(b"\r\n\r\n")
    return int(status_line.split(b" ")[1]), json.loads(payload or b"null")


async def latch(guard, request):
    """Call the registered middleware the way Hermes does, from a non-loop thread."""

    def run():
        guard.llm_execution(request=request, next_call=lambda r: pytest.fail("provider called"),
                            platform="on_air", api_mode="chat_completions")

    worker = threading.Thread(target=run)
    worker.start()
    await asyncio.get_running_loop().run_in_executor(None, worker.join)
    for _ in range(100):  # let the scheduled cut run on this loop
        await asyncio.sleep(0)


def cleared(ad):
    return [r for r in ad.controller.receipts.tail(500) if r.get("kind") == "runtime_cleared"]


def test_clear_route_refuses_when_live_or_not_latched(env, plugin_registration, fresh_runtime_guard):
    async def run():
        ad, port, auth = await connected(env)
        try:
            c = ad.controller
            st, body = await call(port, "POST", "/runtime/clear", body={}, headers=auth)
            assert (st, body) == (409, {"ok": False, "error": "the runtime gate is not latched"})
            st, body = await call(port, "POST", "/mode", body={"mode": "approval"}, headers=auth)
            assert st == 200 and body["state"]["live"] is True
            st, body = await call(port, "POST", "/runtime/clear", body={}, headers=auth)
            assert st == 409 and body == {"ok": False, "error": "go off air before clearing the runtime latch"}
            assert c.state.mode == "approval"
            st, body = await call(port, "POST", "/stop", body={}, headers=auth)
            assert st == 200 and body["state"]["mode"] == "paused"
            st, body = await call(port, "POST", "/runtime/clear", body={}, headers=auth)
            assert st == 409 and "off air" in body["error"]

            # Latched: the cut leaves the show OFF AIR and arming is refused, so a latched live show
            # is unreachable through the desk. Force the mode to pin the order anyway: a show that is
            # not off air is refused before the latch is touched.
            await call(port, "POST", "/mode", body={"mode": "approval"}, headers=auth)
            await latch(fresh_runtime_guard, {"tools": [{"name": "x"}]})
            assert c.state.mode == "off_air" and fresh_runtime_guard.violated()
            c.state.mode = "approval"
            st, body = await call(port, "POST", "/runtime/clear", body={}, headers=auth)
            assert st == 409 and "off air" in body["error"]
            assert fresh_runtime_guard.violation()["rule"] == "tool_surface"
            assert cleared(ad) == []
        finally:
            await ad.disconnect()

    asyncio.run(run())


def test_clear_route_after_a_latch_then_arm_succeeds(env, plugin_registration, fresh_runtime_guard):
    async def run():
        ad, port, auth = await connected(env)
        try:
            st, _ = await call(port, "POST", "/mode", body={"mode": "approval"}, headers=auth)
            assert st == 200
            await latch(fresh_runtime_guard, {"tools": [{"name": "x"}]})
            st, state = await call(port, "GET", "/state", headers=auth)
            assert st == 200 and state["mode"] == "off_air"
            assert state["preflight"]["ok"] is False
            assert state["preflight"]["reason"] == "runtime_refused:tool_surface"
            failing = [ch["name"] for ch in state["preflight"]["checks"] if ch["status"] == "fail"]
            assert failing == ["runtime_latch"]
            st, body = await call(port, "POST", "/mode", body={"mode": "approval"}, headers=auth)
            assert st == 409 and "runtime_refused:tool_surface" in body["error"]

            # Nothing but an authorized POST with an empty object clears it.
            refused = [
                ({}, None, None, 401),
                ({**auth, "Origin": "http://127.0.0.1:9119"}, None, None, 403),
                ({**auth, "Authorization": "Bearer wrong"}, None, None, 401),
                (auth, {"force": True}, None, 400),
                (auth, {"rule": "tool_surface"}, None, 400),
                (auth, [], None, 400),
                (auth, None, b"not json", 400),
            ]
            for headers, payload, raw, want in refused:
                st, body = await call(port, "POST", "/runtime/clear", body=payload, raw=raw, headers=headers)
                assert st == want and body["ok"] is False, (headers.keys(), payload, raw, st, body)
            assert (await call(port, "GET", "/runtime/clear", headers=auth))[0] == 405
            assert fresh_runtime_guard.violated() and cleared(ad) == []

            st, body = await call(port, "POST", "/runtime/clear", body={}, headers=auth)
            assert st == 200 and body["ok"] is True
            state = body["state"]
            assert state["mode"] == "off_air" and state["preflight"]["ok"] is True
            assert "reason" not in state["preflight"]
            assert not [ch for ch in state["preflight"]["checks"] if ch["name"] == "runtime_latch"]
            assert not fresh_runtime_guard.violated()

            st, body = await call(port, "GET", "/receipts?limit=500", headers=auth)
            rows = [r for r in body["receipts"] if r["kind"] == "runtime_cleared"]
            assert st == 200 and len(rows) == 1 and rows[0]["rule"] == "tool_surface"
            refused_at = [i for i, r in enumerate(body["receipts"]) if r["kind"] == "runtime_refused"]
            assert len(refused_at) == 1 and refused_at[0] < body["receipts"].index(rows[0])

            # A second clear has nothing to clear and writes no receipt.
            st, body = await call(port, "POST", "/runtime/clear", body={}, headers=auth)
            assert (st, body) == (409, {"ok": False, "error": "the runtime gate is not latched"})
            assert len(cleared(ad)) == 1

            st, body = await call(port, "POST", "/mode", body={"mode": "approval"}, headers=auth)
            assert st == 200 and body["state"]["mode"] == "approval" and body["state"]["live"] is True
        finally:
            await ad.disconnect()

    asyncio.run(run())


def test_clear_route_with_an_empty_request_body(env, plugin_registration, fresh_runtime_guard):
    """Missing body is rejected without changing the latch; an actual object clears."""
    async def run():
        ad, port, auth = await connected(env)
        try:
            await call(port, "POST", "/mode", body={"mode": "approval"}, headers=auth)
            await latch(fresh_runtime_guard, {"messages": [{"role": "system", "content": "<memory-context>x</memory-context>"}]})
            st, body = await call(port, "POST", "/runtime/clear", headers=auth)
            assert st == 400 and fresh_runtime_guard.violated() and cleared(ad) == []
            st, body = await call(port, "POST", "/runtime/clear", body={}, headers=auth)
            assert st == 200 and body["state"]["preflight"]["ok"] is True
            assert [r["rule"] for r in cleared(ad)] == ["private_context"]
        finally:
            await ad.disconnect()

    asyncio.run(run())


def test_concurrent_clear_has_one_winner(env, plugin_registration, fresh_runtime_guard):
    async def run():
        ad, port, auth = await connected(env)
        try:
            for trial in range(10):
                assert (await call(port, "POST", "/mode", body={"mode": "approval"}, headers=auth))[0] == 200
                await latch(fresh_runtime_guard, {"tools": [{"name": "x"}]})
                responses = await asyncio.gather(*(call(port, "POST", "/runtime/clear", body={}, headers=auth) for _ in range(8)))
                statuses = sorted(st for st, _ in responses)
                assert statuses == [200] + [409] * 7
                assert len(cleared(ad)) == trial + 1
                assert not fresh_runtime_guard.violated()
        finally:
            await ad.disconnect()
    asyncio.run(run())


def test_clear_refresh_exception_is_non_latch_and_recovers(env, plugin_registration, fresh_runtime_guard, monkeypatch):
    async def run():
        ad, port, auth = await connected(env)
        try:
            assert (await call(port, "POST", "/mode", body={"mode": "approval"}, headers=auth))[0] == 200
            await latch(fresh_runtime_guard, {"tools": [{"name": "x"}]})
            original = ad.controller._preflight_check
            def broken():
                raise RuntimeError("transient preflight error")
            monkeypatch.setattr(ad.controller, "_preflight_check", broken)
            st, result = await call(port, "POST", "/runtime/clear", body={}, headers=auth)
            assert st == 200 and not fresh_runtime_guard.violated()
            pf = result["state"]["preflight"]
            assert pf["ok"] is False and not pf.get("reason", "").startswith("runtime_refused:")
            assert any(ch["status"] == "fail" for ch in pf["checks"])
            st, state = await call(port, "GET", "/state", headers=auth)
            assert st == 200 and state["preflight"] == pf
            assert len(cleared(ad)) == 1
            assert (await call(port, "POST", "/runtime/clear", body={}, headers=auth))[0] == 409
            assert (await call(port, "POST", "/mode", body={"mode": "approval"}, headers=auth))[0] == 409
            monkeypatch.setattr(ad.controller, "_preflight_check", original)
            st, result = await call(port, "POST", "/mode", body={"mode": "approval"}, headers=auth)
            assert st == 200 and result["state"]["preflight"]["ok"] is True
        finally:
            await ad.disconnect()
    asyncio.run(run())
