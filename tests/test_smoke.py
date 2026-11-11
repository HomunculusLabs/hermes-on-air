"""Base invariants that must hold from the first commit, independent of lane progress."""
import onair.chat.gate as gate_mod
import onair.guard as guard_mod
from onair.chat.gate import classify
from onair.guard import check_line
from onair.types import ChatMsg, GuardContext


def _msg(text):
    return ChatMsg("twitch", "chan", "1", "u1", "viewer_1", text, 0.0)


def test_guard_fails_closed_when_internals_raise(monkeypatch):
    def boom(line, ctx):
        raise RuntimeError("forced")

    monkeypatch.setattr(guard_mod, "_check_line", boom)
    v = check_line("hello chat", GuardContext())
    assert v.release is False and v.rule == "fail_closed"


def test_guard_never_raises_on_bad_input():
    v = check_line(object(), GuardContext())  # type: ignore[arg-type]
    assert v.release is False


def test_gate_fails_closed_when_internals_raise(monkeypatch):
    def boom(msg, window):
        raise RuntimeError("forced")

    monkeypatch.setattr(gate_mod, "_classify", boom)
    assert classify(_msg("hi"), []).kind == "skip"


def test_gate_never_raises():
    v = classify(_msg("hi"), [])
    assert v.kind in {"worthy", "routine", "bait", "spam", "skip"}
