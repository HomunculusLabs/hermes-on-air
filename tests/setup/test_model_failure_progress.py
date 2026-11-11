"""A model draft failure is reported truthfully: HTTP 429 named without leaking the exception, and the
setup job's progress ends with no step left 'running'.

Real engine, real onboarding creator and real private preview runtime (loopback, ephemeral ports). The
host-owned ``ctx.llm`` edge is replaced BEFORE any external effect by the disclosed in-process fixtures
below; they raise or answer locally and make no network, provider or credential use. A fixture 429 is NOT
a real provider answer.
"""
from __future__ import annotations

import asyncio
import os
import threading
from types import SimpleNamespace

import pytest

from onair import setup_engine as se

from test_quick_setup import SECRET, all_text, fresh_engine, owner, wait_done  # noqa: F401

LEAK = "sk-zai-FIXTURE-0123456789abcdef"
URL = "https://api.example.invalid/api/coding/paas/v4/chat/completions?key=" + LEAK
REJECTED = ("The model provider rejected the request (HTTP 429). Check model access or limits in Hermes; "
            "no preview reply was generated.")
GENERIC = "The model call failed. No preview reply was generated."
NEUTRAL = ("Not completed: the model step failed, so no suggested reply is waiting. Check the private preview's "
           "status before trying again.")
SHUTDOWN_CLAIMS = ("shut down", "was stopped", "closed", "is off", "turned off")


class ProviderError(Exception):
    """FIXTURE shaped like an SDK status error: stable ``status_code`` plus secret-bearing text/body."""

    def __init__(self, status_code, text=""):
        super().__init__(text or f"Error code: {status_code} - {{'error': {{'code': '1311', 'message': "
                                 f"'plan does not include GLM-5.3-FlashX', 'key': '{LEAK}', 'url': '{URL}'}}}}")
        self.status_code = status_code
        self.body = {"error": {"code": "1311", "key": LEAK}}
        self.response = SimpleNamespace(url=URL, headers={"authorization": "Bearer " + LEAK})


class BrokenStatus(Exception):
    @property
    def status_code(self):
        raise RuntimeError(LEAK)


class FixtureModel:
    """FIXTURE for ``ctx.llm``: raises the given error (or answers) in-process; counts every call."""

    def __init__(self, error=None, gate=None):
        self.error, self.gate, self.calls, self.probe_calls = error, gate, 0, 0
        self.entered = threading.Event()

    def complete(self, messages, **kw):
        if kw.get("max_tokens") == 4:
            self.probe_calls += 1
            return SimpleNamespace(text="OK", provider="fixture-provider", model="fixture-model")
        self.calls += 1
        self.entered.set()
        if self.gate is not None:
            self.gate.wait(30)
        if self.error is not None:
            raise self.error
        return SimpleNamespace(text="Yes, it's on! Welcome to the stream.", provider="fixture-provider",
                               model="fixture-model")


def _preview(client):
    preview = object.__new__(se.PreviewShow)
    preview.persona, preview.max_tokens = "fixture", 10
    preview.draft_error = preview.model_attribution = ""
    preview.model_client = client
    return preview


# ---- the actual consuming _draft_once ------------------------------------------------------------
@pytest.mark.parametrize("error,expected", [
    (ProviderError(429), REJECTED),
    (ProviderError(500), GENERIC),
    (ProviderError("429"), GENERIC),  # only a stable integer status classifies
    (ProviderError(True), GENERIC),
    (RuntimeError("Error code: 429 " + LEAK), GENERIC),  # text is never parsed for a status
    (BrokenStatus(), GENERIC),
])
def test_draft_once_names_only_a_stable_429_and_never_the_exception(error, expected):
    model = FixtureModel(error=error)
    preview = _preview(model)
    assert preview._draft_once("hi") == ""
    assert preview.draft_error == expected
    assert model.calls == 1  # no automatic retry, no fallback reply
    assert LEAK not in preview.draft_error and "api.example" not in preview.draft_error
    assert "outage" not in preview.draft_error.lower() and "down" not in preview.draft_error.lower()


def test_draft_once_success_after_a_429_clears_the_error():
    preview = _preview(FixtureModel(error=ProviderError(429)))
    preview._draft_once("hi")
    preview.model_client = FixtureModel()
    assert preview._draft_once("hi") == "Yes, it's on! Welcome to the stream."
    assert preview.draft_error == "" and preview.model_attribution == "fixture-provider · fixture-model"


