# Safety and its limits

On Air separates the owner's agent from a public stage home. It checks stage requests for tools
and memory context and guards generated lines before caption and speech output. Approval mode
holds co-host lines for owner review; autonomous mode releases guarded lines without that review.
These checks are fallible. They do not replace a human moderator, Twitch moderation or safe
handling of public content. The separate home is not an OS sandbox.

## Model failures and notices

The stage accepts text only on the gateway's final-delivery path for its current turn, after
Hermes's per-turn hooks identify the same successful, uninterrupted text response and its exact
contents. Missing or inconsistent proof is withheld. The stage also checks the entire delivery
for known Hermes error and notice shapes before splitting it into lines. Known notices are not
drafted, captioned or spoken. The desk shows a short failure category. For setup and private-show
model failures, On Air logs only the step, failure category (usually the exception type), numeric
HTTP status when available and elapsed milliseconds at debug level; it never logs exception text,
credentials, URLs or response bodies there. Hermes itself may log or persist provider detail before
On Air sees it. The per-line guard also blocks known system text. An empty or timed-out turn counts
as a failure.

After `model_failure_pause_after` consecutive failures (default **3**, a positive integer in the
stage plugin settings), the stage pauses and stops automatic chat and beat turns. The desk says
“Paused: the model is not answering”. The owner can resume after investigating, or submit one
new typed host line as a retry while paused if host turns are enabled. A successful retry is held
for approval and does **not** resume the show automatically. A stop or privacy cut remains in
force; neither is undone by this recovery mechanism.

The gateway's final-delivery marker alone does **not** certify that the model succeeded: Hermes
can put a failure notice on that path. On Air also requires matching agent-turn completion and
output hooks. Missing expected hooks or mismatched text fail closed. These hooks and transcript
rows are not cryptographic proof of provider bytes: another plugin could transform an output
before they observe it, and a future Hermes version could change their semantics. The pattern
check is a backstop, not a proof; it can withhold an innocent reply that resembles a notice. Use
a dedicated stage profile, test after upgrades, and keep approval and monitoring in place when
appropriate. See [troubleshooting](troubleshooting.md).

## Owner decisions

The owner approves setup and show start plans, arms the stage, approves or rejects held lines,
chooses autonomous mode, lifts privacy, unmutes, selects and confirms a shared window, and
chooses whether to go live. A broadcast request requires the owner to type the returned command
and confirm a card. The owner handles the stream key directly; never paste it into agent chat.
A tool response confirms only what it says. OBS readback is not a render or sound test; a stage
LIVE label is not OBS streaming status.

## Cuts are not interchangeable

Privacy blocks On Air's output and clears pending work. Mute blocks On Air audio but captions
continue. Stop, off air and stopping the OBS stream are distinct operations. Privacy does not
control other OBS sources or microphones, and audio already sent to a provider cannot be recalled.
If control is lost, use OBS's own controls and verify the output. See [desk](desk.md).

Cut in stops the current overlay clip and clears its caption and pending work; it does not arm,
unmute or lift privacy. Optional hold-to-talk begins recording only on an owner gesture, sends audio
for the owner's configured speech-to-text, and defaults to transcript review. That service may
be cloud-hosted. On Air does not add the owner's microphone to the broadcast; an independent OBS
microphone needs its own privacy control. See [Talk](talk.md).

Open mic is a separate, default-off owner setting and requires a fresh owner click on every
page load. While the stream is open, the desk displays a prominent listening indicator. Each
utterance goes to the owner's configured speech-to-text, which may be a cloud service; it can
be wrong and cannot recall audio already sent. The transcript becomes HOST TEXT for a host
turn, not a caption or the owner's voice on air. Only the co-host's reply can be published,
subject to the usual guard, approval mode, privacy and mute controls. In autonomous mode a
guarded reply can air without owner approval. Open mic is not a wake word or an approval tool.
Privacy, Stop, off air, loss or restart of the stage, and other documented conditions close
capture and discard buffered clips. The model cannot start the microphone or change its mode.

## Data and exposure

The local display pages are not bearer-token authenticated; other local processes can access them.
Control APIs use tokens. Viewer names and line text can be retained in receipts. Model and optional
voice providers receive the content they need to process; protect local data and credentials.
See the [README disclosure](../README.md#disclosure) and [compatibility](compatibility.md).
Alerts can appear immediately in approval mode even when their thank-you waits for approval.

Host relays require approval mode by default. If the owner sets `host_relay_autonomous: true`,
model-relayed text can trigger replies in autonomous mode that air after the automatic guard
without owner approval. Typed host routes are unchanged.
