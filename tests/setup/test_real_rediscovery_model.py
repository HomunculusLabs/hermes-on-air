"""Force Hermes plugin rediscovery in-process without starting a gateway."""
import json

import pytest

from test_installed_entry import _install_copy, _run


def test_force_rediscovery_uses_current_host_facade(tmp_path):
    pytest.importorskip("hermes_cli.plugins")
    from hermes_cli.config_defaults import DEFAULT_CONFIG

    home = tmp_path / "owner"
    _install_copy(home)
    (home / "config.yaml").write_text(json.dumps({
        "_config_version": DEFAULT_CONFIG["_config_version"],
        "plugins": {"enabled": ["on-air"]},
        "model": {"provider": "fixture", "default": "fixture-model"},
    }))
    out = _run(tmp_path, home, """
        from types import SimpleNamespace
        from hermes_cli.plugins import PluginContext
        class Stale:
            def complete(self, **kw):
                raise RuntimeError('fixture failure')
        class Fresh:
            def __init__(self): self.calls = 0
            def complete(self, **kw):
                self.calls += 1
                return SimpleNamespace(text='OK', provider='fixture', model='fixture-model')
        stale, fresh = Stale(), Fresh()
        current = [stale]
        PluginContext.llm = property(lambda self: current[0])
        discover_plugins(force=True)
        entry = platform_registry.get('on_air')
        pkg = entry.adapter_factory.__module__.rsplit('.', 1)[0]
        se = importlib.import_module(pkg + '.setup_engine')
        eng = se.engine()
        answers = {'channel': 'cozy_fox', 'use_model': True,
                   'target': str(Path(sys.argv[0]).parent / 'stage')}
        bad = eng.plan(answers)
        out['bad'] = bad['ok']
        out['bad_action'] = bad['next_action']
        out['stage_created'] = Path(answers['target']).exists()
        current[0] = fresh
        good = eng.plan(answers)
        out['good'] = good['ok']
        out['state'] = good['model_connection']['state']
        out['calls'] = fresh.calls
    """)
    assert out["bad"] is False and "Start a new Hermes session" in out["bad_action"]
    assert out["stage_created"] is False
    assert out["good"] is True and out["state"] == "ready" and out["calls"] == 1
