# Talk to your co-host

Use this guide to send host lines, use the microphone and cut in safely.

On Air can take a typed host line, a short microphone clip, or a line relayed by your own
agent. The co-host's answer follows the normal guard, optional approval, privacy and mute
controls. **Queued** means accepted for later, not necessarily published or heard.
For co-host speech configuration and the voice test, see [Voice](voice.md).

## Turn it on

Enable host turns in the stage profile:

```yaml
plugins:
  entries:
    on-air:
      settings:
        host_turns: true
```

In the profile serving the dashboard desk, enable the microphone separately:

```yaml
plugins:
  entries:
    on-air:
      settings:
        host_mic: true
        host_mic_open: true   # optional: offer the owner an open-mic switch
        host_mic_review: true
        host_cut_in_on_talk: true
```

The microphone and host turns are off by default. Restart the stage through the normal
owner-approved controls, then open the dashboard's **On Air** page. **Talk** opens when host
turns are enabled and the show is armed; it can also be expanded manually.
To reach the real stage, the dashboard backend must run in that stage's profile; the owner's
separate profile does not discover it. The desk-profile microphone settings and speech-to-text
are therefore the stage profile's settings in this arrangement.
Configure the dashboard profile's own Hermes speech-to-text with `hermes tools`. Browser
WebM clips require `ffprobe` from FFmpeg on the dashboard computer. In that same dashboard
profile, `host_mic_timeout_seconds` sets the transcription timeout: default 20 seconds,
integer range 5–120. Invalid values use the default. The first use of a local model can
take longer; increase the timeout within that range if needed.

## Open mic

With `host_mic_open: true` and `host_mic: true`, the owner can switch on **Open mic**
in Talk. It is off by default and after every page load. A red **Mic open: listening**
indicator and level meter remain visible while the browser has the microphone. Switch it off
at any time. Each detected utterance goes directly to your configured speech-to-text provider,
which may be cloud based, and its transcript enters the host queue without review. The review
lock still applies to hold-to-talk. Speech-to-text errors can produce odd HOST TEXT, but the
plugin never puts the owner's speech itself on air: only guarded co-host replies can air.
Approval mode still requires you to approve each reply; autonomous mode releases replies after
the guard. This is not a wake word or hands-free approval. Wear headphones.

Stay quiet for the first second while the browser measures the noise floor. Detection is a
simple level threshold, not a speech recognizer: background noise, speakers, or a soft voice
can trigger or miss a segment. Approximately 150 ms above the threshold starts an utterance,
with about 300 ms of buffered audio included; 800 ms of silence ends it. Segments with less
than 400 ms of detected speech are discarded; a long segment is cut at 28 seconds. A single
upload/transcription runs at a time; up to two waiting segments are kept in order. If that
queue fills, the oldest waiting segment is dropped and the desk displays a note. The desk uses
`X-OnAir-Capture: open` and `X-OnAir-Mode: send` on the existing authenticated microphone
route; the server rejects that capture header unless `host_mic_open` is enabled. It does not
change the owner's hold-to-talk release preference.

The microphone closes when privacy or Stop is requested, the show stops or goes off air, the
desk loses the stage or its boot/epoch changes, the tab is hidden for 60 seconds, the device
ends, or three uploads in a row are refused. A recording in progress and waiting recordings
are discarded. Audio already sent to a provider cannot be recalled.

## Type, talk, or relay

Type into **Host line** and select **Send to co-host**. Alternatively, hold **Hold to talk**
while speaking and release to transcribe, or hold **V** with Talk focused outside a text field.
The **Keyboard shortcuts** checkbox also enables C and V outside Talk for this page session;
it is off by default. A click alternative starts on the first click and transcribes on the second.
The browser asks for microphone permission; **Listening** and a timer show active capture.
Release, a second click, or the approximately 30-second cap closes the microphone. Loss of
focus, privacy, an unrelated cut or a changed show state discards a recording. No microphone
stream is kept between clips.

You may instead ask your own local agent to relay your words with `onair_stage_host_turn`.
Relays use the host-turn intake and require approval mode by default; a settling interval
also applies after startup and safety loosening. If you enable `host_relay_autonomous`,
relayed text can lead to guarded replies without owner approval. See [Host turns](host-turns.md).

