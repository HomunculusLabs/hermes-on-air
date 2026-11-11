"""`hermes onair setup --create-home`: a NEW stage home, refusals, prompts and real preflight."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import sys

import pytest

from onair import cli
from _onboarding_helpers import ENABLED, install_copy, run_hermes, snapshot

UNICODE_DIR = "Stage hömé ✓ two"


def stable(directory):
    """Snapshot without Hermes' own per-run logs and caches."""
    return {k: v for k, v in snapshot(directory).items() if k.split(os.sep)[0] not in ("logs", "cache", "installs")}


def invoke(*argv):
    parser = argparse.ArgumentParser()
    cli.setup_parser(parser)
    return cli.handle(parser.parse_args(argv))


@pytest.fixture
def homes(tmp_path, monkeypatch):
    """An owned HOME (default root HOME/.hermes) and a separate current HERMES_HOME with sentinels."""
    home = tmp_path / "home"
    current = tmp_path / "current-home"
    (home / ".hermes").mkdir(parents=True)
    current.mkdir()
    (current / "auth.json").write_text('{"sentinel": "AUTH-SENTINEL-1234"}')
    (current / ".env").write_text("OPENAI_API_KEY=ENV-SENTINEL-5678\n")
    (current / "SOUL.md").write_text("PRIVATE-PERSONA-SENTINEL\n")
    (current / "config.yaml").write_text("model: {provider: sentinel-provider, default: sentinel-model}\n")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("HERMES_HOME", str(current))
    return tmp_path, home, current


def _load_yaml(text):
    """Parse with the YAML library Hermes itself ships (ruamel.yaml); PyYAML only as a fallback.

    The supported Python 3.14 Hermes environment has ruamel.yaml but not PyYAML, so skipping on a
    missing ``yaml`` module would silently skip every round trip on the supported runtime.
    """
    try:
        from ruamel.yaml import YAML
    except ImportError:
        yaml = pytest.importorskip("yaml", reason="neither ruamel.yaml (Hermes' own) nor PyYAML is installed")
        return yaml.safe_load(text)
    return YAML(typ="safe", pure=True).load(text)


def test_print_only_setup_still_prints_and_labels_the_current_home(homes, monkeypatch, capsys):
    import onair.preflight as module
    monkeypatch.setattr(module, "run_preflight", lambda: {"ok": True, "checks": [
        {"name": "isolation", "status": "pass", "detail": "safe"}]})
    _, _, current = homes
    before = snapshot(current)
    invoke("setup", "--channel", "example", "--seed-bank", "bank.yaml", "--yes")
    out = capsys.readouterr().out
    assert "No configuration was written" in out and "--create-home" in out
    assert f"Preflight of the CURRENT Hermes home: {current}" in out
    assert "does not verify a model provider" in out
    assert snapshot(current) == before


@pytest.mark.parametrize("flag", [["--voice", "tts"], ["--agent-name", "X"], ["--overlay-port", "1"],
                                  ["--never-say", "x"]])
def test_create_only_flags_are_refused_in_print_mode(homes, flag, capsys):
    with pytest.raises(SystemExit) as exc:
        invoke("setup", "--channel", "example", "--seed-bank", "b.yaml", "--yes", *flag)
    assert exc.value.code != 0 and "--create-home" in capsys.readouterr().err


