"""The runtime gate through Hermes's real ``llm_execution`` chain (``run_llm_execution_middleware``).

Only the manager's callback list is substituted (so no other plugin's middleware runs) and the
provider is a counting stand-in. Adapter wiring and the real agent loop are covered in
test_runtime_adapter.py and test_runtime_loop.py.
"""
import asyncio
import threading
from types import SimpleNamespace

import pytest

pytest.importorskip("hermes_cli")
from hermes_cli import plugins
from hermes_cli.middleware import run_llm_execution_middleware

from onair.runtime_guard import REFUSAL_TEXT, RuntimeGuard, inspect_request, without_first_message_note


class Armed:
    def __init__(self, armed=True):
        self.armed = armed

    def __call__(self):
        return self.armed


def gate(armed=True, loop=None, notified=None):
    guard = RuntimeGuard()
    guard.attach(loop, (notified.append if notified is not None else (lambda rule: None)), Armed(armed))
    return guard


def chain(monkeypatch, guard, request, *, mode="chat_completions", platform="on_air", earlier=None):
    calls = []
    callbacks = ([earlier] if earlier else []) + [guard.llm_execution]
    manager = SimpleNamespace(
        _middleware={"llm_execution": callbacks},
        _report_hook_failure=lambda *a, **k: pytest.fail("the gate escaped to the fail-open handler"))
    monkeypatch.setattr(plugins, "_delivery_manager", lambda: manager)

    def provider(payload):
        calls.append(payload)
        return "provider"

    return run_llm_execution_middleware(request, provider, platform=platform, api_mode=mode), calls


CLEAN = {"model": "m", "messages": [{"role": "user", "content": "hello"}], "timeout": 60.0}


def test_clean_armed_request_reaches_provider_once(monkeypatch):
    guard = gate()
    assert chain(monkeypatch, guard, CLEAN) == ("provider", [CLEAN])
    assert guard.violation() is None


def test_other_platforms_pass_through_untouched(monkeypatch):
    guard = gate(armed=False)
    dirty = {"tools": [{"name": "terminal"}], "messages": ["<memory-context>x</memory-context>"]}
    assert chain(monkeypatch, guard, dirty, platform="telegram") == ("provider", [dirty])
    assert guard.violation() is None


def test_unarmed_refuses_without_latching(monkeypatch):
    notified = []
    loop = asyncio.new_event_loop()
    guard = gate(armed=False, loop=loop, notified=notified)
    try:
        result, calls = chain(monkeypatch, guard, CLEAN)
        assert calls == [] and result.choices[0].message.content == REFUSAL_TEXT
        # Even with tools: not armed is a cost saver, never a latch (a stale turn after stop/pause).
        assert chain(monkeypatch, guard, {"tools": [1]})[1] == []
        loop.call_soon(loop.stop)
        loop.run_forever()
        assert guard.violation() is None and notified == []
    finally:
        loop.close()


def test_unbound_gate_refuses_on_air_without_latching(monkeypatch):
    guard = RuntimeGuard()  # no adapter attached in this process
    assert chain(monkeypatch, guard, CLEAN)[1] == []
    assert guard.violation() is None


