# On Air in Hermes Desktop

Use this guide to operate On Air from the Hermes Desktop interface.

The same installable On Air package includes `desktop/plugin.js`. When enabled, it contributes an **On Air** sidebar entry, a full workspace page and a compact right pane. They share one owner and use the existing authenticated dashboard backend through the Desktop SDK; there is no second show controller. The browser dashboard is unchanged. The backend discovers only a show in its own profile: an owner's backend does not discover a separate real stage started in the stage profile. Use a backend running in the stage profile for stage controls; Views mutations require the owner profile and retain the limitations in [Views](views.md).

## Enable

Install and enable the On Air agent plugin in the intended profile. In Desktop, open **Capabilities → Plugins**, rescan and enable the Desktop half separately. For a remote backend, the Desktop half must also be installed on the machine running Desktop. The show and dashboard backend must be available. No build step is required.

## The desk

- **Show** leads with a live status bar: one dominant show-state indicator (Off air, Approval, Autonomous, Paused, with Privacy and Mute overriding it), a quiet connection dot plus rundown line, and the three safety actions — Privacy, Mute, Stop — always in the same top-right group with their keyboard hints; Stop is styled as the destructive action. Below it, waiting lines render as cards (who the line answers, readable line text, a primary Approve on the oldest line and a quiet Skip) with a friendly empty state, beside a Mode segmented control with a one-line consequence under the selected mode and the current rundown. Two columns on wide pages; one column when narrow.
- **Views** centres on a stage diagram drawn with CSS: Main with Sub 1 and Sub 2 where readback places them (stack = insets over main, side = main plus a column), each box labelled with its source, circular swap buttons on a shared axis with hover tooltips and accessible names, and the three source pickers under the stage. In Stack, four diagram buttons choose the inset corner; the selected corner and diagram follow server readback, and a quiet hint marks an arrangement recalled from storage. Insets are raised above the caption band. The "Show Boxes on stream" action is replaced by "On stream now" when Boxes is on program. A source move is reported inline. Simple looks are preview tiles; window sharing is a Choose → Confirm → Show stepper.
- **Host** is a composer with character count, Send, cut-in and draft-clearing messages. The `data-onair-slot="host-voice"` mount point directs the owner to the dashboard's On Air page, Talk section for hold-to-talk; microphone capture is not mounted in Desktop. See [Talk](talk.md).
- **Pane** has a status header, the next waiting line with Approve, safety actions, Cut in, and a mini stage diagram. The diagram is schematic, not a video preview.

States and motion: visible keyboard focus, hover and pressed states, an inline spinner on the notice while a request is in flight, disabled controls with a reason where the state provides one, refusals inline in a warning style, and `prefers-reduced-motion` respected. Text contrast is at least 4.5:1 in both themes on the shipped palette; hit targets are 32 px or larger.

An accepted request is not the same as an observed effect. The desk reports refusals inline and uses readback for state. When the state and waiting-line epochs disagree, it fails closed. An unavailable desk cannot provide emergency control; use independent show controls if needed.

## Screenshots

Two small generic renders from the offline visual harness (headless Chromium, synthetic show data — no real session) are committed at `docs/img/desktop-show-light.png` (light) and `docs/img/desktop-show-dark.png` (dark). They show the Show tab at a wide size: the status bar with the safety group, the waiting-lines column with the primary Approve on the next line, and the mode/rundown sidebar. The 28-capture evidence matrix — every tab, the pane, both themes, wide and narrow, plus privacy, refusal, busy, empty-queue and OBS-unreachable states — lives outside the repository at `desktop-evidence/v3/` on the machine that produced it.

## Lifecycle and limitations

Page and pane share a polling loop. It begins on the first mount, backs off on read errors and stops scheduling when the last surface unmounts. The SDK's REST helper supports a timeout but no abort signal. An already issued call can therefore finish after unmount; the owner discards its late response rather than claiming it canceled the network request. The real Desktop application has not yet been used to verify this integration.

## Existing backend routes

All paths are relative to the plugin-scoped SDK REST helper. Every POST carries a JSON object.

| Method | Path | Payload |
| --- | --- | --- |
| GET | `/state`, `/pending`, `/views/state` | Current show, waiting lines, presentation readback |
| POST | `/approve`, `/reject` | `{"line_id": line.line_id, "epoch": line.epoch}` |
| POST | `/mode` | `{"mode": "approval"}` (also `autonomous`, `paused`, `off_air`) |
| POST | `/privacy`, `/mute` | `{"on": true}` or `{"on": false}` |
| POST | `/stop` | `{}` |
| POST | `/cut-in` | `{"epoch": state.epoch, "boot": state.voice_boot}` |
| POST | `/host/turn` | `{"text": text, "epoch": draft_epoch, "boot": draft_boot, "id": draft_id}`; the random ID remains stable for retries of the same draft |
| POST | `/views/boxes` | `put` with `slot` and `source`; `swap` with `a` and `b`; `arrangement` with `arrangement` and optional `inset_corner`; or `set` with `main`, `sub1`, `sub2`, `arrangement` and optional `inset_corner` |
| POST | `/views/scene` | `{"scene": scene}` |
| POST | `/views/layout` | `{"preset": preset}`, then switch to the returned scene |
| POST | `/views/window` | `{"action": "discover"}`, `show`, or `hide` |

The route and validation source of truth is `dashboard/plugin_api.py`; operational behavior follows the existing dashboard desk. Only an explicit `ok: true` is treated as an accepted action.

## Provenance

The owner-authored private UI's `ui/CONTRACT.md`, `ui/UI-EXPORTS.json`, `ui/desktop/stream-desk-panel.mjs` and `ui/desktop/stream-desk-style.mjs` informed the neutral card layout, compact pane and theme treatment. The private `native-qualification-20261110/native/r16/candidate/desktop/plugin.js` informed the page, sidebar and pane contribution pattern and shared-owner structure. This implementation is rewritten for the existing On Air routes. No third-party voice or call code is included.

## Five-step owner check

1. Install the package in the intended profile, enable the agent half, rescan Desktop plugins and enable the Desktop half.
2. Open the On Air sidebar page and right pane. Confirm both reflect the same live show and update together.
3. In a safe rehearsal, check waiting-line approve/skip, mode confirmation, privacy, mute and Stop. Verify a refused request is shown and stale state cannot approve a line.
4. Check Views against OBS readback, including unavailable sources and window instructions. Send a host line and verify a draft clears when the show epoch changes.
5. Close both surfaces and inspect that polling stops; reopen them and verify the browser dashboard still works. Already in-flight SDK calls can drain until their timeout; immediate network cancellation is not supported by the current SDK.
