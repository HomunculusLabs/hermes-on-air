"""Operator CLI for On Air; registered by the plugin via setup_parser and handle."""
from __future__ import annotations

import argparse
import http.client
import json
import os
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit


def setup_parser(parser: argparse.ArgumentParser) -> None:
    sub = parser.add_subparsers(dest="onair_command", required=True)
    setup = sub.add_parser(
        "setup", help="Print setup instructions and preflight the current home, or create a new stage home",
        description="Without --create-home: print the settings to merge and preflight the CURRENT Hermes "
                    "home (nothing is written). With --create-home DIR: write a new, separate stage home "
                    "at DIR (which must not exist) and touch nothing else.")
    setup.add_argument("--channel")
    setup.add_argument("--host-name")
    setup.add_argument("--seed-bank", help="Seed-bank file (with --create-home: optional absolute path; "
                       "default writes a starter seed bank into the new home)")
    setup.add_argument("--profanity", choices=("allow", "mask", "block"))
    setup.add_argument("--yes", action="store_true", help="Do not prompt")
    setup.add_argument("--create-home", metavar="DIR",
                       help="Create a NEW dedicated stage HERMES_HOME at DIR (must not exist yet)")
    setup.add_argument("--agent-name", help="With --create-home: the co-host's public name (default Co-host)")
    setup.add_argument("--voice", choices=("captions", "tts"),
                       help="With --create-home: captions only (default) or Hermes text_to_speech")
    setup.add_argument("--overlay-port", type=int, help="With --create-home: loopback overlay port (default 18790)")
    setup.add_argument("--never-say", action="append", default=[],
                       help="With --create-home: a word or phrase the host must never say (repeatable)")
    scene = sub.add_parser('scene', help='Create the optional owner 3D scene')
    scene.add_argument('scene_action', nargs='?', choices=('create', 'preview'), default='create')
    scene.add_argument('--force', action='store_true', help='Replace the existing scene (back up owner files first)')
    preflight = sub.add_parser("preflight", help="Check stage isolation of the current Hermes home")
    preflight.add_argument("--json", action="store_true")
    rehearse = sub.add_parser("rehearse", help="Synthetic offline rehearsal: no Twitch, model, TTS or network")
    rehearse.add_argument("--duration", type=float, default=30.0,
                          help="Upper bound in seconds (1-300, default 30)")
    sub.add_parser("status", help="Read the local desk state")
    host = sub.add_parser("host", help="Queue a host line on the current stage")
    host.add_argument("text")
    line = sub.add_parser("check-line", help="Check one line with the output guard")
    line.add_argument("line", nargs="?")
    line.add_argument("--never-say", action="append", default=[])
    line.add_argument("--host-name", default="Host")
    line.add_argument("--protected-name", action="append", default=[])
    line.add_argument("--armed", action="store_true")
    line.add_argument("--profanity", choices=("allow", "mask", "block"), default="mask")
    line.add_argument("--chat", action="append", default=[], help="Classify viewer chat with the inbound gate")
    report = sub.add_parser("report", help="Print recorded show receipts from disk")
    report.add_argument("--show", nargs="?", const="latest", help="Summarize a session, or the latest show")


def _home() -> Path:
    return Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes").expanduser()


def _data_dir() -> Path:
    return _home() / "plugin-data" / "on-air"


_NEXT_ACTIONS = {
    'enabled': 'set gateway.platforms.on_air.enabled: true in this home',
    'toolsets': 'set platform_toolsets.on_air: [no_mcp]',
    'owner_tools': 'run the stage from a separate stage home, not from the home where you use On Air\'s own tools',
    'allowed_users': "put ONAIR_ALLOWED_USERS=onair-stage in this home's .env",
    'memory': 'set memory.memory_enabled: false, memory.user_profile_enabled: false, memory.provider: null',
    'seed_bank': 'point plugins.entries.on-air.settings.seed_bank at a seed bank with at least one beat',
    'channel': 'set plugins.entries.on-air.settings.twitch_channel (letters, digits, underscores)',
    'overlay_port': 'free the overlay port or set plugins.entries.on-air.settings.overlay_port',
    'agent_runtime': 'remove codex_app_server from the model/provider settings',
    'context_engine': 'set context.engine: compressor',
    'auto_tts': 'set voice.auto_tts: false',
    'runtime_gate': 'install and enable the On Air plugin in this home (hermes plugins enable on-air)',
}


def _fail(message: str) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(1)