# ---- the SetupEngine job and its progress --------------------------------------------------------
def _run_model_job(eng, tmp_path, **answers):
    plan = eng.plan({"channel": "cozy_fox", "use_model": True, "target": os.fspath(tmp_path / "stage"),
                     **answers})
    assert plan["ok"], plan
    assert eng.model_client.probe_calls == 1  # the subsequent draft still exercises the original failure
    eng.apply(plan["digest"], se.owner_consent(plan["digest"], "dashboard"))
    return plan, wait_done(eng)


def _states(job):
    return {s["key"]: s["state"] for s in job["steps"]}


def test_429_fails_the_model_step_terminally_and_points_at_model_access(owner, tmp_path):
    model = FixtureModel(error=ProviderError(429))
    eng = fresh_engine(owner, model_client=model)
    _, job = _run_model_job(eng, tmp_path, style={"tone": "upbeat"})
    try:
        assert job["state"] == "failed", job
        assert _states(job) == {"check": "done", "create": "done", "style": "done", "model": "failed",
                                "preview": "failed", "readback": "waiting"}, job
        assert "running" not in _states(job).values()
        steps = {s["key"]: s for s in job["steps"]}
        assert steps["model"]["detail"] == REJECTED and job["error"] == REJECTED
        assert steps["preview"]["detail"] == NEUTRAL
        assert [s["key"] for s in job["steps"] if s["state"] == "failed"][0] == "model"  # what the desk shows
        assert "model's access or limits in Hermes" in job["next_action"]
        assert "before trying again" in job["next_action"] and "saved stage and style are reused" in job["next_action"]
        assert model.calls == 1  # no automatic retry
        # The public job (status) carries the same terminal progress; nothing secret anywhere it persists.
        public = eng.status()["job"]
        assert {s["key"]: s["state"] for s in public["steps"]} == _states(job)
        text = all_text(eng)
        assert LEAK not in text and "api.example" not in text and SECRET not in text
        # Stop/quiescence preserved: the preview the failed step started is off and stays stoppable.
        assert eng.preview_facts()["running"] is False
        assert eng.stop_preview()["ok"] is True
        # The saved stage and style are reused by the next attempt (here with the canned stand-in).
        status = eng.status()
        assert status["saved_stage"]["channel"] == "cozy_fox", status
        assert status["saved_stage"]["style"]["tone"] == "upbeat", status["saved_stage"]
        again = eng.plan({"use_model": False})
        assert again["ok"] and again["kind"] == "resume", again
        eng.apply(again["digest"], se.owner_consent(again["digest"], "dashboard"))
        retry = wait_done(eng)
        assert retry["state"] == "done", retry
        assert _states(retry)["create"] == "skipped" and _states(retry)["model"] == "skipped"
        assert model.calls == 1
        assert eng.preview_facts()["running"] is True
    finally:
        eng.stop_preview()


def test_non_429_model_failure_is_terminal_with_the_unchanged_next_action(owner, tmp_path):
    model = FixtureModel(error=RuntimeError("Connection refused " + URL))
    eng = fresh_engine(owner, model_client=model)
    _, job = _run_model_job(eng, tmp_path)
    try:
        assert job["state"] == "failed" and job["error"] == GENERIC, job
        assert _states(job)["model"] == "failed" and _states(job)["preview"] == "failed"
        assert "running" not in _states(job).values()
        assert job["next_action"] == "Press Set up & preview again; your saved folder is reused."
        assert LEAK not in all_text(eng) and "Connection refused" not in all_text(eng)
        assert eng.preview_facts()["running"] is False
    finally:
        eng.stop_preview()


def test_success_with_a_model_still_drafts(owner, tmp_path):
    model = FixtureModel()
    eng = fresh_engine(owner, model_client=model)
    _, job = _run_model_job(eng, tmp_path)
    try:
        assert job["state"] == "done", job
        assert _states(job) == {"check": "done", "create": "done", "style": "skipped", "model": "done",
                                "preview": "done", "readback": "done"}
        assert model.calls == 1 and eng.preview_facts()["running"] is True
    finally:
        eng.stop_preview()