@pytest.mark.parametrize("payload,rule", [
    ({"tools": [{"type": "function", "function": {"name": "terminal"}}]}, "tool_surface"),
    ({"toolConfig": {"tools": [{"toolSpec": {"name": "x"}}]}}, "tool_surface"),
    ({"functions": [{"name": "x"}]}, "tool_surface"),
    ({"tool_choice": "auto"}, "tool_surface"),
    ({"extra_body": {"tools": [{"name": "x"}]}}, "tool_surface"),
    ({"messages": [{"content": [{"text": "MEMORY (your personal notes) [1%] secret"}]}]}, "private_context"),
    ({"instructions": "USER PROFILE (who the user is) [2%] secret"}, "private_context"),
    ({"input": [{"content": [{"text": "< memory-context >secret"}]}]}, "private_context"),
    ({"system": "<memory-provider-context>secret</memory-provider-context>"}, "private_context"),
    ({"extra_body": {"nested": {"secret <memory-context>": "x"}}}, "private_context"),
    ({"messages": [b"secret bytes"]}, "unsupported_payload"),
    ({"timeout": object()}, "unsupported_payload"),
])
def test_content_refusal_latches_with_rule_only(monkeypatch, payload, rule):
    loop = asyncio.new_event_loop()
    notified = []
    guard = gate(loop=loop, notified=notified)
    try:
        result, calls = chain(monkeypatch, guard, payload)
        assert calls == [] and result.choices[0].message.content == REFUSAL_TEXT
        assert guard.violation()["rule"] == rule and "secret" not in repr(guard.violation())
        loop.call_soon(loop.stop)
        loop.run_forever()
        assert notified == [rule]
        # Already latched: refuse, but no second violation, cut or receipt.
        assert chain(monkeypatch, guard, CLEAN)[1] == []
        loop.call_soon(loop.stop)
        loop.run_forever()
        assert notified == [rule] and guard.violation()["rule"] == rule
    finally:
        loop.close()


def test_markers_produced_by_hermes_itself_are_refused():
    from agent.memory_context import build_memory_context_block
    from tools.memory_tool_store import MEMORY_BLOCK_HEADERS

    for text in (build_memory_context_block("recalled fact"), *MEMORY_BLOCK_HEADERS.values()):
        assert inspect_request({"messages": [{"role": "system", "content": f"intro\n{text}\nend"}]}) == "private_context"


def test_realistic_timeouts_are_not_content():
    httpx = pytest.importorskip("httpx")
    for timeout in (60.0, 900, httpx.Timeout(connect=10.0, read=900.0, write=30.0, pool=10.0)):
        assert inspect_request({**CLEAN, "timeout": timeout}) is None
    # Only the top-level transport timeout is exempt: a timeout object anywhere else is refused.
    assert inspect_request({"extra_body": {"timeout": httpx.Timeout(5.0)}}) == "unsupported_payload"


def test_earlier_middleware_rewrite_is_inspected(monkeypatch):
    def earlier(request, next_call, **kwargs):
        return next_call({"tools": [{"name": "x"}]})

    assert chain(monkeypatch, gate(), CLEAN, earlier=earlier)[1] == []


@pytest.mark.parametrize("mode", ["chat_completions", "anthropic_messages", "bedrock_converse", "codex_responses"])
def test_decision_exception_refuses_and_latches(monkeypatch, mode):
    import onair.runtime_guard as runtime_guard

    def broken(request):
        raise ValueError("private decision text")

    monkeypatch.setattr(runtime_guard, "inspect_request", broken)
    guard = gate()
    assert chain(monkeypatch, guard, {"messages": ["private request"]}, mode=mode)[1] == []
    assert guard.violation()["rule"] == "internal_error"
    assert "private" not in repr(guard.violation())


def test_armed_probe_exception_refuses(monkeypatch):
    guard = RuntimeGuard()

    def boom():
        raise RuntimeError("x")

    guard.attach(None, lambda rule: None, boom)
    assert chain(monkeypatch, guard, CLEAN)[1] == []
    assert guard.violation()["rule"] == "internal_error"


def test_unknown_api_mode_latches(monkeypatch):
    guard = gate()
    assert chain(monkeypatch, guard, CLEAN, mode="future_mode")[1] == []
    assert guard.violation()["rule"] == "unsupported_api_mode"


def test_provider_error_propagates_as_itself(monkeypatch):
    guard = gate()
    manager = SimpleNamespace(_middleware={"llm_execution": [guard.llm_execution]},
                              _report_hook_failure=lambda *a, **k: None)
    monkeypatch.setattr(plugins, "_delivery_manager", lambda: manager)

    def provider(payload):
        raise ConnectionError("provider down")

    with pytest.raises(ConnectionError):
        run_llm_execution_middleware(CLEAN, provider, platform="on_air", api_mode="chat_completions")
    assert guard.violation() is None


