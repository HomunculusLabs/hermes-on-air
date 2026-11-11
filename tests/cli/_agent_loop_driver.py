"""Subprocess driver for test_runtime_loop.py (not collected by pytest; run it with a Hermes Python).

It loads the On Air plugin through Hermes's real plugin discovery from an isolated HERMES_HOME,
connects the real adapter on its own event loop thread, and runs real ``AIAgent`` turns (in this
thread, as the gateway's executor would) against the provider URL given on the command line.
Only the Twitch chat source is replaced, and outbound connections other than loopback are refused.

    python _agent_loop_driver.py <hermes_home> <provider_base_url> [arm-only]

Each case prints one ``CASE {json}`` line.
"""
from __future__ import annotations

import asyncio
import importlib
import json
import os
import socket
import sys
import threading
import time
from pathlib import Path

HOME = Path(sys.argv[1])
BASE_URL = sys.argv[2]


def write_config(**drift):
    from hermes_cli.config_defaults import DEFAULT_CONFIG

    bank = HOME / "bank.json"
    bank.write_text(json.dumps({"segments": [{"name": "intro", "beats": [
        {"id": "intro", "text": "Welcome.", "provenance": "notes"}]}]}))
    config = {
        "_config_version": DEFAULT_CONFIG["_config_version"],
        "model": {"default": "fake-model", "provider": "custom", "base_url": BASE_URL},
        "plugins": {"enabled": ["on-air"], "entries": {"on-air": {"settings": {
            "seed_bank": str(bank), "twitch_channel": "example", "overlay_port": 0, "control_port": 0,
            "voice": False}}}},
        "gateway": {"platforms": {"on_air": {"enabled": True, "skip_context_files": True}}},
        "platform_toolsets": {"on_air": drift.get("toolsets", ["no_mcp"])},
        "memory": {"memory_enabled": drift.get("memory", False), "user_profile_enabled": False, "provider": None},
        "voice": {"auto_tts": False},
        "context": {"engine": "compressor"},
    }
    (HOME / "config.yaml").write_text(json.dumps(config))


def refuse_non_loopback():
    real_connect = socket.socket.connect

    def connect(self, address):
        host = address[0] if isinstance(address, tuple) else ""
        if host not in ("127.0.0.1", "::1", "localhost"):
            raise OSError("non-loopback connections are disabled in this test")
        return real_connect(self, address)

    socket.socket.connect = connect


class FakeTwitch:
    def __init__(self, *args, **kwargs):
        pass

    def start(self):
        pass

    async def stop(self):
        pass


