"""OBS control against the LABELLED loopback v5 double: configure/readback, speech audio, scenes, window share,
broadcast observation and the human go-live release. No real OBS, capture, output or destination."""
import json

import pytest

from conftest import make
from fake_obs import FakeObs
from onair.presentation import agent
from onair.presentation.obs_control import ObsConfig

C = ObsConfig()


def call(ctx, name, args=None):
    return json.loads(ctx.tools[name]["handler"](args or {}))


def served(obs, **cfg):
    ctx, _ = make(obs.url, **cfg)
    assert call(ctx, "onair_present_publish", {"sample": True})["ok"]
    return ctx


def test_disconnected_is_not_success(free_url):
    ctx, _ = make(free_url)
    out = call(ctx, "onair_present_status")
    assert out["obs"]["ok"] is False and out["obs"]["connected"] is False and out["window"] is None
    for name, args in (("onair_obs_scene", {"scene": C.presentation_scene}), ("onair_obs_setup", {"action": "inspect"}),
                       ("onair_broadcast", {"action": "status"}), ("onair_window_share", {"action": "show"})):
        res = call(ctx, name, args)
        assert res["ok"] is False and res["connected"] is False, name
    assert "unknown" in ctx.commands["onair-broadcast"]("status")


def test_wrong_password_reported_without_secret(monkeypatch):
    fake = FakeObs(password="right-horse").start()
    try:
        monkeypatch.setenv("ONAIR_OBS_PASSWORD", "wrong-canary-6812")
        ctx, _ = make(fake.url)
        out = call(ctx, "onair_obs_setup", {"action": "inspect"})
        assert out["ok"] is False and "password" in out["error"] and "wrong-canary-6812" not in json.dumps(out)
        monkeypatch.setenv("ONAIR_OBS_PASSWORD", "right-horse")
        assert call(ctx, "onair_obs_setup", {"action": "inspect"})["ok"] is True
    finally:
        fake.stop()


def test_configure_owned_sources_readback_without_program_or_output(obs):
    ctx = served(obs)
    url = agent.runtime().surface.url
    out = call(ctx, "onair_obs_setup", {"action": "configure"})
    assert out["ok"] is True, out
    pres, ov = obs.inputs[C.presentation_input], obs.inputs[C.overlay_input]
    assert pres["kind"] == ov["kind"] == "browser_source"
    assert pres["settings"] == {"width": 1920, "height": 1080, "url": url, "reroute_audio": False}
    assert ov["settings"]["url"] == "http://127.0.0.1:18790/overlay" and ov["settings"]["reroute_audio"] is True
    assert obs.items(C.presentation_scene) == [C.presentation_input, C.overlay_input]  # overlay on top
    assert C.overlay_input in obs.items(C.window_scene)
    assert obs.program == "Scene" and not obs.requests("SetCurrentProgramScene") and not obs.requests("StartStream")
    assert out["sources"]["speech_audio"]["ok"] is True and out["sources"]["presentation"]["label"].startswith("Browser Source")
    st = call(ctx, "onair_obs_setup", {"action": "inspect"})
    assert st["presentation_ready"] is True and st["stream"]["state"] == "inactive" and st["program_scene"] == "Scene"
    # idempotent: configuring twice keeps one item each
    assert call(ctx, "onair_obs_setup", {"action": "configure"})["ok"]
    assert obs.items(C.presentation_scene).count(C.overlay_input) == 1


def test_second_speaking_overlay_flags_doubling(obs):
    ctx = served(obs)
    assert call(ctx, "onair_obs_setup", {"action": "configure"})["ok"]
    obs.add_input("Scene", "Old captions", "browser_source", {"url": "http://127.0.0.1:18790/overlay?layout=captions",
                                                              "reroute_audio": True})
    out = call(ctx, "onair_obs_setup", {"action": "configure"})
    assert not out['ok'] and 'Old captions' in out['next_human_action']
    sa = out["sources"]["speech_audio"]
    assert sa["ok"] is False and sa["extra_overlay_clients"] == ["Old captions"] and sa["overlay_clients"] == 2
    assert call(ctx, "onair_obs_setup", {"action": "inspect"})["presentation_ready"] is False


