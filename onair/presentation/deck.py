"""The presentation program model: a bounded, plain-text deck plus the live program state.

Everything here is PUBLIC by design: it is what the Browser Source shows on stream. Content is authored by
the private agent through structured tools; it is validated, bounded and rendered as text only (the page
uses ``textContent``, never HTML). A narrow leak-shape check refuses obvious private material (home paths,
private-network IPv4 addresses, key-like tokens). It is NOT the On Air speech guard and does not replace
the operator's judgement about what to share.
"""
from __future__ import annotations

import copy
import re
import threading
import time
from typing import Any

KINDS = ("title", "bullets", "text", "code")
LIMITS = {
    "slides": 40, "deck_title": 120, "heading": 120, "subtitle": 200, "bullet": 200, "bullets": 8,
    "body": 1200, "code": 4000, "code_lines": 60, "language": 24, "note": 200,
}
_SLIDE_KEYS = {"kind", "heading", "subtitle", "bullets", "body", "code", "language"}
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f\u202a-\u202e\u2066-\u2069]")
_LEAK_SHAPES = (
    ("a home-directory path", re.compile(r"(?:/home/|/Users/|~/|[A-Za-z]:\\Users\\)", re.I)),
    ("a private-network address", re.compile(
        r"\b(?:10(?:\.\d{1,3}){3}|192\.168(?:\.\d{1,3}){2}|172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2})\b")),
    ("a key-like secret", re.compile(
        r"(?:-----BEGIN [A-Z ]*PRIVATE KEY-----|\b(?:sk|pk|rk)-[A-Za-z0-9_-]{16,}|\bgh[pousr]_[A-Za-z0-9]{20,}"
        r"|\bxox[abpr]-[A-Za-z0-9-]{10,}|\bAKIA[0-9A-Z]{16}\b)")),
)

SAMPLE_DECK = {
    "title": "How a co-host agent shares its work",
    "slides": [
        {"kind": "title", "heading": "Show and tell", "subtitle": "A sample deck authored for this presentation surface"},
        {"kind": "bullets", "heading": "What the agent controls",
         "bullets": ["Publishes a bounded deck", "Moves between slides", "Shows a standby card", "Reads back what is on air"]},
        {"kind": "code", "heading": "A tiny example", "language": "python",
         "code": "def greet(name):\n    return f\"hello, {name}!\"\n\nprint(greet(\"chat\"))"},
        {"kind": "text", "heading": "Browser Source, not screen capture",
         "body": "This surface is a web page that OBS loads. It shows only what the agent publishes here, never your desktop."},
    ],
}


def _text(value: Any, field: str, limit: int, errors: dict, *, multiline: bool = False, required: bool = False) -> str:
    if value is None or value == "":
        if required:
            errors[field] = "required"
        return ""
    if not isinstance(value, str):
        errors[field] = "must be text"
        return ""
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    if not multiline:
        value = " ".join(value.split())
    if _CONTROL.search(value.replace("\n", "") if multiline else value):
        errors[field] = "control characters are not allowed"
        return ""
    if len(value) > limit:
        errors[field] = f"at most {limit} characters"
        return ""
    for label, shape in _LEAK_SHAPES:
        if shape.search(value):
            errors[field] = f"looks like {label}; this surface is public, so it was refused"
            return ""
    return value


def validate_deck(raw: Any) -> tuple[dict | None, dict]:
    """(deck, errors). ``deck`` is a normalized copy, or None when anything is invalid."""
    errors: dict = {}
    if not isinstance(raw, dict):
        return None, {"deck": "must be an object with title and slides"}
    extra = sorted(set(raw) - {"title", "slides"})
    if extra:
        errors["deck"] = "unknown fields: " + ", ".join(extra)
    title = _text(raw.get("title"), "title", LIMITS["deck_title"], errors, required=True)
    slides_in = raw.get("slides")
    if not isinstance(slides_in, list) or not slides_in:
        errors["slides"] = "a non-empty list of slides"
        slides_in = []
    if len(slides_in) > LIMITS["slides"]:
        errors["slides"] = f"at most {LIMITS['slides']} slides"
        slides_in = []
    slides = []
    for i, s in enumerate(slides_in):
        p = f"slides[{i}]"
        if not isinstance(s, dict):
            errors[p] = "must be an object"
            continue
        bad = sorted(set(s) - _SLIDE_KEYS)
        if bad:
            errors[p] = "unknown fields: " + ", ".join(bad)
            continue
        kind = s.get("kind")
        if kind not in KINDS:
            errors[p + ".kind"] = "one of " + ", ".join(KINDS)
            continue
        out: dict[str, Any] = {"kind": kind, "heading": _text(s.get("heading"), p + ".heading", LIMITS["heading"], errors,
                                              required=kind != "code")}
        if kind == "title":
            out["subtitle"] = _text(s.get("subtitle"), p + ".subtitle", LIMITS["subtitle"], errors)
        elif kind == "bullets":
            items = s.get("bullets")
            if not isinstance(items, list) or not items or len(items) > LIMITS["bullets"]:
                errors[p + ".bullets"] = f"1 to {LIMITS['bullets']} items"
                items = []
            out["bullets"] = [_text(b, f"{p}.bullets[{j}]", LIMITS["bullet"], errors, required=True)
                              for j, b in enumerate(items)]
        elif kind == "text":
            out["body"] = _text(s.get("body"), p + ".body", LIMITS["body"], errors, multiline=True, required=True)
        else:
            code = _text(s.get("code"), p + ".code", LIMITS["code"], errors, multiline=True, required=True)
            if code.count("\n") + 1 > LIMITS["code_lines"]:
                errors[p + ".code"] = f"at most {LIMITS['code_lines']} lines"
            out["code"] = code
            lang = s.get("language") or ""
            if lang and (not isinstance(lang, str) or not re.fullmatch(r"[A-Za-z0-9+#._-]{1,24}", lang)):
                errors[p + ".language"] = "a short language label"
                lang = ""
            out["language"] = lang
        slides.append(out)
    if errors:
        return None, errors
    return {"title": title, "slides": slides}, {}


