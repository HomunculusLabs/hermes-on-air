"""Backend for the dashboard's first-run setup wizard.

The wizard never writes files itself. ``plan`` validates the answers and the target path with
the existing onboarding checks (``validate_options`` / ``check_target``) and describes exactly
what would be created. ``apply`` needs an explicit confirmation and then calls the existing
creator, ``onboarding.create_stage_home``, with the same refusals as
``hermes onair setup --create-home``: only a NEW directory outside every Hermes home, nothing
read from or copied out of the current home. ``readiness`` re-reads the created stage home from
disk and runs the real preflight checks against it, then reports plain-language rows.

What the dashboard cannot do for a separate stage home (Hermes has no API for it): install the
On Air plugin into it, choose/test its model provider, start its gateway. Those stay explicit,
listed as remaining actions with exact commands.
"""
from __future__ import annotations

import os
from pathlib import Path

from .onboarding import (MARKER, StageHomeError, StageOptions, check_target, create_stage_home,
                         render_config, shell_quote, validate_options)

_MAX_TEXT = 200


def _text(answers: dict, key: str, default: str = "") -> str:
    value = answers.get(key, default)
    if value is None:
        value = default
    if not isinstance(value, str):
        raise StageHomeError(f"{key} must be text.")
    return value.strip()


def options_from(answers: dict) -> tuple[str, StageOptions, dict[str, str]]:
    """Turn wizard answers into (target, StageOptions); field errors are collected per field."""
    if not isinstance(answers, dict):
        raise StageHomeError("answers must be an object")
    errors: dict[str, str] = {}
    channel = _text(answers, "channel").lstrip("#")
    host = _text(answers, "host_name", "Host")
    agent = _text(answers, "agent_name", "Co-host")
    topics = " ".join(_text(answers, "topics").split())  # one public line, no injected structure
    voice = _text(answers, "voice", "captions") or "captions"
    target = _text(answers, "target")
    try:
        from .sources.twitch_irc import normalize_channel
        normalize_channel(channel)
    except (ValueError, TypeError):
        errors["channel"] = ("Enter your Twitch channel name, like my_channel (letters, digits and "
                             "underscores, 4 to 25 characters, without the #).")
    for key, value, label in (("host_name", host, "Your name"), ("agent_name", agent, "Co-host name")):
        if not value or any(ch in value for ch in "\r\n\x00") or len(value) > 60:
            errors[key] = f"{label} must be one line, 1 to 60 characters."
    if len(topics) > _MAX_TEXT or any(ch in topics for ch in "\r\x00"):
        errors["topics"] = f"Keep show topics to {_MAX_TEXT} characters or fewer."
    if voice not in ("captions", "tts"):
        errors["voice"] = "Choose captions only or voice."
    if not target:
        errors["target"] = "Choose a folder path for the new stage home."
    options = StageOptions(channel=channel, host_name=host or "Host", agent_name=agent or "Co-host",
                           voice=voice if voice in ("captions", "tts") else "captions")
    options.topics = topics  # type: ignore[attr-defined]
    return target, options, errors


def plan(answers: dict) -> dict:
    """Validate everything and describe the change; create nothing."""
    target, options, errors = options_from(answers)
    if not errors:
        try:
            validate_options(options)
        except StageHomeError as exc:
            errors["form"] = str(exc)
    path = None
    if "target" not in errors:
        try:
            path = check_target(target)
        except StageHomeError as exc:
            errors["target"] = str(exc)
    if errors or path is None:
        return {"ok": False, "errors": errors or {"target": "Choose a folder path."}}
    files = ["config.yaml", ".env", "SOUL.md", MARKER, "seed_bank.yaml"]
    return {
        "ok": True,
        "target": os.fspath(path),
        "creates": [os.fspath(path / name) for name in sorted(files)],
        "summary": [
            f"Creates a NEW folder {path} (it must not exist yet) holding only On Air's public stage.",
            f"Reads chat from twitch.tv/{options.channel} anonymously (read-only; no login).",
            f"You, {options.host_name}, are the broadcaster. The co-host is called {options.agent_name}.",
            "Captions only (voice off)." if options.voice == "captions"
            else "Voice ON: you still have to set up and test Hermes text-to-speech for this stage.",
            ("Public show topics: " + options.topics) if getattr(options, "topics", "") else "No show topics (optional).",
            "Host turns are OFF by default; enable host_turns in the stage settings to use them. Relay settling defaults to 20 seconds (5–120).",
            "Every line waits for your approval; nothing is armed or started.",
            "Memory, user profile, tools and project context files are OFF for this stage.",
        ],
        "untouched": ["Your current Hermes home, its model, keys, memory and persona are not read, "
                      "copied or changed."],
        "config_preview": render_config(options, os.fspath(path / "seed_bank.yaml")),
    }


