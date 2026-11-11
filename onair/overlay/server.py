"""Loopback overlay server (lane: core). Contract: SPEC.md section 4.

Stdlib asyncio only (aiohttp is not a Hermes core dependency, only an extra). Binds
``127.0.0.1`` and nothing else.

- ``GET /overlay`` (and ``/overlay/<file>``) serves the static page from ``onair/overlay/web/``
  under a strict Content-Security-Policy: same-origin scripts, styles, media and SSE only; no
  inline script, no external fetches, no framing.
- ``GET /events`` is Server-Sent Events. Every event is one JSON object with ``type`` and a
  monotonically increasing ``seq``; shapes are built by the ``ev_*`` helpers below and nothing
  else is ever published. A new subscriber first receives the current ``state``. There is no
  replay: reconnecting clients never receive old captions.
- ``GET /audio/<id>.wav`` serves a released clip; 404 after a privacy cut, stop or cancel.

Requests whose ``Host`` header is not a loopback name for this port are refused (DNS-rebinding
defence), and so is any method other than GET/HEAD.

``LoopbackHTTPServer`` is the tiny HTTP/1.1 core shared with the desk control API.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import mimetypes
import os
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

logger = logging.getLogger(__name__)

WEB_DIR = Path(__file__).resolve().parent / "web"
CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; "
    "media-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)
_SECURITY_HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Resource-Policy": "same-origin",
    "X-Frame-Options": "DENY",
    "Cache-Control": "no-store",
}
_MAX_HEADER_BYTES = 16 * 1024
_MAX_BODY_BYTES = 64 * 1024
_STATIC_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".webp": "image/webp",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
    ".woff": "font/woff",
    ".ttf": "font/ttf",
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".ogg": "audio/ogg",
    ".txt": "text/plain; charset=utf-8",
}
_REASONS = {
    200: "OK",
    204: "No Content",
    302: "Found",
    400: "Bad Request",
    401: "Unauthorized",
    403: "Forbidden",
    404: "Not Found",
    405: "Method Not Allowed",
    409: "Conflict",
    413: "Payload Too Large",
    421: "Misdirected Request",
    500: "Internal Server Error",
    503: "Service Unavailable",
}


# ============================================================================================
# HTTP core


@dataclass
class Request:
    method: str
    path: str
    query: dict[str, list[str]]
    headers: dict[str, str]
    body: bytes = b""

    def json(self) -> Any:
        if not self.body:
            return {}
        return json.loads(self.body.decode("utf-8"))


@dataclass
class Response:
    status: int = 200
    body: bytes = b""
    content_type: str = "text/plain; charset=utf-8"
    headers: dict[str, str] = field(default_factory=dict)

    @classmethod
    def json(cls, data: Any, status: int = 200) -> Response:
        return cls(status, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json")

    @classmethod
    def text(cls, text: str, status: int = 200) -> Response:
        return cls(status, text.encode("utf-8"))


SCENE_CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; "
             "img-src 'self' blob: data:; connect-src 'self' blob: data:; "
             "worker-src 'self' blob: data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
_SCENE_TYPES = {key: _type for key, _type in _STATIC_TYPES.items() if key in
                {'.html', '.js', '.mjs', '.css', '.json', '.png', '.jpg', '.jpeg', '.webp'}}
_SCENE_TYPES.update({'.ktx2': 'image/ktx2', '.hdr': 'image/vnd.radiance', '.glb': 'model/gltf-binary',
                     '.gltf': 'model/gltf+json', '.bin': 'application/octet-stream',
                     '.vrm': 'model/gltf-binary', '.wasm': 'application/wasm'})


@dataclass
class FileResponse:
    file: Any
    size: int
    content_type: str
    status: int = 200
    headers: dict[str, str] = field(default_factory=lambda: {'Content-Security-Policy': SCENE_CSP})


@dataclass
class SSEStream:
    """Returned by a handler to hold the connection open as an event stream."""

    subscribe: Callable[[], asyncio.Queue]
    unsubscribe: Callable[[asyncio.Queue], None]
    keepalive_s: float = 15.0


Handler = Callable[[Request], Awaitable["Response | FileResponse | SSEStream"]]


class LoopbackHTTPServer:
    """Minimal HTTP/1.1 server bound to 127.0.0.1 (one request per connection, SSE excepted)."""

    def __init__(self, handler: Handler, *, port: int = 0, name: str = "http"):
        self._handler = handler
        self._requested_port = int(port)
        self._server: asyncio.AbstractServer | None = None
        self._name = name
        self._streams: set[asyncio.Task] = set()
        self.port = 0

    @property
    def host(self) -> str:
        return "127.0.0.1"

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    async def start(self) -> None:
        self._server = await asyncio.start_server(
            self._client, host="127.0.0.1", port=self._requested_port, reuse_address=True
        )
        sock = self._server.sockets[0]
        self.port = sock.getsockname()[1]
        logger.info("on-air %s listening on 127.0.0.1:%s", self._name, self.port)

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            for task in list(self._streams):
                task.cancel()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._server.wait_closed(), timeout=2.0)
            self._server = None

    def _allowed_host(self, host: str) -> bool:
        host = (host or "").strip().lower()
        return host in {
            f"127.0.0.1:{self.port}",
            f"localhost:{self.port}",
            "127.0.0.1",
            "localhost",
            f"[::1]:{self.port}",
        }

    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        if task is not None:
            self._streams.add(task)
        try:
            req = await asyncio.wait_for(self._read_request(reader), timeout=10.0)
            if req is None:
                return
            if not self._allowed_host(req.headers.get("host", "")):
                await self._write(writer, Response.text("misdirected request", 421), head=req.method == "HEAD")
                return
            try:
                result = await self._handler(req)
            except Exception:
                logger.exception("on-air %s handler error", self._name)
                result = Response.text("internal error", 500)
            if isinstance(result, SSEStream):
                await self._stream(writer, result)
            else:
                await self._write(writer, result, head=req.method == "HEAD")
        except (TimeoutError, ConnectionError, asyncio.IncompleteReadError):
            pass
        except _BadRequest as exc:
            with contextlib.suppress(Exception):
                await self._write(writer, Response.text(str(exc) or "bad request", exc.status))
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.debug("on-air %s connection error", self._name, exc_info=True)
        finally:
            if task is not None:
                self._streams.discard(task)
            with contextlib.suppress(Exception):
                writer.close()

    async def _read_request(self, reader: asyncio.StreamReader) -> Request | None:
        try:
            head = await reader.readuntil(b"\r\n\r\n")
        except asyncio.LimitOverrunError:
            raise _BadRequest("headers too large", 400) from None
        except asyncio.IncompleteReadError:
            return None
        if len(head) > _MAX_HEADER_BYTES:
            raise _BadRequest("headers too large", 400)
        lines = head.decode("latin-1").split("\r\n")
        parts = lines[0].split(" ")
        if len(parts) != 3 or not parts[2].startswith("HTTP/1."):
            raise _BadRequest("bad request line", 400)
        method, target = parts[0].upper(), parts[1]
        headers: dict[str, str] = {}
        for line in lines[1:]:
            if not line:
                continue
            k, sep, v = line.partition(":")
            if not sep:
                raise _BadRequest("bad header", 400)
            headers[k.strip().lower()] = v.strip()
        body = b""
        if "transfer-encoding" in headers:
            raise _BadRequest("chunked bodies are not supported", 400)
        length = headers.get("content-length")
        if length:
            if not length.isdigit() or int(length) > _MAX_BODY_BYTES:
                raise _BadRequest("body too large", 413)
            body = await reader.readexactly(int(length))
        split = urlsplit(target)
        return Request(method, unquote(split.path), parse_qs(split.query), headers, body)

    async def _write(self, writer: asyncio.StreamWriter, resp: Response | FileResponse, *, head: bool = False) -> None:
        hdrs = {
            **_SECURITY_HEADERS,
            **resp.headers,
            "Content-Type": resp.content_type,
            "Content-Length": str(resp.size if isinstance(resp, FileResponse) else len(resp.body)),
            "Connection": "close",
        }
        out = [f"HTTP/1.1 {resp.status} {_REASONS.get(resp.status, 'Status')}"]
        out += [f"{k}: {v}" for k, v in hdrs.items()]
        if isinstance(resp, FileResponse):
            try:
                writer.write(("\r\n".join(out) + "\r\n\r\n").encode("latin-1"))
                if not head:
                    remaining = resp.size
                    while remaining:
                        chunk = resp.file.read(min(64 * 1024, remaining))
                        if not chunk:
                            break
                        writer.write(chunk)
                        remaining -= len(chunk)
                        await writer.drain()
                else:
                    await writer.drain()
            finally:
                resp.file.close()
        else:
            writer.write(("\r\n".join(out) + "\r\n\r\n").encode("latin-1") + (b"" if head else resp.body))
            await writer.drain()

    async def _stream(self, writer: asyncio.StreamWriter, sse: SSEStream) -> None:
        hdrs = {
            **_SECURITY_HEADERS,
            "Content-Type": "text/event-stream; charset=utf-8",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
        writer.write(("HTTP/1.1 200 OK\r\n" + "".join(f"{k}: {v}\r\n" for k, v in hdrs.items()) + "\r\n").encode())
        await writer.drain()
        q = sse.subscribe()
        try:
            while True:
                try:
                    item = await asyncio.wait_for(q.get(), timeout=sse.keepalive_s)
                except TimeoutError:
                    item = ("keepalive", ": keepalive\n\n")
                if item is _CLOSE:
                    break
                writer.write(item[1].encode("utf-8"))
                await writer.drain()
        finally:
            sse.unsubscribe(q)


class _BadRequest(Exception):
    def __init__(self, msg: str, status: int = 400):
        super().__init__(msg)
        self.status = status


_CLOSE: Any = object()  # sentinel pushed to a subscriber queue to end its stream


# ============================================================================================
# events (SPEC section 4 shapes; nothing else is ever published)


def ev_caption(cid: str, speaker: str, text: str, ttl_ms: int) -> dict:
    return {"type": "caption", "id": cid, "speaker": speaker, "text": text, "ttl_ms": int(ttl_ms)}


def ev_caption_clear() -> dict:
    return {"type": "caption_clear"}


def ev_chat(cid: str, name: str, text: str) -> dict:
    return {"type": "chat", "id": cid, "name": name, "text": text}


def ev_audio(aid: str, url: str, caption_id: str) -> dict:
    return {"type": "audio", "id": aid, "url": url, "caption_id": caption_id}


def ev_state(live: bool, mode: str, privacy: bool, muted: bool) -> dict:
    return {"type": "state", "live": bool(live), "mode": mode, "privacy": bool(privacy), "muted": bool(muted)}


def ev_alert(aid: str, kind: str, name: str, amount: int, months: int, tier: str, ttl_ms: int) -> dict:
    """A support alert. ``name`` is display-guarded ("someone" when the guard fails); there is no
    viewer message field, ever. Sound is the overlay's to play, and ``state.muted`` silences it."""
    return {
        "type": "alert",
        "id": aid,
        "kind": kind,
        "name": name,
        "amount": int(amount),
        "months": int(months),
        "tier": str(tier or ""),
        "ttl_ms": int(ttl_ms),
    }


