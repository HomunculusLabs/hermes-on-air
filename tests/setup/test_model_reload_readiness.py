"""Model access after a mid-life registration uses the host's current facade."""
from types import SimpleNamespace

from onair import setup_agent as sa
from onair import setup_engine as se
from test_quick_setup import FakeCtx, owner, wait_done  # noqa: F401


def test_registration_resolves_current_context_at_use(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    stale = SimpleNamespace(complete=lambda **kw: (_ for _ in ()).throw(RuntimeError("stale")))
    fresh = SimpleNamespace(complete=lambda **kw: SimpleNamespace(text="ok"))
    ctx = FakeCtx(llm=stale)
    monkeypatch.setattr(se, "_ENGINES", {})
    try:
        sa.register(ctx)
        engine = se.engine(tmp_path)
        assert engine.model_client is stale
        ctx.llm = fresh
        assert engine.model_client is fresh
        assert engine.model_client.complete(messages=[]).text == "ok"
    finally:
        se.set_model_client(None)


def test_job_resolves_changed_client_after_registration(owner, tmp_path, monkeypatch):
    class Model:
        def __init__(self, fail=False):
            self.fail, self.calls = fail, 0
        def complete(self, **kw):
            self.calls += 1
            if self.fail:
                raise RuntimeError("fixture stale")
            return SimpleNamespace(text="Hello from the fixture.", provider="fixture", model="fixture")

    monkeypatch.setattr(se, "_ENGINES", {})
    stale, planned, job_client = Model(fail=True), Model(), Model()
    ctx = FakeCtx(llm=stale)
    sa.register(ctx)
    eng = se.engine()
    ctx.llm = planned
    plan = eng.plan({"channel": "cozy_fox", "target": str(tmp_path / "stage"), "use_model": True})
    assert plan["ok"] and stale.calls == 0 and planned.calls == 1
    eng.step_hook = lambda step: setattr(ctx, "llm", job_client) if step == "preview" else None
    try:
        result = eng.apply(plan["digest"], se.owner_consent(plan["digest"], "dashboard"))
        assert result["ok"] and wait_done(eng)["state"] == "done"
        assert stale.calls == 0 and planned.calls == 1 and job_client.calls == 1
    finally:
        eng.stop_preview()
        se.set_model_client(None)


def test_registration_in_another_profile_does_not_rebind_an_existing_engine(monkeypatch, tmp_path):
    monkeypatch.setattr(se, "_ENGINES", {})
    first, second = tmp_path / "first", tmp_path / "second"
    client_a, client_b = object(), object()
    monkeypatch.setenv("HERMES_HOME", str(first))
    sa.register(FakeCtx(llm=client_a))
    a = se.engine()
    monkeypatch.setenv("HERMES_HOME", str(second))
    sa.register(FakeCtx(llm=client_b))
    b = se.engine()
    assert a.model_client is client_a
    assert b.model_client is client_b
    se.set_model_client(None)
