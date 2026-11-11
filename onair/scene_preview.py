"""Private synthetic scene preview; the scene never plays audio."""
from __future__ import annotations

import asyncio
import contextlib
import io
import math
import struct
import wave
from pathlib import Path

from .overlay.server import (EventHub, FileResponse, OverlayServer, ev_alert, ev_audio, ev_caption,
                             ev_caption_clear, ev_chat, ev_state)


def synthetic_voice(duration: float) -> bytes:
    """A deterministic, speech-like test signal generated without an asset file."""
    rate = 8000
    frames = bytearray()
    for i in range(int(rate * max(.02, min(duration, 30)))):
        t = i / rate
        phase = t % .42
        amp = max(0.0, math.sin(math.pi * min(phase / .29, 1))) if phase < .29 else 0
        tone = math.sin(2 * math.pi * 170 * t) + .35 * math.sin(2 * math.pi * 510 * t)
        frames.extend(struct.pack('<h', int(10000 * amp * tone)))
    out = io.BytesIO()
    with wave.open(out, 'wb') as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(frames)
    return out.getvalue()


class ScenePreview:
    def __init__(self, scene_dir: Path, *, interval: float = 1.2):
        self.hub = EventHub()
        self.state = ev_state(False, 'off_air', False, True)
        self.hub.snapshot = lambda: self.state
        self.clip = synthetic_voice(interval)
        self.server = OverlayServer(self.hub, lambda _: (self.clip, 'audio/wav'), port=0, scene_dir=scene_dir)
        self.interval = interval
        self.task = None

    async def start(self):
        index = self.server._scene_static('index.html')
        if not isinstance(index, FileResponse):
            raise ValueError('No scene found in this home; run hermes onair scene first. Scene paths with symlinks are not served.')
        index.file.close()
        await self.server.start()
        self.task = asyncio.create_task(self._cycle())

    async def _cycle(self):
        def state(live, privacy=False):
            self.state = ev_state(live, 'approval' if live else 'off_air', privacy, not live)
            self.hub.publish(self.state)

        while True:
            state(False)
            await asyncio.sleep(self.interval)
            state(True)
            await asyncio.sleep(self.interval)
            self.hub.publish(ev_caption('preview', 'Co-host', 'This is a private scene preview.',
                                        int(self.interval * 1000)))
            self.hub.publish(ev_audio('preview', '/audio/preview.wav', 'preview'))
            await asyncio.sleep(self.interval)
            self.hub.publish(ev_caption_clear())
            self.hub.publish(ev_chat('chat1', 'Viewer', 'Hello!'))
            await asyncio.sleep(self.interval)
            self.hub.publish(ev_chat('chat2', 'Viewer', 'Nice set!'))
            await asyncio.sleep(self.interval)
            self.hub.publish(ev_alert('alert1', 'sub', 'Viewer', 1, 1, '', 2000))
            await asyncio.sleep(self.interval * 2)
            state(True, True)
            await asyncio.sleep(self.interval)
            state(True)
            await asyncio.sleep(self.interval)
            state(False)
            await asyncio.sleep(self.interval)

    async def stop(self):
        if self.task:
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task
        await self.server.stop()


async def run(scene_dir: Path):
    preview = ScenePreview(scene_dir)
    await preview.start()
    print(f'Private synthetic preview: http://127.0.0.1:{preview.server.port}/scene/', flush=True)
    print('Synthetic show cycle; the scene plays no audio. Press Ctrl-C to close.', flush=True)
    try:
        await asyncio.Event().wait()
    finally:
        await preview.stop()
