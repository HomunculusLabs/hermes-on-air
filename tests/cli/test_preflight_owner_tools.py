"""Preflight refuses to run the stage from a home that has On Air's owner tools.

Hermes offers a plugin's toolsets to every platform unless they are recorded as known and left unselected for
it. The owner tools are registered in the owner's own home only, so a home that has them and also runs the
stage would put them in every co-host request, and the runtime gate would refuse each reply.
"""
from onair import preflight
from onair.preflight import run_preflight


def owner_tools(config=None, **kwargs):
    result = run_preflight(config=config if config is not None else {}, settings={}, **kwargs)
    return next(c for c in result["checks"] if c["name"] == "owner_tools"), result


def test_a_home_without_owner_tools_passes(monkeypatch):
    monkeypatch.setattr(preflight, "_OWNER_TOOLSETS", set())
    check, _ = owner_tools()
    assert check["status"] == "pass"


def test_owner_tools_registered_in_this_process_fail_and_name_the_stage_home(monkeypatch):
    monkeypatch.setattr(preflight, "_OWNER_TOOLSETS", set())
    preflight.note_owner_toolset("onair_setup")
    preflight.note_owner_toolset("onair_present")
    check, result = owner_tools()
    assert check["status"] == "fail" and result["ok"] is False
    assert "onair_present, onair_setup" in check["detail"] and "--create-home" in check["detail"]


def test_checking_another_home_uses_the_toolsets_named_for_it(monkeypatch):
    monkeypatch.setattr(preflight, "_OWNER_TOOLSETS", {"onair_setup"})
    assert owner_tools(owner_toolsets=())[0]["status"] == "pass"  # a marked stage home, checked from the owner's
    assert owner_tools(owner_toolsets=("onair_setup",))[0]["status"] == "fail"


def test_toolsets_recorded_as_known_and_unselected_are_not_offered(monkeypatch):
    monkeypatch.setattr(preflight, "_OWNER_TOOLSETS", {"onair_setup", "onair_present"})
    assert owner_tools({"known_plugin_toolsets": {"on_air": ["onair_setup", "onair_present"]}})[0]["status"] == "pass"
    part, _ = owner_tools({"known_plugin_toolsets": {"on_air": ["onair_setup"]}})
    assert part["status"] == "fail" and "onair_present" in part["detail"] and "onair_setup" not in part["detail"]
    for odd in (None, "onair_setup", {"on_air": "onair_setup"}, {"on_air": None}):
        assert owner_tools({"known_plugin_toolsets": odd})[0]["status"] == "fail"
