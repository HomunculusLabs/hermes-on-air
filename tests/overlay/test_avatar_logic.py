"""Pure avatar decisions run without Three.js or a browser."""
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(not shutil.which('node'), reason='Node unavailable')
def test_avatar_logic(tmp_path):
    source = Path(__file__).parents[2] / 'onair/scene_starter/avatar_logic.js'
    (tmp_path / 'logic.mjs').write_text(source.read_text())
    script = tmp_path / 'check.mjs'
    script.write_text('''
import {moodForLine, moodChannels, armTarget, framingScale} from './logic.mjs';
import assert from 'node:assert/strict';
assert.equal(moodForLine('Is it ready?'), 'thinking');
assert.equal(moodForLine('Wow!'), 'surprised');
assert.equal(moodForLine('That is wonderful!'), 'happy');
assert.equal(moodForLine('haha'), 'happy');
assert.equal(moodForLine('Sorry about that.'), 'sad');
assert.equal(moodForLine('A quiet day.'), 'neutral');
assert.equal(moodForLine(null), 'neutral');
const available = new Set(['Expr.brow_warm', 'Expr.mouth_soft_smile', 'Expr.brow_concerned']);
assert.deepEqual(moodChannels('happy', name => available.has(name)), ['Expr.brow_warm', 'Expr.mouth_soft_smile']);
assert.deepEqual(moodChannels('sad', name => available.has(name)), ['Expr.brow_concerned']);
available.add('happy');
assert.deepEqual(moodChannels('happy', name => available.has(name)), ['happy']);
assert.deepEqual(moodChannels('neutral', name => available.has(name)), []);
assert.deepEqual(armTarget(1), [.27, -.963, 0]);
assert.deepEqual(armTarget(-1), [-.27, -.963, 0]);
assert.ok(framingScale(2, 'bust') > framingScale(2, 'waist'));
assert.ok(framingScale(2, 'waist') > framingScale(2, 'full'));
assert.equal(framingScale(0, 'waist'), 1);
''')
    subprocess.run(['node', str(script)], check=True, capture_output=True, text=True)