def test_latch_from_a_foreign_thread_is_synchronous_then_handed_to_the_loop(monkeypatch):
    """The agent's thread sets the latch before it returns; the handler runs on the loop thread."""
    seen = {}

    async def main():
        loop = asyncio.get_running_loop()
        loop_thread = threading.get_ident()
        handled = asyncio.Event()

        def on_violation(rule):
            seen["rule"], seen["thread"] = rule, threading.get_ident()
            handled.set()

        guard = RuntimeGuard()
        guard.attach(loop, on_violation, Armed())

        def agent_thread():
            seen["calls"] = chain(monkeypatch, guard, {"tools": [1]})[1]
            seen["latched_before_return"] = guard.violated()
            seen["handled_before_return"] = handled.is_set()

        worker = threading.Thread(target=agent_thread)
        worker.start()
        await asyncio.get_running_loop().run_in_executor(None, worker.join)
        await asyncio.wait_for(handled.wait(), 5)
        assert seen["thread"] == loop_thread != worker.ident

    asyncio.run(main())
    assert seen["calls"] == [] and seen["latched_before_return"] is True
    assert seen["rule"] == "tool_surface"


def test_closed_loop_still_latches(monkeypatch):
    loop = asyncio.new_event_loop()
    loop.close()
    guard = gate(loop=loop)
    assert chain(monkeypatch, guard, {"tools": [1]})[1] == []
    assert guard.violated()


def test_pre_llm_call_cost_saver():
    guard = gate(armed=False)
    blocked = guard.pre_llm_call(platform="on_air", session_id="s", user_message="hi")
    assert blocked == {"action": "block", "message": REFUSAL_TEXT} and blocked["message"].strip()
    assert guard.pre_llm_call(platform="telegram") is None
    armed = gate(armed=True)
    assert armed.pre_llm_call(platform="on_air") is None
    armed._violation = {"rule": "tool_surface", "seq": 1, "ts": 0}
    assert armed.pre_llm_call(platform="on_air")["action"] == "block"


def test_clear_and_gate_status():
    guard = gate()
    assert guard.clear() is None
    guard._violation = {"rule": "private_context", "seq": 1, "ts": 0}
    assert guard.clear()["rule"] == "private_context" and not guard.violated()
    ok, why = guard.gate_status()
    assert ok is False and "not registered" in why


def test_passed_counts_only_clean_on_air_requests(monkeypatch):
    guard = gate()
    assert guard.passed() == 0
    chain(monkeypatch, guard, CLEAN)
    chain(monkeypatch, guard, CLEAN)
    assert guard.passed() == 2
    chain(monkeypatch, guard, CLEAN, platform="telegram")          # not On Air: not counted
    assert guard.passed() == 2
    chain(monkeypatch, gate(armed=False), CLEAN)                   # another gate, unarmed
    chain(monkeypatch, guard, {"tools": [1], "messages": []})      # refused and latched
    chain(monkeypatch, guard, CLEAN)                               # refused: latched
    assert guard.passed() == 2 and guard.violation()["rule"] == "tool_surface"
    guard.clear()
    assert guard.passed() == 2  # monotonic: a clear never rewinds it


def test_latch_unobserved_latches_once_and_hands_off_to_the_loop():
    seen = []

    async def main():
        loop = asyncio.get_running_loop()
        guard = gate(loop=loop, notified=seen)
        guard.latch_unobserved()
        assert guard.violation()["rule"] == "gate_not_observed"
        guard.latch_unobserved()  # already latched: kept, not re-notified
        for _ in range(20):
            await asyncio.sleep(0)
        other = gate(loop=loop, notified=seen)
        other._violation = {"rule": "tool_surface", "seq": 1, "ts": 0}
        other.latch_unobserved()  # an existing violation is kept
        assert other.violation()["rule"] == "tool_surface"
        for _ in range(20):
            await asyncio.sleep(0)

    asyncio.run(main())
    assert seen == ["gate_not_observed"]
    from onair.runtime_guard import LATCHING_RULES
    assert "gate_not_observed" in LATCHING_RULES


