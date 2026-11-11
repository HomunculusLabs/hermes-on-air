# Compatibility, install verification and troubleshooting

This page says what On Air is tested on, how to install and remove it cleanly, where it keeps
data, and how to diagnose the common startup problems without sharing anything private.

## Supported versions

| Requirement | Value | Where it comes from |
|---|---|---|
| Hermes | **0.22.7 or newer** | `requires_hermes: ">=0.22.7"` in `plugin.yaml` |
| Python | **3.14**, the interpreter Hermes itself runs on | Hermes 0.22.7's installation guide: current first-party installations run on Python 3.14; it does not promise current runtime support on 3.11–3.13 |
| Install method | Hermes' official installer, then `hermes plugins install` | Hermes publishes no supported wheel or sdist; there is no `pip install` route |
| Operating system | wherever that Hermes release runs | the plugin is pure Python and binds loopback only |
| Optional | a Hermes TTS provider for voice | without one, On Air runs captions-only |

On Air has no Python dependencies of its own and runs on the interpreter your Hermes install uses,
so Hermes sets the Python version. Hermes' `pyproject.toml` still declares a broader
`requires-python = ">=3.11,<3.15"`, but 3.11–3.13 are **not promised** by Hermes and therefore not
by On Air. Some pure modules, such as the word filter, would even import on 3.10; that is not
support either.

## Test matrix

