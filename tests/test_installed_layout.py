"""The plugin must import the way Hermes loads it.

Hermes copies the plugin into ``$HERMES_HOME/plugins/on-air`` and imports that directory under its
own package name (``hermes_plugins.<slug>``), so ``onair`` is a subpackage there, never a top-level
module. An absolute ``from onair....`` import inside the plugin therefore raises
``ModuleNotFoundError`` once installed. ``tests/conftest.py`` puts the repo root on ``sys.path``,
which hides that from in-process tests, so these checks scan the source and run a fresh interpreter
on a copy outside the repo, with the repo root kept off ``sys.path``.
"""

from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "onair"


def _plugin_sources() -> list[Path]:
    return [ROOT / "__init__.py", *sorted((ROOT / PACKAGE).rglob("*.py"))]


def _absolute_self_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names = [node.module]
        elif isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        else:
            continue
        found += [f"{path.relative_to(ROOT)}:{node.lineno}: {name}"
                  for name in names if name.split(".")[0] == PACKAGE]
    return found


def test_plugin_never_imports_itself_by_absolute_name():
    """Static check, so a lazy import inside a function body is caught too."""
    offenders = [hit for path in _plugin_sources() for hit in _absolute_self_imports(path)]
    assert offenders == [], "use relative imports inside the plugin:\n" + "\n".join(offenders)


def _install_copy(home: Path) -> Path:
    plugin_dir = home / "plugins" / "on-air"
    shutil.copytree(ROOT / PACKAGE, plugin_dir / PACKAGE,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for name in ("__init__.py", "plugin.yaml"):
        shutil.copy(ROOT / name, plugin_dir / name)
    return plugin_dir


def _clean_env(tmp_path: Path, home: Path) -> dict[str, str]:
    """No repo root on PYTHONPATH, a throwaway HERMES_HOME and HOME."""
    keep = [p for p in os.environ.get("PYTHONPATH", "").split(os.pathsep)
            if p and Path(p).resolve() != ROOT]
    env = dict(os.environ, HERMES_HOME=str(home), HOME=str(tmp_path), PYTHONDONTWRITEBYTECODE="1",
               HERMES_DISABLE_LAZY_INSTALLS="1", PYTHONPATH=os.pathsep.join(keep))
    if not keep:
        env.pop("PYTHONPATH")
    return env


# Shared preamble: drop anything that resolves to the repo root, and prove no top-level ``onair``
# is importable, otherwise the check would pass for the wrong reason.
_PREAMBLE = """
import importlib, importlib.util, sys
from pathlib import Path

REPO = Path(sys.argv[1]).resolve()
sys.path[:] = [p for p in sys.path if Path(p or ".").resolve() != REPO]
assert importlib.util.find_spec("onair") is None, "a top-level onair is importable"
"""

# Exercise the import paths the bug broke: module-level imports in the gate and stage, and the
# lazy imports inside the word filter's name and line scans.
_EXERCISE = """
def exercise(pkg):
    for sub in ("stage", "chat.gate", "wordfilter", "guard"):
        importlib.import_module(pkg + "." + sub)
    wf = sys.modules[pkg + ".wordfilter"]
    types = importlib.import_module(pkg + ".types")
    guard = sys.modules[pkg + ".guard"]
    gate = sys.modules[pkg + ".chat.gate"]
    wf.name_tiers("@plain_viewer_42")
    wf.line_tiers("hello chat, welcome to the show")
    verdict = guard.check_line("hello chat, welcome to the show", types.GuardContext())
    assert verdict.release, verdict
    msg = types.ChatMsg("twitch", "chan", "1", "u1", "viewer_1", "how long have you been streaming?", 0.0)
    kind = gate.classify(msg, []).kind
    assert kind == "worthy", kind
    assert "onair" not in sys.modules, "a top-level onair module was imported"
"""


def test_installed_copy_imports_under_its_own_package_name(tmp_path):
    """Load the copy the way ``hermes_cli.plugins_loader`` does, in plain Python (no Hermes)."""
    home = tmp_path / "hermes"
    plugin_dir = _install_copy(home)
    script = tmp_path / "load_plugin.py"
    script.write_text(_PREAMBLE + _EXERCISE + textwrap.dedent("""
        import types as _types

        plugin_dir = Path(sys.argv[2])
        ns = _types.ModuleType("hermes_plugins")
        ns.__path__ = []
        sys.modules["hermes_plugins"] = ns
        name = "hermes_plugins.on_air"
        spec = importlib.util.spec_from_file_location(
            name, plugin_dir / "__init__.py", submodule_search_locations=[str(plugin_dir)])
        module = importlib.util.module_from_spec(spec)
        module.__package__ = name
        module.__path__ = [str(plugin_dir)]
        sys.modules[name] = module
        spec.loader.exec_module(module)
        exercise(name + ".onair")
        print("OK")
    """))
    # -I: ignore PYTHONPATH and user site, and keep the script's and the current directory off sys.path.
    proc = subprocess.run([sys.executable, "-I", str(script), str(ROOT), str(plugin_dir)],
                          env=_clean_env(tmp_path, home), cwd=tmp_path, capture_output=True,
                          text=True, timeout=120)
    assert proc.returncode == 0 and proc.stdout.strip() == "OK", proc.stdout[-2000:] + proc.stderr[-4000:]


def test_hermes_discovery_imports_and_connects_installed_copy(tmp_path):
    """Real ``discover_plugins()`` in a throwaway HERMES_HOME, then the adapter's ``connect()``."""
    pytest.importorskip("hermes_cli.plugins")
    pytest.importorskip("gateway.platform_registry")
    from hermes_cli.config_defaults import DEFAULT_CONFIG

    home = tmp_path / "hermes"
    _install_copy(home)
    config = {
        "_config_version": DEFAULT_CONFIG["_config_version"],
        "plugins": {"enabled": ["on-air"], "entries": {"on-air": {"settings": {
            "overlay_port": 0, "control_port": 0, "voice": False}}}},
    }
    (home / "config.yaml").write_text(json.dumps(config))
    script = tmp_path / "discover_plugin.py"
    script.write_text(_PREAMBLE + _EXERCISE + textwrap.dedent("""
        import asyncio

        from hermes_cli.plugins import discover_plugins

        discover_plugins()
        from gateway.config import PlatformConfig
        from gateway.platform_registry import platform_registry

        entry = platform_registry.get("on_air")
        assert entry is not None, "On Air platform not registered"
        pkg = entry.adapter_factory.__module__.rsplit(".", 1)[0]
        assert pkg.split(".")[0] != "onair", pkg

        async def connect():
            # connect() builds the stage, which imports the gate and the word filter.
            adapter = entry.adapter_factory(PlatformConfig(enabled=True, extra={}))
            try:
                assert await adapter.connect(), "adapter connect() returned False"
            finally:
                await adapter.disconnect()

        asyncio.run(connect())
        exercise(pkg)
        print("OK " + pkg)
    """))
    proc = subprocess.run([sys.executable, str(script), str(ROOT)], env=_clean_env(tmp_path, home),
                          cwd=tmp_path, capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0 and proc.stdout.strip().startswith("OK "), proc.stdout[-2000:] + proc.stderr[-4000:]
