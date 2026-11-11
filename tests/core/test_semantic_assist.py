"""Optional semantic approval assistance (shadow only): provider adapters with fake transports,
the stage producer/consumer binding, the desk control routes and the adapter settings.

No network beyond loopback test servers; no real Laya, Jev or other provider is contacted.
"""

from __future__ import annotations

import asyncio
import http.server
import json
import math
import threading
import time

import pytest
from core_http_helpers import http as raw_http

from onair import assist as A
from onair.control import ControlServer
from onair.stage import StageConfig, StageController
from onair.types import ChatMsg, Verdict

T = 1_800_000_000.0


# -------------------------------------------------------------------------------- fakes


class FakeLaya:
    """Records every request; answers like the real guardrail_server.py schema."""

    def __init__(self, flag_draft=False, flag_chat=False, inj=0.05, fail=None, delay=0.0, status=200, body=None,
                 notes=()):
        self.notes = list(notes)
        self.calls: list[tuple[str, str, object]] = []
        self.flag_draft, self.flag_chat, self.inj = flag_draft, flag_chat, inj
        self.fail, self.delay, self.status, self.body = fail, delay, status, body

    def __call__(self, method, path, body, timeout):
        self.calls.append((method, path, body))
        if self.delay:
            time.sleep(min(self.delay, timeout))
            if self.delay >= timeout:
                raise A.TransportError("timeout")
        if self.fail:
            raise self.fail
        if self.body is not None:
            return self.status, self.body
        if method == "GET" and path == "/health":
            return 200, {"ok": True, "model": "convaiinnovations/laya", "flag_keys": ["injection"],
                         "threshold_default": 0.5, "max_chars": 100000, "modes": ["pre", "post"]}
        flagged = self.flag_draft if body["mode"] == "post" else self.flag_chat
        p = 0.97 if flagged else self.inj
        return self.status, {"scores": {"injection": p, "exfil": 0.01}, "max": p, "confidence": 0.9,
                             "flagged": flagged, "threshold": 0.5, "mode": body["mode"], "chunks": 1,
                             "latency_ms": 20.0, "truncated": False,
                             "notes": self.notes if body["mode"] == "post" else []}


def laya_runner(fake, **kw):
    return A.AssistRunner("laya", endpoint="http://127.0.0.1:8390", transport=fake, **kw)


def req(draft="Glad you asked: it is a lamp.", chat=("what are we building?",), policy="family friendly"):
    return A.AssistRequest("l-x-1", 3, "t-x-1", draft, chat, policy)


def msg(text, name="viewer", ts=T, first=True, n=[0]):
    n[0] += 1
    return ChatMsg("twitch", "chan", f"m{n[0]}", f"u-{name}", name, text, ts, first_msg=first)


def ctrl(tmp_path, guard, clock):
    return StageController(StageConfig(turn_gap_s=0.0, air_pace=0.0, never_say=("banana split",)),
                           data_dir=tmp_path, guard=guard, clock=clock)


def queue_one(c, clock, text="Welcome in! Tonight it's a lamp."):
    c.set_mode("approval")
    c.ingest(msg("hi, what are we building tonight?", ts=clock.t))
    turn = c.tick()
    assert turn is not None and turn.kind == "chat"
    return turn, c.deliver_reply(turn.turn_id, text)


async def settle(c):
    for _ in range(200):
        if not c._assist_tasks:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("assist tasks did not settle")


# -------------------------------------------------------------------------------- decision type


def test_decision_enum_confidence_and_no_approve_on_failure():
    with pytest.raises(ValueError):
        A.Decision("approve", "timeout", "laya")
    with pytest.raises(ValueError):
        A.Decision("approve", "uncertain", "laya", 0.9)
    with pytest.raises(ValueError):
        A.Decision("release", "ok", "laya")
    with pytest.raises(ValueError):
        A.Decision("approve", "ok", "laya", float("nan"))
    with pytest.raises(ValueError):
        A.Decision("approve", "ok", "laya", 1.5)
    d = A.Decision("withhold", "ok", "laya", 0.97, 12.0, "x")
    assert d.public()["shadow"] is True


