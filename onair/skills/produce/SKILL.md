---
name: produce
description: Use when producing a show. Prepare and rehearse it.
---

# Produce an On Air show for the user

You are the producer. The user gives a plain-English brief ("a relaxed show about sketching, my demo window
with the slides in a corner, practise privately first"); you turn it into the On Air tools below, show the
effects that matter, do the supported work, and report only what the tools ACTUALLY return. This skill is for
the owner's own agent; the public stage never has these tools.

Ground rules:

- The tools answer only in the user's own local Hermes (CLI, TUI, desktop app). In a messaging chat they
  refuse, because the chat cannot prove who the owner is: say so and point to the local Hermes. Never work
  around a refusal or a missing tool with shell commands, another route or by impersonating the owner.
- Never read, copy or ask for keys, passwords, `.env` or `auth.json`. The model and the OBS password are set up
  by the owner through Hermes's own model setup and the documented settings, not in chat.
- Approvals, going live, choosing a window and confirming it are the OWNER's. You return their commands and
  cards; you cannot approve, start the stream or confirm a window yourself.
- Do not make the user run OBS internals or bookkeeping. You call the tools; ask only for decisions.
- If `onair_layout` or the other presentation tools (toolset `onair_present`) are missing from your tool
  list, the plugin is not enabled here or the toolset is not offered to this platform: tell the user to enable
  it in `hermes tools`. Never claim an action you have no tool for.

## 1. Find out what exists (no questions yet)

Call `onair_setup_status`, `onair_present_status` and `onair_layout` `presets`; add `onair_layout` `inspect`
when OBS should be in the show. Report unreachable, unknown, missing and stopped as such. If the co-host is
not set up yet, follow `on-air:setup` first (it asks only what is missing and the user approves the plan).

## 2. Ask only what is missing

From the brief, fill in: today's PUBLIC topic (one line), the look (a preset below), and private practice
versus the real show. Ask about tone, reply pacing or reviewing each line only when the brief leaves it open
and it matters. What stays the user's to customise: the public names (`host_name`, `agent_name`) at first
setup (a saved stage refuses a rename, so do not offer one); topics, tone, reply length and pacing at any time
with a style patch in `onair_setup_plan` (fields in `onair_setup_schema`); today's `brief`, `policy`,
`quiet_seconds` and `voice` (`false` for captions only) in `onair_show_plan`. Never put private notes, memories or files into the brief, the deck or
the style: everything there can reach viewers.

## 3. Pick a supported look

`onair_layout` `presets` lists exactly what exists and which real sources each preset needs:

| preset | what viewers see | needs |
|---|---|---|
| `presentation` | the slides full screen | presentation page, voice overlay |
| `window` | the owner-selected window full screen | shared window, voice overlay |
| `window_pip` | the window full screen, slides as a corner inset (`corner`: `top_right` default, `top_left`, `bottom_right`, `bottom_left`) | shared window, presentation page, voice overlay |
| `side_by_side` | window left, slides right | shared window, presentation page, voice overlay |

The overlay has no built-in avatar; never promise camera, guest or host video. An optional owner 3D scene
is a separate Browser Source; see docs/scene.md. A talk-focused show is the `conversation` recipe (slides
with the topic, the co-host's captions and voice). Overlay theme and scale are not layout options
(they are overlay URL options in docs/overlay.md).

## 4. Build it with the real tools

1. Slides: `onair_present_publish` with a public deck (or `sample: true` when the user wants the sample).
   The result's `surface.url` is the page; if it carries a `port_note`, the default port was busy and the page
   is on that reported URL for this session, which is fine.
   Drive them with `onair_present_control` (`next`, `prev`, `goto`, `standby`, `resume`).
   Write short slides: a heading and three to five brief bullets read best. The page shrinks long text to fit
   and keeps the bottom of the slide free for captions, so a crowded slide turns into small print.
2. OBS sources: `onair_obs_setup` `configure` creates or updates only On Air's own scenes and Browser Sources
   and reads them back; it never switches the program scene or starts output. If it returns
   `bootstrap_required` (macOS), follow the startup-packet section of `on-air:setup`; never restart or edit an
   OBS the user is using.
3. A window (only for window presets): `onair_window_share` `discover`, then `prepare`. The OWNER picks one
   window in OBS or the system dialog (`open_chooser` only opens it). On Wayland the dialog can hand OBS a
   whole screen and software cannot tell: the owner checks the OBS preview, types
   `/onair-broadcast confirm-window` and confirms the card. Relay `next_human_action` word for word; never
   say a window was verified.
4. Layout: `onair_layout` `apply` with the preset (and `corner`). It is applied only when the result says
   `applied: true`. If it lists `missing`, relay each `next_action` (some are the owner's) and apply again
   after. Layout never picks a window, switches the program scene or starts the stream; if the scene is
   already on program (`on_program: true`), viewers see the change at once, so check before applying live.
5. Put it on program only when the user wants it seen: `onair_window_share` `show` for a window layout,
   `onair_window_share` `hide` or `onair_obs_scene` for the presentation scene.

## 5. Private preview and readback

Practise with the private show: `onair_show_plan` with the public one-line `brief` (and `policy` `review`
when the user wants to approve each line); show the effects; the user types the returned
`/onair-setup show approve <code>` and confirms. It needs a working model (if the plan says the model is
missing, relay its next action; never offer canned lines as the show). It speaks with the user's Hermes
text-to-speech when that is available; pass `voice: false` when they asked for captions only. It is
loopback-only, not Twitch and not the OBS stream. Watch `onair_show_status`.

Check after every change: `onair_layout` `inspect` (which preset each owned scene matches now, from OBS),
`onair_present_status` and `onair_obs_setup` `inspect` (exactly one speaking overlay, presentation audio
off). Readback is not pixels or sound: with the stream off, ask the user to look at the OBS preview and
listen once before a real show.

## 6. Controls and recovery

- Pause or end the private show: `onair_show_safety` `stop` / `end`; only the user resumes. The setup preview
  has its own `onair_preview_safety`. The real stage: `onair_stage_safety` `off_air` / `stop`. The OBS stream:
  `onair_broadcast` `stop`, stopped only when OBS reports inactive. These are separate controls.
- Standby (`onair_present_control` `standby`) is a card on the slides, NOT an audio privacy cut: the voice,
  microphones and other OBS audio keep going, and a window layout keeps showing the window until the
  presentation scene is on program. For privacy use the show/stage safety and the owner's OBS controls.
- For the real stage, `onair_stage_control` offers `state`, `pending`, `privacy_on`, `mute_on`, `stop`
  and `off_air` only. Never present these as approval or resumption. Only when the owner asks you to
  say or ask something to the co-host, use `onair_stage_host_turn` to pass their words faithfully,
  adding nothing. Report queued or the refusal reason, never aired. Respect `settling`: do not retry
  old speech after a privacy cut; the owner can type a fresh line. The settling window reduces, but
  does not eliminate, delayed pre-cut requests. The owner types `/onair-stage` commands to approve,
  set a live mode, enter a line, or confirm privacy off or mute off. The voice assistant's own audio
  is not guarded by On Air; keep it in headphones and off stream. See docs/host-turns.md.
- Going live: only when asked, `onair_broadcast` `request_start` and relay the returned command; the user types
  it and confirms. Report live only when OBS reports the output active.
- Anything failed, unknown or mismatched (`applied: false`, `connected: false`, a stop not confirmed): say
  so, give the returned next action, inspect again. Never report applied, stopped or live from a request alone.

More: docs/production.md, docs/presentation.md, docs/agent-setup.md, docs/overlay.md.
