"""The REAL stage, operated by the owner's own agent: plan -> owner approval -> start -> observed state -> stop
-> start again (resume).

The stage home is the one THIS setup created and saved (``saved.json``; it carries the stage marker). The
supported way to run a stage is Hermes's own public CLI with that home, so the approved start runs exactly three
fixed commands and nothing else (no shell, no arguments from a model or a user):

0. ``hermes config path`` (read-only): the home Hermes actually resolves must be the stage's own;
1. ``hermes onair preflight`` with ``HERMES_HOME=<stage>`` (read-only safety check; it must pass), then
2. ``hermes gateway run`` with ``HERMES_HOME=<stage>``, as its own process session.

The child gets a minimal environment (``_ENV_KEEP`` plus ``HERMES_HOME`` and Hermes's profile pin): never this process's environment
wholesale, so the owner's own provider keys (which Hermes loads into the owner's process) are not inherited.
The stage reads its OWN config/.env, exactly as when the owner starts it in a terminal.

An inherited-environment allowlist is not the whole boundary: every Hermes CLI process ALSO loads, at startup
and again on each gateway turn, the installation's project ``.env`` (``hermes_cli/main.py`` and
``gateway/run.py`` ``load_hermes_dotenv(project_env=<install root>/.env)``) and the administrator-managed scope
(``/etc/hermes``, ``hermes_cli/managed_scope.py``); and provider credential discovery may look in ``$HOME``.
So before ANY CLI process (even ``config path``) the start checks, by file metadata only (nothing is read):
the CLI is the installation this plugin runs in, that installation has no project ``.env`` and there is no
managed scope; otherwise it refuses with the exact reason. The child's ``HOME`` is a stage-owned folder
(``plugin-data/on-air/home``), so owner-home credential files are not discovered. The stage's own
``.env``/``secrets:`` (its explicitly configured connection) stay the only secret sources it can use.

Approval: ``status`` and every internal re-check are read-only (``readiness``); only an explicit ``plan``
issues an approval code, a newer plan replaces it, and ``start`` compares and consumes exactly the code the
owner was shown, under the setup lock, after the owner's card returned. One approval, one start attempt.

Stop truth: the endpoints a started stage announced are saved in its runtime record. A stop is complete only
when no process of the stage's own session remains and every saved endpoint is closed; otherwise the record
(and its endpoints) is kept, the stop says NOT confirmed, status says so, and no new start replaces it.

Prepared vs unprepared: a stage is startable only when its own plugin is present and enabled and its own model
is configured. Otherwise the plan names the exact missing prerequisite and its secure next action (the
plugin manager / ``hermes model`` in the stage home, which ask for any key themselves); nothing is started,
installed, downloaded or copied. A plugin folder present in the stage is reported as "present and enabled",
which may be a plugin-manager install or a drop-in copy: this module does not claim plugin-manager acceptance.

The stage always starts OFF AIR. Going on air (approval/auto mode) and approving lines stay with the owner on
the stage's desk; the agent's controls are safety-only: off air, stop.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from . import setup_engine as se

PLAN_FILE = "stage-plan.json"
RUNTIME_FILE = "stage-runtime.json"
LOG_FILE = "stage-gateway.log"
REFUSAL_FILE = "stage-refusal.json"
START_WAIT_S = 45.0
STOP_WAIT_S = 30.0
PREFLIGHT_TIMEOUT_S = 90.0
# Non-secret process basics only. Provider keys, tokens and the rest of the owner's environment stay behind.
_ENV_KEEP = ("PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "TMPDIR", "PYTHONPATH",
             "PYTHONDONTWRITEBYTECODE", "HERMES_DISABLE_LAZY_INSTALLS", "UV_OFFLINE")
_URL = re.compile(r"http://127\.0\.0\.1:([1-9][0-9]{0,4})")
_CHILDREN: dict[int, subprocess.Popen] = {}  # this process's own started stages (reaped on stop)
_MANAGED_DIR = Path("/etc/hermes")  # hermes_cli/managed_scope.py default (HERMES_MANAGED_DIR is not passed on)


def own_launchers() -> list[Path]:
    """Where the ``hermes`` CLI of the Hermes THIS plugin runs in can be: beside this interpreter (an
    environment install), or the self-managed launcher inside the installation root that this process loads
    ``hermes_cli`` and ``gateway`` from (``<root>/.hermes/bin/hermes``; its interpreter has no ``hermes`` beside
    it). Paths only; nothing is checked for existence here."""
    found = [Path(sys.executable).with_name("hermes")]
    roots, unknown = _install_roots()
    if not unknown:
        found += [root / ".hermes" / "bin" / "hermes" for root in sorted(roots)]
    return found


def hermes_command() -> list[str] | None:
    """The public ``hermes`` CLI of the Hermes this plugin runs in (same install), else the one on PATH."""
    for own in own_launchers():
        if own.is_file() and os.access(own, os.X_OK):
            return [str(own)]
    found = shutil.which("hermes")
    return [found] if found else None


def stage_user_home(home: Path) -> Path:
    """The stage process's own ``$HOME``: a stage-owned folder, never the owner's home (credential discovery)."""
    path = home / "plugin-data" / "on-air" / "home"
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


_ORIGIN_PACKAGES = ("hermes_cli", "gateway")  # the two packages whose project root .env a Hermes process loads


def _package_origin(name: str) -> Path | None:
    """The file a package of THIS interpreter (the one the admitted ``hermes`` beside it runs) is loaded from,
    located by metadata (``find_spec`` does not import it); None when it cannot be attributed to a real file
    (missing, namespace/frozen/builtin, or a lookup error)."""
    import importlib.util
    try:
        spec = importlib.util.find_spec(name)
    except (ImportError, ValueError, AttributeError):
        return None
    origin = getattr(spec, "origin", None) if spec is not None else None
    if not origin or origin in ("namespace", "built-in", "frozen") or not os.path.isfile(origin):
        return None
    return Path(origin).resolve()


def _install_roots() -> tuple[set[Path], list[str]]:
    """The project roots whose ``.env`` the CLI and the gateway load (``PROJECT_ROOT`` / ``parents[1]``), plus
    the packages whose origin could NOT be attributed (then nothing can be called checked). Both packages may
    share one root."""
    roots, unknown = set(), []
    for name in _ORIGIN_PACKAGES:
        origin = _package_origin(name)
        if origin is None or len(origin.parents) < 2:
            unknown.append(name)
            continue
        roots.add(origin.parents[1])
    return roots, unknown


def install_isolation(argv0: list | None) -> list[dict]:
    """Metadata-only check (nothing is opened or read) of the startup secret planes every Hermes CLI process loads
    besides its own home: the installation's project ``.env`` and the administrator-managed scope. Returns the
    problems; empty means the stage's own home is its only secret source. Runs before ANY CLI process."""
    problems: list[dict] = []
    if not argv0:
        return [{"key": "isolation", "ok": False, "agent_can_do": False, "detail": "The hermes command was not found.",
                 "next_action": "Install Hermes."}]
    own = own_launchers()
    if Path(argv0[0]) not in own:
        problems.append({"key": "isolation", "ok": False, "agent_can_do": False,
                         "detail": (f"The hermes command {argv0[0]} is not the installation this plugin runs in "
                                    f"({' or '.join(str(p) for p in own)}), so setup cannot check what that "
                                    "installation loads at startup."),
                         "next_action": "Run the stage from the same Hermes installation as this agent."})
        return problems
    roots, unknown = _install_roots()
    if unknown:  # an unverifiable origin is never "checked and safe" by an empty loop
        problems.append({"key": "isolation", "ok": False, "agent_can_do": False,
                         "detail": ("Setup could not locate where this installation loads "
                                    + " and ".join(unknown) + " from (" + " and ".join(_ORIGIN_PACKAGES)
                                    + " must both resolve to files of this Hermes installation; they may share "
                                    "one folder), so it cannot check which project .env the stage would load."),
                         "next_action": "Run the stage from a complete, normally installed Hermes (the same one as "
                                        "this agent); setup does not start the stage until it can check this."})
        return problems
    for root in sorted(roots):
        env_file = root / ".env"
        if os.path.lexists(env_file):
            problems.append({"key": "isolation", "ok": False, "agent_can_do": False,
                             "detail": (f"This Hermes installation has a project-level {env_file}. Every Hermes process "
                                        "loads it at startup and on each gateway turn (hermes_cli/main.py, "
                                        "gateway/run.py), so the stage would get whatever it contains, not only its "
                                        "own connection. Setup did not open it."),
                             "next_action": (f"The owner moves the settings in {env_file} into their own Hermes home "
                                             "(or removes that file) and plans the stage start again. Setup never "
                                             "edits, reads or copies it.")})
    if _MANAGED_DIR.is_dir():
        problems.append({"key": "isolation", "ok": False, "agent_can_do": False,
                         "detail": (f"An administrator-managed Hermes scope exists at {_MANAGED_DIR}; it applies to "
                                    "every Hermes process, including the stage, so setup cannot show the stage is "
                                    "limited to its own connection."),
                         "next_action": f"The administrator of {_MANAGED_DIR} decides; setup does not start the stage "
                                        "while it is present."})
    return problems