def test_create_home_writes_a_consistent_bundle_and_reads_nothing_from_current(homes, monkeypatch, capsys):
    tmp, _, current = homes
    target = tmp / UNICODE_DIR
    before = snapshot(current)
    opened = []
    recording = [True]

    def audit(event, args):
        if recording[0] and event == "open" and args and isinstance(args[0], (str, bytes, os.PathLike)):
            opened.append(os.fsdecode(args[0]))
    sys.addaudithook(audit)
    try:
        invoke("setup", "--create-home", str(target), "--channel", "example_chan", "--host-name", "Ana María",
               "--agent-name", "Robo \"Pal\"", "--never-say", "banana split", "--yes")
    finally:
        recording[0] = False
    out = capsys.readouterr().out
    assert f"Created a NEW On Air stage home: {target}" in out
    assert "Preflight was NOT run" in out and "no model and no On Air plugin installed" in out
    assert "hermes onair rehearse" in out and "hermes onair preflight" in out and "hermes model" in out
    assert "NOT a readiness check" in out
    assert not [p for p in opened if str(current) in p], opened
    assert snapshot(current) == before
    assert sorted(p.name for p in target.iterdir()) == [".env", ".onair-stage-home", "SOUL.md",
                                                       "config.yaml", "seed_bank.yaml"]
    assert stat.S_IMODE(target.stat().st_mode) == 0o700
    assert all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in target.iterdir())
    blob = "".join(p.read_text(encoding="utf-8") for p in target.iterdir())
    for secret in ("AUTH-SENTINEL", "ENV-SENTINEL", "PRIVATE-PERSONA", "sentinel-provider", "sentinel-model"):
        assert secret not in blob
    assert (target / ".env").read_text().splitlines()[-1] == "ONAIR_ALLOWED_USERS=onair-stage"
    config = _load_yaml((target / "config.yaml").read_text(encoding="utf-8"))
    settings = config["plugins"]["entries"]["on-air"]["settings"]
    assert config["plugins"]["enabled"] == ["on-air"] and "model" not in config
    assert settings["seed_bank"] == str(target / "seed_bank.yaml")
    assert settings["host_name"] == "Ana María" and settings["agent_names"] == ['Robo "Pal"']
    assert settings["never_say"] == ["banana split"] and settings["voice"] is False
    soul = (target / "SOUL.md").read_text(encoding="utf-8")
    assert 'Robo "Pal"' in soul and "Ana María" in soul
    from onair.beats import SeedBank
    assert SeedBank(settings["seed_bank"]).beats()


@pytest.mark.parametrize("voice,expected", [("captions", {"persona"}), ("tts", {"persona", "tts"})])
def test_generated_config_is_consumed_by_the_real_preflight(homes, monkeypatch, capsys, voice, expected):
    tmp, _, _ = homes
    target = tmp / "stage"
    invoke("setup", "--create-home", str(target), "--channel", "example", "--voice", voice, "--yes")
    config = _load_yaml((target / "config.yaml").read_text(encoding="utf-8"))
    config["plugins"]["entries"]["on-air"]["settings"]["overlay_port"] = 0  # any free port here
    import onair.preflight as module
    env = dict(line.split("=", 1) for line in (target / ".env").read_text().splitlines() if "=" in line and not line.startswith("#"))
    monkeypatch.setattr(module, "_scoped", lambda name: env.get(name))

    class RegisteredGate:
        def gate_status(self):
            return True, "registered (test stand-in)"
    result = module.run_preflight(config, guard=RegisteredGate())
    statuses = {c["name"]: c["status"] for c in result["checks"]}
    assert result["ok"] is True, result
    assert {n for n, s in statuses.items() if s != "pass"} == expected
    assert statuses["persona"] == "warn"


def _sentinel_dir(path):
    path.mkdir()
    (path / "keep.txt").write_text("SENTINEL")


@pytest.mark.parametrize("kind", ["empty_dir", "dir_with_files", "file", "dangling_symlink", "dir_symlink"])
def test_existing_targets_are_refused_and_preserved(homes, kind, capsys):
    tmp, _, _ = homes
    target = tmp / "existing"
    other = tmp / "other"
    _sentinel_dir(other)
    if kind == "empty_dir":
        target.mkdir()
    elif kind == "dir_with_files":
        _sentinel_dir(target)
    elif kind == "file":
        target.write_text("SENTINEL")
    elif kind == "dangling_symlink":
        target.symlink_to(tmp / "missing")
    else:
        target.symlink_to(other)
    before = snapshot(tmp)
    with pytest.raises(SystemExit) as exc:
        invoke("setup", "--create-home", str(target), "--channel", "example", "--yes")
    assert exc.value.code != 0
    err = capsys.readouterr().err
    assert "Refusing" in err and "nothing was changed" in err
    assert snapshot(tmp) == before


