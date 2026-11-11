"""A non-null host facade is not evidence that a model request works."""
import contextvars
import logging
import os
import threading
from types import SimpleNamespace

import pytest

from onair import setup_agent as sa
from onair import setup_engine as se
from test_quick_setup import FakeCtx, owner, wait_done  # noqa: F401


class Model:
    def __init__(self, error=None, block=None):
        self.error, self.block, self.calls = error, block, []

    def complete(self, **kw):
        self.calls.append(kw)
        if self.block:
            self.block.wait(10)
        if self.error:
            raise self.error
        return SimpleNamespace(text="ok")


def _answers(tmp_path):
    return {"channel": "cozy_fox", "target": os.fspath(tmp_path / "stage"), "use_model": True}


def test_failure_refuses_before_plan_or_folder_and_redacts(owner, tmp_path, caplog):
    client = Model(RuntimeError("secret-token-and-url"))
    eng = se.SetupEngine(owner, model_client=client)
    before = eng.status()["model_connection"]
    files_before = sorted(p.relative_to(owner) for p in owner.rglob("*"))
    assert before["state"] == "untested" and before["usable_here"] is False
    assert before["next_action"] == "The model connection is untested here. Ask your Hermes agent to plan setup and test it."
    with caplog.at_level(logging.DEBUG):
        plan = eng.plan(_answers(tmp_path))
    assert not plan["ok"] and plan["next_action"] == se.MODEL_UNAVAILABLE
    assert len(client.calls) == 1 and client.calls[0]["max_tokens"] <= 4
    assert not (tmp_path / "stage").exists()
    assert sorted(p.relative_to(owner) for p in owner.rglob("*")) == files_before
    assert eng.status()["model_connection"]["state"] == "unavailable"
    assert "RuntimeError" in caplog.text and "secret-token-and-url" not in caplog.text


def test_success_is_cached_without_status_calls(owner, tmp_path):
    client = Model()
    eng = se.SetupEngine(owner, model_client=client)
    for _ in range(3):
        assert eng.status()["model_connection"]["state"] == "untested"
    assert not client.calls
    first = eng.plan(_answers(tmp_path))
    assert first["ok"] and first["model_connection"]["state"] == "ready"
    assert len(client.calls) == 1
    assert client.calls[0]["purpose"] == "onair.setup-preview"
    assert eng.plan(_answers(tmp_path))["ok"]
    assert len(client.calls) == 1
    assert not (tmp_path / "stage").exists()


def test_replaced_client_is_not_covered_by_previous_success(owner, tmp_path):
    client = Model()
    eng = se.SetupEngine(owner, model_client=client)
    assert eng.plan(_answers(tmp_path))["ok"]
    eng.model_client = Model(RuntimeError("failure"))
    assert eng.status()["model_connection"]["state"] == "untested"
    assert not eng.plan(_answers(tmp_path))["ok"]


def test_approval_entry_rechecks_changed_context_before_card(owner, tmp_path):
    eng = se.SetupEngine(owner, model_client=Model())
    plan = eng.plan(_answers(tmp_path))
    assert plan["ok"]
    eng.model_client = Model(RuntimeError("secret"))
    ctx = FakeCtx(surface="cli")
    command = sa.make_command(ctx, get_engine=lambda: eng)
    assert command("approve " + plan["code"]) == se.MODEL_UNAVAILABLE
    assert ctx.asked == [] and not (tmp_path / "stage").exists()


def test_changed_context_invalidates_success(owner, tmp_path):
    scope = contextvars.ContextVar("route", default="first")
    client = Model()
    eng = se.SetupEngine(owner, model_client=client)
    assert eng.plan(_answers(tmp_path))["ok"]
    token = scope.set("second")
    try:
        assert eng.status()["model_connection"]["state"] == "untested"
        client.error = RuntimeError("changed route")
        plan = eng.plan(_answers(tmp_path))
        assert not plan["ok"] and plan["next_action"] == se.MODEL_UNAVAILABLE
        assert len(client.calls) == 2
    finally:
        scope.reset(token)


def test_liveness_timeout_is_bounded_and_does_not_stack_calls(owner, tmp_path, monkeypatch):
    release = threading.Event()
    client = Model(block=release)
    eng = se.SetupEngine(owner, model_client=client)
    monkeypatch.setattr(se, "MODEL_PROBE_TIMEOUT_S", 0.05)
    try:
        assert not eng.plan(_answers(tmp_path))["ok"]
        assert not eng.plan(_answers(tmp_path))["ok"]
        assert len(client.calls) == 1
        assert not eng.dir.exists()
    finally:
        release.set()


def test_missing_client_during_job_is_logged_and_never_canned(owner, tmp_path, caplog):
    home = tmp_path / "stage"
    home.mkdir()
    (home / "config.yaml").write_text("{}")
    (home / "SOUL.md").write_text("fixture")
    eng = se.SetupEngine(owner)
    with caplog.at_level(logging.DEBUG), pytest.raises(se._ModelDraftError):
        eng._start_preview({"home": str(home), "id": "fixture"}, {"use_model": True})
    assert "step=preview" in caplog.text and "category=ClientUnavailable" in caplog.text


