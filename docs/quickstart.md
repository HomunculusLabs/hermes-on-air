# Quickstart: a private rehearsal

Use this guide to install On Air and run your first private rehearsal.

You need Hermes 0.22.7 or newer using its Python 3.14 runtime, a configured model for a real
private show, and OBS if you want to preview the composition. See [compatibility](compatibility.md).

1. In an interactive terminal, run `hermes setup` to configure a model provider for your owner
   home, if you have not already. Keep credentials out of chat. Install and enable the plugin:

   ```sh
   hermes plugins install https://github.com/HomunculusLabs/hermes-on-air --enable
   ```

   Start a new Hermes session after installation: plugin tools are offered from the next session.
   If a gateway was already running, restart that gateway if the new session cannot reach the model.

2. Open your local Hermes agent. Say: “Set me up to stream. Rehearse privately first.”
   Give it your Twitch channel, public co-host style, public show topic and whether you want
   captions only or voice. Never paste a password or stream key into chat.
3. Review the setup effects. Type the approval command the agent returns yourself and confirm
   the card. The setup preview is private and may use a canned reply.
   If the default stage folder is unsuitable, the plan shows a safe alternative and why it was chosen;
   you can instead ask your agent to use a specific new absolute folder under your home.
4. Ask for a private show with approval. Review its effects, type its returned approval command
   and confirm the card. This show uses your owner model and private typed chat, not Twitch.
   Show or skip the drafts. A configured voice provider still needs a listening test; see [Voice](voice.md).
5. If using OBS, ask the agent to prepare slides and sources. Check the OBS preview and audio
   yourself. For window sharing, pick a single window and follow the confirmation instructions.
6. End the private show when finished. A rehearsal is not proof of a live stream or a real Twitch
   connection. To prepare the separate stage, see [streaming setup](streaming-setup.md). To
   control that stage from the desk, run the dashboard backend in the stage profile; the owner
   profile's dashboard does not discover it. [Views](views.md) has separate owner-profile limits.

Optional: `hermes onair rehearse` is a synthetic offline test with canned chat and no model,
Twitch or voice. It is not the private model show or a live-readiness check.

### Model connection checks

Status reports `untested` when a client exists but has not successfully answered in this process
and runtime context, `ready` after it has, and `unavailable` when no client is available or its
last check failed. `usable_here` is true only for `ready`; a past success does not guarantee the
next request will succeed. Status itself does not call the model.

If you choose the model for setup, planning makes at most one tiny connection test when the
connection is not ready, before issuing an approval code or creating a stage folder; your provider
may charge for it. A successful check is cached for that client and runtime context in this process.
The approved preview makes its own request. A private show plan also checks an untested connection.
If planning and approval run in different processes or runtime contexts, the approving process must
check again. Hermes may retry or route requests according to its own provider policy. A timed-out
check can still be finishing in the host; On Air does not launch another for that client and runtime
context while the check is in flight.
