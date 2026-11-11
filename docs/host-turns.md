# Host turns on the real stage

A host turn is a line from the show host to the on-stream co-host, separate from Twitch chat. A
line typed at the desk or sent from its microphone takes priority over waiting chat and unapproved
quiet-beat drafts, which are withdrawn. It waits for a current model turn; in approval mode it can
be dispatched while an approved line is queued or airing, without interrupting that line. In
autonomous mode it waits for approved or released speech to finish. Chat drafts are
kept behind the host reply. The co-host's answer uses the same runtime check, line guard, approval,
captions, voice and output mute as a chat reply. Queued means accepted for a future turn, not
spoken. At most three host lines can wait. Model-relayed host lines do not receive these privileges.
Chat replies are selective; inspect `inputs` in desk state or the private receipts for skipped chat.

## Turn it on

Host turns are **off by default**. In the stage home's Hermes configuration set:

```yaml
plugins:
  entries:
    on-air:
      settings:
        host_turns: true
        host_relay_settle_seconds: 20  # optional; 5–120 seconds
        host_relay_autonomous: false  # optional; owner opt-in
```

Restart the stage through its normal owner controls so the settings take effect. The setup wizard
and stage status show whether host turns are enabled and the settling setting; change them in the
stage configuration. The stage must be in approval or autonomous mode, not paused or covered by
privacy. In approval mode, review and approve the co-host's output before it can air. Relays are
refused in autonomous mode by default: type the line, switch to approval, or explicitly set
`host_relay_autonomous: true` in the stage settings. This opt-in allows model-relayed text in
autonomous mode, where the co-host reply can air after the automatic guard without owner approval. Without that opt-in, changing to autonomous drops waiting relays and cancels
an in-flight relayed turn; already drafted lines still require approval.

## Ask by voice or type a line

In your own Hermes session, ask your assistant, for example, “Tell the co-host: what should we build
next?” It can use `onair_stage_host_turn` to pass the words you asked it to pass to the saved running
stage. It does not relay every word you say. Transcription or the assistant can mishear or change
words; check what was queued when accuracy matters. The tool reports queued or refused, not aired.

You can also type:

- In the authenticated dashboard, use the **Host line** control. The dashboard proxies the fixed
  `/host/turn` route using its private desk bearer; it does not expose that bearer to the browser.
  The dashboard's session authentication, Host validation and Origin/CORS restrictions apply.
- In your own local Hermes session: `/onair-stage say What should we build next?`.
- In a terminal using the **stage** home: `hermes onair host "What should we build next?"`.
- An owner-controlled local program may read `GET /state`, then post JSON to `/host/turn` using the
  authenticated loopback desk connection: `{"text": "...", "epoch": 1, "id": "optional-id"}`.
  The epoch must be the integer just read; `id` is optional. The route needs the private bearer
  credential. Model relays include `"via": "relay"`; human-typed routes omit it.

The state epoch binds a post to the state read before it. A privacy cut or Stop between that read
and the post causes refusal. It does **not** prove when a message was composed: callers must not
fetch a fresh epoch for buffered input and pretend it is newly typed. Re-enter the line after a cut.
A repeated id is refused rather than queued twice. Each running stage keeps up to 4096 used ids;
restart it to reset that bound.

### Relay settling

After stage startup, privacy is turned off, the stage is armed from off air or resumed from pause,
or approval mode changes to autonomous, the stage
refuses relay requests for a **settling window** (20 seconds by default, configurable from 5 to 120).
It reports `settling`, the seconds remaining and the option to type the line instead. Turning mute
off does not start this window. Typing a fresh line bypasses it. The window reduces the chance that
a delayed spoken request made before a cut arrives after the cut is lifted; it is **not a proof of
freshness**. A request delayed longer than the window can still arrive afterward, so stop or cancel
old voice requests and do not rely on the window to protect private speech.

`/onair-stage approve` displays the oldest waiting line in a choice card and requires your confirmation;
waiting lines in the desk, pending tool and approval card identify their originating turn as host,
chat, beat or support (unknown when unavailable). The source describes the trigger, not approval
or a guarantee about the generated text.
`/onair-stage approve ID` selects one. If the card cannot be shown, nothing is approved. Type
`/onair-stage mode approval` or `/onair-stage mode autonomous` to change mode. To remove a safety
control, type `/onair-stage privacy off` or `/onair-stage mute off` and confirm the choice card; if
no card is available nothing changes. The model-facing `onair_stage_control` can read state and
pending lines or enable privacy, enable mute, stop or take the stage off air. It cannot approve a
line, arm the stage, unmute or remove privacy.

## Trust boundary

The model-facing safety controls cannot loosen stage safety: they cannot arm, approve, unmute or
remove privacy. Every generated line still passes the guard and, in approval mode, the owner's
approval. This is not a human-presence guarantee for the CLI or desk bearer. An agent given an
unrestricted terminal as the same user can run any command that user can, including the stage CLI
and authenticated desk requests. Run the stage in approval mode while such an agent is active,
or keep terminal approvals on. Private file permissions do not separate processes of the same user.

## Privacy and voice limitations

A cut-in clears captions, revokes clips, drops unapproved and approved co-host lines, and cancels
in-flight work behind a new epoch fence. It carries queued owner-typed or desk-microphone host
lines into that epoch in order. If an owner host turn was already in flight and no newer owner line
is queued, it is re-asked once; a second cut does not re-ask it again. An old reply cannot air after
the cut. If a model turn was in flight, the cut starts a fresh model session so delayed cancellation
of the old session cannot cancel the new question. Previous conversational context in that model
session is not carried forward. Relayed host lines are dropped, not carried or re-asked. Chat remains subject to its
existing selective handling. Privacy, Stop or an off-air transition instead drops waiting host
turns of every origin and fences late replies. Host text and its origin are recorded in owner-private receipts; text is cleaned, can be
sent to the stage model and may be repeated in a generated reply. It is not placed in the public
chat overlay. The optional desk microphone is described in [Talk to your co-host](host-mic.md).
A privacy cut does **not** switch off a microphone owned by another app. Audio already sent to
another provider cannot be recalled.

The assistant's own spoken audio is **not guarded by On Air**: keep it in headphones and off the
stream. Voice delegation, waiting for any current turn or airing line, stage generation and, in
approval mode, your approval all add delay. Expect a pause, not an instant back-and-forth; no latency
is guaranteed.

**Desktop voice surface, source finding:** The checked Hermes source routes desktop live-voice
requests into the active desktop session, so the owner-surface check permits the plugin tool when it
is enabled. Desktop creates sessions as `gui` (`apps/desktop/src/api/canonical-protocol.ts:553`),
which maps to `desktop` (`gateway/session_policy.py:85`); plugin tools read the active policy's
platform (`hermes_cli/plugin_session.py:23-30`). The live voice event delegates to the composer
(`apps/desktop/src/lib/voice-live.ts:423-428`,
`apps/desktop/src/app/chat/composer/hooks/use-voice-live-conversation.ts:294-299,323`), which submits
it as a `voice-live` turn on the session
(`apps/desktop/src/app/chat/composer/hooks/use-composer-voice.ts:185-191,250`). That is a per-turn
surface (`gateway/session_surface.py:13-26`), not a replacement for the desktop session platform.
This is a source finding, **not a live voice acceptance test**. Actual delegation, provider setup,
audio routing, timing and microphone release still need a live check by the owner.
