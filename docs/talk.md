# Talk to your co-host

On Air can take a typed host line, a short microphone clip, or a line relayed by your own
agent. The co-host's answer follows the normal guard, optional approval, privacy and mute
controls. **Queued** means accepted for later, not necessarily published or heard.

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
        host_mic_review: true
        host_cut_in_on_talk: true
```

The microphone and host turns are off by default. Restart the stage through the normal
owner-approved controls, then open the dashboard's **On Air** page and expand **Talk**.
To reach the real stage, the dashboard backend must run in that stage's profile; the owner's
separate profile does not discover it. The desk-profile microphone settings and speech-to-text
are therefore the stage profile's settings in this arrangement.
Configure the dashboard profile's own Hermes speech-to-text with `hermes tools`. Browser
WebM clips require `ffprobe` from FFmpeg on the dashboard computer. In that same dashboard
profile, `host_mic_timeout_seconds` sets the transcription timeout: default 20 seconds,
integer range 5–120. Invalid values use the default. The first use of a local model can
take longer; increase the timeout within that range if needed.

## Type, talk, or relay

Type into **Host line** and select **Send to co-host**. Alternatively, hold **Hold to talk**
while speaking and release to transcribe, or hold **V** while the desk has focus outside a
text field. A click alternative starts on the first click and transcribes on the second.
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

Select **Cut in** in Talk or press **C** outside text fields; the Desktop Host tab and compact
pane also have a Cut in button. The owner may type `/onair-stage cut` in the local session.
The action clears the caption, asks the overlay player to stop and discards old pending work;
it cannot arm, approve, unmute or lift privacy. It is refused while the stage is private,
paused or off air, or if its state is stale.

With `host_cut_in_on_talk: true` (the default), pressing Hold to talk cuts an airing line
first. Recording starts only after that same press receives confirmation and rechecks the
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
This is push-to-talk, not hands-free conversation or a phone call.

## Troubleshooting

- **Speech-to-text unavailable:** configure the dashboard profile's provider with `hermes tools`
  and try again. The plugin does not install or download a model for you.
- **Invalid or unsupported audio:** install `ffprobe` from FFmpeg for browser WebM clips, then
  retry. Clips must be nonempty, at most 30 seconds and at most 2 MiB.
- **Permission denied:** permit microphone access for the dashboard in your browser and retry.
  The control does not keep the microphone open after a take.
- **Stale or changed show:** privacy, Stop, a cut, or a restart can invalidate the original
  epoch or boot. Restore the stage through owner controls if appropriate, then record or type
  a new line; do not reuse a stale transcript.
- **No co-host audio:** check the overlay and your configured Hermes text-to-speech, and listen
  to the OBS monitor. On Air's microphone input does not add your voice to OBS.
