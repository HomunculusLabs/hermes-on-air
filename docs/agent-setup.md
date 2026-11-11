# Agent-first setup, customization and private preview

Use this guide to prepare a private preview, show and separate stage with your owner agent.

On Air is operated by your own private Hermes agent. You say what you want; the agent does the
work through the plugin's own tools, and you approve the exact plan. The dashboard's **Start here**
tab shows the same plan and state and is optional.

## What you do

1. Install and enable the plugin in your own Hermes home (see `getting-started.md`).
2. Tell your agent, for example: *"Set up On Air for my channel cozy_fox. Keep it relaxed and reply
   less often. Never talk about politics."*
3. The agent shows you the plan's effects and an approval code. You type
   `/onair-setup approve <code>` in your own chat and pick **Set up & preview** on the card (or press
   **Set up & preview** → **Approve** in the dashboard). That is the only step only you can do.
4. A private preview starts on this computer with one test message and a suggested reply:
   **Message → Suggested reply → Viewer preview**. You choose **Show this reply** or **Skip**.
   **Pause** and **Stop** are always visible. Nothing reaches Twitch, OBS, your live overlay or
   viewers.

Still unavoidable for a new user: choosing the channel, the approval itself, and (if you have none)
setting up a model connection with Hermes's own `hermes model` flow. Voice and OBS come after the
first reply and stay optional.

## What the agent uses

| tool | effect |
|---|---|
| `onair_setup_schema` | read-only contract: answers, public style fields, presets, examples, states |
| `onair_setup_status` | read-only sanitized status (model connection label only, never keys) |
| `onair_setup_plan` | read-only plan: exact effects + digest + approval code |
| `onair_setup_progress` | read-only job steps, results, errors with one next action, preview facts |
| `onair_setup_cancel` | safety: cancel (its changes are undone) and stop the preview |
| `onair_preview_safety` | safety: pause (privacy cut) or stop the private preview |

The packaged skill `on-air:setup` tells the agent how to use these six setup and preview tools.
If the default stage folder is unsuitable, the plan names the alternative and the reason; the owner
may request a specific new absolute folder under their home using the optional `stage_home` answer.
They cannot approve or apply the setup, start a preview, test the model, show a reply or run shell
commands. Approval comes from the dashboard click or the typed `/onair-setup approve` plus the host's
choice card. Other On Air tools operate presentation and OBS as documented below. The tools, command and skill are
not registered in a stage home and refuse the On Air platform, so the public co-host never sees
them.

A second packaged skill, `on-air:produce`, is for producing a show once setup is done: it takes a
plain-English brief, asks only what is missing, picks a supported look (`onair_layout`), builds it
with the presentation, OBS and window tools, rehearses in the private show and covers stop and
recovery. See `production.md`. It is registered the same way, owner homes only.