def _preflight(*, as_json: bool = False) -> None:
    try:
        from .preflight import run_preflight
        result = run_preflight()
        if not isinstance(result, dict) or not isinstance(result.get("ok"), bool) or not isinstance(result.get("checks"), list):
            raise ValueError("invalid preflight result")
        for check in result["checks"]:
            if not isinstance(check, dict) or any(not isinstance(check.get(k), str) for k in ("name", "status", "detail")):
                raise ValueError("invalid preflight check")
    except Exception as exc:
        if as_json:
            print(json.dumps({"ok": False, "checks": [{"name": "preflight", "status": "fail", "detail": "Preflight unavailable or failed."}]}))
        _fail(f"Preflight unavailable or failed ({type(exc).__name__}); not ready to arm.")
    if as_json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(f"Preflight of the CURRENT Hermes home: {_home()}")
        for check in result["checks"]:
            print(f"{check['name']}: {check['status']} - {check['detail']}")
    if not result["ok"]:
        if not as_json:
            failed = [c['name'] for c in result['checks'] if c['status'] == 'fail']
            print("\nNext actions:", file=sys.stderr)
            for name in failed:
                print(f"- {name}: {_NEXT_ACTIONS.get(name, 'see the detail above')}", file=sys.stderr)
            print("- or create a separate stage home with these settings: "
                  "hermes onair setup --create-home <new directory> --channel <channel>", file=sys.stderr)
            print("- to see the show path with no account or network: hermes onair rehearse", file=sys.stderr)
        _fail("Preflight failed; not ready to arm.")
    if not as_json:
        print("Preflight passed.")
        print("This checks stage isolation only. It does not verify a model provider or credentials,")
        print("the Twitch channel, or voice; nothing was armed. Arm into approval from the desk when ready.")


def _setup(args: argparse.Namespace) -> None:
    def value(name: str, label: str, default: str = "") -> str:
        current = getattr(args, name)
        if current is None and not args.yes and sys.stdin.isatty():
            try:
                current = input(f"{label}{' [' + default + ']' if default else ''}: ").strip() or default
            except (EOFError, KeyboardInterrupt):
                _fail("Setup cancelled; no configuration was written.")
        if current is None:
            current = default
        if not current or not current.strip():
            _fail(f"Setup requires --{name.replace('_', '-')} (or an interactive terminal).")
        return current.strip()

    if args.create_home is not None:
        _create_home(args, value)
        return
    for flag in ('agent_name', 'voice', 'overlay_port'):
        if getattr(args, flag) is not None:
            _fail(f"--{flag.replace('_', '-')} applies only with --create-home; nothing was written.")
    if args.never_say:
        _fail("--never-say applies only with --create-home; nothing was written.")
    channel = value('channel', 'Twitch channel')
    host = value('host_name', 'Host name', 'Host')
    seed = value('seed_bank', 'Seed bank path')
    profanity = value('profanity', 'Profanity policy (mask/block/allow)', 'mask')
    if profanity not in ('mask', 'block', 'allow'):
        _fail('Profanity policy must be mask, block, or allow.')
    # JSON strings are valid YAML scalars, including for quotes and newlines.
    channel, host, seed = (json.dumps(item, ensure_ascii=False) for item in (channel, host, seed))
    print(f'''Add the following to Hermes config.yaml (merge with existing settings):
plugins:
  enabled: [on-air]
  entries:
    on-air:
      settings:
        twitch_channel: {channel}
        host_name: {host}
        agent_names: [CoHostName]  # replace with the agent's protected names
        seed_bank: {seed}
        never_say: ["phrase one", "phrase two"]  # replace with your own terms
        profanity: {profanity}
        dead_air_seconds: 45
        alerts:
          enabled: true
          thank_you: true
          include_messages: false
          min_bits: 1
          during_privacy: drop
gateway:
  platforms:
    on_air:
      enabled: true
      skip_context_files: true  # no AGENTS.md/CLAUDE.md/.cursorrules or skills; SOUL.md still loads
platform_toolsets:
  on_air: [no_mcp]
memory:
  memory_enabled: false
  user_profile_enabled: false
  provider: null
context:
  engine: compressor
voice:
  auto_tts: false
onboarding:
  profile_build: "off"  # Hermes never offers to build a profile of the public sender
# model: keep a chat-completions, Anthropic, Bedrock or Responses provider; never set
# openai_runtime: codex_app_server (its turns bypass the runtime gate; preflight fails).

Set this environment variable for the gateway:
ONAIR_ALLOWED_USERS=onair-stage

Use a dedicated stage home: no other LLM middleware plugins, and no private text in its SOUL.md.
The home where your own agent uses On Air's setup and presentation tools cannot run the stage as well;
preflight refuses it (owner_tools), because the co-host must never be offered tools.
No configuration was written. Preflight below checks the current configuration, not the
suggested settings. To write these settings into a NEW, separate stage home instead, run
`hermes onair setup --create-home <new directory> --channel <channel>`.
''')
    _preflight()


