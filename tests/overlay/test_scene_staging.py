"""State, privacy and reaction contracts without a browser dependency."""
import asyncio
import shutil
import subprocess
from pathlib import Path

import pytest


def test_staging_fail_closed_and_reactions(tmp_path):
    node = shutil.which('node')
    if not node:
        pytest.skip('node unavailable')
    source = Path(__file__).parents[2] / 'onair/scene_starter/events.js'
    (tmp_path / 'events.mjs').write_text(source.read_text().replace(
        "from './avatar_logic.js'", "from './avatar_logic.mjs'"))
    (tmp_path / 'avatar_logic.mjs').write_text((source.parent / 'avatar_logic.js').read_text())
    script = r'''
import {connectEvents} from './events.mjs';
globalThis.document = {body:{dataset:{}}};
globalThis.EventSource = class { close() { this.closed = true; } };
let now = 0; globalThis.performance = {now: () => now};
const calls = [], character = {
  setSpeaking(v) { this.speaking = v; },
  setPresence(v) { calls.push(['presence', v]); },
  glance(...v) { calls.push(['glance', ...v]); },
  react(v) { calls.push(['react', v]); }
};
const stage = {setPresence(v) { calls.push(['stage', v]); }, alert() { calls.push(['alert']); }};
const e = connectEvents(character, {}, stage);
const send = ev => e.onmessage({data: JSON.stringify(ev)});
const state = (live, privacy=false, muted=false) => send({type:'state', live, privacy, muted, mode: live?'approval':'off_air'});
const check = (curtain, tally) => {
  if (document.body.dataset.curtain !== curtain || document.body.dataset.tally !== tally)
    throw Error(JSON.stringify(document.body.dataset));
};
check('true', 'off');
state(true); check('false', 'on');
const presenceCount = calls.filter(c => c[0] === 'presence').length;
state(true, false, true); state(true, false, false);
if (calls.filter(c => c[0] === 'presence').length !== presenceCount) throw Error('mute changed presence');
send({type:'chat'}); send({type:'chat'});
now = 3999; send({type:'chat'}); now = 4000; send({type:'chat'});
if (calls.filter(c => c[0] === 'glance').length !== 2) throw Error('chat rate limit');
send({type:'alert'}); send({type:'alert'});
if (calls.filter(c => c[0] === 'alert').length !== 1 || !calls.some(c => c[0] === 'react' && c[1] === 'alert')) throw Error('alert missing or repeated');
state(true, true); check('true', 'off');
send({type:'state', live:true, mode:'approval'}); check('true', 'off');
send({type:'chat'}); send({type:'alert'});
state(false); check('false', 'off');
state(true, false, true); check('false', 'on');
send({type:'caption', id:'muted', ttl_ms:1000});
if (character.speaking) throw Error('muted mouth');
e.onerror(); check('true', 'off');
send({type:'state', live:false, mode:'paused', privacy:false, muted:false}); check('false', 'off');
e.onerror(); check('true', 'off');
state(false); check('false', 'off'); if (character.speaking) throw Error('off-air mouth');
e.onerror(); state(true, true); check('true', 'off');
send({type:'state', live:false, mode:'off_air', privacy:false}); check('true', 'off');
state(true); check('false', 'on');
e.close(); check('true', 'off');
'''
    result = subprocess.run([node, '--input-type=module', '-e', script], cwd=tmp_path,
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


def test_preview_cycle_has_live_reactions_privacy_and_stop(tmp_path):
    from onair.scene_preview import ScenePreview

    async def exercise():
        preview = ScenePreview(tmp_path, interval=.01)
        events = []
        preview.hub.publish = events.append
        task = asyncio.create_task(preview._cycle())
        try:
            async def complete():
                while len([e for e in events if e['type'] == 'state']) < 5:
                    await asyncio.sleep(.01)
            await asyncio.wait_for(complete(), 2)
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        types = [e['type'] for e in events]
        assert all(name in types for name in ('caption', 'audio', 'caption_clear', 'chat', 'alert'))
        states = [(e['live'], e['privacy']) for e in events if e['type'] == 'state']
        assert states[:5] == [(False, False), (True, False), (True, True), (True, False), (False, False)]
    asyncio.run(exercise())