Enablement: enable the plugin (`plugins.enabled: [on-air]` in the owner's home). The loader
registers 13 setup, stage and private-show tools in `onair_setup`, eight presentation and OBS
tools in `onair_present`, both slash commands and both packaged skills. If you saved an explicit
`platform_toolsets` list, use `hermes tools` to offer the relevant toolsets to your local client.
Messaging platforms are refused by the owner-only handlers, even if a toolset is offered there.

## What the stage reads

The stage's real settings path (platform extra -> plugin settings -> `ONAIR_*` env) reads the saved
`reply_pacing` into the chat selection limits and `never_say` (with avoid-topics) into the guard
word list; tested through the real loader and adapter in `tests/setup/test_installed_entry.py`.
`reply_length`, `tone`, `persona` and `topics` reach the real stage only as `SOUL.md` instructions to its
model; only the private preview enforces the length as a token cap.

## Customizable (public) style

| field | values | consumer |
|---|---|---|
| `persona` | one or two public sentences (240 chars, no links/markup) | stage `SOUL.md` |
| `tone` | friendly, relaxed, upbeat, dry | stage `SOUL.md` |
| `topics` / `avoid_topics` | up to 8 short subjects each | `SOUL.md`; avoid-topics also join `never_say` (deterministic guard) |
| `reply_length` | short, medium | `SOUL.md`; preview token cap 60/120 |
| `reply_pacing` | normal, less_often, rare | `reply_pacing` setting → chat selection (`max_worthy_per_turn`, `routine_gap_s`) in the stage and the preview |
| `output` | captions | voice needs its own listening test after the first reply |

Unknown fields (for example `voice`, `autonomous`, `system_prompt`) are refused with a reason.
Choices are saved in the stage home's own settings (`reply_pacing`, `public_style`, `never_say`)
and a marked section of `SOUL.md`; other edits to those files are kept. A later change is a
`customize` plan with the same approval; a cancelled change restores the exact previous bytes.

## Real stage: what setup did, and what is left

Setup writes everything it can inside the stage home it created and owns: settings, channel, isolation
(memory/profile off, `[no_mcp]`), the stage's own nonsecret `.env` (`ONAIR_ALLOWED_USERS=onair-stage`), the
public `SOUL.md`, the starter seed bank and the saved style. Started the way the gateway starts
(`load_hermes_dotenv` on that home, then plugin discovery and `adapter.connect()`), that home arms through
the unmodified preflight; `tests/setup/test_installed_entry.py` drives that chain with only the Twitch reader
and the provider replaced by loopback fixtures (message -> real agent loop and runtime gate -> real guard
holds the draft or blocks never_say -> desk approval -> overlay caption -> disconnect).

What is left is in `onair_setup_status` -> `real_stage.steps`. Two prerequisites stay with the user, each
with `why_not_here` and its exact secure next action: installing the plugin into the stage is the Hermes
plugin manager's job (`HERMES_HOME=<stage> hermes plugins install ... --enable`; it may download Hermes's
own dependencies), and the model needs the user's own provider and key in the stage's `hermes model`
(setup never reads or copies keys). Preflight and starting the stage are `agent_can_do: true`: they are
the approved real-stage operation below.

## Operating the real stage from the agent (`onair/stage_runtime.py`)

For a PREPARED stage (its plugin present and enabled, its own model configured, and, if voice is on,
a text-to-speech provider configured in that stage home) the owner's agent runs the
real stage through the registered entry:

If voice is on without a provider, the owner can configure one with `HERMES_HOME=<stage> hermes tools`
or save captions only. A configured provider is not sound-tested by this check: listen to one line
before going live. Preflight retains its voice warning.

1. `onair_stage_status` -> `startable`, or `missing` with each prerequisite's exact `next_action`. Status is
   read-only (`stage_runtime.readiness`): it never issues or replaces an approval code.
