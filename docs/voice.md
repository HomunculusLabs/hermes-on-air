# Co-host voice

On Air uses Hermes's configured `text_to_speech` tool. The plugin does not talk to a
voice provider itself: Hermes chooses and invokes the configured backend. Viewer-triggered
text that survives the guard and, in approval mode, owner approval can be sent to that backend;
a hosted provider receives that text. The stage captions the line and plays its complete
voice clip through the overlay. A voice provider may charge for synthesis. Read
[Safety](safety.md) for the limits of privacy and cuts.

## Configure the stage profile

In the **stage's** Hermes `config.yaml`, enable On Air voice and select the Hermes TTS
backend. The owner's profile and the stage profile are separate: configuring one does not
configure the other. This example uses Hermes's documented OpenAI TTS settings:

```yaml
plugins:
  entries:
    on-air:
      settings:
        voice: true
voice:
  auto_tts: false
tts:
  provider: openai
  openai:
    model: gpt-4o-mini-tts
    voice: alloy
```

Configure the provider's credentials through Hermes, not in agent chat. Hermes documents
`VOICE_TOOLS_OPENAI_KEY` for this provider. For a different hosted provider use its own
Hermes settings and credentials. Keep `voice.auto_tts: false`: On Air controls its own
output. Set On Air's `voice: false` for captions only.

A local **command-type** provider is also configured in Hermes, for example with Piper
installed separately and a local model selected by the owner:

```yaml
tts:
  provider: local-piper
  providers:
    local-piper:
      type: command
      command: "piper -m /path/to/voice.onnx -f {output_path} < {input_path}"
      output_format: wav
      timeout: 30
```

Hermes supplies `{input_path}` (UTF-8 text) and `{output_path}` (the audio file). This
command is an example, not an installer; adapt it to the installed program. Command providers
run with the local user's permissions, so only configure commands you trust. For current
provider options, credential handling, placeholder quoting and dependencies see
[Hermes Voice & TTS](https://hermes-agent.nousresearch.com/docs/user-guide/features/tts).

## Check voice before a show

Run this command in the **same stage profile and environment** that will run the show:

```sh
hermes onair voice-test
```

It synthesises one fixed short sentence through the same Hermes tool as the stage, reports
success or a failure category, synthesis time, clip duration and audio type, and deletes the
test clip. It does not play audio. The check accepts up to 20 MiB and currently verifies
WAV or MP3 duration; an Ogg or FLAC clip can be playable in the overlay yet fail this check
because its duration cannot currently be measured. A successful test proves synthesis, **not**
that anyone can hear it or that OBS routing is correct.

Preflight records the last result for up to **24 hours** and compares a keyed fingerprint of
the effective `tts` configuration. A changed `tts` configuration or an expired record requires
another test; a recent failed test blocks preflight until corrected, voice is disabled or the
record expires.
The fingerprint contains no raw configuration values or credentials. Changes outside `tts`
(for example an environment credential, a local model file or the remote service itself) may
not invalidate it; test again after changing these. A provider or route can also fail after a
successful test. If preflight failed at stage startup, restart the stage through the normal
owner-approved process after fixing the failure.

## Timing and monitoring

Each approved/released line is synthesised in full before its caption and audio are published;
the next line is synthesised when its turn comes. Provider speed, model startup and the length
of the line affect the delay. Cold local engines may take much longer than warm ones. There is
no guaranteed start time. `voice_ahead` preparation is not supported in this version; no such
setting is available and no extra ahead-of-time provider calls are made.

True streaming playback is not supported: a clip must be complete and revocable before it plays.
Mute stops On Air audio while captions continue; privacy, Stop and cut-in have different scopes
(see [Safety](safety.md)). They cannot recall text already sent to a provider or sound already
heard by the audience.

Audio comes from the On Air overlay page loaded as an **OBS browser source**, not from the
microphone or a separate desktop player. In OBS, enable audio control for that browser source
if needed and use the Audio Mixer / Advanced Audio Properties to monitor it; select an
appropriate monitoring device and verify a real approved line in a private preview. Check both
what you hear and what reaches the stream mix. Avoid monitoring twice or using speakers that
feed the microphone; headphones help. OBS readback and the test command do not prove sound.

## Troubleshooting

- **`voice-test` fails:** check the stage profile's Hermes provider, credentials, dependencies,
  and local command/model; rerun `hermes onair voice-test`. Its category deliberately omits
  provider error text. Consult your provider's own safe diagnostics without exposing secrets.
- **Test passes, no sound:** verify voice is enabled and not muted, the correct overlay is in
  OBS, browser-source audio is controlled in OBS, mixer levels are up, and monitoring/output
  routing and the device are correct. Listen to a real approved line in private preview.
- **Captions but no test pass for Ogg/FLAC:** switch to WAV or MP3 for the diagnostic, or
  recognise that the present duration check cannot verify those formats; do not treat that
  failure as proof the overlay cannot play them.
- **Long gaps:** inspect the reported synthesis seconds; try a warm engine, shorter lines or
  a faster provider in the stage profile, then retest. The next line is not prepared ahead.
- **Unexpected or duplicate speech:** check that only one overlay source is active and that
  Hermes gateway auto-TTS is off. Check OBS monitoring and avoid duplicate audio routes.
