---
name: first-stream
description: Use when starting a first stream. Rehearse off air.
---

# First stream

1. In the owner's local Hermes, call `onair_setup_status` and `onair_setup_schema` with `{}`.
   Ask only for missing channel and public persona choices. Chat is anonymous and read-only.
   Call `onair_setup_plan` with those answers and show its effects. The owner types the
   returned `/onair-setup approve <code>` and confirms the card. Call `onair_setup_progress`
   until the job is done; a failure needs its returned next action, not a success claim.
2. For a real stage, call `onair_stage_status` with `{}`. For each missing prerequisite relay
   its exact next action: the owner installs the plugin and configures the stage model in its
   own home. If voice is enabled but no provider is configured, the owner configures stage TTS
   or chooses captions only. For synthesis, run `hermes onair voice-test` in the stage profile;
   see docs/voice.md. Neither configuration nor this test is a listening test. Call
   `onair_stage_plan` only when startable; show its effects. The owner types
   `/onair-setup stage approve <code>` and confirms. A start is not an arm; verify mode is off air.
3. If the owner wants the sample, publish it with:

```json
onair_present_publish {"sample":true}
```

   Then call `onair_obs_setup` `configure`. Use `onair_obs_setup` `inspect` and `onair_layout` `inspect`
   to read back OBS. A readback does not prove pixels or sound. For a window or 3D scene,
   follow the views skill. Never promise camera, guest or host video.
4. Call `onair_show_plan` with a public brief, review policy and a voice choice, for example:

```json
onair_show_plan {"brief":"A private practice show","policy":"review","voice":false}
```

   Show the effects. The owner types `/onair-setup show approve <code>` and confirms. Use
   `onair_show_status` and `onair_show_chat` for a private typed test. This is not Twitch chat.
   Ask the owner to inspect OBS preview and listen to one real voice line if voice is desired.
   `onair_show_safety` `end` ends the private show; verify the returned state.
5. Before a real show read back: channel, persona and public content; isolated stage and preflight;
   approval mode and operator; OBS preview and single overlay; voice and audio routing; privacy and
   Stop; window choice; stream-key ownership. Only if requested, call `onair_broadcast`
   `request_start`; relay its returned command. The owner types `/onair-broadcast start <code>`
   and confirms. Report live only when OBS reports active; stage mode is not OBS output.

If a tool refuses messaging surfaces, use a local owner session, not a workaround. If settings
changed, the stage may need a normal owner-approved restart to load them. See docs/quickstart.md.

## Hard rules

Never go live, arm, approve a line, lift privacy or unmute on your own. Never handle a
stream key or ask for a password in chat. Never claim something aired, stopped, rendered
or sounded good without the appropriate observed evidence. A refused owner tool is not
permission to find another way around it. Owner-only decisions belong to the owner.

## Tell the owner

Use short sentences. Say what was observed, what remains unknown, and the next decision.
If a tool refuses, quote its reason and next action. Do not call an unconfirmed result a success.