def test_preview_start_failure_interrupts_a_running_model_step(owner, tmp_path, monkeypatch):
    """A preview runtime failure is still the preview's failure; the not-yet-drafted model step is not done."""
    def no_start(self):
        raise se.SetupError("The private preview could not start.")
    monkeypatch.setattr(se.PreviewShow, "start", no_start)
    model = FixtureModel()
    eng = fresh_engine(owner, model_client=model)
    _, job = _run_model_job(eng, tmp_path)
    assert job["state"] == "failed" and job["error"] == "The private preview could not start.", job
    assert _states(job)["preview"] == "failed" and _states(job)["model"] == "interrupted", job
    assert job["next_action"] == "Press Set up & preview again; your saved folder is reused."
    assert model.calls == 0


def test_cancel_during_a_429_draft_is_cancelled_not_failed(owner, tmp_path):
    gate = threading.Event()
    model = FixtureModel(error=ProviderError(429), gate=gate)
    eng = fresh_engine(owner, model_client=model)
    plan = eng.plan({"channel": "cozy_fox", "use_model": True, "target": os.fspath(tmp_path / "stage")})
    eng.apply(plan["digest"], se.owner_consent(plan["digest"], "dashboard"))
    try:
        assert model.entered.wait(20)
        assert eng.cancel()["job"]["state"] == "cancelling"
        gate.set()
        job = wait_done(eng)
        assert job["state"] == "cancelled", job
        assert "running" not in _states(job).values() and "failed" not in _states(job).values(), job
        assert not os.path.lexists(tmp_path / "stage")  # the folder this job made is removed
        assert eng.preview_facts()["running"] is False
        assert model.calls == 1
    finally:
        gate.set()
        eng.stop_preview()


@pytest.mark.parametrize("delayed", [True, False], ids=["draining_teardown", "ordinary_teardown"])
def test_failed_model_step_never_claims_a_shutdown_it_has_not_confirmed(owner, tmp_path, monkeypatch, delayed):
    """The failed preview step's progress text stays true whether or not the preview's teardown has finished.

    FIXTURE: ``PreviewShow._teardown`` is wrapped to record the real show and, for ``draining_teardown``, to hold
    the real teardown until released, so the real loopback control endpoint is still answering while the
    failed job is read. The model edge is the in-process 429 fixture above; nothing leaves the process.
    """
    release, entered, shows = threading.Event(), threading.Event(), []
    original = se.PreviewShow._teardown

    async def recorded_teardown(show):
        shows.append(show)
        entered.set()
        while delayed and not release.is_set():
            await asyncio.sleep(0.05)
        await original(show)

    monkeypatch.setattr(se.PreviewShow, "_teardown", recorded_teardown)
    model = FixtureModel(error=ProviderError(429))
    eng = fresh_engine(owner, model_client=model)
    try:
        _, job = _run_model_job(eng, tmp_path)
        assert job["state"] == "failed" and job["error"] == REJECTED, job
        assert _states(job)["model"] == "failed" and _states(job)["preview"] == "failed", job
        detail = next(s for s in job["steps"] if s["key"] == "preview")["detail"]
        public = next(s for s in eng.status()["job"]["steps"] if s["key"] == "preview")["detail"]
        assert not any(claim in detail.lower() for claim in SHUTDOWN_CLAIMS), detail
        assert detail == public == NEUTRAL
        assert "model's access or limits in Hermes" in job["next_action"]
        assert entered.wait(15) and shows, "the real preview teardown was not reached"
        show = shows[0]
        if delayed:  # the counterexample is live while the text is read: thread + HTTP control still up
            assert show.thread is not None and show.thread.is_alive()
            status, state = se.preview_call(eng.dir, "GET", "/state")
            assert status == 200 and isinstance(state, dict), (status, state)
            assert eng.preview_facts().get("running") is True
            release.set()
        show.thread.join(15)
        assert not show.thread.is_alive(), "fixture teardown did not finish"
        stopped = eng.stop_preview()
        assert stopped["ok"] is True and stopped["running"] is False, stopped
        assert eng.preview_facts()["running"] is False
        assert se.preview_call(eng.dir, "GET", "/state")[0] == 503
        assert model.calls == 1
    finally:
        release.set()
        for show in shows:
            if show.thread is not None:
                show.thread.join(15)
        eng.stop_preview()


