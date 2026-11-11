"""Loopback desk control API (lane: core). SPEC.md section 4, "Control discovery".

At connect the core writes ``<plugin_data_dir>/control.json``::

    {"url": "http://127.0.0.1:<port>", "overlay_url": "http://127.0.0.1:<overlay_port>/overlay",
     "token_file": "desk.token", "pid": <int>}

``desk.token`` (mode 0600, regenerated at every connect) holds a random bearer token. The
dashboard's ``plugin_api.py`` reads both files and proxies the desk routes here. Every request
must carry ``Authorization: Bearer <token>``; any request with an ``Origin`` header is refused
(browsers always send one cross-origin, the dashboard proxy never does), as is a non-loopback
``Host``. The server binds 127.0.0.1 only.

Routes (JSON in, JSON out):

- ``GET /state`` · ``GET /pending`` · ``GET /receipts?limit=N`` (1-500, default 50)
- ``GET /alerts`` · ``POST /alerts`` (partial runtime settings) · ``GET /alerts/recent?limit=N``
- ``POST /mode {"mode"}`` · ``POST /approve {"line_id","epoch"}`` · ``POST /reject {"line_id","epoch"}``
- ``POST /privacy {"on": bool}`` · ``POST /mute {"on": bool}`` (a JSON boolean only; anything
  else is a 400 and changes nothing) · ``POST /stop``
- ``POST /runtime/clear {}`` (an empty JSON object only): the operator's clear for the runtime
  gate's latch, off air only. It calls the controller's ``clear_runtime_violation`` (attached by
  the adapter; a bare controller has none, so the route is a 404). 409 while the show is not off
  air or the gate is not latched. Arming afterwards still runs preflight.
- ``GET /assist``: optional semantic assistance status (provider off/laya/jev, state, counters,
  last connection test). Never carries a key. ``POST /assist/test {}`` (an empty JSON object
  only) runs the provider's harmless connection check (Laya ``GET /health``, no inference; Jev a
  local key/config check, no network). Neither route can approve, reject or air a line, and
  neither accepts an endpoint, host or header from the caller.

Errors are ``{"ok": false, "error": "..."}`` with 400 / 401 / 403 / 404 / 405 / 409.
"""

from __future__ import annotations

import contextlib
import hmac
import json
import logging
import os
import re
import secrets
from pathlib import Path

from .overlay.server import LoopbackHTTPServer, Request, Response
from .stage import ControlError, StageController, _write_private

logger = logging.getLogger(__name__)

CONTROL_FILE = "control.json"
TOKEN_FILE = "desk.token"

_GET_ROUTES = {"/state", "/pending", "/receipts", "/alerts/recent", "/assist"}
_POST_ROUTES = {"/mode", "/approve", "/reject", "/privacy", "/mute", "/stop", "/runtime/clear", "/assist/test", "/host/turn"}
_POST_ROUTES.add("/cut-in")


