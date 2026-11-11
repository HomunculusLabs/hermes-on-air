"""Speech for released lines (lane: core). SPEC.md section 2, "TTS order".

The guard runs first; only a line the guard released (and, in approval mode, the operator
approved) is ever synthesized. Synthesis goes through Hermes' own configured TTS by the
documented plugin route ``ctx.dispatch_tool("text_to_speech", {...})``: whatever provider the
profile configured (``tts.provider``), with no core-module imports. When no plugin context or no
working TTS is available the show runs captions-only; the overlay's ``audio`` events simply
don't happen.

Clips are held in memory by the stage controller and served once released at
``/audio/<id>.wav``. The route keeps the SPEC name; the ``Content-Type`` reflects the bytes the
provider actually produced (WAV, MP3, Ogg or FLAC), which browsers play regardless of the
extension.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import struct
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Clip:
    data: bytes
    mime: str
    duration_s: float


class TTSEngine(Protocol):
    available: bool

    async def synth(self, text: str) -> Clip | None: ...


class CaptionsOnly:
    """No speech: every line airs as a caption only."""

    available = False
    name = "captions-only"

    async def synth(self, text: str) -> Clip | None:
        return None


class HermesTTS:
    """Hermes' configured TTS through ``ctx.dispatch_tool("text_to_speech", ...)``.

    The tool call runs in a worker thread. If it outlives ``timeout_s`` the line airs captions-only
    and the job is marked abandoned: when the thread finally returns, its output file is deleted
    instead of read, so a slow provider never leaks clips into ``clips/``. Leftovers from a crash
    are swept when the engine starts.
    """

    name = "hermes-tts"

    def __init__(self, dispatch_tool: Callable[[str, dict], Any], out_dir: Path, *, timeout_s: float = 30.0):
        self._dispatch = dispatch_tool
        self._out = Path(out_dir)
        self._timeout = timeout_s
        self.available = True
        self.failures = 0
        self.abandoned = 0
        self._sweep()

    def _sweep(self) -> None:
        with contextlib.suppress(OSError):
            for fp in self._out.glob("clip-*"):
                with contextlib.suppress(OSError):
                    fp.unlink()

    def _run(self, text: str, target: Path, abandoned: threading.Event) -> Clip | None:
        self._out.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(OSError):
            os.chmod(self._out, 0o700)
        paths: list[str] = [str(target)]
        try:
            raw = self._dispatch("text_to_speech", {"text": text, "output_path": str(target)})
            data = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
            if isinstance(data, dict):
                paths += [str(p) for p in (data.get("file_paths") or [data.get("file_path")]) if p]
            if abandoned.is_set():
                return None
            if not isinstance(data, dict) or data.get("success") is False or data.get("error"):
                logger.warning(
                    "on-air TTS failed: %s",
                    str((data or {}).get("error", "unknown"))[:200] if isinstance(data, dict) else "bad result",
                )
                return None
            blobs: list[bytes] = []
            for p in dict.fromkeys(paths[1:]):
                with contextlib.suppress(OSError):
                    blobs.append(Path(p).read_bytes())
            if not blobs:
                return None
            mime = sniff_mime(blobs[0])
            body = b"".join(blobs) if mime == "audio/mpeg" else blobs[0]
            return Clip(body, mime, audio_duration(body, mime) or estimate_speech_seconds(text))
        finally:
            for p in dict.fromkeys(paths):  # never leave a clip behind, read or abandoned
                with contextlib.suppress(OSError):
                    Path(p).unlink()

    async def synth(self, text: str) -> Clip | None:
        target = self._out / f"clip-{uuid.uuid4().hex}.mp3"
        abandoned = threading.Event()
        try:
            return await asyncio.wait_for(asyncio.to_thread(self._run, text, target, abandoned), timeout=self._timeout)
        except Exception as exc:
            abandoned.set()  # the worker deletes its output when it returns
            with contextlib.suppress(OSError):
                target.unlink()
            self.failures += 1
            if isinstance(exc, asyncio.TimeoutError):
                self.abandoned += 1
            logger.warning("on-air TTS error: %s", type(exc).__name__)
            return None


# --------------------------------------------------------------------------------------------
# audio helpers (pure)


def sniff_mime(data: bytes) -> str:
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "audio/wav"
    if data[:3] == b"ID3" or (len(data) > 1 and data[0] == 0xFF and (data[1] & 0xE0) == 0xE0):
        return "audio/mpeg"
    if data[:4] == b"OggS":
        return "audio/ogg"
    if data[:4] == b"fLaC":
        return "audio/flac"
    return "application/octet-stream"


def wav_duration(data: bytes) -> float | None:
    if len(data) < 44 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        return None
    pos, byte_rate = 12, 0
    while pos + 8 <= len(data):
        cid, size = data[pos : pos + 4], struct.unpack("<I", data[pos + 4 : pos + 8])[0]
        if cid == b"fmt " and size >= 16:
            byte_rate = struct.unpack("<I", data[pos + 16 : pos + 20])[0]
        elif cid == b"data":
            if byte_rate <= 0:
                return None
            return min(size, len(data) - pos - 8) / byte_rate
        pos += 8 + size + (size & 1)
    return None


_MP3_BITRATES = {
    (3, 1): [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320],  # MPEG1 L3
    (2, 1): [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160],  # MPEG2/2.5 L3
}


def mp3_duration(data: bytes) -> float | None:
    start = 0
    if data[:3] == b"ID3" and len(data) >= 10:
        start = 10 + ((data[6] & 0x7F) << 21 | (data[7] & 0x7F) << 14 | (data[8] & 0x7F) << 7 | (data[9] & 0x7F))
    for i in range(start, min(len(data) - 4, start + 4096)):
        if data[i] == 0xFF and (data[i + 1] & 0xE0) == 0xE0:
            version = (data[i + 1] >> 3) & 0x3  # 3 = MPEG1, 2 = MPEG2, 0 = MPEG2.5
            layer = (data[i + 1] >> 1) & 0x3  # 1 = Layer III
            if layer != 1 or version == 1:
                continue
            idx = (data[i + 2] >> 4) & 0xF
            table = _MP3_BITRATES[(3, 1)] if version == 3 else _MP3_BITRATES[(2, 1)]
            if not 0 < idx < 15:
                continue
            kbps = table[idx]
            return (len(data) - i) * 8 / (kbps * 1000)
    return None


def audio_duration(data: bytes, mime: str) -> float | None:
    try:
        if mime == "audio/wav":
            return wav_duration(data)
        if mime == "audio/mpeg":
            return mp3_duration(data)
    except Exception:
        return None
    return None


def estimate_speech_seconds(text: str) -> float:
    """Rough spoken length: ~2.7 words per second plus a short lead-in."""
    return 0.4 + len((text or "").split()) / 2.7


def silent_wav(seconds: float = 0.25, rate: int = 8000) -> bytes:
    """A tiny valid mono 16-bit WAV of silence (tests and the fake show)."""
    n = int(seconds * rate)
    data = b"\x00\x00" * n
    fmt = struct.pack("<HHIIHH", 1, 1, rate, rate * 2, 2, 16)
    return (
        b"RIFF"
        + struct.pack("<I", 36 + len(data))
        + b"WAVE"
        + b"fmt "
        + struct.pack("<I", 16)
        + fmt
        + b"data"
        + struct.pack("<I", len(data))
        + data
    )


class FakeTTS:
    """Deterministic engine for tests and the fake show: a short silent WAV per line."""

    available = True
    name = "fake"

    def __init__(self, seconds: float = 0.25, delay_s: float = 0.0):
        self.seconds = seconds
        self.delay_s = delay_s
        self.calls: list[str] = []

    async def synth(self, text: str) -> Clip | None:
        self.calls.append(text)
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        data = silent_wav(self.seconds)
        return Clip(data, "audio/wav", self.seconds)
