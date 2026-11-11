"""macOS OBS startup bootstrap through the REAL registered onair_obs_setup handler.

Fixture authority, labelled: FakeCtx.session_surface() == "cli" stands in for the host's owner session (the
handler still runs owner_session(ctx)); FakeObs is a loopback WebSocket double, not native OBS. Nothing here
proves a native render.
"""
import hashlib
import json
import os

import pytest

from conftest import make
from fake_obs import FakeObs
from onair.presentation import agent, obs_bootstrap
from onair.presentation.obs_control import ObsConfig

C = ObsConfig()
MUTATIONS = {"CreateScene", "CreateInput", "SetInputSettings", "CreateSceneItem", "SetSceneItemIndex",
             "SetCurrentProgramScene", "SetSceneItemEnabled", "RemoveInput", "StartStream"}


def call(ctx, tool, args=None):
    return json.loads(ctx.tools[tool]["handler"](args or {}))


def served(obs, **cfg):
    ctx, _ = make(obs.url, **cfg)
    assert call(ctx, "onair_present_publish", {"sample": True})["ok"]
    return ctx


@pytest.fixture
def mac():
    fake = FakeObs(platform="macos").start()
    yield fake
    fake.stop()


def startup_dir(home):
    return home / "plugin-data" / "on-air" / "obs-startup"


def load_collection_into(fake, path):
    """What a FRESH OBS process does with the startup collection: its sources exist before any RPC."""
    coll = json.loads(path.read_bytes())
    by_uuid = {s["uuid"]: s for s in coll["sources"]}
    for scene in (s for s in coll["sources"] if s["id"] == "scene"):
        fake.scenes.setdefault(scene["name"], [])
        for item in scene["settings"]["items"]:
            src = by_uuid[item["source_uuid"]]
            fake.add_input(scene["name"], src["name"], src["id"], dict(src["settings"]))
    fake.program = coll["current_program_scene"]
    return coll


def test_blank_mac_configure_refuses_before_any_mutation_and_names_prepare(mac, owner_home):
    ctx = served(mac)
    before = len(mac.log)
    out = call(ctx, "onair_obs_setup", {"action": "configure"})
    assert out["ok"] is False and out["bootstrap_required"] is True and out["configured"] is False, out
    assert out["platform"] == "macos"
    assert "prepare_startup" in out["next_agent_action"]
    assert "fresh" in out["why"].lower() and "worker" in out["why"].lower()
    assert not [r for r in mac.log[before:] if r[0] in MUTATIONS], mac.log[before:]
    assert mac.inputs == {} and list(mac.scenes) == ["Scene"]
    assert out["startup_packet"]["prepared"] is False
    assert not startup_dir(owner_home).exists()          # refusal writes no files


def test_inspect_reports_bootstrap_need_without_files(mac, owner_home):
    ctx = served(mac)
    st = call(ctx, "onair_obs_setup", {"action": "inspect"})
    assert st["ok"] and st["startup_bootstrap_required"] is True
    assert not [r for r in mac.log if r[0] in MUTATIONS]
    assert not (owner_home / "plugin-data").exists()


def test_prepare_startup_writes_secret_free_packet_from_runtime(mac, owner_home, monkeypatch):
    monkeypatch.setenv("ONAIR_OBS_PASSWORD", "pw-must-not-appear-123")
    ctx = served(mac)
    url = agent.runtime().surface.url
    n = len(mac.log)
    out = call(ctx, "onair_obs_setup", {"action": "prepare_startup"})
    assert out["ok"] is True and out["status"] == "created" and out["applied"] is False, out
    assert len(mac.log) == n                              # offline: no OBS contact at all
    d = startup_dir(owner_home)
    assert sorted(os.listdir(d)) == sorted(obs_bootstrap.FILES)
    coll = json.loads((d / obs_bootstrap.COLLECTION_FILE).read_bytes())
    browsers = [s for s in coll["sources"] if s["id"] == "browser_source"]
    assert len(browsers) == 1                             # the real presentation source only, no overlay/voice page
    b = browsers[0]
    assert b["name"] == C.presentation_input and b["settings"]["url"] == url
    assert b["settings"]["reroute_audio"] is False and b["muted"] is True and b["monitoring_type"] == 0
    assert (b["settings"]["width"], b["settings"]["height"]) == (1920, 1080)
    assert coll["current_program_scene"] == C.presentation_scene
    req = json.loads((d / obs_bootstrap.SETTINGS_FILE).read_bytes())
    assert req["user_ini"]["General"]["FirstRun"] == "true" and req["user_ini"]["when"] == "before_first_start"
    assert req["user_ini"]["Basic"]["SceneCollection"] == coll["name"]
    assert req["loopback_target"]["url"] == url and req["loopback_target"]["resolves_on"] == "OBS_HOST"
    order = [s["step"] for s in req["start_order"]]
    assert order.index("write_owned_prestart_settings") < order.index("start_fresh_obs_with_collection") \
        < order.index("onair_obs_setup_configure")
    man = json.loads((d / obs_bootstrap.MANIFEST_FILE).read_bytes())
    for name, meta in man["files"].items():
        assert hashlib.sha256((d / name).read_bytes()).hexdigest() == meta["sha256"]
    assert out["manifest_sha256"] == hashlib.sha256((d / obs_bootstrap.MANIFEST_FILE).read_bytes()).hexdigest()
    for name in obs_bootstrap.FILES:
        blob = (d / name).read_bytes()
        assert b"pw-must-not-appear-123" not in blob and b"server_password" not in blob and b'"key"' not in blob
    assert out["state"] == "prepared_not_applied"


