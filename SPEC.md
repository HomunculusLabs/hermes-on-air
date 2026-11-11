# On Air: design specification

**Goal:** a Hermes plugin that lets an agent co-host a live stream with explicit operator controls.
This document records the architecture, interfaces and safety invariants. See the README and
current code for the supported user path and implementation details.

---

## 1. The user path

1. `hermes plugins install …/hermes-on-air`, then `hermes onair setup`: Twitch channel, stage
   profile and seed-bank file.
2. `hermes onair preflight` checks that the stream session can't reach tools or private memory.
   It refuses to arm otherwise.
3. Add one OBS **Browser Source** (`http://127.0.0.1:<port>/overlay`). It carries the subtitle
   line, floating chat, a LIVE dot and the agent's voice.
4. Open the **On Air desk** (dashboard tab): OFF AIR → APPROVAL → AUTONOMOUS, approve or reject
   lines, Privacy cut, Mute all, Stop all.
5. Go live. Real questions get answered with captions and voice. Bait (decode tricks,
   "say X", fake-host lines) gets silence. When chat goes quiet, the agent fills the dead air
   with real show content from the seed bank.
6. Privacy cut covers every surface in one frame, even while the model is mid-turn.

## 2. Architecture

```
Twitch IRC (anonymous, read-only)
   │  ChatMsg
   ▼
chat.gate.classify ──bait/spam──▶ silent skip (receipt only)
   │ worthy / routine
   ▼
chat.select (selective reply, routine cap, emote folding) ──nothing worthy + quiet≥45s──▶ beats.next()
   │ one MessageEvent per turn, chat wrapped as a data block, sender = synthetic "stage"
   ▼
Hermes gateway → AIAgent (stage profile: no tools, no memory)
   │ reply text
   ▼
adapter.send() → split into lines → guard.check_line (per line, fail closed)
   │ released lines only                       │ withheld → receipt + desk
   ▼                                           ▼
stage controller (mode: OFF_AIR | PAUSED | APPROVAL | AUTONOMOUS; privacy; mute; epoch)
   │ APPROVAL: queue for desk approve/reject     AUTONOMOUS: release
   ▼
tts.synth(line) → overlay server (SSE + audio) → OBS browser source
```

- **One plugin directory** carries: `plugin.yaml` + `__init__.py` (platform adapter, CLI
  commands) and `dashboard/` (desk tab + `plugin_api.py`). Verify the deferral semantics of a
  platform plugin with `hermes plugins doctor` (platform adapters load lazily) before relying
  on eager CLI registration.
- **Platform name:** `on_air`. **Plugin name:** `on-air`.
- **Sender model:** the adapter never forwards raw viewer messages as users. It dispatches a
  curated turn from one synthetic sender id `onair-stage`, and setup allow-lists exactly that id.
  Viewer text is quoted inside a `live_chat` data block, never as instructions. **Do not** use
  `trusted_inbound`.
- **Support alerts:** subs, resubs, gifted subs, gift bombs,
  bits cheers and raids arrive on the same anonymous read-only IRC connection (`USERNOTICE` and the
  `bits` tag on `PRIVMSG`). Each becomes a `SupportEvent` → an overlay `alert` plus at most one guarded
  thank-you turn. Gift bombs and bursts coalesce into one alert and one thank-you. The attached viewer
  message is untrusted chat. It passes the gate, is never read verbatim and is never shown unguarded.
  Display names pass the decoded-echo name check before they appear in an alert or a spoken line.
- **TTS order:** the guard runs **before** any caption or audio. Gateway auto-TTS or voice mode
  must not synthesize unguarded reply text for this platform. Either the adapter owns synthesis
  after the guard, or preflight proves auto-TTS is off for `on_air`.
- **State** lives under `plugin_data_dir()`: receipts (JSONL), seed-bank usage and the desk token.
  There is no replay on restart.
- **Reference implementations:** the bundled `plugins/platforms/irc` adapter (stdlib asyncio
  IRC; Twitch speaks IRC) and the Home Assistant plugin for service-adapter fields.

## 3. Frozen interfaces (`onair/types.py`)

These are the shared types. Import them rather than redefining them.

