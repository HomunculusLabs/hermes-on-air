"""Small deterministic English word filter. ROT13 is concealment, not security.

``hits`` returns original-string, half-open offsets. ``mask`` replaces profanity
only; callers must decide what to do with slur hits themselves. This is a
best-effort backstop, not a guarantee that text is safe to broadcast.
"""
from __future__ import annotations

import codecs
import re
import unicodedata
from dataclasses import dataclass

from ..guard.lexicon import fold, fold_variants
from .lists import ENTRIES
from .seed import SEED


@dataclass(frozen=True, slots=True)
class Hit:
    tier: str
    start: int
    end: int


def _forms(word: str) -> set[str]:
    # Deliberately finite: do not match arbitrary suffixes inside innocent words.
    forms = {word, word + "s", word + "es", word + "ed", word + "ing",
             word + "er", word + "ers"}
    if word.endswith("e"):
        forms.update({word[:-1] + "ed", word[:-1] + "ing", word + "r", word + "rs"})
    if word.endswith("y"):
        forms.update({word[:-1] + "ies", word[:-1] + "ied"})
    if len(word) >= 3 and word[-1] not in "aeiouwxy" and word[-2] in "aeiou" and word[-3] not in "aeiou":
        forms.update({word + word[-1] + "ed", word + word[-1] + "ing"})
    return forms


_WORDS = [(tier, codecs.decode(encoded, "rot_13")) for tier, encoded in sorted(set(ENTRIES) | set(SEED))]
# Generated suffixes can coincide with common benign English words. Remove
# these forms entirely (including their obfuscated spellings), rather than
# suppressing hits based on neighboring text or weakening root matching.
_EXCLUDED_FORMS = {codecs.decode(encoded, "rot_13") for encoded in (
    "fcvprf", "fcvprq", "fcvpvat", "qvpxre", "qvpxref",
)}
_FORMS = [(tier, form) for tier, word in _WORDS for form in sorted(_forms(word))
          if form not in _EXCLUDED_FORMS]

# Ask the guard for leet interpretations rather than maintaining a rival table.
_READINGS: dict[str, set[str]] = {chr(c): {chr(c)} for c in range(ord("a"), ord("z") + 1)}
for _char in "0123456789@$!|+€£":
    for _reading in fold_variants("a" + _char):
        if len(_reading) == 2 and _reading[1] in _READINGS:
            _READINGS[_reading[1]].add(fold(_char))


def _pattern(form: str, *, inner: bool = False) -> re.Pattern[str]:
    # Bounded gaps and repetitions prevent pathological backtracking and make
    # matching work for common letter-separated obfuscations without crossing lines.
    letters = ["[" + re.escape("".join(sorted(_READINGS[c]))) + "]{1,8}" for c in form]
    body = r"(?:[^\w\r\n]|_){0,3}".join(letters)
    return re.compile(body if inner else r"(?<![^\W_])" + body + r"(?![^\W_])")


_PATTERNS = [(tier, _pattern(form)) for tier, form in _FORMS]
# Inner scans are restricted to roots, not generated suffixes. Weak profanity
# roots have many ordinary-name collisions and remain whole-part matches only.
_STRONG = {codecs.decode(s, 'rot_13') for s in ('shpx', 'fuvg', 'phag', 'juber', 'fyhg')}
_INNER = [(tier, _pattern(root, inner=True)) for tier, root in _WORDS
          if tier == 'slur' or root in _STRONG]
_MASKED = re.compile(r"(?<![^\W_])[a-z0-9*?]+(?![^\W_])")


def _fold_with_offsets(text: str) -> tuple[str, list[int]]:
    out: list[str] = []
    offsets: list[int] = []
    for index, char in enumerate(text):
        # The guard reads these capital-I lookalikes as either i or l.
        # Its leet digit 1 has those same readings in _READINGS.
        value = "1" if char in "IІӀ" else fold(char)
        out.append(value)
        offsets.extend([index] * len(value))
    return "".join(out), offsets


def hits(text: str) -> list[Hit]:
    """Return sorted, deduplicated hits with offsets in the original text."""
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    folded, offsets = _fold_with_offsets(text)
    found: set[Hit] = set()

    def add(tier: str, start: int, end: int) -> None:
        original_end = offsets[end - 1] + 1
        while original_end < len(text) and unicodedata.category(text[original_end]) == "Mn":
            original_end += 1
        found.add(Hit(tier, offsets[start], original_end))

    for tier, pattern in _PATTERNS:
        for match in pattern.finditer(folded):
            add(tier, match.start(), match.end())

    # Wildcards represent exactly one missing character. Do not guess when the
    # visible pattern corresponds to more than one listed term/form.
    for match in _MASKED.finditer(folded):
        token = match.group()
        if not any(c in "*?" for c in token) or sum(c not in "*?" for c in token) < 2:
            continue
        candidates = {(tier, form) for tier, form in _FORMS if len(form) == len(token)
                      and all(c in "*?" or c in _READINGS[expected]
                              for c, expected in zip(token, form))}
        if len(candidates) == 1:
            tier, _ = next(iter(candidates))
            add(tier, match.start(), match.end())
    return sorted(found, key=lambda h: (h.start, h.end, h.tier))


def name_tiers(text: str) -> set[str]:
    """Scan a handle's joined parts; dictionary letter runs remain whole-word.

    This is heuristic: unknown innocent names can collide with roots. It is
    intentionally not used for overlay message masking, which needs offsets.
    """
    from ..guard.decoders import split_joined
    from ..guard.lexicon import dictionary

    tiers = {h.tier for h in hits(text)}
    # split_joined preserves @ handles and trailing digits; split letter runs
    # again so those delimiters cannot conceal a root.
    for part in split_joined(text.lstrip('@')):
        for run in re.findall(r'[^\W\d_]+', part, flags=re.UNICODE):
            tiers.update(h.tier for h in hits(run))
            normalized = fold(run)
            if normalized in dictionary() or normalized == 'scunthorpe':
                continue
            for tier, pattern in _INNER:
                if pattern.search(normalized):
                    tiers.add(tier)
    return tiers


def line_tiers(text: str) -> set[str]:
    """Whole-word scan plus joined-token scan for non-dictionary words."""
    from ..guard.lexicon import dictionary
    tiers = {h.tier for h in hits(text)}
    for token in re.findall(r'\w+', text, flags=re.UNICODE):
        normalized = fold(token)
        if normalized not in dictionary() and normalized != 'scunthorpe':
            tiers.update(name_tiers(token))
    return tiers


def mask(text: str) -> str:
    """Replace each overlapping profanity span with one ``[filtered]``."""
    spans = [(h.start, h.end) for h in hits(text) if h.tier == "profanity"]
    merged: list[list[int]] = []
    for start, end in spans:
        if merged and start < merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    pieces = []
    cursor = 0
    for start, end in merged:
        pieces.extend((text[cursor:start], "[filtered]"))
        cursor = end
    pieces.append(text[cursor:])
    return "".join(pieces)
