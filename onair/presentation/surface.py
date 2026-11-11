"""The public presentation surface: a loopback page that OBS loads as a Browser Source.

Endpoints (GET/HEAD only, ``127.0.0.1`` only, loopback ``Host`` required; reuses the overlay's
``LoopbackHTTPServer``):

- ``/present``             the program page (static, strict CSP: same-origin script/style/SSE, no media)
- ``/present/app.js``, ``/present/present.css``   its static assets
- ``/present/state``       the current PUBLIC program snapshot as JSON (read-only)
- ``/present/events``      Server-Sent Events: the current snapshot on connect, then one per change

There is no write endpoint. Content changes only through the private agent's tools in this process.
A disconnected page shows its standby card, never stale content.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from ..overlay.server import LoopbackHTTPServer, Request, Response, SSEStream, _CLOSE
from .deck import Program

WEB = Path(__file__).resolve().parent / "web"
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; "
       "media-src 'none'; font-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
_STATIC = {"/present": ("index.html", "text/html; charset=utf-8"), "/present/": ("index.html", "text/html; charset=utf-8"),
           "/present/app.js": ("app.js", "text/javascript; charset=utf-8"),
           "/present/present.css": ("present.css", "text/css; charset=utf-8")}


class PresentationSurface:
    def __init__(self, program: Program, port: int = 0) -> None:
        self.program = program
        self.http = LoopbackHTTPServer(self._handle, port=port, name="presentation")
        self._subs: set[asyncio.Queue] = set()
        self.loop: asyncio.AbstractEventLoop | None = None
        self.serving = False

    @property
    def url(self) -> str:
        return self.http.base_url + "/present" if self.serving else ""

    @property
    def clients(self) -> int:
        return len(self._subs)

    async def start(self) -> None:
        await self.http.start()
        self.loop = asyncio.get_running_loop()
        self.serving = True

    async def stop(self) -> None:
        self.serving = False
        for q in list(self._subs):
            q.put_nowait(_CLOSE)
        await self.http.stop()

    def _frame(self) -> tuple[str, str]:
        return ("program", "data: " + json.dumps(self.program.public(), ensure_ascii=False) + "\n\n")

    def broadcast(self) -> None:
        """Push the current snapshot to every connected page. Call on the surface's own loop."""
        frame = self._frame()
        for q in list(self._subs):
            q.put_nowait(frame)

    def _subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        q.put_nowait(self._frame())
        self._subs.add(q)
        return q

    def _unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.discard(q)

    async def _handle(self, req: Request):
        if req.method not in ("GET", "HEAD"):
            return Response.text("method not allowed", 405)
        if req.path in _STATIC:
            name, ctype = _STATIC[req.path]
            # no-store: an OBS Browser Source must never keep an old page, script or stylesheet after an upgrade
            return Response(200, (WEB / name).read_bytes(), ctype, {"Content-Security-Policy": CSP, "Cache-Control": "no-store"})
        if req.path == "/present/state":
            resp = Response.json(self.program.public())
            resp.headers["Content-Security-Policy"] = CSP
            resp.headers["Cache-Control"] = "no-store"
            return resp
        if req.path == "/present/events":
            return SSEStream(self._subscribe, self._unsubscribe, keepalive_s=15.0)
        return Response.text("not found", 404)
