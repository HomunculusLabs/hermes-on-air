"""Shared fixtures for the On Air CLI / preflight / runtime-gate tests."""
from __future__ import annotations

import json

import pytest


@pytest.fixture(autouse=True)
def fresh_runtime_guard(monkeypatch):
    """Each test gets its own plugin-owned gate, so a latch never leaks between tests."""
    import onair.runtime_guard as runtime_guard

    guard = runtime_guard.RuntimeGuard()
    monkeypatch.setattr(runtime_guard, "_GUARD", guard)
    return guard


@pytest.fixture
def plugin_registration(monkeypatch, fresh_runtime_guard, tmp_path_factory):
    """Run the plugin's real ``register(ctx)`` against a real Hermes ``PluginContext`` on a private
    ``PluginManager`` (the platform lands in the real platform registry, as in the gateway).

    It registers the way a stage process does, in a marked stage home: there the plugin registers the
    co-host only and none of the owner's tools, which preflight would refuse to arm beside."""
    pytest.importorskip("gateway.platform_registry")
    plugins = pytest.importorskip("hermes_cli.plugins")
    from hermes_cli.plugins_manifest import PluginManifest

    import onair.adapter as adapter

    manager = plugins.PluginManager()
    ctx = plugins.PluginContext(PluginManifest(name="on-air", kind="platform"), manager)
    monkeypatch.setattr(adapter, "_PLUGIN_CTX", None)
    from onair.onboarding import MARKER

    stage_home = tmp_path_factory.mktemp("stage-home")
    (stage_home / MARKER).write_text("test stage home\n", encoding="utf-8")
    with monkeypatch.context() as registering:
        registering.setenv("HERMES_HOME", str(stage_home))
        adapter.register(ctx)
    adapter._PLUGIN_CTX = None  # no plugin settings / dispatch_tool: tests inject TTS themselves
    yield manager
    for handle in (fresh_runtime_guard.registration, fresh_runtime_guard.hook_registration):
        if handle is not None:
            handle.dispose()


@pytest.fixture
def safe_home(tmp_path, monkeypatch):
    """An isolated HERMES_HOME whose effective config passes every static preflight check."""
    home = tmp_path / "hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("ONAIR_ALLOWED_USERS", "onair-stage")
    bank = tmp_path / "bank.json"
    bank.write_text(json.dumps({"segments": [{"name": "intro", "beats": [
        {"id": "intro", "text": "Welcome.", "provenance": "notes"}]}]}))
    config = {
        "gateway": {"platforms": {"on_air": {"enabled": True, "skip_context_files": True}}},
        "platform_toolsets": {"on_air": ["no_mcp"]},
        "memory": {"memory_enabled": False, "user_profile_enabled": False, "provider": None},
        "voice": {"auto_tts": False}, "context": {"engine": "compressor"},
        "plugins": {"entries": {"on-air": {"settings": {
            "seed_bank": str(bank), "twitch_channel": "example", "overlay_port": 0}}}},
    }
    (home / "config.yaml").write_text(json.dumps(config))
    return home
