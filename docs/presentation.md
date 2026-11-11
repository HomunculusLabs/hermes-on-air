# Presentation, shared window and broadcast (private agent)

`onair.presentation` lets your own private Hermes agent put its work on stream and run OBS output:

- a **presentation surface**: a public web page the agent fills with a small plain-text deck (title, bullets,
  text and code slides), moves between slides and switches to a standby card;
- **OBS control** over WebSocket v5: the owned presentation Browser Source, the existing caption/voice overlay,
  approved program scenes and read-back of what OBS actually reports;
- a **shared OS window**: one window that *you* choose in OBS or the system chooser, shown on its own scene;
- **layout presets** (`onair_layout`): slides, window, window with a slides inset, or side by side, arranged
  only in the owned scenes and read back;
- **broadcast start/stop**: start is your release (you type a command and confirm); stop is always available.

Two kinds of sharing are kept apart everywhere (tool results, labels, readiness):

| Label | What it is | Who chooses the content |
|---|---|---|
| `browser_source` — "Browser Source (web page, not screen capture)" | OBS loads the agent's page | the agent, through `onair_present_publish` |
| `os_window` — "Shared window (OS window capture, chosen by you in OBS)" | OBS captures one OS window | you, in OBS properties or the system dialog |

A Browser Source is never described as screen capture. Screen-only capture kinds are never used as a window
share. The one exception is Wayland's combined screen/window kind: it is shown only after the owner confirms
one intended window, and is always reported as `capture_type_verified: false` (see Shared window).

## Integration contract

The integrator owns the single real registration call and the `plugin.yaml` merge.

```python
from onair import presentation
registered = presentation.register(ctx, config=None)   # -> ["tool:...", ..., "command:onair-broadcast"]
presentation.teardown()                                 # on plugin shutdown
```

- `register` registers NOTHING in an On Air stage home (the public co-host). It serves nothing and contacts
  no OBS: the surface starts on the first `onair_present_publish` or `serve`; OBS is contacted only by an OBS tool.
- Every handler takes authority from `ctx.session_surface()` (owner clients `cli`, `tui`, `desktop` only;
  the On Air platform and messaging chats are refused). Schemas have `additionalProperties: false` and extra
  keys such as `caller`/`owner`/`approved` are refused, never read.
- `teardown()` stops the surface (its port closes) and the private loop thread. It does **not** stop a running
  broadcast; an operator's stream is never ended silently.

Manifest additions for the integrator (`plugin.yaml`):

```yaml
provides_tools: [onair_present_status, onair_present_publish, onair_present_control, onair_obs_setup,
                 onair_obs_scene, onair_window_share, onair_broadcast, onair_layout]
optional_env:
  - {name: ONAIR_OBS_URL, description: "OBS WebSocket URL (loopback only, default ws://127.0.0.1:4455)", password: false}
  - {name: ONAIR_OBS_PASSWORD, description: "OBS WebSocket server password", password: true}
  - {name: ONAIR_PRESENT_PORT, description: "Loopback port for the presentation page (default 18795)", password: false}
config_schema:
  presentation: {type: dict, default: {}, description: "obs_url, present_port, overlay_port/overlay_url, approved_scenes, owned scene/input names"}
```

Configuration is read from `config` (if passed), else `ctx.get_config("presentation")`, else environment.
Keys: `obs_url`, `present_port`, `overlay_port` (default `ONAIR_OVERLAY_PORT` or 18790) or `overlay_url`,
`approved_scenes` (existing scenes the operator allows the agent to switch to), `presentation_scene`,
`window_scene`, `presentation_input`, `overlay_input`, `window_input`, `width`, `height`, `start_deadline_s`,
`stop_deadline_s`, and opt-in `scene_view` (default false). The OBS password is read from `ONAIR_OBS_PASSWORD` at call time and never returned.
For the three-box layout, dashboard Views and the optional 3D scene see [Views and boxes](views.md).

## Tools (toolset `onair_present`)

