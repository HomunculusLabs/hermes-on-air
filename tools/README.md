# On Air core: developer notes

This covers the dev tools and the gateway integration notes for the core
modules (`onair/sources`, `onair/chat`, `onair/beats.py`, `onair/stage.py`,
`onair/adapter.py`, `onair/overlay/server.py`, `onair/control.py`,
`onair/tts.py`).

## Fake show (no gateway, no LLM, no Twitch)

```
python tools/dev_fake_show.py                 # any Python with PyYAML or ruamel.yaml
python tools/dev_fake_show.py --voice --dead-air 20 --guard real
```

Shadow-only semantic assistance beside queued lines (never approves anything):

```
python tools/dev_fake_show.py --mode approval --assist demo        # toy rules, offline
python tools/dev_fake_show.py --mode approval --assist laya --assist-endpoint http://127.0.0.1:8390
python tools/assist_eval.py --endpoint http://127.0.0.1:8390 --out results.json   # frozen synthetic set
```

Open the printed overlay URL (default `http://127.0.0.1:18790/overlay`) in a
browser or an OBS browser source. A scripted chat feed loops. It includes:

- newcomers and real questions, which get answered;
- emote walls, folded into one acknowledgement;
- forged host lines with homoglyph names, decode requests and say-requests, which stay silent;
- support events: a sub, a gift bomb with its gift lines (one alert), a cheer whose
  message is bait (the alert airs, the message never reaches a turn) and a raid.

A fake agent writes short replies after `--think` seconds.

- **Keys (on a TTY):** `p` privacy, `m` mute, `s` stop, `a` approval, `u` autonomous, `o` off air, `y`/`n` approve or reject the oldest pending line, `q` quit.
- **Desk API:** at the URL printed on startup, with the bearer token from `<data-dir>/desk.token`.
- **Data dir:** `$TMPDIR/onair-fake-show` by default (`--data-dir` to change). It stays outside
  the plugin tree, because `hermes plugins validate` scans every file under the plugin
  directory, gitignored scratch included, and fake bait in receipts would trip its scanner.
- **Signals:** SIGINT and SIGTERM shut the show down cleanly and remove `control.json`.
- **Guards:**
  - `--guard demo` (the default) uses a pass-through demo guard. A banner says so.
  - `--guard real` uses `onair.guard.check_line`.
- **Voice:** `--voice` makes silent WAV clips, so `/audio` and the `audio` events run.
- `--no-alerts` leaves the support feed out; `--once` plays each script once.

## Gateway configuration

```yaml
plugins:
  enabled: [on-air]
  entries:
    on-air:
      settings:
        twitch_channel: your_channel      # read-only, anonymous
        host_name: YourName               # protected from impersonation (homoglyphs too)
        agent_names: [CoHostName]         # names the agent answers to; also protected
        seed_bank: /path/to/seed_bank.yaml
        never_say: ["phrase one", "phrase two"]
        dead_air_seconds: 45
        alerts:                           # support alerts (all optional; these are the defaults)
          enabled: true
          thank_you: true                 # at most one guarded thank-you turn per alert burst
          include_messages: false         # the attached sub / cheer message stays out of the turn
          min_bits: 1
          during_privacy: drop            # or "queue": hold silently until live again
gateway:
  platforms:
    on_air:
      enabled: true
      extra: {}                           # optional per-platform overrides, same keys
platform_toolsets:
  on_air: [no_mcp]                        # no tools at all for the stream session
```

Add this to `.env`:

```
ONAIR_ALLOWED_USERS=onair-stage
```

Settings resolve in this order:

1. `gateway.platforms.on_air.extra`
2. `plugins.entries.on-air.settings`
3. `ONAIR_<KEY>` environment variables
4. built-in defaults

Other keys: `overlay_port` (default 18790), `control_port` (default 0 = any free port), `speaker_name` (the caption label, default `Co-host`), `protected_names`, and `voice` (default true).

**Toolsets.** With no `platform_toolsets.on_air` entry, the platform gets
the default toolsets, which for one install was 18. An explicit empty
list `[]` still pulls in every globally enabled MCP server. Only `[no_mcp]`
resolves to no tools at all.

## How a turn flows (verified against `gateway/platforms/base.py`)

1. **Chat in.** The read-only source pushes `ChatMsg` into the controller. The gate classifies each message:
   - **bait:** a receipt only;
   - **spam / skip:** dropped;
   - **worthy:** answered;
   - **routine:** capped.
   The overlay's chat ticker shows only display-guarded, non-bait chat.
2. **Picking a turn.** In order: worthy chat, then a thank-you for supporters, then
   budgeted routine chat, then a dead-air beat. Routine chat gets at most one reply per
   `routine_gap_s` (120 s), and only when nothing worthy is pending. Walls fold into one
   acknowledgement. A tick with nothing worthy and no budget starts nothing.
