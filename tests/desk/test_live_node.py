"""Live view behavior using the same React/jsdom dependencies as the desk tests."""
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize('script', ['test_live_logic.cjs', 'test_live_ui.cjs', 'test_tab_lifecycle.cjs', 'test_live_theme.cjs', 'test_tab_menu.cjs'])
def test_live_node(script):
    node = shutil.which('node')
    modules = Path(os.environ.get('ONAIR_HERMES_SDK', '')) / 'node_modules'
    if not node or not modules.is_dir():
        pytest.skip('Node and dashboard dependencies required')
    env = dict(os.environ, NODE_PATH=str(modules))
    result = subprocess.run([node, str(Path(__file__).parent / script)], cwd=ROOT,
                            env=env, capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'passed' in result.stdout
