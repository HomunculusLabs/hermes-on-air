"""The cut action is a safety-only, boot-fenced stage operation."""
import asyncio
from onair import setup_agent, stage_runtime
from onair.control import ControlServer
from onair.stage import StageConfig, StageController


def test_cut_tool_and_owner_command_use_real_stage(tmp_path, monkeypatch):
    async def run():
        home = tmp_path / 'stage'
        controller = StageController(StageConfig(host_turns=True))
        controller.set_mode('approval')
        server = ControlServer(controller, home / 'plugin-data' / 'on-air')
        await server.start()
        class Engine:
            def _read(self, key):
                return {'home': str(home), 'pid': 123}
        monkeypatch.setattr(stage_runtime, 'saved_stage', lambda eng: (home, ''))
        monkeypatch.setattr(stage_runtime, '_alive', lambda record: True)
        monkeypatch.setattr(stage_runtime, '_control', lambda home, pid: {'url': server.url, 'token_file': 'desk.token'})
        try:
            old = controller.state.epoch
            controller.queue_host_turn('synthetic line', old)
            result = await asyncio.to_thread(stage_runtime.safe_control, Engine(), 'cut_in')
            assert result['ok'] and controller.state.epoch == old + 1
            assert controller.state.mode == 'approval' and not controller.state.privacy and not controller.state.muted
            assert not controller._host_pending
            result = await asyncio.to_thread(stage_runtime.owner_command, Engine(), 'cut')
            assert result['ok'] and controller.state.epoch == old + 2
            controller.set_privacy(True)
            old = controller.state.epoch
            result = await asyncio.to_thread(stage_runtime.safe_control, Engine(), 'cut_in')
            assert not result['ok'] and controller.state.epoch == old and controller.state.privacy
            for action in ('approve', 'unmute', 'privacy_off', 'arm'):
                assert not stage_runtime.safe_control(Engine(), action)['ok']
        finally:
            await server.stop()
    assert 'cut_in' in setup_agent.SCHEMAS['onair_stage_control']['parameters']['properties']['action']['enum']
    asyncio.run(run())


def test_owner_slash_cut_uses_only_cut_action(monkeypatch):
    class Context:
        def session_surface(self):
            return 'cli'
    calls = []
    monkeypatch.setattr(setup_agent.se, 'is_stage_home', lambda: False)
    monkeypatch.setattr(stage_runtime, 'owner_command', lambda eng, action, value=None: calls.append((action, value)) or {'message': 'done'})
    command = setup_agent.make_stage_command(Context(), lambda: object())
    assert command('cut') == 'done'
    assert calls == [('cut', None)]
    command('cut extra')
    assert calls == [('cut', None)]