_EVENT_KEYS = {
    "caption": ("type", "id", "speaker", "text", "ttl_ms"),
    "caption_clear": ("type",),
    "chat": ("type", "id", "name", "text"),
    "audio": ("type", "id", "url", "caption_id"),
    "alert": ("type", "id", "kind", "name", "amount", "months", "tier", "ttl_ms"),
    "state": ("type", "live", "mode", "privacy", "muted"),
}
_DROPPABLE_ON_URGENT = {"caption", "chat", "audio", "alert"}


class EventHub:
    """Fan-out of overlay events to SSE subscribers, with a global ``seq``.

    Each subscriber queue holds ``(type, frame)`` tuples, so an urgent event (privacy cut, stop)
    can drop the caption, chat and audio frames a slow client has not received yet.
    """

    def __init__(self, *, max_queue: int = 256):
        self._subs: set[asyncio.Queue] = set()
        self._seq = 0
        self._max = max_queue
        self.snapshot: Callable[[], dict] = lambda: ev_state(False, "off_air", False, False)
        self.history: list[dict] = []  # recent events for tests and the desk; never replayed to clients
        self._listeners: list[Callable[[dict], None]] = []

    @property
    def seq(self) -> int:
        return self._seq

    def add_listener(self, fn: Callable[[dict], None]) -> None:
        self._listeners.append(fn)

    def _stamp(self, event: dict) -> dict:
        keys = _EVENT_KEYS.get(event.get("type", ""))
        if keys is None or set(event) != set(keys):
            raise ValueError(f"not a SPEC overlay event: {sorted(event)}")
        self._seq += 1
        ordered = {"type": event["type"], "seq": self._seq}
        ordered.update({k: event[k] for k in keys if k != "type"})
        return ordered

    @staticmethod
    def _frame(ev: dict) -> str:
        return f"id: {ev['seq']}\ndata: {json.dumps(ev, ensure_ascii=False, separators=(',', ':'))}\n\n"

    def publish(self, event: dict, *, urgent: bool = False) -> dict:
        """Stamp and fan out one event. ``urgent`` first drops undelivered caption, chat and audio
        frames from every subscriber (a privacy cut must not trail old frames)."""
        ev = self._stamp(event)
        item = (ev["type"], self._frame(ev))
        for q in list(self._subs):
            if urgent:
                self._purge(q)
            if q.qsize() >= self._max:
                self._drop(q)  # a stalled client: drop it rather than buffer without bound
                continue
            q.put_nowait(item)
        self.history.append(ev)
        del self.history[:-200]
        for fn in list(self._listeners):
            with contextlib.suppress(Exception):
                fn(ev)
        return ev

    @staticmethod
    def _purge(q: asyncio.Queue) -> None:
        kept = []
        while not q.empty():
            item = q.get_nowait()
            if item is _CLOSE or item[0] not in _DROPPABLE_ON_URGENT:
                kept.append(item)
        for item in kept:
            q.put_nowait(item)

    def _drop(self, q: asyncio.Queue) -> None:
        self._subs.discard(q)
        while not q.empty():
            q.get_nowait()
        q.put_nowait(_CLOSE)

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        snap = self._stamp(self.snapshot())
        q.put_nowait((snap["type"], self._frame(snap)))
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.discard(q)

    def close_all(self) -> None:
        for q in list(self._subs):
            self._drop(q)

    def subscriber_count(self) -> int:
        return len(self._subs)


