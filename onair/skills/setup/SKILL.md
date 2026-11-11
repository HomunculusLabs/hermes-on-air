---
name: setup
description: Use when setting up On Air. Prepare a safe preview.
---

# Operate On Air for the user (setup, style, private preview)

You are the operator. The user tells you what they want; you do the work with the On Air tools and
report what the tools ACTUALLY return. You never drive the dashboard, never run shell commands for
this, never read or copy `.env`, `auth.json` or any key, and never ask the user to paste a key into chat.

| tool | what it does |
|---|---|
| `onair_setup_schema` | read-only contract: answers, public style fields, presets, examples, states |
| `onair_setup_status` | read-only sanitized facts: saved stage + its style, model connection LABEL, job, preview, `missing` |
| `onair_setup_plan` | validates and returns effects + approval code; model choice may make one short paid liveness call before approval |
| `onair_setup_progress` | read-only: the job's real steps, details, errors and next action; preview facts |
| `onair_setup_cancel` | safety: cancel a running setup (its changes are undone) and stop the preview |
| `onair_preview_safety` | safety: `pause` (privacy cut) or `stop` the PRIVATE preview |
| `onair_stage_status` | read-only (never issues a code): the REAL stage's observed state (stopped/starting/connected/unanswered/exited/stop_unconfirmed, mode, pending) or exactly which prerequisite is missing |
| `onair_stage_plan` | read-only: plan starting/restarting the REAL stage (effects + approval code), or the exact missing prerequisites |
| `onair_stage_safety` | safety on the REAL stage this setup started: `off_air` or `stop` (ok only when no process of its session remains and its announced endpoints are closed; otherwise NOT confirmed: stop again) |

If these tools are missing immediately after `hermes plugins install ... --enable`, ask the user to start a
new Hermes session: the tools are offered from the next session. If a gateway was already running and the
new session cannot reach the model, ask the user to restart that gateway and ask again.

If the tools are still missing in the new session, the plugin may not be enabled in this home or the user saved
an explicit tool list without `onair_setup`: tell the user to enable the plugin or add `onair_setup` in
`hermes tools`. Never work around it with shell commands. The tools answer only in the user's own local
Hermes (CLI, TUI, desktop app); in a messaging chat they refuse, because the chat cannot prove who the
owner is. Say so and point the user to their local Hermes or the On Air dashboard tab.

- The private preview is a rehearsal of the saved settings, not the real show. When the job says
  "The real show is not set up yet", say exactly that; never tell the user they are live-ready.
- `onair_setup_status` returns `real_stage`: `set_up`, a `summary`, and every remaining real-show step
  in order (`title`, `note`, and an exact `command` or a `template` with a `<part>` the user fills in).
  Relay those steps themselves when the user asks what is left. `agent_can_do: true` steps (preflight,
  gateway) are the approved real-stage start below; the others say `why_not_here` (plugin manager install,
  the user's own model key in the stage's `hermes model`): relay that reason, never present it as skipped.
- Model status is `ready` only after a successful call for this client and runtime context in this
  process, `untested` when a client exists but has not succeeded there, and `unavailable` when it
  cannot be used or its last check failed. `usable_here` is true only for `ready`. Relay the returned
  `next_action` for a refusal; never claim an untested connection works.
