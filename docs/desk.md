# The On Air desk

The desk is the **On Air** tab in the Hermes dashboard. It is how the operator approves lines,
cuts privacy, mutes and stops a running show. It talks to the show only through the dashboard's
own authenticated proxy (`dashboard/plugin_api.py`, fixed routes). It never shows, copies or asks
for the desk token, and it has no address field: it can only reach the show that the dashboard
process itself discovers. The owner's profile cannot discover a stage started in a separate stage
profile; a stage-profile dashboard is needed for its show controls. Owner-profile Views has a
separate OBS controller and limitations described in [Views](views.md).

## Start here: setup wizard, practice and help

The On Air tab opens on **Start here** the first time (when no stage is connected). Four tabs stay
available at all times: **Start here**, **Practice**, **Desk** and **Help**.

- **Start here** is a six-step wizard: Welcome (practice or set up) → Stage folder → Channel and
  co-host → Brain and output → Review → Check and finish. Answers and the current step are saved
  in the browser (no secrets are asked for). Each Next is checked by the server with the same
  validation as `hermes onair setup --create-home`; errors show beside the field. Review lists the
  exact new folder and what it will contain; nothing is written until you tick the confirmation and
  press **Create stage folder**. The server then calls the existing stage-home creator (a NEW
  folder only, outside every Hermes home; nothing is read from or copied out of your current home),
  re-reads the created `config.yaml`/`.env` from disk and runs the real preflight checks against
  it. The readiness list separates **Done**, **Needs action** and **Not tested**.
- What the dashboard cannot do for another Hermes folder (no Hermes API for it): install the On Air
  plugin there, choose/test its model, test voice, start its gateway. Those rows show the exact
  command to run (`HERMES_HOME=<folder> hermes plugins install …`, `… hermes model`,
  `… hermes onair preflight`). Voice chosen in the wizard stays **Needs action** until tested.
- **Practice** starts a practice show inside the dashboard process: the real stage controller,
  chat gate, output guard, desk control API and overlay server on loopback, with scripted chat
  and canned replies (no model, Twitch, voice or network). It writes no `control.json` or token
  file, so the real desk can never find or be taken over by it, and it never turns into a real
  show. The tutorial's **Next** unlocks only after the practice show reports the result of your
  own click (a draft waiting, a caption aired, a rejection, privacy on then off, stop then
  Approval). **Skip** and **Replay tutorial** send nothing. The **Overlay preview** shows the
  caption that aired; the link opens the real practice overlay on this computer.
- **Desk** starts with one plain status block (chat source, show mode, overlay, voice) and one
  **Next:** line. Guard log, support alerts, receipts and optional assistance are under
  **Advanced**. Approve = put this exact line on the overlay (spoken only if voice is on).
  The LIVE chip means the stage is armed; it is not proof of a Twitch broadcast.

## Reaching a running show

Until a show answers, the desk shows **Connect this desk to a show** with these steps. Every
command already exists. Complete commands have a Copy button; the one command with a part only you
can fill in is shown as a dashed template without one. `stage` is an example profile name — use
yours.

1. **Use a dedicated stage profile:** `hermes profile create stage`. Keep private memory,
   personal `SOUL.md` text and other LLM middleware plugins out of it.
2. **Install On Air in that profile:** `hermes -p stage plugins install <git-url> --enable`.
   Plugins are per profile, so a new profile starts without On Air (until then `hermes -p stage
   onair …` reports that `onair` is not a command). Replace `<git-url>` with the Git URL you
   installed On Air from; a local clone works as a `file://` URL. If On Air is already installed
   in that profile but disabled, `hermes -p stage plugins enable on-air` turns it on.
3. **Print the settings for that profile:** `hermes -p stage onair setup`. It prints the
   `config.yaml` and `.env` lines to merge and runs preflight; it writes nothing.
4. **Pass preflight:** `hermes -p stage onair preflight`. Every check must pass (warnings are
   advice). The show runs preflight again each time you arm.
5. **Start the stage gateway:** `hermes -p stage gateway run` (foreground; Ctrl+C stops the
   gateway and the show).
6. **Open this desk from the same profile:** `hermes -p stage dashboard --isolated`, then the On
   Air tab. The desk finds a show only when the dashboard serving it runs from the same profile
   as the stage gateway. `--isolated` gives that profile its own dashboard; add `--port` if the
   default port is taken. `hermes -p stage onair status` reads the same state from a terminal.