2. `onair_stage_plan` -> effects + approval code (digest of the stage's config, SOUL.md and plugin files).
   Only this explicit plan issues a code; a newer plan replaces (invalidates) the older one.
3. The USER types `/onair-setup stage approve <code>` and picks "Start the stage (off air)" on the host card.
   After the card returns, `start` compares the shown code with the stored plan UNDER the setup lock and
   consumes it (one approval, one start attempt; a plan replaced while the card was open starts nothing).
   Before ANY CLI process it checks by file metadata only (nothing opened) the startup secret planes every
   Hermes process loads besides its own home: the CLI must be this plugin's own installation, BOTH `hermes_cli`
   and `gateway` must resolve (by metadata) to files of that installation (they may share one root; a missing,
   namespace or unlocatable origin is unverifiable and refuses), that
   installation must have no project `.env` (`hermes_cli/main.py` and `gateway/run.py` load
   `<install root>/.env` at startup and on each gateway turn) and there must be no administrator-managed
   scope (`/etc/hermes`); otherwise it refuses with the exact reason and next action. The child's `HOME` is a
   stage-owned folder (`plugin-data/on-air/home`), so owner-home credential files are not discovered; the
   stage's own `.env`/`secrets:` remain its only secret sources.
   The start re-checks every prerequisite (read-only), checks the home Hermes ACTUALLY resolves (`hermes config path`
   must print the stage's own config; Hermes re-homes a HERMES_HOME outside `profiles/` to a sticky
   `active_profile` of that root, so the child carries Hermes's own profile pin `HERMES_GATEWAY_PROFILE_PIN=1`
   and a mismatch refuses before anything starts), runs `hermes onair preflight` (must pass) and then
   `hermes gateway run`, both with `HERMES_HOME=<stage>` and a minimal environment (PATH, HOME, locale,
   TMPDIR, PYTHONPATH and Hermes's own no-lazy-install flags; never the owner's provider keys), as its own
   process session. The config-target probe and preflight each have their own 90 s timeout (two serialized
   bounds under the setup lock, not 90 s in total). It records the pid + process start time + a generation id
   and returns the OBSERVED state: the stage's `control.json` names that pid and its desk answers
   `mode: off_air`. An approval while it already runs starts nothing and reports the observed mode (or
   "desk did not answer: NOT confirmed off air"). A start that does not connect in time KEEPS its process and
   says so; `onair_stage_safety stop` recovers it, and a new plan + approval starts again.
4. Going on air (Approval) and approving lines stay with the owner on that stage's desk.
5. `onair_stage_safety` `off_air` / `stop` (or `/onair-setup stage off|stop`): `off_air` is `ok` with
   `mode: off_air` only when the stage's own desk confirmed it; a stage that is positively gone is a no-op
   (`stopped: true`, no mode claimed); a dead main process with an unconfirmed stop is NOT off air. A
   missing or invalid saved stage selection is not proof the recorded stage is gone: while a runtime record
   remains, `off_air` reports `state: target_missing` (nothing sent) unless a complete check shows its
   process is gone; `stop` still acts on that owned record. If the recorded pid now belongs to a process
   with a different (or unknown) start time, stop sends NO signal (`state: foreign_lifetime`) and keeps the
   record unconfirmed. If the process now holding that pid cannot be identified at all (its /proc data is
   unreadable or malformed, or the existence probe is refused), that is NOT treated as gone: stop sends no
   signal (`state: identity_unknown`), keeps the record unconfirmed and a new start stays refused; status and
   `off_air` also report `identity_unknown` (not gone, nothing sent). If the identity was lost only after an
   earlier off-air operation in the same stop, the receipt says that operation was attempted and gives its
   own result (`off_air_attempted`, `off_air_result`); an attempt is not proof a request reached the desk or
   that output is off. Only an affirmatively absent leader lets stop clean up survivors of its old session. The
   off-air request goes only to the exact runtime generation the operation captured (re-checked under the
   setup lock right before it), so a replacement stage never receives it. Stop takes it off air, ends
   that process session and is `ok` only when NO process of the stage's own session remains (a COMPLETE
   `/proc` scan; an unreadable entry makes it unconfirmed) and every
   endpoint it announced (saved in the runtime record when observed, so they survive the leader and
   control.json disappearing) is closed. Endpoints that were never observed (`endpoints_observed: false`)
   are not called checked: the stop then relies only on the session-scoped proof
   (`unobserved_endpoints_proof: session_gone…`): every On Air socket is opened by the stage's own gateway
   process, so with its whole session gone none of its processes can hold one (processes that leave the
   session are the accepted residual). Otherwise the stop says NOT confirmed, the record is kept, status
   reports `stop_unconfirmed`, and no new start replaces it; stop again. The record is removed only by
   compare-and-delete of that exact generation.
   Restarting (resume) is a new plan + approval; a second copy never starts a second stage for one home.

