"""Run the documented quickstart in docs/getting-started.md, command for command.

The bash block between the quickstart markers runs unchanged under ``bash -euo pipefail`` in an
owned scratch HOME, in a private network namespace when available, with stdin from /dev/null.
``hermes`` is a shell function that runs the real Hermes CLI, with two disclosed stand-ins:

- ``hermes plugins install <src> --enable`` places the plugin at ``$HERMES_HOME/plugins/on-air``
  (the layout Hermes installs) and enables it, instead of fetching the Git URL and preparing
  Hermes' isolated plugin runtime (several hundred MB, and network). The real subcommand's
  ``--enable`` flag is checked separately against the real parser.
- lines marked ``# interactive`` or ``# when ready`` (model selection, gateway start) are only
  checked for existence in the real CLI, never run.
"""
from __future__ import annotations

import re
import subprocess

from _onboarding_helpers import ROOT, clean_env, hermes_python, netns_prefix, run_hermes

DOC = ROOT / "docs" / "getting-started.md"


def quickstart() -> str:
    text = DOC.read_text(encoding="utf-8")
    block = text.split("<!-- quickstart:begin -->", 1)[1].split("<!-- quickstart:end -->", 1)[0]
    match = re.search(r"```bash\n(.*?)```", block, re.S)
    assert match, "quickstart block missing"
    return match.group(1)


def test_quickstart_commands_exist_in_the_real_cli(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    script = quickstart()
    skipped = [line for line in script.splitlines() if line.rstrip().endswith(("# interactive", "# when ready"))]
    assert len(skipped) == 2
    for args, flag in ((["plugins", "install", "--help"], "--enable"), (["model", "--help"], "usage: hermes model"),
                       (["gateway", "run", "--help"], "usage: hermes gateway run")):
        proc = run_hermes(args, home=home, hermes_home=tmp_path / "h", tmpdir=tmp_path / "t")
        assert proc.returncode == 0 and flag in proc.stdout, proc.stdout + proc.stderr


def test_documented_quickstart_runs_end_to_end(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    script = "\n".join(
        (": skipped " + re.sub(r"[^\w -]", "", line)) if line.rstrip().endswith(("# interactive", "# when ready"))
        else line for line in quickstart().splitlines())
    prelude = f"""
set -euo pipefail
hermes() {{
  if [ "$1 $2" = "plugins install" ]; then
    local h="${{HERMES_HOME:-$HOME/.hermes}}"
    mkdir -p "$h/plugins/on-air"
    cp -r {str(ROOT / 'onair')!r} {str(ROOT / '__init__.py')!r} {str(ROOT / 'plugin.yaml')!r} "$h/plugins/on-air/"
    [ -f "$h/config.yaml" ] || printf 'plugins:\\n  enabled: [on-air]\\n' > "$h/config.yaml"
    echo "stand-in install into $h"
    return 0
  fi
  {hermes_python()!r} -B -m hermes_cli.main "$@"
}}
"""
    env = clean_env(home, None, tmp_path / "t")
    (tmp_path / "t").mkdir()
    proc = subprocess.run([*netns_prefix(), "bash", "-c", prelude + script], env=env, cwd=tmp_path / "t",
                          stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=300)
    out = proc.stdout
    assert proc.returncode == 0, out[-4000:] + proc.stderr[-4000:]
    stage = home / "onair-stage"
    assert out.count("=== SYNTHETIC OFFLINE REHEARSAL ===") == 2
    assert out.count("On Air package: hermes_plugins.on_air.onair") == 2
    assert f"Created a NEW On Air stage home: {stage}" in out
    assert f"Preflight of the CURRENT Hermes home: {stage}" in out
    assert "persona: warn" in out and "tts: pass" in out and "Preflight passed." in out
    assert "does not verify a model provider or credentials" in out
    assert (stage / "SOUL.md").read_text(encoding="utf-8").startswith("# Public stage persona")
    assert re.search(r"^model:", (stage / "config.yaml").read_text(encoding="utf-8"), re.M) is None
