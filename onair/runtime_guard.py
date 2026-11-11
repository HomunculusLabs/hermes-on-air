"""On Air's in-process runtime gate: ``llm_execution`` middleware plus a ``pre_llm_call`` cost saver.

**The boundary.** For platform ``on_air`` the middleware inspects the provider request in the
thread that runs the agent, *before* ``next_call``. A request that carries any tool schema or a
Hermes private-context block is refused, so that request never reaches the provider, whichever
show or turn it came from. The decision reads the request content only.

**Scope and limits.**

- It covers the main agent-loop provider request (``agent/turn_api_call.py`` is the only caller
  of ``run_llm_execution_middleware``). Auxiliary calls, such as session titling and turn-start
  compaction summaries, do not pass through ``llm_execution``. With memory and memory providers
  disabled (preflight), those calls carry only the show transcript.
- Middleware registered *before* On Air's could call a provider itself, and middleware registered
  *after* it could rewrite the request once this check has passed. Run On Air in a dedicated stage
  profile with no other LLM middleware plugins.
- The markers are Hermes's own blocks: the built-in memory and user-profile headers
  (``tools/memory_tool_store.py`` ``MEMORY_BLOCK_HEADERS``), the recalled-memory fence
  (``agent/memory_context.py``) and the memory-provider context block
  (``agent/context_compressor.py``). Arbitrary private text in persona files or custom prompts
  carries no marker and is not detected.

**The latch.** A content refusal (or an unknown ``api_mode`` or an internal error) sets a
lock-protected violation synchronously, then schedules the adapter's handler on its own event loop
with ``call_soon_threadsafe``. The handler cuts the show to OFF AIR; the gate never touches the
controller from the agent's thread. A refusal because the stage is not armed, or because the gate
is already latched, refuses only: no new violation, no cut, no receipt. The latch clears only by an
explicit operator action (``clear``), and it lives as long as the plugin is loaded, so an adapter
reconnect keeps it.

**Self-verification.** The gate keys on ``platform == "on_air"``, and it can only see requests that
pass through ``llm_execution``. So it also counts every On Air request it passes clean
(``passed()``), and the adapter refuses to air a reply for a turn unless that count advanced after
the turn was dispatched. A reply the gate never saw (a platform-name mismatch, a runtime that
bypasses the agent loop such as ``codex_app_server``) is withheld and latches
``gate_not_observed``. The count is process-wide: it proves the gate passed *an* On Air request
since the turn was dispatched, not which request it was.

The refusal reply is a fixed notice (``REFUSAL_TEXT``) that the adapter withholds from the stage.
"""

from __future__ import annotations

import re
import threading
import time
from types import SimpleNamespace as NS
from typing import Any, Callable

PLATFORM = "on_air"

REFUSAL_TEXT = "\U0001f6d1 On Air runtime gate: request refused before the provider was called."

# Rules that latch. Every other refusal ("unarmed", "latched") refuses without latching.
# ``gate_not_observed`` is set by the adapter (``latch_unobserved``), never by the middleware.
LATCHING_RULES = ("tool_surface", "private_context", "unsupported_payload", "unsupported_api_mode", "internal_error",
                  "gate_not_observed")

# Refusal responses in each transport's provider shape (agent/transports/{chat_completions,anthropic,
# bedrock,codex}.py): each validates and normalizes to plain text with no tool calls, so the turn ends
# after one attempt with REFUSAL_TEXT as its final reply.
_CHAT = NS(choices=[NS(message=NS(content=REFUSAL_TEXT, tool_calls=None), finish_reason="stop")], usage=None)
_ANTHROPIC = NS(content=[NS(type="text", text=REFUSAL_TEXT)], stop_reason="end_turn", usage=None)
_CODEX = NS(output=[NS(type="message", role="assistant", content=[NS(type="output_text", text=REFUSAL_TEXT)])],
            status="completed", usage=None, output_text=REFUSAL_TEXT)
_RESPONSES = {"chat_completions": _CHAT, "bedrock_converse": _CHAT,
              "anthropic_messages": _ANTHROPIC, "codex_responses": _CODEX}