def test_preview_timeout_is_logged_and_invalidates_readiness(owner, tmp_path, monkeypatch, caplog):
    from onair.onboarding import StageOptions, create_stage_home
    home = tmp_path / "stage"
    create_stage_home(str(home), StageOptions(channel="cozy_fox"))
    client = Model()
    eng = se.SetupEngine(owner, model_client=client)
    eng.dir.mkdir(parents=True)
    assert se._probe_model(client) == ""
    class SilentPreview:
        def __init__(self, **kwargs):
            self.control = SimpleNamespace(token="fixture", url="fixture")
            self.overlay = SimpleNamespace(overlay_url="fixture")
        def start(self): pass
        def _publish(self): pass
        def send_test_message(self): pass
        def facts(self): return {"pending": 0, "draft_error": ""}
    monkeypatch.setattr(se, "PreviewShow", SilentPreview)
    monkeypatch.setattr(se, "MODEL_TIMEOUT_S", -5)
    monkeypatch.setattr(eng, "_watch_release", lambda _: None)
    with caplog.at_level(logging.DEBUG), pytest.raises(se._ModelDraftError, match="did not answer"):
        eng._start_preview({"home": str(home), "id": "fixture"}, {"use_model": True})
    assert "step=preview" in caplog.text and "category=TimeoutError" in caplog.text
    assert se._model_state(client) == "unavailable"


def test_failed_preview_invalidates_prior_ready_state(owner, tmp_path):
    client = Model()
    eng = se.SetupEngine(owner, model_client=client)
    assert eng.plan(_answers(tmp_path))["ok"]
    client.error = RuntimeError("secret")
    preview = object.__new__(se.PreviewShow)
    preview.persona, preview.max_tokens = "fixture", 10
    preview.draft_error = preview.model_attribution = ""
    preview.model_client = client
    assert preview._draft_once("hello") == ""
    assert eng.status()["model_connection"]["state"] == "unavailable"


def test_preview_failure_logs_category_without_provider_text(caplog):
    preview = object.__new__(se.PreviewShow)
    preview.persona, preview.max_tokens = "fixture", 10
    preview.draft_error = preview.model_attribution = ""
    preview.model_client = Model(RuntimeError("secret-token-and-url"))
    with caplog.at_level(logging.DEBUG):
        assert preview._draft_once("hello") == ""
    assert "step=preview" in caplog.text and "category=RuntimeError" in caplog.text
    assert "elapsed_ms=" in caplog.text and "secret-token-and-url" not in caplog.text


def test_context_reaches_probe_and_preview_worker(owner, tmp_path):
    runtime = contextvars.ContextVar("fixture_runtime", default=None)
    seen = []

    class ScopedModel:
        def complete(self, **kw):
            seen.append(runtime.get())
            if runtime.get() != "fixture":
                raise RuntimeError("wrong context")
            return SimpleNamespace(text="Hello there.", provider="fixture", model="fixture")

    token = runtime.set("fixture")
    eng = se.SetupEngine(owner, model_client=ScopedModel())
    try:
        plan = eng.plan(_answers(tmp_path))
        assert plan["ok"]
        eng.apply(plan["digest"], se.owner_consent(plan["digest"], "dashboard"))
        job = wait_done(eng)
        assert job["state"] == "done", job
        assert seen == ["fixture", "fixture"]
    finally:
        eng.stop_preview()
        runtime.reset(token)


def test_real_hermes_facade_keeps_runtime_through_preview(owner, tmp_path, monkeypatch):
    ac = pytest.importorskip("agent.auxiliary_client")
    plugins = pytest.importorskip("hermes_cli.plugins")
    seen = []

    def host_call(**kwargs):
        seen.append(ac._read_main_provider())
        if seen[-1] != "runtime-fixture":
            raise RuntimeError("runtime context was lost")
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="Hello."))],
                               model="fixture-model", usage=None)

    monkeypatch.setattr(ac, "call_llm", host_call)
    ctx = plugins.PluginContext(plugins.PluginManifest(name="on-air"), SimpleNamespace())
    token = ac.set_runtime_main("runtime-fixture", "fixture-model")
    eng = se.SetupEngine(owner, model_client=ctx.llm)
    try:
        plan = eng.plan(_answers(tmp_path))
        assert plan["ok"]
        eng.apply(plan["digest"], se.owner_consent(plan["digest"], "dashboard"))
        job = wait_done(eng)
        assert job["state"] == "done", job
        assert seen == ["runtime-fixture", "runtime-fixture"]
    finally:
        eng.stop_preview()
        ac.reset_runtime_main(token)


def test_rate_limit_message_is_not_erased(owner, tmp_path):
    class Limit(Exception):
        status_code = 429
    eng = se.SetupEngine(owner, model_client=Model(Limit("secret")))
    plan = eng.plan(_answers(tmp_path))
    assert not plan["ok"] and plan["error"] == se.MODEL_REJECTED_429
    assert not eng.dir.exists()