def test_prepare_is_idempotent_and_detects_drift_collision_symlink(mac, owner_home):
    ctx = served(mac)
    first = call(ctx, "onair_obs_setup", {"action": "prepare_startup"})
    again = call(ctx, "onair_obs_setup", {"action": "prepare_startup"})
    assert again["ok"] and again["status"] == "unchanged" and again["manifest_sha256"] == first["manifest_sha256"]
    d = startup_dir(owner_home)
    # drift: a hand edit is never overwritten
    p = d / obs_bootstrap.COLLECTION_FILE
    p.write_bytes(p.read_bytes() + b" ")
    drift = call(ctx, "onair_obs_setup", {"action": "prepare_startup"})
    assert drift["ok"] is False and drift["status"] == "drift" and p.read_bytes().endswith(b" ")
    # collision: our file names present without our manifest
    (d / obs_bootstrap.MANIFEST_FILE).unlink()
    col = call(ctx, "onair_obs_setup", {"action": "prepare_startup"})
    assert col["ok"] is False and col["status"] == "collision"
    # symlink anywhere in the owned namespace is refused, nothing followed
    for f in os.listdir(d):
        (d / f).unlink()
    d.rmdir()
    target = owner_home / "elsewhere"
    target.mkdir()
    d.symlink_to(target)
    sym = call(ctx, "onair_obs_setup", {"action": "prepare_startup"})
    assert sym["ok"] is False and sym["status"] == "refused_symlink" and os.listdir(target) == []


def test_prepare_updates_own_unmodified_packet_when_runtime_changes(mac, owner_home):
    ctx = served(mac)
    first = call(ctx, "onair_obs_setup", {"action": "prepare_startup"})
    agent.runtime().stop_serving()
    assert call(ctx, "onair_present_control", {"action": "serve"})["ok"]
    out = call(ctx, "onair_obs_setup", {"action": "prepare_startup"})
    if agent.runtime().surface.url == json.loads((startup_dir(owner_home) / obs_bootstrap.SETTINGS_FILE)
                                                  .read_bytes())["loopback_target"]["url"] and out["status"] == "unchanged":
        return  # same ephemeral port reused: nothing to update
    assert out["ok"] and out["status"] == "updated" and out["previous_manifest_sha256"] == first["manifest_sha256"]


def test_prepare_requires_serving_surface_and_writes_nothing(mac, owner_home):
    ctx, _ = make(mac.url)
    out = call(ctx, "onair_obs_setup", {"action": "prepare_startup"})
    assert out["ok"] is False and "serv" in out["error"]
    assert not (owner_home / "plugin-data").exists()


@pytest.mark.parametrize("surface", ["telegram", "on_air", ""])
def test_non_owner_callers_get_no_files_and_no_obs(mac, owner_home, surface):
    ctx, _ = make(mac.url, surface=surface)
    out = call(ctx, "onair_obs_setup", {"action": "prepare_startup"})
    assert out["refused"] is True
    out = call(ctx, "onair_obs_setup", {"action": "configure"})
    assert out["refused"] is True
    assert mac.log == [] and not (owner_home / "plugin-data").exists()


def test_stage_home_registers_nothing(mac, owner_home):
    from onair.onboarding import MARKER
    (owner_home / MARKER).write_text("stage")
    ctx, done = make(mac.url)
    assert done == [] and ctx.tools == {}


def test_consumed_packet_then_primed_mac_configure_and_same_show_controls(mac, owner_home):
    ctx = served(mac)
    assert call(ctx, "onair_obs_setup", {"action": "configure"})["bootstrap_required"] is True
    prep = call(ctx, "onair_obs_setup", {"action": "prepare_startup"})
    coll = load_collection_into(mac, startup_dir(owner_home) / obs_bootstrap.COLLECTION_FILE)
    assert list(mac.inputs) == [C.presentation_input]
    before = len(mac.log)
    out = call(ctx, "onair_obs_setup", {"action": "configure"})
    assert out["ok"] is True and out["configured"] is True and out.get("bootstrap_required") is not True, out
    assert out["render"]["verified"] is False and "not" in out["render"]["note"].lower()
    created = [r for r in mac.log[before:] if r[0] == "CreateInput"]
    assert [r[1]["inputName"] for r in created] == [C.overlay_input]     # presentation reused, not re-created
    assert out["sources"]["speech_audio"]["ok"] is True and out["sources"]["speech_audio"]["overlay_clients"] == 1
    assert prep["startup_packet"]["collection_name"] == coll["name"]
    for step in ({"action": "next"}, {"action": "standby", "title": "Back soon"}, {"action": "resume"}):
        assert call(ctx, "onair_present_control", step)["ok"], step
    assert call(ctx, "onair_present_publish", {"sample": True, "start": 1})["ok"]
    sc = call(ctx, "onair_obs_scene", {"scene": C.presentation_scene})
    assert sc["ok"] and mac.program == C.presentation_scene
    assert not mac.requests("StartStream")


def test_non_mac_blank_configure_unchanged(obs):
    ctx = served(obs)
    out = call(ctx, "onair_obs_setup", {"action": "configure"})
    assert out["ok"] is True and out["configured"] is True and not out.get("bootstrap_required")
    assert out["render"]["verified"] is False


def test_primed_mac_with_foreign_browser_source_configures(mac):
    mac.add_input("Scene", "Somebody's page", "browser_source", {"url": "http://127.0.0.1:9/x"})
    ctx = served(mac)
    out = call(ctx, "onair_obs_setup", {"action": "configure"})
    assert out["ok"] is True and out["configured"] is True
    assert "Somebody's page" in mac.inputs and mac.inputs["Somebody's page"]["settings"]["url"] == "http://127.0.0.1:9/x"