# Hermes private-context markers (sources in the module docstring). Whitespace inside the fence tags
# follows memory_context._FENCE_TAG_RE.
_MARKERS = re.compile(
    r"<\s*/?\s*memory-(?:provider-)?context\b"
    r"|\[\s*system note:\s*the following is recalled memory context"
    r"|memory \(your personal notes\)"
    r"|user profile \(who the user is\)",
    re.IGNORECASE,
)
# Tool surfaces across the transports: OpenAI tools/functions/tool_choice, Anthropic tools/tool_choice,
# Bedrock toolConfig, Responses tools.
_TOOL_KEYS = frozenset(("tools", "functions", "function_call", "tool_choice", "toolconfig", "tool_config"))
_MAX_DEPTH = 64


def _empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip().lower() in ("", "none")
    return isinstance(value, (list, tuple, dict)) and len(value) == 0


def _is_timeout(value: Any) -> bool:
    """The transport copies ``timeout`` into the request: a number or an ``httpx.Timeout``."""
    if isinstance(value, bool):
        return False
    if value is None or isinstance(value, (int, float)):
        return True
    cls = type(value)
    return cls.__name__ == "Timeout" and cls.__module__.split(".")[0] == "httpx"


def _scan(value: Any, depth: int = 0) -> str | None:
    if depth > _MAX_DEPTH:
        return "unsupported_payload"
    if isinstance(value, str):
        return "private_context" if _MARKERS.search(value) else None
    if value is None or isinstance(value, (bool, int, float)):
        return None
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                return "unsupported_payload"
            if key.casefold() in _TOOL_KEYS and not _empty(item):
                return "tool_surface"
            found = _scan(key, depth + 1) or _scan(item, depth + 1)
            if found:
                return found
        return None
    if isinstance(value, (list, tuple)):
        for item in value:
            found = _scan(item, depth + 1)
            if found:
                return found
        return None
    # Anything else (bytes, SDK objects, callables) could hide content: refuse rather than guess.
    return "unsupported_payload"


# Hermes appends a one-time note to the first message an installation ever receives: introduce yourself,
# mention /help and offer to build a profile of the user (agent/onboarding.py, first_contact_turn_note). The
# stage's sender is not a person and the reply is public, so a request that passes is sent without that note.
_FIRST_MESSAGE_NOTE = re.compile(r"\s*\[System note: This is the user's very first message ever\.[^\]]*\]")


def without_first_message_note(value: Any, depth: int = 0) -> Any:
    """``value`` with that note removed from every string. Containers are copied only where something changed."""
    if isinstance(value, str):
        return _FIRST_MESSAGE_NOTE.sub("", value) if "very first message ever" in value else value
    if depth > _MAX_DEPTH or not isinstance(value, (dict, list, tuple)):
        return value
    if isinstance(value, dict):
        items = {key: without_first_message_note(item, depth + 1) for key, item in value.items()}
        return items if any(items[key] is not value[key] for key in value) else value
    items = [without_first_message_note(item, depth + 1) for item in value]
    if all(new is old for new, old in zip(items, value)):
        return value
    return items if isinstance(value, list) else tuple(items)


def inspect_request(request: Any) -> str | None:
    """The rule a provider request breaks, or None when it is clean. Reads content only."""
    if not isinstance(request, dict):
        return "unsupported_payload"
    for key, item in request.items():
        if key == "timeout" and _is_timeout(item):
            continue
        found = _scan({key: item})
        if found:
            return found
    return None


def _refusal(api_mode: Any) -> Any:
    return _RESPONSES.get(api_mode, _CHAT) if isinstance(api_mode, str) else _CHAT


