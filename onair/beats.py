"""Dead-air content beats from the user's seed bank (lane: core). SPEC.md 5.7.

A seed bank is a YAML (or JSON) file the show's owner writes and approves *off air*: segments in
running order, each with beats, each beat with a provenance note saying where its facts come
from. The agent riffs on a beat in its own words; the beat itself is never read out verbatim, and
every line the agent says about it still passes the output guard.

```yaml
version: 1
segments:
  - name: opening
    beats:
      - id: opening-what-we-build
        text: "Tonight's build: a desk lamp that dims when chat gets quiet."
        provenance: "show notes, rundown item 1"
```

Rules (fail closed): a malformed file loads as an *empty* bank with ``error`` set, never a
partial one. Beat ids are unique slugs, text is 1-420 characters without ``<`` or ``>``, and
every beat needs a provenance. The first pass follows segment order; after exhaustion the
configured policy determines whether eligible evergreen material is revisited.
"""

from __future__ import annotations

import json
import logging
import re
from collections import deque
from dataclasses import replace
from pathlib import Path
from typing import Any

from .types import Beat

logger = logging.getLogger(__name__)

MAX_BEAT_CHARS = 420
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")
_SEGMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _-]{0,47}$")


class SeedBankError(ValueError):
    pass


def _load_yaml(text: str) -> Any:
    try:
        from ruamel.yaml import YAML  # a Hermes core dependency

        return YAML(typ="safe", pure=True).load(text)
    except ImportError:
        pass
    try:
        import yaml  # type: ignore[import-untyped]

        return yaml.safe_load(text)
    except ImportError:
        pass
    try:
        return json.loads(text)  # a JSON seed bank needs no YAML library
    except ValueError:
        raise SeedBankError("a YAML seed bank needs PyYAML or ruamel.yaml (both ship with Hermes)") from None


def _settings(data: dict) -> tuple[str, int, bool]:
    policy = data.get("exhaustion_policy", "loop")
    gap = data.get("min_repeat_gap", 1)
    reset = data.get("reset_on_rearm", True)
    if policy not in ("loop", "stop", "wrap_only"):
        raise SeedBankError("exhaustion_policy must be loop, stop, or wrap_only")
    if type(gap) is not int or not 1 <= gap <= 1000:
        raise SeedBankError("min_repeat_gap must be an integer from 1 to 1000")
    if type(reset) is not bool:
        raise SeedBankError("reset_on_rearm must be a boolean")
    return policy, gap, reset


def parse_bank(data: Any) -> list[tuple[str, list[Beat]]]:
    """Validate a loaded seed bank; raise ``SeedBankError`` on anything unexpected."""
    if not isinstance(data, dict):
        raise SeedBankError("seed bank must be a mapping")
    if data.get("version", 1) != 1:
        raise SeedBankError("unsupported seed bank version")
    _settings(data)
    segs = data.get("segments")
    if not isinstance(segs, list) or not segs:
        raise SeedBankError("seed bank needs a non-empty 'segments' list")
    seen: set[str] = set()
    names: set[str] = set()
    out: list[tuple[str, list[Beat]]] = []
    for seg in segs:
        if not isinstance(seg, dict):
            raise SeedBankError("each segment must be a mapping")
        name = seg.get("name")
        if not isinstance(name, str) or not _SEGMENT_RE.match(name) or name in names:
            raise SeedBankError(f"bad or duplicate segment name: {name!r}")
        names.add(name)
        beats_raw = seg.get("beats")
        if not isinstance(beats_raw, list):
            raise SeedBankError(f"segment {name!r} needs a 'beats' list")
        beats: list[Beat] = []
        for b in beats_raw:
            if not isinstance(b, dict):
                raise SeedBankError(f"segment {name!r}: each beat must be a mapping")
            bid, text, prov = b.get("id"), b.get("text"), b.get("provenance")
            if not isinstance(bid, str) or not _ID_RE.match(bid) or bid in seen:
                raise SeedBankError(f"bad or duplicate beat id: {bid!r}")
            seen.add(bid)
            if not isinstance(text, str) or not text.strip() or len(text) > MAX_BEAT_CHARS or re.search(r"[<>]", text):
                raise SeedBankError(f"beat {bid}: text must be 1-{MAX_BEAT_CHARS} chars without < or >")
            if not isinstance(prov, str) or not prov.strip():
                raise SeedBankError(f"beat {bid}: provenance is required")
            if type(b.get("evergreen", True)) is not bool:
                raise SeedBankError(f"beat {bid}: evergreen must be a boolean")
            beats.append(Beat(beat_id=bid, segment=name, text=" ".join(text.split()), provenance=prov.strip()))
        out.append((name, beats))
    return out


