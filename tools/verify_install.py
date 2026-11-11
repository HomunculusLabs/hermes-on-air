#!/usr/bin/env python3
"""Fresh-install verification for the On Air plugin.

Installs a selected local plugin tree into a brand-new, owned ``HOME`` / ``HERMES_HOME`` and drives
the plugin the way a new user would, through the Hermes CLI:

* ``--install-mode installer`` (default): Hermes' own plugin manager,
  ``hermes plugins install file://<tree> --ref <exact commit> --enable --yes-deps``. A failed install
  is a FAIL. The plugin manager prepares its own pinned runtime in the owned home, so it needs a
  dependency source: pass ``--allow-dependency-downloads`` to let only that step (and the launcher
  bootstrap) reach the package index; otherwise it runs behind the dead proxies and fails.
  ``--install-mode copy`` is a NARROWER stand-in: it copies the tree to
  ``$HERMES_HOME/plugins/on-air`` and lists it in ``plugins.enabled``, with no plugin admission;
* ``hermes plugins list`` must discover the installed plugin;
* ``hermes onair preflight`` on a legitimate captions-only configuration (must pass) and on unsafe
  configurations (each must be refused, without echoing config values);
* ``hermes onair check-line``, ``status`` with no show running and with stale discovery state,
  ``report`` with no receipts, and non-interactive ``setup`` (must not prompt or write config);
* the real Hermes plugin loader (``discover_plugins()``): the plugin must import under Hermes' own
  ``hermes_plugins.*`` namespace, never as a top-level ``onair``; its adapter connects with no chat
  source and no voice, ``hermes onair status`` reads the live desk, and disconnect removes its state;
* ``hermes plugins remove on-air``, then removal of the owned root.

Command entry: by default each CLI step runs ``<HERMES_PYTHON> -B -m hermes_cli.main ...``, a
Python-module entry that is NARROWER than the public ``hermes`` launcher. ``--hermes-launcher``
runs the CLI steps through a launcher instead (``--bootstrap-launcher`` first runs its
``pm repair`` in the owned home, which a fresh home needs). The report records the actual argv.

Nothing here adds the installed plugin directory to ``sys.path`` or replaces the Hermes loader. The
only fakes are external boundaries: no Twitch channel is configured for the loader step, voice is
off (captions only), no model is configured, the public plugin-catalog fetch is answered by an
empty cache file inside the owned home, and outside the opted-in dependency steps every HTTP(S)
proxy variable points at a closed loopback port. Dead proxies only catch clients that honour proxy
variables; they are a tripwire, not a network sandbox.

Usage::

    python tools/verify_install.py --hermes-python /path/to/python --allow-dependency-downloads
    python tools/verify_install.py --install-mode copy --report /somewhere/outside/report.json
    python tools/verify_install.py --hermes-launcher /path/to/hermes --bootstrap-launcher \
        --hermes-python /path/to/python --allow-dependency-downloads --strict-version

Exit status: 0 when every required step passed, 1 when any step failed, 2 on a usage error.
Skipped or limited steps are listed in the report; they are never counted as passes.
``--strict-version`` turns an unverifiable Hermes version (an unstamped build reports 0.0.0) into a
failure instead of a limitation; CI uses it.
The script needs only the standard library and runs on the interpreter you start it with; Hermes
runs on ``--hermes-python`` (default: this interpreter).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import textwrap
import time
from dataclasses import dataclass, field
from pathlib import Path

PLUGIN_NAME = "on-air"
OWNED_MARKER = ".onair-verify-owned"
# The file that marks a stage home. There the plugin registers the co-host only; in any other home it also
# registers the owner tools, and preflight refuses to run the stage beside them (owner_tools).
STAGE_MARKER = ".onair-stage-home"
# Copied into the install: what a user gets from a clean checkout, without caches or local state.
IGNORE = shutil.ignore_patterns(".git", "__pycache__", "*.pyc", ".pytest_cache", ".venv", "venv",
                                "plugin-data", "node_modules", "*.egg-info", ".mypy_cache")
DEAD_PROXY = "http://127.0.0.1:9"
# Only OS plumbing passes through. API keys, tokens, PYTHONPATH and the caller's HOME never do.
_PASSTHROUGH = ("SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "SYSTEMDRIVE", "PROGRAMDATA")
CANARY = "onair-verify-canary-" + "0" * 8  # planted in unsafe configs; must never be echoed


class HarnessError(Exception):
    """A usage or safety refusal: nothing was installed."""


@dataclass
class Step:
    name: str
    status: str  # pass | fail | skip | limitation
    detail: str
    command: list[str] = field(default_factory=list)
    rc: int | None = None
    stdout: str = ""
    stderr: str = ""
    seconds: float = 0.0


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def tree_digest(root: Path) -> dict[str, str]:
    """sha256 per file under *root* (``.git`` excluded): proves the source tree is not touched."""
    out = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if rel.parts and rel.parts[0] == ".git":
            continue
        if path.name == ".env":
            raise HarnessError("refusing to read a .env in the plugin tree")
        if path.is_file() and not path.is_symlink():
            out[rel.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def read_manifest(src: Path) -> dict[str, str]:
    """The few top-level ``plugin.yaml`` scalars this harness needs, without a YAML library."""
    values = {}
    for line in (src / "plugin.yaml").read_text(encoding="utf-8").splitlines():
        if line[:1].isspace() or ":" not in line or line.lstrip().startswith("#"):
            continue
        key, _, value = line.partition(":")
        value = value.split(" #", 1)[0].strip().strip('"').strip("'")
        if value and key.strip() in ("name", "kind", "version", "requires_hermes"):
            values[key.strip()] = value
    return values


def parse_version(text: str) -> tuple[int, ...]:
    parts = []
    for piece in text.strip().split("."):
        digits = "".join(ch for ch in piece if ch.isdigit())
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def satisfies_minimum(version: str, spec: str) -> bool | None:
    """``>=X.Y.Z`` only (the form plugin.yaml uses); ``None`` for anything else."""
    spec = spec.strip()
    if not spec.startswith(">=") or "," in spec:
        return None
    have, need = parse_version(version), parse_version(spec[2:])
    if not have or not need:
        return None
    width = max(len(have), len(need))
    return have + (0,) * (width - len(have)) >= need + (0,) * (width - len(need))


RUNTIME_PROBE = """\
import json, os, subprocess, sys
from importlib import metadata
from pathlib import Path
import hermes_cli
out = {"python": ".".join(map(str, sys.version_info[:3])), "platform": sys.platform,
       "python_executable": sys.executable}
try:
    meta = metadata.metadata("hermes-agent")
    out["requires_python"], out["dist_version"] = meta.get("Requires-Python"), meta.get("Version")
except metadata.PackageNotFoundError:
    out["requires_python"] = out["dist_version"] = None
try:
    out["legacy_version"] = str(getattr(hermes_cli, "__version__"))