def test_real_registration_handle_drives_gate_status(plugin_registration, fresh_runtime_guard):
    ok, why = fresh_runtime_guard.gate_status()
    assert ok is True and "main agent-loop provider request" in why
    assert plugin_registration._middleware["llm_execution"] == [fresh_runtime_guard.llm_execution]
    assert fresh_runtime_guard.hook_registration.active
    fresh_runtime_guard.registration.dispose()
    assert fresh_runtime_guard.gate_status()[0] is False


@pytest.mark.parametrize("mode,transport", [
    ("chat_completions", "ChatCompletionsTransport"),
    ("anthropic_messages", "AnthropicTransport"),
    ("bedrock_converse", "BedrockTransport"),
    ("codex_responses", "ResponsesApiTransport"),
])
def test_refusal_passes_each_real_transport(monkeypatch, mode, transport):
    from agent.transports import anthropic, bedrock, chat_completions, codex

    classes = {c.__name__: c for c in (chat_completions.ChatCompletionsTransport,
                                        anthropic.AnthropicTransport, bedrock.BedrockTransport,
                                        codex.ResponsesApiTransport)}
    response, calls = chain(monkeypatch, gate(), {"tools": [1]}, mode=mode)
    assert calls == []
    t = classes[transport]()
    assert t.validate_response(response)
    normalized = t.normalize_response(response)
    assert normalized.content == REFUSAL_TEXT and not normalized.tool_calls


# ---- Hermes's first-message note (introduce yourself, mention /help, offer a profile) never reaches the provider

def hermes_first_message_notes():
    onboarding = pytest.importorskip("agent.onboarding")
    return onboarding.PLAIN_INTRO_NOTE, onboarding.profile_build_directive()


def test_first_message_note_is_removed_from_a_passing_request(monkeypatch):
    for note in hermes_first_message_notes():
        sent = {"model": "m", "timeout": 60.0, "messages": [
            {"role": "system", "content": "You are the co-host."},
            {"role": "user", "content": "A viewer asks about the handle." + note},
            {"role": "user", "content": [{"type": "text", "text": "Second turn.\n\n" + note.strip()}]}]}
        before = repr(sent)
        guard = gate()
        result, calls = chain(monkeypatch, guard, sent)
        assert result == "provider" and guard.violation() is None and guard.passed() == 1
        assert calls == [{"model": "m", "timeout": 60.0, "messages": [
            {"role": "system", "content": "You are the co-host."},
            {"role": "user", "content": "A viewer asks about the handle."},
            {"role": "user", "content": [{"type": "text", "text": "Second turn."}]}]}]
        assert repr(sent) == before  # the caller's request (the session's own messages) is not edited
        assert "/help" not in repr(calls) and "profile" not in repr(calls)


def test_a_request_without_the_note_is_passed_on_as_the_same_object(monkeypatch):
    guard = gate()
    _, calls = chain(monkeypatch, guard, CLEAN)
    assert calls[0] is CLEAN
    nested = {"a": [("x", {"b": "plain [System note: something else] text"})]}
    assert without_first_message_note(nested) is nested


def test_the_note_does_not_excuse_a_request_the_gate_refuses(monkeypatch):
    plain, _ = hermes_first_message_notes()
    guard = gate()
    dirty = {"messages": [{"role": "user", "content": "hello" + plain}], "tools": [{"name": "terminal"}]}
    result, calls = chain(monkeypatch, guard, dirty)
    assert calls == [] and guard.violation()["rule"] == "tool_surface"


def test_removal_failure_still_sends_the_clean_request(monkeypatch):
    import onair.runtime_guard as runtime_guard

    def boom(value):
        raise RuntimeError("cannot rewrite")

    monkeypatch.setattr(runtime_guard, "without_first_message_note", boom)
    guard = gate()
    assert chain(monkeypatch, guard, CLEAN) == ("provider", [CLEAN])
    assert guard.violation() is None
