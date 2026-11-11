"""onair_layout through the REAL registered handler: presets, inspect and apply on the owned scenes.

Fixture authority, labelled: FakeCtx("cli") stands in for the owner session; FakeObs is the loopback OBS
WebSocket v5 double (request validation mirrors obs-websocket e28f8497). Window readiness and the Wayland owner
card are the existing window-share logic, driven through the real /onair-broadcast command with a modelled card
answer. Nothing here is a native OBS pixel, a real window selection or a genuine owner.
"""
import json
import sys

import pytest

from conftest import make
from fake_obs import FakeObs
from onair.presentation import agent, layout
from onair.presentation.obs_control import ObsConfig

C = ObsConfig()
MUTATING = {"SetSceneItemTransform", "SetSceneItemEnabled", "SetSceneItemIndex", "CreateSceneItem", "CreateInput",
            "CreateScene", "SetInputSettings", "SetCurrentProgramScene", "StartStream", "StopStream", "RemoveSceneItem",
            "SetVideoSettings", "SetInputMute", "SetInputVolume", "PressInputPropertiesButton"}


def call(ctx, tool, args=None):
    return json.loads(ctx.tools[tool]["handler"](args or {}))


def canvas(fake, w=1920, h=1080):
    fake.video = {"fpsNumerator": 30, "fpsDenominator": 1, "baseWidth": w, "baseHeight": h,
                  "outputWidth": w // 2, "outputHeight": h // 2}


def mutations(fake, start=0):
    return [(k, d) for k, d in fake.log[start:] if k in MUTATING]


def configured(fake, **cfg):
    ctx, _ = make(fake.url, **cfg)
    assert call(ctx, "onair_present_publish", {"sample": True})["ok"]
    assert call(ctx, "onair_obs_setup", {"action": "configure"})["configured"] is True
    return ctx


def ready_window(fake, ctx, frames=(2560, 1440)):
    """X11 OBS: window-only kind; the 'human' picks a window in the input's properties (modelled)."""
    assert call(ctx, "onair_window_share", {"action": "prepare"})["ok"]
    fake.inputs[C.window_input]["settings"]["capture_window"] = "4242\r\nDemo\r\ndemo"
    fake.frames[C.window_input] = frames
    assert call(ctx, "onair_window_share", {"action": "discover"})["window"]["ready"] is True


@pytest.fixture
def x11():
    fake = FakeObs(platform="linux", session="x11").start()
    canvas(fake)
    yield fake
    fake.stop()


def item_transform(fake, scene, name):
    return fake.item(scene, name)["_transform"]


def assert_box(fake, scene, name, box):
    t = item_transform(fake, scene, name)
    assert t["boundsType"] == "OBS_BOUNDS_SCALE_INNER" and t["alignment"] == 5 and t["boundsAlignment"] == 0, t
    assert (t["positionX"], t["positionY"], t["boundsWidth"], t["boundsHeight"]) == \
        (box["x"], box["y"], box["w"], box["h"]), (scene, name, t)


# ---- registration and inputs --------------------------------------------------------------------------------
def test_registered_schema_is_bounded_and_owner_only(obs):
    ctx, done = make(obs.url)
    assert "tool:onair_layout" in done
    tool = ctx.tools["onair_layout"]
    assert tool["toolset"] == "onair_present"
    params = tool["schema"]["parameters"]
    assert params["additionalProperties"] is False and params["required"] == ["action"]
    assert set(params["properties"]) == {"action", "preset", "corner", "inset_corner", "main", "sub1", "sub2", "slot", "source", "a", "b", "arrangement"}
    assert params["properties"]["action"]["enum"] == ["presets", "inspect", "apply", "boxes", "set", "put", "swap", "arrangement"]
    assert params["properties"]["source"]["enum"] == ["scene", "slides", "window", "empty"]
    assert params["properties"]["preset"]["enum"] == list(layout.PRESETS) == list(layout.PRESET_NAMES)
    assert params["properties"]["corner"]["enum"] == list(layout.CORNERS)
    n = obs.connections
    for extra in ({"source": "Somebody's camera"}, {"url": "http://127.0.0.1:1/x"}, {"css": "body{}"},
                  {"html": "<b>x</b>"}, {"scene": "Scene"}):
        out = call(ctx, "onair_layout", {"action": "apply", "preset": "presentation", **extra})
        assert out["ok"] is False and "Not accepted" in out["error"], extra
    for surface in ("discord", "on_air", "telegram", ""):
        ctx.surface = surface
        assert call(ctx, "onair_layout", {"action": "apply", "preset": "presentation"})["refused"] is True, surface
    assert obs.connections == n and not mutations(obs)       # nothing reached OBS


def test_stage_home_registers_no_layout_tool(tmp_path, monkeypatch, obs):
    home = tmp_path / "stage"
    home.mkdir()
    (home / ".onair-stage-home").write_text("stage\n")
    monkeypatch.setenv("HERMES_HOME", str(home))
    ctx, done = make(obs.url)
    assert done == [] and "onair_layout" not in ctx.tools


@pytest.mark.parametrize("args,why", [
    ({"action": "apply"}, "Give a preset"),
    ({"action": "apply", "preset": "side_by_side", "corner": "top_left"}, "only to the window_pip"),
    ({"action": "apply", "preset": "window_pip", "corner": "middle"}, "corner"),
    ({"action": "apply", "preset": "avatar"}, "Give a preset"),
    ({"action": "inspect", "preset": "window"}, "only with action 'apply'"),
    ({"action": "presets", "corner": "top_left"}, "only with action 'apply'"),
])
def test_bad_inputs_are_refused_before_obs(obs, args, why):
    ctx, _ = make(obs.url)
    n = obs.connections
    out = call(ctx, "onair_layout", args)
    assert out["ok"] is False and why in out["error"] and out.get("applied", False) is False, out
    assert obs.connections == n


def test_presets_are_static_and_name_the_real_sources(free_url):
    ctx, _ = make(free_url)                    # nothing listens: presets needs no OBS
    out = call(ctx, "onair_layout", {"action": "presets"})
    assert out["ok"] is True and set(out["presets"]) == set(layout.PRESET_NAMES)
    for name, p in out["presets"].items():
        assert set(p["requires"]) <= set(out["requirements"]) and "voice_overlay" in p["requires"], name
        assert p["scene_name"] in (C.presentation_scene, C.window_scene)
    assert "shared_window" not in out["presets"]["presentation"]["requires"]
    assert {"shared_window", "presentation_page"} <= set(out["presets"]["window_pip"]["requires"])
    assert "confirm-window" in out["requirements"]["shared_window"]
    assert "NOT an audio privacy cut" in out["recipes"]["standby"]["not"]
    assert "no built-in avatar" in out["recipes"]["conversation"]["not"]
    assert "no camera, guest or host video" in out["recipes"]["conversation"]["not"]
    assert "optional owner 3D scene" in out["recipes"]["conversation"]["not"]
    assert "docs/scene.md" in out["recipes"]["conversation"]["not"]
    assert "theme" in out["not_offered"] and "switching the program scene" in out["not_offered"]


# ---- pure geometry (the same functions apply and the static preview use) -------------------------------------
CANVASES = [(1920, 1080), (1280, 720), (2560, 1440), (3440, 1440), (1080, 1920), (640, 480), (853, 480)]


@pytest.mark.parametrize("w,h", CANVASES)
def test_every_box_stays_inside_the_canvas_and_keeps_aspect(w, h):
    for preset in layout.PRESET_NAMES:
        for corner in layout.CORNERS if preset == "window_pip" else (layout.DEFAULT_CORNER,):
            boxes = layout.boxes(preset, w, h, corner)
            assert boxes["overlay"] == {"x": 0, "y": 0, "w": w, "h": h}       # captions page: the whole canvas
            for role, b in boxes.items():
                assert all(isinstance(v, int) for v in b.values()), (preset, role, b)
                assert b["x"] >= 0 and b["y"] >= 0 and b["w"] >= 1 and b["h"] >= 1
                assert b["x"] + b["w"] <= w and b["y"] + b["h"] <= h, (preset, corner, role, b, w, h)
                for sw, sh in ((1920, 1080), (1280, 1024), (800, 1280)):
                    r = layout.fitted_rect(b, sw, sh)
                    assert abs(r["w"] / r["h"] - sw / sh) < 1e-9             # aspect ratio kept
                    assert r["x"] >= b["x"] - 1e-6 and r["y"] >= b["y"] - 1e-6
                    assert r["x"] + r["w"] <= b["x"] + b["w"] + 1e-6 and r["y"] + r["h"] <= b["y"] + b["h"] + 1e-6
            if preset == "side_by_side":
                a, c = boxes["window"], boxes["presentation"]
                assert a["x"] + a["w"] <= c["x"]                              # no overlap
            if preset == "window_pip":
                inset = boxes["presentation"]
                assert inset["w"] < w / 2 and inset["h"] < h / 2
                assert (inset["x"] < w / 2) == corner.endswith("left")
                assert (inset["y"] < h / 2) == corner.startswith("top")


def test_known_numbers_at_1080p():
    assert layout.boxes("window_pip", 1920, 1080) == {
        "window": {"x": 0, "y": 0, "w": 1920, "h": 1080}, "presentation": {"x": 1296, "y": 48, "w": 576, "h": 324},
        "overlay": {"x": 0, "y": 0, "w": 1920, "h": 1080}}
    assert layout.boxes("side_by_side", 1920, 1080)["presentation"] == {"x": 984, "y": 290, "w": 888, "h": 499}
    assert layout.fitted_rect({"x": 0, "y": 0, "w": 1920, "h": 1080}, 2560, 1440)["scale"] == 0.75
    assert layout.fitted_rect({"x": 0, "y": 0, "w": 1920, "h": 1080}, 0, 1440) is None


# ---- apply / inspect through the registered handler ----------------------------------------------------------
def test_presentation_preset_applies_reads_back_and_never_touches_program(obs):
    canvas(obs, 1280, 720)
    ctx = configured(obs)
    n = len(obs.log)
    out = call(ctx, "onair_layout", {"action": "apply", "preset": "presentation"})
    assert out["ok"] is True and out["applied"] is True and out["readback"]["matches"] is True, out
    assert out["canvas"] == {"base_width": 1280, "base_height": 720} and out["on_program"] is False
    assert "onair_obs_scene" in out["note"] and "not changed" in out["note"]
    for name in (C.presentation_input, C.overlay_input):
        assert_box(obs, C.presentation_scene, name, {"x": 0, "y": 0, "w": 1280, "h": 720})
    sent = {k for k, _ in mutations(obs, n)}
    assert sent <= {"SetSceneItemTransform", "SetSceneItemEnabled", "SetSceneItemIndex"}, sent
    assert obs.program == "Scene" and not obs.requests("SetCurrentProgramScene") and not obs.requests("StartStream")
    insp = call(ctx, "onair_layout", {"action": "inspect"})
    assert insp["ok"] and insp["current"]["presentation"] == {"preset": "presentation"}
    assert insp["last_applied"]["preset"] == "presentation" and insp["last_applied"]["still_matches"] is True
    assert insp["presets"]["presentation"]["ready"] is True
    assert insp["presets"]["window_pip"]["ready"] is False                   # no window yet: says so
    assert [m["need"] for m in insp["presets"]["window_pip"]["missing"]] == ["shared_window"]


def test_ready_selected_window_pip_then_window_then_side_by_side(x11):
    ctx = configured(x11)
    ready_window(x11, ctx)
    n = len(x11.log)
    out = call(ctx, "onair_layout", {"action": "apply", "preset": "window_pip"})
    assert out["applied"] is True and out["corner"] == "top_right" and out["scene"] == C.window_scene, out
    assert out["window"]["ready"] is True and out["window"]["capture_type_verified"] is True
    boxes = layout.boxes("window_pip", 1920, 1080, "top_right")
    for role, name in (("window", C.window_input), ("presentation", C.presentation_input), ("overlay", C.overlay_input)):
        assert_box(x11, C.window_scene, name, boxes[role])
    assert x11.items(C.window_scene) == [C.window_input, C.presentation_input, C.overlay_input]   # bottom -> top
    assert all(i["sceneItemEnabled"] for i in x11.scenes[C.window_scene])
    # the inset was created hidden and shown only after its bounds were set
    log = x11.log[n:]
    create = next(i for i, (k, d) in enumerate(log) if k == "CreateSceneItem")
    assert log[create][1] == {"sceneName": C.window_scene, "sourceName": C.presentation_input, "sceneItemEnabled": False}
    pid = x11.item(C.window_scene, C.presentation_input)["sceneItemId"]
    set_t = next(i for i, (k, d) in enumerate(log) if k == "SetSceneItemTransform" and d["sceneItemId"] == pid)
    shown = next(i for i, (k, d) in enumerate(log) if k == "SetSceneItemEnabled" and d["sceneItemId"] == pid)
    assert create < set_t < shown and log[shown][1]["sceneItemEnabled"] is True
    assert x11.program == "Scene" and not x11.requests("SetCurrentProgramScene")
    insp = call(ctx, "onair_layout", {"action": "inspect"})
    assert insp["current"]["window"] == {"preset": "window_pip", "corner": "top_right"}
    assert insp["last_applied"]["still_matches"] is True

    out = call(ctx, "onair_layout", {"action": "apply", "preset": "window_pip", "corner": "bottom_left"})
    assert out["applied"] is True
    assert_box(x11, C.window_scene, C.presentation_input, layout.boxes("window_pip", 1920, 1080, "bottom_left")["presentation"])
    assert x11.items(C.window_scene).count(C.presentation_input) == 1                      # never duplicated

    out = call(ctx, "onair_layout", {"action": "apply", "preset": "window"})
    assert out["applied"] is True
    assert x11.item(C.window_scene, C.presentation_input)["sceneItemEnabled"] is False     # slides hidden, kept
    assert_box(x11, C.window_scene, C.window_input, {"x": 0, "y": 0, "w": 1920, "h": 1080})
    assert call(ctx, "onair_layout", {"action": "inspect"})["current"]["window"] == {"preset": "window"}

    out = call(ctx, "onair_layout", {"action": "apply", "preset": "side_by_side"})
    assert out["applied"] is True
    b = layout.boxes("side_by_side", 1920, 1080)
    assert_box(x11, C.window_scene, C.window_input, b["window"])
    assert_box(x11, C.window_scene, C.presentation_input, b["presentation"])
    assert x11.item(C.window_scene, C.presentation_input)["sceneItemEnabled"] is True
    # showing it is still the window-share step; the layout survives it
    assert call(ctx, "onair_window_share", {"action": "show"})["ok"] and x11.program == C.window_scene
    assert call(ctx, "onair_layout", {"action": "inspect"})["current"]["window"] == {"preset": "side_by_side"}


def test_on_program_scene_change_is_reported_as_visible_now(x11):
    ctx = configured(x11)
    ready_window(x11, ctx)
    assert call(ctx, "onair_window_share", {"action": "show"})["ok"]
    out = call(ctx, "onair_layout", {"action": "apply", "preset": "window_pip"})
    assert out["applied"] is True and out["on_program"] is True and "viewers see" in out["note"]
    assert x11.program == C.window_scene


def test_approved_scene_switch_cannot_show_an_unready_window(obs):
    """Window layouts live in the shared-window scene; onair_obs_scene uses the same readiness as show."""
    canvas(obs)
    ctx = configured(obs)
    assert call(ctx, "onair_window_share", {"action": "prepare"})["ok"]
    obs.frames[C.window_input] = (1280, 720)
    obs.inputs[C.window_input]["settings"]["RestoreToken"] = "tok-1"      # picked, not yet confirmed
    out = call(ctx, "onair_obs_scene", {"scene": C.window_scene})
    assert out["ok"] is False and "confirm-window" in out["next_human_action"] and obs.program == "Scene"
    assert not obs.requests("SetCurrentProgramScene") and "_fingerprint" not in out["window"]
    assert call(ctx, "onair_obs_scene", {"scene": C.presentation_scene})["ok"] is True   # slides: unaffected
    ctx.choice = "confirm"
    assert ctx.commands["onair-broadcast"]("confirm-window").startswith("Confirmed")
    out = call(ctx, "onair_obs_scene", {"scene": C.window_scene})
    assert out["ok"] is True and obs.program == C.window_scene


def test_missing_window_input_is_an_actionable_refusal(x11):
    ctx = configured(x11)
    n = len(x11.log)
    out = call(ctx, "onair_layout", {"action": "apply", "preset": "window_pip"})
    assert out["ok"] is False and out["applied"] is False and out["changed"] is False
    miss = {m["need"]: m for m in out["missing"]}
    assert set(miss) == {"shared_window"} and "'prepare'" in miss["shared_window"]["next_action"]
    assert miss["shared_window"]["human_only"] is False                       # the agent can prepare it
    assert not mutations(x11, n)


def test_wayland_unconfirmed_window_is_refused_until_the_owner_card(obs):
    canvas(obs)
    ctx = configured(obs)
    assert call(ctx, "onair_window_share", {"action": "prepare"})["ok"]
    obs.frames[C.window_input] = (1280, 720)                                   # the owner picked something
    obs.inputs[C.window_input]["settings"]["RestoreToken"] = "tok-1"
    before = {i["sourceName"]: dict(i["_transform"]) for i in obs.scenes[C.window_scene]}
    n = len(obs.log)
    for preset in ("window", "window_pip", "side_by_side"):
        out = call(ctx, "onair_layout", {"action": "apply", "preset": preset})
        assert out["applied"] is False and out["changed"] is False, out
        w = {m["need"]: m for m in out["missing"]}["shared_window"]
        assert "/onair-broadcast confirm-window" in w["next_action"] and w["human_only"] is True
        assert w["window"]["operator_confirmed"] is False and w["window"]["capture_type_verified"] is False
    assert not mutations(obs, n)
    assert {i["sourceName"]: i["_transform"] for i in obs.scenes[C.window_scene]} == before
    assert C.presentation_input not in obs.items(C.window_scene)
    ctx.choice = "confirm"                                                     # modelled host owner card
    assert ctx.commands["onair-broadcast"]("confirm-window").startswith("Confirmed")
    out = call(ctx, "onair_layout", {"action": "apply", "preset": "window_pip"})
    assert out["applied"] is True, out
    assert out["window"]["selection"] == "operator_confirmed" and out["window"]["capture_type_verified"] is False
    # re-preparing revokes the confirmation: the layout tool honours it at once
    call(ctx, "onair_window_share", {"action": "prepare"})
    out = call(ctx, "onair_layout", {"action": "apply", "preset": "window"})
    assert out["applied"] is False and "confirm-window" in json.dumps(out["missing"])


def test_unknown_canvas_changes_nothing(obs):
    ctx = configured(obs)
    obs.video = None
    n = len(obs.log)
    out = call(ctx, "onair_layout", {"action": "apply", "preset": "presentation"})
    assert out["applied"] is False and out["canvas"] is None
    assert [m["need"] for m in out["missing"]] == ["canvas"] and not mutations(obs, n)
    assert call(ctx, "onair_layout", {"action": "inspect"})["current"] == {"presentation": None, "window": None}


def test_unserved_page_and_unconfigured_obs_are_missing_prerequisites(obs):
    ctx, _ = make(obs.url)
    out = call(ctx, "onair_layout", {"action": "apply", "preset": "presentation"})
    needs = {m["need"] for m in out["missing"]}
    assert out["applied"] is False and {"scene", "presentation_page", "voice_overlay"} <= needs, out
    assert any("onair_present_publish" in m["next_action"] for m in out["missing"])
    assert not mutations(obs)


def test_unreachable_obs_is_never_applied(free_url):
    ctx, _ = make(free_url)
    out = call(ctx, "onair_layout", {"action": "apply", "preset": "presentation"})
    assert out["ok"] is False and out["applied"] is False and out["connected"] is False
    insp = call(ctx, "onair_layout", {"action": "inspect"})
    assert insp["ok"] is False and insp["connected"] is False


def test_refused_request_is_not_applied_and_not_remembered(x11):
    ctx = configured(x11)
    ready_window(x11, ctx)
    x11.refuse["SetSceneItemTransform"] = 402
    out = call(ctx, "onair_layout", {"action": "apply", "preset": "window_pip"})
    assert out["ok"] is False and out["applied"] is False and "402" in out["error"], out
    assert out["changed"] is True and out["requests_accepted"] and "inspect" in out["note"]
    assert "server-side detail" not in json.dumps(out)
    assert agent.runtime().obs.layout_last is None
    assert call(ctx, "onair_layout", {"action": "inspect"})["last_applied"] is None


def test_readback_mismatch_is_not_applied(x11):
    ctx = configured(x11)
    ready_window(x11, ctx)
    x11.transform_sticky = True                       # OBS answers success but keeps the old transforms
    out = call(ctx, "onair_layout", {"action": "apply", "preset": "window_pip"})
    assert out["ok"] is False and out["applied"] is False and out["readback"]["matches"] is False
    assert any("transform differs" in p for p in out["readback"]["problems"]), out["readback"]
    assert "NOT applied" in out["error"] and agent.runtime().obs.layout_last is None


def test_foreign_scenes_and_items_are_never_touched(x11):
    x11.add_input("Scene", "Somebody's page", "browser_source", {"url": "http://127.0.0.1:9/x", "width": 1920,
                                                                 "height": 1080})
    ctx = configured(x11)
    ready_window(x11, ctx)
    x11.add_input(C.window_scene, "Owner logo", "image_source", {"file": "logo.png"})
    x11._add_item("Scene", C.presentation_input)                           # an owned source in a foreign scene
    foreign = [("Scene", "Somebody's page"), (C.window_scene, "Owner logo"), ("Scene", C.presentation_input)]
    before = {k: (dict(x11.item(*k)["_transform"]), x11.item(*k)["sceneItemEnabled"]) for k in foreign}
    ids = {x11.item(*k)["sceneItemId"] for k in foreign}
    n = len(x11.log)
    for preset, extra in (("window_pip", {}), ("side_by_side", {}), ("window", {}), ("presentation", {})):
        assert call(ctx, "onair_layout", {"action": "apply", "preset": preset, **extra})["applied"] is True, preset
    for k, d in mutations(x11, n):
        assert d.get("sceneName") in (C.presentation_scene, C.window_scene), (k, d)
        assert d.get("sceneItemId") not in ids, (k, d)
    assert {k: (x11.item(*k)["_transform"], x11.item(*k)["sceneItemEnabled"]) for k in foreign} == before
    assert x11.items("Scene") == ["Somebody's page", C.presentation_input]


def test_exactly_one_speaking_overlay_and_presentation_audio_off(x11):
    ctx = configured(x11)
    ready_window(x11, ctx)
    out = call(ctx, "onair_layout", {"action": "apply", "preset": "window_pip"})
    assert out["applied"] is True
    voice = out["speech_audio"]
    assert voice["ok"] is True and voice["overlay_clients"] == 1 and voice["presentation_audio_off"] is True
    for scene in (C.presentation_scene, C.window_scene):
        assert x11.items(scene).count(C.overlay_input) == 1
    assert x11.inputs[C.presentation_input]["settings"]["reroute_audio"] is False
    # a second page pointing at the overlay would double the voice: refused, nothing changed
    x11.add_input("Scene", "Second overlay", "browser_source", {"url": "http://localhost:18790/overlay",
                                                                "reroute_audio": True})
    n = len(x11.log)
    out = call(ctx, "onair_layout", {"action": "apply", "preset": "window"})
    assert out["applied"] is False and not mutations(x11, n)
    m = {m["need"]: m for m in out["missing"]}["voice_overlay"]
    assert "Second overlay" in m["next_action"]


def test_window_preset_refuses_an_existing_presentation_page_with_obs_audio_on(x11):
    """Page audio off is shared by every preset, including the window-only one that does not show the slides.
    Fixture authority: the 'owner' turning on Control audio via OBS is a FakeObs settings edit, not native sound."""
    ctx = configured(x11)
    ready_window(x11, ctx)
    x11.inputs[C.presentation_input]["settings"]["reroute_audio"] = True
    n = len(x11.log)
    out = call(ctx, "onair_layout", {"action": "apply", "preset": "window"})
    sent = mutations(x11, n)
    assert out["ok"] is False and out["applied"] is False and out["changed"] is False, (len(sent), out)
    assert not sent
    insp = call(ctx, "onair_layout", {"action": "inspect"})
    for preset in ("window", "window_pip", "side_by_side", "presentation"):
        assert insp["presets"][preset]["ready"] is False, (preset, insp["presets"][preset])
        assert any("audio is on" in m["why"] for m in insp["presets"][preset]["missing"]), preset
    m = next(m for m in out["missing"] if "audio is on" in m["why"])
    assert m["need"] == "voice_overlay" and "onair_obs_setup configure" in m["next_action"]
    # an unserved page cannot be reconfigured until it is served again: the next step says so
    agent.runtime().stop_serving()
    n = len(x11.log)
    out = call(ctx, "onair_layout", {"action": "apply", "preset": "window"})
    assert out["applied"] is False and out["changed"] is False and not mutations(x11, n)
    m = next(m for m in out["missing"] if "audio is on" in m["why"])
    assert m["next_action"].startswith("onair_present_publish")
    # the ordinary remedy: configure turns the page audio off, then the window preset applies
    assert call(ctx, "onair_present_publish", {"sample": True})["ok"]
    assert call(ctx, "onair_obs_setup", {"action": "configure"})["configured"] is True
    assert x11.inputs[C.presentation_input]["settings"]["reroute_audio"] is False
    out = call(ctx, "onair_layout", {"action": "apply", "preset": "window"})
    assert out["applied"] is True and out["speech_audio"]["presentation_audio_off"] is True, out


def test_window_only_needs_no_presentation_page(x11):
    """Explicit window-only operation: an unserved or absent presentation page is not a window prerequisite."""
    ctx = configured(x11)
    ready_window(x11, ctx)
    agent.runtime().stop_serving()                                            # page no longer served
    out = call(ctx, "onair_layout", {"action": "apply", "preset": "window"})
    assert out["applied"] is True, out
    pip = call(ctx, "onair_layout", {"action": "apply", "preset": "window_pip"})
    assert pip["applied"] is False and "presentation_page" in {m["need"] for m in pip["missing"]}
    del x11.inputs[C.presentation_input]                                      # page source removed in OBS
    for scene in x11.scenes:
        x11.scenes[scene] = [i for i in x11.scenes[scene] if i["sourceName"] != C.presentation_input]
    insp = call(ctx, "onair_layout", {"action": "inspect"})
    assert insp["presets"]["window"]["ready"] is True, insp["presets"]["window"]
    out = call(ctx, "onair_layout", {"action": "apply", "preset": "window"})
    assert out["applied"] is True, out


def test_reset_elsewhere_is_never_reported_as_still_applied(x11):
    ctx = configured(x11)
    ready_window(x11, ctx)
    assert call(ctx, "onair_layout", {"action": "apply", "preset": "window_pip"})["applied"] is True
    # configure (canvas fit) arranges its own items but leaves the inset alone
    fit = call(ctx, "onair_obs_setup", {"action": "configure"})
    assert fit["configured"] is True
    assert (C.window_scene, C.presentation_input) not in {(i["scene"], i["source"]) for i in fit["fit"]["items"]}
    assert call(ctx, "onair_layout", {"action": "inspect"})["last_applied"]["still_matches"] is True
    # the owner drags the inset in OBS: readback says so
    x11.item(C.window_scene, C.presentation_input)["_transform"]["positionX"] = 10.0
    insp = call(ctx, "onair_layout", {"action": "inspect"})
    assert insp["last_applied"]["still_matches"] is False and insp["current"]["window"] is None
    assert any("presentation" in p for p in insp["last_applied"]["problems"])
    # a canvas change also invalidates it
    assert call(ctx, "onair_layout", {"action": "apply", "preset": "window_pip"})["applied"] is True
    canvas(x11, 1280, 720)
    insp = call(ctx, "onair_layout", {"action": "inspect"})
    assert insp["last_applied"]["still_matches"] is False and insp["canvas"]["base_width"] == 1280
    # the window input re-created by the owner (new item, default transform)
    canvas(x11)
    assert call(ctx, "onair_layout", {"action": "apply", "preset": "window"})["applied"] is True
    del x11.inputs[C.window_input]
    x11.scenes[C.window_scene] = [i for i in x11.scenes[C.window_scene] if i["sourceName"] != C.window_input]
    insp = call(ctx, "onair_layout", {"action": "inspect"})
    assert insp["last_applied"]["still_matches"] is False and insp["presets"]["window"]["ready"] is False
    st = call(ctx, "onair_present_status")                                    # status claims no layout label
    assert not [k for k in (*st, *(st.get("obs") or {})) if "layout" in k.lower()], sorted(st)


def test_optimized_mode_still_executes_test_assertions():
    """Under python -O the production code has no asserts to strip; pytest rewrites the asserts in this module
    into explicit checks, so they still run. Recorded so a -O run is meaningful, not vacuous."""
    with pytest.raises(AssertionError):
        assert sys.flags.optimize < 0
    print("optimize_flag=" + str(sys.flags.optimize))
