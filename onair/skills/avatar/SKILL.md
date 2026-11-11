---
name: avatar
description: Use when adding a VRM avatar. Verify it locally.
---

# Use an owner VRM

1. Ask the owner for a VRM they own or may use for streaming. They can make one with VRoid Studio
   or choose a CC0 model; inspect the actual licence, attribution, redistribution and streaming
   terms. Do not imply any model is cleared without checking. Do not copy private or unlicensed art.
2. Put the trusted file at `plugin-data/on-air/scene/avatar.vrm` in the SAME Hermes home as the
   desired overlay. The starter detects it and switches from the code-built mascot to the VRM.
   A model that fails to load, or lacks humanoid data, falls back to the mascot automatically:
   `data-avatar` is then `fallback-load-error` (a valid VRM with no `aa`/`blink` expressions still
   performs idle motion and reacts, but has no lip-sync or blink).
3. Frame and pose it with `scene.json`:
   - `avatarFraming`: `bust` (default), `waist`, `full` or `manual`. The first three auto-scale,
     place the model and adjust the shot camera using its bounding box. Built-in rooms hide the desk
     or podium for a VRM; the mascot keeps it. Check face size and headroom at 480x270 for each
     model. `manual` uses `avatarScale`/`avatarPosition` and the configured `camera` and shot.
   - `avatarPose`: `relaxed` (default) lowers the arms from the T-pose into a natural stance with
     slightly bent elbows and softly curled fingers; `tpose` disables that.
4. What is animated out of the box: breathing (spine/chest), slow sway and weight shift, subtle
   head drift, randomised blinking (sometimes double), eye saccades/look-at toward the camera with
   a small wander, spring bones (hair, tail, clothes) via the library's own update, lip-sync from
   the decoded voice envelope (`aa` primary, `ih`/`ee`/`ou`/`oh` blended when present), small
   loudness-scaled head nods while speaking, and preset mood expressions (`happy`, `sad`,
   `surprised`, `relaxed`) with eased blends. Bound standard mood presets take priority; if empty,
   recognized optional custom brow and mouth channels can provide a limited fallback. Custom mouth
   channels are modest during speech and clear with the visemes. A model missing any of these simply skips that part.
   Captions also drive a local mood: `setMood(moodForLine(text))` picks thinking/happy/surprised/
   sad/neutral from punctuation and a short word list; the text is never rendered or sent anywhere.
   Optional character methods (both mascot and VRM): `setMood(name)`, `glance(xNorm, yNorm, ms)`,
   `react('alert'|'greet'|'nod')`, `setPresence('live'|'offair'|'privacy')`.
5. Verify locally: run `hermes onair scene preview` in that home, open the loopback URL privately,
   and screenshot. Check the body data attributes in browser developer tools: `data-avatar`
   (`vrm`/`starter`/`fallback-load-error`), `data-avatar-pose` (`relaxed`/`tpose`/`unavailable`),
   `data-avatar-left-arm-y` and `data-avatar-right-arm-y` (about `-0.96` when the relaxed pose
   applied), `data-avatar-blink`, `data-avatar-aa` and `data-avatar-mouths` while a voiced caption
   plays (all `0.000` after clear/Stop/mute/privacy/disconnect), and `?bg=transparent` shows only
   the avatar with clean alpha. Inspect the face at full size AND at a 480x270 inset; moods need a
   model that ships the preset expressions (many simple models do not — check
   `data-avatar-exprs`). The mascot keeps its own brow/ear/head mood equivalents.
6. Limits: there is no face tracking, camera or avatar generation, and the character has no voice
   of its own; only the existing overlay plays voice. Tune `voiceOffsetMs` (default 0, positive
   delays, range -2000 to 2000 ms) on a real private show. Check framing and streaming rights with
   the owner before use. See docs/scene.md.

## Hard rules

Never go live, arm, approve a line, lift privacy or unmute on your own.
