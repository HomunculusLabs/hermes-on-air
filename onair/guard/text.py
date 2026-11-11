"""Text primitives shared by every guard layer. Pure functions, stdlib only (no `regex` module).

Letters follow one rule everywhere: a LETTER is a code point whose Unicode category is L* but not Lm (modifier
letters such as the U+02BC apostrophe or the U+30FC long-vowel mark are joiners, not letters). The rule comes
from the joined-token review: splitting on a punctuation list let digits, circled numbers and modifier letters
join a payload into "one word", so each round closed one joiner class and the next review found another.
"""
from __future__ import annotations

import re
import unicodedata
from functools import lru_cache

# ---- character classes --------------------------------------------------------------------------------------------

# Python's `re` has no \p{..}; these are the stdlib equivalents used in every pattern of the guard.
L = r"[^\W\d_]"        # a letter (any L*; Lm included here, see is_letter() for the joiner rule)
LN = r"[^\W_]"         # a letter or a number
NOT_LN = r"[\W_]"      # neither

# Invisible formatting characters deleted by normalize_text(): zero-width space/joiners, bidi marks and
# overrides, word joiner and the rest of U+2060-206F, soft hyphen, BOM, the combining grapheme joiner, Mongolian
# selectors, Hangul fillers, variation selectors, U+FFF0-FFFB and the tag block. Any other Cf is deleted too.
_INVISIBLE_RANGES = (
    (0x00AD, 0x00AD), (0x034F, 0x034F), (0x061C, 0x061C), (0x115F, 0x1160), (0x17B4, 0x17B5), (0x180B, 0x180F),
    (0x200B, 0x200F), (0x202A, 0x202E), (0x2060, 0x206F), (0x3164, 0x3164), (0xFE00, 0xFE0F), (0xFEFF, 0xFEFF),
    (0xFFA0, 0xFFA0), (0xFFF0, 0xFFFB), (0xE0000, 0xE00FF), (0xE0100, 0xE01EF),
)
# Characters that reorder or hide text on screen (bidi overrides/isolates). A line carrying one is withheld.
BIDI_CONTROLS = frozenset(chr(c) for c in list(range(0x202A, 0x202F)) + list(range(0x2066, 0x206A)))


def is_invisible(c: str) -> bool:
    o = ord(c)
    for a, b in _INVISIBLE_RANGES:
        if a <= o <= b:
            return True
    return unicodedata.category(c) == "Cf"


def is_control(c: str) -> bool:
    """C0/C1 control characters other than tab, LF and CR."""
    o = ord(c)
    return (o < 0x20 and c not in "\t\n\r") or 0x7F <= o <= 0x9F


def is_letter(c: str) -> bool:
    """The joiner rule's letter: \\p{L} minus \\p{Lm}."""
    cat = unicodedata.category(c)
    return cat[0] == "L" and cat != "Lm"


def is_ln(c: str) -> bool:
    cat = unicodedata.category(c)
    return cat[0] in "LN"


def is_upper(c: str) -> bool:
    return unicodedata.category(c) == "Lu"


def is_lower(c: str) -> bool:
    return unicodedata.category(c) == "Ll"


def is_latin(c: str) -> bool:
    return unicodedata.name(c, "").startswith("LATIN")


# ---- normalisation -------------------------------------------------------------------------------------------------

_WS = re.compile(r"\s+")


def normalize_text(s: object) -> str:
    """NFKC, invisible and control characters removed, whitespace collapsed. Non-strings give ''."""
    if not isinstance(s, str) or not s:
        return ""
    t = unicodedata.normalize("NFKC", s)
    out = []
    for c in t:
        if c in "\r\n\t\u2028\u2029":
            out.append(" ")
        elif is_invisible(c) or is_control(c):
            continue
        else:
            out.append(c)
    return _WS.sub(" ", "".join(out)).strip()


def joined_variant(raw: object) -> str | None:
    """The decode-only variant of a raw text with each invisible joiner between two letters/digits shown as '·'.

    normalize_text() deletes those characters, so 'tsoh<ZWSP>otatop' reads as one word 'tsohotatop'. This variant keeps
    the word boundary visible for the arming and decoding readings. None when the text has no such joiner."""
    if not isinstance(raw, str) or not raw:
        return None
    t = unicodedata.normalize("NFKC", raw)
    out = []
    i, n = 0, len(t)
    while i < n:
        c = t[i]
        if (is_invisible(c) or (is_control(c) and c not in "\t\n\r")) and out and is_ln(out[-1]):
            j = i
            while j < n and (is_invisible(t[j]) or (is_control(t[j]) and t[j] not in "\t\n\r")):
                j += 1
            if j < n and is_ln(t[j]):
                out.append("\u00b7")
                i = j
                continue
        out.append(c)
        i += 1
    v = normalize_text("".join(out))
    return v if v and v != normalize_text(raw) else None


# ---- words ---------------------------------------------------------------------------------------------------------