def test_localhost_alias_overlay_still_counts_as_second_speaker(obs):
    ctx = served(obs)
    assert call(ctx, "onair_obs_setup", {"action": "configure"})["ok"]
    obs.add_input("Scene", "Alias captions", "browser_source", {"url": "http://localhost:18790/overlay/",
                                                                "reroute_audio": True})
    out = call(ctx, "onair_obs_setup", {"action": "configure"})
    assert not out['ok'] and 'Alias captions' in out['next_human_action']
    sa = out["sources"]["speech_audio"]
    assert sa["ok"] is False and sa["extra_overlay_clients"] == ["Alias captions"]


def test_deadlines_bounded_below_call_timeouts():
    cfg = ObsConfig(start_deadline_s=600, stop_deadline_s=-5, poll_s=0)
    assert cfg.start_deadline_s <= 25 and cfg.stop_deadline_s > 0 and cfg.poll_s > 0


def test_name_collision_refused_nothing_created(obs):
    obs.add_input("Scene", C.presentation_input, "image_source", {"file": "x.png"})
    ctx = served(obs)
    out = call(ctx, "onair_obs_setup", {"action": "configure"})
    assert out["ok"] is False and "not a Browser Source" in out["error"]
    assert not obs.requests("CreateInput") and not obs.requests("SetInputSettings") and not obs.requests("CreateScene")


def test_configure_requires_serving_surface(obs):
    ctx, _ = make(obs.url)
    out = call(ctx, "onair_obs_setup", {"action": "configure"})
    assert out["ok"] is False and obs.connections == 0


def test_scene_switch_approved_only_and_read_back(obs):
    ctx = served(obs, approved_scenes=["Starting Soon"])
    obs.scenes["Starting Soon"] = []
    call(ctx, "onair_obs_setup", {"action": "configure"})
    assert call(ctx, "onair_obs_scene", {"scene": "Private desk"})["ok"] is False
    assert not obs.requests("SetCurrentProgramScene")
    out = call(ctx, "onair_obs_scene", {"scene": "Starting Soon"})
    assert out["ok"] and out["program_scene"] == "Starting Soon" == obs.program
    obs.ignore_scene_switch = True
    out = call(ctx, "onair_obs_scene", {"scene": C.presentation_scene})
    assert out["ok"] is False and out["program_scene"] == "Starting Soon" and "did not report" in out["error"]


@pytest.mark.parametrize('target', ['presentation', 'extra'])
def test_scene_switch_refuses_foreign_capture_even_under_approved_name(obs, target):
    ctx = served(obs, approved_scenes=['Approved intro'])
    assert call(ctx, 'onair_obs_setup', {'action': 'configure'})['ok']
    scene = C.presentation_scene if target == 'presentation' else 'Approved intro'
    obs.add_input(scene, 'Unconfirmed capture', 'pipewire-screen-capture-source', {})
    result = call(ctx, 'onair_obs_scene', {'scene': scene})
    assert not result['ok'] and obs.program == 'Scene'


@pytest.mark.parametrize('target', ['presentation', 'window'])
def test_builtin_scene_rejects_foreign_browser_source(obs, target):
    ctx = served(obs)
    assert call(ctx, 'onair_obs_setup', {'action': 'configure'})['ok']
    cfg = agent.runtime().obs.cfg
    scene = cfg.presentation_scene if target == 'presentation' else cfg.window_scene
    if target == 'window':
        assert call(ctx, 'onair_window_share', {'action': 'prepare'})['ok']
        obs.frames[cfg.window_input] = (1280, 720)
        obs.inputs[cfg.window_input]['settings']['RestoreToken'] = 'chosen'
        ctx.choice = 'confirm'
        assert ctx.commands['onair-broadcast']('confirm-window').startswith('Confirmed')
    obs.add_input(scene, 'Foreign browser', 'browser_source', {'url': 'http://example.invalid/'})
    result = call(ctx, 'onair_obs_scene', {'scene': scene})
    assert not result['ok'] and obs.program == 'Scene'