| OS | Python | Hermes | How | Status |
|---|---|---|---|---|
| Linux x86_64 | 3.11 (not promised) | v0.22.7, version verified | harness: plugin-manager install at an exact commit, Python-module entry, `--strict-version` | **executed locally: all steps pass**. Python 3.11 is outside Hermes' promised runtime, so this shows behaviour, not support |
| Linux x86_64 | 3.14 (3.14.9, the launcher's own runtime) | v0.22.7 source checkout; identity verified from that checkout's own git (the tag commit; the report also records the dirty flag) | harness through the public `hermes` launcher: `pm repair` bootstrap in a fresh owned home, plugin-manager install at an exact commit, `--strict-version` | **executed locally: all steps pass**, including the version check |
| Linux x86_64 | 3.14 | an existing install's dependency environment run directly (Python-module entry); its copied tree has no install stamp and no git, so its provenance is unknown | harness: plugin-manager install at an exact commit, Python-module entry | **executed locally: all behaviour steps pass**; the version check is a limitation, and with `--strict-version` it fails, as it should |
| Linux x86_64 | 3.12, 3.14 | none installed | static portability tests only | **executed locally: pass**; the fresh-install test is skipped (no Hermes) |
| Linux (`ubuntu-latest`) | 3.14 | v0.22.7, official installer | `.github/workflows/compatibility.yml`: public launcher, `pm repair` bootstrap, plugin-manager install, `--strict-version` | defined, **not executed** yet |
| macOS (`macos-latest`) | 3.14 | v0.22.7, official installer | same workflow | defined, **not executed** yet |
| Windows | — | — | not in the workflow (Hermes installs there with `install.ps1`, which the workflow does not drive) | **not covered** |

A workflow file is not a CI result. A row only counts as executed once GitHub Actions has
actually run it and the run is linked from the release notes. The harness and tests were written
with other platforms in mind (Windows-safe file names, explicit UTF-8, guarded POSIX-only calls),
but nothing has run on macOS or Windows. One local Linux run used the public `hermes` launcher,
including its `pm repair` bootstrap in a fresh home; the official installer itself and the
workflow rows have not run. The other local runs used the Python-module entry
(`python -m hermes_cli.main`), which is narrower than the public launcher. A failed Hermes
install on a runner fails the row; it is not counted as skipped.

## Install

On Air is a directory plugin: Hermes imports it from `<HERMES_HOME>/plugins/on-air` under its own
`hermes_plugins.on_air` package. `<HERMES_HOME>` is `~/.hermes` unless you set `HERMES_HOME` or use
a named profile. Use a dedicated stage profile, so the show never shares memory or tools with your
everyday agent.

1. Install Hermes 0.22.7 or newer with its official installer
   (`curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash`, or `install.ps1` on
   Windows).

2. Install and enable the plugin with Hermes' plugin manager:

   ```bash
   hermes plugins install https://github.com/HomunculusLabs/hermes-on-air --enable
   # a local checkout at an exact commit works the same way:
   hermes plugins install "file://$PWD" --ref "$(git rev-parse HEAD)" --enable
   ```

   The plugin manager prepares its own small pinned runtime in your Hermes home the first time.
   It fetches those pinned packages from your package index unless Hermes' uv cache already has
   them. If your network needs a mirror, configure it the way Hermes documents for `uv`.

3. Add the settings that `hermes onair setup` prints to `<HERMES_HOME>/config.yaml`. See
   [getting-started.md](getting-started.md) for the manual setup path.

4. Put `ONAIR_ALLOWED_USERS=onair-stage` in `<HERMES_HOME>/.env`. With Hermes 0.22.7, a value
   in that file took precedence over an exported shell variable of the same name.

5. Check that Hermes discovers and loads the plugin:

   ```bash
   hermes plugins list            # on-air: status "enabled", source "git pinned@<commit>"
   hermes onair setup --channel your_channel --host-name YourName --seed-bank /path/to/bank.yaml --yes
   hermes onair preflight         # must end with "Preflight passed." before you arm a show
   hermes onair check-line "hello chat"
   ```

A manual copy into `<HERMES_HOME>/plugins/on-air` plus `plugins: {enabled: [on-air]}` is also
discovered (as source `user`), but it is **narrower**: no plugin admission, no pinned commit and
no record of where the tree came from. Prefer `hermes plugins install`.

## Verify a fresh install (the harness)

`tools/verify_install.py` checks the whole new-user path on your machine, without touching your
real Hermes home:

```bash
# recommended: through the public launcher (a fresh owned home first needs the launcher's `pm repair`):
python tools/verify_install.py --hermes-launcher "$(command -v hermes)" --bootstrap-launcher \
    --allow-dependency-downloads --strict-version --report /tmp/onair-verify.json
# narrower: only the Python-module entry of one interpreter (see the note below):
python tools/verify_install.py --hermes-python /path/to/hermes/python \
    --allow-dependency-downloads --strict-version --report /tmp/onair-verify.json
```

The `--hermes-python` form checks only that interpreter's Python-module entry, not the public
launcher. On a standard self-managed install, the dependency environment's copied Hermes tree
can lack recorded provenance (no install stamp and no git). That route then reports the version
as unknown and, with `--strict-version`, fails (the matrix row above). On such an install, use
the launcher form to verify the version.

It creates a brand-new, owned `HOME` and `HERMES_HOME` (deliberately with spaces and non-ASCII
characters in the path). By default (`--install-mode installer`) it installs the plugin with
`hermes plugins install file://<tree> --ref <exact commit> --enable --yes-deps`; a failed install
is a failure. The commit is the selected checkout's own `HEAD` when that checkout is clean,
otherwise a throwaway commit of the copied tree, and the report says which. Then it checks:

- the installed tree is byte-identical to the selected tree, and `hermes plugins list` shows it
  enabled at that commit;
- `preflight` passes on a safe captions-only config;
- `preflight` refuses an empty toolset list, enabled memory, `codex_app_server`, trusted
  inbound, a disabled platform, wrong allowed users, an overlay port in use and a malformed
  `config.yaml`, without echoing config values;
- `check-line` allows an ordinary line and withholds a never-say line;
- `status` refuses cleanly with no show running and with stale state left by a crash;
- `report` handles an install with no receipts;
- non-interactive `setup` neither prompts nor writes config;
- through the real Hermes plugin loader, the adapter connects with no chat source and no voice,
  `status` reads the live desk, and disconnect removes the discovery file;
- `hermes plugins remove on-air` works and the `onair` command is gone afterwards;
- the source tree is left byte-identical, with no bytecode written into it;
- the owned root is deleted at the end.

Options that change what is covered:

- `--install-mode copy` replaces the plugin-manager install with the narrower manual copy above.
  Its report row is `install_copy_layout`, never an install pass.
- The CLI steps run as `<HERMES_PYTHON> -B -m hermes_cli.main …` unless you pass
  `--hermes-launcher`. The Python-module entry is narrower than the public `hermes` launcher. The
  report records the argv that actually ran.
- `--allow-dependency-downloads` lets only the plugin install and the launcher bootstrap reach your
  package index, so the plugin manager can fill the empty owned home's cache. Without it those
  steps run behind the dead proxies, the plugin manager's `uv sync` cannot fetch its pinned
  runtime, and the install fails. The report blames the throwaway home only when the output
  shows that `uv sync` fetch failure. Any other install failure (a manifest or security-scan
  refusal, a broken Hermes) is reported without a guessed cause: read that step's stdout/stderr.
- `--strict-version` fails when the Hermes version cannot be verified. The harness asks the
  selected runtime (the launcher's own runtime when you pass `--hermes-launcher`) for its
  identity through `hermes_cli.version_info`, which reads the running tree's install stamp, then
  that tree's git. A tree with neither has unknown provenance: without the flag that is a
  `limitation` row. An identity that belongs to a different tree than the one the runtime
  imported, or a version below the plugin's minimum, always fails. The legacy
  `hermes_cli.__version__` (stamp only, `0.0.0` without a stamp) and the `hermes-agent`
  distribution version are reported as diagnostics only.

Safety properties:

- The installed plugin directory is never put on `sys.path`, and Hermes' loader is never replaced.
- Child processes get a minimal environment. Your API keys, tokens, `PYTHONPATH` and `HOME` are not
  passed through.
- Outside the opted-in dependency steps, every HTTP(S) proxy variable points at a closed loopback
  port. That catches clients that honour proxy variables. It is a tripwire, not a network sandbox.
- The public plugin-catalog lookup is answered from an empty cache file inside the owned home. No
  Twitch channel is configured for the adapter step, no model is configured and voice is off.
- The tool refuses to put its work directory or its report inside the plugin tree.
- It only deletes a root that still carries its own ownership token. Use `--keep` to inspect the
  root afterwards.

Exit status 0 means every required step passed. A `limitation` or `skip` row is reported as
such and is never counted as a pass. A plugin-manager install needs temporary space in the
owned home for what Hermes prepares there: about 300 MB when only the plugin runtime is needed,
and up to about 600 MB when Hermes also has to build a dependency environment for that home (a
launcher bootstrap, or some interpreters). It is all deleted at the end.

The portability tests run the same harness from a source path with spaces and Unicode:

```bash
ONAIR_HERMES_PYTHON=/path/to/hermes/python ONAIR_VERIFY_ALLOW_DOWNLOADS=1 \
    python -m pytest -q -rs tests/portability
```

`ONAIR_VERIFY_ALLOW_DOWNLOADS=1` selects the plugin-manager install. Without it the fresh-install
test uses the narrower copy layout. `ONAIR_HERMES_LAUNCHER` (with
`ONAIR_VERIFY_BOOTSTRAP_LAUNCHER=1` for a fresh home) runs it through a launcher. Without a
Hermes interpreter the fresh-install test is **skipped** and says so. The static checks still
run: version declarations, the workflow's permissions, portable file names, UTF-8 text and
syntax for the oldest declared Python.

## Uninstall and rollback

```bash
hermes plugins remove on-air       # deletes <HERMES_HOME>/plugins/on-air and drops it from plugins.enabled
```

Afterwards `hermes onair …` reports that `onair` is not a `hermes` command. The plugin's settings
under `plugins.entries.on-air`, the `gateway.platforms.on_air` block and `.env`'s
`ONAIR_ALLOWED_USERS` stay where you put them; remove them by hand if you no longer want them.
Restart the gateway after removing the plugin, so no running process still has it loaded.

`remove` keeps the plugin's data directory, so receipts survive an uninstall or a reinstall.

To roll back to an earlier version, stop the gateway and move the plugin to that version's
**full 40-character commit** with the plugin manager. Hermes accepts no tags, branches or
abbreviated SHAs for `--ref`.

```bash
hermes plugins install <the source you installed from> --force --ref <full 40-character commit>
hermes plugins list            # on-air: source "git pinned@<first 8 characters of that commit>"
hermes onair preflight         # must end with "Preflight passed."
```

Then start the gateway again. A forced reinstall from the same source replaces the plugin's code
but keeps untracked files, and copies any edits you made to tracked files to
`<HERMES_HOME>/plugins-backup/`. To start completely clean instead, run `hermes plugins remove
on-air`, then `hermes plugins install <source> --ref <full commit> --enable`.

Do not run `git checkout` inside `<HERMES_HOME>/plugins/on-air`. The installed clone need not
contain older commits (`fatal: unable to read tree`), and Hermes records the pinned commit
separately, so `hermes plugins list` would still show the old pin.

Only for the narrower manual copy layout, where Hermes records no source or pin: replace
`<HERMES_HOME>/plugins/on-air` with the older tree, then run `hermes onair preflight`.

To stage that tree from a `git archive` tarball and a manifest of its files, extract into a new,
empty directory with an explicit umask in a subshell, then check the copy with the verifier:

```bash
mkdir "$STAGING"                                   # new and empty, never the installed plugin
(umask 0022; tar -C "$STAGING" -xf "$ARCHIVE")     # the subshell leaves your own umask alone
python3 -B tools/verify_dropin.py "$MANIFEST" "$STAGING"   # rc 0 = exact files, hashes and modes
```

`git archive` stores ordinary files as 0664, so the umask decides the extracted mode: 0022 gives
the manifest's 0644 and 0755. The verifier compares each file's actual permission bits, so an
extraction under umask 0002 (files 0664) or a stricter one (0600) is reported as `mode_mismatch`
rather than passed. It also refuses extra, missing or edited files, symlinks and special files.

## Data and receipts

On Air keeps runtime data under `<HERMES_HOME>/plugin-data/on-air/`. Setup also creates a separate
stage home with its own configuration and public files. Presentation startup packets may be applied
by an explicit consumer to a new OBS configuration root; see [presentation.md](presentation.md).

| Path | What | Retention |
|---|---|---|
| `receipts/YYYY-MM-DD.jsonl` | one JSON line per decision (chat verdicts, withheld lines, controls, alerts) | append-only, kept until you delete it; not rotated or uploaded |
| `control.json` | desk discovery (loopback URL), written at connect | removed at clean disconnect; a leftover one after a crash is harmless |
| `desk.token` | bearer token for the desk API (mode 0600 where the OS supports it) | regenerated at every connect; the last one stays on disk until you delete it |
| `clips/clip-*` | TTS output while a line is being synthesized (voice on only) | read into memory and deleted straight away; leftovers from a crash are swept at the next start |

There is no telemetry. `hermes onair report` summarizes receipts from disk. Receipts can contain
viewer names and the reasons chat was skipped, so treat them as private stream data. Delete the
whole `plugin-data/on-air` directory to wipe all On Air state.

## Troubleshooting

| Symptom | Likely cause | What to do |
|---|---|---|
| `hermes: 'onair' is not a hermes command` | plugin not discovered or not enabled | run `hermes plugins list` and find `on-air`. Installed with `hermes plugins install`, its source is `git pinned@<sha8>` (or `git` without `--ref`); if its status is not `enabled`, run `hermes plugins enable on-air`; if it is not listed, install it again. A manual copy (narrower) is listed with source `user`: the directory must be named `on-air` and contain `plugin.yaml` and `__init__.py`, and `on-air` must be in `plugins.enabled`. |
| `ModuleNotFoundError: No module named 'onair'` | the plugin was imported outside Hermes (e.g. `python -c "import onair"`), or a modified copy uses an absolute self-import | inside Hermes, `onair` is `hermes_plugins.on_air.onair`. Use relative imports; run `python tools/verify_install.py` to check the installed layout. |
| `Preflight failed; not ready to arm.` | one or more checks failed | read the `fail` lines (`hermes onair preflight`, or `--json`). Each names the exact setting: `toolsets` → `platform_toolsets.on_air: [no_mcp]`; `memory` → disable both stores and the provider; `allowed_users` → `.env`; `agent_runtime` → remove `codex_app_server`. |
| preflight shows only `preflight: fail - Preflight could not safely inspect the configuration.` | `config.yaml` does not parse, or a section has the wrong type | Hermes prints the line number and saves a backup; `hermes config check`, fix it, rerun preflight. |
| `Preflight unavailable or failed (…); not ready to arm.` | preflight itself crashed (an unusual Hermes build or a broken install) | reinstall the plugin tree; run the harness; report the exception *type* shown, not your config. |
| `overlay_port: fail` / gateway log `overlay port … unavailable` | another process (often a second show or an old gateway) holds the port | stop the other process or set `overlay_port` to a free port, and update the OBS browser-source URL to match. |
| `runtime_gate: fail` | the runtime gate is not registered in this process | use a dedicated stage profile with no other `llm_execution` middleware plugins, then restart the gateway. |
| `tts: warn` | voice is on but not verified | test your Hermes TTS provider before the show, or set `voice: false` for captions-only. Captions-only is fully supported. |
| captions appear but no audio | the TTS tool failed or timed out (30 s): that line airs captions-only | check the Hermes TTS configuration; captions-only is the designed fallback, not an error. |
| `On Air status unavailable: control discovery or loopback request failed.` | no show is running, or `control.json` is left from a crash | start the gateway with the platform enabled; a stale `control.json` is replaced at the next connect. |
| `Setup requires --channel (or an interactive terminal).` | `setup` ran without a TTY | pass `--channel`, `--host-name` and `--seed-bank`, with `--yes`. Setup never writes configuration. |
| `plugins install` fails with `uv sync` / `Failed to fetch` | the plugin manager could not reach your package index for its pinned runtime packages | check network or proxy settings, or configure a package mirror as Hermes documents for `uv`, then retry. `hermes pm doctor` checks Hermes' package manager. |
| `hermes: no dependency environment is committed for this install; run hermes pm repair` | the launcher was started with a new, empty `HERMES_HOME` | run `hermes pm repair` once for that home. |

## Gathering diagnostics safely

When you ask for help, share **results, not configuration**. These are safe to paste:

- `hermes --version` and `python --version` (from the Hermes interpreter), plus your OS name;
- `hermes plugins list --json`, trimmed to the `on-air` entry;
- `hermes onair preflight --json`. It reports check names, pass/fail and fixed advice text,
  never your values;
- the summary lines of `python tools/verify_install.py --report <file>`. The report replaces local
  paths with `<ROOT>`, `<SRC>`, `<HERMES_PYTHON>` and `<HERMES_LAUNCHER>`;
- `hermes onair report --show`: counts by rule and disposition, plus beat names from your seed
  bank. Check those names before posting.

Do **not** paste:

- `config.yaml` or `.env`;
- `desk.token`;
- anything under `auth`, `sessions`, `memories` or `SOUL.md`;
- raw `receipts/*.jsonl`, which hold viewer names and chat-derived reasons;
- gateway logs that include chat text.

If a log line is needed, copy only the On Air line and replace channel and viewer names with
placeholders. On Air has no separate diagnostics command. The commands above are the
supported surface.
