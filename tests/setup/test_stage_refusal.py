"""Stage readiness and durable refusal, through the registered owner surfaces."""
import re
import socket

import pytest

from onair import stage_runtime as sr
from test_quick_setup import owner  # noqa: F401
from test_stage_runtime import _made_stage, _prepare

pytestmark = pytest.mark.skipif(sr.hermes_command() is None, reason="no Hermes CLI")


def _set_port(home, key, port):
    path = home / "config.yaml"
    text = path.read_text()
    if key == "overlay_port":
        text = re.sub(r"overlay_port: \d+", f"overlay_port: {port}", text)
    else:
        text = text.replace("overlay_port:", f"control_port: {port}\n        overlay_port:")
    path.write_text(text)


@pytest.mark.parametrize("key", ["overlay_port", "control_port"])
def test_busy_port_blocks_status_and_plan(owner, tmp_path, monkeypatch, key):
    eng, home, ctx, call, command = _made_stage(owner, tmp_path, monkeypatch)
    _prepare(home)
    if key == "control_port":
        with socket.socket() as free_overlay:
            free_overlay.bind(("127.0.0.1", 0))
            _set_port(home, "overlay_port", free_overlay.getsockname()[1])
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        port = occupied.getsockname()[1]
        _set_port(home, key, port)
        status = call("onair_stage_status")
        plan = call("onair_stage_plan")
        assert status["startable"] is False, status
        assert plan["ok"] is False and "approve_with" not in plan, plan
        assert key in status["next_action"] and str(port) in str(status), status
        assert key in plan["next_action"] and str(port) in str(plan), plan
        text = command("stage status")
        assert key in text
        if key == "overlay_port":
            print("BLOCKED STATUS:", text)


def test_refusal_after_plan_is_durable_and_off_clears_it(owner, tmp_path, monkeypatch):
    eng, home, ctx, call, command = _made_stage(owner, tmp_path, monkeypatch)
    _prepare(home)
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        port = occupied.getsockname()[1]
        _set_port(home, "overlay_port", port)
    plan = call("onair_stage_plan")
    assert plan["ok"], plan
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", port))
        occupied.listen()
        reply = command("stage approve " + plan["approve_with"]["chat"].split()[-1])
        assert "overlay_port" in reply and str(port) in reply, reply
    status = call("onair_stage_status")
    refusal = status["last_refusal"]
    assert refusal["time"] and str(port) in refusal["reason"] and "overlay_port" in refusal["remedy"]
    assert refusal == sr.status(type(eng)(home=eng.home))["last_refusal"]
    assert refusal["reason"] in command("stage status") and refusal["remedy"] in command("stage status")
    assert call("onair_stage_safety", {"action": "stop"})["ok"]
    assert "last_refusal" not in call("onair_stage_status")


def test_child_output_never_leaks_to_readiness_or_refusal(owner, tmp_path, monkeypatch):
    import subprocess
    eng, home, ctx, call, command = _made_stage(owner, tmp_path, monkeypatch)
    _prepare(home)
    canary = 'UNTRUSTED_CANARY_742'
    monkeypatch.setattr(sr.subprocess, 'run', lambda *a, **k: subprocess.CompletedProcess(a, 1, canary, canary))
    status, plan = call('onair_stage_status'), call('onair_stage_plan')
    assert canary not in str(status) + str(plan)
    sr.record_refusal(eng, plan)
    assert canary not in eng._path(sr.REFUSAL_FILE).read_text()


def test_concurrent_readiness_probe_reports_unknown(owner, tmp_path, monkeypatch):
    import threading
    eng, home, ctx, call, command = _made_stage(owner, tmp_path, monkeypatch)
    _prepare(home)
    entered, release = threading.Event(), threading.Event()
    def held(*args):
        entered.set()
        assert release.wait(10)
        return {'ok': True, 'missing': []}
    monkeypatch.setattr(sr, '_stage_preflight', held)
    thread = threading.Thread(target=sr.readiness, args=(eng,))
    thread.start()
    try:
        assert entered.wait(5)
        result = sr.readiness(eng)
        assert not result['ok'] and 'unknown' in str(result).lower()
        assert 'unavailable' not in str(result).lower()
        assert not eng._path(sr.REFUSAL_FILE).exists()
    finally:
        release.set()
        thread.join(10)