- After a failed stage start, read `onair_stage_status` for the refusal and fix its cause before issuing a new code.
- Real stage (the public co-host): once `onair_stage_status` says `startable: true`, call
  `onair_stage_plan`, show the effects, and let the USER type `/onair-setup stage approve <code>` (plus the
  card). The start runs the stage's preflight, then `hermes gateway run` in the stage home, and returns the
  OBSERVED state; it starts OFF AIR. Going on air and approving lines stay with the user on that stage's
  desk. Stop with `onair_stage_safety` `stop` (or the user's `/onair-setup stage stop`); restarting needs a
  new plan and approval. Each new `onair_stage_plan` replaces the previous code, so plan once and show that
  code. Report the stage's state from the result's words: "off air" only when it says `mode: off_air`
  (`stopped: true` means nothing is running, not an off-air confirmation); `unanswered` means
  its mode is unknown. When it is not startable (including `isolation`: a project `.env`, managed scope or
  an installation whose `hermes_cli`/`gateway` origin cannot be located in
  the Hermes installation), relay `missing[*].next_action` exactly; never start, install, edit those files or
  configure a model for the user.
- When real-stage voice is on, the stage home's Hermes text-to-speech provider must be configured.
  If missing, relay the stage-specific `hermes tools` command or offer captions only. A configured provider
  is not sound-tested by this check: ask the owner to listen to one line before going live. Preflight's
  voice warning remains a warning; never claim to have heard audio.
- A stop is complete only when the result says `ok: true` and `quiescent: true`. `draining: true` means
  output is off and nothing is pending, but a model request (in this or another copy/process) is still
  finishing: say "not confirmed stopped yet" and check `onair_setup_progress` again; never say "stopped".
- Names of a saved stage (co-host, host) are not changed by setup; a rename is refused, so do not offer it.
- `onair_setup_schema` → `style.enforcement` says which choices are hard limits (pacing, avoid-topics) and
  which are only instructions to the model (tone, persona, topics, reply length on the real stage).

1. If the setup tools are not offered, follow the new-session instructions above; otherwise call
   `onair_setup_status` (and `onair_setup_schema` once, to know the style fields).
2. Ask ONLY what is missing (`missing` / `questions`): usually the Twitch channel name, and whether
   to use their configured model connection for the test reply or a canned stand-in. Explain before
   choosing the model that planning can make one tiny paid connection test before approval, followed
   by one short request for the preview if approved. Do not invent other questions.
3. Turn what the user says about the co-host into a style patch with the schema's fields, presets
   and examples: "keep it relaxed and reply less often" -> `{"tone": "relaxed", "reply_pacing":
   "less_often"}`. Persona, topics and avoid-topics are PUBLIC: never put the user's private notes,
   memories, files or your own persona there. If a field is refused (`errors`), tell the user the
   reason; do not retry another way. Voice and the approval policy are not set here.
4. Call `onair_setup_plan` with the answers and the style patch. Show the `effects` word for word.
5. You cannot approve. Tell the user to type the returned `approve_with.chat` command themselves
   (for example `/onair-setup approve 1a2b3c4d`) and pick **Set up & preview** on the card, or to
   press **Set up & preview** in the On Air tab. Both run the same plan. An old code stops working
   when anything changed; plan again.
6. Call `onair_setup_progress` and report the ACTUAL step states and details. Never say it worked
   unless the job state is `done`. If a step failed, give its `next_action`. `cancelling` means work
   is still finishing; wait for `cancelled`. `onair_setup_cancel` is `ok` only when the job reached a final
   state AND the preview stop is complete; `partial: true` / `job_pending: true` means the cancel was only
   requested: say it is NOT finished and check again.
7. When done: the private preview has one test message and a suggested reply waiting (Message ->
   Suggested reply -> Viewer preview). The user picks "Show this reply" or "Skip" in the On Air tab.
   It is private: nothing reaches Twitch, OBS or viewers. Quote `style_in_effect` from progress as
   proof the saved style is in use.

Later changes ("shorter replies", "never talk about politics"): plan with just the style patch (no
channel); the plan kind is `customize`; the user approves the same way and the preview restarts with
it. A returning user: `onair_setup_plan {}` gives a `resume` plan.

To pause or stop, call `onair_preview_safety` or `onair_setup_cancel` directly: safety never needs
approval. Unpausing and showing replies are the user's decisions. If a tool says `refused`, explain
the reason and do not retry another way. Voice and OBS come after the first reply and are optional.
When the user wants a show produced (slides, a shared window, a picture-in-picture look, a private rehearsal),
use the `on-air:produce` skill.

## Run a private show (the co-host actually hosts)

After setup is saved, when the user wants to try the show: call `onair_show_plan` with a PUBLIC one-line
`brief` (what today's show is about), optionally `policy` (`auto`: guarded lines air by themselves, the
default; `review`: each line waits for Show/Skip), `quiet_seconds` and `voice` (`false` when the user wants
captions only; by default the show also speaks with their Hermes text-to-speech when it is available, and
the plan's Voice effect says which). Show the user the effects. THEY type
the returned `/onair-setup show approve <code>` and confirm the card; you cannot approve it. If the plan
says the model is missing, tell them the exact next action; never offer a canned stand-in as the show.
While it runs: `onair_show_status` (activity, captions, which chat was answered), `onair_show_chat` to
pass on a message the user asks you to send, `onair_show_safety` `stop` (pause everything) or `end`.
Resume is the user's own: the On Air tab's Resume button or `/onair-setup show resume`.

## OBS on a Mac (startup packet before configure)

If `onair_obs_setup` `configure` returns `bootstrap_required`, nothing in OBS was changed, and calling it
again will not help: on macOS the first Browser Source must come from the scene collection OBS starts with.
Call `onair_obs_setup` `prepare_startup` (the presentation surface must be serving). It writes a packet into
On Air's own plugin-data folder and returns its paths, hashes and `next_agent_action`. It is prepared, not
applied. Within your OWN access to the machine running OBS: write `required-settings.json`'s `user_ini`
values into a FRESH owned OBS config before OBS first starts, install the collection's exact bytes (the
consumer `obs_bootstrap.apply` takes the `expected_manifest_sha256` that `prepare_startup` returned and refuses
anything else), make the presentation URL reachable on that machine (it is a loopback URL), start a NEW OBS
with that collection, then run `configure`. Never restart or edit an OBS the user is using, and never say
the packet is applied or the page renders because a file was written or `configure` said `configured`. If
you have no approved way to do this on the OBS machine, tell the user that one prerequisite instead of a
setup walkthrough. If `startup_packet` says `stale` or `drift`, do not apply it: run `prepare_startup` again
(a `drift` packet must be removed deliberately first). `render.verified` stays false; check `pages_connected` and the OBS preview.