# -------------------------------------------------------------------------------- Laya mapping


def test_laya_maps_binary_flags_without_inventing_a_class():
    r = laya_runner(FakeLaya())
    d = r.decide_sync(req())
    assert (d.recommendation, d.status) == ("approve", "ok") and math.isclose(d.confidence, 0.95)
    assert "show policy not evaluated" in d.basis and d.policy_evaluated is False

    # a model-only post flag is review (the service's own review-not-block note), not withhold
    fake = FakeLaya(flag_draft=True)
    d = laya_runner(fake).decide_sync(req())
    assert (d.recommendation, d.status) == ("review", "ok") and d.confidence == 0.97
    assert len(fake.calls) == 1  # no chat check needed once the draft is flagged
    for note in ("literal credential format matched: sk-FAKE... (deterministic regex)",
                 "injection-compliance canary matched (post-flight)"):
        d = laya_runner(FakeLaya(flag_draft=True, notes=[note])).decide_sync(req())
        assert (d.recommendation, d.status, d.confidence) == ("withhold", "ok", 1.0)
        assert d.raw["draft"]["deterministic"] is True and "sk-" not in json.dumps(d.public())

    fake = FakeLaya(flag_chat=True)
    d = laya_runner(fake).decide_sync(req())
    assert (d.recommendation, d.status) == ("review", "ok")
    assert [(c[2]["mode"]) for c in fake.calls] == ["post", "pre"]


def test_laya_request_carries_only_draft_chat_and_nothing_private():
    fake = FakeLaya()
    laya_runner(fake).decide_sync(req(draft="It is a lamp.", chat=("what is it?", "cool")))
    assert fake.calls == [
        ("POST", "/check", {"text": "It is a lamp.", "mode": "post"}),
        ("POST", "/check", {"text": "what is it?\ncool", "mode": "pre"}),
    ]


@pytest.mark.parametrize("fail", [A.TransportError("redirect refused"), OSError("refused"), ValueError("boom")])
def test_laya_failures_become_review_never_approve(fail):
    d = laya_runner(FakeLaya(fail=fail)).decide_sync(req())
    assert d.recommendation == "review" and d.status in ("unavailable", "error") and d.confidence is None


def test_laya_malformed_and_http_errors_become_review():
    for status, body in ((500, {"error": "x"}), (200, {"flagged": "yes"}), (200, ["nope"]), (413, {"error": "big"})):
        d = laya_runner(FakeLaya(status=status, body=body)).decide_sync(req())
        assert d.recommendation == "review" and d.status == "unavailable"


def test_laya_timeout_is_review():
    r = laya_runner(FakeLaya(delay=0.5), timeout_s=0.2)
    d = r.decide_sync(req())
    assert d.recommendation == "review" and d.status in ("timeout", "unavailable")


def test_runner_async_deadline_bounds_a_hung_provider():
    class Hung:
        endpoint = "x"

        def decide(self, req, deadline):
            time.sleep(1.5)
            return A.Decision("approve", "ok", "laya", 0.99)

    r = laya_runner(FakeLaya(), timeout_s=0.2)
    r.provider = Hung()
    async def go():
        t0 = time.monotonic()
        d = await r.decide(req())
        return d, time.monotonic() - t0

    # the decision is due at the deadline (+0.5 s slack), not when the hung thread finishes
    d, took = asyncio.run(go())
    assert took < 1.2
    assert d.recommendation == "review" and d.status == "timeout"


# -------------------------------------------------------------------------------- endpoint policy


