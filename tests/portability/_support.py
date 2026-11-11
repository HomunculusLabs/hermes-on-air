"""Shared helpers for the portability tests: load the install harness from its file path.

The harness is a standalone tool, not part of the plugin package, so it is loaded by path rather
than imported through the plugin. Nothing here touches the installed-plugin import path.
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HARNESS = ROOT / "tools" / "verify_install.py"
WORKFLOW = ROOT / ".github" / "workflows" / "compatibility.yml"
DOC = ROOT / "docs" / "compatibility.md"

# Kept in one place so docs, workflow and manifest agree.
# Hermes 0.22.7's installation guide: current first-party installations run on Python 3.14, and it
# does not promise current runtime support on 3.11-3.13 although its pyproject still declares the
# broader Requires-Python >=3.11,<3.15. Support (and the CI matrix) is 3.14; the declared window is
# only a lower bound for source-syntax checks, never a support claim.
SUPPORTED_PYTHON = (3, 14)
DECLARED_PYTHON_MIN = (3, 11)
DECLARED_PYTHON_MAX_EXCLUSIVE = (3, 15)
MIN_HERMES = "0.22.7"


def load_harness():
    spec = importlib.util.spec_from_file_location("onair_verify_install", HARNESS)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def hermes_python() -> Path | None:
    """The interpreter with Hermes installed: ``ONAIR_HERMES_PYTHON``, else this one if it has Hermes."""
    configured = os.environ.get("ONAIR_HERMES_PYTHON")
    if configured:
        return Path(configured)
    if importlib.util.find_spec("hermes_cli") is not None:
        return Path(sys.executable)
    return None


def copy_plugin_tree(dest: Path) -> Path:
    module = load_harness()
    shutil.copytree(ROOT, dest, ignore=module.IGNORE)
    return dest
