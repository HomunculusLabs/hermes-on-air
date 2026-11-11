"""Run the synthetic push-to-talk state-machine test when Node dependencies exist."""
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(('script', 'message'), [
    ('test_host_mic_ui.cjs', 'host mic state machine passed'),
    ('test_host_cut_on_talk.cjs', 'cut on talk passed'),
    ('test_open_mic_vad.cjs', 'open microphone VAD and barge-in passed'),
    ('test_host_mic_mode_ui.cjs', 'mic release mode passed'),
])
def test_host_mic_ui_state_machine(script, message):
    node = shutil.which('node')
    modules = os.environ.get('ONAIR_NODE_MODULES') or str(Path.home() / '.hermes/hermes-agent/node_modules')
    if not node or not (Path(modules) / 'jsdom').is_dir():
        pytest.skip('Node and jsdom/React modules are not available')
    env = dict(os.environ, NODE_PATH=modules)
    result = subprocess.run([node, str(Path('tests/desk') / script)], cwd=ROOT, env=env,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert message in result.stdout