3. **Dispatch.** The adapter builds **one** `MessageEvent` from the synthetic sender
   `onair-stage` (`chat_type="dm"`, `message_id = turn_id`, `allow_gateway_control=False`).
   Viewer names and text are XML-escaped inside a `live_chat` block (`support_alert` for a
   thank-you). The event's chat id is the **show's session**, `stage-<boot>-<n>`: every arm from
   off air starts a new one, and a restart never reuses one, so one show's chat never carries
   into the next show's agent context.
4. **Reply out.** The gateway delivers the final reply through `send(chat_id, text, reply_to=turn_id)`. `send` splits it into spoken lines and runs the guard once per line, with this turn's chat window. Only lines for the **current** turn at the **current** epoch can air:
   - in approval mode, released lines wait in the desk queue;
   - in autonomous mode, they go to the air queue.
   A guard exception withholds that line only.
5. **Speech and captions.** For each line, TTS runs first. Then the overlay gets the `caption`, then the `audio` event that points at `/audio/<id>.wav`.
6. **Ending the turn.** `on_processing_complete` ends the turn. If the agent takes longer than `turn_timeout_s`, the turn is abandoned and the in-flight run is cancelled.

`send` always reports success, even for withheld lines. A withheld line is a decision. If `send` reported a failure, the gateway would retry it or ledger it for redelivery.

**Nothing can air from:**

- gateway notices or error messages (`reply_to` is not the current turn);
- a late reply after Stop or Privacy cut (stale epoch);
- a delivery-ledger redelivery or an auto-resumed turn after a restart. Turn ids carry a per-process boot id, so no replay is possible.

## The gate, in brief

- **Newcomers are per show.** The controller keeps a set of viewers seen since the show was
  armed from off air and passes it to `classify(..., seen=...)`. A regular never turns back
  into a "newcomer" because a busy or long stretch pushed their earlier lines out of the
  500-message / 900 s window. Twitch's own `first-msg` flag still counts.
- **Translation asks follow the payload.** A translate / define / pronounce / "how do you say"
  ask is silent bait (`translation_ask`) only when viewer-supplied text is present: in the same
  message, in the same viewer's previous two messages ("je suis fatigué" then "what does that
  mean?"), or in the same viewer's next two messages after a payload-less ask ("can you
  translate?" then "je suis fatigué"). Payload-less asks ("can you translate?", "do you speak
  Spanish?"), the host's own name ("how do you pronounce your name?") and translating an
  object ("how do I translate my game into Spanish?") stay ordinary questions.
- **Forged authority:** "<host or agent> says / wants / told …" is bait whatever follows, as
  are homoglyph and leetspeak names.
- **Moderation:** `CLEARMSG` drops that message, a targeted `CLEARCHAT` drops that viewer's
  waiting chat, and a channel-wide `CLEARCHAT` drops everything waiting.

## Operator controls (invariant 4)

`set_privacy`, `set_muted` and `stop` are synchronous and need no model, so they take effect while a turn is in flight or the provider is hung.

**Privacy cut and Stop all** both:

- bump the epoch;
- drop pending and queued lines, and the chat waiting to be answered (nothing asked before
  or during a cut is answered after it lifts);
- drop pending thank-yous and the alert burst being collected;
- forget every clip, so `/audio/*` returns 404;
- clear captions;
- publish an urgent `state` event, which drops undelivered caption, chat, audio and alert frames for every overlay client;
- cancel the in-flight agent run (best effort).

**Stop** also moves the mode to `paused`.

**Mute** stops audio but keeps captions running. The overlay reads `state.muted` to silence
alert sounds too.

**Desk bodies are strict.** `POST /privacy` and `POST /mute` accept only a JSON boolean `on`.
`{}`, `null`, strings, numbers or a wrong key are a 400 and change nothing.

**Modes.** The only way out of `off_air` is `approval`. `autonomous` is reachable only from
`approval`, or from `paused` once this show has been in approval (resuming after Stop all).
Both the controller and `POST /mode` enforce it.

## Support alerts (invariant 9)

- **Source.** The same anonymous read-only connection carries them: `USERNOTICE` by `msg-id`
  (`sub`, `resub`, `subgift`, `submysterygift` → `giftbomb`, `giftpaidupgrade` /
  `primepaidupgrade` → `sub`, `raid`) and `PRIVMSG` with a `bits` tag (a cheer, with its
  `Cheer100`-style tokens stripped). Other `msg-id`s get an `ignored` receipt. A cheer never
  doubles as chat. Twitch's `system-msg` text is never used.
- **Coalescing.** A gift bomb swallows the gifter's next N gift lines for 60 s. Events fold
  into one burst until 2.5 s pass quietly or 10 s after the burst opened, whichever comes
  first. Each burst is **one** overlay `alert` and at most **one** thank-you turn. Several gifts
  from one gifter read as a gift bomb. A multi-supporter burst shows `name: "<n> supporters"`
  with the strongest kind (gift bomb, raid, bits, sub, gift), and `amount` is that kind's total.
