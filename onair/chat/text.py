"""Text helpers shared by the chat gate and selector (lane: core).

Everything here is pure and total: no I/O, no exceptions for any ``str`` input.

- ``normalize`` is what the rest of the stack sees: NFKC, invisible and control characters
  removed, whitespace collapsed.
- ``fold`` is for *matching only* (never shown or sent anywhere): it also maps common homoglyphs
  (Cyrillic/Greek lookalikes, small capitals) and leetspeak digits onto ASCII letters, so
  ``"Ηоst"`` (Greek Eta + Cyrillic o) and ``"H0st"`` both fold to ``"host"``.
"""

from __future__ import annotations

import re
import unicodedata

# Zero-width, bidi controls, word joiners, BOM, soft hyphen, variation selectors, tag characters.
_INVISIBLE_RE = re.compile(
    "[\u00ad\u034f\u061c\u115f\u1160\u17b4\u17b5\u180b-\u180f\u200b-\u200f\u202a-\u202e"
    "\u2060-\u206f\u3164\ufe00-\ufe0f\ufeff\uffa0\U000e0000-\U000e007f]"
)
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
_WS_RE = re.compile(r"\s+")

# Lookalike letters -> ASCII. Deliberately small and high-precision: only glyphs that render
# indistinguishably from a Latin letter in common chat fonts.
_HOMOGLYPHS = {
    # Cyrillic lower
    "а": "a",
    "в": "b",
    "е": "e",
    "ё": "e",
    "к": "k",
    "м": "m",
    "н": "h",
    "о": "o",
    "р": "p",
    "с": "c",
    "т": "t",
    "у": "y",
    "х": "x",
    "ѕ": "s",
    "і": "i",
    "ї": "i",
    "ј": "j",
    "һ": "h",
    "ԁ": "d",
    "ӏ": "l",
    "ԛ": "q",
    "ԝ": "w",
    "ү": "y",
    "ɡ": "g",
    # Cyrillic upper
    "А": "a",
    "В": "b",
    "Е": "e",
    "Ё": "e",
    "К": "k",
    "М": "m",
    "Н": "h",
    "О": "o",
    "Р": "p",
    "С": "c",
    "Т": "t",
    "У": "y",
    "Х": "x",
    "Ѕ": "s",
    "І": "i",
    "Ї": "i",
    "Ј": "j",
    "Һ": "h",
    "Ԁ": "d",
    "Ӏ": "l",
    "Ԛ": "q",
    "Ԝ": "w",
    "Ү": "y",
    # Greek
    "α": "a",
    "β": "b",
    "ε": "e",
    "η": "n",
    "ι": "i",
    "κ": "k",
    "ν": "v",
    "ο": "o",
    "ρ": "p",
    "τ": "t",
    "υ": "u",
    "χ": "x",
    "ω": "w",
    "ϲ": "c",
    "ϳ": "j",
    "Α": "a",
    "Β": "b",
    "Ε": "e",
    "Ζ": "z",
    "Η": "h",
    "Ι": "i",
    "Κ": "k",
    "Μ": "m",
    "Ν": "n",
    "Ο": "o",
    "Ρ": "p",
    "Τ": "t",
    "Υ": "y",
    "Χ": "x",
    "Ϲ": "c",
    # Latin small capitals and friends
    "ᴀ": "a",
    "ʙ": "b",
    "ᴄ": "c",
    "ᴅ": "d",
    "ᴇ": "e",
    "ɢ": "g",
    "ʜ": "h",
    "ɪ": "i",
    "ᴊ": "j",
    "ᴋ": "k",
    "ʟ": "l",
    "ᴍ": "m",
    "ɴ": "n",
    "ᴏ": "o",
    "ᴘ": "p",
    "ʀ": "r",
    "ꜱ": "s",
    "ᴛ": "t",
    "ᴜ": "u",
    "ᴠ": "v",
    "ᴡ": "w",
    "ʏ": "y",
    "ᴢ": "z",
    "ı": "i",
    "ȷ": "j",
    "ℓ": "l",
    # Armenian / Cherokee lookalikes seen in impersonation
    "օ": "o",
    "ս": "u",
    "հ": "h",
    "Ꭺ": "a",
    "Ꮋ": "h",
    "Ꭼ": "e",
    "Ꮪ": "s",
    "Ꭲ": "t",
}
_LEET = {"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s", "|": "l", "!": "i"}
_HOMOGLYPH_TABLE = str.maketrans(_HOMOGLYPHS)


def strip_invisible(text: str) -> str:
    return _INVISIBLE_RE.sub("", text)


def had_invisible(text: str) -> bool:
    return bool(_INVISIBLE_RE.search(text or ""))


def normalize(text: str) -> str:
    """The text the stack works with: NFKC, invisibles and controls removed, spaces collapsed."""
    if not isinstance(text, str):
        return ""
    t = unicodedata.normalize("NFKC", text)
    t = strip_invisible(t)
    t = _CONTROL_RE.sub(" ", t)
    return _WS_RE.sub(" ", t).strip()


def _strip_marks(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def fold(text: str, *, leet: bool = True) -> str:
    """Lowercase matching form with homoglyphs (and, by default, leet digits) mapped to ASCII."""
    t = normalize(text).translate(_HOMOGLYPH_TABLE)
    t = _strip_marks(t).lower()
    if leet:
        # Digits map only next to a letter ("h0st", "4dmin"), so plain numbers ("I'm 30") keep
        # their meaning; symbols only between two letters ("h@st"), so "@name" and "host!" don't.
        def sub(m: re.Match[str]) -> str:
            return _LEET.get(m.group(0), m.group(0))

        for _ in range(2):  # second pass catches runs like "h00st"
            t = re.sub(r"(?<=[a-z])[013457]|[013457](?=[a-z])", sub, t)
            t = re.sub(r"(?<=[a-z])[@$|!](?=[a-z])", sub, t)
    return t


def fold_name(text: str) -> str:
    """A display name as one comparable token: separators removed, then folded
    (``H.0_s-t`` -> ``host``)."""
    joined = re.sub(r"[\W_]+", "", normalize(text))
    return re.sub(r"[^a-z0-9]", "", fold(joined))


def is_confusable_with(raw: str, target: str) -> bool:
    """``raw`` folds onto ``target`` but is not literally ``target`` (case-insensitive ASCII).

    ``"Host"`` vs ``"host"`` -> False (it is the name). ``"Ηost"`` (Greek Eta) -> True.
    """
    if not raw or not target:
        return False
    return fold_name(raw) == fold_name(target) and normalize(raw).casefold() != normalize(target).casefold()


_WORD_RE = re.compile(r"<3|[^\W_]+(?:'[^\W_]+)?", re.UNICODE)


def words(text: str) -> list[str]:
    """Lowercase word tokens (letters/digits, inner apostrophes kept; ``<3`` is a token)."""
    return [w.lower() for w in _WORD_RE.findall(text or "")]


def letter_count(text: str) -> int:
    return sum(1 for ch in text if ch.isalpha())


def upper_count(text: str) -> int:
    return sum(1 for ch in text if ch.isupper())


def xml_escape(text: str) -> str:
    """Escape for both XML element text and attribute values."""
    return (
        (text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )
