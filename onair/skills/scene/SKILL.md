---
name: scene
description: Use when setting up a 3D scene. Preview it safely.
---

# Set up an optional owner scene

1. Ask which Hermes home runs the desired private show or separate stage. In that home run
   `hermes onair scene`; it writes `plugin-data/on-air/scene/` there and fetches pinned, verified
   dependencies. If offline, report the failure. Never replace an existing scene without consent;
   `--force` replaces the whole folder, including owner files, so back it up first. The starter
   contains a procedural room and mascot; edit `scene.json` for colours, camera and scale.
2. For an owner VRM, follow `on-air:avatar`; put a model they may stream at `avatar.vrm` in that
   folder. Check its licence. Only use trusted files and code; the scene shares the overlay origin.
3. For a private, synthetic preview without a show, run `hermes onair scene preview` in the same
   home. Open its printed loopback URL, inspect a screenshot and stop the preview with Ctrl-C.
   It cycles off air, live, generated voice and caption, synthetic chat and alert, privacy, and stop,
   without playing audio or using a real show. Choose `theme` (`cozy-day`, `night-studio`,
   `clean-stage`) and `shot` (`wide`, `medium`, `close`) in `scene.json`, or append
   `?theme=night-studio&shot=close` to the preview URL. For actual show behaviour, preview on the running
   overlay port privately, before putting it in OBS.
4. For an owned switchable view, set `presentation.scene_view: true` in the owner plugin settings,
   then run `onair_obs_setup configure`. This creates a silent scene Browser Source at the fixed
   loopback `/scene/` URL and reuses the one captions/voice overlay. See `docs/views.md` for the
   three boxes and dashboard limitations. Alternatively have the owner add one Browser Source
   by hand, 1920x1080, pointing at `http://127.0.0.1:PORT/scene/`, where PORT is that home's
   configured overlay port. Use `?bg=transparent` for a separate character layer. Do not add
   duplicate audio-enabled overlays.
   Set `quality` to `auto` (default), `high`, or `low`; `?quality=low` overrides it. Auto drops
   to low after sustained slow frames and stays there until reload; inspect `data-quality`. Low
   renders at half pixel ratio without shadows, fill/rim lights or alert particles. If OBS's browser
   has no GPU acceleration, try low quality, a 1280x720 source scaled up, and 30 fps. Measure and
   visually verify the result in OBS: this does not guarantee a particular frame rate.
5. A voice clip drives the mouth; caption-only lines use procedural timing. In a private real-show
   OBS test, listen to the single audio-enabled overlay and adjust `voiceOffsetMs` in `scene.json`
   (default 0, positive delays, range -2000 to 2000 ms) if needed. Verify clear, mute, Stop, privacy
   and disconnection close the mouth; privacy and lost connection immediately hide the scene until
   a fresh live non-private state. The transparent layer becomes fully transparent. Check the OBS
   preview before any live use. A lost connection cannot hide already captured frames; keep the owner's OBS controls ready.

The overlay has no built-in avatar; this optional scene is owner-controlled. Never promise a camera,
face tracking, avatar generation or guest/host video. See docs/scene.md and `on-air:scene-build`.

## Hard rules

Never go live, arm, approve a line, lift privacy or unmute on your own.
