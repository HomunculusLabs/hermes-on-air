"""Selective reply (lane: core). Contract: SPEC.md sections 3 and 5.8.

``pick(pending, now, state)`` decides which already-gated messages a chat turn carries *now*.
It may return ``[]``, and an empty pick means no turn at all. Ranking alone is not selectivity:
this function declines.

- Worthy items (newcomer, real question, direct ask) are ranked and up to
  ``max_worthy_per_turn`` distinct viewers are picked; a picked viewer's other waiting worthy
  message rides along so one answer can cover both. Routine chatter never rides with them.
- Only when nothing worthy is waiting, and only once per ``routine_gap_s`` (about two minutes),
  one routine item may be picked: the newest line of an emote/hype wall in progress (one group
  acknowledgement for the whole wall), otherwise the most substantive chatter line.
- Bait, spam and skips never reach the pool; they are ignored here if passed.

``pick`` is pure: the caller records ``state.last_routine_reply_at`` when it actually starts a
routine turn (see ``is_routine``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..types import ChatMsg, GateVerdict, StageState
from . import shapes
from .gate import current_config, own_names, user_key


@dataclass(frozen=True)
class SelectConfig:
    max_worthy_per_turn: int = 2
    routine_gap_s: float = 120.0
    wall_min: int = 3
    wall_window_s: float = 60.0
    max_age_s: float = 150.0


DEFAULTS = SelectConfig()
_WORTH = {"newcomer": 100.0, "question": 80.0, "direct_ask": 60.0}
_SELF = re.compile(r"\b(?:you|your|you're|youre|yourself|u|ur)\b", re.I)
_REFLECTIVE = re.compile(
    r"\b(?:remember|memory|forget|tomorrow|feel|feels|real|self|choose|think|dream|miss|meaning"
    r"|home|goodnight|favorite|favourite|why|because)\b",
    re.I,
)


def worth_score(msg: ChatMsg, verdict: GateVerdict, now: float, names: tuple[str, ...] = ()) -> float:
    s = _WORTH.get(verdict.rule, 40.0)
    if verdict.rule == "newcomer" and shapes.is_real_question(msg.text, names):
        s += 10
    if shapes.names_agent(msg.text, names) or _SELF.search(msg.text):
        s += 5
    return s - max(0.0, now - msg.ts) / 3.0  # -1 per 3 s waited


def routine_score(msg: ChatMsg, now: float, names: tuple[str, ...] = ()) -> float:
    t = msg.text
    cw = len(shapes.content_words(t, names))
    you = len(_SELF.findall(t))
    return (
        min(15, cw)
        + (10 if you >= 1 and _REFLECTIVE.search(t) else 0)
        + (3 if shapes.names_agent(t, names) else 0)
        - max(0.0, now - msg.ts) / 6.0
    )


def pick(
    pending: list[tuple[ChatMsg, GateVerdict]], now: float, state: StageState, *, config: SelectConfig | None = None
) -> list[ChatMsg]:
    cfg = config or DEFAULTS
    names = own_names(current_config())
    fresh = [
        (m, v)
        for m, v in pending
        if isinstance(m, ChatMsg) and isinstance(v, GateVerdict) and now - m.ts <= cfg.max_age_s
    ]

    worthy = sorted(
        ((m, v) for m, v in fresh if v.kind == "worthy"),
        key=lambda mv: (-worth_score(mv[0], mv[1], now, names), mv[0].ts, mv[0].msg_id),
    )
    picked: list[ChatMsg] = []
    users: set[str] = set()
    for m, _v in worthy:
        if len(picked) >= cfg.max_worthy_per_turn:
            break
        k = user_key(m)
        if k in users:
            continue
        users.add(k)
        picked.append(m)
    for p in list(picked):  # one extra waiting worthy message per picked viewer rides along
        extra = next((m for m, _ in worthy if user_key(m) == user_key(p) and m not in picked), None)
        if extra is not None:
            picked.append(extra)
    if picked:
        return sorted(picked, key=lambda m: m.ts)

    if now - state.last_routine_reply_at < cfg.routine_gap_s:
        return []
    routine = [(m, v) for m, v in fresh if v.kind == "routine"]
    walls = [m for m, v in routine if v.rule == "wall" and now - m.ts <= cfg.wall_window_s]
    if len({user_key(m) for m in walls}) >= cfg.wall_min:
        return [max(walls, key=lambda m: (m.ts, m.msg_id))]
    chatter = [m for m, v in routine if v.rule == "chatter"]
    if not chatter:
        return []
    return [max(chatter, key=lambda m: (routine_score(m, now, names), m.ts, m.msg_id))]


def is_routine(picked: list[ChatMsg], pending: list[tuple[ChatMsg, GateVerdict]]) -> bool:
    """True when the pick is a routine (budgeted) reply rather than worthy chat."""
    kinds = {m.msg_id: v.kind for m, v in pending}
    return bool(picked) and all(kinds.get(m.msg_id) == "routine" for m in picked)


def wall_members(
    anchor: ChatMsg, pending: list[tuple[ChatMsg, GateVerdict]], *, config: SelectConfig | None = None
) -> list[ChatMsg]:
    """The wall lines a group acknowledgement folds together (anchor included)."""
    cfg = config or DEFAULTS
    return [
        m for m, v in pending if v.kind == "routine" and v.rule == "wall" and abs(anchor.ts - m.ts) <= cfg.wall_window_s
    ]