class ControlServer:
    def __init__(self, controller: StageController, data_dir: Path | None, *, port: int = 0, overlay_url: str = ""):
        self.controller = controller
        self.data_dir = Path(data_dir) if data_dir else None
        self.overlay_url = overlay_url
        self.token = secrets.token_urlsafe(32)
        self.http = LoopbackHTTPServer(self._handle, port=port, name="desk-control")

    @property
    def url(self) -> str:
        return self.http.base_url

    async def start(self) -> None:
        await self.http.start()
        if self.data_dir is not None:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            with contextlib.suppress(OSError):
                os.chmod(self.data_dir, 0o700)
            _write_private(self.data_dir / TOKEN_FILE, self.token + "\n")
            _write_private(
                self.data_dir / CONTROL_FILE,
                json.dumps(
                    {
                        "url": self.url,
                        "overlay_url": self.overlay_url,
                        "token_file": TOKEN_FILE,
                        "pid": os.getpid(),
                    }
                ),
            )

    async def stop(self) -> None:
        await self.http.stop()
        if self.data_dir is None:
            return
        path = self.data_dir / CONTROL_FILE
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
            if current.get("pid") == os.getpid() and current.get("url") == self.url:
                path.unlink()
        except (OSError, ValueError):
            pass

    def _authorized(self, req: Request) -> bool:
        auth = req.headers.get("authorization", "")
        scheme, _, value = auth.partition(" ")
        if scheme.lower() != "bearer" or not value:
            return False
        return hmac.compare_digest(value.strip().encode(), self.token.encode())

    async def _handle(self, req: Request) -> Response:
        if "origin" in req.headers:
            return _err("cross-origin requests are refused", 403)
        if not self._authorized(req):
            return _err("missing or bad desk token", 401)
        path = req.path.rstrip("/") or "/"
        if path == "/alerts" and req.method not in ("GET", "HEAD", "POST"):
            return _err("method not allowed", 405)
        if path in _GET_ROUTES and req.method not in ("GET", "HEAD"):
            return _err("method not allowed", 405)
        if path in _POST_ROUTES and req.method != "POST":
            return _err("method not allowed", 405)
        c = self.controller
        try:
            if path == "/alerts" and req.method in ("GET", "HEAD"):
                return Response.json({"alerts": c.alert_settings()})
            if path == "/alerts/recent":
                try:
                    limit = int((req.query.get("limit") or ["50"])[0])
                except ValueError:
                    return _err("limit must be an integer", 400)
                return Response.json({"alerts": c.recent_alerts(limit)})
            if path == "/state":
                return Response.json(c.snapshot())
            if path == "/assist":
                return Response.json({"assist": c.assist_status()})
            if path == "/pending":
                return Response.json({"epoch": c.state.epoch, "pending": c.pending_lines()})
            if path == "/receipts":
                try:
                    limit = int((req.query.get("limit") or ["50"])[0])
                except ValueError:
                    return _err("limit must be an integer", 400)
                return Response.json({"receipts": c.receipts.tail(max(1, min(500, limit)))})
            if path not in _POST_ROUTES and path != "/alerts":
                return _err("not found", 404)
            # Attached by the adapter in _build, not a StageController method: absent on a bare
            # controller (the fake show, core tests), where there is no runtime gate to clear.
            clear_runtime = getattr(c, "clear_runtime_violation", None) if path == "/runtime/clear" else None
            if path == "/runtime/clear" and not callable(clear_runtime):
                return _err("not found", 404)
            if path in ("/runtime/clear", "/assist/test") and not req.body:
                return _err("body must be an empty JSON object: {}", 400)
            try:
                body = req.json()
            except (ValueError, UnicodeDecodeError):
                return _err("body must be JSON", 400)
            if not isinstance(body, dict):
                return _err("body must be a JSON object", 400)
            if path == "/cut-in":
                if (set(body) != {"epoch", "boot"} or type(body.get("epoch")) is not int
                        or not isinstance(body.get("boot"), str) or not re.fullmatch(r"[0-9a-f]{32}", body["boot"])):
                    return _err('body must carry an integer epoch and stage boot id', 400)
                if body["boot"] != c.voice_boot_id:
                    return Response.json({"ok": False, "reason": "stale_boot"}, 409)
                return Response.json({"ok": True, "state": c.cut_in(body["epoch"])})
            if path == "/host/turn" and "boot" in body:
                boot = body["boot"]
                if not isinstance(boot, str) or not re.fullmatch(r"[0-9a-f]{32}", boot):
                    return _err("invalid stage boot id", 400)
                if boot != c.voice_boot_id:
                    return Response.json({"ok": False, "reason": "stale_boot"}, 409)
                body = {key: value for key, value in body.items() if key != "boot"}
            if path == "/host/turn":
                if (set(body) - {"text", "epoch", "id", "via"} or not isinstance(body.get("text"), str)
                        or type(body.get("epoch")) is not int
                        or ("via" in body and body["via"] != "relay")
                        or ("id" in body and (not isinstance(body["id"], str)
                                              or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", body["id"])) )):
                    return _err("Provide text, an integer epoch, an optional valid id and optional via=relay only.", 400)
                result = c.queue_host_turn(body["text"], body["epoch"], body.get("id"), via=body.get("via", "typed"))
                return Response.json(result, 200 if result["ok"] else 409)
            if path == "/alerts":
                return Response.json({"ok": True, "alerts": c.set_alert_settings(body)})
            if path == "/assist/test":
                if body:
                    # the endpoint is operator configuration only; the desk cannot redirect a test
                    return _err("body must be an empty JSON object: {}", 400)
                result = await c.assist_test()
                return Response.json({"ok": True, "test": result, "assist": c.assist_status()})
            if path == "/mode":
                mode = body.get("mode")
                if not isinstance(mode, str):
                    return _err("'mode' must be a string", 400)
                state = c.set_mode(mode)
            elif path == "/approve":
                state = c.approve(str(body.get("line_id", "")), body.get("epoch"))
            elif path == "/reject":
                state = c.reject(str(body.get("line_id", "")), body.get("epoch"))
            elif path in ("/privacy", "/mute"):
                on = body.get("on")
                if not isinstance(on, bool):
                    # strict: {}, null, strings, numbers or a wrong key never change state
                    return _err('body must carry a JSON boolean: {"on": true} or {"on": false}', 400)
                state = c.set_privacy(on) if path == "/privacy" else c.set_muted(on)
            elif path == "/runtime/clear":
                if body:
                    # strict: the clear takes no arguments, so a stray key never reads as a request
                    return _err("body must be an empty JSON object: {}", 400)
                state = clear_runtime()
            else:  # /stop
                state = c.stop()
            return Response.json({"ok": True, "state": state})
        except ControlError as exc:
            return _err(str(exc), exc.status)


def _err(message: str, status: int) -> Response:
    return Response.json({"ok": False, "error": message}, status)
