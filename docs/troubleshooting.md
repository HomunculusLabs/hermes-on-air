# Troubleshooting

Report the exact refusal or state. A request is not confirmation. For detailed prerequisites see
[agent setup](agent-setup.md); for OBS details see [presentation](presentation.md).

| Symptom | Check | What to do |
|---|---|---|
| OBS unreachable | OBS process, WebSocket address and secure password | Start or reconnect your intended OBS. Check the owner settings and inspect again. |
| Changed settings seem ignored | Which home and running process? | Restart the affected stage through the normal owner-approved flow; inspect again. |
| Stage not prepared | Stage status lists missing items | Plugin: owner installs in stage home. Model: owner configures it there. Voice: configure stage TTS or choose captions. Isolation, preflight, port, target or process issues: follow each returned next action. |
| Stage voice has no provider | Stage-home voice setup | Configure its provider or save captions only; listen to a real line later. |
| Scene blank or covered | Scene home, assets, URL, privacy and connection | Test the scene privately; inspect OBS preview. Privacy, off air or disconnect can hide it. |
| Model did not answer / model paused | Provider availability, usage limits and billing in the stage account | Nothing from the recognized failed delivery is aired. Check the provider account, wait for quota reset or fix access; see the private stage log for details. Then resume deliberately or retry with a typed host line. |
| Co-host silent | Mode, privacy, approvals, mute, receipts, provider and rundown | Resolve the observed cause. The owner decides whether to resume or approve. The bank can be exhausted. |
| Captions but no sound | Mute, TTS and OBS audio routing | Configure or reconnect the affected source; listen privately. |
| Avatar missing or wrong size | Correct home, file, fallback and framing | Check its license and local preview; see [scene](scene.md). |
| Go-live code expired | Most recent broadcast plan | Ask for a fresh request if still wanted; only the owner types and confirms it. |
| Tools refuse in a messaging chat | Session surface | Use your own local Hermes. Do not work around the owner check. |

A privacy cut is not a universal OBS audio cut. If the desk is unreachable and a broadcast may
still be active, use OBS's own controls and verify its output state. See [desk](desk.md) and
[safety](safety.md). A fresh provider configuration or successful readback is not a sound or
render test. The overlay's LIVE badge is not the OBS streaming indicator.

The desk shows a short category such as “rate limited” rather than the provider's text. After
three consecutive failed turns by default, automatic chat and beats pause; change
`model_failure_pause_after` in the stage plugin settings if needed. A typed host retry is allowed
while model-paused when host turns are enabled, and its successful response waits for approval;
the owner must still resume. These checks match known notice shapes, not every possible message.