Unprepared stages start nothing. A plugin folder in the stage may be a plugin-manager install or a drop-in
copy; this check does not tell them apart and claims no plugin-manager acceptance.
`tests/setup/test_stage_runtime.py` drives this through the registered tools and command against the REAL
`hermes gateway run` (real gateway runner, plugin load, adapter, runtime gate, guard, desk, overlay), with
only the external edges replaced: a loopback IRC fixture for Twitch (a `sitecustomize` on the test's
PYTHONPATH redirects the reader's TLS connection and refuses all non-loopback connections) and a loopback
OpenAI-compatible model. Its user-completed prerequisites are disclosed fixtures: a drop-in plugin copy, a
loopback model entry with a placeholder key and a free overlay port. It proves start -> off air -> owner
arms -> viewer message -> model -> held reply -> owner approves -> overlay caption -> agent off air ->
stop -> start again -> stop, and that the stage process did not inherit a key planted in the owner's
environment.

Two defects only the real gateway showed, fixed here: the adapter put metadata on its MessageEvent, which
the gateway's native admission refuses for every non-WhatsApp platform (`invalid_params (metadata)`), so
every stage turn was dropped; and preflight's overlay-port probe bound without `SO_REUSEADDR` (the overlay
server itself uses `reuse_address=True`), so a restart right after a stop failed preflight on TIME_WAIT.

## Stop is honest across copies and processes

`stop` is `ok` only when output is off, nothing is pending, the preview's ports are closed AND no model
request is still in flight anywhere. A stopped preview whose request is still running publishes that in the
shared setup folder until its owner records it finished; every other copy or process (dashboard, agent tool,
`/onair-setup stop`, status) reports `draining`, not stopped, meanwhile. `/onair-setup stop|cancel` uses the
same combined result as the cancel tool and the dashboard: "stopped" only when the setup job reached a
terminal state AND the preview stop is complete; otherwise it names the unconfirmed part.

## Not tested here

Genuine provider replies, Twitch (TLS + certificate to irc.chat.twitch.tv), voice, OBS, a real LLM
conversation using these tools, plugin-manager installation, a fresh cold install and whether a new user
understands the flow are not established by the automated tests.

## The private show (the co-host actually hosts)

After setup is saved, your own Hermes agent can run a **private show**: the co-host opens it in its own
words, brings up the next talking point whenever chat is quiet, answers the private chat worth answering
and says every released line through the real overlay (captions) and Hermes's own voice.

- **Plan** (read-only): `onair_show_plan {brief, policy, quiet_seconds, voice}`. `brief` is PUBLIC text (said on
  air). `policy` is `auto` (lines the output guard releases air by themselves; default) or `review` (each
  line waits for Show/Skip). `voice: false` makes this show captions only; by default it also speaks when
  your Hermes text-to-speech is available here. The plan lists every effect, the model connection and voice it will use, and
  a cap of 40 model requests per show.
- **Approve once**: you type `/onair-setup show approve <code>` in your own CLI/TUI/desktop chat and pick
  **Start the show** on the card. The normal dashboard has no model connection, so its On Air tab shows
  that exact command as the next step instead of a Start button (a process that does have `ctx.llm` shows
  **Start the show**). The agent cannot approve it.
- **While it runs**: the show is the first section of the On Air tab (Start here), above the setup
  preview, whose own Pause/Stop are labelled "Setup preview only" and do not stop the show. It shows the activity (Thinking, Speaking, Listening,
  Waiting for you, Paused), the captions (🔊 = also spoken), each private chat message and whether it was
  answered or set aside by the chat gate, and a private captions+voice page URL. Type chat there, or ask
  your agent (`onair_show_chat`).
- **Stop / Resume / End**: **Stop show** pauses everything at once (in-flight line, queued speech and
  captions are dropped); **Resume show** continues (owner only: tab button or `/onair-setup show resume`);
  **End show** closes it. The agent's `onair_show_safety` can stop or end, never resume.

What is real: the host's model client (`ctx.llm`), the stage's own turn prompts (`render_turn_prompt`),
`StageController` selection/guard/epochs, `HermesTTS` through `ctx.dispatch_tool("text_to_speech")` and
the overlay. Nothing is canned: with no reachable model connection the plan refuses and names the
prerequisite (`hermes model`, or start it from your own agent chat when the model is configured but not
reachable from that process); it never offers the setup preview's canned stand-in. With no text-to-speech in the hosting process the plan says **captions
only**. The show is loopback-only: no Twitch, OBS, live overlay or stage `control.json`.

YAML: On Air reads stage `config.yaml` through `onair/yamlio.py` (Hermes's own `ruamel.yaml`; PyYAML only if
present), so the supported Python 3.14 environment without PyYAML works.
