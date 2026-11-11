"""Work-share state must reject leaks before publishing anything."""
import json
import pytest

from conftest import make
from onair.presentation import agent


def call(ctx, args):
    return json.loads(ctx.tools['onair_work_share']['handler'](args))


def test_registered_work_lifecycle(obs):
    ctx, _ = make(obs.url)
    shown = call(ctx, {'action': 'show', 'title': 'Fix a test', 'panes': [
        {'kind': 'terminal', 'label': 'Tests', 'content': 'first'}]})
    assert shown['ok'] and shown['revision'] > 0
    rt = agent.runtime()
    assert rt.program.public()['mode'] == 'privacy'
    rt.surface._trusted_overlay = rt.obs.cfg.overlay_url
    rt.program.work_visibility(True)  # stand in for a verified live, non-private overlay state
    assert rt.program.public()['mode'] == 'work'
    appended = call(ctx, {'action': 'append', 'pane': 0, 'text': '\nsecond'})
    assert appended['ok'] and appended['revision'] > shown['revision']
    assert call(ctx, {'action': 'read'})['program']['work']['panes'][0]['content'] == 'first\nsecond'
    assert call(ctx, {'action': 'clear'})['ok']
    assert rt.program.public()['mode'] == 'standby'


@pytest.mark.parametrize('value,rule', [
    ('one\nTOKEN=do-not-echo', 'secret assignment'),
    ('Authorization: Bearer do-not-echo', 'authorization header'),
    ('name@example.invalid', 'email address'),
    ('/home/person/private', 'home-directory path'),
    ('10.3.4.5', 'private-network address'),
    ('sk-' + 'a' * 20, 'key-like secret'),
    ('bad\x07value', 'control characters'),
])
def test_refusal_never_echoes_content(obs, value, rule):
    ctx, _ = make(obs.url)
    result = call(ctx, {'action': 'show', 'title': 'Safe', 'panes': [
        {'kind': 'text', 'label': 'Output', 'content': value}]})
    encoded = json.dumps(result)
    assert not result['ok'] and rule in encoded and value not in encoded
    assert 'line' in encoded
    assert agent.runtime().surface is None


def test_ansi_and_append_tail(obs):
    ctx, _ = make(obs.url)
    assert call(ctx, {'action': 'show', 'title': 'Tests', 'panes': [
        {'kind': 'terminal', 'label': 'Log', 'content': '\x1b[31mfailed\x1b[0m'}]})['ok']
    agent.runtime().surface._trusted_overlay = agent.runtime().obs.cfg.overlay_url
    agent.runtime().program.work_visibility(True)
    assert call(ctx, {'action': 'append', 'pane': 0, 'text': '\n' + '\n'.join(str(i) for i in range(130))})['ok']
    content = call(ctx, {'action': 'read'})['program']['work']['panes'][0]['content']
    assert content.splitlines() == [str(i) for i in range(10, 130)]


def test_escape_sequences_and_cross_append_secret(obs):
    ctx, _ = make(obs.url)
    assert call(ctx, {'action': 'show', 'title': 'Tests', 'panes': [
        {'kind': 'terminal', 'label': 'Log', 'content': '\x1b]0;title\x07hello\x1b[32m!\x1b[0m\nAPI_TOKEN'}]})['ok']
    agent.runtime().surface._trusted_overlay = agent.runtime().obs.cfg.overlay_url
    agent.runtime().program.work_visibility(True)
    before = call(ctx, {'action': 'read'})['program']['work']['panes'][0]['content']
    assert before == 'hello!\nAPI_TOKEN'
    secret = 'secret-canary'
    denied = call(ctx, {'action': 'append', 'pane': 0, 'text': '=' + secret})
    assert not denied['ok'] and 'line 2' in json.dumps(denied) and secret not in json.dumps(denied)
    assert call(ctx, {'action': 'read'})['program']['work']['panes'][0]['content'] == before


def test_limits_unknown_fields_and_owner_commands(obs):
    ctx, _ = make(obs.url)
    pane = {'kind': 'code', 'label': 'Code', 'content': 'ok'}
    for bad in [
        {'action': 'show', 'title': 'x' * 81, 'panes': [pane]},
        {'action': 'show', 'title': 'x', 'panes': []},
        {'action': 'show', 'title': 'x', 'panes': [dict(pane, content='a\n' * 120)]},
        {'action': 'show', 'title': 'x', 'panes': [dict(pane, html='x')]},
        {'action': 'show', 'title': 'x', 'panes': [dict(pane, content='a' * 6001)]},
    ]:
        assert not call(ctx, bad)['ok']
    assert call(ctx, {'action': 'show', 'title': 'x', 'panes': [pane]})['ok']
    assert not call(ctx, {'action': 'append', 'pane': 0, 'text': 'x'})['ok']
    assert not call(ctx, {'action': 'step', 'done': ['d'] * 6, 'current': 'c', 'next': []})['ok']
    assert call(ctx, {'action': 'step', 'done': ['done'], 'current': 'now', 'next': ['next']})['ok']
    assert 'revision' in ctx.commands['onair-present']('work status')
    assert 'cleared' in ctx.commands['onair-present']('work clear').lower()
    ctx.surface = 'telegram'
    assert 'only' in ctx.commands['onair-present']('work clear').lower()


def test_show_refuses_without_mutation_when_surface_unavailable(obs, monkeypatch):
    ctx, _ = make(obs.url)
    rt = agent.runtime()
    before = rt.program.public()
    monkeypatch.setattr(rt, 'serve', lambda: {'ok': False, 'error': 'unavailable'})
    result = call(ctx, {'action': 'show', 'title': 'Example', 'panes': [
        {'kind': 'text', 'label': 'Output', 'content': 'safe'}]})
    assert not result['ok'] and rt.program.public() == before


def test_unknown_key_value_is_not_echoed(obs):
    ctx, _ = make(obs.url)
    value = 'private-marker-for-refusal'
    result = call(ctx, {'action': 'read', value: 'x'})
    assert not result['ok'] and value not in json.dumps(result)


def test_work_frames_coalesce(obs, monkeypatch):
    import time
    ctx, _ = make(obs.url)
    assert call(ctx, {'action': 'show', 'title': 'x', 'panes': [
        {'kind': 'text', 'label': 'log', 'content': ''}]})['ok']
    rt = agent.runtime()
    time.sleep(.3)
    seen = []
    monkeypatch.setattr(rt.surface, 'broadcast', lambda **kw: seen.append((time.monotonic(), rt.program.public()['revision'])))
    for i in range(30):
        assert call(ctx, {'action': 'status', 'status': str(i)})['ok']
    time.sleep(.55)
    assert 1 <= len(seen) <= 3
    assert seen[-1][1] == rt.program.public()['revision']
