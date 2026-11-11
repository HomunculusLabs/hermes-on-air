"""Run the desk's JavaScript behavior tests with node when it is available.

``test_js_behavior.cjs`` loads dashboard/dist/index.js in a jsdom window with a fake SDK and
asserts registration, safe text rendering, epoch-bound approvals, the mode gate, the optimistic
stop race and the disconnected state. ``test_js_preflight.cjs`` asserts the preflight panel (the
latch reason and only the failing checks) and the "Clear runtime latch" button, shown only off air
while the reason is ``runtime_refused:<rule>``. ``test_js_journey.cjs`` walks a new operator's
path: the setup guide with copyable existing commands while nothing answers, healthy approval
with 0, many and maximum-length lines, privacy wording, preflight fixes, latch recovery, a lost
connection and stale reads that must never restore a live-looking state.
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
NODE_MODULES = Path(os.environ.get('ONAIR_HERMES_SDK', str(Path.home() / '.hermes' / 'hermes-agent'))) / 'node_modules'


@pytest.mark.parametrize("script", ["test_js_behavior.cjs", "test_js_preflight.cjs", "test_js_journey.cjs", "test_js_assist.cjs", "test_js_novice.cjs", "test_js_resume.cjs", "test_js_mode_status.cjs", "test_js_quick_stop.cjs", "test_js_quick_stop_partial.cjs", "test_js_views.cjs", "test_js_host_draft.cjs"])
def test_desk_js_behavior(script):
    node = shutil.which("node")
    if not node or not NODE_MODULES.is_dir():
        pytest.skip("node + dashboard node_modules not available")
    env = dict(os.environ, NODE_PATH=str(NODE_MODULES))
    result = subprocess.run([node, str(HERE / script)], cwd=ROOT, capture_output=True, text=True, timeout=120, env=env)
    print(result.stdout)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "passed" in result.stdout