def test_scene_switch_fails_closed_on_malformed_item_readback(obs):
    ctx = served(obs, approved_scenes=['Approved intro'])
    obs.scenes['Approved intro'] = []
    original = obs._handle
    def malformed(kind, data):
        if kind == 'GetSceneItemList' and data.get('sceneName') == 'Approved intro':
            return 100, {}
        return original(kind, data)
    obs._handle = malformed
    result = call(ctx, 'onair_obs_scene', {'scene': 'Approved intro'})
    assert not result['ok'] and obs.program == 'Scene'


def test_broadcast_requires_human_release_and_observed_active(obs):
    ctx = served(obs)
    out = call(ctx, "onair_broadcast", {"action": "request_start"})
    assert out["ok"] and out["started"] is False and not obs.requests("StartStream")
    code = out["approve_with"].split()[-1]
    cmd = ctx.commands["onair-broadcast"]
    assert "Nothing was started" in cmd("start 000000") and not obs.requests("StartStream")
    assert "Nothing was started" in cmd("start " + code)   # the code was consumed by the failed attempt above
    code = call(ctx, "onair_broadcast", {"action": "request_start"})["approve_with"].split()[-1]
    ctx.choice = "cancel"
    assert "Cancelled" in cmd("start " + code) and not obs.requests("StartStream")
    code = call(ctx, "onair_broadcast", {"action": "request_start"})["approve_with"].split()[-1]
    ctx.choice = None
    assert "nothing was started" in cmd("start " + code) and not obs.requests("StartStream")
    code = call(ctx, "onair_broadcast", {"action": "request_start"})["approve_with"].split()[-1]
    ctx.choice = "go"
    msg = cmd("start " + code)
    assert msg.startswith("Live:") and obs.stream_active and len(obs.requests("StartStream")) == 1
    assert "Go live" in json.dumps(ctx.questions[-1])
    assert call(ctx, "onair_broadcast", {"action": "status"})["stream"]["state"] == "active"
    out = call(ctx, "onair_broadcast", {"action": "stop"})
    assert out["ok"] and out["stream"]["active"] is False and not obs.stream_active
    assert call(ctx, "onair_broadcast", {"action": "stop"})["ok"] is True  # 501 not running -> confirmed by readback


def test_admitted_start_that_never_goes_active_is_not_live(obs):
    obs.activate_after = None
    ctx = served(obs)
    code = call(ctx, "onair_broadcast", {"action": "request_start"})["approve_with"].split()[-1]
    msg = ctx.commands["onair-broadcast"]("start " + code)
    assert msg.startswith("NOT live") and "not observed active" in msg and len(obs.requests("StartStream")) == 1


# ---- selected OS window --------------------------------------------------------------------------------------
def test_linux_wayland_obsolete_window_kind_not_advertised_or_creatable(obs):
    """Default double = upstream-shaped Wayland OBS: the obsolete window-only kind is neither listed nor creatable;
    only the combined screen/window kind is, and discover says exactly what that means."""
    assert "pipewire-window-capture-source" not in obs.kinds and "xcomposite_input" not in obs.kinds
    assert obs._handle("CreateInput", {"sceneName": "Scene", "inputName": "probe",          # upstream: 605
                                       "inputKind": "pipewire-window-capture-source"})[0] == 605
    ctx = served(obs)
    call(ctx, "onair_obs_setup", {"action": "configure"})
    d = call(ctx, "onair_window_share", {"action": "discover"})["window"]
    assert d["share"] == "os_window" and d["exists"] is False and d["ready"] is False
    assert d["window_kinds_available"] == [] and d["can_create_input"] is True
    assert d["creatable_kind"] == "pipewire-screen-capture-source"
    text = " ".join(d["prerequisites"])
    assert "obsolete" in text and "whole monitor OR one window" in text and "/onair-broadcast confirm-window" in text
    assert "onair_present_publish" in text and "xcomposite_input" in text


@pytest.mark.parametrize("platform", ["org.freedesktop.platform", "ubuntu"])
def test_distro_wayland_discover_prepare_owner_confirmation(platform):
    fake = FakeObs(platform=platform, kinds=[
        "image_source", "color_source", "slideshow", "jack_output_capture", "pipewire-camera-source",
        "pipewire-screen-capture-source", "pulse_input_capture", "pulse_output_capture", "v4l2_input",
        "browser_source", "ffmpeg_source", "text_ft2_source",
    ]).start()
    try:
        ctx = served(fake)
        before = len(fake.requests("CreateInput"))
        result = call(ctx, "onair_window_share", {"action": "discover"})
        print("DISCOVER_JSON=" + json.dumps(result, sort_keys=True))
        w = result["window"]
        assert w["platform"] == "linux" and w["platform_reported"] == platform
        assert w["creatable_kind"] == "pipewire-screen-capture-source" and w["can_create_input"]
        assert w["next_human_action"] and len(fake.requests("CreateInput")) == before
        assert call(ctx, "onair_window_share", {"action": "prepare"})["ok"]
        fake.frames[C.window_input] = (1280, 720)
        fake.inputs[C.window_input]["settings"]["RestoreToken"] = "chosen"
        w = call(ctx, "onair_window_share", {"action": "discover"})["window"]
        assert not w["ready"] and not w["capture_type_verified"]
        assert not call(ctx, "onair_window_share", {"action": "show"})["ok"]
        ctx.choice = "confirm"
        assert ctx.commands["onair-broadcast"]("confirm-window").startswith("Confirmed")
        w = call(ctx, "onair_window_share", {"action": "discover"})["window"]
        assert w["ready"] and w["operator_confirmed"] and not w["capture_type_verified"]
        assert call(ctx, "onair_window_share", {"action": "show"})["ok"]
    finally:
        fake.stop()


@pytest.mark.parametrize("platform", ["other", "", None])
def test_unknown_platform_without_linux_kinds_is_unsupported(platform):
    fake = FakeObs(platform=platform, kinds=["browser_source", "image_source"]).start()
    try:
        ctx = served(fake)
        w = call(ctx, "onair_window_share", {"action": "discover"})["window"]
        assert w["platform"] == "unknown" and w["platform_reported"] == platform
        assert not w["can_create_input"] and w["creatable_kind"] is None
        assert "No capture kind is supported" in w["prerequisites"][0]
    finally:
        fake.stop()


@pytest.mark.parametrize("platform", ["osx", "darwin"])
def test_macos_platform_aliases(platform):
    fake = FakeObs(platform=platform).start()
    try:
        ctx = served(fake)
        w = call(ctx, "onair_window_share", {"action": "discover"})["window"]
        assert w["platform"] == "macos" and w["platform_reported"] == platform
        assert w["creatable_kind"] == "screen_capture"
        inspect = call(ctx, "onair_obs_setup", {"action": "inspect"})
        assert inspect["obs"]["platform"] == "macos" and inspect["obs"]["platform_reported"] == platform
        assert inspect["startup_bootstrap_required"]
        setup = call(ctx, "onair_obs_setup", {"action": "configure"})
        assert setup["bootstrap_required"] and setup["platform"] == "macos"
        assert setup["platform_reported"] == platform and not fake.requests("CreateInput")
    finally:
        fake.stop()


@pytest.mark.parametrize("kind", ["pipewire-screen-capture-source", "pipewire-window-capture-source",
                                  "pipewire-desktop-capture-source", "xcomposite_input", "xshm_input",
                                  "xshm_input_v2", "v4l2_input", "pulse_input_capture"])
def test_normalise_platform_linux_evidence(kind):
    from onair.presentation.obs_control import _normalise_platform
    assert _normalise_platform("distribution", {kind}) == "linux"
    assert _normalise_platform("", {kind}) == "unknown"
    assert _normalise_platform(None, {kind}) == "unknown"
    assert _normalise_platform("windows", {kind}) == "windows"
    assert _normalise_platform("darwin", {kind}) == "macos"