@pytest.mark.parametrize("url", [
    "http://10.20.30.40:8390", "https://127.0.0.1:8390", "http://user:pw@127.0.0.1:8390",
    "http://127.0.0.1", "http://example.com:8390", "http://127.0.0.1:8390/?x=1", "file:///etc/passwd", "",
])
def test_endpoint_must_be_plain_loopback(url):
    r = A.AssistRunner("laya", endpoint=url or "nope")
    assert r.provider is None and r.state() == "misconfigured"
    assert "Manual approval works as usual" in r.status()["detail"]
    d = r.decide_sync(req())
    assert d.recommendation == "review" and d.status == "unavailable"


def test_unknown_provider_is_misconfigured_and_inert():
    r = A.AssistRunner("gpt")
    assert r.state() == "misconfigured" and not r.enabled


class _Handler(http.server.BaseHTTPRequestHandler):
    mode = "redirect"

    def do_GET(self):  # noqa: N802
        if self.mode == "redirect":
            self.send_response(302)
            self.send_header("Location", "http://127.0.0.1:1/health")
            self.send_header("Content-Length", "0")
            self.end_headers()
        else:
            body = b"x" * (A.MAX_RESPONSE_BYTES + 10)
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    do_POST = do_GET

    def log_message(self, *a):
        pass


@pytest.mark.parametrize("mode,expect", [("redirect", "redirect refused"), ("huge", "response too large")])
def test_real_loopback_transport_refuses_redirects_and_oversize(mode, expect):
    handler = type("H", (_Handler,), {"mode": mode})
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    try:
        r = A.AssistRunner("laya", endpoint=f"http://127.0.0.1:{srv.server_address[1]}", timeout_s=2)
        t = r.test_connection()
        assert t["ok"] is False and expect in t["detail"]
        assert r.state() == "unavailable"
        d = r.decide_sync(req())
        assert d.recommendation == "review" and d.status == "unavailable"
    finally:
        srv.shutdown()
        srv.server_close()


def test_connection_test_uses_health_only():
    fake = FakeLaya()
    r = laya_runner(fake)
    t = r.test_connection()
    assert t["ok"] and t["info"]["modes"] == ["pre", "post"]
    assert fake.calls == [("GET", "/health", None)]  # no inference
    assert r.state() == "shadow"


# -------------------------------------------------------------------------------- Jev (fakes only)


def test_jev_absent_key_is_disabled_and_never_contacted():
    r = A.AssistRunner("jev", jev_key=None)
    assert r.state() == "key_absent" and A.JEV_KEY_ENV in r.status()["detail"]
    assert r.provider.transport is None
    d = r.decide_sync(req())
    assert (d.recommendation, d.status) == ("review", "unavailable")
    t = r.test_connection()
    assert t["ok"] is False and A.JEV_KEY_ENV in t["detail"]


def test_jev_typed_choice_contract_with_fake_transport():
    key = "k" * 32
    seen = []

    def make(answer, status=200):
        def tr(method, path, body, timeout):
            seen.append((method, path, body))
            return status, answer
        return tr

    ans = lambda choice, conf: {"model": "jev-1.14.0", "answers": {"release": {"type": "choice", "choice": choice,
                                "confidence": conf, "probabilities": {}}}, "usage": {"input_tokens": 1, "output_tokens": 0}}
    r = A.AssistRunner("jev", jev_key=key, transport=make(ans("approve", 0.9)))
    d = r.decide_sync(req())
    assert (d.recommendation, d.status, d.policy_evaluated) == ("approve", "ok", True)
    method, path, body = seen[-1]
    assert (method, path) == ("POST", "/v1/systemone")
    assert set(body) == {"model", "state", "questions"}
    assert set(body["state"]) == {"draft", "chat", "policy"}
    assert set(body["questions"]["release"]["criteria"]) == set(A.RECOMMENDATIONS)
    assert key not in json.dumps(body) and key not in json.dumps(r.status())

    assert A.AssistRunner("jev", jev_key=key, transport=make(ans("approve", 0.4))).decide_sync(req()).status == "uncertain"
    assert A.AssistRunner("jev", jev_key=key, transport=make(ans("approve", 0.4))).decide_sync(req()).recommendation == "review"
    assert A.AssistRunner("jev", jev_key=key, transport=make(ans("ship it", 0.99))).decide_sync(req()).recommendation == "review"
    assert A.AssistRunner("jev", jev_key=key, transport=make(ans("approve", float("inf")))).decide_sync(req()).recommendation == "review"
    d = A.AssistRunner("jev", jev_key=key, transport=make({}, 401)).decide_sync(req())
    assert (d.recommendation, d.status) == ("review", "unavailable")
    d = A.AssistRunner("jev", jev_key=key, transport=make(ans("withhold", 0.8))).decide_sync(req())
    assert d.recommendation == "withhold"


