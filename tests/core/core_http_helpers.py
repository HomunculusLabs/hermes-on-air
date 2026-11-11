"""Tiny raw-HTTP helpers for the core lane tests (stdlib only)."""

from __future__ import annotations

import asyncio
import json


async def http(
    port: int, method: str, path: str, *, body: object = None, headers: dict | None = None, host: str | None = None
) -> tuple[int, dict, bytes]:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    data = json.dumps(body).encode() if body is not None else b""
    hdrs = {"Host": host or f"127.0.0.1:{port}", "Content-Length": str(len(data)), **(headers or {})}
    req = f"{method} {path} HTTP/1.1\r\n" + "".join(f"{k}: {v}\r\n" for k, v in hdrs.items()) + "\r\n"
    writer.write(req.encode() + data)
    await writer.drain()
    raw = await asyncio.wait_for(reader.read(), timeout=5)
    writer.close()
    head, _, payload = raw.partition(b"\r\n\r\n")
    lines = head.decode("latin-1").split("\r\n")
    status = int(lines[0].split(" ")[1])
    out = {}
    for line in lines[1:]:
        k, _, v = line.partition(":")
        out[k.strip().lower()] = v.strip()
    return status, out, payload


class SSEClient:
    """Reads SSE ``data:`` frames from /events as parsed JSON events."""

    def __init__(self, port: int):
        self.port = port
        self.events: list[dict] = []
        self._task: asyncio.Task | None = None
        self.status: int | None = None
        self.headers: dict = {}

    async def __aenter__(self) -> SSEClient:
        self._reader, self._writer = await asyncio.open_connection("127.0.0.1", self.port)
        self._writer.write(f"GET /events HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\n\r\n".encode())
        await self._writer.drain()
        head = await asyncio.wait_for(self._reader.readuntil(b"\r\n\r\n"), timeout=5)
        lines = head.decode().split("\r\n")
        self.status = int(lines[0].split(" ")[1])
        for line in lines[1:]:
            k, _, v = line.partition(":")
            if k:
                self.headers[k.strip().lower()] = v.strip()
        self._task = asyncio.create_task(self._pump())
        return self

    async def _pump(self) -> None:
        buf = b""
        while True:
            chunk = await self._reader.read(4096)
            if not chunk:
                return
            buf += chunk
            while b"\n\n" in buf:
                frame, buf = buf.split(b"\n\n", 1)
                for line in frame.decode().split("\n"):
                    if line.startswith("data: "):
                        self.events.append(json.loads(line[6:]))

    async def wait_for(self, pred, timeout: float = 3.0) -> dict:
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            for ev in self.events:
                if pred(ev):
                    return ev
            await asyncio.sleep(0.01)
        raise AssertionError(f"no matching SSE event; got {self.events}")

    def of(self, kind: str) -> list[dict]:
        return [e for e in self.events if e.get("type") == kind]

    async def __aexit__(self, *exc) -> None:
        if self._task:
            self._task.cancel()
        self._writer.close()