# ---- the preview's model route: the show's own slot (auxiliary.onair_show) ---------------------------------------
# Real seam: the plugin's _ShowModelCtx/_ShowModel wrapper around the host's real agent.plugin_llm.PluginLlm (its own
# trust gate) consumed by the real PreviewShow._draft_once. Edges replaced: the host's call_llm (FIXTURE recorder:
# no provider, network or credential) and the auxiliary-task registry read, standing in for the slot that
# register_auxiliary_task records at load. Before the fix only purpose=onair.private-show was routed, so the preview
# ran on the unbound default auxiliary client (observed holding a stale, unentitled model) while the show used the slot.
ROUTE_PLUGIN = "on-air"
ROUTE_REPLY = "Yes, it's on! Welcome to the stream."


def _route_response(text=ROUTE_REPLY):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))], model="fixture-model",
                           usage=None)


class _RouteHostCtx:
    """A plugin context carrying the host's real PluginLlm; register_auxiliary_task is recorded."""

    def __init__(self, llm):
        self.llm, self.registered = llm, []

    def register_auxiliary_task(self, key, **kw):
        self.registered.append(key)

    def get_config(self, key, default=None):
        return default


@pytest.fixture
def route_host(owner, monkeypatch):  # noqa: F811 - an owner home, never a stage home
    plugin_llm = pytest.importorskip("agent.plugin_llm")  # host SDK seam; a SKIP is "not run"
    import agent.auxiliary_client as ac
    import hermes_cli.plugins as hp
    from onair import adapter
    calls, registry = [], []
    monkeypatch.setattr(ac, "call_llm", lambda **kw: calls.append(kw) or _route_response())
    monkeypatch.setattr(hp, "get_plugin_auxiliary_tasks", lambda: list(registry))
    llm = plugin_llm.PluginLlm(plugin_id=ROUTE_PLUGIN,
                               policy_loader=lambda pid: plugin_llm._TrustPolicy(plugin_id=pid))  # default trust
    raw = _RouteHostCtx(llm)
    routed = adapter._register_show_model_task(raw)
    registry.extend({"key": k, "plugin": ROUTE_PLUGIN} for k in raw.registered)
    return SimpleNamespace(ctx=adapter._ShowModelCtx(raw, routed), raw=raw, routed=routed, calls=calls,
                           registry=registry, adapter=adapter, plugin_llm=plugin_llm, ac=ac)


def test_setup_preview_draft_routes_through_the_show_model_slot(route_host):
    assert route_host.routed and route_host.raw.registered == ["onair_show"]
    preview = _preview(route_host.ctx.llm)
    assert preview._draft_once("is the stream on?") == ROUTE_REPLY and preview.draft_error == ""
    assert len(route_host.calls) == 1
    call = route_host.calls[0]
    assert call["task"] == "onair_show"
    assert call["provider"] is None and call["model"] is None  # no override: the slot (unset: main model) decides
    assert call["route_info"] == {}  # the host reports the slot's real route back
    assert preview.model_attribution.endswith("fixture-model")


def test_show_route_and_other_requests_are_unchanged(route_host):
    msgs = [{"role": "user", "content": "hi"}]
    route_host.ctx.llm.complete(messages=msgs, purpose="onair.private-show")
    route_host.ctx.llm.complete(messages=msgs, purpose="onair.label")
    route_host.ctx.llm.complete(messages=msgs)
    assert [c["task"] for c in route_host.calls] == ["onair_show", None, None]


def test_a_caller_named_task_is_not_rewritten(route_host):
    route_host.registry.append({"key": "onair_other", "plugin": ROUTE_PLUGIN})
    route_host.ctx.llm.complete(messages=[{"role": "user", "content": "hi"}], purpose="onair.setup-preview",
                                task="onair_other")
    assert route_host.calls[-1]["task"] == "onair_other"


def test_preview_route_keeps_the_host_trust_gate(route_host):
    route_host.registry.clear()  # the host no longer lists the slot as this plugin's: its gate must refuse
    preview = _preview(route_host.ctx.llm)
    assert preview._draft_once("hi") == "" and preview.draft_error == se.MODEL_FAILED  # generic text only
    assert route_host.calls == []  # refused before any provider call


def test_without_the_slot_the_preview_stays_on_the_host_client(route_host, monkeypatch):
    llm = route_host.raw.llm
    older = SimpleNamespace(llm=llm, get_config=lambda k, d=None: d)  # an older host: no register_auxiliary_task
    wrapped = route_host.adapter._ShowModelCtx(older, route_host.adapter._register_show_model_task(older))
    assert wrapped.llm is llm
    assert _preview(wrapped.llm)._draft_once("hi") == ROUTE_REPLY and route_host.calls[-1]["task"] is None
