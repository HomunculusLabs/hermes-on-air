"""Catalog credential boundaries are profile-scoped and never read dotenv directly."""
import ast
from pathlib import Path
import logging
import builtins
import yaml

import pytest


@pytest.fixture
def scope():
    return pytest.importorskip('agent.secret_scope')


def test_scope_wins_and_multiplex_miss_does_not_use_process_env(scope, monkeypatch, caplog):
    from onair.secrets import get
    monkeypatch.setenv('ONAIR_OBS_PASSWORD', 'foreign-value')
    multi = scope.set_multiplex_context(True)
    token = scope.set_secret_scope({'ONAIR_OBS_PASSWORD': 'scoped-value'})
    try:
        with caplog.at_level(logging.DEBUG):
            assert get('ONAIR_OBS_PASSWORD') == 'scoped-value'
            scope.reset_secret_scope(token)
            token = scope.set_secret_scope({})
            assert get('ONAIR_OBS_PASSWORD') == ''
        assert 'scoped-value' not in caplog.text and 'foreign-value' not in caplog.text
    finally:
        scope.reset_secret_scope(token)
        scope.reset_multiplex_context(multi)


def test_presentation_and_dashboard_use_scope(scope, monkeypatch):
    from onair.presentation.agent import _config
    from dashboard import plugin_api
    monkeypatch.setenv('ONAIR_OBS_PASSWORD', 'foreign-value')
    multi = scope.set_multiplex_context(True)
    token = scope.set_secret_scope({'ONAIR_OBS_PASSWORD': 'scoped-value'})
    try:
        config, _ = _config(None, {})
        assert config.password() == 'scoped-value'
        assert plugin_api._views_sanitize({'value': 'scoped-value'}) == {'value': '[redacted]'}
        scope.reset_secret_scope(token)
        token = scope.set_secret_scope({})
        assert config.password() == ''
    finally:
        scope.reset_secret_scope(token)
        scope.reset_multiplex_context(multi)


def test_jev_env_uses_scope(scope, monkeypatch):
    from onair.adapter import _env
    monkeypatch.setenv('ONAIR_JEV_API_KEY', 'foreign-value')
    multi = scope.set_multiplex_context(True)
    token = scope.set_secret_scope({'ONAIR_JEV_API_KEY': 'scoped-value'})
    try:
        assert _env('jev_api_key') == 'scoped-value'
        scope.reset_secret_scope(token)
        token = scope.set_secret_scope({})
        assert _env('jev_api_key') is None
    finally:
        scope.reset_secret_scope(token)
        scope.reset_multiplex_context(multi)


def test_stage_sentinel_uses_selected_scope_not_process(scope, monkeypatch, tmp_path):
    from onair.setup_wizard import _env_value
    monkeypatch.setenv('ONAIR_ALLOWED_USERS', 'onair-stage')
    monkeypatch.setattr(scope, 'build_profile_secret_scope', lambda home: {})
    assert _env_value(tmp_path, 'ONAIR_ALLOWED_USERS') == ''
    monkeypatch.setattr(scope, 'build_profile_secret_scope', lambda home: {'ONAIR_ALLOWED_USERS': 'onair-stage'})
    assert _env_value(tmp_path, 'ONAIR_ALLOWED_USERS') == 'onair-stage'