def test_overlap_symlinked_parent_and_unowned_parent_are_refused(homes, capsys):
    tmp, home, current = homes
    default_root = home / ".hermes"
    (default_root / "profiles").mkdir()  # parents exist: each refusal is for its own reason
    real = tmp / "real-parent"
    real.mkdir()
    link = tmp / "linked-parent"
    link.symlink_to(real)
    cases = [
        (current / "stage", f"overlaps an existing Hermes home (the current Hermes home, {current})"),
        # HERMES_HOME points elsewhere, yet ~/.hermes stays protected
        (default_root / "profiles" / "stage", f"overlaps an existing Hermes home (the default Hermes root, {default_root})"),
        (default_root / "stage", "the default Hermes root"),
        (tmp, "already exists"),
        (link / "stage", "goes through a symlink"),
        (tmp / "missing" / "stage", "parent directory does not exist"),
    ]
    if os.getuid() != 0 and os.stat("/").st_uid != os.getuid():
        cases.append(("/onair-stage-test-never-created", "not owned by you"))
    before = snapshot(tmp)
    for target, needle in cases:
        with pytest.raises(SystemExit) as exc:
            invoke("setup", "--create-home", str(target), "--channel", "example", "--yes")
        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert "Refusing" in err and needle in err and "nothing was changed" in err, (target, err)
    assert snapshot(tmp) == before
    assert not os.path.lexists("/onair-stage-test-never-created")


def test_default_root_is_protected_with_and_without_hermes_home(homes, monkeypatch, capsys):
    tmp, home, _ = homes
    (home / ".hermes" / "profiles").mkdir()
    target = home / ".hermes" / "profiles" / "stage"
    for hermes_home in (str(tmp / "current-home"), str(tmp / "stage-a"), None):
        if hermes_home is None:
            monkeypatch.delenv("HERMES_HOME", raising=False)
        else:
            monkeypatch.setenv("HERMES_HOME", hermes_home)
        with pytest.raises(SystemExit) as exc:
            invoke("setup", "--create-home", str(target), "--channel", "example", "--yes")
        assert exc.value.code == 1
        assert "the default Hermes root" in capsys.readouterr().err
        assert not target.exists()


@pytest.mark.parametrize("argv,needle", [
    (["--channel", "#not a channel!"], "Twitch channel"),
    (["--channel", "example", "--seed-bank", "relative.yaml"], "--seed-bank"),
    (["--channel", "example", "--seed-bank", "/nonexistent/bank.yaml"], "--seed-bank"),
    (["--channel", "example", "--host-name", "   "], "--host-name"),
    (["--channel", "example", "--overlay-port", "70000"], "--overlay-port"),
    (["--channel", "example", "--profanity", "allow", "--never-say", "  "], "--never-say"),
    ([], "--channel"),
])
def test_malformed_or_absent_inputs_fail_without_writing(homes, argv, needle, capsys):
    tmp, _, _ = homes
    target = tmp / "stage"
    with pytest.raises(SystemExit) as exc:
        invoke("setup", "--create-home", str(target), "--yes", *argv)
    assert exc.value.code == 1  # an On Air refusal, not an argparse usage error
    assert needle in capsys.readouterr().err
    assert not os.path.lexists(target)


def test_malformed_seed_bank_file_is_refused(homes, capsys):
    tmp, _, _ = homes
    bad = tmp / "bad.yaml"
    bad.write_text("segments: [not, beats]\n")
    with pytest.raises(SystemExit):
        invoke("setup", "--create-home", str(tmp / "stage"), "--channel", "example", "--seed-bank", str(bad), "--yes")
    assert "--seed-bank" in capsys.readouterr().err and not (tmp / "stage").exists()


def test_user_seed_bank_is_referenced_not_copied(homes, capsys):
    tmp, _, _ = homes
    bank = tmp / "my bank.json"
    bank.write_text(json.dumps({"segments": [{"name": "s", "beats": [{"id": "intro", "text": "Hi.", "provenance": "me"}]}]}))
    invoke("setup", "--create-home", str(tmp / "stage"), "--channel", "example", "--seed-bank", str(bank), "--yes")
    assert not (tmp / "stage" / "seed_bank.yaml").exists()
    config = _load_yaml((tmp / "stage" / "config.yaml").read_text(encoding="utf-8"))
    assert config["plugins"]["entries"]["on-air"]["settings"]["seed_bank"] == str(bank)


