# Seed-bank rundown

A seed bank is an owner-approved YAML or JSON file of short talking points for quiet
moments. The stage offers one beat when the dead-air timer expires, after higher-priority
chat and support turns. Offering a beat consumes it even if a generated response is
later withheld; all generated output still passes the normal guard and approval path.
The beat is a prompt, not a promise that speech will be produced. Pacing, privacy,
muting, approval, guard decisions, and provider availability still apply.

Setup writes a starter bank in the stage home. Before going live, replace each
`[OWNER: ...]` placeholder with approved details or remove that beat, and edit any
other points that do not suit your show. Point the `seed_bank` setting to your file.
See `tools/example_seed_bank.yaml` for a separate example. Keep facts short and cite
where they came from in each beat's `provenance`; never put private information in a
bank. IDs must be unique lowercase slug-like identifiers, text must be 1–420
characters without angle brackets, and segment names must be unique. Invalid banks
fail closed rather than loading a partial rundown.

```yaml
version: 1
exhaustion_policy: loop
min_repeat_gap: 1
reset_on_rearm: true
segments:
  - name: opening
    beats:
      - id: hello-viewers
        text: "Welcome viewers and invite a question."
        provenance: "owner show notes"
  - name: wrap
    beats:
      - id: recap-viewers
        text: "Ask viewers what they enjoyed discussing."
        provenance: "owner show notes"
      - id: timely-closing
        text: "Offer a sign-off when the host is ready."
        provenance: "owner show notes"
        evergreen: false
```

On the first pass the bank offers unused beats in segment and list order. After
all beats are used, `loop` (the default) revisits eligible evergreen beats in order,
with a prompt asking for a new angle. Beats are evergreen by default; mark a beat
`evergreen: false` if it is time-specific or inappropriate to revisit. `stop` leaves
quiet time unfilled after the first pass. `wrap_only` recycles evergreen beats in a
segment named `wrap` (case-insensitive). `min_repeat_gap` is the number of other
beat offers that must occur between uses of the same ID; it defaults to 1 and must
be an integer from 1 to 1000. The bank will not violate this rule even if that means
no beat is available: use at least gap + 1 distinct eligible beats, including in a
wrap-only pool. The example above only has one eligible wrap beat and thus is not
sufficient for continuous `wrap_only` operation.

When `reset_on_rearm` is true, arming from off air resets the rundown. Set
`reset_on_rearm: false` to retain usage. Pausing and resuming the same show does
not reset the rundown; the rundown state is shown on the desk. Usage files from
before the upgrade lack recent-beat history, so the first repeat after an upgrade
is not checked against the previous process's last offers. Start a fresh rundown
to avoid that ambiguity.

## Why did my co-host go quiet?

Check live mode, privacy and mute, pending approvals, guard/receipt results, and the
model and speech provider. Check `seed_bank_error` and `beats_left`; a missing,
invalid or empty bank supplies no beats. With `stop`, the bank intentionally goes
quiet after the last beat. With a repeat policy, too few eligible beats for the
configured gap can also exhaust the bank. Add or mark enough evergreen beats, reduce
the gap (never below 1), or choose another policy. A chat turn may take priority;
the dead-air clock restarts after activity, and a beat offer does not guarantee an
approved or audible line.
