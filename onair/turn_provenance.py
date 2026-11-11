"""Bind a gateway delivery to the successful Hermes agent turn that produced it.

The gateway copies contextvars into its agent worker. An absent or changed hook contract
leaves this proof incomplete and the adapter refuses the delivery.
"""
from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
import threading
from typing import Any


@dataclass
class TurnProof:
    stage_turn_id: str
    prompt: str
    lock: Any = field(default_factory=threading.Lock)
    identity: tuple[str, str, str] | None = None
    response: str | None = None
    success: bool = False
    end_seen: bool = False
    invalid: bool = False

    def matches(self, stage_turn_id: str, content: str) -> bool:
        with self.lock:
            return (not self.invalid and self.success and self.stage_turn_id == stage_turn_id
                    and isinstance(self.response, str) and bool(self.response.strip())
                    and self.response.strip() == content.strip())


current_proof: ContextVar[TurnProof | None] = ContextVar("onair_turn_proof", default=None)


def _identity(session_id: Any, task_id: Any, turn_id: Any) -> tuple[str, str, str] | None:
    if not all(isinstance(x, str) and x for x in (session_id, task_id, turn_id)):
        return None
    return session_id, task_id, turn_id


def observe_start(*, platform: Any = None, session_id: Any = None, task_id: Any = None,
                  turn_id: Any = None, user_message: Any = None, parent_session_id: Any = None,
                  **_: Any) -> None:
    proof = current_proof.get()
    identity = _identity(session_id, task_id, turn_id)
    if proof is None or platform != "on_air" or parent_session_id or not identity or user_message != proof.prompt:
        return
    with proof.lock:
        if proof.identity is not None and proof.identity != identity:
            proof.invalid = True
        else:
            proof.identity = identity


def observe_output(*, platform: Any = None, session_id: Any = None, task_id: Any = None,
                   turn_id: Any = None, assistant_response: Any = None,
                   conversation_history: Any = None, **_: Any) -> None:
    proof = current_proof.get()
    identity = _identity(session_id, task_id, turn_id)
    if proof is None or platform != "on_air" or not identity:
        return
    with proof.lock:
        if proof.identity == identity:
            # The finalizer can append its own verifier/explainer after writing the
            # model's assistant row. Never certify that appended text as the reply.
            last_assistant = None
            saw_current_user = False
            if isinstance(conversation_history, list):
                for row in reversed(conversation_history):
                    if isinstance(row, dict) and row.get("role") == "user":
                        saw_current_user = True
                        break
                    if last_assistant is None and isinstance(row, dict) and row.get("role") == "assistant":
                        last_assistant = row.get("content")
            if (proof.response is not None or not isinstance(assistant_response, str)
                    or not saw_current_user or not isinstance(last_assistant, str)
                    or assistant_response.strip() != last_assistant.strip()):
                proof.invalid = True
            else:
                proof.response = assistant_response


def observe_end(*, platform: Any = None, session_id: Any = None, task_id: Any = None,
                turn_id: Any = None, completed: Any = None, failed: Any = None,
                interrupted: Any = None, turn_exit_reason: Any = None, **_: Any) -> None:
    proof = current_proof.get()
    identity = _identity(session_id, task_id, turn_id)
    if proof is None or platform != "on_air" or not identity:
        return
    with proof.lock:
        if proof.identity == identity:
            if proof.end_seen:
                proof.invalid = True
                return
            proof.end_seen = True
            proof.success = (completed is True and failed is False and interrupted is False
                             and isinstance(turn_exit_reason, str)
                             and turn_exit_reason.startswith("text_response(")
                             and proof.response is not None)
