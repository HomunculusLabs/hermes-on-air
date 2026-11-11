"""Opt-in real-browser work view acceptance; uses one browser and a local synthetic overlay."""
import asyncio
import json
import os
import subprocess
import time
from pathlib import Path

import pytest

from conftest import make
from onair.overlay.server import EventHub, OverlayServer, ev_state
from onair.presentation import agent

pytestmark = pytest.mark.skipif(not os.environ.get('ONAIR_WORK_PLAYWRIGHT'), reason='provide an existing Playwright package')


def test_work_browser_matrix(obs, tmp_path):
    ctx, _ = make(obs.url)
    rt = agent.runtime()
    hub = EventHub()
    state = {'privacy': False}
    hub.snapshot = lambda: ev_state(not state['privacy'], 'approval', state['privacy'], False)
    overlay = OverlayServer(hub, lambda _id: None, port=0)
    rt.call(overlay.start())
    rt.obs.cfg.overlay_url = overlay.overlay_url
    handler = ctx.tools['onair_work_share']['handler']
    def call(args):
        result = json.loads(handler(args))
        assert result['ok'], result
        return result
    failed = 'FAIL test_clamp: expected 10, got 12'
    code = 'def clamp(value, maximum):\n    return value\n\nassert clamp(12, 10) == 10'
    shown = call({'action': 'show', 'title': 'Fix the clamp function', 'status': 'test failed', 'panes': [
        {'kind': 'code', 'label': 'clamp.py', 'language': 'python', 'content': code},
        {'kind': 'terminal', 'label': 'Tests', 'content': '\n'.join(['$ pytest -q'] + ['checking case %02d' % i for i in range(18)] + [failed])}]})
    deadline = time.monotonic() + 4
    while rt.program.public()['mode'] != 'work' and time.monotonic() < deadline:
        time.sleep(.02)
    assert rt.program.public()['mode'] == 'work'
    out = Path(os.environ.get('ONAIR_WORK_SHOTS') or tmp_path / 'shots')
    env = dict(os.environ, ONAIR_PLAYWRIGHT=os.environ['ONAIR_WORK_PLAYWRIGHT'],
               ONAIR_BROWSER_EXECUTABLE=os.environ['ONAIR_WORK_BROWSER'], ONAIR_PRESENT_URL=shown['surface']['url'], ONAIR_OUT=str(out))
    driver = Path(__file__).parent / 'browser' / 'work.cjs'
    proc = subprocess.Popen(['node', str(driver)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, env=env)
    results = []
    def capture(name, mode, contains=None):
        for width, height in [(1920,1080), (1280,720), (640,360), (480,270)]:
            proc.stdin.write(json.dumps({'name': name, 'mode': mode, 'contains': contains, 'width': width, 'height': height}) + '\n')
            proc.stdin.flush()
            row = json.loads(proc.stdout.readline())
            assert row.get('ok'), row
            info = row['info']
            results.append({'name': name, 'width': width, **info})
            assert not row['errors'] and not info['documentOverflow'] and not info['injected'], row
            if mode == 'work':
                assert info['font'] >= 24 and info['terminalTail'], row
            if mode == 'privacy':
                assert info['privacyClean'], row
            if name == 'passing':
                assert 'PASS: 1 test passed' in info['text'], row
            if name == 'long-code':
                assert any('more lines' in value for value in info['more']), row
            if name == 'max-steps':
                assert info['stepCount'] == 6 and not info['railOverflow'], row
    try:
        assert json.loads(proc.stdout.readline())['ready']
        capture('failing', 'work', failed)
        call({'action': 'update', 'pane': 0, 'content': 'def clamp(value, maximum):\n    return min(value, maximum)'})
        call({'action': 'append', 'pane': 1, 'text': '\n$ pytest -q\nPASS: 1 test passed'})
        call({'action': 'status', 'status': 'tests passing'})
        capture('passing', 'work', 'tests passing')
        call({'action': 'show', 'title': 'Review the fix', 'panes': [
            {'kind': 'diff', 'label': 'Change', 'content': '-    return value\n+    return min(value, maximum)'}]})
        capture('diff', 'work', 'Review the fix')
        call({'action': 'show', 'title': 'A literal example', 'panes': [
            {'kind': 'text', 'label': 'Notes', 'content': '<b>plain text</b>\nNo markup runs here.'}]})
        capture('single', 'work', 'A literal example')
        call({'action': 'step', 'done': ['Reproduce failure'], 'current': 'Fix function', 'next': ['Run tests']})
        capture('steps', 'work', 'PROGRESS')
        call({'action': 'step', 'done': ['W' * 60, 'X' * 60], 'current': 'Y' * 60,
              'next': ['Z' * 60, 'M' * 60, 'N' * 60]})
        capture('max-steps', 'work', 'Y' * 60)
        call({'action': 'show', 'title': 'Long code sample', 'panes': [
            {'kind': 'code', 'label': 'Source', 'content': '\n'.join('line ' + str(i) for i in range(120))},
            {'kind': 'terminal', 'label': 'Output', 'content': 'last log line'}]})
        capture('long-code', 'work', 'Long code sample')
        call({'action': 'clear'})
        capture('cleared', 'standby')
        call({'action': 'show', 'title': 'Private cut test', 'panes': [
            {'kind': 'text', 'label': 'Test', 'content': 'Only a synthetic example'}]})
        capture('before-privacy', 'work', 'Private cut test')
        async def privacy():
            state['privacy'] = True
            hub.publish(ev_state(False, 'approval', True, False), urgent=True)
        rt.call(privacy())
        capture('privacy', 'privacy')
        state['privacy'] = False
        async def resume(): hub.publish(ev_state(True, 'approval', False, False))
        rt.call(resume())
        capture('after-privacy', 'work', 'Private cut test')
        assert json.loads(ctx.tools['onair_present_publish']['handler']({'sample': True}))['ok']
        capture('deck', 'slides')
        call({'action': 'show', 'title': 'Back to work', 'panes': [{'kind': 'text', 'label': 'Log', 'content': 'Ready'}]})
        capture('work-again', 'work', 'Back to work')
        call({'action': 'clear'})
        capture('deck-restored', 'slides')
    finally:
        proc.stdin.write('{"quit":true}\n'); proc.stdin.flush(); proc.stdin.close()
        assert proc.wait(timeout=15) == 0
        rt.call(overlay.stop())
        out.mkdir(parents=True, exist_ok=True)
        (out / 'report.json').write_text(json.dumps(results, indent=2))
