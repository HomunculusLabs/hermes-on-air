# Review guide

Use this guide as a contributor or reviewer to check changes before merging.

Every pull request into `main` gets one independent code review before it merges. The author
never reviews their own PR. The reviewer posts one review per head commit with one of three verdicts:

- `APPROVE`: nothing blocks at this commit. The reviewer then squash-merges, pinned to that commit.
- `REQUEST_CHANGES`: at least one blocking finding, with proof.
- `BLOCKED`: the reviewer couldn't run the check the change depends on. The review names the missing
  check.

A verdict covers only the commit it names. Any new push needs a new review.

Only four kinds of finding can block, and each needs proof (file and line, plus a command, test or
exact trace):
- something breaks;
- a security or privacy problem;
- the PR doesn't do what it says, or a test passes without testing the change;
- it breaks a rule in this file or in `SPEC.md`.

Everything else is a note, and notes don't block.

## Checks

Run these on the PR head and on its base, so problems already on `main` aren't blamed on the PR.
The dependency-only command is a smoke subset, not the complete suite. See the README for the
Hermes-runtime full-suite command and its prerequisites; record passes and skips separately.

```bash
uvx --with pytest --with pyyaml --with websockets --with ruamel.yaml --with fastapi --with httpx pytest -q -p no:cacheprovider tests/core tests/guard tests/wordfilter tests/portability
hermes plugins validate . --install-deps
python tools/dev_fake_show.py --guard real --voice --once --duration 20 --port 0
```

The fake show uses an ephemeral port with `--port 0`, avoiding a conflict with an existing show.

Keep probe and scratch scripts outside the checkout. `hermes plugins validate` scans every file
under the plugin directory, including gitignored ones.

## Focus

- **Viewer-chosen output.** Look for any path that lets a viewer choose words the host says or
  shows: the output guard (`onair/guard/`), the chat gate (`onair/chat/`), the word filter
  (`onair/wordfilter/`), and the alert and display-name paths in `onair/stage.py`.
- **No tools on stream.** The stream session gets no tools. Keep `platform_toolsets.on_air: [no_mcp]`
  in the docs and keep the adapter's checks.
- **Listeners.** They stay on loopback. The desk control API requires its bearer token and refuses
  any request that carries an `Origin` header.
- **Public-repo hygiene.** No secrets, personal or agent names, machine names, LAN addresses or
  absolute local paths, in files or in commit messages.
  - The word lists stay ROT13-encoded.
  - Tests build their canaries from the encoded lists rather than writing the words in plain text.
- **Tests must be able to fail.** A changed test or checker has to show an input or mutation that it
  catches.

## Settled decisions (not findings)

These choices are deliberate, and their cost has been measured. See README "Limitations (by design)"
and `tests/guard/PRICE.md`.

- **The guard is strict.** It withholds or skips:
  - translation and definition read-outs of viewer text;
  - quoted-term echoes;
  - every "say ___" request, shout-outs included;
  - refusals once a turn is armed;
  - cipher-shaped non-word tokens in host lines.
- **Accepted residuals:**
  - raid names that imitate the host with lookalike letters (the alert shows "someone");
  - rot13 or reversed supporter names;
  - one joined handle;
  - unknown ciphers;
  - base64-shaped handles such as `Hancock99`.
- **The word filter is a backstop.** Host speech matches whole words only. Innocent uses of a listed
  word can be masked.
- **A show always arms into approval mode.** Autonomous mode is reachable only from approval.

## Merge

- **Squash-merge only**, pinned to the approved head (`head_commit_id` in the merge API).
- **Title:** `<area>: <what changed>`.
- **Message:** a clean public summary, with no local paths, task or card ids, machine names, or
  agent or person names.
- Branch history never reaches `main`.