## Review or Direct

**Review before sending** puts the transcript into Host line for you to check and edit before
sending. **Send as soon as I release** is Direct: the transcript enters the host queue without
that review. The choice is saved per Hermes home. `host_mic_review: true` is the default and
locks Review; set it to `false` only if you want the owner to choose either option. Speech
recognition can be wrong, so keep Review on unless you accept that risk.

## Cut in

Select **Cut in** in Talk or press **C** when Talk has focus outside text fields (or when
**Keyboard shortcuts** is enabled); the Desktop Host tab and compact
pane also have a Cut in button. The owner may type `/onair-stage cut` in the local session.
The action clears the caption, asks the overlay player to stop, revokes clips and drops pending
and approved co-host lines. It fences the previous epoch, so an old reply cannot air. Owner-typed
and desk-microphone host lines already queued carry into the new epoch in order. An in-flight
owner host question is re-asked once only if no newer owner line is queued; relayed host lines
are dropped and never re-asked. When a turn was in flight, a cut starts a fresh model session to
isolate delayed cancellation; the old session's conversational context is not carried forward.
A privacy cut, Stop or off air still discards all queued host lines.
A new owner host turn withdraws unapproved quiet-beat drafts and puts its reply ahead of waiting
chat drafts, but does not interrupt approved speech. Chat replies are selective; see `inputs` in
desk state or the private receipts for skips. Cut in cannot arm, approve, unmute or lift privacy.
It is refused while the stage is private, paused or off air, or if its state is stale.

With `host_cut_in_on_talk: true` (the default), pressing Hold to talk cuts an airing line
first. In Open mic, about 400 ms of speech above the voice threshold while a line is airing
requests the cut; wear headphones to reduce accidental cuts from the co-host's own voice.
"Airing" here means her caption and voice are already out. While an approved line's voice
is still being prepared, speaking does not cut it: the line is said when it is ready. The
**Cut in** button cuts at any time.
When nothing is airing, speaking does not itself cut the current turn. Once its transcript is
accepted as a host turn, unapproved beat drafts are withdrawn as described above. The stage
atomically checks that a line is still airing before performing this
automatic cut; if it has already ended, it leaves the waiting work alone.
Recording starts only after that same press receives confirmation and rechecks the
returned stage boot and epoch. Binding to that post-cut state is safe because the owner
initiated that cut; an unrelated state change is not adopted. If the cut is refused, capture
does not start. Set the option to `false` to leave the co-host line airing while you record.
A cut or restart after recording invalidates the original binding; record a new line instead
of retrying it under a new show state.

## What people hear and where data goes

On Air sends **only the co-host**, not your microphone, to its overlay. If you want viewers
to hear you, add your microphone to OBS yourself. Wear headphones to avoid picking up the
co-host and causing feedback. The On Air privacy control does not mute an independently
configured OBS microphone.

The clip goes to your own Hermes speech-to-text configuration, which may use a cloud provider.
It is stored briefly in a private temporary file and deleted after transcription; audio
already sent to a provider cannot be recalled. A sent host line can be retained in the same
private stage receipts as a typed line. Expect a pause for recording, transcription, any
current turn, model generation and optional approval. There is no guaranteed response time.
This is not a phone call: even with Open mic, the co-host takes turns and may require approval.

## Troubleshooting

- **Speech-to-text unavailable:** configure the dashboard profile's provider with `hermes tools`
  and try again. The plugin does not install or download a model for you.
- **Invalid or unsupported audio:** install `ffprobe` from FFmpeg for browser WebM clips, then
  retry. Clips must be nonempty, at most 30 seconds and at most 2 MiB.
- **Permission denied:** permit microphone access for the dashboard in your browser and retry.
  Hold-to-talk closes the microphone after a take; Open mic stays open until switched or cut off.
- **Stale or changed show:** privacy, Stop, a cut, or a restart can invalidate the original
  epoch or boot. Restore the stage through owner controls if appropriate, then record or type
  a new line; do not reuse a stale transcript.
- **No co-host audio:** check the overlay and your configured Hermes text-to-speech, and listen
  to the OBS monitor. On Air's microphone input does not add your voice to OBS.