| Tool | Arguments | Effect |
|---|---|---|
| `onair_present_status` | `include_obs` (default true) | Read-only: surface (serving, URL, connected pages), program (mode, slide, revision, headings), and OBS: program scene, owned sources, speech-audio check, window readiness, stream output. |
| `onair_present_publish` | `deck {title, slides[]}` or `sample: true`; `start` | Validates and replaces the public deck, serves if needed, shows slide `start`. |
| `onair_present_control` | `action`: next, prev, goto (`index`), standby (`title`, `note`), resume, serve, stop_serving | Moves the program; returns the new state. |
| `onair_obs_setup` | `action`: inspect, configure, prepare_startup | `configure` creates/updates only the owned scenes and the two owned Browser Sources, then reads them back. Never switches program or starts output. On macOS with no Browser Source yet it returns `bootstrap_required` before changing anything; `prepare_startup` writes (does not apply) the owned startup packet. See "macOS: start OBS from the startup packet". |
| `onair_obs_scene` | `scene` | Switches program to an approved scene; ok only when OBS reports it. The shared-window scene is refused until the window is ready (the same check as `onair_window_share show`). |
| `onair_window_share` | `action`: discover, prepare, open_chooser, show, hide | See below. |
| `onair_broadcast` | `action`: status, request_start, stop | `request_start` only returns a one-time code. |
| `onair_layout` | `action`: presets, inspect, apply; `preset` (apply): presentation, window, window_pip, side_by_side; `corner` (window_pip only). Boxes: `boxes`, `set`, `put`, `swap`, `arrangement` | Fixed presets and the bounded three-box model; see [Views and boxes](views.md). No arbitrary source, URL, HTML or CSS input. |

Slash command (typed by you): `/onair-broadcast [status | start <code> | stop | confirm-window | revoke-window]`.
`start` needs the current code (single use, 2 minutes) and your confirmation on the host's choice card; a chat
that cannot show the card refuses. OBS's own Start/Stop buttons keep working as always.

`confirm-window` is the only way to confirm the Wayland combined capture input (see Shared window). The command
consumer reads the owned input from OBS (it must exist, be the combined kind and produce frames), asks
`ctx.ask_choice(question, [{"value": "confirm", ...}, {"value": "cancel", ...}])`, and only for `"confirm"`
re-reads the input and binds the confirmation to its UUID, kind and settings (refused if anything changed while
the card was open). It runs only in an owner session (cli/tui/desktop). `revoke-window` drops it. The
confirmation lives in process memory: a restart or re-registration forgets it.

### Deck limits

At most 40 slides; deck title 120 characters; heading 120; subtitle 200; up to 8 bullets of 200; text body
1200; code 4000 characters / 60 lines; language label 24. Text only: the page renders `textContent`, never
HTML. A narrow check refuses obvious private material (home-directory paths, private-network IPv4 addresses,
key-like tokens). Everything on the deck is visible to viewers; it is not a substitute for judgement.

The page fits each slide by itself. Text shrinks in small steps, down to 0.45 of its normal size, until the
slide is whole; within the limits above only code can still be too long, and then its last lines are cut
inside the code box. The lowest 250 of the 1080 pixels stay free of slide text, because that is where the
overlay shows captions; the deck title and the slide counter sit at the top. Short slides read best.

## Endpoints (loopback `127.0.0.1` only, `GET`/`HEAD`, loopback `Host` required)

- `/present` — the page for the OBS Browser Source (1920×1080). Strict CSP, no audio or media. Opened in a plain
  browser window of any size it scales to show the whole slide.
- `/present/state` — the current public snapshot (JSON). Only the current slide is public, never the whole deck.
- `/present/events` — Server-Sent Events: the snapshot on connect, then one per change.

There is no write endpoint. If the page loses its connection it shows the standby card, never stale content.

## OBS

Enable OBS's WebSocket server (Tools → WebSocket Server Settings) and give the password through
`ONAIR_OBS_PASSWORD`. Only loopback `ws://` addresses are accepted. Each action opens a short connection and
reads back the result; when OBS cannot be reached the answer is `connected: false, ok: false`.

`configure` owns these names (configurable): scenes "On Air: Presentation" and "On Air: Shared window",
inputs "On Air presentation (browser)" (the page, *Control audio via OBS* off) and "On Air captions & voice
(browser)" (the overlay, the one source that carries the voice), with the overlay on top in both scenes. An
existing input with an owned name that is not a Browser Source is refused and nothing is changed.