@pytest.mark.parametrize("error", [EOFError, KeyboardInterrupt])
def test_interactive_cancel_writes_nothing(homes, monkeypatch, capsys, error):
    tmp, _, _ = homes
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)

    def cancel(prompt):
        raise error
    monkeypatch.setattr("builtins.input", cancel)
    with pytest.raises(SystemExit) as exc:
        invoke("setup", "--create-home", str(tmp / "stage"))
    assert exc.value.code != 0 and "cancelled" in capsys.readouterr().err
    assert not (tmp / "stage").exists()


def test_interactive_prompts_fill_values(homes, monkeypatch, capsys):
    tmp, _, _ = homes
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    answers = iter(["example", "Presenter", "", "", ""])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    invoke("setup", "--create-home", str(tmp / "stage"))
    settings = _load_yaml((tmp / "stage" / "config.yaml").read_text())["plugins"]["entries"]["on-air"]["settings"]
    assert settings["twitch_channel"] == "example" and settings["host_name"] == "Presenter"
    assert settings["agent_names"] == ["Co-host"] and settings["profanity"] == "mask" and settings["voice"] is False


def test_interrupt_while_writing_removes_partial_home(homes, monkeypatch, capsys):
    tmp, _, _ = homes
    import onair.onboarding as onboarding
    real = onboarding._write_new
    calls = []

    def flaky(path, text, mode):
        calls.append(path)
        if len(calls) == 3:
            raise KeyboardInterrupt
        real(path, text, mode)
    monkeypatch.setattr(onboarding, "_write_new", flaky)
    with pytest.raises(SystemExit):
        invoke("setup", "--create-home", str(tmp / "stage"), "--channel", "example", "--yes")
    assert "removed" in capsys.readouterr().err and not (tmp / "stage").exists()


def test_sigterm_while_writing_removes_partial_home(homes, monkeypatch, capsys):
    import signal

    tmp, _, _ = homes
    import onair.onboarding as onboarding
    real = onboarding._write_new
    calls = []

    def flaky(path, text, mode):
        calls.append(path)
        if len(calls) == 2:
            os.kill(os.getpid(), signal.SIGTERM)  # a service stop or `timeout` mid-write
        real(path, text, mode)
    monkeypatch.setattr(onboarding, "_write_new", flaky)
    before = signal.getsignal(signal.SIGTERM)
    with pytest.raises(SystemExit) as exc:
        invoke("setup", "--create-home", str(tmp / "stage"), "--channel", "example", "--yes")
    assert exc.value.code == 1 and "partial stage home was removed" in capsys.readouterr().err
    assert not (tmp / "stage").exists()
    assert signal.getsignal(signal.SIGTERM) is before


def _signal_names():
    import signal

    return [name for name in ("SIGTERM", "SIGHUP", "SIGINT") if hasattr(signal, name)]


@pytest.mark.parametrize("when", ["just-after-mkdir", "just-before-mkdir"])
@pytest.mark.parametrize("signame", _signal_names())
def test_real_signal_at_the_exclusive_mkdir_removes_the_owned_home(homes, monkeypatch, capsys, signame, when):
    """A real signal sent to this process at the os.mkdir boundary (the old gap: the handler ran as
    mkdir returned, before cleanup was armed, leaving an empty 0700 home behind a 'removed' message)."""
    import signal

    tmp, _, _ = homes
    import onair.onboarding as onboarding
    signum = getattr(signal, signame)
    real_mkdir = os.mkdir
    target = tmp / "stage"
    seen = []

    def mkdir_with_signal(path, mode=0o777, **kw):
        if os.fspath(path) != os.fspath(target):
            return real_mkdir(path, mode, **kw)
        if when == "just-before-mkdir":
            os.kill(os.getpid(), signum)
        real_mkdir(path, mode, **kw)
        seen.append(stat.S_IMODE(os.lstat(path).st_mode))
        if when == "just-after-mkdir":
            os.kill(os.getpid(), signum)  # Python runs the handler right here, as mkdir "returns"
    monkeypatch.setattr(onboarding.os, "mkdir", mkdir_with_signal)
    handlers = {name: signal.getsignal(getattr(signal, name)) for name in _signal_names()}
    with pytest.raises(SystemExit) as exc:
        invoke("setup", "--create-home", str(target), "--channel", "example", "--yes")
    err = capsys.readouterr().err
    assert seen == [0o700 & ~_umask()]  # the directory really was created by this run
    assert exc.value.code == 1 and "partial stage home was removed" in err
    assert not os.path.lexists(target)
    assert {name: signal.getsignal(getattr(signal, name)) for name in _signal_names()} == handlers


