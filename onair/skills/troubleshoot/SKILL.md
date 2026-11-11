---
name: troubleshoot
description: Use when On Air fails. Find the next safe check.
---

# Troubleshoot

1. Call `onair_setup_status`, `onair_stage_status`, `onair_present_status` and
   `onair_broadcast` `status` where applicable. Preserve unknown as unknown. For each refusal,
   give the actual detail and next action. Messaging chats cannot prove owner identity; use
   local owner Hermes rather than bypassing a refusal.
2. Use this symptom → check → next action table:

| Symptom | Check and next action |
|---|---|
| OBS unreachable | Inspect OBS status, its WebSocket address and whether OBS is running; owner configures its password securely. |
| Settings ignored | Confirm the correct home; changes may need an owner-approved stage restart. |
| Stage not prepared | Read each missing item: plugin needs owner installation; model needs owner configuration; voice needs stage TTS or captions only; isolation, preflight, occupied port, target and running-process failures need their reported fix. Never bypass isolation. |
| Voice on, no provider | Owner configures TTS in the stage home or chooses captions only; then plan again. |
| Blank scene or curtain | Check correct home, scene assets and loopback URL; privacy, off-air and disconnection can hide the scene. Check OBS preview. |
| Co-host silent | Check mode, privacy, mute, pending approvals, receipts, provider and seed-bank state. A consumed or exhausted rundown may not offer another beat. |
| Captions but no sound | Check mute, TTS and OBS audio routing. Ask owner to listen; a configured provider is not a sound check. |
| Avatar missing or framed badly | Check the licensed VRM and correct scene home; use the avatar skill to check fallback and framing in preview. |
| Go-live code expired | Request a fresh plan only if the owner still wants it; relay the new code. The owner types and confirms it. |

3. Use `onair_stage_control` `state` and `onair_stage_control` `pending` for a running stage.
   If the desk is unreachable, state is unknown. Ask the owner to stop OBS output if needed;
   a privacy cut does not silence every OBS source. For an unconfirmed stop, say so and follow
   the returned recovery. See docs/troubleshooting.md.

## Hard rules

Never go live, arm, approve a line, lift privacy or unmute on your own. Never handle a
stream key or ask for a password in chat. Never claim something aired, stopped, rendered
or sounded good without the appropriate observed evidence. A refused owner tool is not
permission to find another way around it. Owner-only decisions belong to the owner.

## Tell the owner

Use short sentences. Say what was observed, what remains unknown, and the next decision.
If a tool refuses, quote its reason and next action. Do not call an unconfirmed result a success.
