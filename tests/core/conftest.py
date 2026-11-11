"""Shared fixtures for the core lane tests.

HERMES_HOME is forced to a throwaway directory before anything imports Hermes, so no test can
touch a real profile (runtime status, delivery ledger, plugin data).
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ["HERMES_HOME"] = tempfile.mkdtemp(prefix="onair-core-tests-")

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from onair.types import ChatMsg, GuardContext, Verdict  # noqa: E402

T0 = 1_800_000_000.0


class FakeClock:
    def __init__(self, t: float = T0):
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> float:
        self.t += dt
        return self.t


class RecordingGuard:
    """Permissive test guard (the real guard lane is separate): releases every line except ones
    containing ``BLOCKME``, and records every call with its context."""

    def __init__(self):
        self.calls: list[tuple[str, GuardContext]] = []

    def __call__(self, line: str, ctx: GuardContext) -> Verdict:
        self.calls.append((line, ctx))
        if "BLOCKME" in line:
            return Verdict(False, "test_block", "test marker")
        return Verdict(True, "test_release", "permissive test guard")


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def guard() -> RecordingGuard:
    return RecordingGuard()


_n = 0


def chat(
    text: str,
    name: str = "viewer_1",
    *,
    ts: float = T0,
    uid: str | None = None,
    first: bool = False,
    badges: tuple[str, ...] = (),
    emote_only: bool = False,
    mid: str | None = None,
) -> ChatMsg:
    global _n
    _n += 1
    return ChatMsg(
        "twitch",
        "chan",
        mid or f"m{_n}",
        uid or f"id-{name}",
        name,
        text,
        ts,
        first_msg=first,
        badges=badges,
        emote_only=emote_only,
    )


@pytest.fixture
def mk():
    return chat