def test_linux_without_any_capture_kind_never_suggests_prepare():
    fake = FakeObs(kinds=["browser_source", "image_source"]).start()
    try:
        ctx = served(fake)
        call(ctx, "onair_obs_setup", {"action": "configure"})
        d = call(ctx, "onair_window_share", {"action": "discover"})["window"]
        assert d["can_create_input"] is False and d["creatable_kind"] is None
        assert not any("'prepare'" in p for p in d["prerequisites"])      # never point at an action that cannot work
        before = len(fake.requests("CreateInput"))
        out = call(ctx, "onair_window_share", {"action": "prepare"})
        assert out["ok"] is False and "nothing was created" in out["error"]
        assert len(fake.requests("CreateInput")) == before and C.window_input not in fake.inputs
        assert call(ctx, "onair_window_share", {"action": "show"})["ok"] is False
        assert not fake.requests("SetCurrentProgramScene")
    finally:
        fake.stop()


def test_wayland_combined_source_shown_only_after_owner_card_bound_to_input(obs):
    """prepare -> not showable -> refused confirmations -> host card accepts -> show/readback; changes revoke."""
    ctx = served(obs)
    call(ctx, "onair_obs_setup", {"action": "configure"})
    cmd = ctx.commands["onair-broadcast"]
    before = len(obs.requests("CreateInput"))
    out = call(ctx, "onair_window_share", {"action": "prepare"})
    assert out["ok"] and obs.inputs[C.window_input]["kind"] == "pipewire-screen-capture-source"
    assert obs.inputs[C.window_input]["settings"] == {} and len(obs.requests("CreateInput")) == before + 1
    w = out["window"]
    assert w["selection"] == "operator_selected_unconfirmed" and w["capture_type_verified"] is False
    assert w["operator_confirmed"] is False and w["ready"] is False and "_fingerprint" not in w
    assert "monitor or a window" in out["note"] and "Operator-selected" in w["label"]
    assert "Pick ONE dedicated window" in w["next_human_action"]
    assert cmd("confirm-window").startswith("Nothing confirmed") and not ctx.questions   # nothing chosen yet
    obs.frames[C.window_input] = (1280, 720)                       # the human picked something in the dialog
    obs.inputs[C.window_input]["settings"]["RestoreToken"] = "tok-1"
    w = call(ctx, "onair_window_share", {"action": "discover"})["window"]
    assert w["producing_frames"] and w["ready"] is False and "/onair-broadcast confirm-window" in w["next_human_action"]

    def not_shown():
        shown = call(ctx, "onair_window_share", {"action": "show"})
        return shown["ok"] is False and obs.program != C.window_scene
    assert not_shown() and "confirm-window" in call(ctx, "onair_window_share", {"action": "show"})["next_human_action"]
    # the agent cannot self-approve: no confirm action, extra keys refused, non-owner command refused
    assert call(ctx, "onair_window_share", {"action": "show", "confirmed": True})["ok"] is False
    assert "confirm" not in ctx.tools["onair_window_share"]["schema"]["parameters"]["properties"]["action"]["enum"]
    ctx.surface = "on_air"
    assert not cmd("confirm-window").startswith("Confirmed") and not ctx.questions
    ctx.surface = "cli"
    ctx.choice = "cancel"
    assert cmd("confirm-window").startswith("Cancelled") and len(ctx.questions) == 1 and not_shown()
    ctx.choice = "go"                                              # the go-live card's value is not this card's
    assert cmd("confirm-window").startswith("Cancelled") and not_shown()
    ctx.ask_choice = None                                          # a chat without a confirmation card
    assert "cannot show a confirmation card" in cmd("confirm-window") and not_shown()
    del ctx.ask_choice

    def sneaky(question, choices):                                 # the input changes while the card is open
        obs.inputs[C.window_input]["settings"]["RestoreToken"] = "tok-race"
        return "confirm"
    ctx.ask_choice = sneaky
    assert cmd("confirm-window").startswith("Nothing confirmed") and not_shown()
    del ctx.ask_choice
    ctx.choice = "confirm"                                         # genuine (modelled) host owner card
    assert cmd("confirm-window").startswith("Confirmed")
    assert "ONE window" in ctx.questions[-1][0] and ctx.questions[-1][1][0]["value"] == "confirm"
    w = call(ctx, "onair_window_share", {"action": "discover"})["window"]
    assert w["operator_confirmed"] and w["selection"] == "operator_confirmed" and w["capture_type_verified"] is False
    assert w["ready"] is True
    shown = call(ctx, "onair_window_share", {"action": "show"})
    assert shown["ok"] and obs.program == C.window_scene == shown["program_scene"]
    assert shown["selection"] == "operator_confirmed" and shown["capture_type_verified"] is False
    assert call(ctx, "onair_window_share", {"action": "hide"})["ok"] and obs.program == C.presentation_scene

    def revoked_by(action):
        assert cmd("confirm-window").startswith("Confirmed")
        action()
        w = call(ctx, "onair_window_share", {"action": "discover"})["window"]
        return w["operator_confirmed"] is False and w["ready"] is False and not_shown()
    reopen = revoked_by(lambda: call(ctx, "onair_window_share", {"action": "open_chooser"}))
    assert reopen and obs.requests("PressInputPropertiesButton")[-1][1]["propertyName"] == "Reload"
    assert revoked_by(lambda: call(ctx, "onair_window_share", {"action": "prepare"}))
    assert revoked_by(lambda: obs.inputs[C.window_input]["settings"].update(RestoreToken="tok-2"))   # re-picked

    def recreate():                                                # same name and settings, new OBS input
        settings = dict(obs.inputs[C.window_input]["settings"])
        del obs.inputs[C.window_input]
        obs.scenes[C.window_scene] = [i for i in obs.scenes[C.window_scene] if i["sourceName"] != C.window_input]
        obs.add_input(C.window_scene, C.window_input, "pipewire-screen-capture-source", settings)
    assert revoked_by(recreate)
    assert revoked_by(lambda: cmd("revoke-window"))
    assert obs.program == C.presentation_scene