except Exception as exc:
    out["legacy_version"] = None
    out["legacy_error"] = type(exc).__name__
tree = Path(hermes_cli.__file__).resolve().parent.parent
out["hermes_cli_file"] = str(Path(hermes_cli.__file__).resolve())
out["executing_tree"] = str(tree)
out["install_root_override"] = os.environ.get("HERMES_INSTALL_ROOT")
try:
    from hermes_cli import version_info as vi
    info = vi.get_version_info()
    out["version_info_file"] = str(Path(vi.__file__).resolve())
    out["identity"] = {"base_version": info.base_version, "derived_version": info.derived_version,
                       "commit": info.commit, "source": info.source, "dirty": info.dirty,
                       "distance": info.distance}
except Exception as exc:
    out["identity"] = None
    out["identity_error"] = "%s: %s" % (type(exc).__name__, exc)
out["tree_has_git"] = (tree / ".git").exists()
out["tree_has_stamp"] = (tree / "install-stamp.json").is_file()
head = None
if out["tree_has_git"]:
    try:
        head = subprocess.run(["git", "-C", str(tree), "rev-parse", "HEAD"], capture_output=True, text=True,
                              timeout=20, stdin=subprocess.DEVNULL).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        head = None
out["tree_head"] = head
print(json.dumps(out))
"""

_UNKNOWN_VERSIONS = {"", "unknown", "0.0.0"}


def runtime_identity_verdict(info: dict, need: str) -> tuple[str, str]:
    """``("pass" | "fail" | "unverifiable", reason)`` for a runtime probe report.

    Identity must come from the canonical API of the SAME tree the runtime imported ``hermes_cli`` from:
    a git identity must be that tree's own HEAD, and a stamp must sit in that tree (or under the runtime's
    own ``HERMES_INSTALL_ROOT``). Unknown provenance is unverifiable; another tree's identity fails."""
    ident = info.get("identity")
    if not isinstance(ident, dict):
        return "unverifiable", "canonical identity API unavailable (%s)" % (info.get("identity_error") or "no report")
    tree, vi_file = info.get("executing_tree"), info.get("version_info_file")
    if not tree or not vi_file or not Path(vi_file).is_relative_to(Path(tree)):
        return "fail", "identity API %s is not part of the executing tree %s (mismatched evidence)" % (vi_file, tree)
    base, source = str(ident.get("base_version") or ""), str(ident.get("source") or "unknown")
    if base in _UNKNOWN_VERSIONS or source in ("unknown", "fallback"):
        return "unverifiable", "provenance unknown (source %s, version %s): minimum NOT verified" % (
            source, base or "none")
    if source == "git":
        if not info.get("tree_has_git"):
            return "fail", "git identity was read from a checkout other than the executing tree (mismatched evidence)"
        if not ident.get("commit") or info.get("tree_head") != ident.get("commit"):
            return "fail", "git identity commit %s is not the executing tree's HEAD %s (mismatched evidence)" % (
                ident.get("commit"), info.get("tree_head"))
    elif not info.get("tree_has_stamp") and not info.get("install_root_override"):
        return "fail", "stamp identity (source %s) has no stamp in the executing tree (mismatched evidence)" % source
    if not need:
        return "pass", "the plugin declares no minimum"
    verdict = satisfies_minimum(base, need)
    if verdict is None:
        return "unverifiable", "version %s not comparable with %s: minimum NOT verified" % (base, need)
    if not verdict:
        return "fail", "Hermes %s is too old for %s" % (base, need)
    return "pass", "minimum %s verified" % need


def build_env(root: Path, home: Path, hermes_home: Path, extra: dict[str, str] | None = None,
              hermes_python: Path | None = None, network: bool = False) -> dict[str, str]:
    """Minimal child environment. ``network=True`` drops the dead proxies (dependency steps only)."""
    env = {k: os.environ[k] for k in _PASSTHROUGH if k in os.environ}
    git = shutil.which("git")
    path_dirs = [str(Path(hermes_python or sys.executable).parent)]
    if git:
        path_dirs.append(str(Path(git).parent))
    path_dirs += ["/usr/bin", "/bin"] if os.name != "nt" else []
    tmp = root / "tmp"
    env.update({
        "PATH": os.pathsep.join(dict.fromkeys(path_dirs)),
        "HOME": str(home), "USERPROFILE": str(home), "HERMES_HOME": str(hermes_home),
        "XDG_CONFIG_HOME": str(home / ".config"), "XDG_CACHE_HOME": str(home / ".cache"),
        "XDG_DATA_HOME": str(home / ".local" / "share"), "XDG_STATE_HOME": str(home / ".local" / "state"),
        "TMPDIR": str(tmp), "TEMP": str(tmp), "TMP": str(tmp),
        "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8",
        "HERMES_DISABLE_LAZY_INSTALLS": "1", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
        "NO_COLOR": "1", "TERM": "dumb", "COLUMNS": "200",
        "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost",
    })
    if not network:
        env.update({"HTTP_PROXY": DEAD_PROXY, "HTTPS_PROXY": DEAD_PROXY, "ALL_PROXY": DEAD_PROXY,
                    "http_proxy": DEAD_PROXY, "https_proxy": DEAD_PROXY, "all_proxy": DEAD_PROXY})
    if os.name == "nt":  # keep per-user application data inside the owned home too
        env.update({"APPDATA": str(home / "AppData" / "Roaming"), "LOCALAPPDATA": str(home / "AppData" / "Local")})
    if extra:
        env.update(extra)
    return env


# The plugin manager clones, checks the manifest and runs its security scan before ``uv sync``
# prepares the plugin runtime, so only a failed ``uv sync`` fetch is a dependency-source problem.
_FETCH_FAILURE_MARKERS = ("Failed to fetch", "Failed to download", "error sending request", "Connection refused")


def dependency_fetch_failed(output: str) -> bool:
    """True only when the install output shows ``uv sync`` failing to reach a package source."""
    return "uv sync" in output and any(marker in output for marker in _FETCH_FAILURE_MARKERS)


def install_failure_hint(output: str, downloads_allowed: bool) -> str:
    """Explain a failed plugin-manager install only as far as its own output supports."""
    if not dependency_fetch_failed(output):
        return "; cause not classified by the harness: read this step's stdout/stderr"
    if downloads_allowed:
        return ("; uv sync could not fetch the plugin manager's pinned runtime packages: check this machine's "
                "network, proxy or package mirror")
    return ("; uv sync could not fetch the plugin manager's pinned runtime packages because dependency downloads "
            "were blocked for this empty owned home (a fixture choice, not a product defect): rerun with "
            "--allow-dependency-downloads")


def _free_closed_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _json_config(data: dict) -> str:
    # JSON is valid YAML, so no YAML library is needed to write config.yaml.
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


SAFE_SETTINGS_KEYS = ("seed_bank", "twitch_channel", "overlay_port", "voice", "host_name", "never_say")


