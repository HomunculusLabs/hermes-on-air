# Produce a show with On Air

You describe the show; your own Hermes agent produces it with the On Air tools and the `on-air:produce`
skill. You keep the decisions that are yours: approvals, which window to share, going live, and your own
model and OBS settings. For example, in your local Hermes:

> Make a relaxed show about sketching. Put my drawing app on screen with the slides in a corner. Let's
> practise privately first, and I want to approve each reply.

Use your local Hermes (CLI, TUI or desktop app). Discord and other messaging chats refuse these tools,
because a chat cannot prove you are the owner. Never paste keys or passwords into chat: the model is set up
with Hermes's own `hermes model`, and the OBS WebSocket password goes in `ONAIR_OBS_PASSWORD` (see
presentation.md).

## What the agent does, in order

1. **Checks what exists** (setup, presentation page, OBS, the available looks) without changing anything.
   If the co-host is not set up yet it runs the normal setup first (agent-setup.md).
2. **Asks only what is missing**: usually today's public topic, the look, and private practice or the real
   show. The public names are chosen at first setup; topics, tone, reply length and pacing stay yours to
   change at any time, and each show gets its own one-line topic and quiet-time pacing.
3. **Picks a supported look** (below), publishes your slides and configures On Air's own OBS sources.
4. **Practises privately**: the private show runs the co-host using your configured model and serves
   its output on loopback. Model requests go to your configured provider, which may be remote. You approve
   it by typing the command it gives you and confirming the card.
5. **Reads back** what OBS reports after each change and asks you to look at the OBS preview and listen
   once before a real show. Readback is not a picture or a sound check.

## Looks (`onair_layout`)

| preset | what viewers see | needs |
|---|---|---|
| `presentation` | your slides full screen | the presentation page |
| `window` | the one window you chose, full screen | your shared window |
| `window_pip` | your window full screen, slides as a small corner inset | both |
| `side_by_side` | your window on the left, slides on the right | both |

Every look also has the co-host's captions and voice on top, from exactly one overlay (the slides page has its
audio off, so the voice is never doubled). Each box is fitted to your OBS canvas (Settings > Video, Base
(Canvas) Resolution) with its shape kept, so nothing is cut off. `window_pip` takes a `corner`: `top_right`
(default), `top_left`, `bottom_right` or `bottom_left`; the overlay's captions sit at the bottom and its chat on
the right, so pick the corner that keeps them clear.

What the agent sends (tool calls, not shell commands):

```text
onair_layout {"action": "presets"}
onair_layout {"action": "inspect"}
onair_layout {"action": "apply", "preset": "window_pip", "corner": "top_left"}
onair_window_share {"action": "show"}
```

`apply` arranges only On Air's own scene items, then reads them back; it reports `applied: true` only when
OBS's readback matches. It never chooses a window, switches what is on program, starts the stream or touches
your other scenes and sources. If the look's scene is already on program, viewers see the change at once.
To put a look on program the agent uses `onair_window_share` `show` (window looks) or `hide` / `onair_obs_scene`
(the slides).

The overlay has no built-in avatar, and On Air provides no camera, guest or host video. An optional
[owner 3D scene](scene.md) is a separate Browser Source. For a talk-focused show use `presentation` with a
title slide naming the topic while the co-host talks. Overlay colours and size are overlay URL options and OBS
Custom CSS (overlay.md), not layout options.

## Sharing a window

The agent prepares On Air's capture input; **you** pick the window in OBS or the system's share dialog. On
Linux with Wayland that dialog can also hand OBS a whole screen and no software can tell which, so check the
OBS preview, then type `/onair-broadcast confirm-window` in your own Hermes and confirm the card. Until then a
window look is refused with that next step. Re-preparing, reopening the chooser or changing the input in OBS
ends the confirmation.

## Stop and recover

- Pause or end the private show: ask the agent, or use the On Air tab. Only you resume.
- The real stage has its own off-air and stop; the OBS stream has its own stop, confirmed only when OBS
  reports it inactive. These are separate: ask for each one you mean.
- A **standby** card covers the slides only. It is not a privacy cut: the co-host's voice, microphones and other
  OBS audio keep going, and a window look keeps showing the window until the slides scene is on program. For
  privacy pause the show and use OBS's own mixer and scenes.
- If something is reported missing, failed or mismatched (a window not ready, an unknown canvas, OBS
  unreachable, `applied: false`), the agent gives you the reported next step and checks again. It never calls a
  look applied, a stream stopped or a show live from a request alone.

Going live is always yours: the agent can only hand you a one-time `/onair-broadcast start <code>` to type and
confirm.

More detail: agent-setup.md (setup and the private show), presentation.md (slides, OBS and window sharing),
overlay.md (captions and voice page).
