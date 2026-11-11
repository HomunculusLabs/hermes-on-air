import json

from onair import setup_agent as sa


class Context:
    def __init__(self, surface):
        self.surface = surface

    def session_surface(self):
        return self.surface


def test_safe_control_refuses_unsafe_actions_and_public(monkeypatch):
    monkeypatch.setattr(sa.se, 'is_stage_home', lambda: False)
    handler = sa.make_handlers(Context('desktop'), lambda: object())['onair_stage_control']
    for action in ('approve', 'mode', 'privacy_off', 'mute_off'):
        result = json.loads(handler({'action': action}))
        assert result['ok'] is False
    denied = sa.make_handlers(Context('on_air'), lambda: object())['onair_stage_control']
    assert json.loads(denied({'action': 'state'}))['refused'] is True


def test_typed_stage_commands_require_owner_and_route_actions(monkeypatch):
    monkeypatch.setattr(sa.se, 'is_stage_home', lambda: False)
    calls = []
    def owner_call(_eng, action, value=None):
        calls.append((action, value))
        return {'ok': True, 'message': 'done'}
    monkeypatch.setattr(sa.sr, 'owner_command', owner_call)
    command = sa.make_stage_command(Context('desktop'), lambda: object())
    assert 'done' in command('say Keep this exact line')
    assert calls == [('say', 'Keep this exact line')]
    assert 'done' in command('mode approval')
    assert calls[-1] == ('mode', 'approval')
    denied = sa.make_stage_command(Context('on_air'), lambda: object())
    assert 'not available' in denied('say hello')
    assert len(calls) == 2


def test_owner_command_uses_real_desk(tmp_path, monkeypatch):
    import asyncio
    from onair import stage_runtime as sr
    from onair.control import ControlServer
    from onair.stage import StageConfig, StageController
    from onair.types import Verdict

    async def run():
        home = tmp_path / 'stage'
        c = StageController(StageConfig(host_turns=True, turn_gap_s=0), guard=lambda line, ctx: Verdict(True, 'ok', ''))
        server = ControlServer(c, home / 'plugin-data' / 'on-air')
        await server.start()
        class Engine:
            def _read(self, key):
                return {'home': str(home), 'pid': 123}
        eng = Engine()
        monkeypatch.setattr(sr, 'saved_stage', lambda eng: (home, ''))
        monkeypatch.setattr(sr, '_alive', lambda record: True)
        monkeypatch.setattr(sr, '_control', lambda home, pid: {'url': server.url, 'token_file': 'desk.token'})
        monkeypatch.setattr(sa.se, 'is_stage_home', lambda: False)
        ctx = Context('local')
        previews = []
        ctx.ask_choice = lambda text, choices: previews.append(text) or 'approve'
        command = sa.make_stage_command(ctx, lambda: eng)
        try:
            assert 'accepted' in (await asyncio.to_thread(command, 'mode approval')).lower()
            assert 'queued' in (await asyncio.to_thread(command, 'say Hello co-host')).lower()
            turn = c.tick()
            assert turn and c.deliver_reply(turn.turn_id, 'A safe answer.') == ['queued']
            c.turn_done(turn.turn_id)
            handler = sa.make_handlers(ctx, lambda: eng)['onair_stage_control']
            pending = json.loads(await asyncio.to_thread(handler, {'action': 'pending'}))
            assert pending['result']['pending'][0]['source'] == 'host'
            assert 'accepted' in (await asyncio.to_thread(command, 'approve')).lower()
            assert 'host' in previews[-1].lower()
            assert not c.pending_lines() and c.snapshot()['queued_to_air'] == 1
            out = await asyncio.to_thread(sr.safe_control, eng, 'privacy_on')
            assert out['ok'] is True and c.state.privacy is True
        finally:
            await server.stop()
    asyncio.run(run())


def test_approval_previews_oldest_and_requires_choice(monkeypatch):
    monkeypatch.setattr(sa.se, 'is_stage_home', lambda: False)
    calls = []
    def owner_call(_eng, action, value=None):
        calls.append((action, value))
        if action == 'pending':
            return {'ok': True, 'result': {'pending': [{'line_id': 'line1', 'epoch': 4, 'text': 'Line to review.'}]}}
        return {'ok': True, 'message': 'accepted'}
    monkeypatch.setattr(sa.sr, 'owner_command', owner_call)
    ctx = Context('local')
    previews = []
    ctx.ask_choice = lambda text, choices: previews.append(text) or 'approve'
    command = sa.make_stage_command(ctx, lambda: object())
    assert command('approve') == 'accepted'
    assert 'Line to review.' in previews[0]
    assert calls[-1] == ('approve', {'line_id': 'line1', 'epoch': 4})
    ctx.ask_choice = lambda text, choices: None
    assert command('approve') == 'Nothing was approved.'
    assert calls[-1][0] == 'pending'
