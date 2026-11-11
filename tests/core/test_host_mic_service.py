import asyncio
import io
import json
import wave
from pathlib import Path

import subprocess
import threading

import httpx
import pytest
from fastapi import FastAPI
from dashboard import plugin_api


@pytest.fixture(autouse=True)
def microphone_home(monkeypatch, tmp_path_factory):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path_factory.mktemp('microphone-home')))


def test_cut_on_talk_setting_defaults_on_and_can_be_disabled():
    mic = plugin_api._onair('host_mic')
    class Config:
        def __init__(self, value): self.value = value
        def get_config(self, key):
            return {'host_mic': True, 'host_mic_review': True, 'host_cut_in_on_talk': self.value}.get(key)
    assert asyncio.run(mic.settings(Config(None)))['cut_in_on_talk'] is True
    assert asyncio.run(mic.settings(Config(False)))['cut_in_on_talk'] is False
    assert asyncio.run(mic.settings(Config(True)))['cut_in_on_talk'] is True


def wav(seconds=1):
    out = io.BytesIO()
    with wave.open(out, 'wb') as f:
        f.setparams((1, 2, 8000, 0, 'NONE', 'not compressed'))
        f.writeframes(b'\0\0' * (8000 * seconds))
    return out.getvalue()


def test_mic_review_and_epoch_forwarding(monkeypatch, tmp_path):
    mic = plugin_api._onair('host_mic')
    monkeypatch.setattr(plugin_api._ViewsConfig, 'get_config', lambda self, key: {'host_mic': True, 'host_mic_review': True}.get(key))
    calls = []
    monkeypatch.setattr(mic, '_transcribe', lambda path: {'success': True, 'transcript': 'A synthetic line'})
    def forward(*args):
        if args[1] == '/state':
            return 200, {'live': True, 'mode': 'approval', 'privacy': False, 'epoch': 7, 'voice_boot': 'a'*32}
        calls.append(args)
        return 409, {'ok': False, 'reason': 'stale_epoch', 'message': 'Show changed.'}
    monkeypatch.setattr(plugin_api, '_forward', forward)
    monkeypatch.setattr(mic.tempfile, 'tempdir', str(tmp_path))
    app = FastAPI(); app.include_router(plugin_api.router, prefix='/api/plugins/on-air')
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as c:
            headers = {'content-type':'audio/wav', 'x-onair-epoch':'7', 'x-onair-boot':'a'*32}
            result = (await c.post('/api/plugins/on-air/host/mic', content=wav(), headers=headers)).json()
            assert result['ok'] and result['review'] and result['transcript'] == 'A synthetic line'
            assert not calls
            monkeypatch.setattr(plugin_api._ViewsConfig, 'get_config', lambda self, key: {'host_mic': True, 'host_mic_review': False}.get(key))
            monkeypatch.setattr(mic, '_saved_mode', lambda: 'send')
            headers['x-onair-mode'] = 'send'
            result = (await c.post('/api/plugins/on-air/host/mic', content=wav(), headers=headers)).json()
            assert result['reason'] == 'stale_epoch' and result['message'] == 'Show changed.'
            assert json.loads(calls[-1][3]) == {'text':'A synthetic line', 'epoch':7, 'boot':'a'*32}
            assert not list(tmp_path.iterdir())
    asyncio.run(run())


def test_mic_rejects_disabled_and_invalid_audio(monkeypatch):
    monkeypatch.setattr(plugin_api._ViewsConfig, 'get_config', lambda self, key: None)
    app = FastAPI(); app.include_router(plugin_api.router, prefix='/api/plugins/on-air')
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as c:
            url = '/api/plugins/on-air/host/mic'
            assert not (await c.post(url, content=wav(), headers={'content-type':'audio/wav','x-onair-epoch':'1', 'x-onair-boot':'a'*32})).json()['ok']
            monkeypatch.setattr(plugin_api._ViewsConfig, 'get_config', lambda self, key: True)
            monkeypatch.setattr(plugin_api, '_forward', lambda *args: (200, {'live': True, 'mode': 'approval', 'privacy': False, 'epoch': 1, 'voice_boot': 'a'*32}))
            for body, headers in [(wav(), {'content-type':'text/plain','x-onair-epoch':'1', 'x-onair-boot':'a'*32}),
                                  (b'x' * (2*1024*1024+1), {'content-type':'audio/wav','x-onair-epoch':'1', 'x-onair-boot':'a'*32}),
                                  (wav(31), {'content-type':'audio/wav','x-onair-epoch':'1', 'x-onair-boot':'a'*32}),
                                  (wav(), {'content-type':'audio/wav','x-onair-epoch':'bad'})]:
                assert not (await c.post(url, content=body, headers=headers)).json()['ok']
    asyncio.run(run())


