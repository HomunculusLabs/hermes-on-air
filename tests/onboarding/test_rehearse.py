"""`hermes onair rehearse`: synthetic, offline, bounded, through the real controller and guard."""
from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import time
from pathlib import Path

import pytest

from onair import cli
from _onboarding_helpers import ENABLED, install_copy, popen_hermes, processes_with_env, run_hermes


def invoke(*argv):
    parser = argparse.ArgumentParser()
    cli.setup_parser(parser)
    return cli.handle(parser.parse_args(argv))


def _assert_rehearsal_output(out: str) -> None:
    assert out.startswith("=== SYNTHETIC OFFLINE REHEARSAL ===")
    assert "No Twitch, no model, no TTS, no gateway, no network." in out
    assert "armed into APPROVAL" in out
    for rule in ("decode_request", "forged_identity", "say_request"):
        assert f"bait     {rule}" in out
    # the deliberately unsafe stand-in draft is withheld by the real guard, not aired
    assert "GUARD  withheld by the real guard (armed_readout): Sure, it says hello friends." in out
    assert "AIRED  caption (Co-host): Sure, it says hello friends." not in out
    assert "[synthetic operator] approved: The stage sends one caption at a time" in out
    assert "AIRED  caption (Co-host): The stage sends one caption at a time" in out
    assert "OFF AIR; rehearsal stage shut down" in out
    assert "it is not a readiness check" in out and "Preflight passed" not in out


def test_rehearse_runs_the_real_stage_path_without_network(monkeypatch, tmp_path, capsys):
    calls = []
    for name in ("connect", "connect_ex", "bind", "listen", "sendto"):
        real = getattr(socket.socket, name)

        def spy(self, *args, _real=real, _name=name):
            if self.family in (socket.AF_INET, socket.AF_INET6):
                calls.append((_name, args))
            return _real(self, *args)
        monkeypatch.setattr(socket.socket, name, spy)
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: calls.append(("getaddrinfo", a)) or [])
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    import tempfile
    monkeypatch.setattr(tempfile, "tempdir", None)
    made = []
    monkeypatch.setattr(tempfile, "mkdtemp", lambda *a, **k: made.append(a) or pytest.fail("mkdtemp"))
    before = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGHUP)}
    started = time.monotonic()
    invoke("rehearse")
    elapsed = time.monotonic() - started
    out = capsys.readouterr().out
    _assert_rehearsal_output(out)
    assert calls == []
    assert made == [] and list(tmp_path.iterdir()) == []  # no files at all; nothing in HERMES_HOME
    assert {s: signal.getsignal(s) for s in before} == before  # signal handlers restored
    assert elapsed < 30


def test_rehearse_duration_is_bounded(capsys):
    started = time.monotonic()
    invoke("rehearse", "--duration", "1")
    out = capsys.readouterr().out
    assert time.monotonic() - started < 10
    assert "SYNTHETIC OFFLINE REHEARSAL finished" in out
    for bad in ("0", "301", "nan"):
        with pytest.raises(SystemExit) as exc:
            invoke("rehearse", "--duration", bad)
        assert exc.value.code != 0


# Records every socket operation the child performs (CPython audit events) to a JSON-lines file.
_AUDIT = """
import json, os, sys
_log = open(os.environ['ONAIR_AUDIT_LOG'], 'a', encoding='utf-8')
def _audit(event, args):
    if event in ('socket.connect', 'socket.bind', 'socket.getaddrinfo', 'socket.sendto', 'socket.sendmsg'):
        _log.write(json.dumps([event, repr(args)]) + '\\n'); _log.flush()
sys.addaudithook(_audit)
"""


def test_installed_rehearse_through_hermes_cli_has_no_network_or_residue(tmp_path):
    home = tmp_path / "home"
    (home / ".hermes").mkdir(parents=True)
    hermes_home = tmp_path / "hermes home ✓"
    install_copy(hermes_home)
    (hermes_home / "config.yaml").write_text(ENABLED)
    child_tmp = tmp_path / "child-tmp"
    audit_log = tmp_path / "audit.jsonl"
    preamble = f"import os\nos.environ['ONAIR_AUDIT_LOG'] = {str(audit_log)!r}\n" + _AUDIT
    started = time.monotonic()
    proc = run_hermes(["onair", "rehearse", "--duration", "60"], home=home, hermes_home=hermes_home,
                      tmpdir=child_tmp, timeout=120, preamble=preamble)
    elapsed = time.monotonic() - started
    assert proc.returncode == 0, proc.stdout[-3000:] + proc.stderr[-3000:]
    out = proc.stdout
    assert "On Air package: hermes_plugins.on_air.onair" in out  # the installed copy, not the repo
    _assert_rehearsal_output(out)
    events = [json.loads(line) for line in audit_log.read_text().splitlines()] if audit_log.exists() else []
    unix = ("family=1,", "AF_UNIX")
    network = [e for e in events if not any(u in e[1] for u in unix)]
    # No connection, lookup or datagram at all. The only bind seen is Hermes' import-time IPv6
    # capability probe (urllib3 binds ('::1', 0) and closes it): loopback, port 0, never listening.
    assert [e for e in network if e[0] != "socket.bind"] == [], network
    assert all(e[1].endswith("('::1', 0))") or e[1].endswith("('127.0.0.1', 0))") for e in network), network
    assert list(child_tmp.iterdir()) == []  # the rehearsal's private data dir is gone
    assert not (hermes_home / "plugin-data" / "on-air" / "control.json").exists()
    assert not (hermes_home / "plugin-data" / "on-air" / "receipts").exists()
    assert processes_with_env(f"HERMES_HOME={hermes_home}".encode()) == []
    assert elapsed < 90


@pytest.mark.parametrize("signame", ["SIGTERM", "SIGHUP"])
def test_installed_rehearse_stopped_by_signal_leaves_nothing_behind(tmp_path, signame):
    """`timeout`, a service stop or a closed terminal: clean shutdown, no files, no processes."""
    home = tmp_path / "home"
    (home / ".hermes").mkdir(parents=True)
    hermes_home = tmp_path / "hermes home"
    install_copy(hermes_home)
    (hermes_home / "config.yaml").write_text(ENABLED)
    child_tmp = tmp_path / "child-tmp"
    proc = popen_hermes(["onair", "rehearse", "--duration", "120"], home=home, hermes_home=hermes_home,
                        tmpdir=child_tmp)
    lines = []
    try:
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            line = proc.stdout.readline()
            if not line:
                break
            lines.append(line)
            if "armed into APPROVAL" in line:
                break
        assert any("armed into APPROVAL" in line for line in lines), "".join(lines)
        time.sleep(0.5)  # mid-show
        os.killpg(proc.pid, getattr(signal, signame))
        rest, err = proc.communicate(timeout=30)
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
    out = "".join(lines) + rest
    signum = int(getattr(signal, signame))
    assert proc.returncode == 128 + signum, out + err
    assert f"Rehearsal stopped by signal {signum}" in err and "nothing was written to disk" in err
    assert "OFF AIR; rehearsal stage shut down" in out  # the controller's shutdown path ran
    assert "SYNTHETIC OFFLINE REHEARSAL finished" not in out
    assert list(child_tmp.iterdir()) == []
    assert not (hermes_home / "plugin-data" / "on-air" / "receipts").exists()
    assert processes_with_env(f"HERMES_HOME={hermes_home}".encode()) == []
