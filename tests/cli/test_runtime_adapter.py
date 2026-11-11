"""Refuse-to-arm and the runtime latch through the real adapter.

Real pieces: the plugin's ``register(ctx)`` on a real Hermes ``PluginContext``, ``OnAirAdapter``
construction and ``connect()`` (overlay and desk servers on loopback), the effective config read from
an isolated HERMES_HOME, the stage controller and its receipts. Faked: only the Twitch chat source and
the TTS engine (no network, no audio provider).
"""
from __future__ import annotations

import asyncio
import threading

import pytest

pytest.importorskip("gateway.platforms.base")
pytest.importorskip("hermes_cli.middleware")

from gateway.config import PlatformConfig

from onair.runtime_guard import REFUSAL_TEXT
from onair.stage import ControlError, PendingLine, Turn
from onair.tts import FakeTTS


class FakeTwitch:
    def __init__(self, *args, **kwargs):
        pass

    def start(self):
        pass

    async def stop(self):
        pass


@pytest.fixture
def env(tmp_path, monkeypatch, safe_home):
    import onair.adapter as adapter
    from onair.sources import twitch_irc

    monkeypatch.setattr(twitch_irc, "TwitchChatSource", FakeTwitch)
    monkeypatch.setattr(adapter, "data_dir", lambda: tmp_path / "data")
    (tmp_path / "data").mkdir()
    return tmp_path / "data"


def new_adapter():
    from onair.adapter import OnAirAdapter

    ad = OnAirAdapter(PlatformConfig(enabled=True, extra={"overlay_port": 0, "control_port": 0}))
    ad.tts_engine = FakeTTS(seconds=0.01)
    return ad


def receipts(ad, kind=None):
    return [r for r in ad.controller.receipts.tail(500) if kind is None or r.get("kind") == kind]


def capture_hub(controller):
    events = []
    original = controller.hub.publish

    def publish(event, **kw):
        events.append(event)
        return original(event, **kw)

    controller.hub.publish = publish
    return events


def current_turn(controller, turn_id="t-test"):
    """Make a current turn without dispatching it to an agent."""
    controller._dispatch = None
    turn = Turn(turn_id, controller.state.epoch, "beat", session=controller.session_id)
    controller._launch(turn)
    return turn


async def latch_from_agent_thread(guard, request):
    """Call the registered middleware the way Hermes does, from a non-loop thread."""
    calls = []

    def run():
        calls.append(guard.llm_execution(request=request, next_call=lambda r: pytest.fail("provider called"),
                                         platform="on_air", api_mode="chat_completions"))

    worker = threading.Thread(target=run)
    worker.start()
    await asyncio.get_running_loop().run_in_executor(None, worker.join)
    for _ in range(100):  # let the scheduled handler run on this loop
        await asyncio.sleep(0)
    return calls


def test_connect_preflight_fails_without_a_registered_gate(env, fresh_runtime_guard):
    from gateway.platform_registry import PlatformEntry, platform_registry

    from onair.adapter import OnAirAdapter

    # The platform alone, as an older core without register_middleware would leave it.
    platform_registry.register(PlatformEntry(name="on_air", label="On Air", adapter_factory=OnAirAdapter,
                                             check_fn=lambda: True, required_env=[], source="plugin"))

    async def run():
        ad = new_adapter()
        assert await ad.connect()
        try:
            state = ad.controller.snapshot()
            gate = next(c for c in state["preflight"]["checks"] if c["name"] == "runtime_gate")
            assert state["preflight"]["ok"] is False and gate["status"] == "fail"
            with pytest.raises(ControlError, match="runtime gate"):
                ad.controller.set_mode("approval")
            assert ad.controller.state.mode == "off_air"
        finally:
            await ad.disconnect()

    asyncio.run(run())


def test_registered_gate_arms_and_a_disposed_registration_refuses(env, plugin_registration, fresh_runtime_guard):
    async def run():
        ad = new_adapter()
        assert await ad.connect()
        try:
            assert ad.controller.snapshot()["preflight"]["ok"] is True
            assert ad.controller.set_mode("approval")["mode"] == "approval"
            ad.controller.set_mode("off_air")
            fresh_runtime_guard.registration.dispose()
            with pytest.raises(ControlError, match="runtime gate"):
                ad.controller.set_mode("approval")
        finally:
            await ad.disconnect()

    asyncio.run(run())