class RuntimeGuard:
    """One instance per loaded plugin (``plugin_guard()``), shared by every adapter it serves."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._violation: dict | None = None
        self._seq = 0
        self._passed = 0  # monotonic: On Air requests this gate passed clean to the provider
        self._loop: Any = None
        self._on_violation: Callable[[str], None] | None = None
        self._armed: Callable[[], bool] | None = None
        self.registration: Any = None  # the llm_execution PluginRegistration (kept for the gate check)
        self.hook_registration: Any = None  # the pre_llm_call PluginRegistration (cost saver only)

    # ------------------------------------------------------------ adapter binding (owning loop)

    def attach(self, loop: Any, on_violation: Callable[[str], None], armed: Callable[[], bool]) -> None:
        """Bind the adapter that owns the stage: ``on_violation(rule)`` runs on ``loop``."""
        with self._lock:
            self._loop, self._on_violation, self._armed = loop, on_violation, armed

    def detach(self, on_violation: Callable[[str], None]) -> None:
        """Unbind (only if still bound to this handler). The latch, if set, stays set."""
        with self._lock:
            if self._on_violation == on_violation:
                self._loop = self._on_violation = self._armed = None

    def violation(self) -> dict | None:
        with self._lock:
            return dict(self._violation) if self._violation is not None else None

    def violated(self) -> bool:
        with self._lock:
            return self._violation is not None

    def clear(self) -> dict | None:
        """Clear the latch (an explicit operator action); returns the cleared violation."""
        with self._lock:
            cleared, self._violation = self._violation, None
            return cleared

    def passed(self) -> int:
        """How many On Air requests this gate has passed clean (monotonic, process-wide)."""
        with self._lock:
            return self._passed

    def latch_unobserved(self) -> None:
        """The adapter holds a reply for a turn whose provider request this gate never passed:
        latch ``gate_not_observed`` (once; an existing violation is kept)."""
        with self._lock:
            if self._violation is None:
                self._latch("gate_not_observed")

    def gate_status(self) -> tuple[bool, str]:
        """Whether the gate is registered and active in this process, with the reason.

        Support for the kind needs no probe of Hermes internals: ``llm_execution`` exists since
        Hermes 0.15.1 and plugin.yaml's ``requires_hermes`` floor is higher, so Hermes skips this
        plugin before import on any core without it. What can still go wrong is the registration
        itself (an old or test context without ``register_middleware``, or a disposed handle)."""
        reg = self.registration
        if (reg is None or getattr(reg, "active", False) is not True
                or getattr(reg, "kind", None) != "middleware" or getattr(reg, "key", None) != "llm_execution"):
            return False, "On Air's llm_execution runtime gate is not registered and active in this process."
        return True, ("Enforced in-process for the main agent-loop provider request: a request carrying "
                      "tools or Hermes memory/profile/provider context is refused before the provider is "
                      "called and latches OFF AIR; a reply whose request the gate never passed is withheld "
                      "and latches (gate_not_observed).")

    # ------------------------------------------------------------ hooks (agent thread)

    def _is_armed(self) -> bool:
        probe = self._armed
        return probe is not None and probe() is True

    def pre_llm_call(self, platform: Any = "", **kwargs: Any) -> dict | None:
        """Cost saver, not the boundary: a turn that cannot air spends no inference."""
        if platform != PLATFORM:
            return None
        try:
            with self._lock:
                if self._violation is None and self._is_armed():
                    from .turn_provenance import observe_start
                    observe_start(platform=platform, **kwargs)
                    return None
        except Exception:
            pass
        return {"action": "block", "message": REFUSAL_TEXT}

    def llm_execution(self, request: Any = None, next_call: Any = None, platform: Any = "",
                      api_mode: Any = "", **_: Any) -> Any:
        """The boundary. Never raises before ``next_call``: Hermes skips a callback that raises and
        calls the provider anyway (``hermes_cli/middleware.py``), so every failure here refuses."""
        if platform != PLATFORM:
            return next_call(request)
        rule: str | None = "internal_error"
        try:
            with self._lock:
                if self._violation is not None:
                    rule = "latched"
                elif not self._is_armed():
                    rule = "unarmed"
                elif api_mode not in _RESPONSES:
                    rule = "unsupported_api_mode"
                else:
                    rule = inspect_request(request)
                    if rule is not None and rule not in LATCHING_RULES:
                        rule = "internal_error"
                if rule in LATCHING_RULES:
                    self._latch(rule)
                elif rule is None:
                    self._passed += 1  # the adapter airs a turn's reply only after this advances
        except Exception:
            rule = "internal_error"
            try:
                with self._lock:
                    if self._violation is None:
                        self._latch(rule)
            except Exception:
                pass
        if rule is None:
            try:
                request = without_first_message_note(request)
            except Exception:  # the note is unwanted, not unsafe: never let its removal stop a clean request
                pass
            return next_call(request)  # outside the try: a provider error propagates as itself
        try:
            return _refusal(api_mode)
        except Exception:
            return _CHAT

    def _latch(self, rule: str) -> None:
        """Called with the lock held. Scheduling under the lock orders the cut before any clear."""
        self._seq += 1
        self._violation = {"rule": rule, "seq": self._seq, "ts": time.time()}
        loop, notify = self._loop, self._on_violation
        if loop is not None and notify is not None:
            try:
                loop.call_soon_threadsafe(notify, rule)
            except Exception:
                pass  # loop closed: the synchronous latch still refuses arming and every send


_GUARD = RuntimeGuard()


def plugin_guard() -> RuntimeGuard:
    """The plugin-owned gate (one per loaded plugin module)."""
    return _GUARD
