# Quickstart: a private rehearsal

You need Hermes 0.22.7 or newer using its Python 3.14 runtime, a configured model for a real
private show, and OBS if you want to preview the composition. See [compatibility](compatibility.md).

1. Install and enable the plugin in your owner Hermes home:

   ```sh
   hermes plugins install https://github.com/HomunculusLabs/hermes-on-air --enable
   ```

2. Open your local Hermes agent. Say: “Set me up to stream. Rehearse privately first.”
   Give it your Twitch channel, public co-host style, public show topic and whether you want
   captions only or voice. Never paste a password or stream key into chat.
3. Review the setup effects. Type the approval command the agent returns yourself and confirm
   the card. The setup preview is private and may use a canned reply.
4. Ask for a private show with approval. Review its effects, type its returned approval command
   and confirm the card. This show uses your owner model and private typed chat, not Twitch.
   Show or skip the drafts. A configured voice provider still needs a listening test.
5. If using OBS, ask the agent to prepare slides and sources. Check the OBS preview and audio
   yourself. For window sharing, pick a single window and follow the confirmation instructions.
6. End the private show when finished. A rehearsal is not proof of a live stream or a real Twitch
   connection. To prepare the separate stage, see [streaming setup](streaming-setup.md). To
   control that stage from the desk, run the dashboard backend in the stage profile; the owner
   profile's dashboard does not discover it. [Views](views.md) has separate owner-profile limits.

Optional: `hermes onair rehearse` is a synthetic offline test with canned chat and no model,
Twitch or voice. It is not the private model show or a live-readiness check.
