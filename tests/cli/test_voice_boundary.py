"""Exercise On Air's session and command boundary against Hermes gateway code.

No model, audio provider, network service, or real Hermes home is used.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("gateway.run_voice")
from gateway.config import PlatformConfig
from gateway.platform_registry import PlatformEntry, platform_registry
from gateway.platforms.event import MessageEvent, MessageType
from gateway.run_voice import GatewayVoiceMixin
from gateway.run_inbound import GatewayInboundMixin

from onair.adapter import OnAirAdapter
from onair.stage import StageController, Turn
from onair.types import ChatMsg


@pytest.fixture
def adapter():
    # Register the real plugin platform, without loading plugins or connecting servers.
    platform_registry.register(PlatformEntry(
        name="on_air", label="On Air", adapter_factory=OnAirAdapter,
        check_fn=lambda: True, required_env=[], source="plugin",
    ))
    return OnAirAdapter(PlatformConfig(enabled=True))


def _event(adapter, chat_id):
    return MessageEvent(
        text="Public test question", message_type=MessageType.TEXT,
        source=adapter.build_source(chat_id=chat_id, chat_type="dm", user_id="viewer"),
    )


def test_fresh_show_does_not_inherit_other_persisted_all_modes(tmp_path, adapter):
    controller = StageController(data_dir=tmp_path / "stage")
    controller.set_mode("approval")
    fresh = controller.session_id
    runner = GatewayVoiceMixin.__new__(GatewayVoiceMixin)
    runner._VOICE_MODE_PATH = tmp_path / "home" / "gateway_voice_mode.json"
    runner._VOICE_MODE_PATH.parent.mkdir()
    persisted = {"on_air:stage-previous-1": "all", "telegram:other": "all"}
    runner._VOICE_MODE_PATH.write_text(json.dumps(persisted), encoding="utf-8")
    runner._voice_mode = runner._load_voice_modes()
    runner._adapter_profile_for_source = lambda source: None
    runner._delivery_adapter_for = lambda source: adapter
    adapter._auto_tts_default = False
    assert runner._voice_mode == persisted
    assert f"on_air:{fresh}" not in persisted
    fresh_event = _event(adapter, fresh)
    old_event = _event(adapter, "stage-previous-1")
    # Positive control proves the real run_voice decision sees persisted 'all'.
    assert runner._should_send_voice_reply(old_event, "A public reply.", []) is True
    assert runner._should_send_voice_reply(fresh_event, "A public reply.", []) is False
    # The On Air adapter also refuses the global auto-TTS fallback.
    adapter._auto_tts_default = True
    assert runner._should_send_voice_reply(fresh_event, "A public reply.", []) is False


def test_boot_ids_are_distinct_across_fresh_processes(tmp_path):
    root = Path(__file__).resolve().parents[2]
    code = (
        "import json; from onair.stage import StageController; "
        "c=StageController(); c.set_mode('approval'); "
        "print(json.dumps([c.boot_id,c.session_id]))"
    )
    env = os.environ.copy()
    env.update(HOME=str(tmp_path), HERMES_HOME=str(tmp_path / "hermes"),
               PYTHONDONTWRITEBYTECODE="1")
    results = [json.loads(subprocess.run(
        [sys.executable, "-c", code], cwd=root, env=env, check=True,
        capture_output=True, text=True, timeout=30,
    ).stdout) for _ in range(3)]
    assert len({boot for boot, _ in results}) == len(results)
    assert len({session for _, session in results}) == len(results)
    assert all(session == f"stage-{boot}-1" for boot, session in results)


def test_rendered_viewer_voice_command_is_not_gateway_command(adapter):
    async def run():
        controller = StageController()
        controller.set_mode("approval")
        msg = ChatMsg("twitch", "show", "m1", "u1", "Viewer", "/voice all", 1.0)
        turn = Turn("t1", 1, "chat", chat=(msg,), names={"m1": "Viewer"},
                    texts={"m1": msg.text}, session=controller.session_id)
        events = []

        async def capture(event):
            events.append(event)
            event._gateway_accepted = True

        # Capture the event at the adapter/gateway boundary; no agent is invoked.
        adapter.handle_message = capture
        await adapter._dispatch_turn(turn)
        assert len(events) == 1
        event = events[0]
        assert '<msg name="Viewer">/voice all</msg>' in event.text
        assert event.source.chat_id == controller.session_id
        assert event.message_type == MessageType.TEXT
        assert event.allow_gateway_control is False
        assert not event.is_command() and event.get_command() is None
        # Even a bare command cannot cross the control-disabled event boundary.
        bare = dataclasses.replace(event, text="/voice all")
        assert bare.get_command() is None
        control = dataclasses.replace(bare, allow_gateway_control=True)
        assert control.get_command() == "voice" and control.get_command_args() == "all"
        # Exercise the actual runner command resolution, including a positive control.
        runner = GatewayInboundMixin.__new__(GatewayInboundMixin)
        runner._check_slash_access = lambda source, canonical_cmd: None
        runner._hm_command_hooks = AsyncMock(return_value=(False, None, None))
        for candidate in (event, bare):
            handled, _, command, canonical = await runner._hm_resolve_command(
                candidate, candidate.source, "test-session")
            assert not handled and command is None and canonical is None
        runner._hm_command_hooks.assert_not_awaited()
        handled, _, command, canonical = await runner._hm_resolve_command(
            control, control.source, "test-session")
        assert not handled and command == canonical == "voice"
        runner._hm_command_hooks.assert_awaited_once()

    asyncio.run(run())
