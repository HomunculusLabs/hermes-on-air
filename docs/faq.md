# Frequently asked questions

Use these answers to understand common On Air questions and limitations.

## Does this post to Twitch chat?

No. The stage reads chat anonymously and read-only. Captions and optional speech go to the
local overlay, which OBS can show. See [overlay](overlay.md).

## Does a private rehearsal reach Twitch?

The private show uses typed private input, not Twitch intake. Do not put its overlay on an
active program if you intend it to remain private. A synthetic offline rehearsal is a different
check and uses no model, Twitch or voice. See [quickstart](quickstart.md).

## Can the agent start my stream or approve its own replies?

It can request a broadcast start, but the owner types the returned command and confirms the
card. The owner arms the stage and approves replies in approval mode. Autonomous mode is a
separate owner choice. Safety actions are separate; see [safety](safety.md).

## Can I use my own avatar?

A trusted, licensed VRM can be used in the optional owner scene. Inspect the model, framing and
actual composition before use. There is no built-in face tracking or generated avatar. See
[scene](scene.md).

## Can it hear me continuously?

The stable host-turn path is typed; it is off by default. See [host turns](host-turns.md).

## Does the privacy cut mute my microphone?

No. It does not control microphones or other sources owned by OBS or another application.
Keep their controls available and check them yourself. See [safety](safety.md).

## What happens if the model runs out of quota?

Known failure notices are withheld; the desk shows a short status and pauses after repeated failures.
Check the provider account and see [troubleshooting](troubleshooting.md) before resuming.