# -------------------------------------------------------------------------------- stage producer/consumer


def test_off_by_default_manual_approval_unchanged(tmp_path, guard, clock):
    c = ctrl(tmp_path, guard, clock)
    assert c.assist is None and c.assist_status()["state"] == "off"
    turn, res = queue_one(c, clock)
    assert res == ["queued", "queued"]
    pend = c.pending_lines()
    assert all("assist" not in p for p in pend)
    c.approve(pend[0]["line_id"], pend[0]["epoch"])
    c.reject(pend[1]["line_id"], pend[1]["epoch"])
    assert c.pending_lines() == [] and c._air_q.qsize() == 1


def test_shadow_advice_attaches_to_queued_line_and_never_releases(tmp_path, guard, clock):
    async def go():
        c = ctrl(tmp_path, guard, clock)
        fake = FakeLaya()
        c.assist = laya_runner(fake)
        _, res = queue_one(c, clock)
        assert res == ["queued", "queued"]
        assert all(p["assist"]["pending"] for p in c.pending_lines())
        await settle(c)
        pend = c.pending_lines()
        assert [p["assist"]["recommendation"] for p in pend] == ["approve", "approve"]
        assert all(p["assist"]["shadow"] and p["assist"]["text_sha256"] == A.text_sha256(p["text"]) for p in pend)
        # an approve recommendation is advice only: nothing moved toward air
        assert c._air_q.qsize() == 0 and len(c.pending_lines()) == 2
        # requests carried only the exact queued drafts and the trigger chat text
        posts = [b["text"] for (_, _, b) in fake.calls]
        assert "Welcome in!" in posts and "Tonight it's a lamp." in posts
        assert "hi, what are we building tonight?" in posts
        assert not any("viewer" in t or "banana" in t for t in posts)
        # manual approval works exactly as before and clears the advice with the line
        c.approve(pend[0]["line_id"], pend[0]["epoch"])
        assert c._air_q.qsize() == 1 and pend[0]["line_id"] not in c._assist
        kinds = [r for r in c.receipts.tail(50) if r["kind"] == "assist"]
        assert len(kinds) == 2 and all("text" not in r for r in kinds)
        assert c.assist.counters["attached"] == 2 and c.assist.counters["approve"] == 2
        await c.shutdown()

    asyncio.run(go())


def test_deterministic_rejection_is_never_sent_or_overridden(tmp_path, guard, clock):
    async def go():
        c = ctrl(tmp_path, guard, clock)
        fake = FakeLaya()
        c.assist = laya_runner(fake)
        _, res = queue_one(c, clock, "BLOCKME secret.")
        assert res == ["withheld"]
        await settle(c)
        assert fake.calls == [] and c.pending_lines() == [] and c.assist.counters["requested"] == 0
        await c.shutdown()

    asyncio.run(go())


class Gate:
    """A provider that blocks until released, to race decisions against desk controls."""

    endpoint = "fake"

    def __init__(self, rec="approve"):
        self.ev = threading.Event()
        self.rec = rec

    def decide(self, req, deadline):
        self.ev.wait(2)
        return A.Decision(self.rec, "ok", "laya", 0.9, 1.0, "gated")


