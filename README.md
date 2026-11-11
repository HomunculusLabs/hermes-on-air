# On Air

On Air is a Hermes plugin for an AI co-host on a live stream. It reads Twitch chat and guards
co-host replies before captions and optional voice. Your agent can help prepare OBS and a private
rehearsal while you keep the decisions to arm the show and go live.

![A stylized mascot in a room with a co-host caption along the bottom](docs/img/private-show-captions.png)

*A synthetic private practice scene showing the mascot and a caption at 1280×720.*

![The optional code-built mascot in its starter room](docs/img/scene.png)

*The optional [owner 3D scene](docs/scene.md), shown in a synthetic preview.*

## Features

- Anonymous, read-only Twitch chat; guarded replies with optional owner approval.
- Captions and optional voice, privacy and mute controls, and a rundown for quiet moments.
- OBS slides, shared window and three-box views, plus an optional owner-built 3D scene or VRM.
- [Work view](docs/work-share.md): publish selected code and test output to the existing slides source.
- [Live build](docs/live-build.md): have your agent build in small public steps, with owner-provided narration lines.
- [Talk to your co-host](docs/talk.md): type, hold to talk, review and cut in; plus a private, model-backed rehearsal.

## Quick start: let your agent run it

Install Hermes Agent and configure a model provider with `hermes setup` in an interactive terminal,
then install and enable On Air in your own Hermes home:

```bash
hermes plugins install https://github.com/HomunculusLabs/hermes-on-air --enable
# Optional, synthetic offline check; no Twitch, model, TTS or network:
hermes onair rehearse
```

In your own local Hermes agent, say for example:

> Set up On Air for my channel cozy_fox. Keep the co-host relaxed and reply less often.
> Make a show about sketching, with my drawing window and slides in a corner. Practise
> privately first, and let me approve each reply.

1. The agent checks `onair_setup_status` and asks only for missing choices. It uses
   `on-air:setup` and `on-air:produce` for the supported workflow.
2. It shows the effects and approval code from `onair_setup_plan`. You type the returned
   `/onair-setup approve <code>` and confirm **Set up & preview** on the card. The dashboard
   offers a setup approval too.
3. Review the private preview's test message and suggested reply; choose Show or Skip. The
   preview may use a canned reply or, with your choice, your configured model.
4. For a private show, the agent plans with `onair_show_plan`; you type the returned
   `/onair-setup show approve <code>` and confirm the card. It uses a real model and serves
   captions on loopback; it also speaks when your Hermes text-to-speech is available, unless you
   ask for captions only. Private typed chat is not Twitch chat.
5. The agent can publish slides, prepare supported OBS looks and read back settings. You choose
   any shared window and check the OBS preview and audio. A readback is not a pixels or sound test.
6. After you install the plugin and configure a model in the stage home, the agent can plan the
   real stage with `onair_stage_plan`. You type `/onair-setup stage approve <code>` and confirm
   its card. The stage starts off air. Arming it and going live remain your decisions.

Owner tools answer only in your own local Hermes (CLI, TUI or desktop). They refuse messaging
platforms and the public stage; a messaging chat cannot establish owner identity. The dashboard
is an alternative for supported controls. To control the separate real stage in the desk, the
browser dashboard or Desktop backend must run in that stage's profile; an owner's dashboard
cannot discover it. Views have separate owner-profile limitations; see [Views](docs/views.md).
Never paste keys or passwords into chat.

## What your agent gets

| Purpose | Registered tools |
|---|---|
| Setup | `onair_setup_schema`, `onair_setup_status`, `onair_setup_plan`, `onair_setup_progress`, `onair_setup_cancel` |
| Private preview and show | `onair_preview_safety`, `onair_show_plan`, `onair_show_status`, `onair_show_chat`, `onair_show_safety` |
| Real stage | `onair_stage_status`, `onair_stage_plan`, `onair_stage_control`, `onair_stage_host_turn`, `onair_stage_safety` |
| Presentation | `onair_work_share`, `onair_present_status`, `onair_present_publish`, `onair_present_control` |
| OBS, layout, window and broadcast | `onair_obs_setup`, `onair_obs_scene`, `onair_window_share`, `onair_broadcast`, `onair_layout` |

