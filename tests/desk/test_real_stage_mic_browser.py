"""Opt-in synthetic browser journey through the actual stage controller and control server."""
import asyncio
import io
import math
import os
import struct
import wave
from pathlib import Path
import shutil
import socket
import subprocess
import threading
import time

import pytest
from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse
from dashboard import plugin_api
from onair.control import ControlServer
from onair.overlay.server import OverlayServer
from onair.stage import StageConfig, StageController
from onair.tts import Clip

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(os.environ.get('ONAIR_REAL_STAGE_BROWSER') != '1', reason='opt-in synthetic real-stage browser test')


@pytest.mark.parametrize('width', [1440, 390])
def test_real_stage_synthetic_talk_journey(tmp_path, monkeypatch, width):
    import uvicorn
    assert shutil.which('espeak-ng') and shutil.which('ffprobe')
    assert os.environ.get('ONAIR_PLAYWRIGHT') and os.environ.get('DESK_TEST_CHROMIUM')
    home = tmp_path / 'home'; home.mkdir()
    monkeypatch.setenv('HERMES_HOME', str(home))
    audio = tmp_path / 'synthetic.wav'
    subprocess.run(['espeak-ng', '-w', str(audio), 'Hello from the synthetic host.'], check=True)
    mic = plugin_api._onair('host_mic')
    def transcribe(_):
        time.sleep(1)
        return {'success':True,'transcript':'Hello from the synthetic host.'}
    monkeypatch.setattr(mic, '_transcribe', transcribe)
    monkeypatch.setattr(plugin_api._ViewsConfig, 'get_config', lambda self, key: {'host_mic':True,'host_mic_review':True}.get(key))
    loop = asyncio.new_event_loop(); ready = threading.Event(); stage = {}
    def run_stage():
        asyncio.set_event_loop(loop)
        async def setup():
            buf = io.BytesIO()
            with wave.open(buf, 'wb') as wav:
                wav.setparams((1, 2, 8000, 0, 'NONE', 'not compressed'))
                wav.writeframes(b''.join(struct.pack('<h', int(3000*math.sin(2*math.pi*220*i/8000))) for i in range(8000*30)))
            tone = buf.getvalue()
            class Voice:
                available = True
                async def synth(self, text): return Clip(tone, 'audio/wav', 30)
            async def dispatch(turn):
                # Stub model output; the real controller performs its real line guard and queueing.
                stage['controller'].deliver_reply(turn.turn_id, 'Hello from the co-host.')
                stage['controller'].turn_done(turn.turn_id)
            c = StageController(StageConfig(host_turns=True, turn_gap_s=0, tick_s=.05), tts=Voice(), dispatch=dispatch)
            c.set_mode('approval')
            s = ControlServer(c, None); await s.start()
            overlay = OverlayServer(c.hub, c.clip, port=0); await overlay.start(); await c.start()
            stage.update(controller=c, server=s, overlay=overlay); ready.set()
        loop.run_until_complete(setup()); loop.run_forever()
        loop.run_until_complete(stage['controller'].shutdown()); loop.run_until_complete(stage['server'].stop()); loop.run_until_complete(stage['overlay'].stop()); loop.close()
    stage_thread = threading.Thread(target=run_stage); stage_thread.start()
    assert ready.wait(10)
    s = stage['server']
    monkeypatch.setattr(plugin_api, '_discovery', lambda: (s.http.port, s.token, ''))
    app = FastAPI(); app.include_router(plugin_api.router, prefix='/api/plugins/on-air')
    @app.get('/')
    def page():
        return HTMLResponse('<!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1"><link rel="stylesheet" href="/dashboard-plugins/on-air/dist/style.css"></head><body><div id="root"></div><script src="/sdk.js"></script><script src="/dashboard-plugins/on-air/dist/index.js"></script></body></html>')
    @app.get('/sdk.js')
    def sdk(): return FileResponse(tmp_path / 'sdk.js', media_type='text/javascript')
    @app.get('/dashboard-plugins/on-air/dist/{name}')
    def asset(name: str):
        assert name in ('index.js','host-mic.js','style.css')
        return FileResponse(ROOT / 'dashboard/dist' / name)
    @app.get('/fixture/status')
    async def status():
        async def snapshot():
            c = stage['controller']
            return {'state':c.snapshot(), 'pending':c.pending_lines(), 'events':list(c.hub.history), 'receipts':c.receipts.tail(100)}
        return await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(snapshot(), loop))
    sock = socket.socket(); sock.bind(('127.0.0.1', 0)); sock.listen(128)
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level='error', lifespan='off'))
    thread = threading.Thread(target=server.run, kwargs={'sockets':[sock]}, daemon=True); thread.start()
    try:
        for _ in range(100):
            if server.started: break
            time.sleep(.05)
        assert server.started
        env = dict(os.environ, ONAIR_MIC_URL=f'http://127.0.0.1:{port}/', ONAIR_MIC_WAV=str(audio),
                   ONAIR_MIC_SDK=str(tmp_path/'sdk.js'), ONAIR_MIC_WIDTH=str(width),
                   ONAIR_OVERLAY_URL=f"http://127.0.0.1:{stage['overlay'].port}/overlay")
        result = subprocess.run(['node', str(ROOT/'tests/desk/real_stage_mic_browser.cjs')], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
        assert result.returncode == 0, result.stdout + result.stderr
    finally:
        server.should_exit = True; thread.join(10); sock.close()
        loop.call_soon_threadsafe(loop.stop); stage_thread.join(10)
        assert not thread.is_alive() and not stage_thread.is_alive()
