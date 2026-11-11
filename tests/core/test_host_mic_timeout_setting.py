"""The desk bounds STT in the dashboard's own profile and gives actionable refusals."""
import asyncio
import io
import subprocess
import wave

from dashboard import plugin_api


def clip():
    output = io.BytesIO()
    with wave.open(output, 'wb') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(8000)
        w.writeframes(b'\0\0' * 800)
    return output.getvalue()


def test_timeout_setting_bounds_and_default():
    mic = plugin_api._onair('host_mic')
    class Config:
        def __init__(self, value): self.value = value
        def get_config(self, key): return self.value if key == 'host_mic_timeout_seconds' else None
    for value, expected in [(None, 20), (5, 5), (120, 120), (4, 20), (121, 20), (True, 20), ('60', 20), (5.5, 20)]:
        assert asyncio.run(mic.settings(Config(value)))['timeout_seconds'] == expected


def test_timeout_and_provider_refusals(monkeypatch):
    mic = plugin_api._onair('host_mic')
    def timeout(*args): raise subprocess.TimeoutExpired('stt', 20)
    monkeypatch.setattr(mic, '_transcribe', timeout)
    got = mic._process(clip(), 'audio/wav', 1, '', True, None, 'a'*32)
    assert got['reason'] == 'timeout'
    assert all(part in got['message'] for part in ('dashboard profile', 'hermes tools', 'first use', 'local model'))
    monkeypatch.setattr(mic, '_transcribe', lambda *args: {'success': False, 'no_provider': True})
    got = mic._process(clip(), 'audio/wav', 1, '', True, None, 'a'*32)
    assert got['reason'] == 'no_provider'
    assert 'dashboard profile' in got['message'] and 'hermes tools' in got['message']


def test_configured_timeout_reaches_subprocess(monkeypatch):
    mic = plugin_api._onair('host_mic')
    seen = []
    def transcribe(*args):
        seen.append(args)
        return {'success': True, 'transcript': 'hello'}
    monkeypatch.setattr(mic, '_transcribe', transcribe)
    result = mic._process(clip(), 'audio/wav', 1, '', True, None, 'a'*32, 120)
    assert result['ok'] and seen[0][1] == 120


def test_real_child_skips_unavailable_provider(tmp_path, monkeypatch):
    mic = plugin_api._onair('host_mic')
    package = tmp_path / 'tools'
    package.mkdir()
    (package / '__init__.py').write_text('')
    (package / 'transcription_tools.py').write_text(
        "def is_stt_enabled(): return True\n"
        "def _load_stt_config(): return {}\n"
        "def _get_provider(cfg): return 'none'\n"
        "def transcribe_audio(*args, **kwargs): raise AssertionError('must not transcribe')\n")
    monkeypatch.setenv('PYTHONPATH', str(tmp_path))
    audio = tmp_path / 'clip.wav'
    audio.write_bytes(clip())
    assert mic._transcribe(str(audio)) == {'success': False, 'transcript': '', 'no_provider': True}