def _umask():
    mask = os.umask(0)
    os.umask(mask)
    return mask


def test_signal_while_another_process_wins_the_mkdir_race_leaves_its_path(homes, monkeypatch, capsys):
    import signal

    tmp, _, _ = homes
    import onair.onboarding as onboarding
    real_mkdir = os.mkdir
    target = tmp / "stage"

    def lose_the_race(path, mode=0o777, **kw):
        if os.fspath(path) != os.fspath(target):
            return real_mkdir(path, mode, **kw)
        real_mkdir(path, 0o755)  # someone else creates it between check_target and our mkdir
        (target / "theirs.txt").write_text("FOREIGN\n")
        os.kill(os.getpid(), signal.SIGTERM)
        real_mkdir(path, mode, **kw)  # raises FileExistsError
    monkeypatch.setattr(onboarding.os, "mkdir", lose_the_race)
    with pytest.raises(SystemExit) as exc:
        invoke("setup", "--create-home", str(target), "--channel", "example", "--yes")
    err = capsys.readouterr().err
    assert exc.value.code == 1 and "already exists" in err and "removed" not in err
    assert (target / "theirs.txt").read_text() == "FOREIGN\n"
    assert sorted(os.listdir(target)) == ["theirs.txt"]


def test_signal_after_the_home_was_replaced_never_deletes_the_replacement(homes, monkeypatch, capsys):
    import signal

    tmp, _, _ = homes
    import onair.onboarding as onboarding
    real_write = onboarding._write_new
    target = tmp / "stage"
    moved = tmp / "ours-moved-away"

    def swap_then_signal(path, text, mode):
        real_write(path, text, mode)
        if not moved.exists():
            os.rename(target, moved)  # another actor moves our partial home away ...
            os.mkdir(target)          # ... and puts its own directory at the same path
            (target / "theirs.txt").write_text("FOREIGN\n")
            os.kill(os.getpid(), signal.SIGTERM)
    monkeypatch.setattr(onboarding, "_write_new", swap_then_signal)
    with pytest.raises(SystemExit) as exc:
        invoke("setup", "--create-home", str(target), "--channel", "example", "--yes")
    err = capsys.readouterr().err
    assert exc.value.code == 1 and "was NOT removed" in err
    assert (target / "theirs.txt").read_text() == "FOREIGN\n"
    shutil.rmtree(moved)


def test_ctrl_c_before_anything_is_created_says_nothing_was_created(homes, monkeypatch, capsys):
    tmp, _, _ = homes
    import onair.onboarding as onboarding

    def interrupted(path, mode=0o777, **kw):
        raise KeyboardInterrupt
    monkeypatch.setattr(onboarding.os, "mkdir", interrupted)
    with pytest.raises(SystemExit) as exc:
        invoke("setup", "--create-home", str(tmp / "stage"), "--channel", "example", "--yes")
    err = capsys.readouterr().err
    assert exc.value.code == 1 and "no stage home was created" in err and "removed" not in err
    assert not os.path.lexists(tmp / "stage")


# ---------------------------------------------------------------- the real CLI, installed copies