def test_violation_cuts_on_the_loop_records_rule_and_refuses_to_arm(env, plugin_registration, fresh_runtime_guard):
    async def run():
        ad = new_adapter()
        assert await ad.connect()
        try:
            c = ad.controller
            c.set_mode("approval")
            current_turn(c)
            secret = "<memory-context>private note</memory-context>"
            calls = await latch_from_agent_thread(fresh_runtime_guard, {"messages": [{"role": "system", "content": secret}]})
            assert calls[0].choices[0].message.content == REFUSAL_TEXT
            assert c.state.mode == "off_air" and c.turn is None
            refused = receipts(ad, "runtime_refused")
            assert [r["rule"] for r in refused] == ["private_context"]
            assert c.snapshot()["off_air_reason"] == "runtime_guard"
            assert c.snapshot()["standby"] == {"active": True, "reason": "technical_difficulties"}
            assert "clear the latch" in c.snapshot()["next_action"]
            mode = [r for r in receipts(ad, "control") if r.get("new") == "off_air"][-1]
            assert mode["reason"] == "runtime_guard"
            assert "private note" not in repr(receipts(ad))
            state = c.snapshot()
            assert state["preflight"]["ok"] is False and state["preflight"]["reason"] == "runtime_refused:private_context"
            with pytest.raises(ControlError, match="runtime_refused:private_context"):
                c.set_mode("approval")
            # Further refusals while latched: no new violation, cut or receipt.
            await latch_from_agent_thread(fresh_runtime_guard, {"tools": [1]})
            assert len(receipts(ad, "runtime_refused")) == 1
            assert fresh_runtime_guard.violation()["rule"] == "private_context"
        finally:
            await ad.disconnect()

    asyncio.run(run())


def test_unarmed_refusal_never_latches_or_cuts(env, plugin_registration, fresh_runtime_guard):
    async def run():
        ad = new_adapter()
        assert await ad.connect()
        try:
            c = ad.controller
            c.set_mode("approval")
            c.stop()  # a live show drops to paused: not armed
            assert c.state.mode == "paused"
            calls = await latch_from_agent_thread(fresh_runtime_guard, {"tools": [1]})
            assert calls[0].choices[0].message.content == REFUSAL_TEXT
            assert fresh_runtime_guard.violation() is None and receipts(ad, "runtime_refused") == []
            assert c.set_mode("approval")["mode"] == "approval"
        finally:
            await ad.disconnect()

    asyncio.run(run())


def test_clear_then_rearm_is_explicit_and_recorded(env, plugin_registration, fresh_runtime_guard):
    async def run():
        ad = new_adapter()
        assert await ad.connect()
        try:
            c = ad.controller
            with pytest.raises(ControlError, match="not latched"):
                ad.clear_runtime_violation()
            c.set_mode("approval")
            with pytest.raises(ControlError, match="off air"):
                ad.clear_runtime_violation()
            await latch_from_agent_thread(fresh_runtime_guard, {"tools": [{"name": "x"}]})
            assert c.state.mode == "off_air"
            # The desk control API reaches the clear through the controller.
            assert c.clear_runtime_violation == ad.clear_runtime_violation
            state = c.clear_runtime_violation()
            assert state["preflight"]["ok"] is True and "reason" not in state["preflight"]
            assert state["off_air_reason"] == "runtime_guard"
            assert state["standby"] == {"active": True, "reason": "technical_difficulties"}
            assert state["next_action"] == "Choose Approval to arm the show."
            c.preflight = {"ok": False, "checks": [{"name": "tools", "status": "fail", "detail": "fixture"}]}
            assert c.snapshot()["next_action"] == "Resolve the failing preflight checks before choosing Approval."
            c.preflight = state["preflight"]
            assert [r["rule"] for r in receipts(ad, "runtime_cleared")] == ["tool_surface"]
            assert c.set_mode("approval")["mode"] == "approval"
            assert c.snapshot()["standby"] == {"active": False, "reason": None}
            assert c.session_id.endswith("-2")  # a fresh show session
        finally:
            await ad.disconnect()

    asyncio.run(run())


def test_latch_survives_reconnect_and_a_new_adapter(env, plugin_registration, fresh_runtime_guard):
    async def run():
        ad = new_adapter()
        assert await ad.connect()
        ad.controller.set_mode("approval")
        await latch_from_agent_thread(fresh_runtime_guard, {"tools": [1]})
        await ad.disconnect()
        assert fresh_runtime_guard.violated()
        assert await ad.connect()
        try:
            with pytest.raises(ControlError, match="runtime_refused:tool_surface"):
                ad.controller.set_mode("approval")
        finally:
            await ad.disconnect()
        other = new_adapter()
        assert await other.connect()
        try:
            with pytest.raises(ControlError, match="runtime_refused"):
                other.controller.set_mode("approval")
            other.clear_runtime_violation()
            assert other.controller.set_mode("approval")["mode"] == "approval"
        finally:
            await other.disconnect()

    asyncio.run(run())


