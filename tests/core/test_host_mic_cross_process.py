"""Separate dashboard processes must share the per-home upload gate."""
import os
import subprocess
import sys

import pytest
from dashboard import plugin_api


@pytest.mark.skipif(os.name != 'posix', reason='POSIX process lock probe')
def test_upload_lock_is_shared_across_processes(tmp_path, monkeypatch):
    mic = plugin_api._onair('host_mic')
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    lock = mic._acquire_home_upload_lock()
    assert lock is not None
    script = "from onair.host_mic import _acquire_home_upload_lock; print('busy' if _acquire_home_upload_lock() is None else 'acquired')"
    try:
        child = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True, check=True, timeout=10)
        assert child.stdout.strip() == 'busy'
    finally:
        lock.close()
    second = mic._acquire_home_upload_lock()
    assert second is not None
    second.close()
