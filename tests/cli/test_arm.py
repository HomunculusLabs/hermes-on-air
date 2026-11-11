import asyncio
import json

import pytest

pytest.importorskip('gateway')

from gateway.config import PlatformConfig
from onair.adapter import OnAirAdapter
from onair.stage import ControlError


def test_real_adapter_fails_closed_on_preflight_error(tmp_path, monkeypatch):
    import onair.adapter as adapter
    import onair.preflight as preflight

    monkeypatch.setattr(adapter, 'data_dir', lambda: tmp_path)
    monkeypatch.setattr(preflight, 'run_preflight', lambda **kw: {'ok': False, 'checks': [{'name': 'toolsets', 'status': 'fail', 'detail': 'unsafe tools'}]})
    from gateway.platform_registry import PlatformEntry, platform_registry
    platform_registry.register(PlatformEntry(name='on_air', label='On Air', adapter_factory=OnAirAdapter, check_fn=lambda: True, source='plugin'))
    a = OnAirAdapter(PlatformConfig(enabled=True, extra={'overlay_port': 0}))

    async def run():
        assert await a.connect()
        assert a.controller.snapshot()['preflight']['ok'] is False
        with pytest.raises(ControlError, match='unsafe tools'):
            a.controller.set_mode('approval')
        assert a.controller.state.mode == 'off_air'
        await a.disconnect()
    asyncio.run(run())


def test_real_adapter_rechecks_effective_configuration(tmp_path, monkeypatch, plugin_registration):
    import onair.adapter as adapter
    import onair.preflight as preflight
    config_module = pytest.importorskip('hermes_cli.config')
    from gateway.platform_registry import PlatformEntry, platform_registry
    from onair.sources import twitch_irc

    bank = tmp_path / 'bank.json'
    bank.write_text(json.dumps({'segments': [{'name': 'intro', 'beats': [{'id': 'beat-one', 'text': 'Welcome.', 'provenance': 'notes'}]}]}))
    cfg = {'gateway': {'platforms': {'on_air': {'enabled': True}}},
           'platform_toolsets': {'on_air': []},
           'memory': {'memory_enabled': False, 'user_profile_enabled': False, 'provider': None},
           'voice': {'auto_tts': False}, 'context': {'engine': 'compressor'},
           'plugins': {'entries': {'on-air': {'settings': {'seed_bank': str(bank), 'twitch_channel': 'example', 'overlay_port': 0}}}}}
    monkeypatch.setattr(config_module, 'load_config_readonly', lambda: cfg)
    monkeypatch.setattr(preflight, '_scoped', lambda name: 'onair-stage' if name == 'ONAIR_ALLOWED_USERS' else None)
    monkeypatch.setattr(adapter, 'data_dir', lambda: tmp_path)
    class FakeTwitch:
        def __init__(self, *args, **kwargs): pass
        def start(self): pass
        async def stop(self): pass
    monkeypatch.setattr(twitch_irc, 'TwitchChatSource', FakeTwitch)
    platform_registry.register(PlatformEntry(name='on_air', label='On Air', adapter_factory=OnAirAdapter, check_fn=lambda: True, source='plugin'))
    a = OnAirAdapter(PlatformConfig(enabled=True, extra={'overlay_port': 0, 'twitch_channel': 'example', 'seed_bank': str(bank)}))
    async def run():
        assert await a.connect()
        assert a.controller.snapshot()['preflight']['ok'] is False
        with pytest.raises(ControlError, match='no_mcp'):
            a.controller.set_mode('approval')
        cfg['platform_toolsets']['on_air'] = ['no_mcp']
        with pytest.raises(ControlError, match='no_mcp'):
            a.controller.set_mode('approval')
        await a.disconnect()
        assert await a.connect()
        with pytest.raises(ControlError, match='no_mcp'):
            a.controller.set_mode('approval')
        await a.disconnect()
        new_adapter = OnAirAdapter(PlatformConfig(enabled=True, extra={'overlay_port': 0, 'twitch_channel': 'example', 'seed_bank': str(bank)}))
        assert await new_adapter.connect()
        assert new_adapter.controller.set_mode('approval')['mode'] == 'approval'
        cfg['memory']['memory_enabled'] = True
        with pytest.raises(ControlError, match='memory'):
            new_adapter.controller.set_mode('autonomous')
        assert new_adapter.controller.state.mode == 'approval'
        await new_adapter.disconnect()
    asyncio.run(run())
