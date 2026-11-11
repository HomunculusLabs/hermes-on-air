---
name: live-build
description: Use when building live on stream. Show safe progress.
---

# Build a project live

The owner's agent builds; the separate guarded co-host speaks. This is a local owner-session
workflow, not a stage capability. The short brief can name the project; it does not authorize
publishing private material or operating the show's safety controls. If tools refuse or are
missing, report the reason and next action; do not bypass the owner-session boundary.

1. Identify the requested project and an owner-named project folder. If no folder was given,
   propose a new subfolder of the current working directory named after the project and ask the
   owner to name or confirm it. Work only inside that folder, and do not overwrite existing work
   without permission. Confirm in one line what will be built and the chosen folder, in the owner
   conversation, not on the work view. Scope the first version to a small, testable result.
2. Before changing the view, call `onair_layout` `boxes`, `onair_layout` `inspect` and
   `onair_present_status` and save the observed boxes, arrangement, inset corner, program scene
   and any relevant simple preset for restoration. If the previous state is unknown or
   ambiguous, report that exact restoration cannot be promised; do not guess. Check stage
   state with `onair_stage_control` `state`.
   Privacy, off-air, a disconnected or unknown safety state means stop and clear, not start.
   Prepare the presentation and owned OBS sources only if needed, using `onair_obs_setup`
   `configure`; relay its refusal and missing prerequisites. The optional 3D scene must already
   be enabled and available. Do not change its settings or switch program without owner direction.
3. Publish with `onair_work_share` `show`: title, status and two short public panes (code and
   terminal). Then `onair_work_share` `step` with four to six short items total. Set the owned
   boxes with `onair_layout` `set`, using slides in Main and scene in Sub 1 with Stack, or scene
   in Main and slides in Sub 1 if the owner wants the co-host big. The work view is the slides
   source, not a screen capture. If the 3D scene is unavailable, report it and use slides in Main
   with empty subs rather than pretending there is an inset. A layout change does not switch
   program; if the owner requested the build on stream and the Boxes scene is not on program,
   call `onair_obs_scene` with the owned Boxes scene, check the returned result and ask the owner
   to inspect OBS preview. An already-on-program layout changes immediately. A readback is not
   proof of pixels or sound; surface blanking is not a whole-OBS privacy cut.
4. Make one small change at a time, such as one test or one function. Run real commands within
   the named project folder. Inspect output privately first, remove unsafe material, and append
   only a short verbatim public-safe excerpt of actual output to the terminal pane. Do not invent
   a test result or imply an omitted failure passed. Replace the code pane with only the part
   just written, and advance the step rail and status. Keep a visible change at least three
   seconds apart, and leave the first failing test visible for at least five seconds before the
   fix. Never dump long output; when the terminal pane fills, replace it with a short safe excerpt.
   Viewer pacing never delays a safety cut. If the owner speaks, finish the current small step,
   then follow their direction; stop and clear immediately for a stop, privacy or off-air report.
5. At the start, first failing test, fix, pass and done milestones, coordinate voice as follows.
   `onair_stage_host_turn` accepts only the owner's own words when the owner asks to say or ask
   something to the co-host; pass those words faithfully, adding nothing. The current tool contract
   does NOT authorize autonomous model-written milestone notes. If the owner has not supplied an
   exact line for a milestone, skip its relay and tell the owner narration needs their words; do
   not invent a note or claim the co-host narrated. If the owner supplies a short public line
   explicitly asking the co-host to put a milestone in her own words, relay it at most once for
   that milestone. Queued means accepted, not aired. Do not retry old speech after a cut or bypass
   a refusal. Host turns are disabled by default; relay requests can be refused during the
   settling window after startup, arming, resuming, privacy off or switching to autonomous
   (20 seconds by default; 5–120 configurable). A queue can also fill. In approval mode, the
   generated reply passes the guard and waits for the owner's approval. In autonomous mode,
   relays are refused by default; if the owner has explicitly enabled host relay autonomous,
   guarded replies can air without owner approval. Never enable it yourself. Host text can be
   sent to the model, recorded privately and repeated, so send only reviewed public material.
6. When the intended result is complete and tested, set the work status to done and, only if
   the owner supplied an exact recap line, relay it once under the same rules. If work remains
   incomplete or a test fails, say so and do not mark it done. Report what actually passed or failed;
   leave the result on screen until the owner says otherwise. Then `onair_work_share` `clear`
   and restore the saved supported layout and program scene using the corresponding tool calls,
   only if the snapshot is sufficient and still safe; otherwise report what cannot be restored.
   Inspect the result. Do not restore over a newer owner change or switch away from a newer scene.

## Public content

