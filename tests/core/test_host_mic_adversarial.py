"""Adversarial input checks for the microphone upload boundary."""
import asyncio
import io
import threading
import wave

import httpx
import pytest
from fastapi import FastAPI
from dashboard import plugin_api


@pytest.fixture(autouse=True)
def microphone_home(monkeypatch, tmp_path_factory):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path_factory.mktemp('microphone-home')))


def clip():
    data = io.BytesIO()
    with wave.open(data, 'wb') as out:
        out.setparams((1, 2, 8000, 0, 'NONE', 'not compressed'))
        out.writeframes(b'\0\0' * 8000)
    return data.getvalue()


def test_transcript_controls_stripped_before_review_and_forward(monkeypatch):
    mic = plugin_api._onair('host_mic')
    monkeypatch.setattr(mic, '_transcribe', lambda _: {'success': True, 'transcript': 'hello\x00\x1bworld\x7f'})
    seen = []
    def forward(*args):
        seen.append(args)
        return 200, {'ok': True, 'queued': True}
    reviewed = mic._process(clip(), 'audio/wav', 1, '', True, forward, 'a'*32)
    assert reviewed['transcript'] == 'helloworld'
    direct = mic._process(clip(), 'audio/wav', 1, '', False, forward, 'a'*32)
    assert direct['transcript'] == 'helloworld'
    assert '\\u0000' not in seen[0][3].decode()


def test_duplicate_security_headers_are_refused(monkeypatch):
    mic = plugin_api._onair('host_mic')
    monkeypatch.setattr(plugin_api._ViewsConfig, 'get_config', lambda self, key: True)
    monkeypatch.setattr(mic, '_transcribe', lambda _: {'success': True, 'transcript': 'hello'})
    monkeypatch.setattr(plugin_api, '_forward', lambda *args: (200, {'live': True, 'mode': 'approval', 'privacy': False, 'epoch': 1, 'voice_boot':'a'*32}))
    app = FastAPI(); app.include_router(plugin_api.router, prefix='/api/plugins/on-air')
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            base = [('content-type','audio/wav'), ('x-onair-epoch','1'), ('x-onair-boot','a'*32), ('x-onair-mode','review')]
            for name, value in base:
                response = await client.post('/api/plugins/on-air/host/mic', content=clip(), headers=base+[(name,value)])
                assert not response.json()['ok'], name
            for length in ('', '+1', '01', '-1', '99999999999999999999'):
                response = await client.post('/api/plugins/on-air/host/mic', content=clip(), headers=base+[('content-length',length)])
                assert not response.json()['ok'], length
    asyncio.run(run())


def test_cancelled_upload_does_not_queue_after_transcription(monkeypatch, tmp_path):
    from starlette.datastructures import Headers
    mic = plugin_api._onair('host_mic')
    monkeypatch.setattr(mic.tempfile, 'tempdir', str(tmp_path))
    monkeypatch.setattr(mic, '_saved_mode', lambda: 'send')
    entered, release = threading.Event(), threading.Event()
    def transcribe(_):
        entered.set()
        assert release.wait(5)
        return {'success': True, 'transcript': 'private synthetic words'}
    monkeypatch.setattr(mic, '_transcribe', transcribe)
    class Config:
        def get_config(self, key): return {'host_mic':True, 'host_mic_review':False}.get(key)
    class Request:
        headers = Headers({'content-type':'audio/wav','x-onair-epoch':'1','x-onair-boot':'a'*32,'x-onair-mode':'send'})
        async def stream(self): yield clip()
        async def is_disconnected(self): return False
    calls = []
    def forward(method, path, query, body):
        calls.append(path)
        if path == '/state': return 200, {'live':True,'mode':'approval','privacy':False,'epoch':1,'voice_boot':'a'*32}
        return 200, {'ok':True,'queued':True}
    async def run():
        task = asyncio.create_task(mic.upload(Request(), Config(), forward))
        assert await asyncio.to_thread(entered.wait, 3)
        task.cancel()
        await asyncio.sleep(.05)
        release.set()
        try: await task
        except asyncio.CancelledError: pass
        for _ in range(100):
            if not list(tmp_path.iterdir()): break
            await asyncio.sleep(.02)
        assert not list(tmp_path.iterdir())
        assert '/host/turn' not in calls
    try: asyncio.run(run())
    finally: release.set()


def test_disconnect_during_transcription_discards_and_cleans(monkeypatch, tmp_path):
    from starlette.datastructures import Headers
    mic = plugin_api._onair('host_mic')
    monkeypatch.setattr(mic.tempfile, 'tempdir', str(tmp_path))
    monkeypatch.setattr(mic, '_saved_mode', lambda: 'send')
    entered, release = threading.Event(), threading.Event()
    def transcribe(path):
        from pathlib import Path
        assert Path(path).stat().st_mode & 0o777 == 0o600
        assert Path(path).parent.stat().st_mode & 0o777 == 0o700
        entered.set(); assert release.wait(5)
        return {'success':True, 'transcript':'synthetic'}
    monkeypatch.setattr(mic, '_transcribe', transcribe)
    class Config:
        def get_config(self, key): return {'host_mic':True,'host_mic_review':False}.get(key)
    class Request:
        disconnected = False
        headers = Headers({'content-type':'audio/wav','x-onair-epoch':'1','x-onair-boot':'a'*32,'x-onair-mode':'send'})
        async def stream(self): yield clip()
        async def is_disconnected(self): return self.disconnected
    calls = []
    def forward(method, path, query, body):
        calls.append(path)
        if path == '/state': return 200, {'live':True,'mode':'approval','privacy':False,'epoch':1,'voice_boot':'a'*32}
        return 200, {'ok':True,'queued':True}
    async def run():
        request = Request()
        task = asyncio.create_task(mic.upload(request, Config(), forward))
        assert await asyncio.to_thread(entered.wait, 3)
        request.disconnected = True
        await asyncio.sleep(.15)
        release.set()
        result = await task
        assert b'disconnected' in result.body
        assert '/host/turn' not in calls
        assert not list(tmp_path.iterdir())
    try: asyncio.run(run())
    finally: release.set()


def test_stream_cap_refuses_before_reading_rest(monkeypatch):
    mic = plugin_api._onair('host_mic')
    monkeypatch.setattr(plugin_api._ViewsConfig, 'get_config', lambda self, key: True)
    app = FastAPI(); app.include_router(plugin_api.router, prefix='/api/plugins/on-air')
    pulled = []
    async def chunks():
        yield b'x' * (mic.MAX_BYTES + 1)
        pulled.append('past limit')
        yield b'x'
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            response = await client.post('/api/plugins/on-air/host/mic', content=chunks(), headers={'content-type':'audio/wav','x-onair-epoch':'1','x-onair-boot':'a'*32})
            assert response.status_code == 413 and response.json()['reason'] == 'size'
            assert not pulled
    asyncio.run(run())