def test_preexisting_combined_input_producing_4k_is_not_shown_unconfirmed(obs):
    """Real current-kind negative regression: frames from the combined kind are never enough on their own."""
    obs.add_input("Scene", C.window_input, "pipewire-screen-capture-source", {"RestoreToken": "t"}, frames=(3840, 2160))
    ctx = served(obs)
    call(ctx, "onair_obs_setup", {"action": "configure"})
    call(ctx, "onair_window_share", {"action": "prepare"})        # puts the existing input in the owned scene
    w = call(ctx, "onair_window_share", {"action": "discover"})["window"]
    assert w["producing_frames"] and w["ready"] is False and w["capture_type_verified"] is False
    assert w["selection"] == "operator_selected_unconfirmed"
    assert call(ctx, "onair_window_share", {"action": "show"})["ok"] is False and not obs.requests("SetCurrentProgramScene")


def test_linux_legacy_preexisting_portal_input(obs):
    """An owned window-only PipeWire input that already exists (older scene collection) is still usable."""
    obs.add_input("On Air: Shared window", C.window_input, "pipewire-window-capture-source", {})
    ctx = served(obs)
    call(ctx, "onair_obs_setup", {"action": "configure"})
    created = len(obs.requests("CreateInput"))
    d = call(ctx, "onair_window_share", {"action": "discover"})["window"]
    assert d["exists"] and d["legacy_input"] is True and "older window-only PipeWire input" in d["label"]
    assert d["window_kinds_available"] == [] and d["chooser"] == "portal" and d["ready"] is False
    assert any("xdg-desktop-portal" in p for p in d["prerequisites"])
    assert call(ctx, "onair_window_share", {"action": "prepare"})["ok"]          # only ensures the scene item
    assert len(obs.requests("CreateInput")) == created and obs.inputs[C.window_input]["settings"] == {}
    assert obs.items(C.window_scene) == [C.window_input, C.overlay_input]
    shown = call(ctx, "onair_window_share", {"action": "show"})
    assert shown["ok"] is False and not obs.requests("SetCurrentProgramScene")
    assert call(ctx, "onair_window_share", {"action": "open_chooser"})["ok"]
    assert obs.requests("PressInputPropertiesButton")[-1][1] == {"inputName": C.window_input, "propertyName": "Reload"}
    obs.frames[C.window_input] = (1280, 720)                      # a human chose a window in the system dialog
    shown = call(ctx, "onair_window_share", {"action": "show"})
    assert shown["ok"] and shown["program_scene"] == C.window_scene == obs.program and shown["share"] == "os_window"
    assert shown["selection"] == "window_only_kind" and shown["capture_type_verified"] is True
    hidden = call(ctx, "onair_window_share", {"action": "hide"})
    assert hidden["ok"] and obs.program == C.presentation_scene and hidden["share"] == "browser_source"