**Fit to the canvas.** The owned pages are 1920x1080. `configure` reads the canvas from `GetVideoSettings`
`baseWidth`/`baseHeight` (the base resolution, not the output scaling) and fits every owned Browser Source item
in the two owned scenes inside it with `SetSceneItemTransform` (`OBS_BOUNDS_SCALE_INNER`, aspect ratio kept,
centred), then reads each item back with `GetSceneItemTransform`. Foreign items, and owned sources placed in
other scenes, are never transformed. If OBS reports no valid canvas, refuses a transform, reads back a
different one, or an owned item is missing, the answer is `configured: false` with `fit.fitted: false` and the
reason (`sources_configured` says whether the sources themselves were set). On macOS the startup collection
places the page unscaled, so until `configure` runs a smaller canvas shows only its top-left part.
The presentation page's item inside the shared-window scene (a picture-in-picture or side-by-side box) is
arranged by `onair_layout`, so `configure` leaves it where the layout put it.

### macOS: start OBS from the startup packet

On macOS, OBS starts its browser engine when the first Browser Source of the process is created. If that
first source comes from a WebSocket request, the engine starts on an obs-websocket worker thread and every
Browser Source in that OBS process stays blank (no page request, no renderer). Creating it again over WebSocket
cannot fix that process. So on macOS, `configure` with no Browser Source in OBS returns `ok: false,
bootstrap_required: true` and changes nothing.

`prepare_startup` (owner session only, presentation surface serving) writes three files into the owned
`<HERMES_HOME>/plugin-data/on-air/obs-startup/` folder, never into an OBS config root:

- `OnAirStartup.json`: a scene collection whose only Browser Source is the real presentation source (owned
  name, the runtime's presentation URL and size, muted, audio not routed to OBS). The voice overlay is not in it;
  `configure` adds it later, so the voice still plays once.
- `required-settings.json`: `user.ini` values to write into a FRESH owned OBS config BEFORE its first start
  (`[General] FirstRun=true`, the collection selection), the start order, and the loopback target. A
  `FirstRun=true` that OBS wrote after a run proves nothing.
- `MANIFEST.json`: sha256 and size of both, written last.

It contains no OBS password, stream service/key, profile output settings, provider data or credentials.
Re-running gives `unchanged` for identical bytes and `updated` when the runtime changed and the old packet is
intact. A hand-edited or malformed packet (`drift`), files in the folder without a manifest (`collision`) or a
symbolic link in the folder, its parents or among its entries (`refused_symlink`) are refused without writing.
Deriving the folder path creates nothing: registration, `inspect`, a refused caller and an unserved
`prepare_startup` leave `plugin-data` absent.

The packet is checked strictly wherever it is read (`obs_bootstrap.validate`): exactly the three regular files,
the packet name/version/state, a manifest binding exactly the collection and settings files with matching
sizes and hashes, `[General]` exactly `FirstRun=true` before the first start, the startup collection selected,
one muted presentation Browser Source at the loopback target, and the exact start order. When `configure`
reports `bootstrap_required`, `startup_packet.prepared` is true only for a valid packet built for the CURRENT
runtime URL/config. An intact packet for an older URL is `stale` (run `prepare_startup` again); a malformed one
is `drift` with a `problem` code. Neither is recommended for applying. Checking never writes.

The packet is prepared, not applied. Applying it means, on the machine running OBS: write the prestart
settings, install the collection's exact bytes, make the presentation URL reachable there (it is a loopback
URL, so it must be served or port-forwarded on that machine), start a NEW OBS process with that collection, and
only then run `configure`. Do not restart or edit an OBS the owner is using to do this.

`obs_bootstrap.apply(packet_dir, new_root, expected_manifest_sha256)` is the consumer (stdlib only, so the same
file runs on the OBS machine). It needs the `expected_manifest_sha256` that `prepare_startup` returned and
refuses any other manifest. Before writing anything it refuses an invalid packet, a target that exists, a target
with a symbolic-link parent, a relative or `..` path, a missing parent, or a target that is (inside) a normal OBS
config root (`~/Library/Application Support/obs-studio`, `~/.config/obs-studio`, plus any you add). It writes
only `user.ini` (`FirstRun=true` and the collection selection) and `basic/scenes/OnAirStartup.json` into the new
root, then reads both back. It never starts OBS.

