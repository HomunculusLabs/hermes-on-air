"""Fit the OWNED Browser Sources to the OBS base canvas, through the real registered onair_obs_setup handler.

A 1920x1080 presentation page on a 1280x720 canvas used to sit unscaled at (0,0), so the program showed only its
top-left 1280x720 region. configure must read the canvas from GetVideoSettings baseWidth/baseHeight (not the
output scaling), set fit-inside bounds on every owned item with SetSceneItemTransform, read each one back with
GetSceneItemTransform, and report anything it could not confirm as NOT fitted / NOT configured.

Fixture authority, labelled: FakeCtx("cli") stands in for the owner session; FakeObs is a loopback WebSocket double
whose request validation mirrors obs-websocket e28f8497. Nothing here proves native pixels.
"""
import json

import pytest

from conftest import make
from fake_obs import TRANSFORM_FIELDS, FakeObs
from onair.presentation import obs_bootstrap
from onair.presentation.obs_control import ObsConfig
from test_obs_startup import load_collection_into, startup_dir

C = ObsConfig()
FIT = {"positionX": 0.0, "positionY": 0.0, "rotation": 0.0, "alignment": 5, "boundsType": "OBS_BOUNDS_SCALE_INNER",
       "boundsAlignment": 0}
OWNED_ITEMS = {(C.presentation_scene, C.presentation_input), (C.presentation_scene, C.overlay_input),
               (C.window_scene, C.overlay_input)}


def call(ctx, tool, args=None):
    return json.loads(ctx.tools[tool]["handler"](args or {}))


def served(obs, **cfg):
    ctx, _ = make(obs.url, **cfg)
    assert call(ctx, "onair_present_publish", {"sample": True})["ok"]
    return ctx


def canvas(fake, w=1280, h=720, ow=640, oh=360):
    fake.video = {"fpsNumerator": 30, "fpsDenominator": 1, "baseWidth": w, "baseHeight": h,
                  "outputWidth": ow, "outputHeight": oh}
    return fake


@pytest.fixture
def mac():
    fake = FakeObs(platform="macos").start()
    yield fake
    fake.stop()


def transforms_sent(fake, start=0):
    out = []
    for kind, d in fake.log[start:]:
        if kind == "SetSceneItemTransform":
            name = next(i["sourceName"] for i in fake.scenes[d["sceneName"]] if i["sceneItemId"] == d["sceneItemId"])
            out.append((d["sceneName"], name, d))
    return out


def assert_fitted(fake, scene, name, w, h):
    t = fake.item(scene, name)["_transform"]
    for k, v in FIT.items():
        assert t[k] == v, (scene, name, k, t[k])
    assert (t["boundsWidth"], t["boundsHeight"]) == (w, h), (scene, name)


def test_configure_fits_every_owned_item_to_the_base_canvas_not_the_output(obs):
    canvas(obs, 1280, 720, 640, 360)
    ctx = served(obs)
    out = call(ctx, "onair_obs_setup", {"action": "configure"})
    assert out["ok"] is True and out["configured"] is True, out
    fit = out["fit"]
    assert fit["fitted"] is True and fit["canvas"]["base_width"] == 1280 and fit["canvas"]["base_height"] == 720
    assert "640" not in json.dumps(fit["canvas"])          # output scaling is never used as the canvas
    assert {(i["scene"], i["source"]) for i in fit["items"]} == OWNED_ITEMS
    for item in fit["items"]:
        assert item["fitted"] is True and item["readback"]["boundsWidth"] == 1280, item
        assert item["readback"]["boundsType"] == "OBS_BOUNDS_SCALE_INNER"
    for scene, name in OWNED_ITEMS:
        assert_fitted(obs, scene, name, 1280, 720)
    # the fit is read back AFTER it was set, for each item
    sets = [n for n, (k, _d) in enumerate(obs.log) if k == "SetSceneItemTransform"]
    gets = [n for n, (k, _d) in enumerate(obs.log) if k == "GetSceneItemTransform"]
    assert len(sets) == 3 and all(any(g > s for g in gets) for s in sets)
    assert not obs.requests("SetVideoSettings") and obs.program == "Scene" and not obs.requests("StartStream")


def test_only_supported_v5_transform_fields_are_sent(obs):
    canvas(obs)
    ctx = served(obs)
    assert call(ctx, "onair_obs_setup", {"action": "configure"})["configured"] is True
    sent = transforms_sent(obs)
    assert sent
    for _scene, _name, d in sent:
        assert set(d) == {"sceneName", "sceneItemId", "sceneItemTransform"}
        keys = set(d["sceneItemTransform"])
        assert keys <= TRANSFORM_FIELDS and "cropToBounds" not in keys   # cropToBounds is 5.5+ only; never sent
        assert {"boundsType", "boundsWidth", "boundsHeight", "positionX", "positionY", "alignment"} <= keys


def test_configure_twice_stays_fitted_without_duplicate_items(obs):
    canvas(obs)
    ctx = served(obs)
    assert call(ctx, "onair_obs_setup", {"action": "configure"})["configured"] is True
    out = call(ctx, "onair_obs_setup", {"action": "configure"})
    assert out["configured"] is True and out["fit"]["fitted"] is True
    assert obs.items(C.presentation_scene) == [C.presentation_input, C.overlay_input]