The desk checks again every second and sends nothing until a show answers.

### Captions only, voice or OBS

- **Captions only** is a complete show. Set `voice: false` in the On Air plugin settings;
  preflight then reports captions only.
- **Voice (optional)** speaks lines through your Hermes TTS provider, after the guard and after
  approval. Preflight cannot verify audio routing, so it warns: test voice before going live.
- **OBS (optional):** once connected, *Show state* lists the loopback overlay address (with a
  Copy button) to add as an OBS browser source. Without OBS you can still rehearse from the desk.

## Reading the desk

| Banner chip | Meaning |
|---|---|
| `NOT CONNECTED` | No show has answered since the desk opened. The setup guide is shown. |
| `CONNECTION LOST` · `STATE UNKNOWN` | A show answered before but not now. |
| `STATE UNCONFIRMED` | The show answered, but its state and queue disagreed (it changed mid-refresh). Treated like a lost connection until a consistent answer arrives. |
| `LIVE` / `NOT LIVE`, mode | The show's own `live` and `mode` from the latest consistent refresh. |
| `PREFLIGHT FAILED` / `RUNTIME LATCHED` | Arming is refused; the panel below names why. |
| `PRIVACY CUT ON`, `MUTED` | Current privacy and mute state. |
| `activity: observed` / `quiet` | The show's event counter moved in the last 15 s. It is not proof of chat or of a broadcast. |

Only a state confirmed by the latest refresh drives chips, banners and controls. When the show
stops answering, every show control is disabled (Copy buttons and the setup steps still work
locally; they send nothing to the show), the queue, receipts, alert settings and recent alerts
read *unavailable*, an unsaved alert-settings edit is discarded, and the last confirmed state is
shown once, labelled **Last confirmed … (may have changed)**. A response that arrives after a newer
refresh is discarded, so a slow reply never restores a live-looking desk. If the show may still be
on air while the desk cannot reach it, cut it at the source: Ctrl+C in the stage gateway's terminal,
or hide the overlay source in OBS.

The line under the banner is the **last action**, with its age (e.g. `Last action: Approve —
approve confirmed by the show. (12 s ago)`). An action counts as confirmed only on an explicit
`{"ok": true}` reply; anything else reads `NOT confirmed` with the show's reason. A confirmed action
whose follow-up refresh fails says the current state is unknown.

## Privacy, mute and stop

They are separate controls and always fire immediately, even while the model is busy:

- **Privacy cut (P)** — viewers see "Be right back", audio is blocked and pending work is
  cleared. The banner, the chip and *Show state* all say `Privacy cut ON`. Press P to uncover.
- **Mute all (M)** — mutes audio; captions continue. Press M to unmute.
- **Stop all (Esc)** — cancels speech and pending lines and pauses a live show. Choose Approval
  to resume.

## Modes

- **Off air** — nothing airs. To arm, choose Approval: preflight runs first.
- **Paused** — armed but holding: no new turns start and alerts wait. Stop all lands here.
- **Approval** — every line waits in the queue: `A` approve, `R` reject, arrows or `J`/`K` select.
  Each row's buttons are labelled with its position (`Approve line 3`) and post that line's own id
  and epoch.
- **Autonomous** — guarded lines air without waiting for you. Only from Approval, after a
  confirmation.

## When arming is refused

**Preflight failed** lists only the failing checks, each with the show's own detail and, for known
checks, a one-line fix. Then: fix the stage profile, re-check with `hermes -p stage onair
preflight`, restart the stage gateway so it loads the change, and choose Approval (preflight runs
again and refuses if anything still fails).

**Runtime gate latched** names the refusal rule (`runtime_refused:<rule>`), explains what happened
and what to fix. The show has already been cut to OFF AIR. Off air only, **Clear runtime latch**
posts the existing guarded `runtime/clear` route; the clear is recorded in receipts and does not
arm the show. If the refreshed state still reports the latch, the desk says so instead of
reporting success. Nothing on the desk clears, arms or goes autonomous by itself.

## Semantic assistance (optional, shadow only)

Off by default. When an operator turns it on, each line that the deterministic guard already
released into the approval queue gets a **shadow** recommendation next to it: `✓ suggests
approve`, `✕ suggests withhold` or `? suggests review`, with the provider, a confidence and the
latency. It is advice only. **Recommendations never approve, reject or air anything; only your
Approve sends a line.** A deterministic rejection is final and is never sent to a provider.

Configure it in the On Air plugin settings (`semantic_assist`), then restart the show:

```yaml
semantic_assist:
  provider: laya                      # off (default) | laya | jev
  endpoint: http://127.0.0.1:8390     # loopback http only; set by the operator, never by the browser
  timeout_s: 3                        # 0.2 to 10 seconds; a late answer is "review", never "approve"
  show_policy: "Family-friendly. No personal data."   # public text sent with each check (jev only uses it)
  min_confidence: 0.6                 # jev: below this, the recommendation is "review"
```

- **Laya** is a local prompt-injection guardrail service (`GET /health`, `POST /check`). The draft
  is checked as output (`mode=post`) and the triggering public chat as input (`mode=pre`). A
  deterministic credential or compliance-canary match suggests withhold; a model-only flag on the
  draft or a flagged chat suggests review (the service's own guidance for chat-shaped output);
  both clean suggests approve, which means only "no injection or secret exfiltration detected".
  Laya does not judge your show policy, tone, facts or advice.
- **Jev** (TypeSafe System One) answers one typed question whose options are exactly approve,
  withhold and review, under your public show policy. It needs a key in Hermes secrets
  (the plugin's optional `ONAIR_JEV_API_KEY` env, a password field). Without a key it stays
  disabled and nothing is sent; manual approval is unaffected. Its connection test is local only.
- A request carries only the queued draft, the triggering public chat text (no names, at most 8
  messages) and your public show policy. Never memory, receipts, operator notes or credentials.
- Each recommendation is bound to its line (line id, epoch, SHA-256 of the exact text). A privacy
  cut, stop, off air, re-arm or a replaced line discards it; the desk counts discarded ones.
- Errors, timeouts, redirects, oversized or malformed answers all read as review / unavailable.

The **Semantic assistance** panel shows the provider, endpoint, state (Off, Shadow,
Misconfigured, Key absent, Unavailable), counters, and a **Test connection** button that runs
only the provider's harmless check (Laya `GET /health`, no inference). The control API routes are
`GET /assist` and `POST /assist/test {}`; neither accepts an endpoint, host or header.

Measured limits of the Laya mapping on a 59-case synthetic set are in
`docs/semantic-assist-eval.md`. Treat it as a second pair of eyes, not a release authority.

## Input outcomes in desk state

`GET /state` includes `inputs`, at most five entries in newest-intake-first order:
`{"kind": "host"|"chat", "who": "..." /* chat only */, "text": "...", "outcome": "answering"|"queued"|"carried"|"skipped"|"dropped", "reason": "..."}`.
Text is the cleaned receipt text, truncated to 80 characters. Reasons are fixed short categories;
empty means no additional reason. Routine chat may remain queued before being selected or expiring;
`answering` means dispatched, not that an answer aired. The list is cleared on privacy, Stop and off air.
The authenticated dashboard proxy applies its usual recursive token redaction. This is a state field;
this version does not add a desk display for it. Chat replies are selective; inspect `inputs` in
state for queued or skipped chat, or the owner-private receipts for the underlying decisions.

## Host turns

Host turns are opt-in and use the real stage's ordinary runtime, guard, approval, caption and voice
path. A privacy cut, Stop or off-air transition drops waiting host lines. Use the dashboard's
**Host line** control, the owner's local session or the stage-home CLI. Owner-private receipts
retain cleaned host text. See [host turns](host-turns.md) for relay settling and its limits.

## Trust boundary

Model-facing safety tools cannot loosen stage safety; generated lines still pass the guard and,
in approval mode, the owner's approval. An agent given an unrestricted terminal as the same user
can run any command that user can, including the stage CLI and authenticated desk requests. Use
approval mode while such an agent is active, or keep terminal approvals on. Private file permissions
protect against other users, not processes running as the same user.

## Keyboard and accessibility

`P`, `M` and `Esc` work anywhere on the page except text fields. Every enabled control is reachable
with Tab and Shift+Tab and shows a visible focus ring. Buttons are at least 44 px tall. Status
changes are announced through polite live regions (the last action, copy results); the ticking age
stays out of them. On narrow screens every panel stays in a single column: nothing is hidden.

## Known limits

- The desk can show only fields the show already reports; it has no separate connection telemetry.
  "Lost" means this dashboard got no consistent answer, not that the show stopped.
- Commands use the example profile name `stage`; the desk cannot know your profile's name.
- Screens and tests use synthetic fixture data. They do not prove a real broadcast, a real mute
  or a working audio route.
