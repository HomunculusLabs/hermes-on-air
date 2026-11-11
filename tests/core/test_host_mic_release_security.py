"""Release mode is bound to the owner's press and rechecked before forwarding."""
import asyncio
import shutil
import subprocess
import httpx
import pytest
from fastapi import FastAPI
from dashboard import plugin_api


@pytest.fixture(autouse=True)
def microphone_home(monkeypatch, tmp_path_factory):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path_factory.mktemp('microphone-home')))


def test_review_is_never_escalated_and_lock_wins_during_stt(monkeypatch, tmp_path):
    if not shutil.which('espeak-ng'):
        pytest.skip('espeak-ng is needed for synthetic test audio')
    clip = tmp_path / 'voice.wav'
    subprocess.run(['espeak-ng', '-w', str(clip), 'Synthetic host'], check=True)
    audio = clip.read_bytes()
    mic = plugin_api._onair('host_mic')
    choice = {'host_mic':True, 'host_mic_review':False}
    monkeypatch.setattr(plugin_api._ViewsConfig, 'get_config', lambda self, key: choice.get(key))
    monkeypatch.setattr(mic, '_saved_mode', lambda: 'send')
    calls = []
    def forward(method, path, query, body):
        if path == '/state':
            return 200, {'live':True, 'mode':'approval', 'privacy':False, 'epoch':7, 'voice_boot':'a'*32}
        calls.append((method, path, body)); return 200, {'ok':True, 'queued':True}
    monkeypatch.setattr(plugin_api, '_forward', forward)
    app = FastAPI(); app.include_router(plugin_api.router, prefix='/api/plugins/on-air')
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as c:
            headers={'content-type':'audio/wav','x-onair-epoch':'7','x-onair-boot':'a'*32}
            monkeypatch.setattr(mic, '_transcribe', lambda _: {'success':True,'transcript':'hello'})
            result=(await c.post('/api/plugins/on-air/host/mic', content=audio, headers=headers)).json()
            assert result['review'] and not calls
            def lock_during_transcription(_):
                choice['host_mic_review'] = True
                return {'success':True,'transcript':'hello'}
            monkeypatch.setattr(mic, '_transcribe', lock_during_transcription)
            result=(await c.post('/api/plugins/on-air/host/mic', content=audio, headers={**headers,'x-onair-mode':'send'})).json()
            assert not result['ok'] and result['reason']=='review_required' and result['transcript']=='hello'
            assert not calls
    asyncio.run(run())