def test_foreign_items_and_owned_sources_in_foreign_scenes_are_never_transformed(obs):
    canvas(obs)
    obs.add_input("Scene", "Somebody's page", "browser_source", {"url": "http://127.0.0.1:9/x", "width": 1920,
                                                                 "height": 1080})
    ctx = served(obs)
    assert call(ctx, "onair_obs_setup", {"action": "configure"})["configured"] is True
    # the owner adds a foreign item to an owned scene and an owned source to their own scene
    obs.add_input(C.presentation_scene, "Owner logo", "image_source", {"file": "logo.png"})
    obs._add_item("Scene", C.presentation_input)
    before = {k: dict(obs.item(s, k)["_transform"]) for s, k in (("Scene", "Somebody's page"),
                                                                    (C.presentation_scene, "Owner logo"),
                                                                    ("Scene", C.presentation_input))}
    n = len(obs.log)
    out = call(ctx, "onair_obs_setup", {"action": "configure"})
    assert out["configured"] is True
    touched = {(s, name) for s, name, _d in transforms_sent(obs, n)}
    assert touched == OWNED_ITEMS
    for s, k in (("Scene", "Somebody's page"), (C.presentation_scene, "Owner logo"), ("Scene", C.presentation_input)):
        assert obs.item(s, k)["_transform"] == before[k], (s, k)
    assert {(i["scene"], i["source"]) for i in out["fit"]["items"]} == OWNED_ITEMS


@pytest.mark.parametrize("video", [None, {}, {"baseWidth": None, "baseHeight": None},
                                   {"baseWidth": 0, "baseHeight": 720}, {"baseWidth": 1280, "baseHeight": -1},
                                   {"baseWidth": True, "baseHeight": 720}, {"baseWidth": "1280", "baseHeight": "720"},
                                   {"baseWidth": 1280.5, "baseHeight": 720}, {"baseWidth": 1280},
                                   {"outputWidth": 1280, "outputHeight": 720}])
def test_unknown_or_invalid_canvas_is_not_fitted_and_not_configured(obs, video):
    obs.video = video
    ctx = served(obs)
    out = call(ctx, "onair_obs_setup", {"action": "configure"})
    assert out["ok"] is False and out["configured"] is False, out
    assert out["sources_configured"] is True and out["fit"]["fitted"] is False and out["fit"]["canvas"] is None
    assert "canvas" in out["error"].lower() and "not" in out["error"].lower()
    assert not obs.requests("SetSceneItemTransform")


def test_refused_transform_is_not_fitted(obs):
    canvas(obs)
    obs.refuse["SetSceneItemTransform"] = 402
    ctx = served(obs)
    out = call(ctx, "onair_obs_setup", {"action": "configure"})
    assert out["ok"] is False and out["configured"] is False and out["fit"]["fitted"] is False
    assert all(i["fitted"] is False and "402" in i["error"] for i in out["fit"]["items"]), out["fit"]
    assert "server-side detail" not in json.dumps(out)


def test_refused_readback_is_not_fitted(obs):
    canvas(obs)
    obs.refuse["GetSceneItemTransform"] = 600
    ctx = served(obs)
    out = call(ctx, "onair_obs_setup", {"action": "configure"})
    assert out["configured"] is False and out["fit"]["fitted"] is False
    assert all(i["fitted"] is False for i in out["fit"]["items"])


def test_mismatched_readback_is_not_fitted(obs):
    canvas(obs)
    obs.transform_sticky = True          # OBS answers success but the item keeps its old transform
    ctx = served(obs)
    out = call(ctx, "onair_obs_setup", {"action": "configure"})
    assert out["ok"] is False and out["configured"] is False and out["fit"]["fitted"] is False
    for item in out["fit"]["items"]:
        assert item["fitted"] is False and "boundsType" in item["mismatch"], item


def test_refused_scene_item_is_an_honest_failure(obs):
    canvas(obs)
    obs.refuse["CreateSceneItem"] = 600  # the overlay cannot be added to the shared-window scene
    ctx = served(obs)
    out = call(ctx, "onair_obs_setup", {"action": "configure"})
    assert out["ok"] is False and out.get("configured") is not True
    assert (out.get("fit") or {}).get("fitted") is not True


def test_acknowledged_but_absent_owned_item_is_not_fitted(obs):
    canvas(obs)
    orig = obs._handle

    def handle(kind, d):   # OBS acknowledges CreateSceneItem but the item never appears in the window scene
        if kind == "CreateSceneItem" and d.get("sceneName") == C.window_scene:
            return 100, {"sceneItemId": 999}
        return orig(kind, d)
    obs._handle = handle
    ctx = served(obs)
    out = call(ctx, "onair_obs_setup", {"action": "configure"})
    assert out["ok"] is False and out["configured"] is False and out["fit"]["fitted"] is False
    assert [C.window_scene, C.overlay_input] in out["fit"]["missing"]


def test_blank_mac_reads_no_canvas_and_transforms_nothing(mac):
    canvas(mac)
    ctx = served(mac)
    out = call(ctx, "onair_obs_setup", {"action": "configure"})
    assert out["bootstrap_required"] is True and out["configured"] is False
    assert not mac.requests("GetVideoSettings") and not mac.requests("SetSceneItemTransform")


def test_primed_mac_fits_the_startup_item_it_reuses(mac, owner_home):
    canvas(mac)
    ctx = served(mac)
    call(ctx, "onair_obs_setup", {"action": "prepare_startup"})
    load_collection_into(mac, startup_dir(owner_home) / obs_bootstrap.COLLECTION_FILE)
    startup_item = mac.item(C.presentation_scene, C.presentation_input)
    assert startup_item["_transform"]["boundsType"] == "OBS_BOUNDS_NONE"     # unscaled until configure
    out = call(ctx, "onair_obs_setup", {"action": "configure"})
    assert out["ok"] is True and out["configured"] is True and out["fit"]["fitted"] is True, out
    assert mac.item(C.presentation_scene, C.presentation_input) is startup_item   # reused, not re-created
    for scene, name in OWNED_ITEMS:
        assert_fitted(mac, scene, name, 1280, 720)
