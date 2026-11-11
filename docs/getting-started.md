# Getting started with On Air

Nothing here contacts Twitch, a model provider or a TTS service until you configure them
yourself, and nothing arms a show for you. Installing the plugin and preparing Hermes dependencies
may use the network.

You need [Hermes Agent](https://hermes-agent.nousresearch.com/docs) installed, with `hermes`
on your `PATH`.

## The two homes

Hermes keeps everything (config, `.env`, persona, memory, credentials) in one directory, its
*home*. `HERMES_HOME` selects it; without it, your normal home is used.

- **Your current home** is where you run Hermes today. On Air only needs to be installed there
  so the `hermes onair` commands exist. Its stream platform stays disabled there.
- **The stage home** is a separate, new directory that only the stream uses. Its configuration
  turns off memory, the user profile, context files and tools for the stream, and its persona is
  a short public one. Nothing is copied into it from your current home: no model, key, auth
  store, memory or persona. It is a separate Hermes configuration, not a sandbox. Hermes still
  runs as your user, so the operating system does not stop it from reading your files.

`setup` and `preflight` print which home they act on. `hermes onair preflight` checks the home it
runs in, so prefix stage commands with `HERMES_HOME="$STAGE"`.

## Quickstart

<!-- quickstart:begin -->
```bash
# The plugin's Git URL (or your fork's).
ONAIR_SRC=https://github.com/HomunculusLabs/hermes-on-air

# 1. Install On Air in your current home, so the `hermes onair` commands exist.
hermes plugins install "$ONAIR_SRC" --enable

# 2. Offline first success: synthetic chat through the real gate, guard and stage controller.
#    No Twitch, no model, no TTS, no network. Not a readiness check.
hermes onair rehearse

# 3. Create a NEW stage home. The directory must not exist yet.
STAGE="$HOME/onair-stage"
hermes onair setup --create-home "$STAGE" --channel your_channel --host-name "Your Name" --yes

# 4. Install On Air into the stage home and rehearse there too.
HERMES_HOME="$STAGE" hermes plugins install "$ONAIR_SRC" --enable
HERMES_HOME="$STAGE" hermes onair rehearse

# 5. Check the stage home's isolation.
HERMES_HOME="$STAGE" hermes onair preflight

# 6. Choose a model provider for the stage home (interactive).
HERMES_HOME="$STAGE" hermes model  # interactive

# 7. When you are ready to go live, start the stage gateway yourself.
HERMES_HOME="$STAGE" hermes gateway run  # when ready
```
<!-- quickstart:end -->

### What each step does

1. **Install.** `hermes plugins install` downloads the plugin and `--enable` turns it on. Hermes
   may prepare an isolated runtime for it. In your current home this adds the `hermes onair`
   commands and On Air's request gate, which acts only on the `on_air` platform. No stream
   starts there, because `gateway.platforms.on_air` is not enabled in that home.
2. **Rehearse.** `hermes onair rehearse` arms a private, in-memory stage into approval mode and
   feeds it scripted chat: a newcomer, real questions, a decode request, a forged host line and
   a say-request. A canned stand-in agent answers, and one of its drafts deliberately repeats
   the decoded bait. The real guard withholds that line, a synthetic desk operator approves the
   rest, and the approved lines air as captions. Every line of output is labelled as synthetic.
   It opens no listener and writes no files (its receipts stay in memory). It exits within
   `--duration` seconds (default 30). Ctrl-C, SIGTERM and SIGHUP stop it early through the same
   clean shutdown.
3. **Create the stage home.** `--create-home` writes five files into the new directory:
   - `config.yaml`: On Air enabled, its settings, `platform_toolsets.on_air: [no_mcp]`,
     `skip_context_files: true`, memory and user profile off, no memory provider, the
     `compressor` context engine and `voice.auto_tts: false`;
   - `.env`: only `ONAIR_ALLOWED_USERS=onair-stage`;
   - `SOUL.md`: a short public persona for the co-host;
   - `seed_bank.yaml`: starter show content for quiet moments (or pass `--seed-bank` with an
     absolute path to your own);
   - `.onair-stage-home`: a marker.

   It reads nothing from your current home and copies no model, key, auth store, memory or
   persona. It refuses, without changing anything, a path that already exists (even an empty
   directory or a dangling symlink), a parent reached through a symlink or not owned by you,
   and any path inside or around an existing Hermes home: the current one and your default
   `~/.hermes`, including its `profiles/`. Without a terminal it never prompts:
   pass `--channel` (and anything else you want) with `--yes`. Other options: `--agent-name`,
   `--profanity mask|block|allow`, `--never-say` (repeatable), `--overlay-port` and
   `--voice captions|tts`.
4. **Install into the stage home.** The stage home needs its own copy of the plugin. The
   generated `config.yaml` already lists On Air as enabled. If Hermes is a self-managed source
   install (what the official installer sets up), the first Hermes command you run with a new
   `HERMES_HOME` outside `~/.hermes` prepares a dependency environment for that home; it prints
   `hermes: preparing dependencies for this data root`. That is how such an install treats any
   separate home, not something On Air adds; other kinds of Hermes installation may not do it.
   It needs network access the first time and disk space; later commands reuse it. In one
   measured Linux run (Hermes 0.22.7 on Python 3.14, fast network) that first `plugins install`
   took about a minute and the stage home grew to about 790 MiB, mostly its dependency
   environment and download cache.
5. **Preflight.** It checks the stage home's isolation and should pass with one warning,
   `persona`: Hermes always loads `SOUL.md`, and preflight cannot judge what you write there.
   It does **not** check the model, credentials, the Twitch channel or voice, and it arms
   nothing. If a check fails, the output lists the next action for each failure.
6. **Model.** The stage home starts with no model. `hermes model` picks the provider and model
   for that home only. Use a chat-completions, Anthropic, Bedrock or Responses provider; a
   `codex_app_server` runtime fails preflight because its turns bypass On Air's gate.
7. **Go live.** `hermes gateway run` runs the stage gateway in the foreground. The show stays
   OFF AIR until you arm it into approval from the desk in the Hermes dashboard, and every
   line waits for your approval until you switch to autonomous mode yourself.

## Optional: your channel and voice

- **Channel.** Change `twitch_channel` under `plugins.entries.on-air.settings` in
  `$STAGE/config.yaml`. On Air reads chat anonymously and read-only.
- **Voice.** Captions only is a complete, supported mode, and it is the default for a new stage
  home. To speak lines aloud, create the home with `--voice tts` or set `voice: true` in the
  same settings block, then run `HERMES_HOME="$STAGE" hermes tools` to configure a text-to-speech
  provider for the stage home. Real-stage planning requires a configured provider when voice is on;
  otherwise configure one or save captions only. Configuration is not a sound test: listen to one
  line before going live. Preflight still warns `tts`, because it cannot verify TTS or audio routing.
- **Show content.** Edit `$STAGE/seed_bank.yaml`, then run preflight again.

Run `HERMES_HOME="$STAGE" hermes onair preflight` after every change.

## Settings reference

Settings live under `plugins.entries.on-air.settings` in the home's `config.yaml`. A stage home
created with `--create-home` writes the required ones, and its `.env` holds only
`ONAIR_ALLOWED_USERS=onair-stage`. Most settings can also come from an `ONAIR_*` environment
variable of the same name (for example `ONAIR_PROFANITY`).

```yaml
plugins:
  enabled: [on-air]
  entries:
    on-air:
      settings:
        twitch_channel: your_channel      # read-only, anonymous
        host_name: YourName               # protected from impersonation (lookalike letters too)
        agent_names: [CoHostName]         # names the agent answers to; also protected
        seed_bank: /path/to/seed_bank.yaml
        never_say: ["phrase one", "phrase two"]
        profanity: mask                   # mask (default) | block | allow; slurs are blocked in every mode
        dead_air_seconds: 45
        alerts:                           # all optional; these are the defaults
          enabled: true
          thank_you: true                 # at most one guarded thank-you per alert burst
          include_messages: false         # the attached sub / cheer message stays out of the turn
          min_bits: 1
          during_privacy: drop            # or "queue": hold silently until live again
gateway:
  platforms:
    on_air:
      enabled: true
platform_toolsets:
  on_air: [no_mcp]                        # required: the stream session gets no tools at all
```

`platform_toolsets.on_air: [no_mcp]` matters. With no entry, the stream session gets your default
toolsets. An empty list `[]` still pulls in every globally enabled MCP server.

## Without creating a home

`hermes onair setup` without `--create-home` prints the settings to merge into an existing
configuration and then preflights the **current** home, not the printed settings. It writes
nothing.

The stage cannot run from the home where your own agent uses On Air. That home has On Air's setup and
presentation tools, Hermes offers a plugin's tools to every platform, and the co-host must never be
offered tools: the runtime gate would refuse every reply. Preflight reports this as `owner_tools` and
refuses to arm. A stage home made with `--create-home` registers the co-host only, with no owner tools.
What makes a home a stage home is the empty marker file `.onair-stage-home` in it. `--create-home` writes
it; a home you configure by hand needs it too, and then no longer offers On Air's owner tools.
