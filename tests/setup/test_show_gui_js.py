"""Run the private-show / agent-plan GUI test (``test_js_show.cjs``) against the real dashboard bundle."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
NODE_MODULES = Path.home() / ".hermes" / "hermes-agent" / "node_modules"


def test_show_gui_and_agent_pending_plan():
    node = shutil.which("node")
    if not node or not NODE_MODULES.is_dir():
        pytest.skip("node + dashboard node_modules not available")
    env = dict(os.environ, NODE_PATH=str(NODE_MODULES))
    result = subprocess.run([node, "--max-old-space-size=1536", str(Path(__file__).with_name("test_js_show.cjs"))],
                            cwd=ROOT, capture_output=True, text=True, timeout=120, env=env)
    print(result.stdout)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "passed" in result.stdout
