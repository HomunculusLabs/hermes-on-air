"""Run the shipped overlay script with synthetic EventSource messages in Node."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest


def test_overlay_state_node():
    node = shutil.which('node')
    modules = Path(os.environ.get('ONAIR_HERMES_SDK', str(Path.home() / '.hermes/hermes-agent'))) / 'node_modules'
    if not node or not modules.is_dir():
        pytest.skip('node and dashboard dependencies are not available')
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run([node, str(Path(__file__).with_name('test_state_node.cjs'))],
                            cwd=root, env={**os.environ, 'NODE_PATH': str(modules)},
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
