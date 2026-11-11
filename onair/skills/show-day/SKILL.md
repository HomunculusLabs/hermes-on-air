---
name: show-day
description: Use when running a show day. Check each handoff.
---

# Show day

1. Before: call `onair_setup_status`, `onair_stage_status`, `onair_present_status` and
   `onair_broadcast` `status`. Confirm the owner chose a public brief and rundown. Check OBS
   preview and audio with the owner; check the desired stage is off air, not merely stopped.
   If settings changed, use the owner-approved restart flow. Never ask for the stream key.
2. Start: if the stage is ready, `onair_stage_plan` gives effects and a code; the owner types
   `/onair-setup stage approve <code>` and confirms. The owner chooses Approval at the desk.
   If asked to prepare broadcast, use `onair_broadcast` `request_start`; the owner types the
   returned `/onair-broadcast start <code>` and confirms. Check `onair_broadcast` `status`:
   a stage or overlay LIVE indicator is not proof OBS is streaming.
3. During: call `onair_stage_control` `state` and `onair_stage_control` `pending`. The owner
   reviews and approves or rejects drafts. If a bad line appears, do not repeat it; ask the
   owner to reject it. A line already broadcast cannot be recalled. For a cut call
   `onair_stage_control` `privacy_on`, `onair_stage_control` `mute_on`,
   `onair_stage_control` `stop` or `onair_stage_control` `off_air` as appropriate;
   verify the result. Mute leaves captions; privacy does not control other OBS sources.
   For silence inspect privacy, mute, mode, approvals, guard receipts and the rundown. A beat
   is consumed when offered, not when a line airs. The owner alone resumes or uncovers.
4. End: call `onair_broadcast` `stop` if asked, then check OBS status is inactive. Call
   `onair_stage_safety` `stop` when requested and verify its result. Private-show pause is
   not end: `onair_show_safety` `end` closes that show. If stop is unconfirmed say so.
5. After: review the desk's available receipts with the owner, protect them as private data,
   and note what actually worked or remains untested. Do not claim a show aired from a queue
   or a request alone. See docs/desk.md and docs/seed-bank.md.

Read back: “Channel and public brief? Stage off air and checks clear? OBS preview and sound?
Window safe? Approval operator ready? Privacy and Stop understood? You choose when to go live.”

## Hard rules

Never go live, arm, approve a line, lift privacy or unmute on your own. Never handle a
stream key or ask for a password in chat. Never claim something aired, stopped, rendered
or sounded good without the appropriate observed evidence. A refused owner tool is not
permission to find another way around it. Owner-only decisions belong to the owner.

## Tell the owner

Use short sentences. Say what was observed, what remains unknown, and the next decision.
If a tool refuses, quote its reason and next action. Do not call an unconfirmed result a success.