def test_cleanup_and_no_content_logging(monkeypatch, tmp_path, caplog):
    mic = plugin_api._onair('host_mic')
    monkeypatch.setattr(mic.tempfile, 'tempdir', str(tmp_path))
    def stub(path):
        assert Path(path).stat().st_mode & 0o777 == 0o600
        (Path(path).parent / 'derived.wav').write_bytes(b'synthetic audio')
        if mode == 'error':
            raise ValueError('sensitive transcript')
        if mode == 'timeout':
            raise subprocess.TimeoutExpired('stt', 20)
        return {'success':True, 'transcript':'sensitive transcript'}
    monkeypatch.setattr(mic, '_transcribe', stub)
    for mode in ('success', 'error', 'timeout'):
        answer = mic._process(wav(), 'audio/wav', 1, '', True, None)
        assert answer['ok'] is (mode == 'success')
        assert not list(tmp_path.iterdir())
    assert 'sensitive transcript' not in caplog.text
    assert 'synthetic audio' not in caplog.text


def test_concurrent_clips_are_refused(monkeypatch):
    mic = plugin_api._onair('host_mic')
    entered, done = threading.Event(), threading.Event()
    def stub(_path):
        entered.set()
        assert done.wait(3)
        return {'success':True, 'transcript':'hello'}
    monkeypatch.setattr(mic, '_transcribe', stub)
    thread = threading.Thread(target=lambda: mic._process(wav(), 'audio/wav', 1, '', True, None))
    thread.start()
    try:
        assert entered.wait(3)
        result = mic._process(wav(), 'audio/wav', 1, '', True, None)
        assert result['reason'] == 'busy'
    finally:
        done.set()
        thread.join(3)
    assert not thread.is_alive()


def test_streaming_upload_is_also_serialized(monkeypatch):
    mic = plugin_api._onair('host_mic')
    monkeypatch.setattr(plugin_api._ViewsConfig, 'get_config', lambda self, key: True)
    monkeypatch.setattr(mic, '_transcribe', lambda path: {'success':True, 'transcript':'hello'})
    monkeypatch.setattr(plugin_api, '_forward', lambda *args: (200, {'live': True, 'mode': 'approval', 'privacy': False, 'epoch': 1, 'voice_boot': 'a'*32}))
    app = FastAPI(); app.include_router(plugin_api.router, prefix='/api/plugins/on-air')
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        async def chunks():
            yield wav()[:44]
            entered.set()
            await release.wait()
            yield wav()[44:]
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            url = '/api/plugins/on-air/host/mic'
            headers = {'content-type':'audio/wav', 'x-onair-epoch':'1', 'x-onair-boot':'a'*32}
            first = asyncio.create_task(client.post(url, content=chunks(), headers=headers))
            try:
                await asyncio.wait_for(entered.wait(), 3)
                second = await client.post(url, content=wav(), headers=headers)
                assert second.json()['reason'] == 'busy'
            finally:
                release.set()
            assert (await first).json()['review'] is True
    asyncio.run(run())


def test_real_child_timeout_cleans_provider_temp(monkeypatch, tmp_path):
    mic = plugin_api._onair('host_mic')
    package = tmp_path / 'stub' / 'tools'; package.mkdir(parents=True)
    (package / '__init__.py').write_text('')
    (package / 'transcription_tools.py').write_text(
        'import tempfile,time\n'
        'def is_stt_enabled(): return True\n'
        'def transcribe_audio(path,source=None):\n'
        '    f=tempfile.NamedTemporaryFile(delete=False); f.write(b"private"); f.close()\n'
        '    time.sleep(30)\n')
    audio_tmp = tmp_path / 'audio'; audio_tmp.mkdir()
    monkeypatch.setattr(mic.tempfile, 'tempdir', str(audio_tmp))
    monkeypatch.setattr(mic, '_STT_TIMEOUT', .4)
    monkeypatch.setenv('PYTHONPATH', str(package.parent))
    result = mic._process(wav(), 'audio/wav', 1, '', True, None)
    assert result['reason'] == 'timeout'
    assert not list(audio_tmp.iterdir())
