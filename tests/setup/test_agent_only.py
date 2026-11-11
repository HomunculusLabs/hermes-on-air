"""AGENT-ONLY installed path with customization: no dashboard, no button.

The plugin's own ``register(ctx)`` (what Hermes calls for an installed plugin) registers the tools,
the ``/onair-setup`` command and the skill; this test then uses ONLY those registered handlers plus
the human's typed approval on the host's choice card. Fixture edges, conspicuously:
``FakeCtx`` (host PluginContext surface) and ``LoopbackFixtureModel`` (``ctx.llm`` answered by an
owned 127.0.0.1 server). A fixture reply is not a genuine model reply; this is tool-contract
evidence, not an LLM conversation.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import yaml

from onair import setup_agent as sa
from onair import setup_engine as se
from onair import style as st

from test_quick_setup import SECRET, FakeCtx, LoopbackFixtureModel, all_text, model_server, owner, wait_done  # noqa: F401


def _settings(home):
    return yaml.safe_load((Path(home) / "config.yaml").read_text())["plugins"]["entries"]["on-air"]["settings"]


def _call(ctx, name, args=None):
    return json.loads(ctx.tools[name][2](args or {}))


def test_agent_only_setup_customize_change_stop_resume(owner, tmp_path, model_server, monkeypatch):
    server, handler = model_server
    model = LoopbackFixtureModel(server.server_address[1])
    monkeypatch.setattr(se, "_ENGINES", {})
    monkeypatch.setattr("onair.setup_wizard.suggested_target", lambda: os.fspath(tmp_path / "stage"))
    ctx = FakeCtx(surface="cli", llm=model)
    from onair import adapter

    class HostCtx(FakeCtx):
        def register_platform(self, **kw):
            self.platform = kw
    ctx = HostCtx(surface="cli", llm=model)
    adapter.register(ctx)  # the installed plugin's entry point
    command = ctx.commands["onair-setup"]
    try:
        # 1. discover
        schema = _call(ctx, "onair_setup_schema")
        assert set(schema["style"]["schema"]["properties"]) == {"persona", "tone", "topics", "avoid_topics",
                                                                "reply_length", "reply_pacing", "output"}
        assert {"user_says": "keep it relaxed and reply less often",
                "style": {"tone": "relaxed", "reply_pacing": "less_often"}} in schema["style"]["examples"]
        assert set(schema["style"]["enforcement"]) == set(schema["style"]["schema"]["properties"])
        assert schema["style"]["enforcement"]["reply_length"].startswith("soft in the real stage")
        status = _call(ctx, "onair_setup_status")
        assert status["missing"] == ["channel"] and status["model_connection"]["state"] == "untested"
        assert status["model_connection"]["usable_here"] is False
        assert SECRET not in json.dumps(status) and "api_key" not in json.dumps(status)
        # 2. explicit refusal of unknown/unsafe customization (nothing silently dropped)
        bad = _call(ctx, "onair_setup_plan", {"channel": "cozy_fox", "use_model": True,
                                              "style": {"voice": "on", "system_prompt": "x", "autonomous": True,
                                                        "persona": "visit https://example.invalid", "tone": "angry"}})
        assert bad["ok"] is False and set(bad["errors"]) == {"style.voice", "style.system_prompt", "style.autonomous",
                                                             "style.persona", "style.tone"}
        assert "listening test" in bad["errors"]["style.voice"]
        # 3. propose setup + public persona/tone/topics in ONE plan (natural-language example mapped by the agent)
        style = {"persona": "A sleepy fox who loves cozy games.", "topics": ["cozy games", "tea"],
                 "avoid_topics": ["politics"], "tone": "relaxed", "reply_pacing": "less_often"}
        plan = _call(ctx, "onair_setup_plan", {"channel": "cozy_fox", "use_model": True, "style": style})
        assert plan["ok"] and plan["kind"] == "create" and model.calls == 0 and model.probe_calls == 1
        assert any("tone: friendly -> relaxed" in e and "reply pacing: normal -> less_often" in e for e in plan["effects"])
        assert any("fixture-provider · fixture-model" in e for e in plan["effects"])
        assert not (tmp_path / "stage").exists() and se.engine().job() is None  # planning changed nothing
        # 4. the human's typed approval + host card (a tool cannot do this)
        text = command("approve " + plan["code"])
        job = wait_done(se.engine())
        assert job["state"] == "done", job
        assert "Set up On Air like this?" in ctx.asked[-1][0] and job["approved_by"] == "slash-command"
        steps = {s["key"]: s["state"] for s in job["steps"]}
        assert steps == {"check": "done", "create": "done", "style": "done", "model": "done", "preview": "done",
                         "readback": "done"}, text
        # 5. read back: saved in the stage's own settings + public SOUL.md, and in effect in the running consumer
        home = Path(job["home"])
        saved = _settings(home)
        assert saved["reply_pacing"] == "less_often" and saved["public_style"]["tone"] == "relaxed"
        assert "politics" in saved["never_say"]
        soul = (home / "SOUL.md").read_text()
        assert "Relaxed and unhurried" in soul and "cozy games, tea" in soul and "Public stage persona" in soul
        sent = handler.seen[-1]
        assert "Relaxed and unhurried" in sent["messages"][0]["content"] and sent["max_tokens"] == 120
        progress = _call(ctx, "onair_setup_progress")
        live = progress["preview"]["style_in_effect"]
        assert live["routine_gap_s"] == 300.0 and live["max_worthy_per_turn"] == 1 and "politics" in live["never_say"]
        assert live["reply_pacing"] == "less_often"  # derived from the controller's actual limits
        assert progress["preview"]["running"] and progress["preview"]["input_kind"] == "private_test"
        assert _call(ctx, "onair_setup_status")["saved_stage"]["style"]["topics"] == ["cozy games", "tea"]
        # 6. change ONE supported preference; prove the consumer's actual effect
        change = _call(ctx, "onair_setup_plan", {"use_model": True, "style": {"reply_length": "short",
                                                                              "reply_pacing": "rare"}})
        assert change["ok"] and change["kind"] == "customize"
        assert any("reply length: medium -> short" in e for e in change["effects"])
        command("approve " + change["code"])
        job = wait_done(se.engine())
        assert job["state"] == "done" and {s["key"]: s["state"] for s in job["steps"]}["create"] == "skipped", job
        assert handler.seen[-1]["max_tokens"] == 60
        assert "one short spoken sentence" in handler.seen[-1]["messages"][0]["content"]
        live = _call(ctx, "onair_setup_progress")["preview"]["style_in_effect"]
        assert live["routine_gap_s"] == 600.0 and live["max_tokens"] == 60 and live["tone"] == "relaxed"
        assert live["reply_pacing"] == "rare"
        # The REAL stage consumer reads the same saved settings (adapter -> StageConfig.select).
        assert st.select_config(_settings(home)["reply_pacing"]).routine_gap_s == 600.0
        # 7. safety: pause, stop, status
        paused = _call(ctx, "onair_preview_safety", {"action": "pause"})
        assert paused["ok"] and paused["preview"]["privacy"] is True
        assert _call(ctx, "onair_preview_safety", {"action": "stop"})["preview"]["running"] is False
        assert _call(ctx, "onair_setup_status")["preview"]["running"] is False
        # 8. resume after a "restart" (new engine view of the same home): saved choices preserved
        monkeypatch.setattr(se, "_ENGINES", {})
        se.set_model_client(model)
        status = _call(ctx, "onair_setup_status")
        assert status["missing"] == [] and status["saved_stage"]["style"]["reply_pacing"] == "rare"
        resume = _call(ctx, "onair_setup_plan", {"use_model": False})
        assert resume["ok"] and resume["kind"] == "resume"
        command("approve " + resume["code"])
        job = wait_done(se.engine())
        assert job["state"] == "done", job
        live = _call(ctx, "onair_setup_progress")["preview"]["style_in_effect"]
        assert live["routine_gap_s"] == 600.0 and live["max_tokens"] == 60
        assert SECRET not in all_text(se.engine()) and SECRET not in (home / "config.yaml").read_text()
    finally:
        for eng in list(se._ENGINES.values()):
            eng.stop_preview()
        se.set_model_client(None)


def test_agent_and_form_build_the_same_customize_plan(owner, tmp_path):
    eng = se.SetupEngine(owner)
    plan = eng.plan({"channel": "cozy_fox", "target": os.fspath(tmp_path / "stage")})
    eng.apply(plan["digest"], se.owner_consent(plan["digest"], "dashboard"))
    assert wait_done(eng)["state"] == "done"
    eng.stop_preview()
    handlers = sa.make_handlers(FakeCtx(surface="cli"), get_engine=lambda: eng)
    patch = {"preset": "quiet", "topics": ["chess"]}
    agent = json.loads(handlers["onair_setup_plan"]({"style": patch}))
    form = eng.plan({"style": patch})
    assert agent["digest"] == form["digest"] and agent["kind"] == form["kind"] == "customize"
    assert agent["answers"]["style"] == {"reply_pacing": "rare", "reply_length": "short", "topics": ["chess"]}


def test_cancelled_customize_restores_the_exact_saved_bytes(owner, tmp_path):
    eng = se.SetupEngine(owner)
    plan = eng.plan({"channel": "cozy_fox", "target": os.fspath(tmp_path / "stage")})
    eng.apply(plan["digest"], se.owner_consent(plan["digest"], "dashboard"))
    home = Path(wait_done(eng)["home"])
    eng.stop_preview()
    before = {n: (home / n).read_bytes() for n in ("config.yaml", "SOUL.md")}
    gate, release = threading.Event(), threading.Event()

    def hook(key):
        if key == "model":  # the style step already wrote the files
            gate.set()
            release.wait(10)
    eng.step_hook = hook
    change = eng.plan({"style": {"tone": "dry"}})
    eng.apply(change["digest"], se.owner_consent(change["digest"], "dashboard"))
    assert gate.wait(10)
    assert b"Dry and understated" in (home / "SOUL.md").read_bytes()
    shown = eng.cancel()
    assert shown["job"]["state"] == "cancelling"  # never "cancelled" while the step still runs
    release.set()
    job = wait_done(eng)
    assert job["state"] == "cancelled"
    assert {n: (home / n).read_bytes() for n in before} == before
    assert st.read_style(home)["tone"] == "friendly"


def test_stale_style_plan_is_refused(owner, tmp_path):
    eng = se.SetupEngine(owner)
    plan = eng.plan({"channel": "cozy_fox", "target": os.fspath(tmp_path / "stage")})
    eng.apply(plan["digest"], se.owner_consent(plan["digest"], "dashboard"))
    home = Path(wait_done(eng)["home"])
    eng.stop_preview()
    change = eng.plan({"style": {"tone": "dry"}})
    st.write_files(home, st.render_files(home, st.merged(st.read_style(home), {"tone": "upbeat"}), None))
    out = eng.apply(change["digest"], se.owner_consent(change["digest"], "dashboard"))
    assert out["ok"] is False and out.get("stale") is True and st.read_style(home)["tone"] == "upbeat"


def test_real_stage_adapter_consumes_saved_pacing():
    src = (Path(__file__).resolve().parents[2] / "onair" / "adapter.py").read_text()
    assert "select=select_config(s[\"reply_pacing\"])" in src  # wiring; behaviour proven via select_config below
    assert st.select_config("less_often").max_worthy_per_turn == 1 and st.select_config("bogus").routine_gap_s == 120.0
    _ = time