The first 15 tools belong to `onair_setup`; the other 9 belong to `onair_present` (24 tools in total). If an
explicit tool list hides them, enable the relevant toolsets with `hermes tools`. The packaged
skills are listed below. The owner slash commands include `/onair-setup`, `/onair-stage`, `/onair-present` and
`/onair-broadcast`. The CLI has `hermes onair setup`, `scene`, `preflight`, `rehearse`, `status`,
`host`, `check-line` and `report`. The CLI's manual setup path is documented separately.

## Skills pack

- `on-air:first-stream`: take a new show from setup to private rehearsal.
- `on-air:setup`: prepare, customize and check the co-host.
- `on-air:produce`: prepare slides, OBS and a rehearsal.
- `on-air:live-build`: build in public steps with owner-provided narration.
- `on-air:views`: arrange the three boxes and share a window safely.
- `on-air:scene`: create and preview the optional scene.
- `on-air:scene-build`: design an owner 3D room.
- `on-air:avatar`: use and check a licensed VRM.
- `on-air:host`: send host lines and use the talk controls safely.
- `on-air:show-day`: run the show-day checklist.
- `on-air:troubleshoot`: diagnose failures and report uncertainty.
- `on-air:seed-bank`: interview the owner and prepare a public rundown.

The skills are registered in owner homes, not stage homes. Owner tools refuse messaging
surfaces that cannot establish owner identity.

## What stays yours

- Review plans and type approval commands yourself, then confirm the host card. Safety stops do
  not require approval. Only you resume a private show or arm the stage.
- Choose when to go live. The agent can request a broadcast start, but you type the returned
  `/onair-broadcast start <code>` and confirm it. OBS reports whether output actually started.
- Choose the window in OBS or the system dialog. On Wayland, inspect the preview and confirm
  `/onair-broadcast confirm-window` yourself; the software cannot verify the selected capture.
- Configure model access using the stage home's own `hermes model`. Set the OBS WebSocket
  password through `ONAIR_OBS_PASSWORD`; do not give credentials to the agent in chat.

## How it stays safe

- Setup creates a separate stage home with memory, user profile and context files disabled. It
  is a separate configuration, **not an OS sandbox**: processes still run as your user.
- Preflight requires `platform_toolsets.on_air: [no_mcp]`. An empty list is not equivalent:
  globally enabled MCP servers may still load. The `llm_execution` middleware rejects stage
  requests carrying tools or memory context; the `pre_llm_call` hook skips inference off air.
- A deterministic output guard checks lines before captions and speech; a word filter blocks
  slurs and applies the configured profanity policy. These are fallible backstops, not a
  prompt-injection boundary or a replacement for human moderation.
- The real stage starts off air and arms into approval mode. Privacy, mute, off-air and stop
  controls are separate; a stop or off-air request is not confirmation unless the result says so.
- Network listeners bind to loopback. Control APIs use bearer tokens; the display pages themselves
  are not token-authenticated. See Disclosure before relying on local isolation.

## What this plugin can reach

- **Network:** Twitch IRC, the selected Hermes model, speech-to-text and text-to-speech providers,
  optional TypeSafe Jev or loopback Laya, loopback OBS and display/control APIs, and npm for the
  optional scene scaffold. Hermes installation can contact Git and package services.
- **Files outside plugin data:** selected stage-home configuration, persona and seed bank; installed
  plugin and process metadata; an operator-selected new OBS configuration root and scene directory.
- **Commands:** approved stage setup starts Hermes CLI commands and the stage process; optional
  development and verification tools start subprocesses. See Processes and jobs below.
- **Background processes:** an approved stage runs a separate Hermes process; previews, shows and
  presentation use background threads. Local display pages are not authenticated.
- **Stored credentials:** Hermes manages model and plugin credentials; On Air stores local control
  bearer tokens in its plugin data and writes a nonsecret stage sender file during setup.

## Disclosure

**Network and data sent.** When the real stage is connected, Twitch chat is read over TLS from
`irc.chat.twitch.tv:6697` using an anonymous, read-only IRC login. Model requests go through your
configured Hermes provider; opting into model-backed setup includes a small liveness request before
planning, and private preview can make a further model request. Private show and real stage can make
more. Voice sends released text through Hermes text-to-speech and its configured provider. Optional
hold-to-talk and owner-activated open mic send owner audio to the configured Hermes speech-to-text
provider, which may be cloud-hosted. Open mic sends detected transcripts to the host queue without
review; it is disabled by default and has a separate setting. See [Talk](docs/talk.md). OBS control
connects to a loopback WebSocket, default
`ws://127.0.0.1:4455`. Optional semantic assistance is off by default: Laya uses a configured
loopback HTTP endpoint (default `http://127.0.0.1:8390`); Jev uses
`https://api.typesafe.ai/v1/systemone`. Assistance receives the guarded draft and triggering
public chat, and may receive the public show policy; it advises only, never releases a line.
The optional scene scaffolder downloads pinned, SHA-256-checked three.js and three-vrm archives
from `registry.npmjs.org`. Plugin installation and Hermes dependency preparation can contact Git
and package services.