def safe_config(seed_bank: Path, *, channel: str = "example_channel") -> dict:
    """A legitimate captions-only stage profile: what ``hermes onair setup`` recommends."""
    return {
        "plugins": {"enabled": [PLUGIN_NAME], "entries": {PLUGIN_NAME: {"settings": {
            "seed_bank": str(seed_bank), "twitch_channel": channel, "overlay_port": 0,
            "control_port": 0, "voice": False, "host_name": "Host", "never_say": ["forbidden phrase"],
            "profanity": "mask"}}}},
        "gateway": {"platforms": {"on_air": {"enabled": True, "skip_context_files": True}}},
        "platform_toolsets": {"on_air": ["no_mcp"]},
        "memory": {"memory_enabled": False, "user_profile_enabled": False, "provider": None},
        "context": {"engine": "compressor"},
        "voice": {"auto_tts": False},
    }


def unsafe_variants(seed_bank: Path) -> list[tuple[str, dict, str, set[str]]]:
    """(label, config, ONAIR_ALLOWED_USERS value for $HERMES_HOME/.env, failing checks that must appear)."""
    def mutate(fn):
        cfg = json.loads(json.dumps(safe_config(seed_bank)))
        fn(cfg)
        return cfg

    def tools(cfg):
        cfg["platform_toolsets"] = {"on_air": []}
        cfg["model"] = {"api_key": CANARY}

    def memory(cfg):
        cfg["memory"] = {"memory_enabled": True, "user_profile_enabled": True, "provider": CANARY}

    def codex(cfg):
        cfg["model"] = {"openai_runtime": "codex_app_server", "base_url": "http://" + CANARY + ".invalid"}

    def trusted(cfg):
        cfg["gateway"]["platforms"]["on_air"]["trusted_inbound"] = True

    def disabled(cfg):
        cfg["gateway"]["platforms"]["on_air"]["enabled"] = False

    ok_users = "onair-stage"
    return [
        ("toolsets_empty_list", mutate(tools), ok_users, {"toolsets"}),
        ("memory_enabled", mutate(memory), ok_users, {"memory"}),
        ("codex_app_server", mutate(codex), ok_users, {"agent_runtime"}),
        ("trusted_inbound", mutate(trusted), ok_users, {"trusted_inbound"}),
        ("platform_disabled", mutate(disabled), ok_users, {"enabled"}),
        ("allowed_users_wrong", safe_config(seed_bank), "everyone," + CANARY, {"allowed_users"}),
    ]


_LOADER = textwrap.dedent(r'''
    """Runs inside the Hermes interpreter: real discovery, real adapter, no sys.path edits."""
    import asyncio, importlib.util, json, os, subprocess, sys, urllib.request
    from pathlib import Path

    hermes_home = Path(os.environ["HERMES_HOME"]).resolve()
    plugin_dir = hermes_home / "plugins" / "on-air"
    report = {}

    def norm(p):
        return str(Path(p or ".").resolve())

    assert norm(plugin_dir) not in [norm(p) for p in sys.path], "plugin dir is on sys.path"
    assert importlib.util.find_spec("onair") is None, "a top-level onair is importable before discovery"

    import hermes_cli.main  # noqa: F401  Hermes' own entry-point bootstrap, exactly as `hermes` runs
    from hermes_cli.plugins import discover_plugins
    assert norm(plugin_dir) not in [norm(p) for p in sys.path], "plugin dir is on sys.path"
    discover_plugins()
    from gateway.config import PlatformConfig
    from gateway.platform_registry import platform_registry

    entry = platform_registry.get("on_air")
    assert entry is not None, "On Air platform not registered by discovery"
    module = entry.adapter_factory.__module__
    pkg = module.rsplit(".", 2)[0]
    report["adapter_module"] = module
    assert module.split(".")[0] == "hermes_plugins", module
    assert "onair" not in sys.modules, "a top-level onair module was imported"
    assert norm(plugin_dir) not in [norm(p) for p in sys.path], "discovery left the plugin dir on sys.path"
    adapter_mod = sys.modules[module]
    cli_mod = sys.modules.get(pkg + ".onair.cli") or __import__(pkg + ".onair.cli", fromlist=["_data_dir"])
    expected = hermes_home / "plugin-data" / "on-air"
    report["adapter_data_dir_ok"] = Path(adapter_mod.data_dir()).resolve() == expected
    report["cli_data_dir_ok"] = Path(cli_mod._data_dir()).resolve() == expected
    assert report["adapter_data_dir_ok"] and report["cli_data_dir_ok"], report

    hermes_cmd = json.loads(os.environ["ONAIR_VERIFY_HERMES_CMD"])

    def status():
        done = subprocess.run(hermes_cmd + ["onair", "status"], capture_output=True, text=True,
                              timeout=120, stdin=subprocess.DEVNULL, encoding="utf-8", errors="replace")
        return done.returncode, done.stdout, done.stderr

    async def main():
        adapter = entry.adapter_factory(PlatformConfig(enabled=True, extra={}))
        ok = await adapter.connect()
        assert ok, "adapter connect() returned False"
        try:
            assert adapter.source is None, "a chat source started (no channel is configured)"
            assert adapter.tts_engine is None or not getattr(adapter.tts_engine, "enabled", False), \
                "voice engine active although voice is off"
            report["control_file_present"] = (expected / "control.json").is_file()
            report["token_mode"] = oct((expected / "desk.token").stat().st_mode & 0o777) if os.name != "nt" else "n/a"
            url = adapter.overlay.overlay_url
            report["overlay_loopback"] = url.startswith("http://127.0.0.1:")

            def fetch():  # off the event loop: the overlay server is served by this loop
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                with opener.open(url, timeout=10) as resp:
                    return resp.status

            report["overlay_http"] = await asyncio.get_running_loop().run_in_executor(None, fetch)
            rc, out, err = await asyncio.get_running_loop().run_in_executor(None, status)
            report["status_live_rc"] = rc
            state = json.loads(out) if rc == 0 else {}
            report["status_live_mode"] = state.get("mode")
        finally:
            await adapter.disconnect()
        report["control_file_after_disconnect"] = (expected / "control.json").exists()
        rc, out, err = await asyncio.get_running_loop().run_in_executor(None, status)
        report["status_after_rc"] = rc
        report["status_after_unavailable"] = "status unavailable" in err

    asyncio.run(main())
    print("LOADER " + json.dumps(report, sort_keys=True))
''')


INSTALL_MODES = ("installer", "copy")
MODULE_ENTRY = "python-module entry (<HERMES_PYTHON> -B -m hermes_cli.main), narrower than the public launcher"
_RUN_MAIN = "runpy.run_module('hermes_cli.main', run_name='__main__', alter_sys=True)"