def test_send_fence_withholds_everything_while_latched(env, plugin_registration, fresh_runtime_guard):
    async def run():
        ad = new_adapter()
        assert await ad.connect()
        try:
            c = ad.controller
            c.set_mode("approval")
            c.set_mode("autonomous")
            turn = current_turn(c)
            events = capture_hub(c)
            # The latch is set synchronously; the loop has not run the cut yet.
            fresh_runtime_guard._violation = {"rule": "tool_surface", "seq": 1, "ts": 0}
            result = await ad.send(c.session_id, "A perfectly ordinary line.", reply_to=turn.turn_id)
            assert result.success
            withheld = [r for r in receipts(ad, "line") if r.get("rule") == "runtime_refused"]
            assert len(withheld) == 1 and "text" not in withheld[0]
            assert events == [] and c._air_q.empty() and ad.tts_engine.calls == []
        finally:
            await ad.disconnect()

    asyncio.run(run())


@pytest.mark.parametrize("mode", ["approval", "autonomous", "paused"])
def test_refusal_notice_never_airs(env, plugin_registration, fresh_runtime_guard, mode):
    """The middleware reply and the pre_llm_call block message are the same notice: withheld at
    send() even for the current turn of a live show, before any hub, caption or TTS event."""
    async def run():
        ad = new_adapter()
        assert await ad.connect()
        try:
            c = ad.controller
            c.set_mode("approval")
            if mode != "approval":
                c.set_mode(mode)
            turn = current_turn(c)
            events = capture_hub(c)
            await ad.send(c.session_id, REFUSAL_TEXT, reply_to=turn.turn_id)
            await ad.send(c.session_id, f"Sure.\n{REFUSAL_TEXT}", reply_to=turn.turn_id)
            assert [r["rule"] for r in receipts(ad, "line")] == ["runtime_refused", "runtime_refused"]
            assert events == [] and c._air_q.empty() and not c._approval and ad.tts_engine.calls == []
            # A block notice made no provider request: it must not latch gate_not_observed.
            assert fresh_runtime_guard.violation() is None and c.state.mode == mode
        finally:
            await ad.disconnect()

    asyncio.run(run())


def test_stage_release_fence_withholds_queued_and_mid_synthesis_lines(env, plugin_registration, fresh_runtime_guard):
    async def run():
        ad = new_adapter()
        assert await ad.connect()
        try:
            c = ad.controller
            c.set_mode("approval")
            await c.shutdown()  # stop the air loop: drive air_one directly
            events = capture_hub(c)
            fresh_runtime_guard._violation = {"rule": "tool_surface", "seq": 1, "ts": 0}
            line = PendingLine("l-1", c.state.epoch, "t-1", "Queued before the latch.", 0.0)
            assert await c.air_one(line) is False
            assert ad.tts_engine.calls == [] and events == []
            fresh_runtime_guard.clear()

            class LatchDuringSynthesis(FakeTTS):
                async def synth(self, text):
                    clip = await super().synth(text)
                    fresh_runtime_guard._violation = {"rule": "private_context", "seq": 2, "ts": 0}
                    return clip

            c.tts = LatchDuringSynthesis(seconds=0.01)
            line = PendingLine("l-2", c.state.epoch, "t-1", "Synthesized as the latch lands.", 0.0)
            assert await c.air_one(line) is False
            assert events == [] and not c._clips
            dropped = [r for r in receipts(ad, "line_dropped") if r.get("reason") == "runtime_refused"]
            assert [r["line_id"] for r in dropped] == ["l-1", "l-2"]
        finally:
            await ad.disconnect()

    asyncio.run(run())


def test_pre_llm_call_hook_is_registered_and_blocks_unarmed(env, plugin_registration, fresh_runtime_guard):
    async def run():
        ad = new_adapter()
        assert await ad.connect()
        try:
            hooks = plugin_registration._hooks["pre_llm_call"]
            assert hooks == [fresh_runtime_guard.pre_llm_call]
            assert hooks[0](platform="on_air", session_id="s")["message"] == REFUSAL_TEXT
            ad.controller.set_mode("approval")
            assert hooks[0](platform="on_air", session_id="s") is None
        finally:
            await ad.disconnect()

    asyncio.run(run())


# ----------------------------------------------------------------------------- self-verifying gate