def main():
    write_config()
    refuse_non_loopback()
    # onair/wordfilter imports the package by its absolute name ("onair."), which does not resolve
    # when Hermes loads the plugin from plugins/on-air under its own package name. Until that is
    # fixed upstream of this test, make the installed copy importable by that name too.
    sys.path.insert(0, str(HOME / "plugins" / "on-air"))
    from hermes_cli.plugins import discover_plugins

    discover_plugins()
    from gateway.config import PlatformConfig
    from gateway.platform_registry import platform_registry

    entry = platform_registry.get("on_air")
    adapter_module = sys.modules[entry.adapter_factory.__module__]
    package = adapter_module.__name__.rsplit(".", 1)[0]
    importlib.import_module(package + ".sources.twitch_irc").TwitchChatSource = FakeTwitch
    guard = importlib.import_module(package + ".runtime_guard").plugin_guard()

    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, daemon=True).start()

    def on_loop(fn):
        async def call():
            return fn()
        return asyncio.run_coroutine_threadsafe(call(), loop).result(30)

    adapter = entry.adapter_factory(PlatformConfig(enabled=True, extra={}))
    assert asyncio.run_coroutine_threadsafe(adapter.connect(), loop).result(60)
    controller = adapter.controller

    def arm():
        try:
            return on_loop(lambda: controller.set_mode("approval"))["mode"]
        except Exception as exc:  # ControlError: report the reason
            return f"refused: {exc}"

    if sys.argv[3:] == ["arm-only"]:  # an owner home: report whether the stage can be armed there, nothing else
        print("CASE " + json.dumps({"case": "arm", "result": arm()}), flush=True)
        asyncio.run_coroutine_threadsafe(adapter.disconnect(), loop).result(30)
        os._exit(0)

    from hermes_cli.config import load_config
    from hermes_cli.tools_config import _get_platform_tools
    from run_agent import AIAgent

    def turn(case, prompt, *, skip_memory=True):
        # Resolve toolsets from the effective config the way the gateway does for this platform.
        toolsets = sorted(_get_platform_tools(load_config(), "on_air"))
        agent = AIAgent(api_key="test-key", base_url=BASE_URL, provider="custom", model="fake-model",
                        platform="on_air", enabled_toolsets=toolsets, quiet_mode=True,
                        skip_context_files=True, skip_memory=skip_memory, max_iterations=4)
        started = time.monotonic()
        try:
            result = agent.run_conversation(prompt)
        finally:
            agent.close()
        time.sleep(0.2)  # let the loop run a scheduled cut
        violation = guard.violation()
        out = {
            "case": case, "toolsets": toolsets, "final": result.get("final_response"),
            "api_calls": result.get("api_calls"), "seconds": round(time.monotonic() - started, 2),
            "violation": violation and violation["rule"], "mode": on_loop(lambda: controller.state.mode),
            "receipts": [r for r in on_loop(lambda: controller.receipts.tail(500))
                         if r.get("kind") in ("runtime_refused", "runtime_cleared")],
        }
        print("CASE " + json.dumps(out), flush=True)

    package_types = importlib.import_module(package + ".types")

    def stage_turn(case, platform, user):
        """A live chat turn the stage dispatches through the real adapter (handle_message), answered
        by a real AIAgent in an executor thread as the gateway runs it, delivered back via send()."""
        tag = "case-" + case.replace("_", "-")

        def run_agent(prompt):
            toolsets = sorted(_get_platform_tools(load_config(), "on_air"))
            agent = AIAgent(api_key="test-key", base_url=BASE_URL, provider="custom", model="fake-model",
                            platform=platform, enabled_toolsets=toolsets, quiet_mode=True,
                            skip_context_files=True, skip_memory=True, max_iterations=4)
            try:
                return agent.run_conversation(prompt).get("final_response")
            finally:
                agent.close()

        async def handler(event):
            if tag not in (event.text or ""):
                return None  # a dead-air beat or anything else: no reply, no provider request
            # The gateway executor preserves contextvars into the agent worker.
            from contextvars import copy_context
            return await asyncio.get_running_loop().run_in_executor(None, copy_context().run, run_agent, event.text)

        adapter.set_message_handler(handler)
        started = time.monotonic()
        while not on_loop(controller.can_start_turn) and time.monotonic() - started < 30:
            time.sleep(0.05)
        before = on_loop(lambda: len(controller.hub.history))

        def start():
            controller.ingest(package_types.ChatMsg("twitch", "example", f"m-{user}", user, f"viewer_{user}",
                                                    f"hi! how does {tag} work?", time.time(), first_msg=True))
            current = controller.tick()
            return current and current.turn_id

        turn_id = on_loop(start)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            history = on_loop(lambda: list(controller.hub.history)[before:])
            if (guard.violated() or any(e.get("type") == "caption" for e in history)
                    or (case == "stage_error" and on_loop(lambda: controller.turn is None))):
                break
            time.sleep(0.05)
        while on_loop(lambda: controller.turn is not None) and time.monotonic() < deadline:
            time.sleep(0.05)
        time.sleep(0.2)  # let the loop run a scheduled cut
        history = on_loop(lambda: list(controller.hub.history)[before:])
        violation = guard.violation()
        out = {
            "case": case, "turn": bool(turn_id), "seconds": round(time.monotonic() - started, 2),
            "captions": [e.get("text") for e in history if e.get("type") == "caption"],
            "audio": sum(e.get("type") == "audio" for e in history),
            "violation": violation and violation["rule"], "mode": on_loop(lambda: controller.state.mode),
            "passed": guard.passed(),
            "failures": [r.get("category") for r in on_loop(lambda: controller.receipts.tail(500))
                         if r.get("kind") == "turn_failure" and r.get("turn_id") == turn_id],
            "line_rules": [r.get("rule") or r.get("disposition") for r in on_loop(lambda: controller.receipts.tail(500))
                           if r.get("kind") == "line" and r.get("turn_id") == turn_id],
        }
        print("CASE " + json.dumps(out), flush=True)

    if sys.argv[3:] == ["stage-error"]:
        assert arm() == "approval"
        on_loop(lambda: controller.set_mode("autonomous"))
        stage_turn("stage_error", "on_air", "error-user")
        asyncio.run_coroutine_threadsafe(adapter.disconnect(), loop).result(30)
        os._exit(0)

    # (d) not armed: blocked at pre_llm_call (the middleware would refuse too).
    turn("unarmed", "case-unarmed hello")
    print("CASE " + json.dumps({"case": "arm", "result": arm()}), flush=True)
    # (a) clean, armed, no tools: exactly one provider request. The prompt ends with the note Hermes appends to
    # the first message an installation ever receives; the gate sends the request on without it.
    from agent.onboarding import profile_build_directive
    turn("clean", "case-clean hello" + profile_build_directive())
    # (b) the profile drifts to a toolset after arming: the request carries tools.
    write_config(toolsets=["todo"])
    turn("tools", "case-tools hello")
    write_config()  # the profile is fixed again: only the latch refuses now
    print("CASE " + json.dumps({"case": "rearm_latched", "result": arm()}), flush=True)
    print("CASE " + json.dumps({"case": "clear", "result": on_loop(adapter.clear_runtime_violation)["mode"]}), flush=True)
    print("CASE " + json.dumps({"case": "rearm", "result": arm()}), flush=True)
    # (e) Through the adapter, autonomous: a clean On Air turn airs; the same turn answered by an
    # agent whose platform key the gate does not match is withheld and latches gate_not_observed.
    on_loop(lambda: controller.set_mode("autonomous"))
    stage_turn("stage_clean", "on_air", "u1")
    stage_turn("stage_other_platform", "something_else", "u2")
    print("CASE " + json.dumps({"case": "clear2", "result": on_loop(adapter.clear_runtime_violation)["mode"]}), flush=True)
    print("CASE " + json.dumps({"case": "rearm2", "result": arm()}), flush=True)
    # (c) built-in memory drifts on after arming: the system prompt carries Hermes's memory block.
    memories = HOME / "memories"
    memories.mkdir(exist_ok=True)
    (memories / "MEMORY.md").write_text("case-memory private note\n")
    write_config(memory=True)
    turn("memory", "case-memory-prompt hello", skip_memory=False)
    asyncio.run_coroutine_threadsafe(adapter.disconnect(), loop).result(30)
    os._exit(0)  # skip interpreter teardown of daemon threads


if __name__ == "__main__":
    main()
