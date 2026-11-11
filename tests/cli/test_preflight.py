import copy
import json
import socket


import pytest

from onair.preflight import run_preflight


class RegisteredGate:
    """Stands in for a registered runtime gate so these tests exercise the static checks; the
    gate itself is tested through real Hermes registration in test_runtime_*.py."""

    def gate_status(self):
        return True, 'registered (test stand-in)'


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv('ONAIR_ALLOWED_USERS', 'onair-stage')
    import onair.preflight as preflight
    import onair.runtime_guard as runtime_guard
    monkeypatch.setattr(preflight, '_scoped', lambda name: 'onair-stage' if name == 'ONAIR_ALLOWED_USERS' else None)
    monkeypatch.setattr(runtime_guard, '_GUARD', RegisteredGate())
    seed = tmp_path / 'seed.json'
    seed.write_text(json.dumps({'segments': [{'name': 'opening', 'beats': [
        {'id': 'intro', 'text': 'Welcome to the show.', 'provenance': 'public notes'}]}]}))
    cfg = {'gateway': {'platforms': {'on_air': {'enabled': True, 'skip_context_files': True}}},
           'platform_toolsets': {'on_air': ['no_mcp']},
           'memory': {'memory_enabled': False, 'user_profile_enabled': False, 'provider': ''},
           'context': {'engine': 'compressor'}, 'voice': {'auto_tts': False},
           'plugins': {'entries': {'on-air': {'settings': {
               'seed_bank': str(seed), 'twitch_channel': 'public_channel', 'overlay_port': 0}}}}}
    return cfg, seed


def status(result, name):
    return next(c['status'] for c in result['checks'] if c['name'] == name)


def detail(result, name):
    return next(c['detail'] for c in result['checks'] if c['name'] == name)


def test_pass_and_persona_warning(setup):
    cfg, _ = setup
    result = run_preflight(cfg)
    assert result['ok'] is True
    assert all(set(c) == {'name', 'status', 'detail'} for c in result['checks'])
    assert status(result, 'persona') == 'warn'
    assert 'dedicated stage profile' in detail(result, 'persona') and 'SOUL.md' in detail(result, 'persona')
    for name in ('runtime_gate', 'runtime_tools', 'voice_modes', 'memory', 'agent_runtime', 'context_files'):
        assert status(result, name) == 'pass', name
    assert {c['name'] for c in result['checks'] if c['status'] == 'warn'} <= {'tts', 'persona'}


@pytest.mark.parametrize('section,value', [
    ('model', {'default': 'm', 'provider': 'openai', 'openai_runtime': 'codex_app_server'}),
    ('model', {'default': 'm', 'provider': 'openai-codex', 'openai_runtime': ' Codex_App_Server '}),
    ('model', {'default': 'm', 'provider': 'custom', 'api_mode': 'codex_app_server'}),
    ('providers', {'stage': {'base_url': 'http://127.0.0.1:1/v1', 'api_mode': 'codex-app-server'}}),
    ('custom_providers', [{'name': 'stage', 'api_mode': 'codex_app_server'}]),
])
def test_codex_app_server_runtime_fails(setup, section, value):
    cfg, _ = setup
    cfg[section] = value
    result = run_preflight(cfg)
    assert result['ok'] is False and status(result, 'agent_runtime') == 'fail'
    assert f'under: {section}' in detail(result, 'agent_runtime')
    assert 'http://' not in detail(result, 'agent_runtime')  # section names only, never values


@pytest.mark.parametrize('value', [
    {'default': 'm', 'provider': 'openai', 'openai_runtime': 'auto'},
    {'default': 'm', 'provider': 'openai-codex', 'api_mode': 'codex_responses'},
    {'default': 'm', 'provider': 'custom', 'api_mode': 'chat_completions', 'openai_runtime': ''},
])
def test_agent_loop_runtimes_pass(setup, value):
    cfg, _ = setup
    cfg['model'] = value
    result = run_preflight(cfg)
    assert result['ok'] is True and status(result, 'agent_runtime') == 'pass'


@pytest.mark.parametrize('value', [None, False, 'true', 1])
def test_context_files_warns_unless_skipped(setup, value):
    cfg, _ = setup
    platform = cfg['gateway']['platforms']['on_air']
    if value is None:
        platform.pop('skip_context_files')
    else:
        platform['skip_context_files'] = value
    result = run_preflight(cfg)
    assert result['ok'] is True and status(result, 'context_files') == 'warn'
    assert 'skip_context_files: true' in detail(result, 'context_files')
    assert status(result, 'persona') == 'warn'


def test_context_files_pass_is_honest_about_soul(setup):
    result = run_preflight(setup[0])
    assert status(result, 'context_files') == 'pass'
    assert 'SOUL.md still loads' in detail(result, 'context_files')


def test_unregistered_runtime_gate_fails(setup):
    from onair.runtime_guard import RuntimeGuard
    result = run_preflight(setup[0], guard=RuntimeGuard())
    assert result['ok'] is False
    assert status(result, 'runtime_gate') == 'fail' and status(result, 'runtime_tools') == 'fail'


