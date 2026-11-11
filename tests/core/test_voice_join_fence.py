"""Boot and epoch are one immutable capture-time identity."""
import asyncio
import io
import json
import wave

import httpx
import pytest
from fastapi import FastAPI
from dashboard import plugin_api


@pytest.fixture(autouse=True)
def microphone_home(monkeypatch, tmp_path_factory):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path_factory.mktemp('microphone-home')))


def test_mic_requires_and_forwards_boot(monkeypatch):
    mic = plugin_api._onair('host_mic')
    monkeypatch.setattr(plugin_api._ViewsConfig, 'get_config', lambda self, key: {'host_mic': True, 'host_mic_review': False}.get(key))
    monkeypatch.setattr(mic, '_saved_mode', lambda: 'send')
    monkeypatch.setattr(mic, '_transcribe', lambda path: {'success': True, 'transcript': 'synthetic'})
    calls = []
    def forward(*args):
        calls.append(args)
        if args[1] == '/state':
            return 200, {'live': True, 'mode': 'approval', 'privacy': False, 'epoch': 7, 'voice_boot': 'a'*32}
        return 200, {'ok': True, 'queued': True}
    monkeypatch.setattr(plugin_api, '_forward', forward)
    audio = io.BytesIO()
    with wave.open(audio, 'wb') as f:
        f.setparams((1, 2, 8000, 0, 'NONE', 'not compressed'))
        f.writeframes(b'\0\0' * 8000)
    app = FastAPI()
    app.include_router(plugin_api.router, prefix='/api/plugins/on-air')
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as c:
            url = '/api/plugins/on-air/host/mic'
            headers = {'content-type': 'audio/wav', 'x-onair-epoch': '7', 'x-onair-mode': 'send'}
            assert not (await c.post(url, content=audio.getvalue(), headers=headers)).json()['ok']
            for boot in ('bad', 'A' * 32, '0' * 31):
                assert not (await c.post(url, content=audio.getvalue(), headers={**headers, 'x-onair-boot': boot})).json()['ok']
            assert (await c.post(url, content=audio.getvalue(), headers={**headers, 'x-onair-boot': 'a' * 32})).json()['ok']
            assert json.loads(calls[-1][3]) == {'text': 'synthetic', 'epoch': 7, 'boot': 'a' * 32}
    asyncio.run(run())


def test_review_refuses_restart_before_and_during_transcription(monkeypatch):
    mic = plugin_api._onair('host_mic')
    monkeypatch.setattr(plugin_api._ViewsConfig, 'get_config', lambda self, key: {'host_mic': True, 'host_mic_review': True}.get(key))
    state = {'live': True, 'mode': 'approval', 'privacy': False, 'epoch': 7, 'voice_boot': 'b'*32}
    calls = []
    def transcribe(path):
        calls.append('stt')
        state['voice_boot'] = 'b'*32
        return {'success': True, 'transcript': 'synthetic'}
    monkeypatch.setattr(mic, '_transcribe', transcribe)
    monkeypatch.setattr(plugin_api, '_forward', lambda *args: (200, dict(state)))
    audio = io.BytesIO()
    with wave.open(audio, 'wb') as f:
        f.setparams((1, 2, 8000, 0, 'NONE', 'not compressed'))
        f.writeframes(b'\0\0' * 8000)
    app = FastAPI()
    app.include_router(plugin_api.router, prefix='/api/plugins/on-air')
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as c:
            headers = {'content-type': 'audio/wav', 'x-onair-epoch': '7', 'x-onair-boot': 'a'*32}
            url = '/api/plugins/on-air/host/mic'
            result = (await c.post(url, content=audio.getvalue(), headers=headers)).json()
            assert not result['ok'] and not calls
            state['voice_boot'] = 'a'*32
            result = (await c.post(url, content=audio.getvalue(), headers=headers)).json()
            assert not result['ok'] and 'transcript' not in result and calls == ['stt']
    asyncio.run(run())
