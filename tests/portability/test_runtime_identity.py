"""The harness reads Hermes' identity from the selected runtime's canonical provenance, not a stamp-only alias.

These run without Hermes: they feed recorded probe reports to the pure verdict, and run the probe itself
against small fake ``hermes_cli`` trees so the probe's own tree binding is exercised.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from ._support import MIN_HERMES, load_harness

vi = load_harness()
NEED = ">=" + MIN_HERMES
TREE = "/opt/hermes/checkout"


def report(**over):
    base = {"executing_tree": TREE, "version_info_file": TREE + "/hermes_cli/version_info.py",
            "hermes_cli_file": TREE + "/hermes_cli/__init__.py", "legacy_version": "0.0.0",
            "tree_has_git": True, "tree_has_stamp": False, "tree_head": "a" * 40, "install_root_override": None,
            "identity": {"base_version": MIN_HERMES, "source": "git", "commit": "a" * 40, "dirty": False}}
    ident = over.pop("identity", None)
    base.update(over)
    if ident is not None:
        base["identity"] = None if ident == "missing" else {**base["identity"], **ident}
    return base


def test_canonical_git_identity_of_the_executing_tree_passes_even_when_the_legacy_alias_says_0_0_0():
    status, why = vi.runtime_identity_verdict(report(), NEED)
    assert status == "pass", why


def test_canonical_stamp_identity_in_the_executing_tree_passes():
    status, why = vi.runtime_identity_verdict(
        report(tree_has_git=False, tree_has_stamp=True, tree_head=None,
               identity={"source": "docker", "commit": None}), NEED)
    assert status == "pass", why


@pytest.mark.parametrize("ident", [{"base_version": "unknown", "source": "unknown", "commit": None},
                                   {"base_version": "0.0.0"}, {"base_version": ""},
                                   {"source": "fallback"}, "missing"])
def test_unknown_provenance_is_unverifiable_never_a_pass(ident):
    status, why = vi.runtime_identity_verdict(report(identity=ident), NEED)
    assert status == "unverifiable", why


def test_too_old_fails():
    status, why = vi.runtime_identity_verdict(report(identity={"base_version": "0.22.6"}), NEED)
    assert status == "fail" and "too old" in why


@pytest.mark.parametrize("over", [
    {"tree_has_git": False},                                   # git identity borrowed from another checkout
    {"tree_head": "b" * 40},                                   # a different commit than the executing tree
    {"version_info_file": "/elsewhere/hermes_cli/version_info.py"},  # identity API from another tree
    {"tree_has_git": False, "tree_has_stamp": False, "tree_head": None,
     "identity": {"source": "nix", "commit": None}},           # a stamp source with no stamp in this tree
])
def test_identity_from_another_tree_is_mismatched_evidence(over):
    status, why = vi.runtime_identity_verdict(report(**over), NEED)
    assert status == "fail" and "mismatched" in why, why


def _fake_tree(root: Path, body: str) -> Path:
    pkg = root / "hermes_cli"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("__version__ = '0.0.0'\n", encoding="utf-8")
    (pkg / "version_info.py").write_text(body, encoding="utf-8")
    return root


def _probe(tree: Path, tmp_path: Path) -> dict:
    script = tmp_path / "probe.py"
    script.write_text(vi.RUNTIME_PROBE, encoding="utf-8")
    proc = subprocess.run([sys.executable, "-B", "-I", "-c",
                           "import sys, runpy; sys.path.insert(0, sys.argv[1]); "
                           "runpy.run_path(sys.argv[2], run_name='__main__')", str(tree), str(script)],
                          capture_output=True, text=True, timeout=60, cwd=tmp_path)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


VI_UNKNOWN = ("from types import SimpleNamespace as N\n"
              "def get_version_info():\n"
              "    return N(base_version='unknown', derived_version='unknown', commit=None, source='unknown',"
              " dirty=False, distance=None)\n")


def test_probe_on_an_unstamped_tree_without_git_reports_unknown_and_strict_refuses(tmp_path):
    info = _probe(_fake_tree(tmp_path / "workspace", VI_UNKNOWN), tmp_path)
    assert info["legacy_version"] == "0.0.0" and info["tree_has_git"] is False
    assert info["identity"]["source"] == "unknown"
    assert vi.runtime_identity_verdict(info, NEED)[0] == "unverifiable"


def test_strict_runtime_step_rejects_an_actual_unstamped_probe(tmp_path, monkeypatch):
    info = _probe(_fake_tree(tmp_path / "workspace", VI_UNKNOWN), tmp_path)
    src = tmp_path / "plugin"
    src.mkdir()
    (src / "plugin.yaml").write_text("requires_hermes: >=0.22.7\n", encoding="utf-8")
    verifier = vi.Verifier(src, Path(sys.executable), tmp_path, "verify-", strict_version=True)
    verifier.root = tmp_path
    (tmp_path / "tmp").mkdir()
    monkeypatch.setattr(verifier, "run", lambda *args, **kwargs: (0, json.dumps(info), "", 0.0, []))
    assert verifier.step_runtime() is False
    assert verifier.steps[-1].status == "fail"
    assert "provenance unknown" in verifier.steps[-1].detail
    assert "--strict-version" in verifier.steps[-1].detail


def test_runtime_step_redacts_the_executing_tree_and_executable(tmp_path, monkeypatch):
    src = tmp_path / "plugin"
    src.mkdir()
    (src / "plugin.yaml").write_text("requires_hermes: >=0.22.7\n", encoding="utf-8")
    verifier = vi.Verifier(src, Path(sys.executable), tmp_path, "verify-", strict_version=True)
    verifier.root = tmp_path
    (tmp_path / "tmp").mkdir()
    executable = "/private/runtime/bin/python"
    info = report(python_executable=executable)
    monkeypatch.setattr(verifier, "run", lambda *args, **kwargs: (0, json.dumps(info), "", 0.0, [executable]))
    assert verifier.step_runtime() is True
    public = json.dumps({"facts": verifier.facts, "step": verifier.steps[-1].__dict__})
    assert TREE not in public
    assert executable not in public
    assert "<HERMES_TREE>" in public
    assert "<RUNTIME_PYTHON>" in public


def test_probe_rejects_a_git_identity_claimed_by_a_tree_that_has_no_git(tmp_path):
    claimed = ("from types import SimpleNamespace as N\n"
               "def get_version_info():\n"
               "    return N(base_version='%s', derived_version='%s', commit='%s', source='git', dirty=False,"
               " distance=0)\n" % (MIN_HERMES, MIN_HERMES, "c" * 40))
    info = _probe(_fake_tree(tmp_path / "copy", claimed), tmp_path)
    status, why = vi.runtime_identity_verdict(info, NEED)
    assert status == "fail" and "mismatched" in why
