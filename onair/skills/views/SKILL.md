---
name: views
description: Use when arranging stream views. Check OBS readback.
---

# Views

1. Call `onair_present_status`, `onair_obs_setup` `inspect`, and `onair_layout` `boxes`.
   Unknown or unreachable OBS is not a successful change. Publish slides with
   `onair_present_publish`; call `onair_obs_setup` `configure` and inspect again.
2. Main, Sub 1 and Sub 2 hold Scene, Slides, or one owner-selected Window. Subs may be empty;
   Main cannot. Moving a source moves it out of its old box. The overlay is above the boxes.
   Use these exact examples as needed:

```json
onair_layout {"action":"set","main":"slides","sub1":"scene","sub2":"empty","arrangement":"side"}
onair_layout {"action":"put","slot":"sub2","source":"window"}
onair_layout {"action":"swap","a":"main","b":"sub1"}
onair_layout {"action":"arrangement","arrangement":"stack","inset_corner":"bottom-right"}
```

   A scene needs the optional scene and `scene_view` enabled under `presentation`; configure
   OBS again. Stack defaults to bottom-right; top-right, top-left and bottom-left are optional
   inset corners on `set` or `arrangement`. Insets can cover arbitrary content; the default
   caption band is reserved, but custom captions may differ. Report geometry bounds and fitted
   content, canvas and overlay order from `boxes`; null content means source dimensions or fit
   cannot be computed. Report arrangement_source and inset_corner_source honestly: stored is
   not OBS geometry readback. A successful move names the source, origin and destination.
   A scene already on program changes immediately. `applied: true` and readback
   describe OBS state, not rendered pixels; inspect the preview. A failed apply may be partial.
3. For simple looks call `onair_layout` `presets` then `onair_layout` `apply` with a returned
   preset. For a window call `onair_window_share` `discover` then `onair_window_share` `prepare`.
   The owner selects a single window. On Wayland the owner checks the preview, types
   `/onair-broadcast confirm-window` and confirms the card. Discover again before showing it.
   If refused, relay `next_human_action`; do not claim software verified the portal selection.
   Hide does not stop OS capture. The owner stops capture in OBS.
4. When asked to switch the prepared Boxes scene, call:

```json
onair_obs_scene {"scene":"On Air: Boxes"}
```

   Inspect program and preview. The boxes cannot share arbitrary URLs, remote computers,
   cameras or third-party audio. See docs/views.md for settings and dashboard restrictions.

## Hard rules

Never go live, arm, approve a line, lift privacy or unmute on your own. Never handle a
stream key or ask for a password in chat. Never claim something aired, stopped, rendered
or sounded good without the appropriate observed evidence. A refused owner tool is not
permission to find another way around it. Owner-only decisions belong to the owner.

## Tell the owner

Use short sentences. Say what was observed, what remains unknown, and the next decision.
If a tool refuses, quote its reason and next action. Do not call an unconfirmed result a success.
