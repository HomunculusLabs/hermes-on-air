import argparse
import codecs
import json
import sys
import threading

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from onair import cli
from onair.preflight import run_preflight as _REAL_RUN_PREFLIGHT


def invoke(*argv):
    parser = argparse.ArgumentParser()
    cli.setup_parser(parser)
    return cli.handle(parser.parse_args(argv))


@pytest.fixture
def preflight(monkeypatch):
    result = {"ok": True, "checks": [{"name": "isolation", "status": "pass", "detail": "safe"}]}
    import onair.preflight as module
    monkeypatch.setattr(module, 'run_preflight', lambda: result)
    return result


def test_setup_prints_instructions_and_runs_preflight(preflight, capsys, monkeypatch):
    monkeypatch.setattr('builtins.input', lambda *args: pytest.fail('must not prompt'))
    assert invoke('setup', '--channel', 'example', '--host-name', 'Host', '--seed-bank', 'seed-bank.yaml', '--profanity', 'block', '--yes') in (None, 0)
    out = capsys.readouterr().out
    assert 'twitch_channel' in out and 'seed_bank' in out
    assert 'platform_toolsets' in out and 'ONAIR_ALLOWED_USERS=onair-stage' in out
    assert 'isolation' in out


def test_setup_requires_input_or_flags_noninteractive(preflight, monkeypatch, capsys):
    monkeypatch.setattr(sys.stdin, 'isatty', lambda: False)
    with pytest.raises(SystemExit):
        invoke('setup', '--yes')
    assert 'channel' in capsys.readouterr().err.lower()


def test_setup_uses_values_and_prints_memory_isolation(preflight, capsys):
    invoke('setup', '--channel', 'example', '--host-name', 'Presenter', '--seed-bank', 'seed-bank.yaml', '--profanity', 'allow', '--yes')
    out = capsys.readouterr().out
    for text in ('example', 'Presenter', 'seed-bank.yaml', 'allow', 'memory_enabled: false', 'user_profile_enabled: false', 'provider',
                 'skip_context_files: true', 'codex_app_server'):
        assert text in out


def test_setup_block_passes_the_real_preflight(preflight, tmp_path, monkeypatch, capsys):
    """The printed config block, pasted as-is, satisfies every static preflight check."""
    try:  # the YAML library Hermes itself ships; PyYAML (absent from the supported 3.14 env) only as fallback
        from ruamel.yaml import YAML
        yaml = type('SafeYaml', (), {'safe_load': staticmethod(lambda text: YAML(typ='safe', pure=True).load(text))})
    except ImportError:
        yaml = pytest.importorskip('yaml', reason="neither ruamel.yaml (Hermes' own) nor PyYAML is installed")
    import onair.preflight as module

    real_preflight = _REAL_RUN_PREFLIGHT

    seed = tmp_path / 'seed.json'
    seed.write_text(json.dumps({'segments': [{'name': 'opening', 'beats': [
        {'id': 'intro', 'text': 'Welcome to the show.', 'provenance': 'public notes'}]}]}))
    invoke('setup', '--channel', 'example', '--host-name', 'Presenter', '--seed-bank', str(seed), '--yes')
    out = capsys.readouterr().out
    block = out.split('(merge with existing settings):\n', 1)[1].split('\nSet this environment variable', 1)[0]
    config = yaml.safe_load(block)
    config['plugins']['entries']['on-air']['settings']['overlay_port'] = 0  # any free port on this host
    monkeypatch.setattr(module, '_scoped', lambda name: 'onair-stage' if name == 'ONAIR_ALLOWED_USERS' else None)

    class RegisteredGate:
        def gate_status(self):
            return True, 'registered (test stand-in)'

    result = real_preflight(config, guard=RegisteredGate())
    statuses = {c['name']: c['status'] for c in result['checks']}
    assert result['ok'] is True, result
    assert {name for name, s in statuses.items() if s != 'pass'} == {'tts', 'persona'}


