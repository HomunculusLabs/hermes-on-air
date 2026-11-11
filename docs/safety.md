# Safety and its limits

Use this guide to understand the safety controls and their limits before a show.

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
stage plugin settings), or a recognized usage-limit notice, the stage pauses and stops automatic
chat and beat turns. Before pausing, quiet beats back off by 30, 60, 120, then at most 300 seconds
in addition to the quiet interval; typed owner turns may still be tried. The stage does not
resume itself. A timeout is recorded as `timeout`: a stage deadline, not proof the provider returned an error.
The desk says “Paused: the model is not answering”. The owner can resume after investigating,
or submit one new typed host line as a retry while paused if host turns are enabled. A successful
retry is held for approval and does **not** resume the show automatically. A stop or privacy cut
remains in force; neither is undone by this recovery mechanism.

### The show stopped answering

Inspect `mode`, `model_status`, `paused_reason`, `off_air_reason`, `next_action`,
`model_backoff_seconds`, `standby`, and `preflight`. A `model_paused` receipt records the fixed reason
and next action without provider text. A pause after a deadline records `paused_reason=timeout`;
this identifies the last failed turn, not an HTTP status or proof of an outage. Provider failures
and recognized usage limits pause the stage
without an epoch bump or undoing already aired captions. Existing audio clips are revoked. The
presentation page displays the fixed “We'll be right back” card instead of the work view while the
automatic standby is active, provided that page is the visible OBS source. The fixed card remains
on loss of the overlay connection; a trusted live state is needed to restore the underlying view.
The underlying work is not sent during standby. The separate captions
overlay requires standby support to show a card of its own. Check provider availability and
limits; if enabled, try one typed host line and review the result, then choose Approval to resume.
If `off_air_reason` is `runtime_guard`, fix the named preflight refusal first, then while off air
choose **Clear runtime latch**, verify preflight passes, and choose Approval. A real unobserved
reply still latches and cannot air; gateway notices are withheld without being mistaken for replies.
Neither pause nor off air stops OBS or Twitch.

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

Cut in stops the current overlay clip, clears its caption, revokes clips, discards unapproved and
approved co-host lines and fences all old-epoch replies. Queued owner-typed or desk-microphone
host lines carry forward in order; an in-flight owner question is retried once only when no newer
owner line waits. When a model turn was in flight, the stage starts a fresh model session to isolate
the delayed cancellation; the old session's conversational context is not carried forward. Relay
input is dropped and is never retried. Privacy, Stop and off air still discard
queued host input from every origin. A newly accepted owner host turn withdraws waiting unapproved
quiet-beat drafts but does not interrupt approved speech; chat drafts stay behind its reply. Chat
replies are selective; see `inputs` in desk state or private receipts for skipped chat. Cut in does
not arm, unmute or lift privacy. Optional hold-to-talk begins recording only on an owner gesture, sends audio
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