class Verifier:
    def __init__(self, src: Path, hermes_python: Path, work_base: Path, root_name: str,
                 keep: bool = False, install_mode: str = "installer", allow_downloads: bool = False,
                 strict_version: bool = False, launcher: Path | None = None,
                 bootstrap_launcher: bool = False, timeout: int = 180):
        self.src = src.resolve()
        self.hermes_python = hermes_python
        self.work_base = work_base.resolve()
        self.root_name = root_name
        self.keep = keep
        if install_mode not in INSTALL_MODES:
            raise HarnessError("--install-mode must be one of %s" % ", ".join(INSTALL_MODES))
        self.install_mode = install_mode
        self.allow_downloads = allow_downloads
        self.strict_version = strict_version
        self.launcher = launcher
        self.bootstrap_launcher = bootstrap_launcher
        self.timeout = timeout
        self.steps: list[Step] = []
        self.root: Path | None = None
        self.token = secrets.token_hex(8)
        self.facts: dict = {}

    # -------------------------------------------------------------- safety
    def check_inputs(self) -> None:
        if not (self.src / "plugin.yaml").is_file() or not (self.src / "__init__.py").is_file():
            raise HarnessError("plugin source must contain plugin.yaml and __init__.py")
        if read_manifest(self.src).get("name") != PLUGIN_NAME:
            raise HarnessError("plugin.yaml does not name the on-air plugin")
        if _is_within(self.work_base, self.src):
            raise HarnessError("the work directory must be outside the plugin source tree")
        real_home = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
        for protected in {real_home, Path.home() / ".hermes"}:
            if protected.exists() and self.work_base.resolve() == protected.resolve():
                raise HarnessError("refusing to use a real Hermes home as the work directory")
        if not self.hermes_python.exists():
            raise HarnessError("--hermes-python does not exist")
        if self.launcher is not None and not self.launcher.is_file():
            raise HarnessError("--hermes-launcher does not exist")
        if self.bootstrap_launcher and self.launcher is None:
            raise HarnessError("--bootstrap-launcher needs --hermes-launcher")

    def create_root(self) -> None:
        self.work_base.mkdir(parents=True, exist_ok=True)
        root = Path(tempfile.mkdtemp(prefix=self.root_name, dir=self.work_base))
        (root / OWNED_MARKER).write_text(self.token, encoding="utf-8")
        self.root = root
        self.home = root / "home dir"
        self.hermes_home = self.home / ".hermes"
        self.plugin_dir = self.hermes_home / "plugins" / PLUGIN_NAME
        for d in (self.home, self.hermes_home, root / "tmp", self.hermes_home / "cache"):
            d.mkdir(parents=True, exist_ok=True)
        # The public catalog lookup is an external network boundary: answer it from an empty,
        # fresh cache inside the owned home so no install/discovery step needs the network.
        (self.hermes_home / "cache" / "plugin-catalog.json").write_text(
            json.dumps({"entries": [], "removed": []}), encoding="utf-8")
        self.seed_bank = root / "show notes ü 空白" / "seed bank.json"
        self.seed_bank.parent.mkdir()
        self.seed_bank.write_text(json.dumps({"segments": [{"name": "opening", "beats": [
            {"id": "intro", "text": "Welcome to the show.", "provenance": "public notes"}]}]}),
            encoding="utf-8")

    def cleanup(self) -> Step:
        root = self.root
        if root is None:
            return Step("cleanup", "skip", "no root was created")
        if self.keep:
            return Step("cleanup", "skip", "--keep: owned root left in place")
        marker = root / OWNED_MARKER
        if not marker.is_file() or marker.read_text(encoding="utf-8") != self.token:
            return Step("cleanup", "fail", "ownership marker missing or changed; not deleting")
        shutil.rmtree(root)
        gone = not root.exists()
        return Step("cleanup", "pass" if gone else "fail",
                    "owned root removed" if gone else "owned root still exists")

    # -------------------------------------------------------------- helpers
    def sanitize(self, text: str) -> str:
        pairs = []
        for real, label in ((self.root, "<ROOT>"), (self.src, "<SRC>"), (self.hermes_python, "<HERMES_PYTHON>"),
                            (self.launcher, "<HERMES_LAUNCHER>"),
                            (getattr(self, "runtime_tree", None), "<HERMES_TREE>"),
                            (getattr(self, "runtime_python", None), "<RUNTIME_PYTHON>"),
                            (self.work_base, "<WORK>"), (Path.home(), "~")):
            if real:
                pairs += [(str(real), label), (Path(real).resolve().as_uri(), "file://" + label)]
        # Longest first, so a root inside the work base becomes <ROOT>, not <WORK>/...
        for real, label in sorted(pairs, key=lambda pair: -len(pair[0])):
            text = text.replace(real, label)
        return text

    @property
    def hermes_cmd(self) -> list[str]:
        if self.launcher is not None:
            return [str(self.launcher)]
        return [str(self.hermes_python), "-B", "-m", "hermes_cli.main"]

    @property
    def entry_label(self) -> str:
        return "public launcher (<HERMES_LAUNCHER>)" if self.launcher is not None else MODULE_ENTRY

    def env(self, extra: dict[str, str] | None = None, network: bool = False) -> dict[str, str]:
        return build_env(self.root, self.home, self.hermes_home, extra, self.hermes_python, network)

    def run(self, args: list[str], *, extra_env=None, raw=False, network=False) -> tuple[int, str, str, float, list[str]]:
        cmd = list(args) if raw else self.hermes_cmd + list(args)
        start = time.monotonic()
        try:
            done = subprocess.run(cmd, env=self.env(extra_env, network), cwd=self.root / "tmp", capture_output=True,
                                  stdin=subprocess.DEVNULL, timeout=self.timeout, text=True,
                                  encoding="utf-8", errors="replace")
            rc, out, err = done.returncode, done.stdout, done.stderr
        except subprocess.TimeoutExpired as exc:
            rc, out, err = -9, str(exc.stdout or ""), "timed out after %ss" % self.timeout
        # The argv that actually ran (paths replaced by placeholders), never a prettified "hermes".
        shown = [self.sanitize(a) if len(a) < 400 else "<inline code, %d chars>" % len(a) for a in cmd]
        return rc, out, err, time.monotonic() - start, shown

    def record(self, name: str, ok: bool | None, detail: str, run=None, status: str | None = None) -> Step:
        rc = out = err = None
        secs, shown = 0.0, []
        if run is not None:
            rc, out, err, secs, shown = run
        step = Step(name, status or ("pass" if ok else "fail"), self.sanitize(detail),
                    [self.sanitize(arg) for arg in shown], rc,
                    self.sanitize((out or "")[-3000:]), self.sanitize((err or "")[-3000:]), round(secs, 2))
        self.steps.append(step)
        return step

    def write_config(self, data: dict) -> None:
        (self.hermes_home / "config.yaml").write_text(_json_config(data), encoding="utf-8")

    def write_env_file(self, allowed: str = "onair-stage") -> None:
        (self.hermes_home / ".env").write_text("ONAIR_ALLOWED_USERS=%s\n" % allowed, encoding="utf-8")

    # -------------------------------------------------------------- steps
    def step_runtime(self) -> bool:
        """The selected runtime's Hermes identity, read through ITS canonical provenance API.

        ``hermes_cli.version_info.get_version_info()`` resolves the executing tree's install stamp, then
        its live git; legacy ``hermes_cli.__version__`` is a stamp-only old-updater surface and the
        distribution version is packaging metadata, so both are kept only as diagnostics."""
        script = self.root / "tmp" / "runtime_probe.py"
        script.write_text(RUNTIME_PROBE, encoding="utf-8")
        argv = self._python_argv(script, "hermes_runtime")
        if argv is None:
            return False
        run = self.run(argv, raw=True)
        script.unlink()
        rc, out = run[0], run[1]
        try:
            info = json.loads(out.strip().splitlines()[-1]) if rc == 0 else None
        except (ValueError, IndexError):
            info = None
        if not info:
            self.record("hermes_runtime", False, "the Hermes interpreter could not import hermes_cli", run)
            return False
        # The selected launcher can run a different interpreter and source tree from the caller.
        # Learn their paths before recording the probe, so even the first diagnostic is shareable.
        self.runtime_tree = Path(info["executing_tree"]) if info.get("executing_tree") else None
        self.runtime_python = Path(info["python_executable"]) if info.get("python_executable") else None
        need = read_manifest(self.src).get("requires_hermes", "")
        status, why = runtime_identity_verdict(info, need)
        ident = info.get("identity") or {}
        self.facts.update({
            "python": info.get("python"), "platform": info.get("platform"),
            "hermes": ident.get("base_version"), "hermes_identity": ident,
            "hermes_identity_error": info.get("identity_error"),
            "hermes_executing_tree": self.sanitize(str(info.get("executing_tree"))),
            "hermes_cli_file": self.sanitize(str(info.get("hermes_cli_file"))),
            "hermes_version_info_file": self.sanitize(str(info.get("version_info_file"))),
            "hermes_tree_head": info.get("tree_head"), "hermes_legacy_version": info.get("legacy_version"),
            "dist_version": info.get("dist_version"), "requires_python": info.get("requires_python"),
            "requires_hermes": need, "hermes_identity_verdict": status,
        })
        detail = ("Python %s, Hermes %s from canonical version_info (source %s, commit %s, dirty %s) of the "
                  "executing tree %s; plugin requires %s; diagnostics only: legacy hermes_cli.__version__ %s, "
                  "hermes-agent distribution %s (Requires-Python %s): %s"
                  % (info.get("python"), ident.get("base_version"), ident.get("source"),
                     (ident.get("commit") or "none")[:12], ident.get("dirty"),
                     self.sanitize(str(info.get("executing_tree"))), need or "nothing", info.get("legacy_version"),
                     info.get("dist_version"), info.get("requires_python"), why))
        if status == "unverifiable":
            if self.strict_version:
                self.record("hermes_runtime", False, detail + " (--strict-version)", run)
                return False
            self.record("hermes_runtime", None, detail, run, status="limitation")
            return True
        ok = status == "pass"
        self.record("hermes_runtime", ok, detail, run)
        return ok

    def step_bootstrap(self) -> bool:
        """A fresh HERMES_HOME has no dependency environment for a PM launcher: ``pm repair`` makes one."""
        if not self.bootstrap_launcher:
            return True
        run = self.run(["pm", "repair"], network=self.allow_downloads)
        ok = run[0] == 0
        self.record("launcher_bootstrap", ok, "launcher `pm repair` in the owned home (dependency downloads %s)"
                    % ("allowed" if self.allow_downloads else "blocked"), run)
        return ok

    def step_install(self) -> bool:
        self.source_before = tree_digest(self.src)
        self.facts["install_mode"] = self.install_mode
        self.facts["entry"] = self.entry_label
        self.facts["dependency_downloads"] = ("allowed for install/bootstrap only" if self.allow_downloads
                                              else "blocked")
        staged = self.root / "plugin source ü 空白"
        shutil.copytree(self.src, staged, ignore=IGNORE)
        self.staged = staged
        if self.install_mode == "installer":
            installed, expected_source = self._install_with_plugin_manager(staged)
            if not installed:
                return False
        else:
            shutil.copytree(staged, self.plugin_dir, ignore=IGNORE)
            self.write_config({"plugins": {"enabled": [PLUGIN_NAME]}})
            self.record("install_copy_layout", self.plugin_dir.joinpath("plugin.yaml").is_file(),
                        "NARROWER than the plugin installer: copied the tree to $HERMES_HOME/plugins/on-air and "
                        "listed it in plugins.enabled; no plugin admission ran")
            expected_source = "user"
        run = self.run(["plugins", "list", "--json"])
        entry = None
        try:
            entry = next((e for e in json.loads(run[1]) if e.get("name") == PLUGIN_NAME), None)
        except ValueError:
            pass
        source = str((entry or {}).get("source") or "")
        source_ok = source == expected_source if expected_source == "user" else expected_source in source
        ok = run[0] == 0 and entry is not None and source_ok and entry.get("status") == "enabled"
        self.record("discover_list", ok, "hermes plugins list (expect source %r): %s" % (
            expected_source, json.dumps(entry) if entry else "on-air not listed"), (run[0], "", run[2], run[3], run[4]))
        return ok

    def _source_ref(self, staged: Path) -> tuple[Path, str]:
        """``file://`` source and exact commit: the selected checkout's own clean HEAD when it is one,
        else a throwaway commit of the copied tree (recorded as such, never as a checkout commit)."""
        git = shutil.which("git")
        if not git:
            raise HarnessError("installer mode needs git")

        def git_out(repo: Path, *args: str, env=None) -> str:
            return subprocess.run([git, "-C", str(repo), *args], env=env or self.env(), capture_output=True,
                                  text=True, timeout=60, stdin=subprocess.DEVNULL, check=True).stdout.strip()
        try:
            top = Path(git_out(self.src, "rev-parse", "--show-toplevel")).resolve()
            dirty = git_out(self.src, "status", "--porcelain", "--untracked-files=no")
        except (subprocess.CalledProcessError, OSError):
            top, dirty = None, ""
        if top == self.src and not dirty:
            sha = git_out(self.src, "rev-parse", "HEAD")
            self.facts["source_ref"] = {"kind": "selected checkout HEAD", "commit": sha}
            return self.src, sha
        ident = {"GIT_AUTHOR_NAME": "verify", "GIT_AUTHOR_EMAIL": "verify@example.invalid",
                 "GIT_COMMITTER_NAME": "verify", "GIT_COMMITTER_EMAIL": "verify@example.invalid"}
        for args in (["init", "-q"], ["add", "-A"], ["commit", "-qm", "staged plugin tree"]):
            git_out(staged, *args, env=self.env(ident))
        sha = git_out(staged, "rev-parse", "HEAD")
        self.facts["source_ref"] = {"kind": "throwaway commit of the selected tree (not a checkout commit)",
                                    "commit": sha,
                                    "reason": "uncommitted changes or not the checkout root" if top else "not a git checkout"}
        return staged, sha

    def _install_with_plugin_manager(self, staged: Path) -> tuple[bool, str]:
        repo, sha = self._source_ref(staged)
        run = self.run(["plugins", "install", repo.as_uri(), "--ref", sha, "--enable", "--yes-deps"],
                       network=self.allow_downloads)
        installed = run[0] == 0 and self.plugin_dir.joinpath("plugin.yaml").is_file()
        hint = "" if installed else install_failure_hint(run[1] + "\n" + run[2], self.allow_downloads)
        self.record("install_plugin_manager", installed, "hermes plugins install file://<tree> --ref %s --enable "
                    "--yes-deps -> %s%s" % (sha[:12], "$HERMES_HOME/plugins/on-air" if installed else "failed", hint),
                    run)
        if not installed:
            return False, ""
        # The installed tree is the selected tree: every shipped file matches its source bytes.
        installed_files = tree_digest(self.plugin_dir)
        tracked = subprocess.run([shutil.which("git") or "git", "-C", str(repo), "ls-tree", "-r", "-z", "--name-only",
                                  sha], env=self.env(), capture_output=True, text=True, timeout=60,
                                 stdin=subprocess.DEVNULL, check=True).stdout.split("\0")
        source_files = {p: h for p, h in tree_digest(repo).items() if p in set(tracked)}
        common = set(installed_files) & set(source_files)
        mismatched = sorted(p for p in common if installed_files[p] != source_files[p])
        missing = sorted(p for p in source_files if p not in installed_files)
        ok = not mismatched and not missing and len(common) > 0
        self.record("installed_tree_matches_source", ok,
                    "%d files byte-identical to the selected tree at %s; mismatched=%s missing=%s" % (
                        len(common), sha[:12], mismatched[:5], missing[:5]))
        return ok, "@" + sha[:8]

    def step_preflight(self) -> None:
        self.write_config(safe_config(self.seed_bank))
        self.write_env_file()
        # First as the home where the plugin was just installed: an owner home. Its configuration is right, but
        # it has the owner tools, so it must not be reported ready to run the stage.
        run = self.run(["onair", "preflight", "--json"])
        try:
            failed = {c.get("name") for c in json.loads(run[1]).get("checks", []) if c.get("status") == "fail"}
        except ValueError:
            failed = set()
        self.record("preflight_refuses_owner_home", run[0] == 1 and failed == {"owner_tools"},
                    "a correctly configured home that has the owner tools: rc=%s failed=%s" % (run[0], sorted(failed)),
                    run)
        # The remaining preflight and setup checks model a hand-configured stage home, which carries the marker.
        (self.hermes_home / STAGE_MARKER).write_text("verify_install: hand-configured stage home\n", encoding="utf-8")
        run = self.run(["onair", "preflight", "--json"])
        try:
            result = json.loads(run[1])
        except ValueError:
            result = {}
        statuses = {c.get("name"): c.get("status") for c in result.get("checks", [])}
        fails = sorted(n for n, s in statuses.items() if s == "fail")
        ok = (run[0] == 0 and result.get("ok") is True and not fails and statuses.get("runtime_gate") == "pass"
              and statuses.get("tts") == "pass" and statuses.get("seed_bank") == "pass")
        self.record("preflight_safe_json", ok, "legitimate captions-only config: ok=%s, fails=%s, tts=%s, "
                    "runtime_gate=%s, warnings=%s" % (result.get("ok"), fails, statuses.get("tts"),
                    statuses.get("runtime_gate"), sorted(n for n, s in statuses.items() if s == "warn")), run)
        run = self.run(["onair", "preflight"])
        self.record("preflight_safe_text", run[0] == 0 and "Preflight passed." in run[1],
                    "human-readable preflight passes", run)

        for label, cfg, users, must_fail in unsafe_variants(self.seed_bank):
            self.write_config(cfg)
            self.write_env_file(users)
            run = self.run(["onair", "preflight", "--json"])
            try:
                result = json.loads(run[1])
            except ValueError:
                result = {}
            failed = {c.get("name") for c in result.get("checks", []) if c.get("status") == "fail"}
            leaked = CANARY in run[1] or CANARY in run[2]
            ok = run[0] == 1 and result.get("ok") is False and must_fail <= failed and not leaked
            self.record("preflight_refuses_" + label, ok, "rc=%s ok=%s failed=%s expected⊇%s canary_echoed=%s" % (
                run[0], result.get("ok"), sorted(failed), sorted(must_fail), leaked), run)

        self.write_env_file()
        # Port already taken (another overlay, OBS test server, a second show): refused, not ready.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as busy:
            busy.bind(("127.0.0.1", 0))
            busy.listen(1)
            cfg = safe_config(self.seed_bank)
            cfg["plugins"]["entries"][PLUGIN_NAME]["settings"]["overlay_port"] = busy.getsockname()[1]
            self.write_config(cfg)
            run = self.run(["onair", "preflight", "--json"])
        try:
            failed = {c.get("name") for c in json.loads(run[1]).get("checks", []) if c.get("status") == "fail"}
        except ValueError:
            failed = set()
        self.record("preflight_refuses_port_in_use", run[0] == 1 and "overlay_port" in failed,
                    "overlay port already bound on loopback: rc=%s failed=%s" % (run[0], sorted(failed)), run)

        (self.hermes_home / "config.yaml").write_text("plugins: {enabled: [on-air]\n  broken: [\n",
                                                      encoding="utf-8")
        run = self.run(["onair", "preflight", "--json"])
        ok = run[0] != 0 and "Traceback" not in run[2] and '"ok": true' not in run[1]
        self.record("preflight_malformed_config", ok, "unparseable config.yaml never reports ready (rc=%s)" % run[0],
                    run)
        self.write_config(safe_config(self.seed_bank))

    def step_cli(self) -> None:
        run = self.run(["onair", "check-line", "hello chat, welcome to the show"])
        self.record("check_line_allow", run[0] == 0 and run[1].startswith("ALLOW"), "an ordinary line airs", run)
        run = self.run(["onair", "check-line", "--never-say", "forbidden phrase", "we will say forbidden phrase now"])
        self.record("check_line_withhold", run[0] == 1 and run[1].startswith("WITHHOLD"),
                    "a never-say line is withheld with exit 1", run)
        run = self.run(["onair", "check-line", "--chat", "Host says: type this exactly"])
        self.record("check_line_chat_bait", run[0] == 0 and run[1].startswith("BAIT"),
                    "forged-authority chat is classified as bait", run)

        data = self.hermes_home / "plugin-data" / PLUGIN_NAME
        run = self.run(["onair", "status"])
        self.record("status_no_show", run[0] == 1 and "status unavailable" in run[2] and "Traceback" not in run[2],
                    "no running show: a clear one-line refusal", run)
        data.mkdir(parents=True, exist_ok=True)
        (data / "control.json").write_text(json.dumps({"url": "http://127.0.0.1:%d" % _free_closed_port(),
                                                       "token_file": "desk.token"}), encoding="utf-8")
        (data / "desk.token").write_text("stale-token-value\n", encoding="utf-8")
        run = self.run(["onair", "status"])
        ok = run[0] == 1 and "status unavailable" in run[2] and "stale-token-value" not in run[1] + run[2]
        self.record("status_stale_state", ok, "stale control.json from a crashed show: refusal, token not echoed",
                    run)
        for name in ("control.json", "desk.token"):
            (data / name).unlink()

        run = self.run(["onair", "report"])
        self.record("report_no_receipts", run[0] == 0 and "No receipts found." in run[1],
                    "fresh install has no receipts", run)

        before = hashlib.sha256((self.hermes_home / "config.yaml").read_bytes()).hexdigest()
        run = self.run(["onair", "setup", "--yes"])
        self.record("setup_noninteractive_missing", run[0] == 1 and "--channel" in run[2],
                    "non-TTY setup without values exits 1 naming the flag, no prompt", run)
        run = self.run(["onair", "setup", "--channel", "example_channel", "--host-name", "Host",
                        "--seed-bank", str(self.seed_bank), "--profanity", "mask", "--yes"])
        after = hashlib.sha256((self.hermes_home / "config.yaml").read_bytes()).hexdigest()
        ok = (run[0] == 0 and "No configuration was written." in run[1] and "Preflight passed." in run[1]
              and before == after)
        self.record("setup_noninteractive_flags", ok, "non-TTY setup prints the block, runs preflight, "
                    "config.yaml unchanged=%s" % (before == after), run)
        # Back to an owner home for the first-run commands, which an owner runs from their own home.
        (self.hermes_home / STAGE_MARKER).unlink()
        self.step_onboarding()

    def step_onboarding(self) -> None:
        """The first-run commands: offline rehearsal, then a NEW stage home and its refusals."""
        data = self.hermes_home / "plugin-data" / PLUGIN_NAME
        had_data = data.exists()
        run = self.run(["onair", "rehearse", "--duration", "20"])
        ok = (run[0] == 0 and "=== SYNTHETIC OFFLINE REHEARSAL ===" in run[1] and "withheld by the real guard" in run[1]
              and "it is not a readiness check" in run[1] and data.exists() == had_data)
        self.record("rehearse_offline", ok, "synthetic offline rehearsal: guard withholds the bait, labelled not-readiness, "
                    "writes no plugin data", run)

        stage = self.root / "new stage ü"
        run = self.run(["onair", "setup", "--create-home", str(stage), "--channel", "example_channel",
                        "--host-name", "Host", "--yes"])
        files = sorted(p.name for p in stage.iterdir()) if stage.is_dir() else []
        config_text = (stage / "config.yaml").read_text(encoding="utf-8") if (stage / "config.yaml").is_file() else ""
        ok = (run[0] == 0 and "Created a NEW On Air stage home" in run[1]
              and files == [".env", ".onair-stage-home", "SOUL.md", "config.yaml", "seed_bank.yaml"]
              and not any(line.startswith("model:") for line in config_text.splitlines())
              and (stat.S_IMODE(stage.stat().st_mode) & 0o077) == 0)
        self.record("setup_create_home", ok, "a new 0700 stage home with 5 files and no model; "
                    "files=%s" % files, run)
        self.facts["stage_home_files_sha256"] = {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(stage.iterdir())
            if p.name != ".env" and p.is_file()
        } if stage.is_dir() else {}
        def signature(p):
            s = p.stat()
            return p.name, s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns
        before = sorted(signature(p) for p in stage.iterdir()) if stage.is_dir() else []
        run = self.run(["onair", "setup", "--create-home", str(stage), "--channel", "example_channel", "--yes"])
        after = sorted(signature(p) for p in stage.iterdir()) if stage.is_dir() else []
        self.record("setup_create_home_refuses_existing", run[0] == 1 and "already exists" in run[2] and before == after,
                    "an existing stage home is refused; file metadata unchanged", run)
        inside = self.hermes_home / "stage-inside"
        run = self.run(["onair", "setup", "--create-home", str(inside), "--channel", "example_channel", "--yes"])
        self.record("setup_create_home_refuses_overlap", run[0] == 1 and "overlaps" in run[2] and not os.path.lexists(inside),
                    "a stage home inside the current Hermes home is refused; nothing created", run)
        shutil.rmtree(stage, ignore_errors=True)

    def step_scene(self) -> None:
        starter = self.plugin_dir / "onair" / "scene_starter"
        names = ("index.html", "scene.js", "scene.css", "scene.json", "events.js", "room.js",
                 "geometry.js", "character.js", "lighting.js", "avatar.js", "avatar_logic.js", "quality.js")
        self.record("scene_starter_files", all((starter / name).is_file() for name in names),
                    "installed starter contains every scene source file")
        skills = self.plugin_dir / "onair" / "skills"
        present = all((skills / name / "SKILL.md").is_file() for name in ("scene", "scene-build", "avatar"))
        if present:
            script = self.root / "tmp" / "scene_skills.py"
            script.write_text(textwrap.dedent("""
                import hermes_cli.main
                from hermes_cli.plugins import discover_plugins
                from gateway.platform_registry import platform_registry
                discover_plugins()
                module = platform_registry.get('on_air').adapter_factory.__module__
                pkg = module.rsplit('.', 2)[0]
                setup = __import__(pkg + '.onair.setup_agent', fromlist=['register'])
                class Context:
                    def __init__(self): self.skills = []
                    def register_skill(self, name, path, **kwargs): self.skills.append(name)
                ctx = Context()
                setup.register(ctx)
                assert {'scene', 'scene-build', 'avatar'} <= set(ctx.skills), ctx.skills
            """), encoding="utf-8")
            argv = self._python_argv(script, "scene_skills")
            if argv is not None:
                run = self.run(argv, raw=True)
                self.record("scene_skills", run[0] == 0, "installed owner registration exposes all three scene skills", run)
            script.unlink()
        else:
            self.record("scene_skills", False, "installed scene skill files are missing")
        run = self.run(["onair", "--help"])
        self.record("scene_cli", run[0] == 0 and bool(re.search(r"\bscene\b", run[1])),
                    "installed On Air CLI exposes the scene subcommand", run)

    def step_loader(self) -> None:
        cfg = safe_config(self.seed_bank, channel="")  # no Twitch channel: no external chat connection
        self.write_config(cfg)
        script = self.root / "tmp" / "loader_check.py"
        script.write_text(_LOADER, encoding="utf-8")
        argv = self._python_argv(script, "loader_connect_status_teardown")
        if argv is None:
            script.unlink()
            return
        run = self.run(argv, raw=True,
                       extra_env={"ONAIR_VERIFY_HERMES_CMD": json.dumps(self.hermes_cmd),
                                  "ONAIR_ALLOWED_USERS": "onair-stage"})
        line = next((l for l in run[1].splitlines() if l.startswith("LOADER ")), None)
        facts = json.loads(line[7:]) if line else {}
        ok = (run[0] == 0 and facts.get("adapter_module", "").startswith("hermes_plugins.")
              and facts.get("overlay_http") == 200 and facts.get("status_live_rc") == 0
              and facts.get("status_live_mode") == "off_air" and facts.get("control_file_present") is True
              and facts.get("control_file_after_disconnect") is False and facts.get("status_after_rc") == 1
              and facts.get("status_after_unavailable") is True)
        self.record("loader_connect_status_teardown", ok, json.dumps(facts, sort_keys=True), run)
        script.unlink()
        self.write_config(safe_config(self.seed_bank))

    def _python_argv(self, script: Path, step: str) -> list[str] | None:
        """Interpreter argv for an in-Hermes script: the launcher's own runtime command when a launcher
        is in use (its interpreter and bootstrap), else ``--hermes-python`` directly."""
        if self.launcher is None:
            return [str(self.hermes_python), "-B", str(script)]
        run = self.run(["--print-runtime-command"])
        try:
            argv = json.loads(run[1].strip().splitlines()[-1])
        except (ValueError, IndexError):
            argv = None
        if not (isinstance(argv, list) and argv and isinstance(argv[-1], str) and _RUN_MAIN in argv[-1]):
            self.record(step, False, "the launcher's --print-runtime-command output was not recognised, so "
                        "this check cannot run on the launcher's runtime", run)
            return None
        return argv[:-1] + [argv[-1].replace(_RUN_MAIN, "runpy.run_path(sys.argv.pop(1), run_name='__main__')"),
                            str(script)]

    def step_remove(self) -> None:
        run = self.run(["plugins", "remove", PLUGIN_NAME])
        gone = not self.plugin_dir.exists()
        self.record("uninstall", run[0] == 0 and gone, "hermes plugins remove: plugin dir removed=%s" % gone, run)
        run = self.run(["onair", "preflight"])
        self.record("uninstall_command_gone", run[0] != 0 and "not a `hermes` command" in run[1] + run[2],
                    "after removal the onair command is no longer registered", run)
        leftovers = sorted(p.name for p in (self.hermes_home / "plugin-data" / PLUGIN_NAME).glob("*")) \
            if (self.hermes_home / "plugin-data" / PLUGIN_NAME).is_dir() else []
        self.facts["plugin_data_after_remove"] = leftovers

    def step_source_untouched(self) -> None:
        after = tree_digest(self.src)
        changed = sorted(set(after.items()) ^ set(self.source_before.items()))
        pyc = [p for p in after if p.endswith(".pyc") or "/__pycache__/" in "/" + p]
        self.record("source_untouched", not changed and not pyc,
                    "selected plugin tree byte-identical (%d files), no bytecode written" % len(after)
                    if not changed and not pyc else "changed: %s" % [c[0] for c in changed][:10])

    # -------------------------------------------------------------- driver
    def run_all(self) -> dict:
        self.check_inputs()
        self.create_root()
        try:
            if self.step_bootstrap() and self.step_runtime() and self.step_install():
                self.step_preflight()
                self.step_cli()
                self.step_scene()
                self.step_loader()
                self.step_remove()
            if hasattr(self, "source_before"):
                self.step_source_untouched()
        except Exception as exc:  # report, then still clean up
            self.record("harness_error", False, self.sanitize("%s: %s" % (type(exc).__name__, exc)))
        finally:
            root_label = self.sanitize(str(self.root))
            self.steps.append(self.cleanup())
        counts = {s: sum(1 for st in self.steps if st.status == s) for s in ("pass", "fail", "skip", "limitation")}
        return {
            "ok": counts["fail"] == 0 and counts["pass"] > 0,
            "counts": counts,
            "facts": self.facts,
            "root": root_label,
            "steps": [step.__dict__ for step in self.steps],
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0],
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--plugin-src", type=Path, default=Path(__file__).resolve().parents[1],
                        help="plugin tree to install (default: this checkout)")
    parser.add_argument("--hermes-python", type=Path, default=Path(sys.executable),
                        help="interpreter that has Hermes installed (default: this one)")
    parser.add_argument("--work-base", type=Path, default=Path(tempfile.gettempdir()),
                        help="where to create the owned throwaway root (default: the temp dir)")
    parser.add_argument("--root-name", default="onair verify ü-",
                        help="prefix for the owned root; spaces and non-ASCII are deliberate")
    parser.add_argument("--report", type=Path, help="write the JSON report here (outside the plugin tree)")
    parser.add_argument("--keep", action="store_true", help="leave the owned root in place for inspection")
    parser.add_argument("--install-mode", choices=INSTALL_MODES, default="installer",
                        help="installer: Hermes' plugin manager (default); copy: narrower copy + plugins.enabled")
    parser.add_argument("--allow-dependency-downloads", action="store_true",
                        help="let ONLY the plugin install and launcher bootstrap reach the package index so the "
                             "plugin manager can fetch its pinned runtime into the owned home")
    parser.add_argument("--strict-version", action="store_true",
                        help="fail (not limit) when the Hermes version cannot be verified against plugin.yaml")
    parser.add_argument("--hermes-launcher", type=Path,
                        help="run CLI steps through this `hermes` launcher instead of the Python-module entry")
    parser.add_argument("--bootstrap-launcher", action="store_true",
                        help="run `<launcher> pm repair` in the owned home first (a fresh home needs it)")
    parser.add_argument("--timeout", type=int, default=180, help="seconds per command")
    args = parser.parse_args(argv)
    if args.report and _is_within(args.report.parent, args.plugin_src):
        print("error: write the report outside the plugin tree", file=sys.stderr)
        return 2
    try:
        verifier = Verifier(args.plugin_src, args.hermes_python, args.work_base, args.root_name, keep=args.keep,
                            install_mode=args.install_mode, allow_downloads=args.allow_dependency_downloads,
                            strict_version=args.strict_version, launcher=args.hermes_launcher,
                            bootstrap_launcher=args.bootstrap_launcher, timeout=args.timeout)
        result = verifier.run_all()
    except HarnessError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2
    for step in result["steps"]:
        print("%-11s %-38s %s" % (step["status"].upper(), step["name"], step["detail"]))
    print("summary: %s" % json.dumps(result["counts"]))
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