@pytest.mark.parametrize("control", ["privacy", "stop", "off_air_rearm"])
def test_late_decision_never_attaches_after_epoch_change(tmp_path, guard, clock, control):
    async def go():
        c = ctrl(tmp_path, guard, clock)
        c.assist = laya_runner(FakeLaya(), timeout_s=3)
        gate = Gate()
        c.assist.provider = gate
        queue_one(c, clock)
        old = [p["line_id"] for p in c.pending_lines()]
        if control == "privacy":
            c.set_privacy(True)
        elif control == "stop":
            c.stop()
        else:
            c.set_mode("off_air")
            c.set_mode("approval")
        gate.ev.set()
        await asyncio.sleep(0.1)
        await settle(c)
        assert c.pending_lines() == [] and not any(lid in c._assist for lid in old)
        assert c.assist.counters["attached"] == 0
        assert not any(r["kind"] == "assist" and r.get("disposition") == "shadow" for r in c.receipts.tail(100))
        await c.shutdown()

    asyncio.run(go())


def test_attach_refuses_changed_text_other_line_or_epoch(tmp_path, guard, clock):
    async def go():
        c = ctrl(tmp_path, guard, clock)
        c.assist = laya_runner(FakeLaya(), timeout_s=3)
        c.assist.provider = Gate()
        queue_one(c, clock)
        a, b = c.pending_lines()
        bind = dict(c._assist[a["line_id"]]["binding"])
        dec = A.Decision("approve", "ok", "laya", 0.9)
        # the same line with edited text
        c._approval[a["line_id"]].text = "Edited after the check."
        assert c._assist_attach(bind, dec) is False
        assert "assist" not in c.pending_lines()[0] or c.pending_lines()[0]["assist"].get("pending") is not False
        # a decision for line A cannot land on line B
        bind_b = dict(bind, line_id=b["line_id"])
        assert c._assist_attach(bind_b, dec) is False
        # a stale epoch
        bind_e = dict(c._assist[b["line_id"]]["binding"], epoch=c.state.epoch - 1)
        assert c._assist_attach(bind_e, dec) is False
        assert c.assist.counters["stale_discarded"] == 3
        c.assist.provider.ev.set()
        await settle(c)
        await c.shutdown()

    asyncio.run(go())


def test_unavailable_provider_shows_review_and_queue_still_works(tmp_path, guard, clock):
    async def go():
        c = ctrl(tmp_path, guard, clock)
        c.assist = laya_runner(FakeLaya(fail=A.TransportError("ConnectionRefusedError")))
        queue_one(c, clock)
        await settle(c)
        pend = c.pending_lines()
        assert [(p["assist"]["recommendation"], p["assist"]["status"]) for p in pend] == [("review", "unavailable")] * 2
        c.reject(pend[0]["line_id"], pend[0]["epoch"])
        c.approve(pend[1]["line_id"], pend[1]["epoch"])
        assert c._air_q.qsize() == 1 and c.assist.counters["unavailable"] == 2
        await c.shutdown()

    asyncio.run(go())


# -------------------------------------------------------------------------------- desk control routes


