import asyncio
import json

from onair import setup_agent as sa, stage_runtime as sr
from test_quick_setup import owner  # noqa: F401
from onair.control import ControlServer
from onair.stage import StageConfig, StageController


class Context:
    def __init__(self, surface='desktop', choice='approve'):
        self.surface, self.choice, self.cards = surface, choice, []

    def session_surface(self):
        return self.surface

    def ask_choice(self, text, choices):
        self.cards.append(text)
        return self.choice


def test_tool_owner_surface_and_typed_confirmation(monkeypatch):
    monkeypatch.setattr(sa.se, 'is_stage_home', lambda: False)
    calls = []
    monkeypatch.setattr(sr, 'relay_host_turn', lambda eng, text: calls.append(text) or {'ok': True, 'queued': True})
    handler = sa.make_handlers(Context(), lambda: object())['onair_stage_host_turn']
    assert json.loads(handler({'text': 'Exact words'}))['queued']
    assert not json.loads(handler({'text': 'x', 'extra': 1}))['ok']
    assert json.loads(sa.make_handlers(Context('on_air'), lambda: object())['onair_stage_host_turn']({'text': 'x'}))['refused']
    assert calls == ['Exact words']
    actions = []
    monkeypatch.setattr(sr, 'owner_command', lambda eng, action, value=None: actions.append((action, value)) or {'message': 'accepted'})
    ctx = Context(choice=None)
    cmd = sa.make_stage_command(ctx, lambda: object())
    assert 'nothing' in cmd('privacy off').lower() and not actions
    ctx.choice = 'approve'
    assert cmd('privacy off') == 'accepted'
    assert cmd('mute off') == 'accepted'
    assert actions == [('privacy_off', None), ('mute_off', None)] and len(ctx.cards) == 3


def test_relay_real_desk_settling_and_success(tmp_path, monkeypatch):
    async def go():
        home = tmp_path / 'stage'
        now = [100.0]
        c = StageController(StageConfig(host_turns=True), monotonic=lambda: now[0])
        c.set_mode('approval')
        server = ControlServer(c, home / 'plugin-data' / 'on-air')
        await server.start()
        class Engine:
            def _read(self, key):
                return {'home': str(home), 'pid': 123}
        monkeypatch.setattr(sr, 'saved_stage', lambda eng: (home, ''))
        monkeypatch.setattr(sr, '_alive', lambda record: True)
        monkeypatch.setattr(sr, '_control', lambda home, pid: {'url': server.url, 'token_file': 'desk.token'})
        try:
            result = await asyncio.to_thread(sr.relay_host_turn, Engine(), 'My words')
            assert result['ok'] is False and result['reason'] == 'settling'
            now[0] += 20
            result = await asyncio.to_thread(sr.relay_host_turn, Engine(), 'My words')
            assert result['ok'] and result['queued'] and 'aired' not in result.get('message', '').lower().replace('not aired', '')
            assert c.receipts.tail()[-1]['via'] == 'relay'
            for action in ('privacy_off', 'mute_off'):
                c.set_privacy(True) if action == 'privacy_off' else c.set_muted(True)
                result = await asyncio.to_thread(sr.owner_command, Engine(), action)
                assert result['ok']
                assert not (c.state.privacy if action == 'privacy_off' else c.state.muted)
        finally:
            await server.stop()
    asyncio.run(go())


def test_readiness_surfaces_host_relay_settings(owner, tmp_path, monkeypatch):
    from test_stage_runtime import _made_stage, _prepare
    from onair.setup_wizard import readiness
    eng, home, _, call, _ = _made_stage(owner, tmp_path, monkeypatch)
    _prepare(home)
    config = home / 'config.yaml'
    config.write_text(config.read_text().replace('voice: false', 'host_turns: true\n        host_relay_settle_seconds: 35\n        host_relay_autonomous: true\n        voice: false'))
    monkeypatch.setattr(sr, 'hermes_command', lambda: ['hermes'])
    monkeypatch.setattr(sr, 'install_isolation', lambda _: [])
    monkeypatch.setattr(sr, '_stage_preflight', lambda *_: {'ok': True, 'missing': []})
    row = next(r for r in readiness(str(home))['rows'] if r['key'] == 'host_turns')
    assert 'ON' in row['detail'] and '35' in row['detail'] and 'autonomous relays are ON' in row['detail']
    status = call('onair_stage_status')
    item = next(p for p in status['prerequisites'] if p['key'] == 'host_turns')
    assert item['ok'] and '35' in item['detail']


def test_saved_stage_allowlist_limits_relay_shape(monkeypatch):
    sent = []
    monkeypatch.setattr(sr, '_desk_request', lambda *args: sent.append(args) or (200, {'ok': True}))
    for path, body in [('/host/turn', {'text': 'x', 'epoch': 1}),
                       ('/host/turn', {'text': 'x', 'epoch': 1, 'via': 'typed'}),
                       ('/host/turn/extra', {'text': 'x', 'epoch': 1, 'via': 'relay'}),
                       ('/privacy', {'on': False}), ('/mute', {'on': False})]:
        code, _ = sr.desk_call(None, {}, 'POST', path, body)
        assert code in (403, 404)
    assert not sent
    code, _ = sr.desk_call(None, {}, 'POST', '/host/turn', {'text': 'x', 'epoch': 1, 'boot': 'a'*32, 'via': 'relay'})
    assert code == 200 and len(sent) == 1