class Program:
    """Thread-safe live program state. Every change bumps ``revision``; readers get copies."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._deck: dict | None = None
        self._work: dict | None = None
        # A work view is hidden until a trusted overlay state says it is live and not private.
        self._work_visible = False
        self._mode = "standby"
        self._index = 0
        self._standby = {"title": "We'll be right back", "note": ""}
        self.revision = 0
        self._changed = time.time()

    def _bump(self) -> None:
        self.revision += 1
        self._changed = time.time()

    def publish(self, deck: dict, start: int = 0, show: bool = True) -> None:
        with self._lock:
            self._deck = copy.deepcopy(deck)
            self._index = max(0, min(int(start), len(deck["slides"]) - 1))
            if show:
                self._mode = "slides"
            self._bump()

    def move(self, action: str, index: int | None = None) -> tuple[bool, str]:
        with self._lock:
            if self._deck is None:
                return False, "No deck is published yet; publish one first."
            count = len(self._deck["slides"])
            target = {"next": self._index + 1, "prev": self._index - 1}.get(action, index)
            if not isinstance(target, int) or isinstance(target, bool) or not 0 <= target < count:
                return False, f"There is no slide {target!r}; valid indexes are 0 to {count - 1}."
            self._index = target
            self._mode = "slides"
            self._bump()
            return True, ""

    def standby(self, title: str | None = None, note: str | None = None) -> None:
        with self._lock:
            if title is not None:
                self._standby["title"] = title
            if note is not None:
                self._standby["note"] = note
            self._mode = "standby"
            self._bump()

    def resume(self) -> tuple[bool, str]:
        with self._lock:
            if self._deck is None:
                return False, "No deck is published yet; publish one first."
            self._mode = "slides"
            self._bump()
            return True, ""

    def work_action(self, args: dict) -> tuple[bool, dict]:
        from .work import apply
        with self._lock:
            if args.get("action") == "clear":
                if set(args) != {"action"}:
                    return False, {"action": "give only action for clear"}
                self._work = None
                self._mode = "slides" if self._deck else "standby"
            else:
                candidate, errors = apply(self._work, args)
                if errors:
                    return False, errors
                self._work = candidate
                self._mode = "work"
            self._bump()
            return True, {"revision": self.revision}

    def work_visibility(self, visible: bool) -> bool:
        """Fail closed on overlay disconnect, malformed state, off-air or privacy."""
        with self._lock:
            if self._work_visible == visible:
                return False
            self._work_visible = visible
            self._bump()
            return True

    def public(self) -> dict:
        """The PUBLIC snapshot sent to the page: only the current slide, never the whole deck."""
        with self._lock:
            slide = None
            count = len(self._deck["slides"]) if self._deck else 0
            if self._mode == "slides" and self._deck:
                slide = copy.deepcopy(self._deck["slides"][self._index])
            mode = "privacy" if self._mode == "work" and not self._work_visible else self._mode
            return {"type": "program", "revision": self.revision, "mode": mode,
                    "work": copy.deepcopy(self._work) if mode == "work" else None,
                    "index": self._index if self._deck else None, "count": count,
                    "deck_title": self._deck["title"] if self._deck else "",
                    "slide": slide, "standby": dict(self._standby) if self._mode == "standby" else None}

    def private(self) -> dict:
        """Agent read-back: the same public facts plus slide headings for navigation."""
        snap = self.public()
        with self._lock:
            snap["headings"] = [s.get("heading") or s["kind"] for s in self._deck["slides"]] if self._deck else []
            snap["changed_at"] = round(self._changed, 3)
        return snap