`configure` answers `configured` from what OBS reads back and always returns `render.verified: false`. Settings,
accepted requests and an active source do not prove the page rendered; the page connecting back
(`render.presentation_pages_connected`, `onair_present_status` `surface.pages_connected`) and the OBS preview do.

**Voice is played once.** Every overlay page plays the voice. The speech-audio check passes only when exactly
one overlay Browser Source exists (the owned one, audio through OBS) and the presentation page has audio off
(`localhost`, `127.x` and `::1` addresses of the overlay count as the same page).
Extra overlay sources are listed so you can remove them; the agent does not mute or delete your sources.

## Shared window

1. `discover` reports the OBS platform, which window-only capture kinds this OBS lets a client create
   (`window_kinds_available`), whether `prepare` can create anything (`can_create_input`, `creatable_kind`),
   the input's `selection` and `capture_type_verified`, readiness and the exact `next_human_action`.
   Prerequisites only suggest `prepare` when it can work.
2. `prepare` creates the owned window-capture input in the shared-window scene (or, for an input that already
   exists, only adds it to that scene). It never picks a window.
3. You choose the window:
   - **Windows**: kind `window_capture`; pick the window in the input's properties.
   - **macOS**: kind `screen_capture` with Method *Window Capture* (or the legacy `window_capture`). OBS needs
     Screen Recording permission (System Settings → Privacy & Security → Screen & System Audio Recording).
   - **Linux, OBS running on X11**: kind `xcomposite_input`; pick the window in the input's properties.
   - **Linux, OBS running on Wayland: operator-selected capture, owner-confirmed.** Current OBS has no
     window-only kind there: `xcomposite_input` is not registered on Wayland and the window-only PipeWire kind
     is obsolete. It lists and creates only the combined PipeWire kind `pipewire-screen-capture-source`, whose
     system dialog (xdg-desktop-portal) offers monitors as well as windows. OBS does not report which was
     picked, so the software cannot tell a window from a whole monitor. This kind is supported only as an
     operator-selected source:
     1. `prepare` creates it (`selection: operator_selected_unconfirmed`, `capture_type_verified: false`).
        Nothing is shown. Preparing it or opening the dialog authorizes nothing.
     2. You pick ONE dedicated window (not a screen) in the system dialog. If no dialog appeared,
        `open_chooser` presses the input's Reload button.
     3. Check in the OBS preview that the input shows only that window, then type
        `/onair-broadcast confirm-window` in your own Hermes (CLI, TUI or desktop) and choose
        *Yes: one intended window* on the card. Cancelling, a chat without a card, a non-owner chat or an input
        that changes while the card is open confirms nothing. The agent has no tool action or argument that can
        confirm.
     4. Results then report `selection: operator_confirmed`, `operator_confirmed: true` and still
        `capture_type_verified: false`: your answer is the only check that a window, not a display, was picked.
     The confirmation is bound to the input's OBS UUID, kind and current settings. It ends when the agent runs
     `prepare` or `open_chooser`, when the input is recreated or its settings change (for example a new pick),
     or when you type `/onair-broadcast revoke-window`. Before you confirm, `show` refuses and returns the exact
     `next_human_action`. Alternatives: the presentation page (`onair_present_publish`), or OBS in an X11 session.
   - **Linux, legacy input**: the older window-only `pipewire-window-capture-source` is flagged obsolete in
     current OBS: it is not listed and cannot be created (OBS answers 605). If an input with the owned name and
     that kind already exists in your scene collection, it is used: OBS asks the system's window dialog
     (xdg-desktop-portal), and `open_chooser` presses the input's Reload button if no dialog appeared. Results
     label it `legacy_input: true`.
4. `show` puts the shared-window scene on program only when OBS reports the input producing frames and either
   the input is a window-only kind with a window chosen (`selection: window_only_kind`,
   `capture_type_verified: true`, meaning only that OBS registers the kind as window-only), or it is the
   combined Wayland kind and you have confirmed it as above. `hide` returns to the presentation scene.

