"""``POST /runtime/clear`` on the desk control server, without Hermes on the path.

The clear is attached to the controller by the adapter (``tests/cli/test_runtime_clear_route.py``
drives the real one). Here a bare ``StageController`` has none, and an attached stand-in checks the
route's own contract: token and Origin first, a strict empty-object body, ``ControlError`` mapped to
the usual error shape, and the returned snapshot under ``state``.
"""

from __future__ import annotations

import asyncio
import json

from core_http_helpers import http

from onair.control import ControlServer
from onair.stage import ControlError, StageConfig, StageController


def _controller(tmp_path, guard):
    return StageController(StageConfig(turn_gap_s=0.0, air_pace=0.0), data_dir=tmp_path, guard=guard)


async def _server(tmp_path, c):
    srv = ControlServer(c, tmp_path, port=0)
    await srv.start()
    tok = (tmp_path / "desk.token").read_text().strip()
    return srv, srv.http.port, {"Authorization": f"Bearer {tok}"}


def test_bare_controller_has_no_runtime_clear(tmp_path, guard):
    async def go():
        c = _controller(tmp_path, guard)
        assert getattr(c, "clear_runtime_violation", None) is None  # attached by the adapter only
        srv, port, auth = await _server(tmp_path, c)
        try:
            st, _, body = await http(port, "POST", "/runtime/clear", headers=auth, body={})
            assert st == 404 and json.loads(body) == {"ok": False, "error": "not found"}
            assert (await http(port, "GET", "/runtime/clear", headers=auth))[0] == 405
            assert (await http(port, "POST", "/runtime/clear", body={}))[0] == 401
            assert c.state.mode == "off_air"
        finally:
            await srv.stop()

    asyncio.run(go())


def test_attached_clear_maps_state_errors_and_strict_body(tmp_path, guard):
    async def go():
        c = _controller(tmp_path, guard)
        calls = []
        outcome = {"raise": ControlError("the runtime gate is not latched", 409)}

        def clear():
            calls.append(c.state.mode)
            if outcome["raise"] is not None:
                raise outcome["raise"]
            return c.snapshot()

        c.clear_runtime_violation = clear
        srv, port, auth = await _server(tmp_path, c)
        try:
            # Refused before the clear is reached: auth, Origin, method, body shape.
            for headers, payload, want in (
                ({}, {}, 401),
                ({"Authorization": "Bearer wrong"}, {}, 401),
                ({**auth, "Origin": "http://127.0.0.1:9119"}, {}, 403),
                (auth, {"force": True}, 400),
                (auth, [], 400),
                (auth, "clear", 400),
                (auth, None, 400),  # no body must not reach the clear
            ):
                st, _, body = await http(port, "POST", "/runtime/clear", headers=headers, body=payload)
                if want is not None:
                    assert st == want and json.loads(body)["ok"] is False, (headers, payload, st)
            assert calls == []
            assert (await http(port, "GET", "/runtime/clear", headers=auth))[0] == 405
            assert calls == []

            st, _, body = await http(port, "POST", "/runtime/clear", headers=auth, body={})
            assert st == 409 and json.loads(body) == {"ok": False, "error": "the runtime gate is not latched"}
            outcome["raise"] = ControlError("go off air before clearing the runtime latch", 409)
            st, _, body = await http(port, "POST", "/runtime/clear", headers=auth, body={})
            assert st == 409 and json.loads(body)["error"] == "go off air before clearing the runtime latch"

            outcome["raise"] = None
            st, _, body = await http(port, "POST", "/runtime/clear", headers=auth, body={})
            data = json.loads(body)
            assert st == 200 and data["ok"] is True
            assert data["state"]["mode"] == "off_air" and data["state"]["epoch"] == c.state.epoch
            assert set(data["state"]) == set(c.snapshot())
            assert len(calls) == 3
        finally:
            await srv.stop()

    asyncio.run(go())
