"""Supported-version declarations and cross-platform source hygiene (no Hermes needed).

The plugin manifest, the documented support matrix and the CI workflow must make the same claim,
and every claim must sit inside what Hermes itself supports: Python 3.14 for Hermes 0.22.7. Its
broader declared Requires-Python is not a support promise, and a plugin module that only needs an
older Python (the pure word filter would run on 3.10) is not evidence that Hermes runs there.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import pytest

from ._support import (DECLARED_PYTHON_MAX_EXCLUSIVE, DECLARED_PYTHON_MIN, DOC, MIN_HERMES, ROOT,
                       SUPPORTED_PYTHON, WORKFLOW, load_harness)

vi = load_harness()
PLUGIN_FILES = sorted(p for p in [ROOT / "__init__.py", ROOT / "plugin.yaml", *(ROOT / "onair").rglob("*")]
                      if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc")
WINDOWS_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)),
                    *(f"lpt{i}" for i in range(1, 10))}


def _workflow_text() -> str:
    if not WORKFLOW.is_file():
        pytest.fail("the compatibility workflow is missing")
    return WORKFLOW.read_text(encoding="utf-8")


def _versions(text: str) -> list[tuple[int, int]]:
    return [tuple(map(int, v.split("."))) for v in re.findall(r"\b(3\.1\d)\b", text)]


def test_manifest_minimum_hermes_is_the_documented_one():
    assert vi.read_manifest(ROOT)["requires_hermes"] == ">=" + MIN_HERMES


def test_workflow_matrix_is_the_supported_python():
    text = _workflow_text()
    matrix = re.search(r"python:\s*\[([^\]]*)\]", text)
    assert matrix, "workflow matrix must list python versions inline"
    versions = _versions(matrix.group(1))
    assert versions == [SUPPORTED_PYTHON], versions
    for item in re.findall(r"^\s*-\s*\{\s*os:[^}]*python:\s*\"?(3\.\d+)", text, re.M):
        assert tuple(map(int, item.split("."))) == SUPPORTED_PYTHON, item
    hermes = re.search(r"hermes:\s*\[([^\]]*)\]", text)
    assert hermes and "HERMES_REF: ${{ matrix.hermes }}" in text, "the workflow must pin Hermes per row"
    pins = re.findall(r"\"v?([0-9]+\.[0-9]+\.[0-9]+)\"", hermes.group(1))
    assert pins, "the workflow must pin a Hermes release"
    for pin in pins:
        assert vi.satisfies_minimum(pin, ">=" + MIN_HERMES), pin


def test_workflow_uses_the_official_installer_and_the_strict_installer_mode():
    text = _workflow_text()
    assert "scripts/install.sh" in text and "${HERMES_REF}" in text, "official installer pinned to the tag"
    assert "pip install" not in text.replace("pip install --disable-pip-version-check --quiet pytest", ""), \
        "Hermes publishes no supported wheel: no pip install of hermes-agent"
    for flag in ("--install-mode installer", "--strict-version", "--hermes-launcher", "--bootstrap-launcher",
                 "--allow-dependency-downloads"):
        assert flag in text, flag


def test_workflow_is_least_privilege_and_publishes_nothing():
    text = _workflow_text()
    assert re.search(r"^permissions:\s*\n\s+contents:\s*read\s*$", text, re.M), "top-level read-only token"
    for forbidden in ("pull_request_target", "secrets.", "write-all", "id-token: write", "contents: write",
                      "packages: write", "upload-artifact", "git push", "gh release", "twine", "pypi"):
        assert forbidden not in text, forbidden
    assert "persist-credentials: false" in text
    assert "tools/verify_install.py" in text and "tests/portability" in text


def test_compatibility_doc_matches_the_manifest_and_workflow():
    assert DOC.is_file(), "docs/compatibility.md is missing"
    doc = DOC.read_text(encoding="utf-8")
    assert MIN_HERMES in doc
    assert "3.14" in doc and "not promised" in doc
    assert "pip install \"hermes-agent" not in doc and "Offline, that step fails" not in doc
    assert "not executed" in doc.lower() or "unexecuted" in doc.lower(), \
        "the doc must say which CI rows have not actually run"
    for command in ("hermes plugins list", "hermes onair preflight", "hermes plugins remove on-air",
                    "tools/verify_install.py"):
        assert command in doc, command


@pytest.mark.parametrize("path", PLUGIN_FILES, ids=lambda p: p.relative_to(ROOT).as_posix())
def test_plugin_paths_are_portable(path: Path):
    rel = path.relative_to(ROOT)
    for part in rel.parts:
        assert part.isascii(), "non-ASCII file name"
        assert not part.endswith((" ", ".")), "trailing space or dot is invalid on Windows"
        assert part.split(".")[0].lower() not in WINDOWS_RESERVED, "reserved Windows name"
        assert not set(part) & set('<>:"|?*\\'), "character invalid on Windows"
    # Leave headroom under Windows' 260-character MAX_PATH for HERMES_HOME + plugins/on-air/.
    assert len(rel.as_posix()) <= 120


def test_no_case_insensitive_collisions():
    seen: dict[str, str] = {}
    for path in PLUGIN_FILES:
        key = path.relative_to(ROOT).as_posix().casefold()
        assert key not in seen, (seen.get(key), path)
        seen[key] = path.relative_to(ROOT).as_posix()


@pytest.mark.parametrize("path", [p for p in PLUGIN_FILES if p.suffix == ".py"],
                         ids=lambda p: p.relative_to(ROOT).as_posix())
def test_plugin_source_parses_on_the_minimum_python(path: Path):
    """No syntax newer than the oldest supported Python (best effort: ``ast`` feature_version)."""
    source = path.read_text(encoding="utf-8")
    ast.parse(source, filename=str(path), feature_version=DECLARED_PYTHON_MIN)


@pytest.mark.parametrize("path", [p for p in PLUGIN_FILES if p.suffix in (".py", ".yaml", ".md", ".txt",
                                                                            ".html", ".css", ".js")],
                         ids=lambda p: p.relative_to(ROOT).as_posix())
def test_text_files_are_utf8_without_bom(path: Path):
    data = path.read_bytes()
    assert not data.startswith(b"\xef\xbb\xbf"), "UTF-8 BOM"
    data.decode("utf-8")


def test_text_io_in_the_plugin_names_an_encoding():
    """Windows' default locale encoding is not UTF-8; every text read/write must say so."""
    offenders = []
    for path in (p for p in PLUGIN_FILES if p.suffix == ".py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else ""
            keywords = {k.arg for k in node.keywords}
            if name in ("read_text", "write_text") and "encoding" not in keywords:
                offenders.append(f"{path.relative_to(ROOT)}:{node.lineno} {name}")
            if name == "open" and isinstance(func, (ast.Name, ast.Attribute)) and not (
                    isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id == "os"):
                mode = node.args[1] if len(node.args) > 1 else next(
                    (k.value for k in node.keywords if k.arg == "mode"), None)
                binary = isinstance(mode, ast.Constant) and isinstance(mode.value, str) and "b" in mode.value
                if not binary and "encoding" not in keywords and not name.startswith("_"):
                    offenders.append(f"{path.relative_to(ROOT)}:{node.lineno} open")
    assert offenders == [], offenders


def test_this_interpreter_is_supported_or_the_skip_is_explicit():
    current = sys.version_info[:2]
    if current != SUPPORTED_PYTHON:
        declared = DECLARED_PYTHON_MIN <= current < DECLARED_PYTHON_MAX_EXCLUSIVE
        pytest.skip(f"Python {current} is not the supported Hermes runtime (3.14)"
                    + ("; inside the declared window but not promised" if declared else "")
                    + ": results here are not a supported result")
    assert current == SUPPORTED_PYTHON
