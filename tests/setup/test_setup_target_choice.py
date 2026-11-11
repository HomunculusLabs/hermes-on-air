"""First-run target selection remains read-only until owner consent."""
import json
import os
from pathlib import Path

import pytest

from onair import setup_agent as sa, setup_engine as se
from test_quick_setup import FakeCtx, LoopbackFixtureModel, model_server, owner, wait_done  # noqa: F401


def call(eng, args):
    return json.loads(sa.make_handlers(FakeCtx(), get_engine=lambda: eng)["onair_setup_plan"](args))


def test_foreign_default_and_explicit_target(owner, tmp_path):
    home = Path(os.environ["HOME"])
    (home / "onair-stage").mkdir()
    eng = se.SetupEngine(owner)
    plan = call(eng, {"channel": "test_channel"})
    assert plan["ok"] and plan["target"] == str(home / "onair-stage-2")
    assert any("already exists" in line for line in plan["effects"])
    assert not Path(plan["target"]).exists()
    custom = home / "chosen"
    plan = call(eng, {"channel": "test_channel", "stage_home": str(custom)})
    assert plan["ok"] and plan["target"] == str(custom)
    assert str(custom) in sa.approve_text(plan) and not custom.exists()
    ctx = FakeCtx()
    try:
        sa.make_command(ctx, get_engine=lambda: eng)("approve " + plan["code"])
        assert str(custom) in ctx.asked[-1][0]
        job = wait_done(eng)
        assert job["state"] == "done" and job["home"] == str(custom), job
    finally:
        eng.stop_preview()


def test_fallback_approval_and_job(owner, tmp_path, monkeypatch, model_server):
    # The configured HOME is itself a Hermes home, nested in the owner's account home.
    account_home = Path(os.environ["HOME"])
    nested = account_home / "private"
    nested.mkdir()
    (nested / "config.yaml").write_text((owner / "config.yaml").read_text())
    monkeypatch.setenv("HOME", str(nested))
    monkeypatch.setenv("HERMES_HOME", str(nested))
    monkeypatch.setattr("onair.setup_wizard.account_home", lambda: account_home)
    server, _ = model_server
    eng = se.SetupEngine(nested, model_client=LoopbackFixtureModel(server.server_address[1]))
    ctx = FakeCtx(llm=eng.model_client)
    handler = sa.make_handlers(ctx, get_engine=lambda: eng)["onair_setup_plan"]
    plan = json.loads(handler({"channel": "test_channel", "use_model": True, "style": {"tone": "relaxed"}}))
    assert plan["ok"] and plan["target"] == str(account_home / "onair-stage")
    assert "overlaps" in " ".join(plan["effects"])
    assert not Path(plan["target"]).exists()
    try:
        sa.make_command(ctx, get_engine=lambda: eng)("approve " + plan["code"])
        assert plan["target"] in ctx.asked[-1][0]
        job = wait_done(eng)
        assert job["state"] == "done", job
        assert job["home"] == plan["target"]
        assert all(step["state"] == "done" for step in job["steps"])
    finally:
        eng.stop_preview()


@pytest.mark.parametrize("bad,rule", [("relative", "absolute"), ("traversal", "traversal"),
                                        ("inside", "overlaps"), ("symlink", "already exists"),
                                        ("parent_symlink", "symlink"), ("existing", "already exists"),
                                        ("file", "already exists")])
def test_custom_refusals(owner, tmp_path, bad, rule):
    home = Path(os.environ["HOME"])
    (home / "existing").mkdir()
    (home / "existing" / "foreign").write_text("keep")
    (home / "file").write_text("keep")
    (home / "link").symlink_to(home / "existing", target_is_directory=True)
    targets = {"relative": "relative", "traversal": str(home / "x" / ".." / "bad"),
               "inside": str(owner / "bad"), "symlink": str(home / "link"),
               "parent_symlink": str(home / "link" / "bad"), "existing": str(home / "existing"),
               "file": str(home / "file")}
    eng = se.SetupEngine(owner)
    result = call(eng, {"channel": "test_channel", "stage_home": targets[bad]})
    assert not result["ok"] and rule in result["errors"]["target"]
    assert result["fallback_target"] == str(home / "onair-stage")
    assert not (home / "onair-stage").exists() and not (home / "bad").exists()


def test_default_and_no_possible_fallback(owner, monkeypatch):
    from onair.setup_wizard import NO_TARGET_ACTION
    home = Path(os.environ["HOME"])
    monkeypatch.setattr("onair.setup_wizard.account_home", lambda: home)
    eng = se.SetupEngine(owner)
    plan = call(eng, {"channel": "test_channel"})
    assert plan["ok"] and plan["target"] == str(home / "onair-stage")
    assert not any("default stage folder was not used" in e for e in plan["effects"])
    assert not (home / "onair-stage").exists()
    monkeypatch.setenv("HERMES_HOME", str(home))
    refused = call(se.SetupEngine(home), {"channel": "test_channel"})
    assert not refused["ok"] and NO_TARGET_ACTION in refused["errors"]["target"]
    assert not any((home / ("onair-stage" + suffix)).exists() for suffix in ("", "-2", "-3", "-4"))


def test_foreign_parent_and_outside_home_refused(owner, monkeypatch):
    home = Path(os.environ["HOME"])
    monkeypatch.setattr("onair.setup_wizard.account_home", lambda: home)
    parent = home / "foreign"
    parent.mkdir()
    original = Path.stat
    def foreign_stat(path, *args, **kwargs):
        result = original(path, *args, **kwargs)
        if path == parent:
            values = list(result)
            values[4] = result.st_uid + 1
            return os.stat_result(values)
        return result
    monkeypatch.setattr(Path, "stat", foreign_stat)
    eng = se.SetupEngine(owner)
    result = call(eng, {"channel": "test_channel", "stage_home": str(parent / "new")})
    assert not result["ok"] and "not owned" in result["errors"]["target"]
    assert result["fallback_target"] == str(home / "onair-stage")
    outside = call(eng, {"channel": "test_channel", "stage_home": str(home.parent / "outside")})
    assert not outside["ok"] and "under your home" in outside["errors"]["target"]
    assert not (parent / "new").exists() and not (home / "onair-stage").exists()
