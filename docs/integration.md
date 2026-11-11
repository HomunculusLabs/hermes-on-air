# Integrated private agent show (setup/show + presentation/OBS output)

Use this reference to understand the owner and stage integration boundaries.

The plugin's single registration (`onair/adapter.py: register`) wires both slices for an owner home and
nothing for an On Air stage home:

- `onair/setup_agent.register` — setup, real stage and the private show (`onair_setup_*`, `onair_stage_*`,
  `onair_show_*`, `/onair-setup`, skill `setup`);
- `onair/presentation.register` — `onair_present_*`, `onair_obs_*`, `onair_window_share`, `onair_broadcast`
  and `/onair-broadcast` (toolset `onair_present`); `presentation.teardown` runs on the host's plugin unload
  (`ctx.on_unload`). It closes the presentation page and its loop; it never ends a running broadcast.

## Owner settings the integration adds

| Setting | Effect |
|---|---|
| `auxiliary.onair_show` (Hermes auxiliary slot, registered by the plugin) | The private show's model requests (`purpose=onair.private-show`) and the setup preview's sample reply (`purpose=onair.setup-preview`) route through this slot, so the preview tries the model the show will use. Unset, it uses your main model as before. Set `reasoning_effort` (e.g. `max`), or `provider`/`model`, here; `ctx.llm` alone cannot carry an effort level. The show's "Model:" label still names your main model. |
| `plugins.entries.on-air.settings.voice: false` | The stage's own voice switch also makes the private show captions-only (no text-to-speech request). |
| `plugins.entries.on-air.settings.presentation` | Presentation/OBS config (see docs/presentation.md). With no `overlay_url`/`overlay_port` (or `ONAIR_OVERLAY_PORT`), the OBS overlay source follows the RUNNING private show's own captions+voice page (its port changes per show). |

## Owner surfaces

`/onair-setup ...` and `/onair-broadcast ...` and the private tools accept `cli`, `tui`, `desktop` and
`local`. On the supported SDK (v0.22.7) the classic CLI runs plugin slash commands on the gateway's Local
platform (the machine's own clients on the owner-only local socket), so they arrive as `local`; before this
the documented `/onair-setup show approve <code>` was refused in the classic CLI. Messaging chats and the
On Air platform stay refused.

## Notes from the first genuine combined run

- A Hermes CLI with the plugin enabled auto-starts a local gateway, which also connects the `on_air`
  platform adapter (off air; preflight refuses to arm without a stage profile). It exits with the CLI.
- The CLI's session title generator calls the main model (low effort) after the first turn unless
  `auxiliary.title_generation.model_upgrade_enabled: false`.
- The presentation page lives in the process that published it (normally your CLI session); it stops when
  that process exits.