def test_broken_gate_status_fails_closed(setup):
    class Broken:
        def gate_status(self):
            return 'yes', None
    assert not run_preflight(setup[0], guard=Broken())['ok']


@pytest.mark.parametrize('path,value,check', [
    (('platform_toolsets', 'on_air'), [], 'toolsets'),
    (('platform_toolsets', 'on_air'), ['no_mcp', 'terminal'], 'toolsets'),
    (('context', 'engine'), 'custom', 'context_engine'),
    (('gateway', 'platforms', 'on_air', 'enabled'), False, 'enabled'),
    (('gateway', 'platforms', 'on_air', 'trusted_inbound'), True, 'trusted_inbound'),
    (('memory', 'memory_enabled'), True, 'memory'),
    (('memory', 'user_profile_enabled'), True, 'memory'),
    (('memory', 'provider'), 'external', 'memory'),
    (('voice', 'auto_tts'), True, 'auto_tts'),
])
def test_unsafe_configuration_fails(setup, path, value, check):
    cfg, _ = setup
    obj = cfg
    for part in path[:-1]:
        obj = obj[part]
    obj[path[-1]] = value
    result = run_preflight(cfg)
    assert not result['ok']
    assert status(result, check) == 'fail'


@pytest.mark.parametrize('value', ['', '*', 'onair-stage,other', 'onair-stage,onair-stage'])
def test_exact_allowlist(setup, monkeypatch, value):
    import onair.preflight as preflight
    monkeypatch.setattr(preflight, '_scoped', lambda name: value if name == 'ONAIR_ALLOWED_USERS' else None)
    result = run_preflight(setup[0])
    assert not result['ok'] and status(result, 'allowed_users') == 'fail'


@pytest.mark.parametrize('setting,value,check', [
    ('twitch_channel', 'bad channel', 'channel'),
    ('never_say', ['ok', 42], 'never_say'),
    ('never_say', [' '], 'never_say'),
    ('profanity', 'maybe', 'profanity'),
    ('overlay_port', -1, 'overlay_port'),
])
def test_raw_settings_invalid(setup, setting, value, check):
    cfg, _ = setup
    settings = copy.deepcopy(cfg['plugins']['entries']['on-air']['settings'])
    settings[setting] = value
    result = run_preflight(cfg, settings)
    assert not result['ok'] and status(result, check) == 'fail'


def test_seed_rejected_if_empty_or_invalid(setup):
    cfg, seed = setup
    for content in ('not yaml: [', json.dumps({'segments': [{'name': 'opening', 'beats': []}]})):
        seed.write_text(content)
        result = run_preflight(cfg)
        assert not result['ok'] and status(result, 'seed_bank') == 'fail'


def test_port_probe_and_self_flag(setup):
    cfg, _ = setup
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        sock.listen()
        settings = dict(cfg['plugins']['entries']['on-air']['settings'], overlay_port=sock.getsockname()[1])
        assert status(run_preflight(cfg, settings), 'overlay_port') == 'fail'
        assert status(run_preflight(cfg, settings, port_in_use_by_self=True), 'overlay_port') == 'pass'


def test_config_loader_is_public_and_errors_fail_closed(setup, monkeypatch):
    cfg, _ = setup
    config_module = pytest.importorskip('hermes_cli.config')
    monkeypatch.setattr(config_module, 'load_config_readonly', lambda: cfg)
    assert run_preflight()['ok']
    def broken():
        raise RuntimeError('private data must not leak')
    monkeypatch.setattr(config_module, 'load_config_readonly', broken)
    result = run_preflight()
    assert not result['ok']
    assert 'private data' not in str(result)


def test_tts_warning_and_setting_override(setup):
    cfg, _ = setup
    result = run_preflight(cfg, dict(cfg['plugins']['entries']['on-air']['settings'], voice=True))
    assert result['ok'] and status(result, 'tts') == 'warn'
    assert status(run_preflight(cfg, dict(cfg['plugins']['entries']['on-air']['settings'], voice=False)), 'tts') == 'pass'


def test_setting_precedence(setup, monkeypatch):
    cfg, _ = setup
    monkeypatch.setenv('ONAIR_TWITCH_CHANNEL', 'bad channel')
    assert status(run_preflight(cfg), 'channel') == 'pass'
    cfg['gateway']['platforms']['on_air']['extra'] = {'twitch_channel': 'bad channel'}
    assert status(run_preflight(cfg), 'channel') == 'fail'
    assert status(run_preflight(cfg, {'twitch_channel': 'valid_channel'}), 'channel') == 'pass'
    assert status(run_preflight(cfg, {}), 'channel') == 'pass'


def test_scoped_secret_error_fails_closed(setup, monkeypatch):
    import onair.preflight as preflight
    def broken(name):
        raise RuntimeError('sensitive value')
    monkeypatch.setattr(preflight, '_scoped', broken)
    result = run_preflight(setup[0])
    assert not result['ok'] and 'sensitive value' not in str(result)


def test_malformed_structure_fails_closed(setup):
    cfg, _ = setup
    cfg['memory'] = 'off'
    assert not run_preflight(cfg)['ok']
    assert not run_preflight([], {})['ok']