**Listeners.** All On Air listeners bind `127.0.0.1`, not the LAN:

| Listener | Port and access |
|---|---|
| Stage overlay | 18790 by default, configurable; read-only page, events and released audio, no bearer token; loopback Host checked |
| Stage desk control | Ephemeral by default, configurable; bearer token, loopback Host check and requests with `Origin` refused |
| Presentation page | 18795 by default, configurable; if nobody chose a port and 18795 is busy it uses a free loopback port and reports the URL; read-only page, state and events, no bearer token; loopback Host checked |
| Private preview and dashboard practice | Ephemeral overlay and control ports; overlay is unauthenticated, control uses a bearer token |
| Private show | Ephemeral unauthenticated overlay and separate bearer-token control API |

Local processes can access unauthenticated pages. The Hermes dashboard has its own authentication
and proxies control requests; it is not the same listener as the stage desk API.

**Processes and jobs.** Approved agent-led stage start runs `hermes config path`, then
`hermes onair preflight`, then `hermes gateway run` as a separate process session with a reduced
environment. It checks process identity and may signal its own process group on stop; status
also probes process existence. Preflight temporarily binds a loopback socket to check the overlay
port. Preview, show and presentation use background threads/event loops; setup and cleanup have
worker/watch threads, and the stage runs chat, speech and timing tasks. The gateway continues
until stopped. Optional development and install-verification tools can run subprocesses, create
throwaway homes, use local listeners and, when allowed, download dependencies; see
[compatibility](docs/compatibility.md) and [tools](tools/README.md).

**Files, secrets and retention.** Runtime data is under `<HERMES_HOME>/plugin-data/on-air/`:
receipts, seed usage, setup plans and job state, preview/show facts and tokens, stage process
records and logs, temporary voice clips and optional OBS startup packets. Receipts can contain
viewer names and line text. Tokens are local secrets; protect this directory and delete retained
data when no longer needed. Setup also creates a new stage home outside that directory, with
`config.yaml`, a nonsecret sender `.env`, public `SOUL.md`, a seed bank and a marker; later
customization updates stage files. The optional OBS startup-packet consumer writes to a new
operator-selected OBS configuration root, not an existing one. Hermes and OBS maintain their own
configuration and data. The plugin reads configured seed-bank files, stage configuration and
persona, installed plugin files and process metadata for readiness and stop verification.
The text-only work view publishes text explicitly supplied by the owner agent; it does not read
files, and its filters are not a guarantee against disclosure. See [Work view](docs/work-share.md).

On Air reads `ONAIR_OBS_PASSWORD` and `ONAIR_JEV_API_KEY` at call time through Hermes
`agent.secret_scope.get_secret`; if that API is unavailable, they are treated as empty. Hermes
may use the process environment in single-profile operation, but refuses an unscoped read under
multiplexing. Other `ONAIR_*` settings and `HERMES_HOME` can come from settings or environment.
To check the sender allow-list for a marked stage, the dashboard asks Hermes to build the selected
profile's scoped values; Hermes may read that profile's `.env` and configured secret sources.
The runtime does not parse `.env` itself. It writes the nonsecret stage sender `.env` during approved
setup and checks for an installation project `.env` without reading its contents. The standalone
developer verifier does not read `.env` files: it checks metadata when refusing an existing synthetic
stage home and refuses a source tree containing one. Metadata is not a content hash; separate tests
check the generated fixture's contents and sentinel isolation. Model credentials belong to Hermes, not On Air's setup plans.
On Air has no telemetry or self-update mechanism.

## Limitations (by design)

- The guard is a deterministic backstop, not a prompt-injection boundary. Keep the stage isolated
  and a human operator ready to cut output.
- Requests to translate, define, decode, repeat or say viewer-supplied text are skipped. Quoted
  targets are not echoed. Armed refusals are silent too; bait gets no spoken reaction.
- The decoder covers base64, hex, URL encoding, rot-N, reversal, per-word reversal and atbash.
  Unknown unannounced ciphers, some translation paraphrases and a single joined handle can pass.