def _create_home(args: argparse.Namespace, value) -> None:
    from .onboarding import StageHomeError, StageOptions, check_target, create_stage_home, next_steps
    try:
        check_target(args.create_home)  # refuse before prompting
    except StageHomeError as exc:
        _fail(str(exc))
    channel = value('channel', 'Twitch channel (read-only, anonymous)')
    host = value('host_name', 'Host name', 'Host')
    agent = value('agent_name', 'Co-host public name', 'Co-host')
    profanity = value('profanity', 'Profanity policy (mask/block/allow)', 'mask')
    voice = value('voice', 'Voice (captions/tts)', 'captions')
    options = StageOptions(channel=channel, host_name=host, agent_name=agent, profanity=profanity,
                           seed_bank=args.seed_bank, voice=voice,
                           overlay_port=18790 if args.overlay_port is None else args.overlay_port,
                           never_say=[term.strip() for term in args.never_say])
    try:
        created = create_stage_home(args.create_home, options)
    except StageHomeError as exc:
        _fail(str(exc))
    except KeyboardInterrupt as exc:  # Ctrl-C, or SIGTERM/SIGHUP while writing (onboarding.Stopped)
        partial = getattr(exc, "onair_partial_home", "none")
        if partial == "removed":
            _fail("Setup cancelled; the partial stage home was removed.")
        if partial == "left":
            _fail(f"Setup cancelled; {args.create_home} was NOT removed because it is no longer the "
                  "directory this run created. Check it and remove it yourself if it is yours.")
        _fail("Setup cancelled; no stage home was created.")
    home = created['home']
    print(f"Created a NEW On Air stage home: {home}")
    for name in created['files']:
        print(f"  {name}")
    print(f"Seed bank: {created['seed_bank']}")
    print("Nothing was read from or written to the current Hermes home; no model, key, auth, memory")
    print("or persona was copied. The new home has no model and no On Air plugin installed yet.")
    print("Preflight was NOT run: it checks the Hermes home it runs in, so run it with HERMES_HOME")
    print("set to the new home (step 4).\n")
    print(next_steps(home, voice), end='')


def _rehearse(args: argparse.Namespace) -> None:
    from .onboarding import Stopped
    from .rehearsal import rehearse
    try:
        rehearse(duration=args.duration)
    except ValueError as exc:
        _fail(f"Rehearsal refused: {exc}.")
    except Stopped as exc:
        print(f"Rehearsal stopped by signal {exc.signum}; the stage was shut down and nothing was "
              "written to disk.", file=sys.stderr)
        raise SystemExit(128 + exc.signum) from None
    except KeyboardInterrupt:
        _fail("Rehearsal cancelled; the stage was shut down and nothing was written to disk.")