class SeedBank:
    def __init__(self, path: str | Path | None):
        self.path = Path(path).expanduser() if path else None
        self.error: str | None = None
        self._segments: list[tuple[str, list[Beat]]] = []
        self.exhaustion_policy = "loop"
        self.min_repeat_gap = 1
        self.reset_on_rearm = True
        self._evergreen: set[str] = set()
        self._recent: deque[str] = deque(maxlen=1)
        if self.path is None:
            return
        category = "read"
        try:
            text = self.path.read_text(encoding="utf-8")
            category = "parse"
            data = _load_yaml(text)
            category = "validation"
            self._load(data)
        except Exception as exc:  # fail closed: an invalid bank is an empty bank
            mark = getattr(exc, "problem_mark", None)
            line = getattr(mark, "line", None)
            location = f" at line {line + 1}" if type(line) is int and line >= 0 else ""
            self.error = f"Seed bank {category} error{location}."
            self._segments = []
            logger.warning("on-air %s", self.error)
            logger.debug("on-air seed bank details", exc_info=True)

    @classmethod
    def from_data(cls, data: Any) -> SeedBank:
        bank = cls(None)
        try:
            bank._load(data)
        except Exception:
            bank.error = "Seed bank validation error."
            logger.debug("on-air seed bank validation details", exc_info=True)
        return bank

    def _load(self, data: Any) -> None:
        segments = parse_bank(data)
        policy, gap, reset = _settings(data)
        evergreen = {b["id"] for seg in data["segments"] for b in seg["beats"]
                     if b.get("evergreen", True)}
        self._segments = segments
        self.exhaustion_policy, self.min_repeat_gap, self.reset_on_rearm = policy, gap, reset
        self._evergreen = evergreen
        self._recent = deque(maxlen=gap)

    def reset(self) -> None:
        """Clear repeat history when the controller starts a fresh rundown."""
        self._recent.clear()

    def recent(self) -> list[str]:
        return list(self._recent)

    def restore_recent(self, recent: Any) -> None:
        """Restore bounded history from the controller's usage file, ignoring invalid IDs."""
        self._recent.clear()
        if isinstance(recent, list):
            valid = {beat.beat_id for beat in self.beats()}
            self._recent.extend(x for x in recent[-self.min_repeat_gap:] if isinstance(x, str) and x in valid)

    def segments(self) -> list[str]:
        return [name for name, _ in self._segments]

    def beats(self, segment: str | None = None) -> list[Beat]:
        return [b for name, bs in self._segments for b in bs if segment in (None, "", name)]

    def next(self, segment: str, used: set[str]) -> Beat | None:
        """The first unused beat of ``segment`` (any segment, in order, when ``segment`` is empty)."""
        for b in self.beats(segment):
            if b.beat_id not in used:
                return b
        return None

    def _eligible(self) -> list[Beat]:
        if self.exhaustion_policy == "stop":
            return []
        return [b for b in self.beats() if b.beat_id in self._evergreen
                and (self.exhaustion_policy != "wrap_only" or b.segment.lower() == "wrap")]

    def rundown(self, used: set[str]) -> str:
        """Describe whether fresh material or a valid repeat remains."""
        if not self.beats():
            return "exhausted"
        if not used.intersection(b.beat_id for b in self.beats()):
            return "fresh"
        if self.remaining(used):
            return "running"
        if not any(b.beat_id not in self._recent for b in self._eligible()):
            return "exhausted"
        return "looping"

    def warning(self, used: set[str]) -> str | None:
        if self.rundown(used) != "exhausted":
            return None
        if self.exhaustion_policy == "stop" and self.beats() and not self.remaining(used):
            return "No more prepared material; the co-host will be quiet until other input arrives."
        return "No eligible prepared material is available; check the seed bank and repeat gap."

    def next_in_show(self, segment: str | None, used: set[str]) -> Beat | None:
        """Offer the next beat; record offers to enforce the repeat gap across the first pass too."""
        order = self.segments()
        start = order.index(segment) if segment in order else 0
        for name in order[start:] + order[:start]:
            b = self.next(name, used)
            if b is not None:
                self._recent.append(b.beat_id)
                return b
        eligible = self._eligible()
        # Select in rundown order after the most recent eligible beat, rather than
        # repeatedly preferring the first IDs when the pool is larger than the gap.
        ids = [b.beat_id for b in eligible]
        latest = next((x for x in reversed(self._recent) if x in ids), None)
        start = (ids.index(latest) + 1) % len(ids) if latest is not None else 0
        for b in eligible[start:] + eligible[:start]:
            if b.beat_id not in self._recent:
                self._recent.append(b.beat_id)
                return replace(b, text=(b.text + " This topic was already covered earlier; "
                                        "take a fresh angle rather than repeating it."))
        return None

    def remaining(self, used: set[str]) -> int:
        return sum(1 for b in self.beats() if b.beat_id not in used)
