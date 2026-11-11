import argparse
import asyncio
import json
from pathlib import Path

import pytest

from onair.tts import silent_wav


def test_voice_check_uses_show_engine_and_cleans_file(tmp_path):
    from onair.tts import voice_test
    calls = []
    def dispatch(name, args):
        calls.append((name, args['text']))
        Path(args['output_path']).write_bytes(silent_wav(0.25))
        return {'success': True, 'file_path': args['output_path']}
    result = asyncio.run(voice_test(dispatch, tmp_path, {'provider': 'example'}))
    assert result['ok'] is True and result['category'] == 'ok'
    assert result['audio_type'] == 'audio/wav' and result['duration_s'] == pytest.approx(.25)
    assert calls == [('text_to_speech', 'This is an On Air voice test.')]
    assert not list((tmp_path / 'voice-test-clips').glob('*'))
    assert 'fingerprint' in json.loads((tmp_path / 'voice-test.json').read_text())


@pytest.mark.parametrize('payload,category', [(b'', 'size'), (b'not audio', 'audio_type'), (silent_wav(0), 'duration')])
def test_voice_check_rejects_bad_audio(tmp_path, payload, category):
    from onair.tts import voice_test
    def dispatch(name, args):
        Path(args['output_path']).write_bytes(payload)
        return {'success': True, 'file_path': args['output_path']}
    result = asyncio.run(voice_test(dispatch, tmp_path, {}))
    assert not result['ok'] and result['category'] == category
    assert not list((tmp_path / 'voice-test-clips').glob('*'))


