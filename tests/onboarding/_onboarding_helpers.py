"""Helpers for onboarding tests that run the real Hermes CLI on an installed copy of the plugin.

Every subprocess gets an owned scratch ``HOME`` and ``HERMES_HOME``, a minimum environment,
stdin from ``/dev/null`` (never a TTY) and, where ``unshare`` is available, a private network
namespace with no interfaces up, so any external request would fail instead of leaving the host.
The plugin is placed at ``$HERMES_HOME/plugins/on-air`` the way Hermes installs it; the repo root
is never on ``sys.path`` of the child.
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ENABLED = "plugins:\n  enabled: [on-air]\n"


def hermes_python() -> str:
    explicit = os.environ.get("ONAIR_TEST_HERMES_PYTHON")
    if explicit:
        return explicit
    if importlib.util.find_spec("hermes_cli") is None:
        pytest.skip("needs a Python whose environment provides hermes_cli")
    return sys.executable


def netns_prefix() -> list[str]:
    unshare = shutil.which("unshare")
    if unshare and subprocess.run([unshare, "-rn", "true"], capture_output=True).returncode == 0:
        return [unshare, "-rn"]
    return []


def install_copy(hermes_home: Path) -> Path:
    plugin = hermes_home / "plugins" / "on-air"
    shutil.copytree(ROOT / "onair", plugin / "onair", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for name in ("__init__.py", "plugin.yaml"):
        shutil.copy(ROOT / name, plugin / name)
    return plugin


def clean_env(home: Path, hermes_home: Path | None, tmpdir: Path) -> dict[str, str]:
    env = {"PATH": "/usr/bin:/bin", "HOME": str(home), "TMPDIR": str(tmpdir), "LANG": "C.UTF-8",
           "PYTHONDONTWRITEBYTECODE": "1", "HERMES_DISABLE_LAZY_INSTALLS": "1", "PYTHONIOENCODING": "utf-8"}
    if hermes_home is not None:
        env["HERMES_HOME"] = str(hermes_home)
    return env


def _hermes_argv(args: list[str], preamble: str) -> list[str]:
    code = ("import runpy, sys\n" + preamble +
            "sys.argv = ['hermes'] + sys.argv[1:]\n"
            "runpy.run_module('hermes_cli.main', run_name='__main__', alter_sys=True)\n")
    return [*netns_prefix(), hermes_python(), "-B", "-c", code, *args]


def run_hermes(args: list[str], *, home: Path, hermes_home: Path | None, tmpdir: Path,
               timeout: float = 120, preamble: str = "") -> subprocess.CompletedProcess:
    """``python -c <preamble; run hermes_cli.main>`` with cwd outside the repo."""
    tmpdir.mkdir(parents=True, exist_ok=True)
    return subprocess.run(_hermes_argv(args, preamble), env=clean_env(home, hermes_home, tmpdir), cwd=tmpdir,
                          stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout)


def popen_hermes(args: list[str], *, home: Path, hermes_home: Path | None, tmpdir: Path) -> subprocess.Popen:
    """Like ``run_hermes`` but returns the running process (its own session, stdout piped)."""
    tmpdir.mkdir(parents=True, exist_ok=True)
    return subprocess.Popen(_hermes_argv(args, ""), env=clean_env(home, hermes_home, tmpdir), cwd=tmpdir,
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, start_new_session=True)


def processes_with_env(marker: bytes) -> list[str]:
    """PIDs of live processes whose environment contains exactly ``marker`` (Linux /proc)."""
    found = []
    for proc_dir in Path("/proc").glob("[0-9]*"):
        try:
            environ = (proc_dir / "environ").read_bytes()
        except OSError:
            continue
        if marker in environ.split(b"\0"):
            found.append(proc_dir.name)
    return found


def snapshot(directory: Path) -> dict[str, bytes]:
    return {str(p.relative_to(directory)): (p.read_bytes() if p.is_file() and not p.is_symlink() else b"<dir>")
            for p in sorted(directory.rglob("*"))}