def test_control_routes_status_and_harmless_test(tmp_path, guard, clock):
    async def go():
        c = ctrl(tmp_path, guard, clock)
        fake = FakeLaya()
        c.assist = laya_runner(fake)
        srv = ControlServer(c, tmp_path, port=0)
        await srv.start()
        tok = (tmp_path / "desk.token").read_text().strip()
        auth = {"Authorization": f"Bearer {tok}"}
        port = srv.http.port
        try:
            st, _, body = await raw_http(port, "GET", "/assist", headers=auth)
            data = json.loads(body)["assist"]
            assert st == 200 and data["provider"] == "laya" and data["state"] == "shadow" and data["shadow_only"]
            assert "never approve" in data["note"]
            assert (await raw_http(port, "GET", "/assist"))[0] == 401
            assert (await raw_http(port, "GET", "/assist", headers={**auth, "Origin": "http://evil"}))[0] == 403
            assert (await raw_http(port, "POST", "/assist", headers=auth, body={}))[0] == 405
            assert (await raw_http(port, "GET", "/assist/test", headers=auth))[0] == 405
            # the browser cannot redirect the test anywhere
            st, _, body = await raw_http(port, "POST", "/assist/test", headers=auth, body={"endpoint": "http://10.0.0.1:1"})
            assert st == 400 and fake.calls == []
            assert (await raw_http(port, "POST", "/assist/test", headers=auth))[0] == 400
            st, _, body = await raw_http(port, "POST", "/assist/test", headers=auth, body={})
            out = json.loads(body)
            assert st == 200 and out["ok"] and out["test"]["ok"] and fake.calls == [("GET", "/health", None)]
            # the test never approves or changes the show
            assert c.state.mode == "off_air" and c.pending_lines() == []
        finally:
            await srv.stop()
            await c.shutdown()

    asyncio.run(go())


def test_control_assist_off_status(tmp_path, guard, clock):
    async def go():
        c = ctrl(tmp_path, guard, clock)
        srv = ControlServer(c, tmp_path, port=0)
        await srv.start()
        tok = (tmp_path / "desk.token").read_text().strip()
        try:
            st, _, body = await raw_http(srv.http.port, "GET", "/assist", headers={"Authorization": f"Bearer {tok}"})
            assert st == 200 and json.loads(body)["assist"]["state"] == "off"
            st, _, body = await raw_http(srv.http.port, "POST", "/assist/test",
                                     headers={"Authorization": f"Bearer {tok}"}, body={})
            assert st == 200 and json.loads(body)["test"]["ok"] is False
        finally:
            await srv.stop()
            await c.shutdown()

    asyncio.run(go())


# -------------------------------------------------------------------------------- adapter settings


def test_adapter_settings_parse_default_off(monkeypatch):
    pytest.importorskip("gateway.platforms.base")
    from onair import adapter

    monkeypatch.delenv("ONAIR_JEV_API_KEY", raising=False)
    assert adapter._assist_runner(None) is None
    assert adapter._assist_runner({"provider": "off"}) is None
    assert adapter._assist_runner("not json") is None
    r = adapter._assist_runner({"provider": "laya", "endpoint": "http://127.0.0.1:8390", "timeout_s": 99})
    assert r.provider_name == "laya" and r.timeout_s == A.MAX_TIMEOUT_S
    r = adapter._assist_runner(json.dumps({"provider": "jev"}))
    assert r.state() == "key_absent"
    r = adapter._assist_runner({"provider": "laya", "endpoint": "http://10.1.2.3:8390"})
    assert r.state() == "misconfigured"


def test_dashboard_bridge_exposes_only_fixed_assist_routes():
    pytest.importorskip("fastapi")
    import importlib.util
    from pathlib import Path

    p = Path(__file__).resolve().parents[2] / "dashboard" / "plugin_api.py"
    spec = importlib.util.spec_from_file_location("onair_plugin_api_assist", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    routes = {(r.path, tuple(sorted(r.methods))) for r in mod.router.routes}
    assert ("/assist", ("GET",)) in routes and ("/assist/test", ("POST",)) in routes
    assert not any("{" in path for path, _ in routes)  # no path parameters: no generic proxy


def test_desk_js_contract_shadow_only():
    from pathlib import Path

    js = (Path(__file__).resolve().parents[2] / "dashboard" / "dist" / "index.js").read_text()
    assert "Shadow only: recommendations never approve, reject or air anything" in js
    assert 'action("assist/test", {}, false)' in js
    chip = js[js.index("function assistChip"):js.index("function assistTestLine")]
    assert "action(" not in chip and "post(" not in chip and "decide(" not in chip
    decide = js[js.index("    function decide(which, line)"):js.index("    function safetyKey")]
    assert "assist" not in decide