- **Names.** A name must pass `onair.guard.check_name` (encoded or decoded names, never-say
  terms, protected-name lookalikes), read as a plain handle, and pass the line guard. Otherwise
  it shows and is spoken as "someone". Anonymous gifts show as "someone". Gift recipients are
  never shown or sent to the model.
- **Messages.** With `include_messages: false` (the default) the attached message never
  touches the gate, the window or a turn. With `true` it is gated like chat: it joins the
  guard window either way (so bait arms the guard), and rides in the thank-you as a quoted
  `<msg>` only if the gate passed it.
- **Priority.** Worthy chat outranks a thank-you, and a thank-you outranks routine chat and
  dead-air beats. Thank-yous older than 180 s are dropped.
- **Never under privacy, pause or stop.** `during_privacy: drop` (the default) drops events
  and pending thank-yous. `queue` holds un-alerted events silently (up to 50, for 10 min) and
  alerts them once the show is live again; pending thank-yous are dropped either way. Off air
  drops everything.
- **Approval mode.** The overlay alert airs directly, since it carries no model output and no
  viewer text. The thank-you lines wait for the desk like any other line.

## TTS findings

- **Route.** `onair/tts.py` calls `ctx.dispatch_tool("text_to_speech", {"text", "output_path"})`. That is the documented `PluginContext.dispatch_tool`, which goes to `tools.registry.dispatch`, so the user's configured Hermes TTS provider and voice are used. It doesn't depend on the session toolset, so `[no_mcp]` doesn't affect it.
- **Validation.** No Hermes core module is imported for speech. `hermes plugins validate` reports all checks OK with 0 warnings, including "no core override".
- **Fallback.** If the tool is unavailable, returns an error, raises or times out (30 s), that line airs captions-only.
- **Timeouts leave nothing behind.** A timed-out job is marked abandoned; when its worker
  thread returns, its output files are deleted instead of read. Leftover `clip-*` files from a
  crash are swept when the engine starts.
- **Order.** Speech is synthesized only **after** the guard released the line, and in approval mode only after the desk approved it.
- **Gateway auto-TTS can't speak unguarded text here:**
  - `_wants_auto_tts` only fires for inbound `VOICE` events, and every On Air turn is `TEXT`.
  - `play_tts`, `send_voice` and the other media senders are overridden to refuse, with a `media_refused` receipt.
  - `supports_streaming_tts` returns False.
  - `register()` asks for the `minimal` display tier, which turns streaming previews, tool progress and interim messages off for `on_air`.

## Plugin loading (deferral)

- **Bundled platforms are deferred.** Only `source == "bundled"` platform plugins get the deferred loader (`plugins_discovery.gate_manifest`).
- **User installs load eagerly.** A user-installed `kind: platform` plugin listed in `plugins.enabled` takes the normal `load` path. Its `register()` runs at discovery in every process that discovers plugins, CLI included, so `ctx.register_cli_command` is available there.
- **CLI cost is negligible.** Discovery already imports `gateway.platforms.base` with the plugin disabled. Measured with and without the plugin enabled, the difference is within noise.

## Tests

The supported Hermes runtime is Python 3.14; use a pytest environment with the required
Python dependencies and the Hermes SDK on `PYTHONPATH`. For the complete compatibility
matrix and the fresh-install verifier, see [compatibility](../docs/compatibility.md).

The fresh-install test uses `ONAIR_HERMES_PYTHON` as the **actual runtime under test**.
With `--strict-version`, an interpreter whose copied Hermes workspace has neither an
install stamp nor Git metadata fails the version check, even if a different checkout on
`PYTHONPATH` has a valid tag. Use a runtime with verifiable identity from its own executing
source tree; do not copy in a stamp or borrow another checkout's version. The verifier's
public-launcher mode is the closer new-user installation check; copy mode and the Python
module entry are explicitly narrower.

Without the Hermes core (`uvx --with pytest pytest -q`), the adapter / end-to-end module
skips, and so does the YAML seed-bank test when no YAML library is present (a YAML bank then
fails closed with "needs PyYAML or ruamel.yaml"; Hermes ships both).

The tests never touch a real profile, because `HERMES_HOME` is forced to a temp dir. The adapter and end-to-end tests run on the real `gateway.platforms.base` and register through the real platform registry, and **every test in that module runs twice: once on a permissive fake guard and once on the real `onair.guard.check_line`.**

The end-to-end tests cover:

- a fake IRC stream through the real source;
- the gate;
- a turn becoming a `MessageEvent`;
- a canned agent reply going through `send`, the guard and the air queue to an SSE caption plus audio;
- bait producing no turn;
- a Privacy cut mid-turn, after which nothing airs, the run is cancelled and clips 404;
- a restart, after which nothing replays;
- a support burst (gift bomb plus its gift lines, a resub and a cheer carrying bait, an
  encoded name, an unknown notice) becoming one alert and one guarded thank-you, with no
  viewer message or unsafe name anywhere in the turn or on SSE;
- `include_messages: true` gating the attached messages;
- support under a privacy cut never airing, even after the cut lifts.