- Base64 and lookalike supporter names become “someone”; rot13 or reversed names can show as
  written. An encoded-looking non-word in a host line can be withheld, including innocent text.
- Moderated chat already shown cannot be recalled until it scrolls away. Alerts air immediately
  in approval mode, though the thank-you line still waits for approval.
- The curated English word filter misses unlisted terms and unsupported obfuscation, and may
  block innocent uses. Keep Twitch AutoMod and human moderation enabled.
- The overlay has no built-in avatar, and no camera, guest or host video is provided. An optional
  [owner 3D scene](docs/scene.md) can be added separately. Typed private-show input is not Twitch
  intake. Voice needs your own Hermes TTS configuration and a listening test.
- On Wayland the owner confirms the share; OBS cannot attest whether it is a window or a screen.
  Layout changes to a scene already on program are visible immediately.
- macOS and Windows have not been tested in the documented compatibility matrix. See its
  distinctions between native execution, test doubles and defined but unexecuted CI.

## Manual setup and reference

- [Documentation index](docs/index.md): all guides in one place.
- [Quickstart](docs/quickstart.md): install and rehearse privately.
- [Streaming setup](docs/streaming-setup.md): stage, OBS, views and going live.
- [Troubleshooting](docs/troubleshooting.md): symptoms and safe checks.
- [FAQ](docs/faq.md): common questions and limits.
- [Safety](docs/safety.md): owner decisions, controls and their limits.
- [Views](docs/views.md): three boxes and window sharing.
- [Host turns](docs/host-turns.md): typed lines to the co-host.
- [Seed bank](docs/seed-bank.md): prepare the show rundown.

- [Getting started](docs/getting-started.md): manual CLI setup, offline rehearsal and settings.
- [Agent setup](docs/agent-setup.md): approvals, preview, private show and real stage.
- [Production](docs/production.md): briefs, layouts and recovery.
- [Presentation](docs/presentation.md): slides, OBS, shared windows and broadcast.
- [Desk](docs/desk.md): operator controls and optional semantic assistance.
- [Overlay](docs/overlay.md): browser source, captions and audio.
- [Optional 3D scene](docs/scene.md): owner-built room and bring-your-own VRM.
- [Compatibility](docs/compatibility.md): tested matrix, install verification and data removal.
- [Design specification](SPEC.md): contracts and numbered invariants.

## Compatibility

Hermes **0.22.7 or newer** is required, using Hermes's Python **3.14** runtime. On Air has no
Python dependencies of its own. The documented local matrix includes Linux tests; macOS CI is
defined but not executed, and Windows is not covered. Python 3.11–3.13 are not promised by
Hermes for this runtime. See [compatibility](docs/compatibility.md) for exact test scope.

## Development

The dependency-only smoke suite below covers core, guard, word filter and portability tests; it
is **not** the full suite. In a clean environment it should report passes and skips (including
Hermes-dependent portability checks), with no failures:

```bash
uvx --with pytest --with pyyaml --with websockets --with ruamel.yaml --with fastapi --with httpx pytest -q -p no:cacheprovider tests/core tests/guard tests/wordfilter tests/portability
```

For the full suite, use Hermes's supported Python 3.14 runtime, a matching Hermes checkout, and
pytest plus the dependencies above available to that interpreter. Set `HERMES_TEST_PYTHON` to
that Python and `HERMES_SDK` to the checkout; add the test dependencies to its environment or to
`PYTHONPATH`. From the repository root run:

```bash
PYTHONPATH="$HERMES_SDK${PYTHONPATH:+:$PYTHONPATH}" "$HERMES_TEST_PYTHON" -B -m pytest -q -p no:cacheprovider
hermes plugins validate . --install-deps
hermes onair rehearse        # once installed: the gate, guard and stage controller, offline
```

The full suite should report passes and environment-dependent skips, not failures; a skip is not
proof that its integration ran. Browser and installed-runtime checks may require extra opt-in
fixtures. See [docs/REVIEW.md](docs/REVIEW.md) for the review checks.

## License

MIT. The word filter includes a subset of LDNOOBW under CC BY 4.0 and entries also present in
better_profanity under MIT; see [its attribution](onair/wordfilter/README.md). The guard's English
Speller Database word list has its own permissive notice in
[WORDLIST-LICENSE.txt](onair/guard/WORDLIST-LICENSE.txt).
