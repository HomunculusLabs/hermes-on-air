# Views and boxes

Use this guide to arrange the OBS boxes and share a window.

Views arranges **three boxes** in the owned `On Air: Boxes` OBS scene: Main, Sub 1 and Sub 2.
A box can contain the optional 3D **Scene**, **Slides** (including the [work view](work-share.md)), or one owner-selected **Window**;
either sub can also be **Empty**. Main cannot be empty. Each source appears at most once: putting
it in another box moves it. Swap any two boxes unless that would empty Main. **Stack** fills the
canvas with Main and places smaller subs in a corner; **Side** places Main to the left and stacks
subs in the right column. Stack defaults to `bottom-right`, with Sub 1 nearest the bottom and Sub 2
above it; `top-right`, `top-left` and `bottom-left` are also available. The insets sit above the
bottom 250/1080 of the canvas reserved for the default caption band. Side ignores the corner for
geometry, but remembers the choice for Stack. Empty subs are hidden. If both are empty, Main fills
the canvas in both arrangements, so OBS geometry cannot distinguish the arrangement.

The captions/voice overlay is a single full-canvas top layer, **not a box**. The scene and slides
pages are silent. The boxes support no arbitrary OBS source, URL, remote computer, camera or
third-party audio. Apply/readback reports geometry, enabled state and order—not rendered pixels
or sound. A failed apply can leave partial changes; inspect OBS before continuing. Changing a
scene already on program changes what viewers see immediately. The plugin does not guarantee a
whole-program privacy cut: keep OBS's own controls available. OBS cannot atomically switch and verify a
scene: an item added at the instant of switching can appear briefly before detection and rollback. If
rollback or its readback fails, check OBS immediately; the current program scene may be unknown.

## Owner-agent control

Publish or serve slides and ask the owner agent to run `onair_obs_setup` with `action: configure`.
To use Scene, enable `scene_view: true` in the presentation settings first (see below).
For example, ask your agent to call:

```json
{"action":"set","main":"slides","sub1":"scene","sub2":"empty","arrangement":"side"}
```

with `onair_layout`. Use `{"action":"put","slot":"sub2","source":"window"}` to move
an available source into a box; `{"action":"swap","a":"main","b":"sub1"}` swaps;
`{"action":"arrangement","arrangement":"stack","inset_corner":"bottom-right"}` changes geometry.
`inset_corner` is optional on `set` and `arrangement`; `set` defaults to bottom-right and
`arrangement` preserves the previous choice if omitted. `{"action":"boxes"}`
reads back the boxes. If Boxes does not exist yet, a first put, swap or arrangement starts with
Scene in Main when available, otherwise Slides; both subs start empty and the arrangement is Stack.
A present but inconsistent scene is never silently reset. These operations arrange but do not switch program. Ask for
`onair_obs_scene` with `scene: "On Air: Boxes"` to show it. The window guard is checked again
when switching to Boxes. Before switching any approved scene, the plugin also refuses unverified
captures, nested scenes and unsupported source kinds; approval of a scene name alone does not
confirm a capture. Only the owner can choose and, where required, confirm a capture.

### Reading the result

`boxes` in inspect and apply results includes `canvas` (`width`, `height`), `slots`, and `geometry`
keyed by `main`, `sub1`, and `sub2`. Each geometry row has `source`, `enabled`, `bounds`, `content`
and `z_index` (OBS scene item index, higher is in front). Rectangles use `x`, `y`, `width`, `height`
in canvas pixels. Bounds are OBS's observed transform; content is the centered scale-inner fit of
OBS's observed source dimensions within those bounds, rounded to two decimals. `content` is null if
source dimensions are unavailable or the transform is not the expected uncropped, unrotated fit.
Empty boxes have `enabled: false` and null rectangles/index. If no supported layout can be identified,
`slots` and `geometry` are null. `overlay_topmost` indicates an enabled overlay above all other
enabled items; it is not a render check. Results also retain availability, window and program state.

`arrangement_source` is `readback` only when geometry uniquely identifies the arrangement; otherwise
it is `stored` when a previously successful selection is in the private scene identity record, or
null when unknown. `arrangement_ambiguous` remains true when geometry alone is ambiguous. Likewise,
`inset_corner_source` is `readback`, `stored`, or null. A successful move has a `move` object with
`source`, `from`, `to` and a human-readable `message`; failed operations do not claim a move.

The presentation page reserves its right column for default Stack insets as well as its caption band.
Other slides, 3D scenes and windows have no automatic content-aware safe area, and non-default corners
can cover them. Caption styling/position can also be customized beyond the default band. The plugin
cannot see rendered pixels, verify what a portal selected, or hear audio; inspect OBS preview and listen.

### First time, off air

After starting the presentation surface and setting up OBS, these owner requests work as a walkthrough:

1. “Please set up OBS, publish sample slides, put the 3D scene in Main with both subs empty, and show the boxes on program. Do not start streaming.”
2. “Please put the slides in Sub 1, keep the 3D scene in Main, and report the observed pixel geometry.”
3. “Please swap Main and Sub 1, then change the arrangement to Side. Remain off air.”
4. “Please change the arrangement to Stack, preserving the current sources. Then swap back and put slides in Sub 2; tell me what moved.”
5. “Please empty Sub 2 and report the box readback.”

Serving slides can show a standby card until a deck is published. Scene needs the optional scene view
enabled; if it is unavailable, choose Slides as Main. Showing a scene does not start or stop a stream;
check OBS's stream status separately.

## Dashboard Views

The authenticated desk shows the observed program scene, OBS stream state, box assignments,
source availability, arrangements, swaps and simple looks. **Simple looks** are the existing
fixed presets: Slides (`presentation`), Shared window (`window`), Window + slides inset
(`window_pip`), and Side by side (`side_by_side`). They are distinct from custom boxes. The
3D scene also appears as a whole-scene switch when configured and present.

The dashboard and owner agent normally run in different processes and sometimes different
profiles. The dashboard reads its **own profile's** presentation settings/environment and uses
an independent OBS controller. It cannot know the owner's in-memory window confirmation or
the actual presentation surface URL. As a result it disables affected box/preset controls and
asks for `onair_layout` in the owner agent; it cannot open the chooser or prepare the capture
without invalidating the wrong process's confirmation, so those actions also go through the
owner agent. A dashboard running in a stage profile disables view mutations entirely. Where a
configured approved scene or a window-only capture is independently verifiable, the bounded
routes can act and read back. Configure the intended owner profile consistently; a stage
profile is not a substitute for the owner runtime. Unreachable OBS disables the controls.

The stream line is **read-only**. The dashboard cannot start or stop OBS streaming. Ask the
owner agent for `onair_broadcast` with `action: request_start`, then type the returned
`/onair-broadcast start <code>` yourself and confirm on the host card. OBS's own controls
remain available. The desk's show privacy, mute and stop controls are not a universal OBS cut.
The Host line appears only when the connected show reports it enabled; the desk sends the
current show epoch and displays the stage's acceptance or refusal.

## Share one window

1. In your owner session ask for `onair_window_share` **discover**, then **prepare**. This
   prepares an input but does not authorize or select a window.
2. On Windows choose one window in the input's OBS properties. On macOS grant Screen Recording
   permission and select the Window Capture method and one window in OBS properties. On Linux
   X11 choose one window in OBS properties. On Linux Wayland/PipeWire the portal can select a
   whole monitor as well as a window: choose **one dedicated window**, not a screen. If needed
   ask your owner agent for `open_chooser`; it revokes previous confirmation.
3. Check the OBS preview. On the combined Wayland capture, type
   `/onair-broadcast confirm-window` in your local owner session and confirm the host card.
   OBS cannot verify that the selected surface is a window. Confirmation belongs to that owner
   runtime and is bound to the input identity/settings. It is revoked by prepare, reopening the
   chooser, changes, explicit revoke or runtime loss. Revocation invalidates confirmation immediately;
   hiding is serialized with the controller's other operations and read back. Box operations recheck
   authorization after the OBS requests and attempt to hide the window if it changed. OBS provides no
   atomic transaction across these requests: a capture may briefly appear, or hiding may fail if OBS
   disconnects. Check OBS and hide the item there if the plugin cannot confirm it hidden.
4. Ask for **discover** again, then use `onair_layout` to put `window` in a box, or use
   `onair_window_share` **show** for the simple shared-window scene. Both refuse an unready
   capture. **Hide** returns to the presentation scene; it does not stop OS capture. Stop the
   capture in OBS when finished; use `/onair-broadcast revoke-window` as appropriate.

## Duplicate captions overlay

Older setups may already have a manually added Browser Source pointing to the overlay URL. Setup
and Boxes name that source and the plugin's intended captions/voice input instead of creating or
using two speaking overlays. In OBS, remove the old input, or rename it **and change its URL away
from the overlay**, then run setup again. Renaming alone leaves a second audio client. The plugin
never deletes a source it did not create. Check OBS preview and audio after the change.

## Optional 3D scene

Create the scene in the same Hermes home as the overlay as described in [scene.md](scene.md).
Set `scene_view: true` in the owner plugin's `presentation` settings and run `onair_obs_setup`
`configure`. The default is false. Configure derives `/scene/` from the configured loopback
`overlay_url`; it refuses non-loopback origins and creates one silent owned Browser Source in
`On Air: 3D scene`, with the existing captions/voice overlay above it. It does not create another
speaking overlay or start an output. The scene becomes an approved whole-scene switch and a box
source. Configuration readback is not render verification: check OBS preview and listen before
use. See [presentation.md](presentation.md) for OBS setup and platform prerequisites.
