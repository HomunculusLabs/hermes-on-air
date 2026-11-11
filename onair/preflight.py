"""Read-only, fail-closed checks for an On Air configuration.

Static checks read the effective Hermes config through ``load_config_readonly``. ``runtime_gate``
checks that On Air's ``llm_execution`` gate is registered and active in *this* process (the
gateway's, at adapter connect; the CLI's own plugin load for ``hermes onair preflight``). A pass
is not proof that arbitrary persona text or other plugins are safe: ``persona`` stays a warning.
``agent_runtime`` is a config-level check: a ``codex_app_server`` runtime runs the whole turn, tools
included, in a Codex subprocess that never reaches ``llm_execution``, so it refuses to arm (the
adapter would also withhold such a reply, but tools that already ran cannot be undone).
``context_files`` passes only when the platform skips project context files; SOUL.md loads anyway.
The overlay bind probe is inherently racy; the server still has to bind the port itself.
``owner_tools`` refuses a home where this plugin registered its owner tools (setup, presentation): Hermes
offers a plugin's toolsets to every platform unless they are recorded as known and left unselected for it,
so the co-host's requests would carry them and the runtime gate would refuse every reply. A stage home
registers none.
"""
from __future__ import annotations

import os
import socket
from collections.abc import Mapping
from typing import Any


# Toolsets of owner tools registered in THIS process (empty in a stage home, where none are registered).
_OWNER_TOOLSETS: set[str] = set()


def note_owner_toolset(name: str) -> None:
    """Record that this process registered owner tools under ``name`` (called at registration)."""
    _OWNER_TOOLSETS.add(name)


def _offered_owner_toolsets(config: Mapping, registered) -> list[str]:
    """Registered owner toolsets Hermes would offer to the On Air platform under this config."""
    known = config.get('known_plugin_toolsets')
    known = known.get('on_air') if isinstance(known, Mapping) else None
    known = [x for x in known if isinstance(x, str)] if isinstance(known, (list, tuple)) else []
    return sorted(str(name) for name in registered if name not in known)


def _section(obj: Mapping, key: str) -> Mapping:
    value = obj.get(key, {})
    if not isinstance(value, Mapping):
        raise ValueError('invalid configuration section')
    return value


def _scoped(name: str) -> str | None:
    from .secrets import get
    return get(name)


_RUNTIME_KEYS = ('openai_runtime', 'api_mode')


def _codex_app_server_sections(config: Any, *, _top: str | None = None, _depth: int = 0) -> set[str]:
    """Top-level config sections that select the ``codex_app_server`` runtime anywhere inside
    (``model.openai_runtime``, ``model.api_mode``, a provider entry's ``api_mode``). Only section
    names are returned, never values."""
    if _depth > 32:
        raise ValueError('configuration too deep')
    found: set[str] = set()
    items = config.items() if isinstance(config, Mapping) else enumerate(config) if isinstance(config, list) else ()
    for key, value in items:
        top = _top if _top is not None else str(key)
        if (isinstance(key, str) and key.casefold() in _RUNTIME_KEYS and isinstance(value, str)
                and value.strip().lower().replace('-', '_') == 'codex_app_server'):
            found.add(top)
        elif isinstance(value, (Mapping, list)):
            found |= _codex_app_server_sections(value, _top=top, _depth=_depth + 1)
    return found