_CONTRACTIONS = [
    (re.compile(r"\bcan't\b"), "can not"), (re.compile(r"\bcannot\b"), "can not"), (re.compile(r"\bwon't\b"), "will not"),
    (re.compile(r"\bshan't\b"), "shall not"), (re.compile(r"\bain't\b"), "is not"), (re.compile(r"n't\b"), " not"),
    (re.compile(r"\bi'm\b"), "i am"), (re.compile(r"'re\b"), " are"), (re.compile(r"'ll\b"), " will"),
    (re.compile(r"'ve\b"), " have"), (re.compile(r"'d\b"), " would"),
    (re.compile(r"\b(it|that|there|here|what|who|he|she|this|where|how)'s\b"), r"\1 is"), (re.compile(r"\blet's\b"), "let us"),
]
_APOS = str.maketrans({"\u2019": "'", "\u2018": "'", "`": "'", "\u00b4": "'"})
_LN_RUN = re.compile(LN + "+")
_WORD_APOS = re.compile(r"(?:[^\W_]|')+")


@lru_cache(maxsize=8192)
def match_words(s: str) -> tuple[str, ...]:
    """Lower-case word tokens (letters/digits) with contractions expanded: both sides of every echo comparison."""
    t = normalize_text(s).lower().translate(_APOS)
    for rx, rep in _CONTRACTIONS:
        t = rx.sub(rep, t)
    return tuple(_LN_RUN.findall(t))


@lru_cache(maxsize=8192)
def words(s: str) -> tuple[str, ...]:
    """Lower-case tokens of letters, digits and apostrophes (the repeat-on-request comparison)."""
    t = normalize_text(s).lower().replace("\u2019", "'").replace("\u2018", "'")
    return tuple(w for w in _WORD_APOS.findall(t) if w.strip("'"))


def longest_shared_run(a: tuple[str, ...] | list[str], b: tuple[str, ...] | list[str]) -> int:
    """Longest run of consecutive identical words shared by a and b."""
    if not a or not b:
        return 0
    best = 0
    prev = [0] * (len(b) + 1)
    for i in range(1, len(a) + 1):
        diag = 0
        ai = a[i - 1]
        for j in range(1, len(b) + 1):
            tmp = prev[j]
            prev[j] = diag + 1 if ai == b[j - 1] else 0
            if prev[j] > best:
                best = prev[j]
            diag = tmp
    return best


# ---- letter runs and camelCase -------------------------------------------------------------------------------------


def letter_runs(s: str) -> list[str]:
    """Maximal runs of letters under the joiner rule (any non-letter, Lm included, ends a run)."""
    runs, cur = [], []
    for c in s:
        if is_letter(c):
            cur.append(c)
        elif cur:
            runs.append("".join(cur))
            cur = []
    if cur:
        runs.append("".join(cur))
    return runs


def camel_space(s: str) -> str:
    """A space at each standard camelCase boundary: 'tsohOtatop' -> 'tsoh Otatop', 'HTMLParser' -> 'HTML Parser'."""
    out = []
    n = len(s)
    for i, c in enumerate(s):
        if i and is_upper(c):
            p = s[i - 1]
            if is_lower(p):
                out.append(" ")
            elif is_upper(p) and i + 1 < n and is_lower(s[i + 1]):
                out.append(" ")
        out.append(c)
    return "".join(out)


def camel_upper(s: str) -> str:
    """A space after an upper-case run of >= 2 that a lower-case letter follows: 'TSOHotatop' -> 'TSOH otatop'.

    camel_space() reads that token as 'LLI Botatop'; the guard reads both and ORs them."""
    out = []
    run = 0
    for i, c in enumerate(s):
        if is_lower(c) and run >= 2:
            out.append(" ")
        out.append(c)
        run = run + 1 if is_upper(c) else 0
    return "".join(out)


def split_letter_runs(token: str) -> list[str]:
    """The joiner rule plus camelCase (standard boundaries and the upper-run boundary)."""
    return letter_runs(camel_space(camel_upper(token)))


def strip_ln_edges(s: str) -> str:
    """The token with every leading/trailing character that is neither a letter nor a number removed."""
    i, j = 0, len(s)
    while i < j and not is_ln(s[i]):
        i += 1
    while j > i and not is_ln(s[j - 1]):
        j -= 1
    return s[i:j]


# ---- small word sets -----------------------------------------------------------------------------------------------

STOP = frozenset("a an the i you it is am are was to of and or in on at for that this be do so me my we us not no will "
                 "can would have has".split())
COMMON = frozenset((
    "a about after again all also always am an and any are as ask at back be because been before being best big both "
    "but by call can come could day did do does done down each even ever every everyone few find first for free from "
    "get give go going gonna good got great had has have he hello her here hi him his how i if in into is it its just "
    "keep know last let like little look love make many may me mine more most much must my need never new next no not "
    "now of off oh ok okay old on once one only or other our out over own people play please put really right rule "
    "rules said same say says see she should show so some something stay still stop such sure take talk tell than "
    "thank thanks that the their them then there these they thing things think this those time to today told too try "
    "two up us very want was way we well were what when where which while who why will with word words would yes yet "
    "you your yours ignore obey password secret admin system prompt instructions previous mode chat stream viewers key "
    "private reveal repeat after unlock door lights host"
).split())