def test_screen_capable_kind_is_never_a_window_share(obs):
    obs.add_input("Scene", C.window_input, "pipewire-desktop-capture-source", {}, frames=(3840, 2160))  # legacy screen
    ctx = served(obs)
    w = call(ctx, "onair_window_share", {"action": "discover"})["window"]
    assert w["ready"] is False and "whole screen" in w["error"]
    assert any("Delete or rename" in p and "'prepare'" in p for p in w["prerequisites"])  # prepare can then create
    assert call(ctx, "onair_window_share", {"action": "show"})["ok"] is False
    assert call(ctx, "onair_window_share", {"action": "prepare"})["ok"] is False
    assert ctx.commands["onair-broadcast"]("confirm-window").startswith("Nothing confirmed") and not ctx.questions
    assert not obs.requests("SetCurrentProgramScene") and not obs.requests("CreateInput")


@pytest.mark.parametrize("platform,session,kind,unselected,selected", [
    ("windows", None, "window_capture", {"window": ""}, {"window": "Demo:DemoClass:demo.exe"}),
    ("macos", None, "screen_capture", {"type": 0, "window": 77}, {"type": 1, "window": 77}),
    ("linux", "x11", "xcomposite_input", {"capture_window": ""}, {"capture_window": "4242\r\nDemo\r\ndemo"}),
])
def test_list_selector_platforms_require_human_selection(platform, session, kind, unselected, selected):
    fake = FakeObs(platform=platform, session=session).start()
    try:
        ctx = served(fake)
        d = call(ctx, "onair_window_share", {"action": "discover"})["window"]
        assert d["window_kinds_available"][0] == kind and d["can_create_input"] is True
        assert any("'prepare'" in p for p in d["prerequisites"])
        out = call(ctx, "onair_window_share", {"action": "prepare"})
        assert fake.inputs[C.window_input]["kind"] == kind
        fake.inputs[C.window_input]["settings"] = dict(unselected)
        fake.frames[C.window_input] = (800, 600)
        w = call(ctx, "onair_window_share", {"action": "discover"})["window"]
        assert w["selected"] is False and w["ready"] is False and w["prerequisites"]
        if platform == "macos":
            assert any("Screen Recording" in p for p in w["prerequisites"])
        assert call(ctx, "onair_window_share", {"action": "open_chooser"})["ok"] is False
        fake.inputs[C.window_input]["settings"] = dict(selected)
        w = call(ctx, "onair_window_share", {"action": "discover"})["window"]
        assert w["ready"] is True and "Demo" not in json.dumps(w)   # window titles are never returned
        assert call(ctx, "onair_window_share", {"action": "show"})["ok"]
    finally:
        fake.stop()


def test_status_combines_surface_obs_and_window(obs):
    ctx = served(obs)
    call(ctx, "onair_obs_setup", {"action": "configure"})
    st = call(ctx, "onair_present_status")
    assert st["surface"]["label"] == "Browser Source (web page, not screen capture)"
    assert st["obs"]["sources"]["presentation"]["url_matches"] and st["window"]["share"] == "os_window"
    assert st["obs"]["stream"]["state"] == "inactive"