def run_preflight(config=None, settings=None, *, port_in_use_by_self=False, guard=None, owner_toolsets=None) -> dict:
    """Return {ok, checks}; checks have name, status (pass/fail/warn), and detail.

    ``owner_toolsets`` names the owner toolsets registered in the home being checked. It defaults to this
    process's own registrations; pass ``()`` when checking another home that is a marked stage home.

    ``settings`` is the adapter's raw settings mapping. If omitted, values are
    resolved from platform extra, plugin settings, environment, then defaults.
    ``port_in_use_by_self`` must only be set by the adapter that owns the listener.
    ``guard`` defaults to the plugin-owned runtime gate (``runtime_guard.plugin_guard()``).
    """
    checks: list[dict[str, str]] = []

    def add(name: str, good: bool, success: str, failure: str) -> None:
        checks.append({'name': name, 'status': 'pass' if good else 'fail',
                       'detail': success if good else failure})

    def warn(name: str, detail: str) -> None:
        checks.append({'name': name, 'status': 'warn', 'detail': detail})

    def result() -> dict:
        return {'ok': not any(c['status'] == 'fail' for c in checks), 'checks': checks}

    try:
        if config is None:
            from hermes_cli.config import FailedConfigRead, load_config_readonly
            config = load_config_readonly()
            if isinstance(config, FailedConfigRead):
                raise ValueError('configuration read failed')
        if not isinstance(config, Mapping):
            raise ValueError('invalid configuration')
        gateway = _section(config, 'gateway')
        platform = _section(_section(gateway, 'platforms'), 'on_air')
        plugin = _section(_section(_section(config, 'plugins'), 'entries'), 'on-air')
        plugin_settings = _section(plugin, 'settings')
        extra = _section(platform, 'extra')
        memory = _section(config, 'memory')
        context = _section(config, 'context')
        voice = _section(config, 'voice')
        toolsets = _section(config, 'platform_toolsets')
        if settings is not None and not isinstance(settings, Mapping):
            raise ValueError('invalid settings')

        def setting(key: str, default: Any) -> Any:
            sources = (settings, plugin_settings) if settings is not None else (extra, plugin_settings)
            for source in sources:
                value = source.get(key)
                if value is not None and value != '':
                    return value
            return _scoped('ONAIR_' + key.upper()) or default

        add('enabled', platform.get('enabled') is True,
            'On Air platform is enabled.', 'Enable gateway.platforms.on_air.')
        add('trusted_inbound', platform.get('trusted_inbound', False) is False and extra.get('trusted_inbound', False) is False,
            'Trusted inbound is not enabled in platform configuration.', 'Trusted inbound must be disabled.')
        add('toolsets', toolsets.get('on_air') == ['no_mcp'],
            'On Air explicitly uses [no_mcp].', 'Set platform_toolsets.on_air to exactly [no_mcp].')
        offered = _offered_owner_toolsets(config, _OWNER_TOOLSETS if owner_toolsets is None else owner_toolsets)
        add('owner_tools', not offered,
            "On Air's owner tools are not offered to the co-host in this home.",
            "This is your own agent's home: On Air's owner tools (" + ', '.join(offered) + ') are registered '
            'here and Hermes would offer them to the co-host, so the runtime gate would refuse every reply. '
            'Run the stage from a separate stage home: '
            'hermes onair setup --create-home <new directory> --channel <channel>.')
        codex = sorted(_codex_app_server_sections(config))
        add('agent_runtime', not codex,
            'No codex_app_server runtime is configured (config-level check): turns run in the Hermes '
            'agent loop, where the runtime gate applies.',
            'codex_app_server is selected (under: ' + ', '.join(codex) + '): the whole turn runs in a '
            'Codex subprocess with its own tools, outside the runtime gate. Remove '
            'model.openai_runtime: codex_app_server / api_mode: codex_app_server for On Air.')
        add('context_engine', context.get('engine', 'compressor') == 'compressor',
            'The configured context engine is compressor.',
            'A custom context engine can add tools or private context; use compressor.')
        allowed = _scoped('ONAIR_ALLOWED_USERS')
        add('allowed_users', isinstance(allowed, str) and allowed.strip() == 'onair-stage',
            'Only the synthetic stage sender is allowed.', 'ONAIR_ALLOWED_USERS must be exactly onair-stage.')
        add('memory', memory.get('memory_enabled') is False and memory.get('user_profile_enabled') is False
            and memory.get('provider') in (None, ''),
            'Built-in memory and user profile are disabled and no provider is configured.',
            'Disable both built-in memory stores and remove the memory provider.')
        add('auto_tts', voice.get('auto_tts', False) is False,
            'Gateway auto TTS is off in configuration.', 'Disable voice.auto_tts before going on air.')

        from .beats import SeedBank
        seed = setting('seed_bank', '')
        bank = SeedBank(os.fspath(seed)) if isinstance(seed, (str, os.PathLike)) and seed else None
        add('seed_bank', bank is not None and bank.error is None and bool(bank.beats()),
            'Seed bank parses and contains beats.', 'Provide a valid seed bank containing at least one beat.')

        never = setting('never_say', ())
        if isinstance(never, str):
            never = never.split(',')
        valid_never = isinstance(never, (list, tuple)) and all(isinstance(x, str) and bool(x.strip()) for x in never)
        add('never_say', valid_never, 'Never-say terms are valid.', 'Never-say must contain non-empty strings.')
        profanity = setting('profanity', 'mask')
        add('profanity', isinstance(profanity, str) and profanity in ('allow', 'mask', 'block'),
            'Profanity policy is valid.', 'Profanity policy must be allow, mask, or block.')
        from .sources.twitch_irc import normalize_channel
        channel = setting('twitch_channel', '')
        try:
            if not isinstance(channel, str):
                raise ValueError('invalid channel')
            normalize_channel(channel)
            channel_ok = True
        except ValueError:
            channel_ok = False
        add('channel', channel_ok, 'Twitch channel is valid.', 'Provide a valid Twitch channel name.')

        # Both listeners use the same loopback bind semantics. Keep sockets open together so
        # two explicitly configured listeners cannot both claim the same port.
        held = []
        try:
            for key, default, label in (('overlay_port', 18790, 'Overlay'), ('control_port', 0, 'Desk')):
                port = setting(key, default)
                shown = 'setting'
                try:
                    if isinstance(port, bool) or not isinstance(port, (int, str)):
                        raise ValueError('invalid port')
                    if isinstance(port, str) and not port.isascii():
                        raise ValueError('invalid port')
                    port = int(port)
                    shown = str(port)
                    if not 0 <= port <= 65535 or not isinstance(port_in_use_by_self, bool):
                        raise ValueError('invalid port')
                    if not port_in_use_by_self:
                        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        held.append(sock)
                        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                        sock.bind(('127.0.0.1', port))
                        sock.listen(1)
                        if port == 0:  # ephemeral listeners do not compete for a configured port
                            held.pop().close()
                    detail = ''
                    good = True
                except (ValueError, TypeError, OSError, OverflowError):
                    good = False
                    try:
                        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as suggestion:
                            suggestion.bind(('127.0.0.1', 0))
                            free = suggestion.getsockname()[1]
                        action = f'Set {key} to a free port, for example {free}, in the stage settings.'
                    except OSError:
                        action = f'Set {key} to a free loopback port in the stage settings.'
                    detail = f'{label} port {shown} is invalid or unavailable. {action}'
                add(key, good, f'{label} port is available or owned by this adapter.', detail)
        finally:
            for sock in held:
                sock.close()

        tts = setting('voice', True)
        if tts is False or (isinstance(tts, str) and tts.strip().lower() in ('0', 'false', 'no', 'off')):
            add('tts', True, 'On Air voice is disabled (captions only).', '')
        elif tts is True or (isinstance(tts, str) and tts.strip().lower() in ('1', 'true', 'yes', 'on')):
            warn('tts', 'TTS availability and audio routing are not verified; test voice before the show.')
        else:
            add('tts', False, '', 'Voice must be a boolean setting.')

        if guard is None:
            from .runtime_guard import plugin_guard
            guard = plugin_guard()
        gate_ok, gate_detail = guard.gate_status()
        if not isinstance(gate_ok, bool) or not isinstance(gate_detail, str):
            raise ValueError('invalid gate status')
        checks.append({'name': 'runtime_gate', 'status': 'pass' if gate_ok else 'fail', 'detail': gate_detail})
        add('runtime_tools', gate_ok,
            'Any tool schema in an On Air provider request is refused at runtime and latches OFF AIR.',
            'Tool isolation needs the runtime gate; see runtime_gate.')
        add('voice_modes', True,
            'Fresh per-show chat ids and no command text is ever dispatched; gateway auto-TTS is '
            'overridden off for On Air.', '')
        if platform.get('skip_context_files') is True:
            checks.append({'name': 'context_files', 'status': 'pass', 'detail':
                           'gateway.platforms.on_air.skip_context_files is on: project context files '
                           '(.hermes.md, AGENTS.md, CLAUDE.md, .cursorrules), auto-loaded skills and '
                           'subdirectory hints are skipped; SOUL.md still loads.'})
        else:
            warn('context_files', 'gateway.platforms.on_air.skip_context_files is not true: project context '
                 'files, auto-loaded skills and subdirectory hints can reach the stage prompt; set '
                 'skip_context_files: true or use a dedicated stage profile.')
        warn('persona', 'SOUL.md still loads for gateway sessions (skip_context_files does not skip it), '
             'and persona files and custom prompts cannot be inspected: keep private text out of '
             "the stage profile's SOUL.md and run On Air in a dedicated stage profile with no other "
             'LLM middleware plugins.')
    except Exception:
        # Do not expose config values, file contents, exception messages, or secrets.
        add('preflight', False, '', 'Preflight could not safely inspect the configuration.')
    return result()
