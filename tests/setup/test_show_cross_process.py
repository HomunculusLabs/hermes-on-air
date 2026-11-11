"""Real cross-process show owner/status/control; fixture model and voice only."""
import os
import subprocess
import sys
from pathlib import Path


def test_shared_owner_and_foreign_identity(tmp_path):
    root = Path(__file__).resolve().parents[2]
    env = dict(os.environ, HERMES_HOME=str(tmp_path / 'home'), TMPDIR=str(tmp_path),
               PYTHONDONTWRITEBYTECODE='1', HERMES_DISABLE_LAZY_INSTALLS='1')
    env['PYTHONPATH'] = str(root) + os.pathsep + env.get('PYTHONPATH', '')
    result = subprocess.run([sys.executable, '-B', str(Path(__file__).with_name('show_cross_process_probe.py'))],
                            cwd=root, env=env, text=True, capture_output=True, timeout=60)
    assert result.returncode == 0, result.stdout + '\n' + result.stderr
    assert '"foreign_resume_http": 409' in result.stdout
    assert '"after_foreign_resume": "paused"' in result.stdout