# ============================================================================================
# overlay server

ClipLookup = Callable[[str], tuple[bytes, str] | None]


class OverlayServer:
    def __init__(self, hub: EventHub, clip_lookup: ClipLookup, *, port: int = 18790, web_dir: Path | None = None,
                 scene_dir: Path | None = None):
        self.hub = hub
        self._clip_lookup = clip_lookup
        self.web_dir = (web_dir or WEB_DIR).resolve()
        home = Path(os.environ.get('HERMES_HOME') or Path.home() / '.hermes').expanduser()
        default_scene = home / 'plugin-data' / 'on-air' / 'scene'
        self.scene_dir = Path(scene_dir) if scene_dir is not None else default_scene
        self._scene_anchor = home if self.scene_dir == default_scene else self.scene_dir.parent
        self._scene_parts = ('plugin-data', 'on-air', 'scene') if self.scene_dir == default_scene else (self.scene_dir.name,)
        self.http = LoopbackHTTPServer(self._handle, port=port, name="overlay")

    @property
    def port(self) -> int:
        return self.http.port

    @property
    def overlay_url(self) -> str:
        return f"{self.http.base_url}/overlay"

    async def start(self) -> None:
        await self.http.start()

    async def stop(self) -> None:
        self.hub.close_all()
        await self.http.stop()

    async def _handle(self, req: Request) -> Response | FileResponse | SSEStream:
        if req.method not in ("GET", "HEAD"):
            return Response(405, b"method not allowed", headers={"Allow": "GET, HEAD"})
        path = req.path
        if path == "/":
            return Response(302, b"", headers={"Location": "/overlay"})
        if path == "/events":
            return SSEStream(self.hub.subscribe, self.hub.unsubscribe)
        m = re.fullmatch(r"/audio/([A-Za-z0-9_-]{1,64})\.wav", path)
        if m:
            clip = self._clip_lookup(m.group(1))
            if clip is None:
                return Response.text("not found", 404)
            data, mime = clip
            return Response(200, data, mime, headers={"Accept-Ranges": "none"})
        if path.startswith('/scene/') or path == '/scene':
            if path == '/scene':
                index = self._scene_static('index.html')
                if isinstance(index, FileResponse):
                    index.file.close()
                    return Response(302, b'', headers={'Location': '/scene/', 'Content-Security-Policy': SCENE_CSP})
                index.headers['Content-Security-Policy'] = SCENE_CSP
                return index
            if path == '/scene/avatar-status.json':
                index = self._scene_static('index.html')
                if isinstance(index, FileResponse):
                    index.file.close()
                    avatar = self._scene_static('avatar.vrm')
                    present = isinstance(avatar, FileResponse)
                    if present:
                        avatar.file.close()
                    result = Response.json({'avatar': present})
                else:
                    result = index
            else:
                result = self._scene_static(path[len('/scene/'):] or 'index.html')
            result.headers['Content-Security-Policy'] = SCENE_CSP
            return result
        if path in ("/overlay", "/overlay/"):
            return self._static("index.html")
        if path.startswith("/overlay/"):
            return self._static(path[len("/overlay/") :])
        return Response.text("not found", 404)

    def _scene_static(self, rel: str) -> Response | FileResponse:
        parts = rel.split('/')
        ctype = _SCENE_TYPES.get(Path(rel).suffix.lower())
        if not ctype or '\\' in rel or any(not p or p.startswith('.') for p in parts):
            return Response.text('not found', 404)

        from ..scene_paths import open_scene_file

        def open_file(names: list[str]):
            return open_scene_file(self._scene_anchor, self._scene_parts + tuple(names))
        try:
            if rel != 'index.html':
                with open_file(['index.html']):
                    pass
            stream = open_file(parts)
            return FileResponse(stream, os.fstat(stream.fileno()).st_size, ctype)
        except (OSError, ValueError):
            return Response.text('not found', 404)

    def _static(self, rel: str) -> Response:
        if (
            not rel
            or rel.startswith("/")
            or "\\" in rel
            or any(p in ("", ".", "..") for p in rel.split("/"))
            or any(p.startswith(".") for p in rel.split("/"))
        ):
            return Response.text("not found", 404)
        target = (self.web_dir / rel).resolve()
        try:
            target.relative_to(self.web_dir)
        except ValueError:
            return Response.text("not found", 404)
        ctype = _STATIC_TYPES.get(target.suffix.lower())
        if ctype is None or not target.is_file():
            return Response.text("not found", 404)
        try:
            data = target.read_bytes()
        except OSError:
            return Response.text("not found", 404)
        return Response(200, data, ctype)


def guess_mime(path: str) -> str:
    return mimetypes.guess_type(path)[0] or "application/octet-stream"
