"""The private show's voice answer: false means captions only for that show; it can never turn a voice on."""
import os
from types import SimpleNamespace

from onair import setup_engine as se
from onair import show_runtime as sr
from test_quick_setup import fresh_engine, owner, wait_done  # noqa: F401  (owner is a fixture)


def _saved_stage(owner, tmp_path, tts=None):
    client = SimpleNamespace(complete=lambda **kw: SimpleNamespace(text="OK"))
    eng = fresh_engine(owner, model_client=client)  # fixture responds to the show's readiness check
    eng.tts_dispatch = tts
    plan = eng.plan({"channel": "cozy_fox", "use_model": False, "target": os.fspath(tmp_path / "stage")})
    eng.apply(plan["digest"], se.owner_consent(plan["digest"], "dashboard"))
    assert wait_done(eng)["state"] == "done"
    eng.stop_preview()
    return eng


def _voice_line(plan):
    return next(e for e in plan["effects"] if e.startswith("Voice:"))


def test_voice_false_plans_a_captions_only_show(owner, tmp_path, monkeypatch):
    eng = _saved_stage(owner, tmp_path, tts=lambda *a, **k: None)
    # Hermes text-to-speech is available here: the default plan speaks.
    monkeypatch.setattr(sr, "tts_connection", lambda dispatch: {"usable_here": True, "label": "fixture-tts",
                                                               "next_action": ""})
    spoken = sr.plan(eng, {"brief": "paper planes"}, remember=False)
    assert spoken["ok"] and spoken["voice"]["usable_here"] is True and "fixture-tts" in _voice_line(spoken)
    silent = sr.plan(eng, {"brief": "paper planes", "voice": False}, remember=False)
    assert silent["ok"] and silent["voice"]["usable_here"] is False and silent["voice"]["off_by_choice"] is True
    assert _voice_line(silent) == "Voice: off for this show, as you asked. Captions only."
    assert silent["digest"] != spoken["digest"]  # the approval covers the choice
    # the same answers plan the same show again (what start() re-checks before running)
    assert sr.plan(eng, {"brief": "paper planes", "voice": False}, remember=False)["digest"] == silent["digest"]
    # voice true is the default, not a second way to speak
    assert sr.plan(eng, {"brief": "paper planes", "voice": True}, remember=False)["digest"] == spoken["digest"]


def test_voice_true_cannot_turn_on_an_unavailable_voice(owner, tmp_path, monkeypatch):
    eng = _saved_stage(owner, tmp_path)
    monkeypatch.setattr(sr, "tts_connection", lambda dispatch: {"usable_here": False, "label": "",
                                                               "next_action": "no dispatcher"})
    plan = sr.plan(eng, {"brief": "paper planes", "voice": True}, remember=False)
    assert plan["ok"] and plan["voice"]["usable_here"] is False
    assert _voice_line(plan).startswith("Voice: NOT available here, so captions only.")


def test_voice_must_be_a_boolean(owner, tmp_path):
    eng = _saved_stage(owner, tmp_path)
    for bad in ("off", 0, None, "false"):
        out = sr.plan(eng, {"brief": "paper planes", "voice": bad}, remember=False)
        assert out["ok"] is False and "voice" in out["errors"], bad


def test_show_plan_schema_offers_the_voice_answer():
    from onair.setup_agent import SCHEMAS
    props = SCHEMAS["onair_show_plan"]["parameters"]["properties"]
    assert props["voice"]["type"] == "boolean" and set(props) == {"brief", "policy", "quiet_seconds", "voice"}
