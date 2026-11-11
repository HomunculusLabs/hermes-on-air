"""Dashboard settings use the documented readonly configuration route."""
import pytest


def test_dashboard_settings_mapping_and_legacy_fallback(monkeypatch):
    config = pytest.importorskip('hermes_cli.config')
    from dashboard.plugin_api import _ViewsConfig
    current = {'plugins': {'entries': {'on-air': {'settings': {'x': 'new'}, 'config': {'x': 'old', 'y': 'legacy'}}}}}
    monkeypatch.setattr(config, 'load_config_readonly', lambda: current)
    assert _ViewsConfig().get_config('x') == 'new'
    assert _ViewsConfig().get_config('y') == 'legacy'
    assert _ViewsConfig().get_config('z') is None
    for malformed in (None, [], {'plugins': []}, {'plugins': {'entries': []}}, {'plugins': {'entries': {'on-air': []}}}):
        monkeypatch.setattr(config, 'load_config_readonly', lambda: malformed)
        assert _ViewsConfig().get_config('x') is None