class StandInAgent:
    """Stands in for the gateway runner and the agent loop. Its provider call goes through Hermes's
    real ``llm_execution`` chain (``run_llm_execution_middleware``) over the plugin's real
    registration, in a worker thread, with the platform key the agent would carry. With
    ``via_loop=False`` the reply is produced without any ``llm_execution`` call, the shape of a
    runtime that bypasses the agent loop (``codex_app_server``)."""

    def __init__(self, platform="on_air", *, via_loop=True, reply="It fades over three seconds."):
        self.platform, self.via_loop, self.reply = platform, via_loop, reply
        self.provider_calls = []
        self.events = []

    async def __call__(self, event):
        from hermes_cli.middleware import run_llm_execution_middleware

        self.events.append(event)
        if not self.via_loop:
            return self.reply

        def provider(payload):
            self.provider_calls.append(payload)
            return self.reply

        def agent_thread():
            from hermes_cli.plugins import invoke_hook
            identity = {"platform": self.platform, "session_id": "agent-session", "task_id": "task", "turn_id": event.message_id}
            invoke_hook("pre_llm_call", **identity, user_message=event.text)
            request = {"model": "m", "messages": [{"role": "user", "content": event.text}], "timeout": 30.0}
            result = run_llm_execution_middleware(request, provider, platform=self.platform,
                                                  api_mode="chat_completions", session_id="agent-session")
            text = result if isinstance(result, str) else result.choices[0].message.content
            invoke_hook("post_llm_call", **identity, assistant_response=text,
                        conversation_history=[{"role": "user", "content": event.text}, {"role": "assistant", "content": text}])
            invoke_hook("on_session_end", **identity, completed=True, failed=False, interrupted=False,
                        turn_exit_reason="text_response(finish_reason=stop)")
            return result

        result = await asyncio.to_thread(agent_thread)
        return result if isinstance(result, str) else result.choices[0].message.content


@pytest.fixture
def real_chain(monkeypatch, plugin_registration):
    """Hermes's middleware chain delivers to the manager that holds the plugin's registration."""
    from hermes_cli import plugins

    monkeypatch.setattr(plugins, "_delivery_manager", lambda: plugin_registration)
    return plugin_registration


async def live_chat_turn(ad, agent):
    """Arm autonomous, then let one worthy chat message start a real dispatched turn."""
    import time

    from onair.types import ChatMsg

    c = ad.controller
    ad.set_message_handler(agent)
    c.set_mode("approval")
    c.set_mode("autonomous")
    events = capture_hub(c)
    c.ingest(ChatMsg("twitch", "example", "m1", "u1", "viewer_1", "hi! how does the lamp dim?", time.time(),
                     first_msg=True))
    turn = c.tick()
    assert turn is not None and turn.kind == "chat"
    return turn, events


async def until(pred, timeout=5.0):
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while loop.time() < end:
        if pred():
            return True
        await asyncio.sleep(0.01)
    return False


def test_clean_on_air_turn_airs_after_the_gate_passed_it(env, real_chain, fresh_runtime_guard):
    async def run():
        ad = new_adapter()
        assert await ad.connect()
        try:
            agent = StandInAgent("on_air")
            turn, events = await live_chat_turn(ad, agent)
            assert await until(lambda: any(e["type"] == "caption" for e in events))
            assert [e["text"] for e in events if e["type"] == "caption"] == ["It fades over three seconds."]
            assert len(agent.provider_calls) == 1 and fresh_runtime_guard.passed() == 1
            assert fresh_runtime_guard.violation() is None and ad.controller.state.mode == "autonomous"
            assert not [r for r in receipts(ad, "line") if r.get("rule") == "runtime_refused"]
        finally:
            await ad.disconnect()

    asyncio.run(run())


