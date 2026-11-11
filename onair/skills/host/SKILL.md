---
name: host
description: Use when talking to the co-host. Send a host line.
---

# Host turns

1. Host turns are off by default. The owner enables `host_turns` in the stage settings and
   restarts that stage through the normal owner-approved flow. Use `onair_stage_status` to
   inspect it; a missing or unknown state is not proof that it is ready.
2. The owner can type `/onair-stage say TEXT` in the local owner session or use
   `hermes onair host "What should we build next?"` in the stage home. The authenticated desk
   has Host line and an optional push-to-talk mic. Enable `host_mic` in the desk profile and
   `host_turns` in the stage. The desk currently discovers only shows in its own profile,
   so controlling a separate stage requires a dashboard backend in that stage profile.
   Its `host_mic_timeout_seconds` defaults to 20 (integer 5–120); configure speech-to-text
   with `hermes tools` in that dashboard profile. `host_mic_review` defaults to true and locks Review before sending.
   False lets the owner choose Review or Send as soon as I release; Review remains the default.
   `host_mic_open` is a separate default-false setting that offers Open mic. It starts off on
   every page load; only the owner may click it on. A red listening indicator means the stream
   is open. Every detected utterance is sent to the owner's speech-to-text, which may use a
   cloud provider, and becomes HOST TEXT without transcript review. It cannot directly put the
   owner's audio or text on air. The co-host reply still follows guard and approval rules.
   Wear headphones: speech during an airing line can request a cut. Privacy, Stop, off air,
   stage loss or restart and the documented timeout/refusal conditions close capture.
3. The co-host reply follows guard and approval rules. Queued means accepted for later, not aired.
   A stage that is off air, paused or private refuses host turns. After a cut, queued owner-typed and desk-microphone lines carry over; relayed lines are dropped.
   Re-record a discarded capture rather than sending a buffered transcript under a new epoch. The owner can type `/onair-stage cut`.
   `onair_stage_control` `cut_in` is a safe-direction action; `pending` inspects waiting lines.
   On the dashboard's On Air page, Talk offers Cut in and hold-to-talk. `host_cut_in_on_talk`
   defaults on and cuts an airing line before that same press starts recording; if refused, no
   recording starts. Only the owner starts capture or changes its release mode.
4. When the owner asks, use `onair_stage_host_turn` to relay only their words. It queues, not airs.
   Relays require approval mode by default. If the owner enables `host_relay_autonomous`,
   relays in autonomous mode can lead to guarded replies airing without owner approval.
   Relay settling applies after startup, arming, resuming, privacy off, and switching to autonomous.
5. Only the owner can type `/onair-stage privacy off` or `/onair-stage mute off` and confirm
   the choice card. If no card is available, nothing changes. See docs/host-mic.md and docs/host-turns.md.

## Hard rules

Never turn on the microphone or change its capture or release mode; only the owner can do so.
Never go live, arm, approve a line, lift privacy or unmute on your own. Never handle a
stream key or ask for a password in chat. Never claim something aired, stopped, rendered
or sounded good without the appropriate observed evidence. A refused owner tool is not
permission to find another way around it. Owner-only decisions belong to the owner.

## Tell the owner

Use short sentences. Say what was observed, what remains unknown, and the next decision.
If a tool refuses, quote its reason and next action. Do not call an unconfirmed result a success.