def _status() -> None:
    directory = _data_dir()
    try:
        discovery = json.loads((directory / "control.json").read_text(encoding="utf-8"))
        if not isinstance(discovery, dict) or discovery.get("token_file") != "desk.token":
            raise ValueError("invalid discovery")
        url = urlsplit(discovery["url"])
        # The control server contract is numeric IPv4 loopback, HTTP, origin only.
        if (url.scheme != "http" or url.hostname != "127.0.0.1" or
                url.username is not None or url.password is not None or
                url.path not in ("", "/") or url.query or url.fragment or
                not url.port or not 1 <= url.port <= 65535):
            raise ValueError("invalid control URL")
        token_path = directory / "desk.token"
        if token_path.is_symlink():
            raise ValueError("invalid desk token path")
        token = token_path.read_text(encoding="utf-8").strip()
        if not token or "\n" in token or "\r" in token:
            raise ValueError("invalid desk token")
        conn = http.client.HTTPConnection("127.0.0.1", url.port, timeout=3)
        try:
            conn.request("GET", "/state", headers={"Authorization": "Bearer " + token})
            response = conn.getresponse()
            if response.status != 200:
                raise ValueError("control request failed")
            raw = response.read(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024:
                raise ValueError("control response too large")
            state = json.loads(raw)
            if not isinstance(state, dict):
                raise ValueError("invalid control state")
        finally:
            conn.close()
    except (OSError, ValueError, KeyError, TypeError, http.client.HTTPException):
        _fail("On Air status unavailable: control discovery or loopback request failed.")
    print(json.dumps(state, ensure_ascii=False, indent=2))


def _host(text: str) -> bool:
    directory = _data_dir()
    try:
        discovery = json.loads((directory / "control.json").read_text(encoding="utf-8"))
        if not isinstance(discovery, dict) or discovery.get("token_file") != "desk.token":
            raise ValueError("invalid discovery")
        url = urlsplit(discovery["url"])
        if (url.scheme != "http" or url.hostname != "127.0.0.1" or url.username is not None
                or url.password is not None or url.path not in ("", "/") or url.query or url.fragment
                or not url.port or not 1 <= url.port <= 65535):
            raise ValueError("invalid control URL")
        token_path = directory / "desk.token"
        if token_path.is_symlink():
            raise ValueError("invalid desk token path")
        token = token_path.read_text(encoding="utf-8").strip()
        if not token or "\n" in token or "\r" in token:
            raise ValueError("invalid desk token")
        conn = http.client.HTTPConnection("127.0.0.1", url.port, timeout=3)
        try:
            headers = {"Authorization": "Bearer " + token}
            conn.request("GET", "/state", headers=headers)
            response = conn.getresponse()
            raw = response.read(1024 * 1024 + 1)
            if response.status != 200 or len(raw) > 1024 * 1024:
                raise ValueError("invalid state response")
            state = json.loads(raw)
            if not isinstance(state, dict) or type(state.get("epoch")) is not int:
                raise ValueError("invalid state")
            payload = json.dumps({"text": text, "epoch": state["epoch"]}).encode()
            conn.request("POST", "/host/turn", body=payload,
                         headers={**headers, "Content-Type": "application/json"})
            response = conn.getresponse()
            raw = response.read(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024:
                raise ValueError("invalid host response")
            result = json.loads(raw)
            if not isinstance(result, dict) or type(result.get("ok")) is not bool:
                raise ValueError("invalid host response")
        finally:
            conn.close()
    except (OSError, ValueError, KeyError, TypeError, http.client.HTTPException):
        _fail("Host turn unavailable: control discovery or loopback request failed.")
    print(json.dumps(result, ensure_ascii=False))
    return result["ok"]


def _check_line(args: argparse.Namespace) -> None:
    from .guard import check_line
    from .types import ChatMsg, GuardContext
    if args.chat:
        from .chat.gate import GateConfig, classify
        msg = ChatMsg(platform='cli', channel='', msg_id='1', user_id='viewer',
                      display_name='viewer', text=args.chat[-1], ts=0)
        verdict = classify(msg, [], config=GateConfig(host_name=args.host_name, never_say=tuple(args.never_say), protected_names=tuple(args.protected_name)))
        print(f"{verdict.kind.upper()}: {verdict.rule} - {verdict.reason}")
        return
    if args.line is None:
        _fail('Provide a line or --chat text.')
    ctx = GuardContext(armed=args.armed, never_say=tuple(args.never_say),
                       protected_names=tuple(args.protected_name), host_name=args.host_name)
    verdict = check_line(args.line, ctx, profanity=args.profanity)
    print(f"{'ALLOW' if verdict.release else 'WITHHOLD'}: {verdict.rule} - {verdict.reason}")
    if not verdict.release:
        raise SystemExit(1)


def _report(show: str | None = None) -> None:
    show = show or 'latest'
    directory = _data_dir() / "receipts"
    try:
        paths = sorted(directory.glob("*.jsonl")) if directory.is_dir() else []
        records = []
        for path in paths:
            with path.open(encoding="utf-8") as stream:
                for number, text in enumerate(stream, 1):
                    if not text.strip():
                        continue
                    try:
                        record = json.loads(text)
                        if not isinstance(record, dict):
                            raise ValueError("not an object")
                    except ValueError:
                        _fail(f"Invalid receipt in {path.name} at line {number}.")
                    records.append(record)
        if not records:
            print("No receipts found.")
            return
        if show is None:
            for record in records:
                print(json.dumps(record, ensure_ascii=False))
            return
        shows: list[tuple[str, list[dict]]] = []
        active = None
        for record in records:
            kind = record.get('kind')
            if kind in ('boot', 'shutdown'):
                active = None
            if (kind == 'control' and record.get('action') == 'mode' and
                    record.get('old') == 'off_air' and record.get('new') == 'approval' and
                    isinstance(record.get('session'), str) and record['session']):
                active = []
                shows.append((record['session'], active))
            if active is not None:
                active.append(record)
            if (kind == 'control' and record.get('action') == 'mode' and
                    record.get('new') == 'off_air'):
                active = None
        matches = shows[-1:] if show == 'latest' else [entry for entry in shows if entry[0] == show]
        if not matches:
            _fail('No matching show found in receipts.')
        session, entries = matches[-1]
        print(f"# On Air show: {json.dumps(session, ensure_ascii=False)}")
        print(f"\nReceipts: {len(entries)}")
        aired_ids = {r.get('line_id') for r in entries if r.get('kind') == 'aired'}
        aired_turns = {r.get('turn_id') for r in entries if r.get('kind') == 'line' and r.get('line_id') in aired_ids and r.get('turn_id')}
        question_ids = {r.get('msg_id') for r in entries if r.get('kind') == 'chat' and r.get('rule') == 'question' and r.get('msg_id')}
        answered_ids = set()
        for record in entries:
            if record.get('kind') == 'turn' and record.get('turn_kind') == 'chat' and record.get('turn_id') in aired_turns:
                answered_ids.update(question_ids.intersection(record.get('chat') or []))
        print(f"\nQuestions in turns with an aired reply: {len(answered_ids)}")
        print(f"Approvals: {sum(r.get('kind') == 'control' and r.get('action') == 'approve' for r in entries)}")
        print(f"Rejections: {sum(r.get('kind') == 'control' and r.get('action') == 'reject' for r in entries)}")
        print(f"Alerts: {sum(r.get('kind') == 'alert' for r in entries)}")
        print(f"Thank-you turns: {sum(r.get('kind') == 'turn' and r.get('turn_kind') == 'support' for r in entries)}")
        print(f"Privacy cuts: {sum(r.get('kind') == 'control' and r.get('action') == 'privacy' and r.get('on') is True for r in entries)}")
        beats = [str(r.get('beat')) for r in entries if r.get('kind') == 'turn' and r.get('turn_kind') == 'beat' and r.get('beat')]
        print(f"Beats used: {len(beats)}")
        for beat in beats:
            print(f"- {json.dumps(beat, ensure_ascii=False)}")
        for title, field, predicate in (
            ('Events', 'kind', lambda r: True),
            ('Chat verdicts', 'verdict', lambda r: r.get('kind') == 'chat'),
            ('Line dispositions', 'disposition', lambda r: r.get('kind') == 'line'),
            ('Lines withheld by guard rule', 'rule', lambda r: r.get('kind') == 'line' and r.get('disposition') == 'withheld'),
            ('Chat skipped by gate rule', 'rule', lambda r: r.get('kind') == 'chat' and r.get('verdict') in {'skip', 'bait', 'spam'}),
        ):
            counts = Counter(str(r[field]) for r in entries if field in r and predicate(r))
            if counts:
                print(f"\n## {title}")
                for name, count in sorted(counts.items()):
                    print(f"- {json.dumps(name, ensure_ascii=False)}: {count}")
    except (OSError, UnicodeError):
        _fail("Could not read On Air receipts.")


def handle(args: argparse.Namespace) -> None:
    command = args.onair_command
    if command == "scene":
        dest = _data_dir() / 'scene'
        if args.scene_action == 'preview':
            if args.force:
                _fail('--force applies only when creating a scene.')
            import asyncio
            from .scene_preview import run
            try:
                asyncio.run(run(dest))
            except ValueError as exc:
                _fail(str(exc))
            except KeyboardInterrupt:
                print('Preview closed.')
            return
        from .scene_scaffold import SceneError, scaffold
        try:
            scaffold(dest, force=args.force, home=_home())
        except (SceneError, OSError) as exc:
            _fail(f'Scene setup failed: {exc}')
        print(f'Created owner scene at {dest}. Open /scene/ on this home’s overlay port.')
    elif command == "setup":
        _setup(args)
    elif command == "preflight":
        _preflight(as_json=args.json)
    elif command == "status":
        _status()
    elif command == "host":
        if not _host(args.text):
            raise SystemExit(1)
    elif command == "rehearse":
        _rehearse(args)
    elif command == "check-line":
        _check_line(args)
    elif command == "report":
        _report(args.show)
    else:
        _fail("Unknown On Air command.")