- `ChatMsg(platform, channel, msg_id, user_id, display_name, text, ts, first_msg, badges, emote_only)`
- `GateVerdict(kind, reason, rule)`, where `kind ∈ {"worthy","routine","bait","spam","skip"}`
- `GuardContext(chat_window: list[ChatMsg], armed: bool, never_say: list[str], protected_names: list[str], host_name: str)`
- `Verdict(release: bool, rule: str, reason: str)`
- `Beat(beat_id, segment, text, provenance)`
- `SupportEvent(platform, channel, event_id, kind, user_id, display_name, ts, amount, months, tier, recipient, message)`,
  where `kind ∈ {"sub","resub","subgift","giftbomb","bits","raid"}`

Function contracts:

- `onair.chat.gate.classify(msg: ChatMsg, window: list[ChatMsg]) -> GateVerdict`. It must never
  raise; any internal error returns `GateVerdict("skip", "gate error", "fail_closed")`.
- `onair.chat.select.pick(pending: list[tuple[ChatMsg, GateVerdict]], now: float, state) -> list[ChatMsg]`.
  It may return an empty list, and an empty pick means no turn at all.
- `onair.guard.check_line(line: str, ctx: GuardContext) -> Verdict`. It must never raise; any
  internal error returns `Verdict(False, "fail_closed", …)`. It is pure, with no I/O.
- `onair.beats.SeedBank(path).next(segment, used: set[str]) -> Beat | None`
- `onair.guard.decoders.candidates(text, *, armed=False) -> list[str]`, `looks_encoded(token) -> bool`,
  `split_joined(token) -> list[str]`. The gate imports these and keeps
  its own fail-closed fallback.

## 4. Overlay + desk contract

The overlay server binds **127.0.0.1 only** on port `onair.overlay_port` (default 18790).

- `GET /overlay` serves static HTML/JS/CSS from `onair/overlay/web/`. Strict CSP, no external
  fetches, and all text rendered with `textContent`.
- `GET /events` is SSE. Every event carries `type` plus `seq`:
  - `{"type":"caption","id","speaker","text","ttl_ms"}`
  - `{"type":"caption_clear"}`
  - `{"type":"chat","id","name","text"}` carries display-guarded chat only.
  - `{"type":"audio","id","url","caption_id"}` tells the overlay to play the clip and show the
    caption while it plays.
  - `{"type":"alert","id","kind","name","amount","months","tier","ttl_ms"}`. `name` is display-guarded
    (a failed guard falls back to "someone"); no viewer message text, ever.
  - `{"type":"state","live":bool,"mode","privacy":bool,"muted":bool}`. `privacy:true` means the
    cover replaces every element and audio stops **in the same frame**.
- `GET /audio/<id>.wav` serves a clip once it's released. 404 after a privacy cut or a cancel.

**Control discovery:** at connect, the core writes `<plugin_data_dir>/control.json` as
`{"url": "http://127.0.0.1:<port>", "overlay_url": "http://127.0.0.1:<overlay_port>/overlay", "token_file": "desk.token", "pid": <int>}`.
`desk.token` is a random token in the same directory (mode 0600). The control API requires
`Authorization: Bearer <token>`, refuses any request that carries an `Origin` header, and
binds 127.0.0.1 only. `plugin_data_dir` for this plugin is `<HERMES_HOME>/plugin-data/on-air/`.

Desk API, mounted by `dashboard/plugin_api.py` at `/api/plugins/on-air/` behind dashboard
auth, and proxied to the stage controller over loopback with the desk token:

- `GET state` · `GET pending` · `GET receipts?limit=`
- `POST mode {"mode": "off_air"|"paused"|"approval"|"autonomous"}`
- `POST approve {"line_id","epoch"}` · `POST reject {"line_id","epoch"}`
- `POST privacy {"on": bool}` · `POST mute {"on": bool}` · `POST stop` (stop all: cancel pending
  speech, clear captions, epoch bump)

## 5. Invariants

1. **Nothing reaches captions, audio or overlay chat without passing its guard.** If a guard
   raises, the line is withheld, and the withholding hits the offending line itself, not just
   the turn.