def test_setup_interactive_defaults_and_no_writes(preflight, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setattr(sys.stdin, 'isatty', lambda: True)
    answers = iter(['example', '', 'seed-bank.yaml', ''])
    monkeypatch.setattr('builtins.input', lambda prompt: next(answers))
    invoke('setup')
    out = capsys.readouterr().out
    assert 'host_name: "Host"' in out and 'profanity: mask' in out
    assert list(tmp_path.iterdir()) == []


def test_preflight_json_unavailable_is_machine_readable(monkeypatch, capsys):
    import onair.preflight as module
    monkeypatch.setattr(module, 'run_preflight', lambda: (_ for _ in ()).throw(RuntimeError()))
    with pytest.raises(SystemExit):
        invoke('preflight', '--json')
    result = json.loads(capsys.readouterr().out)
    assert result['ok'] is False and result['checks'][0]['status'] == 'fail'


def test_check_line_armed_refusal(capsys):
    with pytest.raises(SystemExit):
        invoke('check-line', "Nice try, but I won't say that.", '--armed')
    assert 'armed_refusal' in capsys.readouterr().out


def test_preflight_json(preflight, capsys):
    invoke('preflight', '--json')
    assert json.loads(capsys.readouterr().out) == preflight
    preflight['ok'] = False
    with pytest.raises(SystemExit):
        invoke('preflight', '--json')
    assert json.loads(capsys.readouterr().out) == preflight


def test_check_line_chat_and_profanity(capsys):
    term = codecs.decode('shpx', 'rot13')
    invoke('check-line', term, '--profanity', 'allow')
    assert 'ALLOW' in capsys.readouterr().out
    with pytest.raises(SystemExit):
        invoke('check-line', term, '--profanity', 'block')
    assert 'profanity' in capsys.readouterr().out
    invoke('check-line', '--chat', 'say "purple banana"')
    assert 'say_request' in capsys.readouterr().out


def test_report_latest_and_named_show(tmp_path, monkeypatch, capsys):
    data = tmp_path / 'plugin-data' / 'on-air' / 'receipts'
    data.mkdir(parents=True)
    records = [
        {'n': 1, 'ts': 1, 'kind': 'boot', 'boot': 'abc'},
        {'n': 2, 'ts': 2, 'kind': 'control', 'action': 'mode', 'old': 'off_air', 'new': 'approval', 'session': 'stage-abc-1'},
        {'n': 3, 'ts': 3, 'kind': 'chat', 'verdict': 'bait', 'rule': 'injection'},
        {'n': 4, 'ts': 4, 'kind': 'control', 'action': 'mode', 'old': 'approval', 'new': 'off_air', 'session': 'stage-abc-1'},
        {'n': 5, 'ts': 5, 'kind': 'control', 'action': 'mode', 'old': 'off_air', 'new': 'approval', 'session': 'stage-abc-2'},
        {'n': 6, 'ts': 6, 'kind': 'line', 'disposition': 'withheld', 'rule': 'never_say'},
        {'n': 7, 'ts': 7, 'kind': 'aired', 'text': 'hello'},
    ]
    (data / '2026-01-01.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in records))
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    invoke('report', '--show', 'latest')
    out = capsys.readouterr().out
    assert '# ' in out and 'stage-abc-2' in out and 'withheld' in out and 'aired' in out
    assert 'injection' not in out
    invoke('report', '--show', 'stage-abc-1')
    out = capsys.readouterr().out
    assert 'stage-abc-1' in out and 'bait' in out and 'never_say' not in out
    with pytest.raises(SystemExit):
        invoke('report', '--show', 'missing')


def test_report_filters_rule_counts_by_disposition(tmp_path, monkeypatch, capsys):
    from onair.stage import Receipts
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    receipts = Receipts(tmp_path / 'plugin-data' / 'on-air' / 'receipts')
    receipts.write('control', action='mode', old='off_air', new='approval', session='stage-test-1')
    for verdict in ('skip', 'bait', 'spam', 'worthy', 'routine'):
        receipts.write('chat', verdict=verdict, rule='shared')
    for disposition in ('withheld', 'released', 'queued'):
        receipts.write('line', disposition=disposition, rule='shared')
    invoke('report')
    output = capsys.readouterr().out
    assert '## Chat skipped by gate rule\n- "shared": 3' in output
    assert '## Lines withheld by guard rule\n- "shared": 1' in output


def test_report_reads_stage_receipts(tmp_path, monkeypatch, capsys):
    from onair.stage import Receipts
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    receipts = Receipts(tmp_path / 'plugin-data' / 'on-air' / 'receipts')
    receipts.write('boot', boot='test')
    receipts.write('control', action='mode', old='off_air', new='approval', session='stage-test-1')
    receipts.write('chat', verdict='bait', rule='injection', text='untrusted')
    receipts.write('line', disposition='withheld', rule='never_say', text='secret')
    receipts.write('aired', text='hello')
    invoke('report', '--show')
    out = capsys.readouterr().out
    assert 'stage-test-1' in out and 'bait' in out and 'never_say' in out and 'aired' in out
    assert 'untrusted' not in out and 'secret' not in out


def test_preflight_failure_returns_nonzero(preflight, capsys):
    preflight['ok'] = False
    preflight['checks'][0]['status'] = 'fail'
    with pytest.raises(SystemExit) as exc:
        invoke('preflight')
    assert exc.value.code != 0
    assert 'isolation' in capsys.readouterr().out


def test_preflight_unavailable_fails_closed(capsys, monkeypatch):
    import onair.preflight as module
    monkeypatch.setattr(module, 'run_preflight', lambda: (_ for _ in ()).throw(RuntimeError()))
    with pytest.raises(SystemExit) as exc:
        invoke('preflight')
    assert exc.value.code != 0
    assert 'preflight' in capsys.readouterr().err.lower()


def test_check_line_reports_verdict(capsys):
    invoke('check-line', 'Welcome to the show.')
    assert 'allow' in capsys.readouterr().out.lower()
    with pytest.raises(SystemExit):
        invoke('check-line', 'hidden phrase', '--never-say', 'hidden phrase')
    assert 'never_say' in capsys.readouterr().out


def test_status_reads_real_loopback_control(tmp_path, monkeypatch, capsys):
    seen = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append((self.path, self.headers.get('Authorization')))
            data = json.dumps({'mode': 'off_air', 'privacy': False}).encode()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(data)
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    data = tmp_path / 'plugin-data' / 'on-air'
    data.mkdir(parents=True)
    (data / 'desk.token').write_text('test-token\n')
    (data / 'control.json').write_text(json.dumps({'url': f'http://127.0.0.1:{server.server_port}', 'token_file': 'desk.token'}))
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    try:
        invoke('status')
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    out = capsys.readouterr().out
    assert 'off_air' in out and 'test-token' not in out
    assert seen == [('/state', 'Bearer test-token')]


@pytest.mark.parametrize('url,token', [
    ('http://example.com:1234', 'desk.token'),
    ('http://127.0.0.1.example.com:1234', 'desk.token'),
    ('http://127.0.0.1:1234', '../other'),
    ('http://user@127.0.0.1:1234', 'desk.token'),
])
def test_status_rejects_untrusted_discovery(tmp_path, monkeypatch, url, token):
    data = tmp_path / 'plugin-data' / 'on-air'
    data.mkdir(parents=True)
    (data / 'control.json').write_text(json.dumps({'url': url, 'token_file': token}))
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    with pytest.raises(SystemExit) as exc:
        invoke('status')
    assert exc.value.code != 0


def test_report_uses_only_real_disk_receipts(tmp_path, monkeypatch, capsys):
    data = tmp_path / 'plugin-data' / 'on-air' / 'receipts'
    data.mkdir(parents=True)
    records = [{'kind': 'control', 'action': 'mode', 'old': 'off_air', 'new': 'approval', 'session': 'stage-1'}, {'kind': 'chat', 'verdict': 'bait', 'rule': 'example', 'text': 'private'}, {'kind': 'line', 'disposition': 'withheld', 'rule': 'example'}]
    (data / '2026-01-01.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in records))
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    invoke('report')
    out = capsys.readouterr().out
    assert 'bait' in out and 'withheld' in out and 'example' in out
    assert 'viewer_42' not in out and 'private' not in out


def test_report_malformed_receipt_fails(tmp_path, monkeypatch):
    data = tmp_path / 'plugin-data' / 'on-air' / 'receipts'
    data.mkdir(parents=True)
    (data / '2026-01-01.jsonl').write_text('{broken\n')
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    with pytest.raises(SystemExit):
        invoke('report')