def child_env(home: Path) -> dict:
    env = {k: os.environ[k] for k in _ENV_KEEP if os.environ.get(k)}
    env["HOME"] = str(stage_user_home(home))  # not the owner's home: no owner credential-file discovery
    env["HERMES_HOME"] = str(home)
    # Hermes's CLI re-homes a process whose HERMES_HOME is not ``.../profiles/<name>`` to a sticky
    # ``active_profile`` of that root (hermes_cli/main.py _apply_profile_override); its own profile pin
    # (the marker the gateway gives its tool children, _under_gateway_supervisor) keeps THIS home.
    env["HERMES_GATEWAY_PROFILE_PIN"] = "1"
    return env


def effective_home(argv0: list, env: dict, home: Path) -> tuple[bool, str]:
    """Which home Hermes ACTUALLY resolves with this environment (``hermes config path``, read-only),
    checked before anything is started: it must be the stage's own config."""
    try:
        done = subprocess.run([*argv0, "config", "path"], env=env, cwd=str(home), capture_output=True, text=True,
                              timeout=PREFLIGHT_TIMEOUT_S, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return False, ""
    lines = [ln.strip() for ln in done.stdout.splitlines() if ln.strip()]
    if done.returncode != 0 or len(lines) != 1:
        return False, ""
    try:
        got = Path(lines[0])
        if not got.is_absolute() or got.name != 'config.yaml' or not got.is_file() or not got.parent.is_dir():
            return False, ""
        return got.resolve() == (home / 'config.yaml').resolve(), str(got)
    except (OSError, ValueError):
        return False, ""



# Readiness must verify the target too, independently of the start's final recheck.
_probe_effective_home = effective_home


def _ticks(pid: int) -> str | None:
    """Process start time (clock ticks since boot): with the pid it identifies one process, not a reused pid."""
    try:
        stat = Path(f"/proc/{int(pid)}/stat").read_text(encoding="utf-8", errors="replace")
        fields = stat.rsplit(")", 1)[1].split()
        if fields[0] in ("Z", "X"):  # exited, not yet reaped by its parent: gone, not running
            return None
        return fields[19]
    except (OSError, ValueError, IndexError):
        return None


def _identity(pid: int) -> tuple[str, str | None]:
    """Who holds ``pid`` NOW, for decisions that could signal or control it. Unlike ``_ticks`` (None for every
    failure) this keeps 'cannot tell' apart from 'gone':
    ('absent', None)  - affirmatively no process: /proc entry missing AND a signal-0 probe says ESRCH;
    ('zombie', ticks) - exited, not yet reaped (ticks when readable, else None);
    ('live', ticks)   - a running process with these start ticks;
    ('unknown', None) - unreadable/malformed /proc data, EPERM or any other inspection failure.
    The signal-0 probe is an observation (no signal is delivered); EPERM is unknown, never absence."""
    try:
        stat = Path(f"/proc/{int(pid)}/stat").read_text(encoding="utf-8", errors="replace")
    except (FileNotFoundError, ProcessLookupError):
        try:
            os.kill(int(pid), 0)
        except ProcessLookupError:
            return "absent", None
        except OSError:
            return "unknown", None
        return "unknown", None  # it exists, but its /proc identity cannot be read
    except (OSError, ValueError):
        return "unknown", None
    try:
        fields = stat.rsplit(")", 1)[1].split()
        state, ticks = fields[0], fields[19]
        int(ticks)
    except (IndexError, ValueError):
        return "unknown", None
    if state in ("Z", "X"):
        return "zombie", ticks
    return "live", ticks


def _alive(record: dict | None) -> bool:
    if not record or not record.get("pid"):
        return False
    pid = int(record["pid"])
    child = _CHILDREN.get(pid)
    if child is not None and child.poll() is not None:
        return False
    return _ticks(pid) is not None and _ticks(pid) == record.get("ticks")


def _port_open(url: str) -> bool:
    import socket
    match = _URL.match(url or "")
    if not match:
        return False
    with socket.socket() as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", int(match.group(1)))) == 0


def saved_stage(eng: se.SetupEngine) -> tuple[Path | None, str]:
    saved = eng._read("saved.json") or {}
    raw = saved.get("home")
    if not raw:
        return None, "No stage has been set up yet. Set up On Air first (onair_setup_plan, then the user approves)."
    home = Path(raw)
    if not se.is_stage_home(home):
        return None, f"The saved stage folder {home} is missing or is not an On Air stage; set up On Air again."
    return home, ""


def _stage_config(home: Path) -> dict:
    from .yamlio import YAMLError, safe_load
    try:
        value = safe_load((home / "config.yaml").read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, *YAMLError):
        return {}


def prerequisites(home: Path) -> list[dict]:
    """Every real-stage prerequisite, re-read from the stage's own files, with who does it and how."""
    from .setup_wizard import readiness
    ready = readiness(str(home))
    steps = {s.get("key"): s for s in ready.get("steps") or []}
    cfg = _stage_config(home)
    model = cfg.get("model")
    has_model = (isinstance(model, dict) and bool(model.get("default") or model.get("provider"))) or (
        isinstance(model, str) and bool(model.strip()))
    plugin = steps.get("plugin")
    voice = steps.get("voice")
    voice_row = next((row for row in ready.get("rows", []) if row.get("key") == "voice"), {})
    out = [
        {"key": "plugin", "ok": plugin is None, "agent_can_do": False,
         "detail": ("On Air is present and enabled in the stage's plugins folder (plugin-manager install or a "
                    "drop-in copy; this check does not tell them apart).") if plugin is None else plugin.get("note"),
         "next_action": "" if plugin is None else ("The owner runs, in a terminal: " + str(
             plugin.get("command") or "".join(plugin.get("template") or []))
             + "  (the plugin manager may download Hermes's own dependencies; the agent does not run it)")},
        {"key": "model", "ok": has_model, "agent_can_do": False,
         "detail": "The stage has its own model configured." if has_model else "The stage has no model yet.",
         "next_action": "" if has_model else (f"The owner runs HERMES_HOME={home} hermes model and enters the "
                                              "provider key there. Setup never reads or copies keys.")},
    ]
    if voice is not None:
        out.append({"key": "voice", "ok": False, "agent_can_do": False, "detail": voice.get("note"),
                    "next_action": "Configure text-to-speech for the stage home with " + str(voice.get("command"))
                                   + ", or save captions only."})
    elif (((cfg.get("plugins") or {}).get("entries") or {}).get("on-air") or {}).get("settings", {}).get("voice") is True:
        out.append({"key": "voice", "ok": True, "agent_can_do": False, "detail": voice_row["detail"],
                    "next_action": ""})
    host_row = next((row for row in ready.get("rows", []) if row.get("key") == "host_turns"), None)
    if host_row:
        out.append({"key": "host_turns", "ok": True, "agent_can_do": False,
                    "detail": host_row["detail"], "next_action": ""})
    out += [
        {"key": "preflight", "ok": None, "agent_can_do": True,
         "detail": "Run by the approved start (hermes onair preflight in the stage home); it must pass.",
         "next_action": ""},
        {"key": "gateway", "ok": None, "agent_can_do": True,
         "detail": "Run by the approved start (hermes gateway run in the stage home); it starts OFF AIR.",
         "next_action": ""},
    ]
    return out


def _digest(home: Path) -> str:
    h = hashlib.sha256(b"onair-stage-start\0" + str(home).encode() + b"\0")
    for rel in ("config.yaml", "SOUL.md", "plugins/on-air/plugin.yaml", "plugins/on-air/__init__.py"):
        with contextlib.suppress(OSError):
            h.update(rel.encode() + b"\0" + (home / rel).read_bytes() + b"\0")
    return h.hexdigest()


def _stage_preflight(argv0: list, home: Path) -> dict:
    """Run the stage's own CLI preflight with the same isolated environment as start."""
    try:
        env = child_env(home)
        same, resolved = _probe_effective_home(argv0, env, home)
        if not same:
            detail = "Hermes could not confirm the saved stage folder. Check the stage home selection and try again."
            return {"ok": False, "missing": [{"key": "target", "ok": False, "agent_can_do": True,
                    "detail": detail, "next_action": "Fix the stage home selection and try again."}]}
        done = subprocess.run([*argv0, "onair", "preflight", "--json"], env=env, cwd=str(home),
                              capture_output=True, text=True, timeout=PREFLIGHT_TIMEOUT_S, stdin=subprocess.DEVNULL)
        result = next((json.loads(line) for line in reversed(done.stdout.splitlines())
                       if line.strip().startswith('{')), None)
        if not isinstance(result, dict) or not isinstance(result.get("ok"), bool) or not isinstance(result.get("checks"), list):
            raise ValueError("invalid preflight result")
        failures = [c for c in result["checks"] if isinstance(c, dict) and c.get("status") == "fail"
                    and isinstance(c.get("name"), str) and isinstance(c.get("detail"), str)]
        if done.returncode != 0 and not failures:
            raise ValueError("preflight failed without a reason")
        if not result["ok"] and not failures:
            raise ValueError("preflight failed without a reason")
        if done.returncode == 0 and result["ok"] and not failures:
            return {"ok": True, "missing": []}
        from .cli import _NEXT_ACTIONS
        actions = {**_NEXT_ACTIONS, "control_port": "Free the desk port or set control_port in the stage settings."}
        missing = []
        for check in failures:
            key = check["name"] if check["name"] in actions else "preflight"
            action = actions.get(key, "Check the stage configuration and run its preflight again.")
            detail = "The stage safety check failed: " + key + "."
            if key in ("overlay_port", "control_port"):
                settings = (((_stage_config(home).get("plugins") or {}).get("entries") or {}).get("on-air") or {}).get("settings") or {}
                port = settings.get(key, 18790 if key == "overlay_port" else 0)
                if isinstance(port, int) and not isinstance(port, bool) and 0 <= port <= 65535:
                    detail = f"{key} port {port} is invalid or unavailable."
            missing.append({"key": key, "ok": False, "agent_can_do": True,
                            "detail": detail, "next_action": action})
        return {"ok": False, "missing": missing}
    except (OSError, subprocess.SubprocessError, ValueError, TypeError):
        return {"ok": False, "missing": [{"key": "preflight", "ok": False, "agent_can_do": True,
                "detail": "The stage's safety check could not be completed.",
                "next_action": "Check the stage configuration and run its preflight again."}]}


def record_refusal(eng: se.SetupEngine, result: dict) -> None:
    """Keep the last owner-visible refusal separately from process ownership records."""
    missing = result.get("missing") or []
    reason = "; ".join(str(m.get("detail")) for m in missing if m.get("detail")) or str(result.get("error") or "Start failed.")
    remedy = str(result.get("next_action") or "Check the stage status and fix the cause before planning another start.")
    with eng._file_lock():
        eng._write(REFUSAL_FILE, {"time": time.time(), "reason": reason, "remedy": remedy})


def _clear_refusal(eng: se.SetupEngine) -> None:
    with eng._file_lock():
        eng._path(REFUSAL_FILE).unlink(missing_ok=True)


def _port_lock() -> se.SetupEngine:
    """One local lock for this user's stage probes and starts, across owner homes."""
    import getpass
    import tempfile
    identity = str(os.getuid()) if hasattr(os, "getuid") else hashlib.sha256(getpass.getuser().encode()).hexdigest()[:16]
    return se.SetupEngine(Path(tempfile.gettempdir()) / ("onair-stage-ports-" + identity))


def readiness(eng: se.SetupEngine) -> dict:
    """Read-only, writes nothing: what starting the real stage would do, or the exact prerequisites still missing.
    Used by status and by every internal re-check; it never issues or replaces an approval code."""
    home, error = saved_stage(eng)
    if home is None:
        return {"ok": False, "prepared": False, "error": error}
    prereqs = prerequisites(home)
    missing = [p for p in prereqs if p["ok"] is False]
    argv0 = hermes_command()
    if argv0 is None:
        missing.append({"key": "hermes", "ok": False, "agent_can_do": False,
                        "detail": "The hermes command was not found.", "next_action": "Install Hermes."})
    else:
        isolation = install_isolation(argv0)
        prereqs = prereqs + (isolation or [{"key": "isolation", "ok": True, "agent_can_do": False,
                                            "detail": "This installation has no project .env and no managed scope "
                                                      "(checked by file metadata only).", "next_action": ""}])
        missing += isolation
        record = eng._read(RUNTIME_FILE)
        running = _alive(record)
        if not running and record:
            left = _leftover(record)
            if not left["quiescent"] or record.get("stop_unconfirmed"):
                problem = {"key": "previous", "ok": False, "agent_can_do": True,
                           "detail": ("The previous stage is NOT confirmed stopped"
                                      + (" (its stop is still unconfirmed)." if record.get("stop_unconfirmed") else ".")),
                           "next_action": "Stop it first with onair_stage_safety stop or /onair-setup stage stop."}
                prereqs.append(problem)
                missing.append(problem)
        if not running and _control_any(home) is not None:
            problem = {"key": "previous", "ok": False, "agent_can_do": False,
                       "detail": "Another process already runs this stage.",
                       "next_action": "Stop the other stage before starting another."}
            prereqs.append(problem)
            missing.append(problem)
        # A running stage already owns its listeners; the existing-start path only reports it.
        if not missing and not running:
            try:
                with _port_lock()._file_lock(blocking=False), eng._file_lock(blocking=False):
                    if _alive(eng._read(RUNTIME_FILE)):
                        raise BlockingIOError()
                    pre = _stage_preflight(argv0, home)
            except OSError:
                return {"ok": False, "prepared": False, "unknown": True,
                        "error": "Readiness is unknown: a start or safety check is in progress. Check again.",
                        "next_action": "Wait for it to finish, then check again."}
            prereqs += pre["missing"]
            missing += pre["missing"]
    if missing:
        return {"ok": False, "prepared": False, "home": str(home), "prerequisites": prereqs, "missing": missing,
                "error": "The real stage is not prepared yet: " + "; ".join(m["key"] for m in missing)
                         + ". Nothing was started.",
                "next_action": " ".join(m["next_action"] for m in missing if m["next_action"])}
    settings = (((_stage_config(home).get("plugins") or {}).get("entries") or {}).get("on-air") or {}).get("settings") or {}
    effects = [f"Runs the safety check: HERMES_HOME={home} hermes onair preflight (must pass)",
               f"Starts the real stage: HERMES_HOME={home} hermes gateway run (its own process, until stopped)",
               f"It reads PUBLIC Twitch chat twitch.tv/{settings.get('twitch_channel', '?')} anonymously over the "
               "network and uses the stage's OWN model connection",
               "It starts OFF AIR: nothing is shown until you choose Approval on that stage's desk",
               "Stop any time: ask your agent, or /onair-setup stage stop"]
    voice = next((p for p in prereqs if p["key"] == "voice" and p["ok"]), None)
    if voice:
        effects.append(voice["detail"])
    return {"ok": True, "prepared": True, "home": str(home), "digest": _digest(home), "effects": effects,
            "prerequisites": prereqs}


def plan(eng: se.SetupEngine) -> dict:
    """The explicit plan: readiness plus a NEW approval code (any older code stops working)."""
    ready = readiness(eng)
    if not ready.get("ok"):
        return ready
    code = secrets.token_hex(3)
    with eng._file_lock():
        eng._write(PLAN_FILE, {"digest": ready["digest"], "code": code, "home": ready["home"], "made": time.time()})
    return {**ready, "approve_with": {"chat": f"/onair-setup stage approve {code}"}}


def _stage_dir(home: Path) -> Path:
    return home / "plugin-data" / "on-air"


def _control(home: Path, pid: int | None) -> dict | None:
    """The running stage's control.json, only when the stage process named in it is OUR started process."""
    try:
        info = json.loads((_stage_dir(home) / "control.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(info, dict) or pid is None or info.get("pid") != pid:
        return None
    return info


def desk_call(home: Path, info: dict, method: str, path: str, body: dict | None = None) -> tuple[int, Any]:
    """The stage's own token-authenticated desk API (loopback). Used only for read-only state and off air."""
    import http.client
    if (method, path) not in {("GET", "/state"), ("GET", "/pending"), ("POST", "/mode"),
                              ("POST", "/privacy"), ("POST", "/mute"), ("POST", "/stop"), ("POST", "/host/turn"), ("POST", "/cut-in")}:
        return 404, {"error": "not a stage route"}
    if path == "/host/turn" and (not isinstance(body, dict) or set(body) != {"text", "epoch", "boot", "via"}
                                  or not isinstance(body["text"], str) or type(body["epoch"]) is not int
                                  or not isinstance(body["boot"], str) or not re.fullmatch(r"[0-9a-f]{32}", body["boot"])
                                  or body["via"] != "relay"):
        return 403, {"error": "invalid relay request"}
    if path == "/cut-in" and (not isinstance(body, dict) or set(body) != {"epoch", "boot"}
                               or type(body["epoch"]) is not int or not isinstance(body["boot"], str)
                               or not re.fullmatch(r"[0-9a-f]{32}", body["boot"])):
        return 403, {"error": "invalid cut request"}
    if path == "/mode" and body != {"mode": "off_air"}:
        return 403, {"error": "the agent may only take the stage off air"}
    if path in ("/privacy", "/mute") and (not isinstance(body, dict) or set(body) != {"on"} or body["on"] is not True):
        return 403, {"error": "the agent may only enable this safety control"}
    if path == "/stop" and body not in (None, {}):
        return 403, {"error": "invalid stop request"}
    return _desk_request(home, info, method, path, body)


def _desk_request(home: Path, info: dict, method: str, path: str, body: dict | None) -> tuple[int, Any]:
    import http.client
    match = _URL.fullmatch(info.get("url", ""))
    try:
        token = (_stage_dir(home) / str(info.get("token_file") or "desk.token")).read_text(encoding="ascii").strip()
        if not match:
            raise ValueError("bad url")
        conn = http.client.HTTPConnection("127.0.0.1", int(match.group(1)), timeout=3)
        try:
            headers = {"Authorization": "Bearer " + token, "Accept": "application/json"}
            payload = None
            if method == "POST":
                headers["Content-Type"] = "application/json"
                payload = json.dumps(body or {}).encode()
            conn.request(method, path, payload, headers)
            response = conn.getresponse()
            data = json.loads(response.read(1024 * 1024 + 1))
            return response.status, data
        finally:
            conn.close()
    except (OSError, ValueError, TypeError):
        return 503, {"error": "the stage desk did not answer"}


def safe_control(eng: se.SetupEngine, action: str) -> dict:
    routes = {"state": ("GET", "/state", None), "pending": ("GET", "/pending", None),
              "privacy_on": ("POST", "/privacy", {"on": True}),
              "mute_on": ("POST", "/mute", {"on": True}),
              "stop": ("POST", "/stop", {}), "off_air": ("POST", "/mode", {"mode": "off_air"}),
              "cut_in": ("POST", "/cut-in", None)}
    if action not in routes:
        return {"ok": False, "error": "That action is not available to the agent."}
    home, error = saved_stage(eng)
    record = eng._read(RUNTIME_FILE)
    if home is None or not isinstance(record, dict) or record.get("home") != str(home) or not _alive(record):
        return {"ok": False, "error": error or "The saved stage is not confirmed running."}
    info = _control(home, record.get("pid"))
    if info is None:
        return {"ok": False, "error": "The stage desk is not available."}
    method, path, body = routes[action]
    if action == "cut_in":
        state_code, before = desk_call(home, info, "GET", "/state")
        if (state_code != 200 or not isinstance(before, dict) or type(before.get("epoch")) is not int
                or not isinstance(before.get("voice_boot"), str)
                or not re.fullmatch(r"[0-9a-f]{32}", before["voice_boot"])):
            return {"ok": False, "error": "The stage state could not be read."}
        body = {"epoch": before["epoch"], "boot": before["voice_boot"]}
    code, result = desk_call(home, info, method, path, body)
    if code != 200 or not isinstance(result, dict) or (method == "POST" and result.get("ok") is not True):
        return {"ok": False, "error": "The stage did not confirm the action.", "result": result}
    if method == "POST":
        check_code, state = desk_call(home, info, "GET", "/state")
        verified = (check_code == 200 and isinstance(state, dict) and
                    ((action == "privacy_on" and state.get("privacy") is True) or
                     (action == "mute_on" and state.get("muted") is True) or
                     (action == "off_air" and state.get("mode") == "off_air") or
                     (action == "stop" and state.get("mode") in ("paused", "off_air")) or
                     (action == "cut_in" and state.get("voice_boot") == body["boot"] and state.get("epoch") == body["epoch"] + 1)))
        if not verified:
            return {"ok": False, "error": "The stage action could not be confirmed.", "result": result}
        return {"ok": True, "result": result, "state": state}
    return {"ok": result.get("ok", True) is True, "result": result}


def relay_host_turn(eng: se.SetupEngine, text: str) -> dict:
    """Relay owner words to the verified saved stage; a fresh epoch is not a freshness proof."""
    home, error = saved_stage(eng)
    record = eng._read(RUNTIME_FILE)
    if home is None or not isinstance(record, dict) or record.get("home") != str(home) or not _alive(record):
        return {"ok": False, "reason": "stage_unavailable", "message": error or "The saved stage is not confirmed running."}
    info = _control(home, record.get("pid"))
    if info is None:
        return {"ok": False, "reason": "desk_unavailable", "message": "The stage desk is not available."}
    code, state = desk_call(home, info, "GET", "/state")
    if (code != 200 or not isinstance(state, dict) or type(state.get("epoch")) is not int
            or not isinstance(state.get("voice_boot"), str) or not re.fullmatch(r"[0-9a-f]{32}", state["voice_boot"])):
        return {"ok": False, "reason": "state_unavailable", "message": "The stage state could not be read."}
    code, result = desk_call(home, info, "POST", "/host/turn", {"text": text, "epoch": state["epoch"], "boot": state["voice_boot"], "via": "relay"})
    if code == 200 and isinstance(result, dict) and result.get("ok") is True and result.get("queued") is True:
        return {**result, "message": "Host line queued; it has not aired yet."}
    if isinstance(result, dict) and result.get("ok") is False:
        return result
    return {"ok": False, "reason": "unconfirmed", "message": "The stage did not confirm the relay."}


def owner_command(eng: se.SetupEngine, action: str, value: Any = None) -> dict:
    """Narrow desk operations for a local human's typed command, never a model tool."""
    if action == "cut" and value is None:
        result = safe_control(eng, "cut_in")
        return {**result, "message": "Cut requested." if result.get("ok") else "The stage refused the cut."}
    if action not in ("say", "mode", "pending", "approve", "privacy_off", "mute_off"):
        return {"ok": False, "message": "Unknown stage command."}
    home, error = saved_stage(eng)
    record = eng._read(RUNTIME_FILE)
    if home is None or not isinstance(record, dict) or record.get("home") != str(home) or not _alive(record):
        return {"ok": False, "message": error or "The saved stage is not confirmed running."}
    info = _control(home, record.get("pid"))
    if info is None:
        return {"ok": False, "message": "The stage desk is not available."}
    if action == "pending":
        code, result = desk_call(home, info, "GET", "/pending")
        return {"ok": code == 200 and isinstance(result, dict), "result": result}
    if action == "say":
        code, state = desk_call(home, info, "GET", "/state")
        if code != 200 or not isinstance(state, dict) or type(state.get("epoch")) is not int:
            return {"ok": False, "message": "The stage state could not be read."}
        method, path, body = "POST", "/host/turn", {"text": value, "epoch": state["epoch"]}
    elif action in ("privacy_off", "mute_off") and value is None:
        method, path, body = "POST", "/privacy" if action == "privacy_off" else "/mute", {"on": False}
    elif action == "mode" and value in ("approval", "autonomous"):
        method, path, body = "POST", "/mode", {"mode": value}
    elif action == "approve" and isinstance(value, dict) and set(value) == {"line_id", "epoch"}:
        method, path, body = "POST", "/approve", value
    else:
        return {"ok": False, "message": "Invalid stage command."}
    code, result = _desk_request(home, info, method, path, body)
    if code != 200 or not isinstance(result, dict) or result.get("ok") is not True:
        return {"ok": False, "message": (result.get("message") or result.get("error") or "The stage refused the request.") if isinstance(result, dict) else "The stage did not answer."}
    if action == "say":
        return {"ok": True, "message": "Host line queued; it has not aired yet.", "result": result}
    check_code, state = desk_call(home, info, "GET", "/state")
    if check_code != 200 or not isinstance(state, dict):
        return {"ok": False, "message": "The action was requested, but the stage state could not be checked."}
    if action in ("privacy_off", "mute_off") and state.get("privacy" if action == "privacy_off" else "muted") is not False:
        return {"ok": False, "message": "The requested safety change was not confirmed."}
    if action == "mode" and state.get("mode") != value:
        return {"ok": False, "message": "The requested mode was not confirmed."}
    return {"ok": True, "message": "Stage action accepted.", "state": state}


def _session_scan(sid: int) -> tuple[list[int], bool]:
    """Live (non-zombie) processes of the stage's OWN session (started with start_new_session: sid == pid), and
    whether the scan was COMPLETE: every /proc entry was read, or vanished (exited) while being read. A scan that
    could not read some entry (or /proc itself) is incomplete and never counts as 'no member left'. A session id
    is not reused while any member exists, so these are the stage's own processes."""
    found, complete = [], True
    try:
        entries = list(Path("/proc").iterdir())
    except OSError:
        return [], False
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            fields = (entry / "stat").read_text(encoding="utf-8", errors="replace").rsplit(")", 1)[1].split()
        except (FileNotFoundError, ProcessLookupError):
            continue  # that process exited while it was being read
        except (OSError, IndexError):
            complete = False
            continue
        if len(fields) <= 4:
            complete = False  # malformed: membership unknown, never 'not a member'
            continue
        if fields[0] not in ("Z", "X") and fields[3] == str(sid):
            found.append(int(entry.name))
    return sorted(found), complete


def _session_members(sid: int) -> list[int]:
    """Live members of the stage's own session (see ``_session_scan``)."""
    return _session_scan(sid)[0]


def _endpoints(info: dict | None) -> list[str]:
    return [str(u).rsplit("/overlay", 1)[0] for u in ((info or {}).get("url"), (info or {}).get("overlay_url")) if u]


def _remember(eng: se.SetupEngine, record: dict, **fields: Any) -> dict:
    """Add observations to THIS runtime generation's record (compare: same gen/pid), under the setup lock."""
    with eng._file_lock():
        now = eng._read(RUNTIME_FILE) or {}
        if now.get("gen") != record.get("gen") or now.get("pid") != record.get("pid"):
            return now
        if "endpoints" in fields:
            fields["endpoints"] = sorted(set(now.get("endpoints") or []) | set(fields["endpoints"]))
        now.update(fields)
        eng._write(RUNTIME_FILE, now)
        return now


def _leftover(record: dict) -> dict:
    """What of a started stage is still there, from its OWN saved observations (never from absent ones).

    Endpoints that were observed are checked. Endpoints that were never observed are UNKNOWN, not 'none': the
    only positive proof used for them is session-scoped. Every On Air socket is opened by the stage's own
    gateway process (onair/overlay/server.py asyncio.start_server, inside the adapter), and a socket only stays
    open while some process holds it; under the accepted model (the stage's processes stay in its own session)
    a COMPLETE scan showing the leader and every session member gone proves no process of this stage can still
    hold one. An incomplete scan proves nothing."""
    pid = int(record["pid"])
    members, complete = _session_scan(pid)
    known = list(record.get("endpoints") or [])
    still = [u for u in known if _port_open(u)]
    leader = _alive(record)
    held = "live" if leader else _identity(pid)[0]  # unknown current identity is NOT absence
    session_gone = not leader and held != "unknown" and not members and complete
    return {"leader_alive": leader, "session_processes": members, "session_scan_complete": complete,
            "leader_identity": held,
            "endpoints": known, "endpoints_observed": bool(known), "open": still,
            "unobserved_endpoints_proof": (None if known else
                                           ("session_gone: leader exited and a complete scan found no process of "
                                            "its session" if session_gone else None)),
            "quiescent": session_gone and not still}


def status(eng: se.SetupEngine) -> dict:
    """Observed state of the real stage this setup started (never a cached claim)."""
    home, error = saved_stage(eng)
    record = eng._read(RUNTIME_FILE)
    refusal = eng._read(REFUSAL_FILE)
    if home is None:
        return {"ok": True, "running": False, "error": error, **({"last_refusal": refusal} if refusal else {})}
    out: dict = {"ok": True, "home": str(home), "running": False, "state": "stopped"}
    if refusal:
        out["last_refusal"] = refusal
    if not record or record.get("home") != str(home):
        out["note"] = "The real stage is not running (not started by this setup)."
        return out
    pid = int(record["pid"])
    if not _alive(record):
        left = _leftover(record)
        if left["leader_identity"] == "unknown":
            out.update(state="identity_unknown", pid=pid, leftover=left, log=record.get("log"),
                       note=(f"The process now holding pid {pid} cannot be identified (unreadable or malformed "
                             "/proc data), so the stage is NOT known to be gone and its stop is NOT confirmed; "
                             "use onair_stage_safety stop (or /onair-setup stage stop) again."))
            return out
        if not left["quiescent"] or record.get("stop_unconfirmed"):
            out.update(state="stop_unconfirmed", pid=pid, leftover=left, log=record.get("log"),
                       note=("The stage's main process is gone, but its stop is NOT confirmed: "
                             + (f"processes of its session still run ({left['session_processes']}); "
                                if left["session_processes"] else "")
                             + (f"endpoints still answer ({left['open']}); " if left["open"] else "")
                             + "use onair_stage_safety stop (or /onair-setup stage stop) again."))
            if left["quiescent"]:
                out["note"] = ("The stage's processes are gone and its saved endpoints are closed; run the stop "
                               "once more to confirm it and clear the record.")
            return out
        out.update(state="exited", pid=pid, note="The stage process this setup started is no longer running.",
                   log=record.get("log"))
        child = _CHILDREN.get(pid)
        if child is not None and child.returncode is not None:
            out["exit_code"] = child.returncode
        return out
    info = _control(home, pid)
    out.update(running=True, pid=pid, started_at=record.get("started"), argv=record.get("argv"))
    if info is None:
        out.update(state="starting", note="The stage process is running; its On Air platform has not connected yet.")
        return out
    _remember(eng, record, endpoints=_endpoints(info))
    code, state = desk_call(home, info, "GET", "/state")
    live = (state.get("state") or state) if isinstance(state, dict) else {}
    pending = live.get("pending")
    out.update(state="connected" if code == 200 else "unanswered", desk_status=code,
               mode=live.get("mode") if code == 200 else None,
               host_turns=live.get("host_turns") if code == 200 else None, overlay_url=info.get("overlay_url"),
               desk_url=info.get("url"), pending=len(pending) if isinstance(pending, list) else pending,
               next_action=("The stage is OFF AIR. To go on air, the owner chooses Approval on this stage's desk "
                            f"(HERMES_HOME={home} hermes dashboard --isolated, On Air tab).")
               if code == 200 and live.get("mode") == "off_air" else "")
    if code != 200:
        out["note"] = "The stage's desk did not answer, so its mode is unknown (not confirmed off air)."
    return out


def describe(stage: dict) -> str:
    """Plain words for an OBSERVED stage state (never 'off air' unless the desk said so)."""
    state, mode = stage.get("state"), stage.get("mode")
    pid = stage.get("pid")
    if state == "connected" and mode == "off_air":
        return f"The real stage is running OFF AIR (pid {pid}, state connected)."
    if state == "connected":
        return (f"The real stage is running and its desk reports mode {mode!r} (pid {pid}): it is NOT off air. "
                "Use onair_stage_safety off_air (or /onair-setup stage off) to take it off air.")
    if state == "unanswered":
        return (f"The real stage process is running (pid {pid}) but its desk did not answer, so its mode is "
                "unknown (NOT confirmed off air).")
    if state == "starting":
        return f"The real stage process is running (pid {pid}) but has not connected yet; its mode is unknown."
    if state == "stop_unconfirmed":
        return str(stage.get("note"))
    return f"Real stage: {state}" + (". " + str(stage.get("note")) if stage.get("note") else "")


def _start(eng: se.SetupEngine, digest: Any, consent: Any, code: Any = None) -> dict:
    """Owner-approved only, with exactly the approval code the owner was shown: preflight, then start the stage
    gateway; returns the OBSERVED state."""
    if not isinstance(consent, se.OwnerConsent) or consent.digest != str(digest):
        return {"ok": False, "refused": True, "error": "Starting the real stage needs the owner's own approval."}
    home, error = saved_stage(eng)
    if home is None:
        return {"ok": False, "error": error}
    fresh = readiness(eng)  # re-check every prerequisite now (read-only: it issues no code)
    if not fresh.get("ok"):
        return fresh
    if fresh.get("digest") != digest:
        return {"ok": False, "error": "The stage changed since that plan was made; plan it again. Nothing was started."}
    argv0 = hermes_command()
    if argv0 is None:
        return {"ok": False, "error": "The hermes command was not found. Nothing was started."}
    with eng._file_lock(), _port_lock()._file_lock():
        stored = eng._read(PLAN_FILE) or {}
        if not code or stored.get("code") != str(code) or stored.get("digest") != digest \
                or stored.get("home") != str(home):
            return {"ok": False, "refused": True, "error": "That approval code is no longer current (it was used, or a "
                    "newer plan replaced it). Nothing was started; plan the stage start again."}
        eng._write(PLAN_FILE, {})  # consumed now: one approval, one start attempt
        record = eng._read(RUNTIME_FILE)
        if _alive(record):
            stage = status(eng)
            return {"ok": True, "already_running": True, "stage": stage}
        if record:
            left = _leftover(record)
            if not left["quiescent"] or record.get("stop_unconfirmed"):
                return {"ok": False, "step": "previous", "leftover": left,
                        "error": "The previous stage is NOT confirmed stopped (" + (
                            "its current holder cannot be identified; "
                            if left["leader_identity"] == "unknown" else "") + (
                            "its stop is still unconfirmed; " if record.get("stop_unconfirmed") else "") + (
                            f"session processes {left['session_processes']}; " if left["session_processes"] else "")
                        + (f"endpoints still answering {left['open']}; " if left["open"] else "")
                        + "), so nothing was started. Stop it first: onair_stage_safety stop or /onair-setup stage stop."}
            eng._path(RUNTIME_FILE).unlink(missing_ok=True)  # that generation is positively gone
        foreign = _control_any(home)
        if foreign is not None:
            return {"ok": False, "error": "Another process already runs this stage (pid "
                    f"{foreign.get('pid')}); this setup did not start it and will not start a second one."}
        isolation = install_isolation(argv0)  # metadata only, before ANY CLI process
        if isolation:
            return {"ok": False, "step": "isolation", "missing": isolation,
                    "error": "Nothing was started: " + " ".join(p["detail"] for p in isolation)}
        env = child_env(home)
        same, resolved = effective_home(argv0, env, home)
        if not same:
            return {"ok": False, "step": "target", "resolved": resolved,
                    "error": (f"Hermes would not run the saved stage folder {home} with this start"
                              + (f" (it resolved {resolved})" if resolved else "") + ", so nothing was started."),
                    "next_action": "Fix the stage home selection and try again."}
        pre = _stage_preflight(argv0, home)
        if not pre["ok"]:
            return {"ok": False, "step": "preflight", "missing": pre["missing"],
                    "error": "The stage's safety check (preflight) did not pass, so nothing was started.",
                    "next_action": " ".join(p["next_action"] for p in pre["missing"])}
        log = open(eng._path(LOG_FILE), "ab")  # noqa: SIM115 - handed to the child
        try:
            child = subprocess.Popen([*argv0, "gateway", "run"], env=env, cwd=str(home), stdin=subprocess.DEVNULL,
                                     stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        finally:
            log.close()
        _CHILDREN[child.pid] = child
        eng._write(RUNTIME_FILE, {"home": str(home), "pid": child.pid, "ticks": _ticks(child.pid),
                                  "gen": secrets.token_hex(8), "endpoints": [],
                                  "argv": [Path(argv0[0]).name, "gateway", "run"], "digest": digest,
                                  "started": time.time(), "log": str(eng._path(LOG_FILE)),
                                  "approved_via": consent.source})
        deadline = time.monotonic() + START_WAIT_S
        while time.monotonic() < deadline:
            now = status(eng)
            if now.get("state") in ("connected", "exited", "stop_unconfirmed"):
                break
            time.sleep(0.25)
    now = status(eng)
    ok = now.get("state") == "connected" and now.get("mode") == "off_air"
    if ok:
        return {"ok": True, "stage": now, "preflight": "passed"}
    kept = bool(now.get("running"))
    return {"ok": False, "stage": now, "preflight": "passed", "retained": kept,
            "error": "The stage was started but did not report a connected, off-air On Air platform in time"
                     + (f"; its process (pid {now.get('pid')}) is STILL RUNNING and was kept so its log can be "
                        "read. Stop it with onair_stage_safety stop (or /onair-setup stage stop), then plan a new "
                        "start." if kept else "; its process is no longer running.")
                     + f" Log: {eng._path(LOG_FILE)}"}


def start(eng: se.SetupEngine, digest: Any, consent: Any, code: Any = None) -> dict:
    try:
        result = _start(eng, digest, consent, code)
    except (OSError, subprocess.SubprocessError):
        result = {"ok": False, "error": "The stage start could not be completed.",
                  "next_action": "Check stage status and the stage log before trying again."}
    # Do not let an unauthorised caller overwrite an owner's diagnosis.
    if isinstance(consent, se.OwnerConsent) and consent.digest == str(digest):
        if result.get("ok") and not result.get("already_running"):
            _clear_refusal(eng)
        elif not result.get("ok") and not result.get("unknown"):
            record_refusal(eng, result)
    return result


def _control_any(home: Path) -> dict | None:
    """A control.json whose named process is alive (any owner): one stage per home."""
    try:
        info = json.loads((_stage_dir(home) / "control.json").read_text(encoding="utf-8"))
        os.kill(int(info.get("pid")), 0)
        return info
    except (OSError, ValueError, TypeError):
        return None


def _off_air(eng: se.SetupEngine, expect: dict | None = None) -> dict:
    """Safety, always allowed: take the running stage off air (nothing more is shown).

    ``ok`` with ``mode: off_air`` only when the stage's own desk confirmed it. A stage that is positively gone is
    a no-op (``running: False``, no mode claimed); a dead leader with an unconfirmed stop is NOT off air. The
    POST goes only to the exact runtime generation read here (or ``expect``, the caller's captured one), checked
    under the setup lock right before it, so a replacement stage never receives an older operation's request."""
    home, error = saved_stage(eng)
    with eng._file_lock():
        record = eng._read(RUNTIME_FILE)
    if expect is not None and (not record or record.get("gen") != expect.get("gen")
                               or record.get("pid") != expect.get("pid")):
        return {"ok": False, "error": "The stage record changed (another start or stop ran); nothing was sent."}
    if not record:
        return {"ok": True, "running": False, "stopped": True,
                "note": error or "The real stage is not running (not started by this setup), so there is nothing "
                                 "to take off air."}
    if home is None:  # a missing saved target is NOT proof that the recorded stage is gone: observe the record
        alive = _alive(record)
        left = None if alive else _leftover(record)
        if left is not None and left["quiescent"] and not record.get("stop_unconfirmed"):
            return {"ok": True, "running": False, "stopped": True, "leftover": left,
                    "note": error + " The stage process this setup recorded is gone (complete check), so there is "
                                    "nothing to take off air (no mode was confirmed)."}
        return {"ok": False, "running": alive, "state": "target_missing", "leftover": left,
                "error": error + " A stage this setup started is still recorded and may still be running, so it "
                                 "is NOT confirmed off air and nothing was sent to it. Use onair_stage_safety stop "
                                 "(or /onair-setup stage stop)."}
    if not _alive(record):
        left = _leftover(record)
        if left["quiescent"] and not record.get("stop_unconfirmed"):
            return {"ok": True, "running": False, "stopped": True, "leftover": left,
                    "note": "The stage process this setup started is gone, so there is nothing to take off air "
                            "(no mode was confirmed: nothing is running)."}
        if left["leader_identity"] == "unknown":
            return {"ok": False, "running": None, "state": "identity_unknown", "leftover": left,
                    "error": f"The process now holding pid {record['pid']} cannot be identified, so the stage is "
                             "NOT known to be gone, its output cannot be observed and it is NOT confirmed off "
                             "air; nothing was sent. Use onair_stage_safety stop (or /onair-setup stage stop)."}
        return {"ok": False, "running": False, "state": "stop_unconfirmed", "leftover": left,
                "error": "The stage's main process is gone but its stop is NOT confirmed, so its output cannot be "
                         "observed and it is NOT confirmed off air. Use onair_stage_safety stop "
                         "(or /onair-setup stage stop)."}
    pid = int(record["pid"])
    info = _control(Path(record["home"]), pid)
    if info is None:
        return {"ok": False, "error": "The stage has not connected yet, so it could not be taken off air; stop it instead."}
    with eng._file_lock():  # re-validate the exact generation right before the request
        current_info = _control(Path(record["home"]), pid)
        now = eng._read(RUNTIME_FILE) or {}
        if now.get("gen") != record.get("gen") or now.get("pid") != pid or now.get("home") != record.get("home") \
                or current_info != info:
            return {"ok": False, "error": "The stage record changed (another start or stop ran); nothing was sent."}
        code, state = desk_call(Path(record["home"]), info, "POST", "/mode", {"mode": "off_air"})
    live = (state.get("state") or state) if isinstance(state, dict) else {}
    ok = code == 200 and live.get("mode") == "off_air"
    return {"ok": ok, "mode": live.get("mode"), **({} if ok else {"error": "The stage did not confirm off air."})}


def _stop(eng: se.SetupEngine) -> dict:
    """Safety, always allowed: off air, then end the stage's own process session; ok only when no process of
    that session remains and every endpoint it announced is closed. Otherwise the record is KEPT."""
    home, error = saved_stage(eng)
    with eng._file_lock():
        record = eng._read(RUNTIME_FILE)
    if not record:
        return {"ok": True, "running": False, "note": error or "The real stage is not running (not started by this setup)."}
    pid = int(record["pid"])
    info = _control(Path(record["home"]), pid) if _alive(record) else None
    if info is not None:  # save what it announced BEFORE anything can make it disappear
        captured = record
        record = _remember(eng, record, endpoints=_endpoints(info))
        if record.get("gen") != captured.get("gen") or record.get("pid") != pid:  # a replacement: never act on it
            return {"ok": False, "error": "The stage record changed during the stop (another start or stop ran); "
                    "nothing was sent or signalled. Check status and stop again."}
    went_off = off_air(eng, expect=record) if info is not None else {"ok": None}  # bound to THIS generation
    with eng._file_lock():  # re-validate after the blocking desk call: still the same generation?
        now = eng._read(RUNTIME_FILE) or {}
        if now.get("gen") != record.get("gen") or now.get("pid") != pid:
            return {"ok": False, "error": "The stage record changed during the stop (another start or stop ran); "
                    "nothing was signalled. Check status and stop again."}
        record = now
        held, current = _identity(pid)
        # a DIFFERENT lifetime now holds that pid (it cannot share the old session: Linux never reuses a pid
        # still used as a session id): veto every signal, keep the record, report it unconfirmed
        foreign = held in ("live", "zombie") and current != record.get("ticks")
        # who holds the pid cannot be established (unreadable/malformed/EPERM): NOT absence -> same veto
        unknown = held == "unknown"
        if not foreign and not unknown and (_alive(record) or _session_members(pid)):
            with contextlib.suppress(OSError):
                os.killpg(pid, signal.SIGTERM)  # its own session/group (start_new_session): the gateway and children
    if unknown:
        _remember(eng, record, stop_unconfirmed=True)
        # receipt only: off_air() having been CALLED is not proof an HTTP request went out (it can refuse/no-op)
        attempted = went_off.get("ok") is not None
        confirmed = attempted and went_off.get("mode") == "off_air"
        why = went_off.get("error") or went_off.get("state") or went_off.get("note") or "no confirmation"
        return {"ok": False, "running": None, "pid": pid, "state": "identity_unknown", "signalled": False,
                "off_air": went_off.get("ok"), "off_air_attempted": attempted,
                "off_air_result": went_off if attempted else None,
                "error": f"the process now holding pid {pid} could not be identified (its /proc data is "
                         "unreadable or malformed), so it is NOT treated as gone and NO signal was sent"
                         + ((" (an off-air operation was attempted earlier in this stop, while it was still "
                             "identified; " + ("the desk reported mode off_air then" if confirmed else
                                              f"it did NOT confirm off air: {why}") + ")")
                            if attempted else " and no off-air operation was attempted in this stop")
                         + "; the stop is NOT confirmed and the stage record is kept. Check status and stop again."}
    if foreign:
        _remember(eng, record, stop_unconfirmed=True)
        return {"ok": False, "running": False, "pid": pid, "state": "foreign_lifetime", "signalled": False,
                "error": f"pid {pid} now belongs to a different process than the stage this setup started "
                         "(its start time differs), so NO signal was sent to it; the stop is NOT confirmed "
                         "and the stage record is kept. Check status and stop again."}
    deadline = time.monotonic() + STOP_WAIT_S
    while time.monotonic() < deadline and (_alive(record) or _session_members(pid)):
        time.sleep(0.2)
    child = _CHILDREN.get(pid)
    if child is not None:
        with contextlib.suppress(subprocess.TimeoutExpired):
            child.wait(1)
    left = _leftover(record)
    if not left["quiescent"]:
        _remember(eng, record, stop_unconfirmed=True)
        return {"ok": False, "running": left["leader_alive"], "pid": pid, "off_air": went_off.get("ok"),
                "leftover": left, "open": left["open"],
                "error": ("The stage's main process did not exit in time" if left["leader_alive"] else
                          "The stage's main process exited, but "
                          + ("processes of its session still run " + str(left["session_processes"])
                             if left["session_processes"] else "")
                          + (" and " if left["session_processes"] and left["open"] else "")
                          + ("endpoints it announced still answer " + str(left["open"]) if left["open"] else "")
                          + ("" if left["session_scan_complete"] else
                             ("; " if left["session_processes"] or left["open"] else "")
                             + "its session could not be fully checked (some processes were unreadable)"))
                         + "; the stop is NOT confirmed and the stage record is kept. Stop it again.",
                "endpoints_observed": left["endpoints_observed"]}
    with eng._file_lock():  # compare-and-delete exactly this generation
        now = eng._read(RUNTIME_FILE) or {}
        if now.get("gen") == record.get("gen") and now.get("pid") == pid:
            eng._path(RUNTIME_FILE).unlink(missing_ok=True)
    _CHILDREN.pop(pid, None)
    return {"ok": True, "running": False, "pid": pid, "off_air_first": went_off.get("ok"),
            "endpoints_checked": left["endpoints"], "endpoints_observed": left["endpoints_observed"],
            "unobserved_endpoints_proof": left["unobserved_endpoints_proof"],
            "exit_code": child.returncode if child is not None else None,
            "note": "The real stage is stopped: no process of its session remains (complete check)"
                    + (" and the endpoints it announced are closed." if left["endpoints"] else
                       ". Its endpoints were never observed, so none were checked; with no process of its own "
                       "session left, none of the stage's own processes can still hold one.")}


def off_air(eng: se.SetupEngine, expect: dict | None = None) -> dict:
    result = _off_air(eng, expect)
    if result.get("ok"):
        _clear_refusal(eng)
    return result


def stop(eng: se.SetupEngine) -> dict:
    result = _stop(eng)
    if result.get("ok"):
        _clear_refusal(eng)
    return result
