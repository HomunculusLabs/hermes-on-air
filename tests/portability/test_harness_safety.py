"""The install harness itself must be safe to run on anyone's machine (no Hermes needed)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from ._support import ROOT, load_harness

vi = load_harness()


def test_child_environment_carries_no_caller_secrets_or_paths(tmp_path, monkeypatch):
    for name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "TWITCH_OAUTH", "ONAIR_TWITCH_CHANNEL",
                 "PYTHONPATH", "HERMES_PROFILE", "VIRTUAL_ENV"):
        monkeypatch.setenv(name, "caller-value-must-not-leak")
    home = tmp_path / "home dir"
    env = vi.build_env(tmp_path, home, home / ".hermes")
    assert "caller-value-must-not-leak" not in json.dumps(env)
    assert env["HOME"] == str(home) and env["HERMES_HOME"] == str(home / ".hermes")
    assert env["PYTHONDONTWRITEBYTECODE"] == "1" and env["HERMES_DISABLE_LAZY_INSTALLS"] == "1"
    # Every proxy variable points at a closed loopback port: a stray request fails, loopback works.
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        assert env[name] == vi.DEAD_PROXY
    assert "127.0.0.1" in env["NO_PROXY"]


def test_refuses_a_work_directory_inside_the_plugin_tree(tmp_path):
    verifier = vi.Verifier(ROOT, tmp_path / "python", ROOT / "tmp-work", "x-")
    with pytest.raises(vi.HarnessError, match="outside the plugin source tree"):
        verifier.check_inputs()


def test_refuses_a_tree_that_is_not_this_plugin(tmp_path):
    (tmp_path / "plugin.yaml").write_text("name: something-else\n", encoding="utf-8")
    (tmp_path / "__init__.py").write_text("", encoding="utf-8")
    verifier = vi.Verifier(tmp_path, tmp_path / "python", tmp_path.parent / "work", "x-")
    with pytest.raises(vi.HarnessError, match="on-air"):
        verifier.check_inputs()


def test_refuses_a_report_inside_the_plugin_tree(capsys):
    assert vi.main(["--report", str(ROOT / "report.json")]) == 2
    assert "outside the plugin tree" in capsys.readouterr().err
    assert not (ROOT / "report.json").exists()


def test_cleanup_deletes_only_a_root_it_still_owns(tmp_path):
    verifier = vi.Verifier(ROOT, tmp_path / "python", tmp_path / "work", "owned ü-")
    verifier.create_root()
    root = verifier.root
    assert " " in root.name and "ü" in root.name
    (root / vi.OWNED_MARKER).write_text("someone else", encoding="utf-8")
    step = verifier.cleanup()
    assert step.status == "fail" and root.exists()
    (root / vi.OWNED_MARKER).write_text(verifier.token, encoding="utf-8")
    step = verifier.cleanup()
    assert step.status == "pass" and not root.exists()


def test_owned_home_answers_the_catalog_lookup_offline(tmp_path):
    verifier = vi.Verifier(ROOT, tmp_path / "python", tmp_path / "work", "cat-")
    verifier.create_root()
    try:
        cache = verifier.hermes_home / "cache" / "plugin-catalog.json"
        assert json.loads(cache.read_text(encoding="utf-8")) == {"entries": [], "removed": []}
        assert verifier.seed_bank.is_file() and "空白" in str(verifier.seed_bank)
    finally:
        assert verifier.cleanup().status == "pass"


def test_report_paths_are_sanitized(tmp_path):
    verifier = vi.Verifier(ROOT, tmp_path / "py", tmp_path / "work", "s-")
    verifier.create_root()
    try:
        text = "error at %s/home dir/.hermes and %s/onair" % (verifier.root, ROOT)
        cleaned = verifier.sanitize(text)
        assert str(verifier.root) not in cleaned and str(ROOT) not in cleaned
        assert "<ROOT>/home dir/.hermes" in cleaned and "<SRC>/onair" in cleaned
        # Git and installers print file:// URIs (percent-encoded) and may wrap long paths.
        uri = (verifier.root / "plugin source ü 空白").as_uri()
        cleaned = verifier.sanitize("Cloning %s...\n%s" % (uri, str(tmp_path)[:20]))
        assert uri not in cleaned and "file://<ROOT>/" in cleaned
        assert str(tmp_path.home()) not in verifier.sanitize(str(tmp_path.home() / "elsewhere"))
    finally:
        verifier.cleanup()


@pytest.mark.parametrize("version,spec,expected", [
    ("0.22.7", ">=0.22.7", True), ("0.22.10", ">=0.22.7", True), ("0.23", ">=0.22.7", True),
    ("0.22.6", ">=0.22.7", False), ("0.0.0", ">=0.22.7", False), ("1.0.0", ">=0.22.7", True),
    ("0.22.7", "~=0.22", None), ("", ">=0.22.7", None),
])
def test_minimum_version_comparison(version, spec, expected):
    assert vi.satisfies_minimum(version, spec) is expected


def test_unsafe_variants_cover_each_isolation_check(tmp_path):
    names = set().union(*(must for _, _, _, must in vi.unsafe_variants(tmp_path / "bank.json")))
    assert names == {"toolsets", "memory", "agent_runtime", "trusted_inbound", "enabled", "allowed_users"}
    safe = vi.safe_config(tmp_path / "bank.json")
    settings = safe["plugins"]["entries"]["on-air"]["settings"]
    assert settings["voice"] is False, "the baseline is the captions-only path"
    assert vi.CANARY not in json.dumps(safe)


def test_dead_proxies_are_dropped_only_for_opted_in_dependency_steps(tmp_path):
    blocked = vi.build_env(tmp_path, tmp_path / "h", tmp_path / "h" / ".hermes")
    opened = vi.build_env(tmp_path, tmp_path / "h", tmp_path / "h" / ".hermes", network=True)
    assert blocked["HTTPS_PROXY"] == vi.DEAD_PROXY and blocked["https_proxy"] == vi.DEAD_PROXY
    assert not any(k.lower().endswith("_proxy") and k.lower() != "no_proxy" for k in opened)
    for env in (blocked, opened):  # the owned home is used either way
        assert env["HOME"] == str(tmp_path / "h") and "PYTHONPATH" not in env


# Install-failure outputs, shaped like Hermes 0.22.7's: a blocked uv fetch (stdout/stderr of a real
# run behind the dead proxies, trimmed), a missing Hermes module, and a manifest refusal (the
# ``requires hermes <spec>, running <version>`` format of ``requires_hermes_error``), which the plugin
# manager reports before it prepares any runtime.
_UV_FETCH_BLOCKED = (
    "Cloning file://<SRC>...\nError: Plugin 'on-air' was not published: venv: uv sync exited 2:  Failed to fetch: "
    "https://files.example.invalid/packaging-26.3-py3-none-any.whl\n  cause: error sending request for url\n"
    "  cause: Connection refused (os error 111)\n",
    "Preparing the isolated Hermes runtime…\n  ✗ Installing Python dependencies failed\n"
    "      error: Failed to download `packaging==26.3`\n")
_NO_HERMES_MODULE = ("", "<HERMES_PYTHON>: No module named hermes_cli.main\n")
_MANIFEST_REFUSED = ("Cloning file://<SRC>...\nError: Plugin 'on-air' requires hermes >=99.0, running 0.22.7\n", "")


def _failed_install(tmp_path, monkeypatch, output, allow_downloads):
    verifier = vi.Verifier(ROOT, tmp_path / "python", tmp_path / "work", "hint-", allow_downloads=allow_downloads)
    verifier.create_root()
    try:
        monkeypatch.setattr(verifier, "_source_ref", lambda staged: (staged, "0" * 40))
        monkeypatch.setattr(verifier, "run", lambda args, **kw: (1, output[0], output[1], 0.1, ["<HERMES_PYTHON>"]))
        assert verifier._install_with_plugin_manager(verifier.root) == (False, "")
    finally:
        assert verifier.cleanup().status == "pass"
    (step,) = verifier.steps
    assert step.name == "install_plugin_manager" and step.status == "fail"
    return step.detail


def test_blocked_dependency_fetch_is_attributed_to_the_fixture(tmp_path, monkeypatch):
    detail = _failed_install(tmp_path, monkeypatch, _UV_FETCH_BLOCKED, allow_downloads=False)
    assert "a fixture choice, not a product defect" in detail and "--allow-dependency-downloads" in detail


@pytest.mark.parametrize("output", [_NO_HERMES_MODULE, _MANIFEST_REFUSED], ids=["no-hermes-module", "manifest"])
def test_other_install_failures_are_not_attributed(tmp_path, monkeypatch, output):
    detail = _failed_install(tmp_path, monkeypatch, output, allow_downloads=False)
    assert "fixture" not in detail and "product defect" not in detail and "--allow-dependency-downloads" not in detail
    assert "stdout/stderr" in detail


def test_fetch_failure_with_downloads_allowed_points_at_the_network_not_the_fixture(tmp_path, monkeypatch):
    detail = _failed_install(tmp_path, monkeypatch, _UV_FETCH_BLOCKED, allow_downloads=True)
    assert "fixture" not in detail and "network" in detail


@pytest.mark.parametrize("text,expected", [
    (_UV_FETCH_BLOCKED[0] + _UV_FETCH_BLOCKED[1], True),
    ("uv sync exited 2: Failed to download `x`", True),
    ("Connection refused", False),            # no uv sync: some other client, not the dependency step
    ("uv sync exited 2: resolution failed", False),
    (_NO_HERMES_MODULE[1], False),
    (_MANIFEST_REFUSED[0], False),
])
def test_dependency_fetch_signature(text, expected):
    assert vi.dependency_fetch_failed(text) is expected


def test_unknown_install_mode_and_orphan_bootstrap_are_refused(tmp_path):
    with pytest.raises(vi.HarnessError):
        vi.Verifier(tmp_path, Path(sys.executable), tmp_path / "w", "r", install_mode="pip")
    src = tmp_path / "src"
    src.mkdir()
    (src / "plugin.yaml").write_text("name: on-air\n", encoding="utf-8")
    (src / "__init__.py").write_text("", encoding="utf-8")
    verifier = vi.Verifier(src, Path(sys.executable), tmp_path / "w", "r", bootstrap_launcher=True)
    with pytest.raises(vi.HarnessError):
        verifier.check_inputs()


def test_scene_install_verification_detects_missing_packaging(tmp_path):
    verifier = vi.Verifier(tmp_path, Path(sys.executable), tmp_path, 'owned')
    verifier.root = tmp_path
    verifier.plugin_dir = tmp_path / 'installed'
    verifier.check_inputs = lambda: None
    verifier.create_root = lambda: None
    verifier.cleanup = lambda: vi.Step('cleanup', 'pass', 'fixture')
    verifier.step_bootstrap = verifier.step_runtime = verifier.step_install = lambda: True
    verifier.step_preflight = verifier.step_cli = verifier.step_loader = verifier.step_remove = lambda: None
    verifier.run = lambda args, **kwargs: (0, 'scene', '', 0, args)
    report = verifier.run_all()
    checks = {step['name']: step['status'] for step in report['steps']}
    assert checks.get('scene_starter_files') == 'fail'
    assert checks.get('scene_skills') == 'fail'
    assert checks.get('scene_cli') == 'pass'
    assert not report['ok']