def test_preflight_json_detail_is_not_relayed(owner, tmp_path, monkeypatch):
    import json
    import subprocess
    eng, home, ctx, call, command = _made_stage(owner, tmp_path, monkeypatch)
    _prepare(home)
    canary = 'UNTRUSTED_CANARY_743'
    monkeypatch.setattr(sr, '_probe_effective_home', lambda *a: (True, str(home)))
    payload = json.dumps({'ok': False, 'checks': [{'name': 'overlay_port', 'status': 'fail', 'detail': canary}]})
    monkeypatch.setattr(sr.subprocess, 'run', lambda *a, **k: subprocess.CompletedProcess(a, 1, payload, canary))
    result = call('onair_stage_plan')
    assert canary not in str(result)
    sr.record_refusal(eng, result)
    assert canary not in eng._path(sr.REFUSAL_FILE).read_text()


@pytest.mark.skipif(__import__('os').name == 'nt', reason='setup lock uses POSIX flock')
def test_cross_process_probe_lock_cannot_report_busy(owner, tmp_path, monkeypatch):
    import os
    import subprocess
    import sys
    eng, home, ctx, call, command = _made_stage(owner, tmp_path, monkeypatch)
    _prepare(home)
    config = home / 'config.yaml'
    port = int(re.search(r'overlay_port: (\d+)', config.read_text()).group(1))
    script = """import socket, sys
from pathlib import Path
from onair.stage_runtime import _port_lock
with _port_lock()._file_lock():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', int(sys.argv[2])))
        sock.listen()
        print('ready', flush=True)
        sys.stdin.readline()
"""
    proc = subprocess.Popen([sys.executable, '-B', '-c', script, str(eng.home), str(port)],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            env=dict(os.environ, PYTHONPATH=os.pathsep.join(sys.path)))
    try:
        assert proc.stdout.readline().strip() == 'ready'
        result = call('onair_stage_status')
        assert result['startable'] is None and result['readiness'] == 'unknown'
        assert 'busy' not in str(result).lower()
        assert 'unknown' in command('stage status').lower()
        assert not eng._path(sr.REFUSAL_FILE).exists()
    finally:
        proc.communicate('done\n', timeout=10)
    assert proc.returncode == 0


def test_setup_lock_windows_fallback_is_nonblocking(tmp_path, monkeypatch):
    import os
    import sys
    import types
    from onair import setup_engine as se
    calls = []
    fake = types.SimpleNamespace(LK_NBLCK=1, LK_UNLCK=2)
    fake.locking = lambda fd, mode, count: calls.append(mode)
    class OSProxy:
        name = 'nt'
        def __getattr__(self, key):
            return getattr(os, key)
    monkeypatch.setattr(se, 'os', OSProxy())
    monkeypatch.setitem(sys.modules, 'msvcrt', fake)
    eng = se.SetupEngine(tmp_path)
    with eng._file_lock(blocking=False):
        with eng._file_lock(blocking=False):
            pass
    assert calls == [fake.LK_NBLCK, fake.LK_UNLCK]
    def busy(*args):
        raise OSError('busy')
    fake.locking = busy
    with pytest.raises(BlockingIOError):
        with eng._file_lock(blocking=False):
            pass


def test_approval_does_not_persist_unknown_readiness(owner, tmp_path, monkeypatch):
    eng, home, ctx, call, command = _made_stage(owner, tmp_path, monkeypatch)
    _prepare(home)
    eng._write(sr.PLAN_FILE, {'code': 'test-code'})
    monkeypatch.setattr(sr, 'readiness', lambda _: {'ok': False, 'unknown': True,
                        'error': 'Readiness unknown; check again.'})
    recorded = []
    monkeypatch.setattr(sr, 'record_refusal', lambda *args: recorded.append(args))
    assert 'unknown' in command('stage approve test-code').lower()
    assert recorded == []


def test_independent_owner_probes_do_not_collide(owner, tmp_path, monkeypatch):
    import threading
    from onair import setup_engine as se
    eng, home, ctx, call, command = _made_stage(owner, tmp_path, monkeypatch)
    _prepare(home)
    other = se.SetupEngine(tmp_path / 'other-owner')
    monkeypatch.setattr(sr, 'saved_stage', lambda _: (home, ''))
    entered, release = threading.Event(), threading.Event()
    with socket.socket() as first:
        first.bind(('127.0.0.1', 0))
        port = first.getsockname()[1]
    def probe(*args):
        with socket.socket() as sock:
            try:
                sock.bind(('127.0.0.1', port))
                sock.listen()
            except OSError:
                return {'ok': False, 'missing': [{'key': 'overlay_port', 'ok': False,
                    'detail': 'port busy', 'next_action': 'free port'}]}
            entered.set()
            assert release.wait(10)
            return {'ok': True, 'missing': []}
    monkeypatch.setattr(sr, '_stage_preflight', probe)
    thread = threading.Thread(target=sr.readiness, args=(eng,))
    thread.start()
    try:
        assert entered.wait(5)
        result = sr.readiness(other)
        assert result.get('unknown') and 'busy' not in str(result).lower()
    finally:
        release.set()
        thread.join(10)
