"""A genuine fresh install, driven through the harness, from a source path with spaces and Unicode.

Needs an interpreter that has Hermes installed: ``ONAIR_HERMES_PYTHON=/path/to/python``, or run
pytest with that interpreter. Without one the test is skipped and says so: a skip is not a pass.

``ONAIR_VERIFY_ALLOW_DOWNLOADS=1`` runs the plugin-manager install (``--install-mode installer
--allow-dependency-downloads``); without it the test uses the NARROWER copy layout, because the
plugin manager has no dependency source in an empty owned home. ``ONAIR_HERMES_LAUNCHER`` (plus
``ONAIR_VERIFY_BOOTSTRAP_LAUNCHER=1`` for a fresh home) runs the CLI steps through that launcher.
"""
from __future__ import annotations

import json
import subprocess
import sys

import pytest

import os
from pathlib import Path

from ._support import HARNESS, copy_plugin_tree, hermes_python, load_harness

INSTALLER = os.environ.get("ONAIR_VERIFY_ALLOW_DOWNLOADS") == "1"
LAUNCHER = os.environ.get("ONAIR_HERMES_LAUNCHER")
BOOTSTRAP = os.environ.get("ONAIR_VERIFY_BOOTSTRAP_LAUNCHER") == "1"
INSTALL_STEPS = {"install_plugin_manager", "installed_tree_matches_source"} if INSTALLER else {"install_copy_layout"}
REQUIRED_STEPS = INSTALL_STEPS | ({"launcher_bootstrap"} if LAUNCHER and BOOTSTRAP else set()) | {
    "hermes_runtime", "discover_list", "preflight_safe_json", "preflight_safe_text",
    "preflight_refuses_toolsets_empty_list", "preflight_refuses_memory_enabled",
    "preflight_refuses_codex_app_server", "preflight_refuses_trusted_inbound",
    "preflight_refuses_platform_disabled", "preflight_refuses_allowed_users_wrong",
    "preflight_refuses_port_in_use", "preflight_malformed_config", "check_line_allow", "check_line_withhold", "check_line_chat_bait",
    "status_no_show", "status_stale_state", "report_no_receipts", "setup_noninteractive_missing",
    "setup_noninteractive_flags", "rehearse_offline", "setup_create_home", "setup_create_home_refuses_existing",
    "setup_create_home_refuses_overlap", "scene_starter_files", "scene_skills", "scene_cli",
    "loader_connect_status_teardown", "uninstall", "uninstall_command_gone",
    "source_untouched", "cleanup",
}


@pytest.fixture(scope="module")
def hermes():
    if LAUNCHER:  # the harness runs its in-Hermes checks on the launcher's own runtime
        return hermes_python() or Path(sys.executable)
    path = hermes_python()
    if path is None:
        pytest.skip("no Hermes interpreter: set ONAIR_HERMES_PYTHON (this is a skip, not a pass)")
    probe = subprocess.run([str(path), "-B", "-c", "import hermes_cli.main"], capture_output=True, text=True,
                           timeout=120, stdin=subprocess.DEVNULL)
    if probe.returncode != 0:
        pytest.skip("ONAIR_HERMES_PYTHON cannot import hermes_cli.main: " + probe.stderr[-300:])
    return path


def test_harness_step_list_matches_this_test():
    """Keep this test's expectations in step with the harness, so a renamed step cannot vanish."""
    source = HARNESS.read_text(encoding="utf-8")
    for name in REQUIRED_STEPS | {"install_plugin_manager", "installed_tree_matches_source",
                                  "install_copy_layout", "launcher_bootstrap"}:
        label = name.removeprefix("preflight_refuses_")  # most refusal steps are named from a variant label
        assert f'"{name}"' in source or f'"{label}"' in source, name


def _skip_only_unverifiable_runtime(result, failed):
    # The harness remains strict. An unknown identity is a missing test prerequisite,
    # not a successful minimum-version check; mismatches and old versions still fail.
    if result.get("facts", {}).get("hermes_identity_verdict") == "unverifiable" and set(failed) == {"hermes_runtime"}:
        pytest.skip("selected Hermes runtime provenance cannot be established; strict minimum version unverified")


def test_runtime_prerequisite_skip_is_narrow():
    with pytest.raises(pytest.skip.Exception, match="provenance"):
        _skip_only_unverifiable_runtime({"facts": {"hermes_identity_verdict": "unverifiable"}}, {"hermes_runtime": "unknown"})
    for verdict, failed in (("fail", {"hermes_runtime": "too old"}),
                            ("pass", {"hermes_runtime": "other error"}),
                            ("unverifiable", {"hermes_runtime": "unknown", "cleanup": "failed"})):
        _skip_only_unverifiable_runtime({"facts": {"hermes_identity_verdict": verdict}}, failed)


def test_fresh_install_from_spaced_unicode_paths(hermes, tmp_path):
    src = copy_plugin_tree(tmp_path / "plugin src ü 空白")
    work = tmp_path / "work base é"
    report = tmp_path / "report.json"
    mode = ["--install-mode", "installer", "--allow-dependency-downloads"] if INSTALLER else ["--install-mode", "copy"]
    mode += ["--strict-version"]  # an unknown runtime identity is a hard failure here, never a limitation
    if LAUNCHER:
        mode += ["--hermes-launcher", LAUNCHER] + (["--bootstrap-launcher"] if BOOTSTRAP else [])
    proc = subprocess.run(
        [sys.executable, "-B", str(HARNESS), "--plugin-src", str(src), "--hermes-python", str(hermes),
         "--work-base", str(work), "--report", str(report), *mode],
        capture_output=True, text=True, timeout=900, stdin=subprocess.DEVNULL, encoding="utf-8")
    assert report.is_file(), proc.stdout[-3000:] + proc.stderr[-3000:]
    result = json.loads(report.read_text(encoding="utf-8"))
    by_name = {step["name"]: step for step in result["steps"]}
    failed = {n: s["detail"] for n, s in by_name.items() if s["status"] == "fail"}
    _skip_only_unverifiable_runtime(result, failed)
    assert proc.returncode == 0 and not failed, json.dumps(failed, ensure_ascii=False)[:4000]
    missing = REQUIRED_STEPS - {n for n, s in by_name.items() if s["status"] == "pass"}
    assert not missing, missing
    assert result["facts"]["install_mode"] == ("installer" if INSTALLER else "copy")
    # The report shows the argv that ran, not a prettified "hermes".
    assert by_name["discover_list"]["command"][0] in ("<HERMES_PYTHON>", "<HERMES_LAUNCHER>")
    loader = json.loads(by_name["loader_connect_status_teardown"]["detail"])
    assert loader["adapter_module"].startswith("hermes_plugins.")
    assert loader["status_live_mode"] == "off_air" and loader["control_file_after_disconnect"] is False
    # The owned root is gone, nothing is left under the work base, and the source tree is unchanged.
    assert list(work.iterdir()) == []
    assert not list(src.rglob("__pycache__"))
    # The report is shareable: the owned root and the source path are replaced by placeholders.
    text = report.read_text(encoding="utf-8")
    assert str(src) not in text and result["root"].startswith("<ROOT>")


def test_harness_refuses_a_missing_hermes_interpreter(tmp_path):
    vi = load_harness()
    src = copy_plugin_tree(tmp_path / "src")
    assert vi.main(["--plugin-src", str(src), "--hermes-python", str(tmp_path / "nope" / "python"),
                    "--work-base", str(tmp_path / "work")]) == 2