def test_no_direct_secret_environ_or_dotenv_reads():
    # Runtime has no dotenv-read exceptions. Follow names assigned a literal .env path.
    root = Path(__file__).resolve().parents[1]
    manifest = yaml.safe_load((root / 'plugin.yaml').read_text(encoding='utf-8'))
    entries = (manifest.get('requires_env') or []) + (manifest.get('optional_env') or [])
    secret_names = {e['name'] for e in entries if isinstance(e, dict) and (e.get('password') or e.get('secret'))}
    secret_names.update(k for k, v in (manifest.get('config_schema') or {}).items() if isinstance(v, dict) and v.get('type') == 'secret')
    assert {'ONAIR_OBS_PASSWORD', 'ONAIR_JEV_API_KEY'} <= secret_names
    violations = []
    # tools/verify_install.py is a developer-only harness reading its own synthetic
    # throwaway stage file and hashing its fixture; it is not imported by the plugin.
    # This exception is disclosed for maintainer review. Runtime modules have no exceptions.
    developer_allowlist = {'tools/verify_install.py': 'synthetic disposable verification home'}
    # These two bounded comprehensions only pass through OS/runtime keys, not plugin secrets.
    dynamic_allowlist = {
        ('onair/stage_runtime.py', 184, 'os.environ[k]'): '_ENV_KEEP',
        ('onair/stage_runtime.py', 184, 'os.environ.get(k)'): '_ENV_KEEP',
        ('tools/verify_install.py', 238, 'os.environ[k]'): '_PASSTHROUGH',
    }
    paths = [root / '__init__.py'] + [p for folder in ('onair', 'dashboard', 'tools') for p in (root / folder).rglob('*.py')]
    for path in paths:
        tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
        dotenv_names = set()
        environ_names = {'environ'}
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and any(isinstance(x, ast.Constant) and x.value == '.env' for x in ast.walk(node.value)):
                dotenv_names.update(t.id for t in node.targets if isinstance(t, ast.Name))
            if isinstance(node, ast.Assign) and ast.unparse(node.value) == 'os.environ':
                environ_names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Call, ast.Subscript)):
                receiver = node.func if isinstance(node, ast.Call) else node.value
                receiver_text = ast.unparse(receiver)
                names = {n.id for n in ast.walk(receiver) if isinstance(n, ast.Name)}
                env_access = 'environ' in receiver_text or 'getenv' in receiver_text or bool(names & environ_names)
                rendered = ast.unparse(node)
                if env_access and any(name in rendered for name in secret_names):
                    violations.append(f'{path.relative_to(root)}:{node.lineno}: direct secret environment access')
                index = node.args[0] if isinstance(node, ast.Call) and node.args else node.slice if isinstance(node, ast.Subscript) else None
                if env_access and index is not None and not isinstance(index, ast.Constant):
                    allowed = dynamic_allowlist.get((str(path.relative_to(root)), node.lineno, rendered))
                    assignments = [n.value for n in tree.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == allowed for t in n.targets)]
                    try:
                        safe = bool(allowed and len(assignments) == 1 and not (set(ast.literal_eval(assignments[0])) & secret_names))
                    except (TypeError, ValueError):
                        safe = False
                    if not safe:
                        violations.append(f'{path.relative_to(root)}:{node.lineno}: dynamic environment lookup')
            if isinstance(node, ast.Call):
                text = ast.unparse(node)
                names = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
                refers = any(isinstance(n, ast.Constant) and n.value == '.env' for n in ast.walk(node)) or bool(names & dotenv_names)
                read = (isinstance(node.func, ast.Attribute) and node.func.attr in {'read_text', 'read_bytes', 'read', 'readline', 'readlines'})
                if isinstance(node.func, ast.Name) and node.func.id == 'open':
                    mode = node.args[1] if len(node.args) > 1 else next((k.value for k in node.keywords if k.arg == 'mode'), None)
                    read = mode is None or not isinstance(mode, ast.Constant) or not isinstance(mode.value, str) or 'r' in mode.value or '+' in mode.value
                if refers and read and str(path.relative_to(root)) not in developer_allowlist:
                    violations.append(f'{path.relative_to(root)}:{node.lineno}: dotenv read')
    assert not violations, '\n'.join(violations)


def test_missing_hermes_fails_closed(monkeypatch):
    from onair.secrets import get
    original = builtins.__import__
    def missing(name, *args, **kwargs):
        if name == 'agent.secret_scope':
            raise ImportError('unavailable')
        return original(name, *args, **kwargs)
    monkeypatch.setenv('ONAIR_OBS_PASSWORD', 'foreign-value')
    monkeypatch.setattr(builtins, '__import__', missing)
    assert get('ONAIR_OBS_PASSWORD') == ''


def test_unbound_multiplex_error_is_not_swallowed(scope):
    from onair.secrets import get
    multi = scope.set_multiplex_context(True)
    token = scope.set_secret_scope(None)
    try:
        with pytest.raises(scope.UnscopedSecretError):
            get('ONAIR_OBS_PASSWORD')
    finally:
        scope.reset_secret_scope(token)
        scope.reset_multiplex_context(multi)


def test_jev_runner_reloads_scope_for_each_call(scope, monkeypatch):
    from onair import assist
    from onair.adapter import _assist_runner
    observed = []
    def transport(host, port, *, https=False, headers=None):
        observed.append(headers['Authorization'])
        return lambda *args: (401, {})
    monkeypatch.setattr(assist, 'http_transport', transport)
    multi = scope.set_multiplex_context(True)
    token = scope.set_secret_scope({'ONAIR_JEV_API_KEY': 'first-scoped-value'})
    try:
        runner = _assist_runner({'provider': 'jev'})
        request = assist.AssistRequest('line', 1, 'turn', 'hello')
        assert runner.decide_sync(request).status == 'unavailable'
        scope.reset_secret_scope(token)
        token = scope.set_secret_scope({'ONAIR_JEV_API_KEY': 'second-scoped-value'})
        assert runner.decide_sync(request).status == 'unavailable'
        scope.reset_secret_scope(token)
        token = scope.set_secret_scope({})
        monkeypatch.setenv('ONAIR_JEV_API_KEY', 'foreign-secret-value')
        assert runner.decide_sync(request).status == 'unavailable'
        assert observed == ['Bearer first-scoped-value', 'Bearer second-scoped-value']
        assert 'scoped-value' not in repr(runner.__dict__)
    finally:
        scope.reset_secret_scope(token)
        scope.reset_multiplex_context(multi)


def test_preflight_and_presentation_loop_preserve_scope(scope, monkeypatch):
    from onair.preflight import _scoped
    from onair.presentation.agent import _config, Runtime
    monkeypatch.setenv('ONAIR_OBS_PASSWORD', 'foreign-value')
    multi = scope.set_multiplex_context(True)
    token = scope.set_secret_scope({'ONAIR_OBS_PASSWORD': 'scoped-value', 'ONAIR_ALLOWED_USERS': 'onair-stage'})
    runtime = None
    try:
        assert _scoped('ONAIR_ALLOWED_USERS') == 'onair-stage'
        config, _ = _config(None, {})
        runtime = Runtime(config, port=0)
        async def resolve():
            return config.password()
        assert runtime.call(resolve()) == 'scoped-value'
    finally:
        if runtime is not None:
            runtime.teardown()
        scope.reset_secret_scope(token)
        scope.reset_multiplex_context(multi)