def test_cancelled_engine_abandons_and_sweeps_late_file(tmp_path):
    import threading
    from onair.tts import HermesTTS

    async def run():
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        class Observed(HermesTTS):
            def _run(self, text, target, abandoned):
                self.marker = abandoned
                try:
                    return super()._run(text, target, abandoned)
                finally:
                    finished.set()
        def dispatch(name, args):
            entered.set()
            assert release.wait(3)
            path = Path(args['output_path'])
            path.write_bytes(silent_wav(.2))
            return {'success': True, 'file_path': str(path)}
        engine = Observed(dispatch, tmp_path / 'clips')
        task = asyncio.create_task(engine.synth('fixed words'))
        assert await asyncio.to_thread(entered.wait, 3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert engine.marker.is_set()
        release.set()
        assert await asyncio.to_thread(finished.wait, 3)
        assert list((tmp_path / 'clips').iterdir()) == []
    asyncio.run(run())


def test_voice_check_failure_sanitized(tmp_path, caplog):
    from onair.tts import voice_test
    def dispatch(name, args):
        return {'success': False, 'error': 'secret provider text https://example.invalid/key'}
    result = asyncio.run(voice_test(dispatch, tmp_path, {}))
    assert result['category'] == 'synthesis' and not result['ok']
    assert 'secret' not in json.dumps(result) + caplog.text


def test_voice_preflight_success_failure_age_and_configuration(tmp_path, monkeypatch):
    from onair.tts import voice_test
    from onair.preflight import run_preflight
    import onair.cli as cli
    cfg = {'gateway': {'platforms': {'on_air': {'enabled': True}}}, 'plugins': {'entries': {'on-air': {'settings': {'overlay_port': 0}}}}}
    cfg['tts'] = {'provider': 'test', 'test': {'voice': 'one', 'api_key': 'private-value'}}
    monkeypatch.setattr(cli, '_data_dir', lambda: tmp_path)
    def dispatch(name, args):
        Path(args['output_path']).write_bytes(silent_wav(.2))
        return {'success': True, 'file_path': args['output_path']}
    asyncio.run(voice_test(dispatch, tmp_path, cfg['tts']))
    check = next(c for c in run_preflight(cfg)['checks'] if c['name'] == 'tts')
    assert check['status'] == 'pass' and 'voice tested' in check['detail'].lower()
    from onair.tts import voice_test_status, VOICE_TEST_RECENT_S
    record = json.loads((tmp_path / 'voice-test.json').read_text())
    assert voice_test_status(tmp_path, cfg['tts'], now=record['time'] + VOICE_TEST_RECENT_S + 1)[0] == 'warn'
    assert voice_test_status(tmp_path, cfg['tts'], now=record['time'] - 1)[0] == 'warn'
    assert 'private-value' not in (tmp_path / 'voice-test.json').read_text()
    cfg['tts']['test']['voice'] = 'two'
    check = next(c for c in run_preflight(cfg)['checks'] if c['name'] == 'tts')
    assert check['status'] == 'warn' and 'hermes onair voice-test' in check['detail']
    cfg['tts']['test']['voice'] = 'one'
    asyncio.run(voice_test(lambda *args: {'success': False}, tmp_path, cfg['tts']))
    check = next(c for c in run_preflight(cfg)['checks'] if c['name'] == 'tts')
    assert check['status'] == 'fail' and 'hermes onair voice-test' in check['detail']


def test_voice_cli_dispatches_through_registered_context(tmp_path, monkeypatch, capsys):
    from onair import adapter, cli
    import hermes_cli.config as config
    monkeypatch.setattr(cli, '_data_dir', lambda: tmp_path)
    monkeypatch.setattr(config, 'load_config_readonly', lambda: {'tts': {'provider': 'test'}})
    class Ctx:
        def dispatch_tool(self, name, args):
            Path(args['output_path']).write_bytes(silent_wav(.1))
            return {'success': True, 'file_path': args['output_path']}
    monkeypatch.setattr(adapter, '_PLUGIN_CTX', Ctx())
    parser = argparse.ArgumentParser()
    cli.setup_parser(parser)
    cli.handle(parser.parse_args(['voice-test']))
    assert 'voice-test: ok' in capsys.readouterr().out


def test_voice_cli_without_dispatch_records_failure(tmp_path, monkeypatch, capsys):
    from onair import adapter, cli
    from onair.tts import voice_test_status
    import hermes_cli.config as config
    monkeypatch.setattr(cli, '_data_dir', lambda: tmp_path)
    monkeypatch.setattr(config, 'load_config_readonly', lambda: {'tts': {'provider': 'test'}})
    monkeypatch.setattr(adapter, '_PLUGIN_CTX', None)
    parser = argparse.ArgumentParser()
    cli.setup_parser(parser)
    with pytest.raises(SystemExit):
        cli.handle(parser.parse_args(['voice-test']))
    assert 'category=unavailable' in capsys.readouterr().out
    assert voice_test_status(tmp_path, {'provider': 'test'})[0] == 'fail'


def test_real_hermes_cli_with_command_provider(tmp_path):
    """The real CLI discovers the plugin and calls a local command provider, never a network."""
    import os
    import shlex
    import shutil
    import subprocess
    import sys

    root = Path(__file__).resolve().parents[2]
    home = tmp_path / 'home'
    plugin = home / 'plugins' / 'on-air'
    shutil.copytree(root / 'onair', plugin / 'onair', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    for filename in ('__init__.py', 'plugin.yaml'):
        shutil.copy(root / filename, plugin / filename)
    program = tmp_path / 'speech.py'
    program.write_text('import sys, wave\n'
                       'assert open(sys.argv[1]).read() == "This is an On Air voice test."\n'
                       'w = wave.open(sys.argv[2], "wb")\n'
                       'w.setnchannels(1); w.setsampwidth(2); w.setframerate(8000)\n'
                       'w.writeframes(bytes(4000)); w.close()\n')
    command = f'{shlex.quote(sys.executable)} {shlex.quote(str(program))} {{input_path}} {{output_path}}'
    config = {'plugins': {'enabled': ['on-air']}, 'tts': {'provider': 'localtest', 'providers': {
        'localtest': {'type': 'command', 'command': command, 'output_format': 'wav'}}}}
    (home / 'config.yaml').write_text(json.dumps(config))
    env = dict(os.environ, HERMES_HOME=str(home), HOME=str(home), HERMES_DISABLE_LAZY_INSTALLS='1')
    proc = subprocess.run([sys.executable, '-B', '-m', 'hermes_cli.main', 'onair', 'voice-test'],
                          env=env, cwd=tmp_path, capture_output=True, text=True, timeout=90)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert 'voice-test: ok; category=ok' in proc.stdout
    assert 'audio_type=audio/wav' in proc.stdout
    assert not list((home / 'plugin-data' / 'on-air' / 'voice-test-clips').glob('*'))
    # Hermes's command error includes stderr internally; the owner-facing CLI must not.
    program.write_text('import sys\nsys.stderr.write("provider-secret https://private.invalid/key")\nsys.exit(2)\n')
    proc = subprocess.run([sys.executable, '-B', '-m', 'hermes_cli.main', 'onair', 'voice-test'],
                          env=env, cwd=tmp_path, capture_output=True, text=True, timeout=90)
    assert proc.returncode != 0
    assert 'category=synthesis' in proc.stdout
    assert 'provider-secret' not in proc.stdout + proc.stderr
    assert 'private.invalid' not in proc.stdout + proc.stderr
    assert not list((home / 'plugin-data' / 'on-air' / 'voice-test-clips').glob('*'))