Never show secrets, credentials, absolute paths, usernames, environment dumps, private file
contents or unreviewed third-party text. The work view is public and its filter is only a narrow
backstop. It refuses secret-looking assignments, authorization headers, email addresses, private
network and home-path shapes, key-like strings, other forbidden controls and oversized content;
ANSI escapes are stripped. Each pane is limited to 6000 characters and 120 lines; at most two
panes and six steps. If a view call refuses content, remove or rephrase the flagged part before
another call; never retry the same text. Never add invented command output to replace it. On
privacy, off-air, or the owner's stop/clear request, stop work and call `onair_work_share` `clear`
immediately; report a failed clear and tell the owner to check OBS. Do not republish automatically.

## Worked example

Assume the owner named `banana-synth` as the folder, requested the build on stream and supplied
these exact five public host lines in advance. The sample outputs below are illustrative: use them
only if the real commands actually returned them. Assume the initial supported boxes were scene in
Main, slides in Sub 1, Stack, bottom-right, and Boxes was on program; preserve the actual readback
instead. If any call fails, stop that branch and report the refusal. Each JSON line is one tool
call; ordinary file edits and commands happen between them in the project folder.

1. Inspect and record the pre-build state, including stage safety:

```json
onair_layout {"action":"boxes"}
onair_layout {"action":"inspect"}
onair_present_status {"include_obs":true}
onair_stage_control {"action":"state"}
```

2. Publish the view and steps, then set the layout (if Boxes is not already on program and the
   owner requested it, call `onair_obs_scene` as shown):

```json
onair_work_share {"action":"show","title":"Banana synth","status":"starting","panes":[{"kind":"code","label":"synth.py","language":"python","content":""},{"kind":"terminal","label":"Tests","content":""}]}
onair_work_share {"action":"step","done":[],"current":"Write test","next":["Run test","Implement tone","Verify"]}
onair_layout {"action":"set","main":"slides","sub1":"scene","sub2":"empty","arrangement":"stack","inset_corner":"bottom-right"}
onair_obs_scene {"scene":"On Air: Boxes"}
onair_stage_host_turn {"text":"We are building a banana synth; please introduce it in your own words."}
```

3. Start with a placeholder tone function returning zero samples. Write a test importing it,
   show just that test, and run `python -m pytest -q`. If this is the real output, append it;
   give the failing result five seconds before the fix:

```json
onair_work_share {"action":"update","pane":0,"content":"from synth import tone\n\ndef test_tone():\n    assert round(tone(1, 4, 4)[1], 3) == 1.0"}
onair_work_share {"action":"step","done":["Write test"],"current":"Run test","next":["Implement tone","Verify"]}
onair_work_share {"action":"append","pane":1,"text":"E       assert 0.0 == 1.0"}
onair_stage_host_turn {"text":"The first test fails; please explain that in your own words."}
```

4. Implement the function and show just the new function:

```json
onair_work_share {"action":"update","pane":0,"content":"from math import pi, sin\n\ndef tone(freq, rate, count):\n    return [sin(2 * pi * freq * i / rate) for i in range(count)]"}
onair_work_share {"action":"step","done":["Write test","Run test"],"current":"Implement tone","next":["Verify"]}
onair_stage_host_turn {"text":"The tone function is in place; please explain the fix in your own words."}
```

5. Run the test again, and append this excerpt only if it is real:

```json
onair_work_share {"action":"append","pane":1,"text":"\n1 passed"}
onair_work_share {"action":"step","done":["Write test","Run test","Implement tone"],"current":"Verify","next":[]}
onair_stage_host_turn {"text":"The test passes; please share the result in your own words."}
onair_work_share {"action":"status","status":"done"}
onair_stage_host_turn {"text":"The banana synth test passes; please recap in your own words."}
```

6. Leave the result visible. Only when the owner asks, clear and restore the saved state. In
   this example Boxes was already on program, so no scene switch is needed:

```json
onair_work_share {"action":"clear"}
onair_layout {"action":"set","main":"scene","sub1":"slides","sub2":"empty","arrangement":"stack","inset_corner":"bottom-right"}
onair_layout {"action":"boxes"}
```

## Hard rules

Never go live, arm, approve a line, lift privacy or unmute on your own; never touch OBS items
that the plugin does not own. Never handle a stream key or ask for credentials in chat. Never claim
anything aired, rendered, sounded right, passed or was restored without evidence. The owner's
assistant audio is not guarded: keep it in headphones and off stream. See docs/host-turns.md,
docs/work-share.md and docs/views.md.

## Tell the owner

Be concise: state the goal and folder, observed outcomes, missing prerequisites and any refusal.
Do not equate a queued host turn with spoken narration.