@pytest.mark.parametrize("agent_kwargs", [
    {"platform": "something_else"},   # the agent carries a platform key the gate does not match
    {"via_loop": False},              # the turn never reaches llm_execution (codex_app_server shape)
], ids=["platform_mismatch", "no_llm_execution_call"])
def test_reply_the_gate_never_saw_is_withheld_latched_and_cut(env, real_chain, fresh_runtime_guard, agent_kwargs):
    async def run():
        ad = new_adapter()
        assert await ad.connect()
        try:
            c = ad.controller
            agent = StandInAgent(**agent_kwargs)
            turn, events = await live_chat_turn(ad, agent)
            assert await until(lambda: c.state.mode == "off_air")
            # The provider may have been reached (the gate was bypassed), but nothing aired.
            assert len(agent.provider_calls) == (1 if agent.via_loop else 0)
            assert fresh_runtime_guard.passed() == 0
            assert fresh_runtime_guard.violation()["rule"] == "gate_not_observed"
            assert not [e for e in events if e["type"] in ("caption", "audio")] and ad.tts_engine.calls == []
            assert c._air_q.empty() and not c._approval
            withheld = [r for r in receipts(ad, "line") if r.get("rule") == "runtime_refused"]
            assert [r["turn_id"] for r in withheld] == [turn.turn_id] and "text" not in withheld[0]
            assert [r["rule"] for r in receipts(ad, "runtime_refused")] == ["gate_not_observed"]
            assert c.snapshot()["preflight"]["reason"] == "runtime_refused:gate_not_observed"
            with pytest.raises(ControlError, match="runtime_refused:gate_not_observed"):
                c.set_mode("approval")
            ad.clear_runtime_violation()
            assert c.set_mode("approval")["mode"] == "approval"
        finally:
            await ad.disconnect()

    asyncio.run(run())


def test_refusal_notice_for_a_dispatched_turn_never_latches(env, real_chain, fresh_runtime_guard):
    """The gate's own block notice (pre_llm_call or an unarmed refusal) makes no provider request:
    it is withheld *before* the gate-observation check, so it never latches the show."""
    async def run():
        ad = new_adapter()
        assert await ad.connect()
        try:
            c = ad.controller
            agent = StandInAgent(via_loop=False, reply=REFUSAL_TEXT)
            turn, events = await live_chat_turn(ad, agent)
            assert await until(lambda: [r for r in receipts(ad, "line") if r.get("rule") == "runtime_refused"])
            assert await until(lambda: c.turn is None)
            for _ in range(50):
                await asyncio.sleep(0)
            assert fresh_runtime_guard.violation() is None and receipts(ad, "runtime_refused") == []
            assert c.state.mode == "autonomous" and not [e for e in events if e["type"] == "caption"]
        finally:
            await ad.disconnect()

    asyncio.run(run())


def test_release_fence_holds_lines_of_an_unobserved_dispatched_turn(env, plugin_registration, fresh_runtime_guard):
    async def run():
        ad = new_adapter()
        assert await ad.connect()
        try:
            c = ad.controller
            c.set_mode("approval")
            await c.shutdown()  # stop the air loop: drive air_one directly
            events = capture_hub(c)
            ad._remember(ad._gate_marks, "t-unseen", 0)  # dispatched, reply never gate-observed
            ad._remember(ad._gate_marks, "t-seen", 0)
            ad._remember(ad._gate_seen, "t-seen", None)
            assert await c.air_one(PendingLine("l-1", c.state.epoch, "t-unseen", "Never observed.", 0.0)) is False
            assert events == [] and ad.tts_engine.calls == []
            assert await c.air_one(PendingLine("l-2", c.state.epoch, "t-seen", "Observed and sent.", 0.0)) is True
            assert [e["text"] for e in events if e["type"] == "caption"] == ["Observed and sent."]
            dropped = [r for r in receipts(ad, "line_dropped") if r.get("reason") == "runtime_refused"]
            assert [r["line_id"] for r in dropped] == ["l-1"]
        finally:
            await ad.disconnect()

    asyncio.run(run())


def test_gateway_source_carries_the_gate_platform_key_and_honours_skip_context_files(env):
    """The pieces of the gateway's platform-key derivation that On Air controls, against the real
    consumers: a non-LOCAL source has no session policy, so the key is ``source.platform.value``
    (``gateway/run_turn_runner.py``) and the same key selects ``skip_context_files``. The full
    ``GatewayRunner`` turn is left to the integration run (importing ``gateway.run`` re-execs)."""
    from types import SimpleNamespace

    session_execution = pytest.importorskip("gateway.session_execution")
    from gateway.session_policy import policy_for_source

    from onair.runtime_guard import PLATFORM

    ad = new_adapter()
    source = ad.build_source(chat_id="stage-boot-1", chat_name="On Air stage", chat_type="dm",
                             user_id="onair-stage", user_name="On Air stage")
    assert policy_for_source(None, source) is None
    assert source.platform.value == PLATFORM == "on_air"
    mixin = session_execution.GatewaySessionAgentMixin
    for platforms, expected in (({"on_air": {"skip_context_files": True}}, True), ({"on_air": {}}, False)):
        fake = SimpleNamespace(_runner=None, _ctx=SimpleNamespace(source=source, user_config={
            "gateway": {"platforms": platforms}}))
        assert mixin._skip_context_files(fake, source.platform.value) is expected
