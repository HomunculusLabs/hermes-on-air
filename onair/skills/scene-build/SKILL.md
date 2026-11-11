---
name: scene-build
description: Use when building a 3D room. Design and verify it.
---

# Build an owner streaming environment

1. Ask for a short brief: mood, palette, three to five props, time of day, camera framing, and where
   captions and slides sit. Reserve that space; the face must not collide with text. Confirm which
   home and scene will be edited; back up owner work before changing it.
2. Scaffold and preview as in `on-air:scene`. Pick `cozy-day` for a warm daytime brief,
   `night-studio` for an evening broadcast, or `clean-stage` for slides and minimalism. Set
   `theme` and `shot` (`wide`, `medium`, `close`) in `scene.json`; override palette with, for
   example, `"palette": {"wall": "#394557", "floor": "#514c54", "accent": "#c28172"}`.
   The preview URL also accepts `?theme=night-studio&shot=close`. Captions occupy the lower frame.
   Edit that home's `scene/room.js`: `buildRoom(scene, config, transparent)` remains the wholesale
   room extension point. The exported `themes` registry maps names to `{palette, lights, build}`;
   a builder takes `(scene, config, transparent)`, returns a group and keeps it hidden in transparent
   mode. `lights` names ambient/key/fill/rim colours. The group can supply `userData.setTally(live,
   pulse)`, `userData.animate(t)` and `userData.setPerformer(standing)`; the built-in rooms hide
   foreground furniture when a VRM is loaded. `lighting.js`, `character.js`, `events.js`, `geometry.js` and
   `scene.js` separate the rest. Preserve fail-closed event wiring and avoid remote/inline scripts.
3. Prefer original code-built geometry and procedural textures; few larger props read better than
   many tiny ones. Use a restrained matte palette, a key and fill/ambient light, soft shadows, and
   enough contrast for the character to read at 1080p and in a small sub-box. Keep the scene useful
   with `?bg=transparent`. Owner-provided GLB props are optional custom work: verify the licence and
   streaming rights, and never copy another room or assets without permission.
4. Budget for an OBS Browser Source: high quality caps pixel ratio at 2 and uses a 2048 shadow
   map. Low uses pixel ratio 0.5, no shadow map, no fill/rim lights and no alert particles; auto
   switches to low after sustained slow frames. Aim for 30/60 fps on the owner's machine, avoid per-frame allocations, and measure rather
   than promise a frame rate. Keep code and assets local under the scene folder.
5. Run `hermes onair scene preview`, render a private `/scene/` screenshot with the browser tool,
   LOOK at it at full size and a small sub-box, iterate at least once, then show the owner.
   Voice clips drive mouth openness, with caption-timed procedural fallback when no usable clip
   arrives. In the real private show use only the existing overlay as audio source; tune
   `voiceOffsetMs` in `scene.json` (default 0, positive delays, range -2000 to 2000 ms) while
   listening in OBS. Verify clear/mute/privacy/Stop/disconnect and the real OBS composition before
   anything goes live. Report what was actually inspected; the synthetic preview does not prove OBS
   or a real stage.

## Example: add a theme with one prop

In `room.js`, after the `themes` declaration, reuse the original builder and add a book to its
returned group. `mat` and `box` are already imported from `geometry.js`. Give the new theme its own
palette and choose `"theme": "reading-room"` in `scene.json`:

```js
themes['reading-room'] = {
  ...themes['cozy-day'],
  palette: {wall: '#b8c7bf', floor: '#85796d', accent: '#997b71'},
  build(scene, config, transparent) {
    const room = themes['cozy-day'].build(scene, config, transparent);
    box(room, mat('#78929a'), [.16, .44, .27], [3.35, 3.01, -1.48]);
    return room;
  }
};
```

Run `hermes onair scene preview`, open its loopback URL, capture and inspect a screenshot at full
size and 480×270, then check privacy, off air, live, alert, each intended shot, and transparent mode.

## Troubleshooting

- Blank page: if `vendor/` is missing, back up owner edits and rerun `hermes onair scene` in a
  fresh home or explicitly replace the backed-up scene with `--force`.
- CSP error: use no remote or inline script; keep modules local to the scene.
- Mouth stuck: check whether the fail-closed `events.js` wiring was changed; test clear, Stop and
  disconnect, not just captions.
