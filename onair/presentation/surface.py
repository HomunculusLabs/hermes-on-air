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
from urllib.parse import urlsplit
from typing import Callable

from ..overlay.server import LoopbackHTTPServer, Request, Response, SSEStream, _CLOSE
from ..web_build import capture
from .deck import Program

WEB = Path(__file__).resolve().parent / "web"
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; "
       "media-src 'none'; font-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
_STATIC = {"/present": ("index.html", "text/html; charset=utf-8"), "/present/": ("index.html", "text/html; charset=utf-8"),
           "/present/app.js": ("app.js", "text/javascript; charset=utf-8"),
           "/present/present.css": ("present.css", "text/css; charset=utf-8")}


class PresentationSurface:
    def __init__(self, program: Program, port: int = 0, overlay_url: Callable[[], str] | None = None) -> None:
        self.program = program
        self._overlay_url = overlay_url
        self._trusted_overlay: str | None = None
        self._privacy_writer: asyncio.StreamWriter | None = None
        self._privacy_task: asyncio.Task | None = None
        self.http = LoopbackHTTPServer(self._handle, port=port, name="presentation")
        self._subs: set[asyncio.Queue] = set()
        self.loop: asyncio.AbstractEventLoop | None = None
        self.serving = False
        self.build = ''
        self._assets: dict[str, bytes] = {}

    @property
    def url(self) -> str:
        return self.http.base_url + "/present" if self.serving else ""

    @property
    def clients(self) -> int:
        return len(self._subs)

    async def start(self) -> None:
        self.build, self._assets = capture(WEB, ('index.html', 'app.js', 'present.css'))
        await self.http.start()
        self.loop = asyncio.get_running_loop()
        self.serving = True
        if self._overlay_url is not None:
            self._privacy_task = asyncio.create_task(self._watch_privacy())

    def enforce_source(self) -> None:
        """A changed overlay cannot inherit the previous overlay's privacy proof."""
        try:
            current = self._overlay_url() if self._overlay_url else None
        except Exception:  # noqa: BLE001 - missing proof is always private
            current = None
        if not current or current != self._trusted_overlay:
            if self._trusted_overlay:
                self._trusted_overlay = None
                writer = self._privacy_writer
                if writer is not None and self.loop and self.serving:
                    self.loop.call_soon_threadsafe(writer.close)
            hidden = self.program.work_visibility(False)
            standby_cleared = self.program.technical_standby(False)
            if (hidden or standby_cleared) and self.loop and self.serving:
                self.loop.call_soon_threadsafe(lambda: self.broadcast(urgent=True))

    def snapshot(self) -> dict:
        self.enforce_source()
        public = self.program.public()
        if self.build:
            public['build'] = self.build
        return public

    def _visibility(self, visible: bool, *, standby: bool | None = None) -> None:
        changed_visibility = self.program.work_visibility(visible)
        changed_standby = self.program.technical_standby(standby) if standby is not None else False
        if changed_visibility or changed_standby:
            self.broadcast(urgent=not visible)

    async def _watch_privacy(self) -> None:
        """Follow only the configured loopback overlay; no work is visible on loss of proof."""
        while self.serving:
            writer = None
            try:
                url = self._overlay_url()
                parsed = urlsplit(url)
                if parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or not parsed.port or parsed.username:
                    raise ValueError('overlay is not a loopback HTTP source')
                reader, writer = await asyncio.wait_for(asyncio.open_connection('127.0.0.1', parsed.port, limit=65536), 2)
                self._privacy_writer = writer
                writer.write(f'GET /events HTTP/1.1\r\nHost: 127.0.0.1:{parsed.port}\r\n\r\n'.encode())
                await writer.drain()
                headers = await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'), 2)
                if not headers.startswith(b'HTTP/1.1 200 ') or b'text/event-stream' not in headers.lower():
                    raise ValueError('not an event stream')
                last_frame = asyncio.get_running_loop().time()
                while self.serving:
                    if self._overlay_url() != url:
                        break
                    try:
                        line = await asyncio.wait_for(reader.readline(), 1)
                    except TimeoutError:
                        if asyncio.get_running_loop().time() - last_frame > 25:
                            break
                        continue
                    if not line:
                        break
                    last_frame = asyncio.get_running_loop().time()
                    if line.startswith(b'data: '):
                        event = json.loads(line[6:])
                        if not isinstance(event, dict):
                            raise ValueError('invalid event')
                        if event.get('type') == 'state':
                            live, privacy, mode = event.get('live'), event.get('privacy'), event.get('mode')
                            valid = type(live) is bool and type(privacy) is bool and mode in ('off_air', 'paused', 'approval', 'autonomous')
                            valid = valid and live == (mode in ('approval', 'autonomous') and not privacy)
                            source_ok = self._overlay_url() == url
                            visible = bool(valid and live and not privacy and source_ok)
                            standby = event.get('standby')
                            technical = (valid and source_ok and mode in ('paused', 'off_air')
                                         and isinstance(standby, dict) and standby == {
                                             'active': True, 'reason': 'technical_difficulties'}
                                         and type(standby.get('active')) is bool)
                            self._trusted_overlay = url if valid and source_ok else None
                            # Loss or a non-live state cannot prove that the owner resumed.
                            # Keep the fixed card until a valid live state arrives.
                            self._visibility(visible, standby=True if technical else False if visible else None)
            except (OSError, ValueError, TypeError, KeyError, asyncio.IncompleteReadError, TimeoutError):
                pass
            finally:
                self._trusted_overlay = None
                self._privacy_writer = None
                self._visibility(False)
                if writer is not None:
                    writer.close()
                    try:
                        await writer.wait_closed()
                    except OSError:
                        pass
            if self.serving:
                await asyncio.sleep(0.5)

    async def stop(self) -> None:
        self.serving = False
        if self._privacy_task is not None:
            self._privacy_task.cancel()
            try:
                await self._privacy_task
            except asyncio.CancelledError:
                pass
            self._privacy_task = None
        self._visibility(False)
        for q in list(self._subs):
            q.put_nowait(_CLOSE)
        await self.http.stop()

    def _frame(self) -> tuple[str, str]:
        return ("program", "data: " + json.dumps(self.snapshot(), ensure_ascii=False) + "\n\n")

    def broadcast(self, urgent: bool = False) -> None:
        """Push the current snapshot to connected pages; a cut discards queued frames."""
        frame = self._frame()
        for q in list(self._subs):
            if urgent:
                while not q.empty():
                    q.get_nowait()
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
            return Response(200, self._assets[name], ctype, {"Content-Security-Policy": CSP, "Cache-Control": "no-store"})
        if req.path == "/present/state":
            resp = Response.json(self.snapshot())
            resp.headers["Content-Security-Policy"] = CSP
            resp.headers["Cache-Control"] = "no-store"
            return resp
        if req.path == "/present/events":
            return SSEStream(self._subscribe, self._unsubscribe, keepalive_s=15.0)
        return Response.text("not found", 404)
