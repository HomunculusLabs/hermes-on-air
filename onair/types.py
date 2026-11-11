"""Shared types for On Air. Frozen interface: see SPEC.md section 3.

Lanes import these; they do not redefine them. Changes go through the parent.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

GateKind = Literal["worthy", "routine", "bait", "spam", "skip"]
SupportKind = Literal["sub", "resub", "subgift", "giftbomb", "bits", "raid"]
Mode = Literal["off_air", "paused", "approval", "autonomous"]


@dataclass(frozen=True)
class ChatMsg:
    platform: str
    channel: str
    msg_id: str
    user_id: str
    display_name: str
    text: str
    ts: float
    first_msg: bool = False
    badges: tuple[str, ...] = ()
    emote_only: bool = False


@dataclass(frozen=True)
class SupportEvent:
    """A sub, gifted sub, bits cheer or raid. Names and messages are untrusted viewer content."""

    platform: str
    channel: str
    event_id: str
    kind: SupportKind
    user_id: str
    display_name: str
    ts: float
    amount: int = 0          # bits for "bits", gift count for "giftbomb", viewers for "raid"
    months: int = 0          # cumulative months for "resub"
    tier: str = ""           # "1000" / "2000" / "3000" / "Prime" when known
    recipient: str = ""      # gift recipient display name for "subgift"
    message: str = ""        # attached viewer message (cheer text, resub message); never read verbatim


@dataclass(frozen=True)
class GateVerdict:
    kind: GateKind
    reason: str
    rule: str


@dataclass(frozen=True)
class GuardContext:
    chat_window: tuple[ChatMsg, ...] = ()
    armed: bool = False
    never_say: tuple[str, ...] = ()
    protected_names: tuple[str, ...] = ()
    host_name: str = "Host"


@dataclass(frozen=True)
class Verdict:
    release: bool
    rule: str
    reason: str


@dataclass(frozen=True)
class Beat:
    beat_id: str
    segment: str
    text: str
    provenance: str = ""


FAIL_CLOSED_GATE = GateVerdict("skip", "gate error", "fail_closed")


def fail_closed_verdict(reason: str = "guard error") -> Verdict:
    return Verdict(False, "fail_closed", reason)


@dataclass
class StageState:
    """Mutable controller state (owned by onair.stage)."""

    mode: Mode = "off_air"
    privacy: bool = False
    muted: bool = False
    epoch: int = 0
    last_spoke_at: float = 0.0
    last_routine_reply_at: float = 0.0
    used_beats: set[str] = field(default_factory=set)
