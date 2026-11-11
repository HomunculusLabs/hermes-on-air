"""Which ``hermes`` the stage is started with: the one of the installation this plugin runs in, in both layouts."""
import os
import stat
import sys
from pathlib import Path

import pytest

from onair import stage_runtime as sr


def _exe(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


@pytest.fixture
def self_managed(tmp_path, monkeypatch):
    """The self-managed layout: a bare interpreter with no ``hermes`` beside it; the launcher lives in the source root."""
    root = tmp_path / "hermes-agent"
    launcher = _exe(root / ".hermes" / "bin" / "hermes")
    interpreter = _exe(tmp_path / "tools" / "python" / "bin" / "python3")
    wrapper = _exe(tmp_path / "local-bin" / "hermes")  # a PATH shim outside the installation
    monkeypatch.setattr(sys, "executable", str(interpreter))
    monkeypatch.setattr(sr, "_install_roots", lambda: ({root}, []))
    monkeypatch.setattr(sr, "_MANAGED_DIR", tmp_path / "no-managed-scope")
    monkeypatch.setenv("PATH", str(wrapper.parent) + os.pathsep + os.environ.get("PATH", ""))
    return root, launcher, wrapper


def test_self_managed_launcher_is_found_and_admitted(self_managed):
    root, launcher, wrapper = self_managed
    assert sr.hermes_command() == [str(launcher)]  # not the PATH shim, although it comes first on PATH
    assert sr.install_isolation(sr.hermes_command()) == []


def test_a_launcher_outside_the_installation_is_refused(self_managed):
    root, launcher, wrapper = self_managed
    problems = sr.install_isolation([str(wrapper)])
    assert len(problems) == 1 and "not the installation this plugin runs in" in problems[0]["detail"]
    assert str(launcher) in problems[0]["detail"] and problems[0]["agent_can_do"] is False


def test_project_env_in_the_installation_root_is_still_reported(self_managed):
    root, launcher, wrapper = self_managed
    (root / ".env").write_text("")
    problems = sr.install_isolation(sr.hermes_command())
    assert len(problems) == 1 and str(root / ".env") in problems[0]["detail"]


def test_unlocatable_installation_trusts_no_launcher_inside_it(self_managed, monkeypatch):
    root, launcher, wrapper = self_managed
    monkeypatch.setattr(sr, "_install_roots", lambda: (set(), ["gateway"]))
    assert sr.hermes_command() == [str(wrapper)]  # only PATH is left
    assert "not the installation this plugin runs in" in sr.install_isolation(sr.hermes_command())[0]["detail"]
    assert "not the installation this plugin runs in" in sr.install_isolation([str(launcher)])[0]["detail"]


def test_environment_install_prefers_the_launcher_beside_the_interpreter(tmp_path, monkeypatch):
    root = tmp_path / "hermes-agent"
    beside = _exe(tmp_path / "venv" / "bin" / "hermes")
    _exe(root / ".hermes" / "bin" / "hermes")
    monkeypatch.setattr(sys, "executable", str(_exe(tmp_path / "venv" / "bin" / "python")))
    monkeypatch.setattr(sr, "_install_roots", lambda: ({root}, []))
    monkeypatch.setattr(sr, "_MANAGED_DIR", tmp_path / "no-managed-scope")
    assert sr.hermes_command() == [str(beside)]
    assert sr.install_isolation([str(beside)]) == []
