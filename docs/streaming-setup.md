# Streaming setup

```text
Twitch chat -> separate stage -> guard -> approval or release -> captions / voice overlay
                                              |                         |
                                         owner desk                 OBS composition
Public seed bank ------------------------------+             slides / scene / window
                                                                    |
                                                               OBS output
```

The stage reads Twitch anonymously and read-only. Your owner agent and public co-host use
separate homes. The stage home is not an operating-system sandbox.

## Set up and rehearse

Follow [Quickstart](quickstart.md) for the owner agent and private show. Setup creates a
separate stage home with a public persona and starter seed bank. The owner installs the plugin
and configures the model in that home; the agent relays the exact missing steps. Review and
replace the starter bank's placeholders. See [agent setup](agent-setup.md) and
[seed bank](seed-bank.md).

For stage voice, configure the stage home's own Hermes text-to-speech provider or use captions
only. The owner must listen to a real line through OBS: provider setup is not proof of sound.
The private show's voice uses the owner home, not proof of the stage home's voice.

## OBS and views

Enable OBS WebSocket and securely configure its password in the owner environment. Ask the agent
to publish slides and configure On Air's OBS sources. Use the returned source URLs: a private
show can use a different port on every start. Check one speaking overlay, preview and audio.
See [presentation](presentation.md) and [overlay](overlay.md).

Arrange Scene, Slides and one owner-selected Window into Main, Sub 1 and Sub 2, or use a simple
look. For a 3D scene, create it in the overlay's home, enable the scene view in owner presentation
settings, and configure OBS again. You can supply a licensed VRM; check its framing privately.
The overlay itself has no built-in avatar. See [views](views.md) and [scene](scene.md).
The owner chooses a single window and checks the preview. On Wayland the owner must also confirm;
the portal can select a whole screen and the plugin cannot verify what was selected. Never show
private material in a share.

## Stage and broadcast

The agent checks stage status and relays every missing prerequisite and next step. Once ready,
it can plan the start; the owner types the returned approval command and confirms the card. The
stage starts off air. The owner chooses Approval at the desk and reviews replies. Privacy, mute,
off air, stop and OBS broadcast are distinct controls. A desk LIVE indicator is not proof that
OBS is streaming. See [desk](desk.md) and [safety](safety.md).

Only when the owner wants to go live should the agent request a broadcast start. The owner types
the returned command and confirms its card. Check OBS output status, preview and audio. A request
or confirmation alone is not evidence viewers saw or heard anything. The owner enters the stream
key directly in OBS, never in agent chat. See [presentation](presentation.md) for details.