def test_installed_cli_refuses_default_root_when_hermes_home_is_custom(tmp_path):
    """The documented HERMES_HOME="$STAGE" workflow must not open a way into ~/.hermes."""
    home = tmp_path / "home"
    profiles = home / ".hermes" / "profiles"
    profiles.mkdir(parents=True)
    (home / ".hermes" / "config.yaml").write_text("SENTINEL-DEFAULT-ROOT\n")
    custom = tmp_path / "custom stage"
    install_copy(custom)
    (custom / "config.yaml").write_text(ENABLED)
    before = snapshot(home / ".hermes")  # (Hermes itself may add its launcher shim under ~/.local)
    for target in (profiles / "stage", home / ".hermes" / "stage"):
        proc = run_hermes(["onair", "setup", "--create-home", str(target), "--channel", "example", "--yes"],
                          home=home, hermes_home=custom, tmpdir=tmp_path / "t")
        assert proc.returncode == 1, proc.stdout + proc.stderr
        assert f"overlaps an existing Hermes home (the default Hermes root, {home / '.hermes'})" in proc.stderr
        assert "nothing was changed" in proc.stderr and not os.path.lexists(target)
    assert snapshot(home / ".hermes") == before


def test_installed_cli_creates_home_then_preflights_generated_vs_current(tmp_path):
    """Real Hermes CLI, installed plugin copies: generated home passes, current home does not."""
    home = tmp_path / "home"
    (home / ".hermes").mkdir(parents=True)
    current = tmp_path / "current"
    install_copy(current)
    (current / "config.yaml").write_text(ENABLED)
    (current / "auth.json").write_text('{"sentinel": "AUTH-SENTINEL"}')
    stage = tmp_path / UNICODE_DIR
    # Hermes scaffolds a home on first use; let it do that before taking the baseline.
    run_hermes(["onair", "preflight", "--json"], home=home, hermes_home=current, tmpdir=tmp_path / "t")
    before = stable(current)

    created = run_hermes(["onair", "setup", "--create-home", str(stage), "--channel", "example_chan",
                          "--yes"], home=home, hermes_home=current, tmpdir=tmp_path / "t")
    assert created.returncode == 0, created.stdout + created.stderr
    assert "Created a NEW On Air stage home" in created.stdout and "Preflight was NOT run" in created.stdout
    assert stable(current) == before
    assert "AUTH-SENTINEL" not in "".join(p.read_text() for p in stage.iterdir())

    install_copy(stage)
    passed = run_hermes(["onair", "preflight"], home=home, hermes_home=stage, tmpdir=tmp_path / "t")
    assert passed.returncode == 0, passed.stdout + passed.stderr
    assert f"Preflight of the CURRENT Hermes home: {stage}" in passed.stdout
    assert "persona: warn" in passed.stdout and "tts: pass - On Air voice is disabled (captions only)." in passed.stdout
    assert "runtime_gate: pass" in passed.stdout and "Preflight passed." in passed.stdout
    assert "does not verify a model provider or credentials" in passed.stdout
    as_json = run_hermes(["onair", "preflight", "--json"], home=home, hermes_home=stage, tmpdir=tmp_path / "t")
    assert json.loads(as_json.stdout)["ok"] is True

    failed = run_hermes(["onair", "preflight"], home=home, hermes_home=current, tmpdir=tmp_path / "t")
    assert failed.returncode != 0
    assert f"Preflight of the CURRENT Hermes home: {current}" in failed.stdout
    assert "Next actions:" in failed.stderr and "--create-home" in failed.stderr and "hermes onair rehearse" in failed.stderr
    assert stable(current) == before


def test_installed_cli_without_tty_never_prompts_or_hangs(tmp_path):
    home = tmp_path / "home"
    (home / ".hermes").mkdir(parents=True)
    current = tmp_path / "current"
    install_copy(current)
    (current / "config.yaml").write_text(ENABLED)
    proc = run_hermes(["onair", "setup", "--create-home", str(tmp_path / "stage")], home=home,
                      hermes_home=current, tmpdir=tmp_path / "t", timeout=90)
    assert proc.returncode != 0 and "--channel" in proc.stderr
    assert not (tmp_path / "stage").exists()