def apply(answers: dict, confirm) -> dict:
    if confirm is not True:
        return {"ok": False, "errors": {"confirm": "Tick the confirmation box first; nothing was changed."}}
    checked = plan(answers)
    if not checked["ok"]:
        return checked
    target, options, _ = options_from(answers)
    try:
        created = create_stage_home(target, options)
    except StageHomeError as exc:
        return {"ok": False, "errors": {"target": str(exc)}}
    home = created["home"]
    topics = getattr(options, "topics", "")
    if topics:
        # Appended to the public persona this run just created (exclusive create, same owner).
        with open(home / "SOUL.md", "a", encoding="utf-8") as soul:
            soul.write("\nShow topics (public): " + topics + "\n")
    return {"ok": True, "home": os.fspath(home), "files": created["files"], "readiness": readiness(os.fspath(home))}


class _NotRunning:
    def gate_status(self):
        return False, "not running"


def _env_value(home: Path, name: str) -> str | None:
    try:
        for line in (home / ".env").read_text(encoding="utf-8").splitlines():
            key, sep, value = line.partition("=")
            if sep and key.strip() == name:
                return value.strip()
    except OSError:
        return None
    return None


def readiness(raw_home: str) -> dict:
    """Re-read a marked stage home from disk and report plain-language readiness rows."""
    from .yamlio import YAMLError, safe_load

    from .preflight import run_preflight

    home = Path(os.path.realpath(os.path.expanduser(raw_home or "")))
    if not raw_home or not (home / MARKER).is_file():
        return {"ok": False, "error": "That folder is not an On Air stage home created by setup."}
    try:
        config = safe_load((home / "config.yaml").read_text(encoding="utf-8")) or {}
    except (OSError, *YAMLError):
        return {"ok": False, "error": "The stage home's config.yaml could not be read."}
    settings = (((config.get("plugins") or {}).get("entries") or {}).get("on-air") or {}).get("settings") or {}
    checks = {c["name"]: c for c in run_preflight(config=config, settings=settings, guard=_NotRunning(),
                                                    owner_toolsets=())["checks"]}  # a marked stage home registers none
    allowed_ok = _env_value(home, "ONAIR_ALLOWED_USERS") == "onair-stage"
    h = shell_quote(home)
    rows = []
    # The remaining actions, in the order to do them. Each is one exact command (copyable) or a
    # marked template whose <part> the user must fill in (never offered as copy-ready).
    steps = []

    def row(key, label, state, detail, step=""):
        rows.append({"key": key, "label": label, "state": state, "detail": detail, "step": step})

    def step(key, title, note, command=None, template=None):
        steps.append({"key": key, "title": title, "note": note, "command": command, "template": template})
        return key

    def ok(*names):
        return all(checks.get(n, {}).get("status") == "pass" for n in names)

    plugin_dir = home / "plugins" / "on-air"
    # A folder alone is not an install: require the manifest and the entry module it loads.
    plugin_files = (plugin_dir / "plugin.yaml").is_file() and (plugin_dir / "__init__.py").is_file()
    raw_plugins = config.get("plugins")
    plugins_cfg: dict = raw_plugins if isinstance(raw_plugins, dict) else {}

    def names(value) -> list:
        return value if isinstance(value, list) else []

    plugin_enabled = ("on-air" in names(plugins_cfg.get("enabled"))
                      and "on-air" not in names(plugins_cfg.get("disabled")))
    plugin_step = ""
    if not plugin_files:
        force = " --force" if os.path.lexists(plugin_dir) else ""
        source = _plugin_source()
        bootstrap = (" The first Hermes command in a new folder may download Hermes's own dependencies "
                     "for it (network and disk, once).")
        if source:
            plugin_step = step(
                "plugin", "Install On Air into the stage folder",
                "Installs the committed version of the Git checkout this dashboard runs On Air from ("
                + source[len("file://"):] + ") into the stage folder and turns it on. Hermes warns that "
                "a local file:// source is insecure and not from its catalog; that is expected for your "
                "own copy." + bootstrap,
                command=f"HERMES_HOME={h} hermes plugins install {shell_quote(source)} --enable{force}")
        else:
            plugin_step = step(
                "plugin", "Install On Air into the stage folder",
                "Replace <git-url> with the Git address you installed On Air from (its https:// "
                "repository URL; a local clone works as a file:// URL). This dashboard's copy of On Air "
                "is not a Git checkout, so it cannot fill that in for you." + bootstrap,
                template=[f"HERMES_HOME={h} hermes plugins install ", "<git-url>", f" --enable{force}"])
    elif not plugin_enabled:
        plugin_step = step("plugin", "Turn On Air on in the stage folder",
                           "The plugin files are there but it is not enabled for the stage.",
                           command=f"HERMES_HOME={h} hermes plugins enable on-air")
    model = config.get("model")
    has_model = isinstance(model, dict) and bool(model.get("default") or model.get("provider")) or (
        isinstance(model, str) and bool(model.strip()))
    model_step = step("model", "Check the co-host's model" if has_model else "Choose the co-host's model",
                      "Pick a provider and model for the stage and enter its key there. The stage never "
                      "borrows your personal agent's model or keys.",
                      command=f"HERMES_HOME={h} hermes model")
    voice_on = settings.get("voice") is True
    from .show_runtime import tts_provider
    provider = tts_provider(config)
    voice_step = step("voice", "Set up and test voice",
                      "Voice is ON but not tested: no text-to-speech provider is configured for this stage.",
                      command=f"HERMES_HOME={h} hermes tools") if voice_on and not provider else ""
    preflight_step = step("preflight", "Run the safety check (preflight)",
                          "Checks the saved settings against the installed plugin. Every check must pass. "
                          "It does not connect to Twitch or start anything.",
                          command=f"HERMES_HOME={h} hermes onair preflight")
    gateway_step = step("gateway", "Start the stage",
                        "Starts the stage gateway in the foreground: keep that terminal open. Ctrl+C there "
                        "stops the stage and the show. It starts OFF AIR: nothing is shown until you choose "
                        "Approval on the desk.", command=f"HERMES_HOME={h} hermes gateway run")
    step("desk", "Open the desk for this stage",
         "A desk only sees a stage started from the same folder. Open the On Air tab of the dashboard "
         "this starts (add --port and a free port number if the default one is busy).",
         command=f"HERMES_HOME={h} hermes dashboard --isolated")

    row("settings", "Stage settings saved", "done" if ok("enabled", "context_engine", "auto_tts") else "action",
        f"Saved in {home / 'config.yaml'} and re-read from disk.")
    row("channel", "Twitch channel saved", "done" if ok("channel") else "action",
        f"twitch.tv/{settings.get('twitch_channel', '')}. Saved only: the stage reads this chat anonymously "
        "and read-only (no login) once it is running. Not connected or tested yet.")
    row("isolation", "Private memory, user profile and tools off",
        "done" if ok("memory", "toolsets") and allowed_ok else "action",
        "Memory and user profile disabled, no tools ([no_mcp]), only the stage sender is allowed.")
    row("context", "Public persona only", "done" if ok("context_files") else "action",
        "Project context files are skipped; the co-host loads only the public SOUL.md in this folder.")
    row("seed", "Show content for quiet moments", "done" if ok("seed_bank") else "action",
        "A starter seed bank was written; edit seed_bank.yaml to add your own talking points.")
    row("voice", "Captions / voice", "action" if voice_step else "done",
        (f"Voice is ON ({provider}): configured, not sound-tested by this check: listen to one line before going live."
         if provider else "Voice is ON but not tested: no text-to-speech provider is configured for this stage.")
        if voice_on else "Captions only (voice off). Recommended for a first show.", voice_step)
    try:
        relay_seconds = int(settings.get("host_relay_settle_seconds", 20))
    except (TypeError, ValueError, OverflowError):
        relay_seconds = 20
    relay_seconds = max(5, min(120, relay_seconds))
    host_on = settings.get("host_turns") is True
    row("host_turns", "Host turns / voice relay", "done",
        f"Host turns are {'ON' if host_on else 'OFF'}; relay settling is {relay_seconds} seconds; "
        f"autonomous relays are {'ON' if settings.get('host_relay_autonomous') is True else 'OFF'}. "
        "Edit host_turns, host_relay_settle_seconds or host_relay_autonomous in the stage settings and restart the stage.")
    if plugin_files and plugin_enabled:
        row("plugin", "On Air plugin in the stage", "done",
            "Plugin files (plugin.yaml and its code) found and enabled in the stage settings. "
            "Whether it loads is checked by the safety check and when the stage starts.")
    elif plugin_files:
        row("plugin", "On Air plugin in the stage", "action",
            "Plugin files found, but On Air is not enabled for the stage.", plugin_step)
    elif os.path.lexists(plugin_dir):
        row("plugin", "On Air plugin in the stage", "action",
            "A plugins/on-air folder exists but is missing plugin.yaml or its code, so it is not a usable "
            "install. Reinstall it (the command replaces that folder).", plugin_step)
    else:
        row("plugin", "On Air plugin in the stage", "action",
            "Not installed in the new stage folder yet. This dashboard cannot install plugins into "
            "another Hermes folder.", plugin_step)
    row("model", "Co-host brain (model)", "untested" if has_model else "action",
        "A model is chosen but has not been tested." if has_model
        else "No model chosen yet. The stage never borrows your personal agent's model or keys.", model_step)
    row("preflight", "Safety check (preflight)", "untested",
        "Not run yet. It checks the saved settings in this folder; it does not connect to anything.",
        preflight_step)
    row("runtime", "Stage running and reading chat", "untested",
        "Not started, so nothing reads Twitch chat or shows captions yet. This page cannot see a running "
        "stage; the desk for this stage shows it once started.", gateway_step)
    row("overlay", "Overlay port saved", "done" if ok("overlay_port") else "action",
        f"Port {settings.get('overlay_port', 18790)} saved for the overlay (optional OBS browser source). "
        "It serves pages only while the stage is running.")
    row("practice", "Practice show", "done", "Ready to practise now: it needs no account, model or Twitch.")
    remaining = [r for r in rows if r["state"] != "done"]
    for number, item in enumerate(steps, 1):
        item["number"] = number
    numbers = {item["key"]: item["number"] for item in steps}
    for item in rows:
        item["step"] = numbers.get(item["step"]) if item["step"] else None
    return {"ok": True, "home": os.fspath(home), "rows": rows, "steps": steps,
            "command_note": ("These commands start with HERMES_HOME=<stage folder> because the wizard made "
                             "the stage as its own folder, not as a named Hermes profile. Elsewhere on the "
                             "desk, \"hermes -p stage …\" is the same command for a stage made as a "
                             "profile called stage."),
            "summary": ("Settings saved. Ready to practise. Not ready for a real show yet: "
                        + str(len(steps)) + " step" + ("s" if len(steps) != 1 else "") + " left, in order below.")
            if remaining else "Settings saved and every check passed."}


# The checkout this copy of On Air runs from (tests point it elsewhere).
_SOURCE_ROOT = Path(__file__).resolve().parents[1]


def _plugin_source() -> str | None:
    """A ``file://`` source ``hermes plugins install`` can clone for this very copy, or None.

    Hermes installs plugins by Git clone, so only a Git checkout that holds the plugin manifest
    is offered as an exact command; anything else becomes a marked fill-in template."""
    root = _SOURCE_ROOT
    if (root / ".git").exists() and (root / "plugin.yaml").is_file():
        return "file://" + os.fspath(root)
    return None


def suggested_target() -> str:
    base = Path.home() / "onair-stage"
    candidate, n = base, 2
    while os.path.lexists(candidate) and n < 100:
        candidate, n = Path(f"{base}-{n}"), n + 1
    return os.fspath(candidate)