Window titles are never returned to the agent. Screen-only kinds (for example `monitor_capture`, `xshm_input`,
`pipewire-desktop-capture-source`) are refused as a window share. The combined
`pipewire-screen-capture-source` is NOT technically prevented from capturing a display: the supported policy is
that the owner picks one intended window and confirms it; the API cannot attest that choice. For a first test,
share a dedicated demo window you opened for the show.

Checked against obs-studio `7a25c3fd8b5eb87c2d2e0606cae36e32014bc6fa` (window-capture.c,
mac-sck-video-capture.m, xcomposite-input.c, linux-capture.c, screencast-portal.c, libobs/obs-source.h) and
obs-websocket `e28f84975984340e84fc4879cfc05741cc0afde1` (GetInputKindList skips disabled kinds; CreateInput
checks the same list). Not yet exercised against a running OBS.

## Layout presets (`onair_layout`)

| preset | scene | items (bottom to top) |
|---|---|---|
| `presentation` | On Air: Presentation | page full canvas, overlay full canvas |
| `window` | On Air: Shared window | window full canvas, page hidden (if present), overlay full canvas |
| `window_pip` | On Air: Shared window | window full canvas, page inset 30% of the canvas width in `corner`, 2.5% margin, overlay full canvas |
| `side_by_side` | On Air: Shared window | window left half, page right half (2.5% margins), overlay full canvas |

- `presets` answers without contacting OBS: each preset's real requirements (`presentation_page`,
  `shared_window`, `voice_overlay`, `canvas`), the `standby` and `conversation` recipes and what is not offered.
- `inspect` is read-only: the base canvas, the owned items as OBS reports them (enabled, stacking, bounds), which
  preset each owned scene matches now (`current`), whether the last applied preset `still_matches`, and per
  preset `ready` or `missing[*]` with `need`, `why` and `next_action`.
- `apply` first checks everything by reads only: a valid base canvas (`GetVideoSettings` `baseWidth`/`baseHeight`),
  the owned scene and its overlay item, exactly one speaking overlay and the page's audio off, the page served
  and configured, and for window presets the existing window-share readiness (on Wayland: the owner's
  `/onair-broadcast confirm-window`). Anything missing returns `applied: false, changed: false` and the next
  steps; nothing is sent. Then it hides the owned items the preset does not use, sets each used item's bounds
  (`OBS_BOUNDS_SCALE_INNER`, aspect ratio kept, centred in its box; the same fields `configure` sends), keeps the
  page above the window and the overlay above every owned item, shows the used items (an inset is created
  hidden first), and reads every transform, enabled state and the stacking back. `applied: true` only when the
  readback matches; a refused request or a mismatch says `applied: false` and lists what was already accepted.
- It never selects or captures a window, switches the program scene, starts or stops output, changes audio or
  touches foreign scenes and items. If the scene is already on program, `on_program: true` and viewers see the
  change at once. Put a look on program with `onair_window_share show` (window presets) or `hide` /
  `onair_obs_scene` (presentation).
- Layout state is always read from OBS: a change made elsewhere (dragging the inset, a new canvas size, a
  re-created window input) shows as `still_matches: false`, never as still applied.
- Standby (`onair_present_control standby`) is a card on the page, not an audio privacy cut; in a window
  layout the window stays visible until the presentation scene is on program.

Tested only against the labelled loopback OBS double; the geometry is pure arithmetic shared with the tests.
No native OBS pixels exist for these presets yet.

## Broadcast

`onair_broadcast status` reports OBS's `GetStreamStatus`. Start sends `StartStream` only after your release,
then waits (default 15 s, capped at 25 s) until OBS reports the output active; an accepted request that never becomes active
is reported as NOT live. Stop is confirmed only when the output is observed inactive. The stream destination
and key stay in OBS (Settings → Stream); this module never reads or writes them. Recording is not used.

## Not covered here

- Microphone/input mute automation (the earlier lane's restoration policy is unresolved and excluded).
- Encoder, platform delivery and public go-live acceptance: a separate integration step.
- A real OBS has not been exercised by this component's tests; they use a labelled loopback v5 double.