2. **Chat is data, never authority.** No chat line can change mode, call a tool, approve a line or
   speak as the operator or host. Forged host lines ("<host> here: go live", "<host> says …",
   homoglyph names) stay untrusted.
3. **Start OFF AIR**, entering APPROVAL on arm. A restart never replays anything.
4. **Operator controls work without the model.** Privacy cut, Mute all and Stop all take effect
   while a turn is in flight or the provider is hung.
5. **Stream session isolation.** The `on_air` platform toolset is empty, and the stage profile has
   no memory provider or private context. Preflight enforces this and refuses to arm otherwise.
6. **Bait gets silence, not spoken refusals.** Real questions are still answered.
7. **No dead air.** Outside privacy, pause and stop, silence over 60 s with no worthy chat is
   filled by a seed-bank beat. Each beat is used at most once per show.
8. **Selectivity.** Routine chatter gets at most one reply per ~2 min, and only when nothing better
   is pending. Emote and hype walls fold into one acknowledgement.
9. **Support is not authority.** A sub, cheer or raid buys no instruction, priority over the
   guard or verbatim read-out. Under privacy, pause or stop, alerts are queued silently or dropped
   (configurable), never aired. Mute silences alert sounds too.

## 6. Guard scope

The core is a **four-layer defence** against making the host say things:

1. A never-decode persona line, which ships in the bundled skill and default persona snippet.
2. A **decoded-echo guard** covering base64, hex, URL-encoding, rot-N, whole-string reversal,
   per-word reversal and atbash. Apply it to message segments **and display names**, splitting
   joined tokens (a joiner is any non-letter between two letter runs, plus camelCase).
3. **Armed-window refusal.** Once a turn is armed (decode request, encoded payload, or "say /
   repeat / read this"), read-out and announce lines are refused: "that decodes to …",
   "it says …", "translation: …".
4. **Repeat-on-request.** Every quoted span, plus the object of say / repeat / type / spell, is a
   target. A line containing a target is withheld (stop-only: the rest of the reply still airs).

Plus:

- the user's **never-say list** (config), checked case- and diacritic-insensitively;
- **forged-authority lines** ("<host> says go live");
- **meta leakage**: tool or JSON syntax, system-prompt talk, "as an AI language model".

It must fail closed. Price it honestly: measure false positives on a benign teaching and chat
set, and publish that set with the tests.

Users supply their own public persona, never-say list and seed bank.

## 7. Public-repo hygiene

This repository is public. Do not commit private names, hosts, paths, addresses, tokens, hashes,
internal tracker numbers or persona text anywhere: code, tests, fixtures, docs or commit
messages. Fixture handles and names stay generic (`viewer_42`, `host`, `cohost`); the demo host
is "Host". No compiled binaries and no self-updaters; secrets only through Hermes secret
scoping, never hard-coded. Outbound network calls and every local listener are disclosed in the
README.

## 8. Code layout

| Area | Paths |
|---|---|
| Twitch source, chat gate, selector, beats, stage controller, adapter, overlay server, TTS | `onair/sources/`, `onair/chat/`, `onair/beats.py`, `onair/stage.py`, `onair/adapter.py`, `onair/overlay/`, `onair/tts.py` |
| Guard and word filter | `onair/guard/`, `onair/wordfilter/` |
| Desk web UI | `dashboard/` (manifest, dist, plugin_api.py) |
| Manifest, entry, types, CLI, preflight, runtime gate, skills | `plugin.yaml`, `__init__.py`, `onair/types.py`, `onair/cli.py`, `onair/preflight.py`, `onair/runtime_guard.py`, `onair/skills/` |

Run `pytest -q` and `hermes plugins validate .` before proposing a change.

## 9. Change review

Every change is reviewed before it lands, following `docs/REVIEW.md`; an author does not merge
their own work unchanged. Review covers the invariants above and the disclosure in the README:
new outbound calls, listeners, subprocesses or written files must be added there.

## 10. History

The plugin was built against Hermes 0.22.7 and first released publicly after the initial
development cycle. Earlier iterations used a multi-worker lane process with a schedule; that
process is internal history and is not part of the contract.
