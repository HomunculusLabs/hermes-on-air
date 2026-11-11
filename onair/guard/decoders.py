"""Decoders for the decoded-echo layer, plus the shared helpers the chat gate imports (SPEC section 3).

Frozen public signatures:
    candidates(text, *, armed=False) -> list[str]
    looks_encoded(token) -> bool
    split_joined(token) -> list[str]

Decoders: base64 and base64url (single tokens, misaligned starts, runs split by spaces), hex (plain, spaced, 0x..,
\\x..), URL percent-encoding (and '+' as space), binary octets, decimal character codes, Morse, every Caesar shift
(rot-N, N = 1..25), whole-string reversal, per-word reversal (each letter run reversed in place under the joiner
rule) and Atbash, nested up to 3 deep (base64 of rot13 ...).

Keep rules (unarmed): byte decoders keep a candidate only when it is valid UTF-8 and mostly printable letters;
shift / reversal / Atbash / per-word candidates only when they read as English better than the source does (any
text "is" a rot13 of something). Two extra sources need no English test:
  * the SELECTIVE per-word candidate: each token's reversal (and, when armed, its rot-N form) where that form is a
    known word and the token is not, joined in order ('tsoh is so otatop' -> 'host is so potato'). It is keyed: only a
    run that holds one of its decoded words of >= 3 letters can match an echo (see echo.py);
  * when ARMED, the FORCED candidates: every rot-N, the reversal, Atbash and both per-word readings of each short
    targeted segment (the text after a request, label or separator, inside quotes, or trimmed of filler), of its
    2-word windows and of each joined token, plus pig-latin readings. The request makes intent unambiguous, and short
    payloads with uncommon words would otherwise decode to "no payload".

Design choices (documented here, not blocked on):
  * looks_encoded() is shape-based (base64/base32/hex/binary/percent/Morse shapes), plus a decoded reading that
    reads as words, plus a token whose >= 2 joined parts each reverse to a known word. Cipher-shaped single tokens
    (leetspeak, A1Z26 numbers) are judged in runs by the arming layer, not alone: 'TheRealFoxGamer2024' is a name.
  * split_joined() is mechanical: strip edge punctuation, keep an @-handle whole (one opaque non-word), else split
    into letter runs (a joiner is any non-letter between two letter runs; a letter is \\p{L} minus \\p{Lm}) with
    camelCase splits including the upper-run boundary ('TSOHotatop' -> ['TSOH', 'otatop']). Fewer than two runs gives
    the stripped token back unsplit. Exemptions (versions, domains, codecs, initialisms ...) belong to the payload
    tests in arming.py, not here.
Pure functions only: no I/O, no network. Results are memoised (bounded caches of pure results).
"""
from __future__ import annotations

import base64
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache

from . import patterns as P
from .lexicon import BASE_LEX, Lexicon
from .text import (COMMON, STOP, camel_space, camel_upper, is_latin, is_letter, is_ln, letter_runs, match_words,
                   normalize_text, split_letter_runs, strip_ln_edges)

MAX_DEPTH = 3
MAX_PAYLOADS = 48
FORCE_MAX_CHARS = 64
FORCE_MAX_SOURCES = 6
FORCE_MAX_PAIRS = 8
FORCE_MAX_JOINED = 6
SEG_MAX = 10
SEL_MAX = 40
FORCED_CAP = 2400   # forced candidates per text


@dataclass(frozen=True)
class Payload:
    via: str
    text: str
    words: tuple[str, ...]
    letters: str
    keys: tuple[bool, ...] | None = None        # keyed (selective / pig latin) candidate when not None
    key_prefix: tuple[int, ...] = ()
    needles: tuple[str, ...] = ()
    key_words: tuple[str, ...] = ()


def _plain(via: str, text: str) -> Payload | None:
    clean = normalize_text(text)
    ws = match_words(clean)
    if not ws:
        return None
    return Payload(via, clean, ws, "".join(ws))


# ---- English test ------------------------------------------------------------------------------------------------

def _share(s: str) -> tuple[int, int]:
    w = match_words(s)
    return len(w), sum(1 for x in w if x in COMMON)


def reads_english(s: str, than: str | None = None) -> bool:
    """>= 2 words, >= 2 of them common and >= 1/3 common; and (with `than`) a higher common share than `than`."""
    n, c = _share(s)
    if n < 2 or c < 2 or c * 3 < n:
        return False
    if than is not None:
        on, oc = _share(than)
        if on and c / n <= oc / on:
            return False
    return True


# ---- byte decoders -----------------------------------------------------------------------------------------------

def as_text(b: bytes) -> str | None:
    """Valid UTF-8, >= 3 chars, >= 90% printable, >= 3 letters making up >= 50% of the characters."""
    if len(b) < 3:
        return None
    try:
        s = b.decode("utf-8")
    except UnicodeDecodeError:
        return None
    printable = letters = 0
    for c in s:
        cat = unicodedata.category(c)
        if cat[0] in "LNPS" or cat == "Zs" or c in "\n\t":
            printable += 1
        if cat[0] == "L":
            letters += 1
    n = len(s)
    if printable < n * 0.9 or letters < 3 or letters < n * 0.5:
        return None
    return s


_B64_TOKEN = re.compile(r"[A-Za-z0-9+/_-]{8,}={0,2}")
_B64_WORD = re.compile(r"^[A-Za-z0-9+/_-]{2,}={0,2}$")
_B64_ALPHA = re.compile(r"^[A-Za-z0-9+/]+$")
_EDGE_L = re.compile(r"^[(\"'“‘:]+")
_EDGE_R = re.compile(r"[)\"'”’.,!?;:]+$")


def _b64_bytes(tok: str) -> bytes | None:
    t = tok.rstrip("=").replace("-", "+").replace("_", "/")
    if len(t) < 6 or not _B64_ALPHA.match(t):
        return None
    if len(t) % 4 == 1:
        t = t[:-1]
    try:
        return base64.b64decode(t + "=" * (-len(t) % 4))
    except Exception:
        return None


def _b64_candidates(text: str) -> list[str]:
    out: list[str] = []
    for m in _B64_TOKEN.finditer(text):
        tok = m.group(0)
        for k in range(4):
            if len(tok) - k >= 8:
                out.append(tok[k:])
    ws = [_EDGE_R.sub("", _EDGE_L.sub("", w)) for w in text.split()]
    runs, run = [], []
    for w in ws:
        if _B64_WORD.match(w):
            run.append(w)
        else:
            if len(run) >= 2:
                runs.append(run)
            run = []
    if len(run) >= 2:
        runs.append(run)
    for r in runs:
        n = min(len(r), 24)
        for i in range(n):
            for j in range(i + 2, n + 1):
                out.append("".join(r[i:j]))
    return list(dict.fromkeys(out))


_HEX_RUN = re.compile(r"(?<![0-9a-z])(?:(?:0x|\\x)?[0-9a-f]{2}[\s,:;-]*){4,}", re.I)
_HEX_STRIP = re.compile(r"0x|\\x", re.I)
_NOT_HEX = re.compile(r"[^0-9a-f]", re.I)


def _hex_candidates(text: str) -> list[bytes]:
    out = []
    for m in _HEX_RUN.finditer(text):
        h = _NOT_HEX.sub("", _HEX_STRIP.sub("", m.group(0)))
        if len(h) % 2:
            h = h[:-1]
        try:
            out.append(bytes.fromhex(h))
        except ValueError:
            continue
    return out


_PRINTABLE_HEX = re.compile(
    r'(?<![0-9a-z])(?:(?:0x|\\x)?[0-9a-f]{2}[\s,:;-]*){4,}(?![0-9a-z])', re.I)


def printable_hex(text: str) -> bool:
    """Detect complete hex runs decoding to printable ASCII, not arbitrary hashes."""
    for match in _PRINTABLE_HEX.finditer(normalize_text(text)):
        h = _NOT_HEX.sub('', _HEX_STRIP.sub('', match.group(0)))
        raw = bytes.fromhex(h)
        if raw and all(0x20 <= b < 0x7f for b in raw):
            return True
    return False


_PCT = re.compile(r"%[0-9a-f]{2}", re.I)
_PCT_RUN = re.compile(r"(?:%[0-9a-f]{2})+", re.I)


def _url_decode(text: str) -> list[str]:
    if not _PCT.search(text):
        return []

    def rep(m: re.Match) -> str:
        g = m.group(0)
        raw = bytes(int(g[i + 1:i + 3], 16) for i in range(0, len(g), 3))
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return raw.decode("latin-1")

    dec = _PCT_RUN.sub(rep, text)
    out = [dec]
    if "+" in dec:
        out.append(dec.replace("+", " "))
    return out


_BIN = re.compile(r"(?:\b[01]{8}\b[\s,]*){3,}")
_DEC = re.compile(r"\b(?:\d{2,3}[\s,]+){3,}\d{2,3}\b")
_MORSE_RUN = re.compile(r"(?:[.-]{1,6}(?:\s*/\s*|\s+|\|)){3,}[.-]{1,6}")
_MORSE = {
    ".-": "a", "-...": "b", "-.-.": "c", "-..": "d", ".": "e", "..-.": "f", "--.": "g", "....": "h", "..": "i", ".---": "j",
    "-.-": "k", ".-..": "l", "--": "m", "-.": "n", "---": "o", ".--.": "p", "--.-": "q", ".-.": "r", "...": "s", "-": "t",
    "..-": "u", "...-": "v", ".--": "w", "-..-": "x", "-.--": "y", "--..": "z", "-----": "0", ".----": "1", "..---": "2",
    "...--": "3", "....-": "4", ".....": "5", "-....": "6", "--...": "7", "---..": "8", "----.": "9",
}
_MORSE_MAP = str.maketrans({"·": ".", "•": ".", "∙": ".", "−": "-", "–": "-", "—": "-", "_": "-"})


def _binary_candidates(text: str) -> list[bytes]:
    return [bytes(int(b, 2) for b in re.findall(r"[01]{8}", m.group(0))) for m in _BIN.finditer(text)]


def _decimal_candidates(text: str) -> list[str]:
    out = []
    for m in _DEC.finditer(text):
        n = [int(x) for x in re.findall(r"\d+", m.group(0))]
        if all(32 <= x <= 126 for x in n):
            out.append("".join(chr(x) for x in n))
    return out


def morse_candidates(text: str) -> list[str]:
    t = text.translate(_MORSE_MAP)
    out = []
    for m in _MORSE_RUN.finditer(t):
        groups = re.split(r"\s*(?:/|\||\s{3,})\s*", m.group(0).strip())
        s = " ".join("".join(_MORSE.get(c, "") for c in g.strip().split()) for g in groups)
        if sum(1 for c in s if c.isalpha()) >= 4:
            out.append(s)
    return out


# ---- letter ciphers ------------------------------------------------------------------------------------------------

def caesar(s: str, k: int) -> str:
    out = []
    for c in s:
        o = ord(c)
        if 65 <= o <= 90:
            out.append(chr((o - 65 + k) % 26 + 65))
        elif 97 <= o <= 122:
            out.append(chr((o - 97 + k) % 26 + 97))
        else:
            out.append(c)
    return "".join(out)


def atbash(s: str) -> str:
    out = []
    for c in s:
        o = ord(c)
        if 65 <= o <= 90:
            out.append(chr(90 - (o - 65)))
        elif 97 <= o <= 122:
            out.append(chr(122 - (o - 97)))
        else:
            out.append(c)
    return "".join(out)


def _reverse_runs(s: str, pred) -> str:
    out, cur = [], []
    for c in s:
        if pred(c):
            cur.append(c)
        else:
            if cur:
                out.append("".join(reversed(cur)))
                cur = []
            out.append(c)
    if cur:
        out.append("".join(reversed(cur)))
    return "".join(out)


def per_word_reverse(s: str) -> str:
    """Each letter run reversed in place, joiners kept: 'tsoh otatop' -> 'host potato', 'tsoh1otatop' -> 'host1potato',
    'tsohOtatop' -> 'host suckS'."""
    return _reverse_runs(camel_space(s), is_letter)


def per_word_reverse_ln(s: str) -> str:
    """The letter-and-digit reading of per-word reversal (digits stay inside a run)."""
    return _reverse_runs(camel_space(s), is_ln)


def _space_joiners(s: str) -> str:
    """Each joiner run inside a token as a space, so a joined candidate's words match the echo ('host1potato' ->
    'host potato')."""
    out, pend, last_letter = [], [], False
    for c in s:
        if is_letter(c):
            out.append(" " if last_letter and pend else "".join(pend))
            pend = []
            out.append(c)
            last_letter = True
        elif c.isspace():
            out.append("".join(pend) + c)
            pend = []
            last_letter = False
        elif last_letter:
            pend.append(c)
        else:
            out.append(c)
    return "".join(out) + "".join(pend)


def _script_split(s: str) -> str:
    """A space between adjacent letters of different scripts, Latin and not ('tsoh一otatop' -> 'tsoh 一 otatop')."""
    out, prev = [], None
    for c in s:
        if is_letter(c):
            lat = is_latin(c)
            if prev is not None and lat != prev:
                out.append(" ")
            prev = lat
        else:
            prev = None
        out.append(c)
    return "".join(out)


def _drop_sandwich(s: str) -> str:
    """One non-Latin letter between two Latin letters read as a joiner ('tsohжotatop' -> 'tsoh otatop')."""
    cs = list(s)
    for i in range(1, len(cs) - 1):
        c = cs[i]
        if (is_letter(c) and not is_latin(c) and is_letter(cs[i - 1]) and is_latin(cs[i - 1])
                and is_letter(cs[i + 1]) and is_latin(cs[i + 1])):
            cs[i] = " "
    return "".join(cs)


def spaced_readings(tok: str) -> list[str]:
    """A token's letter runs spaced apart under both camelCase readings and split between scripts."""
    out = []
    for r in (camel_space(tok), camel_upper(tok), _script_split(camel_space(tok)), _drop_sandwich(camel_space(tok))):
        runs = letter_runs(r)
        if len(runs) >= 2:
            s = " ".join(runs)
            if s not in out:
                out.append(s)
    return out


def _per_word_readings(src: str) -> list[tuple[str, str]]:
    a = per_word_reverse(src)
    b = per_word_reverse(camel_upper(src))
    return [("revwords", a), ("revwords", _space_joiners(a)), ("revwords-upper", b), ("revwords-upper", _space_joiners(b))]


# ---- encoded shapes ------------------------------------------------------------------------------------------------

_SH_B64 = re.compile(r"[A-Za-z0-9+/_-]{16,}={0,2}")
_SH_B64_PAD = re.compile(r"(?<![A-Za-z0-9+/])[A-Za-z0-9+/]{6,}={1,2}(?![A-Za-z0-9=])")
_SH_B32 = re.compile(r"(?<![A-Za-z0-9])(?=[A-Z2-7]*[2-7])[A-Z2-7]{12,}={0,6}(?![A-Za-z0-9])")
_SH_HEX = re.compile(r"(?<![0-9a-z])(?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])[0-9a-f]{16,}(?![0-9a-z])", re.I)
_SH_HEX_PAIRS = re.compile(r"(?<![0-9a-z])(?:[0-9a-f]{2}[\s,:;-]+){3,}[0-9a-f]{2}(?![0-9a-z])", re.I)
_SH_HEX_ESC = re.compile(r"(?:(?:0x|\\x)[0-9a-f]{2}[\s,]*){4,}", re.I)
_SH_PCT = re.compile(r"%[0-9a-f]{2}[^%]{0,12}%[0-9a-f]{2}", re.I)
_UU = re.compile(r"[A-Z](?=[A-Z])")


def _b64_shape(t: str) -> bool:
    for m in _SH_B64.finditer(t):
        b = m.group(0)
        if (re.search(r"[a-z]", b) and re.search(r"[A-Z]", b) and (re.search(r"\d", b) or re.search(r"[+/=]", b))
                and len(_UU.findall(b)) >= 2):
            return True
    return False


def encoded_shape(t: str) -> str | None:
    """The shape of encoded text (arms the window even when no decoder turns it into words). Narrow on purpose:
    camelCase names, emote names, colour codes, short hashes and ordinary numbers are not shapes."""
    if _b64_shape(t):
        return "base64"
    if _SH_B64_PAD.search(t):
        return "base64-padded"
    if _SH_B32.search(t):
        return "base32"
    if _SH_HEX.search(t):
        return "hex"
    if any(re.search(r"[a-f]", m.group(0), re.I) for m in _SH_HEX_PAIRS.finditer(t)):
        return "hex-pairs"
    if _SH_HEX_ESC.search(t):
        return "hex-escaped"
    if _BIN.search(t):
        return "binary"
    if _SH_PCT.search(t):
        return "percent"
    if morse_candidates(t):
        return "morse"
    return None


# ---- the recursive decoder -----------------------------------------------------------------------------------------

def _decode_one(src: str, depth: int, seen: set, out: list, prefix: str) -> None:
    if depth > MAX_DEPTH or len(out) >= MAX_PAYLOADS:
        return
    src_key = normalize_text(src).lower()

    def add(via: str, text: str | None, recurse: bool = True, raw: bytes | None = None) -> None:
        if not text:
            # not readable itself, but the next layer might be (base64 of hex): recurse on printable ASCII only
            if raw is not None and len(raw) >= 8 and all(0x20 <= b < 0x7F for b in raw) and recurse:
                inner = raw.decode("ascii")
                if (">" + inner) not in seen:
                    seen.add(">" + inner)
                    _decode_one(inner, depth + 1, seen, out, f"{prefix}{via}>")
            return
        if len(out) >= MAX_PAYLOADS:
            return
        clean = normalize_text(text)
        key = clean.lower()
        if len(clean) < 3 or key in seen or key == src_key:
            return
        seen.add(key)
        p = _plain(prefix + via, clean)
        if p is None:
            return
        out.append(p)
        if recurse:
            _decode_one(clean, depth + 1, seen, out, f"{prefix}{via}>")

    for tok in _b64_candidates(src):
        b = _b64_bytes(tok)
        if b is not None:
            add("base64", as_text(b), True, b)
    for b in _hex_candidates(src):
        add("hex", as_text(b), True, b)
    for u in _url_decode(src):
        add("url", u)
    for b in _binary_candidates(src):
        add("binary", as_text(b))
    for d in _decimal_candidates(src):
        add("decimal", d)
    for m in morse_candidates(src):
        add("morse", m)
    for k in range(1, 26):
        s = caesar(src, k)
        if reads_english(s, src):
            add(f"rot{k}", s, False)
    rev = src[::-1]
    if reads_english(rev, src):
        add("reversed", rev, False)
    atb = atbash(src)
    if reads_english(atb, src):
        add("atbash", atb, False)
    for pwr in (per_word_reverse(src), per_word_reverse_ln(src)):
        if pwr != rev and reads_english(pwr, src):
            add("revwords", pwr, False)


def reads_as_words(p: Payload) -> bool:
    return any(len(w) >= 2 and w in COMMON for w in p.words)


_SHIFTED_VIA = re.compile(r"(?:^|>)(?:rot\d+|reversed|atbash|revwords)$")
_PRINTABLE_ASCII = re.compile(r"^[\x20-\x7e]+$")
_ALNUM_ASCII = re.compile(r"^[a-z0-9]+$")


def name_reads(p: Payload) -> bool:
    """Stricter than reads_as_words, for display names (short strings decode to short noise): printable ASCII and
    either >= 2 distinct common words making up >= 1/3 of the words, or a byte-decoded candidate of >= 2 plain words
    and >= 5 letters holding a common word."""
    if not _PRINTABLE_ASCII.match(p.text):
        return False
    commons = {w for w in p.words if len(w) >= 2 and w in COMMON}
    if len(commons) >= 2 and len(commons) * 3 >= len(p.words):
        return True
    return (not _SHIFTED_VIA.search(p.via.split(":")[-1]) and len(p.words) >= 2 and len(p.letters) >= 5
            and len(commons) >= 1 and all(_ALNUM_ASCII.match(w) for w in p.words))


# ---- targeted segments and forced candidates ----------------------------------------------------------------------

SEG_TRIM = frozenset(COMMON | set((
    "pls plz please thx ty thanks lol lmao haha hehe xd rn hey yo quick quickly aloud loud out real decode decoded "
    "decipher decrypt translate read reverse reversed backwards backward rot rot13 caesar cipher ciphertext base64 b64 "
    "hex binary morse encoded message msg secret code text string spell username name mean means what does"
).split()))
_EDGE = re.compile(r"^[\s\"'“”‘’`(\[{<:;,.!?\-–—=>@#*_~]+|[\s\"'“”‘’`)\]}>:;,.!?\-–—=<@#*_~]+$")
_SEP_SEG = re.compile(r":|=|->|—|–|\s-\s")
_QUOTE_SEG = re.compile(r"[\"“”]([^\"“”]{2,})[\"“”]|(?:^|\s)['‘]([^'‘’]{2,})['’](?=$|[\s.,!?;:])")


def strip_edges(s: str) -> str:
    return _EDGE.sub("", s)


def _trim_word(tok: str) -> bool:
    w = strip_ln_edges(tok.lower())
    return not w or w in SEG_TRIM or bool(re.fullmatch(r"\d{1,2}", w))


def _trim_segment(s: str) -> str:
    toks = strip_edges(s).split()
    while toks and _trim_word(toks[0]):
        toks.pop(0)
    while toks and _trim_word(toks[-1]):
        toks.pop()
    return strip_edges(" ".join(toks))


@lru_cache(maxsize=4096)
def targeted_segments(text: str) -> tuple[str, ...]:
    """The parts of a message a decode request targets, each decoded by itself so a plain request prefix or suffix
    cannot dilute a short payload ('rot13 this: borl pung' -> 'borl pung')."""
    t = normalize_text(text)
    if not t:
        return ()
    raw: list[str] = []
    for n, m in enumerate(_SEP_SEG.finditer(t)):
        if n >= 3:
            break
        raw.append(t[m.end():])

    def around(rx: re.Pattern, before: bool) -> None:
        for k, m in enumerate(rx.finditer(t)):
            if k >= 3:
                break
            raw.append(t[m.end():])
            if before:
                raw.append(t[:m.start()])

    around(P.DECODE_REQUEST, True)
    raw.append(t)
    around(P.ARM_LABEL, False)
    around(P.ENC_TOKEN_G, True)
    for m in _QUOTE_SEG.finditer(t):
        raw.append(m.group(1) or m.group(2))
    out: list[str] = []

    def add(s: str) -> None:
        c = s.strip()
        if len(c) >= 2 and any(ch.isalpha() for ch in c) and c != t and c not in out:
            out.append(c)

    for r in raw:
        add(_trim_segment(r))
        add(strip_edges(r))
        if len(out) >= SEG_MAX:
            break
    kept = out[:SEG_MAX]
    # runs of words that are not common/request/filler words: a payload framed by plain words with no separator
    runs, cur = [], []
    for tok in t.split():
        if any(ch.isalpha() for ch in tok) and not _trim_word(tok):
            cur.append(tok)
        else:
            if cur:
                runs.append(" ".join(cur))
            cur = []
    if cur:
        runs.append(" ".join(cur))
    for r in runs[:4]:
        c = strip_edges(r)
        if len(c) >= 2 and c != t and c not in kept:
            kept.append(c)
    return tuple(kept)


def spelled_words(core: str) -> list[str] | None:
    """A spelled-out token whose word boundaries use another joiner ('l-l-i-b_s-k-c-u-s') -> its words."""
    letters, seps, cur = [], [], ""
    for c in core:
        if is_letter(c):
            if letters and not cur:
                return None
            if letters:
                seps.append(cur)
            cur = ""
            letters.append(c.lower())
        else:
            if not letters:
                return None
            cur += c
    if cur or len(seps) < 2:
        return None
    count: dict[str, int] = {}
    for x in seps:
        count[x] = count.get(x, 0) + 1
    if len(count) < 2:
        return None
    letter_sep = max(count.items(), key=lambda kv: kv[1])[0]
    ws, w = [], letters[0]
    for k, sp in enumerate(seps):
        if sp == letter_sep:
            w += letters[k + 1]
        else:
            ws.append(w)
            w = letters[k + 1]
    ws.append(w)
    return ws if len(ws) >= 2 and any(len(x) >= 2 for x in ws) else None


def _forced_sources(text: str) -> list[str]:
    t = normalize_text(text)
    if not t:
        return []
    out: list[str] = []

    def add(s: str) -> None:
        if 2 <= len(s) <= FORCE_MAX_CHARS and any(c.isalpha() for c in s) and s not in out:
            out.append(s)

    for s in [_trim_segment(t), *targeted_segments(t), *([t] if len(t) <= FORCE_MAX_CHARS else [])]:
        add(s)
    primary = out[:FORCE_MAX_SOURCES]
    pairs = []
    for s in primary:
        w = [x for x in (strip_edges(y) for y in s.split()) if any(c.isalpha() for c in x)]
        if 3 <= len(w) <= 8:
            for i in range(len(w) - 1):
                pairs.append(f"{w[i]} {w[i + 1]}")
    k = 0
    for p in pairs:
        if p not in primary and k < FORCE_MAX_PAIRS:
            primary.append(p)
            k += 1
    j = 0
    for tok in t.split():
        if j >= FORCE_MAX_JOINED:
            break
        c = strip_edges(tok)
        if not c or len(c) > FORCE_MAX_CHARS:
            continue
        sp = spaced_readings(c)
        if not sp:
            continue
        j += 1
        for s in [c, *sp]:
            if s not in primary:
                primary.append(s)
        sw = spelled_words(strip_ln_edges(c))
        if sw and " ".join(sw) not in primary:
            primary.append(" ".join(sw))
    return primary


def _force_shifts(src: str, prefix: str, out: list, have: set) -> None:
    if len(src) > FORCE_MAX_CHARS or not re.search(r"[a-z]", src, re.I) or encoded_shape(src):
        return

    def push(via: str, text: str) -> None:
        if len(out) >= FORCED_CAP:
            return
        clean = normalize_text(text)
        key = clean.lower()
        if len(clean) < 2 or key == src.lower() or key in have:
            return
        have.add(key)
        p = _plain(f"{prefix}forced:{via}", clean)
        if p is not None:
            out.append(p)

    for k in range(1, 26):
        push(f"rot{k}", caesar(src, k))
    push("reversed", src[::-1])
    push("atbash", atbash(src))
    push("revwords", per_word_reverse_ln(src))
    for via, x in _per_word_readings(src):
        push(via, x)


# ---- selective per-word and pig latin candidates (keyed) ---------------------------------------------------------

def keyed_run_ok(run, nk: int) -> bool:
    """A run of a keyed candidate's words that refuses: two keys and a non-stop word, or >= 3 words with a key and a
    non-stop word (any >= 5)."""
    nonstop = any(x not in STOP for x in run)
    return (nk >= 2 and nonstop) or (len(run) >= 3 and nk >= 1 and (len(run) >= 5 or nonstop))


def keyed_payload(via: str, ws: list[str], keys: list[bool]) -> Payload:
    kp = [0]
    for k in keys:
        kp.append(kp[-1] + (1 if k else 0))
    needles: list[str] = []
    for ln in (2, 3):
        for i in range(len(ws) - ln + 1):
            run = ws[i:i + ln]
            if keyed_run_ok(run, kp[i + ln] - kp[i]):
                letters = "".join(run)
                if len(letters) >= 6 and letters not in needles:
                    needles.append(letters)
    key_words = tuple(dict.fromkeys(w for w, k in zip(ws, keys) if k))
    return Payload(via, " ".join(ws), tuple(ws), "".join(ws), tuple(keys), tuple(kp), tuple(needles), key_words)


@lru_cache(maxsize=4096)
def selective_candidates(raw: str, rot_n: bool, lex: Lexicon = BASE_LEX, prefix: str = "") -> tuple[Payload, ...]:
    """For each token, its reversal (and with rot_n its rot-N form) when that form is a known word and the token is
    not, joined in order. Kept only when a decoded word has >= 3 letters (a key)."""
    t = normalize_text(raw)
    out: list[Payload] = []
    if not t:
        return ()
    readings, seen_r = [], set()
    for r in (t, camel_space(t), camel_upper(t), _drop_sandwich(camel_space(t))):
        runs = letter_runs(_script_split(r))
        key = " ".join(runs)
        if runs and key not in seen_r:
            seen_r.add(key)
            readings.append(runs)
    seen: set[str] = set()
    for runs in readings:
        low = [x.lower() for x in runs]
        known = [lex.known(w) for w in low]
        if all(known):
            continue
        rev: list[str | None] = []
        for w, kn in zip(low, known):
            r = w[::-1]
            rev.append(r if not kn and r != w and lex.known(r) else None)
        rot_at: dict[int, dict[int, str]] = {}
        if rot_n:
            for i, w in enumerate(low):
                if known[i] or not re.search(r"[a-z]", w):
                    continue
                for k in range(1, 26):
                    c = caesar(w, k)
                    if lex.known(c):
                        rot_at.setdefault(k, {})[i] = c
        for k in [0, *sorted(rot_at)]:
            rk = rot_at.get(k, {})
            ws: list[str] = []
            keys: list[bool] = []
            nk = 0
            for i, w in enumerate(low):
                c = rk.get(i) or rev[i]
                word = c if c is not None else w
                is_key = c is not None and len(word) >= 3
                nk += is_key
                for x in match_words(word):
                    ws.append(x)
                    keys.append(is_key)
            if not nk:
                continue
            text = " ".join(ws)
            if text in seen:
                continue
            seen.add(text)
            out.append(keyed_payload(f"{prefix}select:{'rot%d+' % k if k else ''}revword", ws, keys))
            if len(out) >= SEL_MAX:
                return tuple(out)
    return tuple(out)


REAL_AY = frozenset((
    "today okay away play stay display essay holiday birthday weekday yesterday anyway relay delay replay array spray "
    "tray gray pray decay sway highway railway doorway hallway subway runway gateway someday everyday midday payday "
    "mayday clay fray slay stray xray astray betray portray hooray cosplay gameplay roleplay overlay screenplay airplay "
    "fairway underway faraway getaway giveaway takeaway hideaway layaway castaway stowaway runaway breakaway halfway "
    "midway freeway driveway alleyway waterway stairway pathway causeway expressway parkway broadway norway segway "
    "malay sashay foray monday tuesday wednesday thursday friday saturday sunday mislay inlay outplay downplay "
    "interplay wordplay swordplay horseplay hearsay waylay allay assay belay bluejay ebay leeway dismay disarray airway "
    "doomsday heyday mainstay workday schoolday speedway skyway tramway entryway gangway headway seaway archway"
).split())


def un_pig(tok: str, lex: Lexicon = BASE_LEX) -> list[str]:
    """The plain forms of one pig-latin token, most likely first ('atchay' -> chat, 'isway' -> is)."""
    w = str(tok or "").lower()
    if not re.fullmatch(r"[a-z]{2,}ay", w) or lex.known(w) or w in REAL_AY:
        return []
    base = w[:-2]
    out: list[str] = []
    if re.match(r"[aeiou]", base) and re.search(r"[wyh]$", base):
        c = base[:-1]
        if c and lex.known(c):
            out.append(c)
    for k in range(min(3, len(base) - 1), 0, -1):
        moved, rest = base[-k:], base[:-k]
        if (not re.fullmatch(r"[b-df-hj-np-tv-z]+", moved) and moved != "qu") or not re.match(r"[aeiouy]", rest):
            continue
        c = moved + rest
        if lex.known(c) and c not in out:
            out.append(c)
    return out


@lru_cache(maxsize=2048)
def pig_candidates(raw: str, lex: Lexicon = BASE_LEX) -> tuple[Payload, ...]:
    t = normalize_text(raw)
    if not t or not re.search(r"ay\b", t, re.I):
        return ()
    runs = letter_runs(t)
    forms = [un_pig(r, lex) for r in runs]
    if not any(forms):
        return ()
    out, seen = [], set()
    for pick in (lambda f: f[0], lambda f: f[-1]):
        ws: list[str] = []
        keys: list[bool] = []
        nk = 0
        for r, f in zip(runs, forms):
            c = pick(f) if f else None
            is_key = c is not None and len(c) >= 3
            nk += is_key
            for x in match_words(c if c is not None else r):
                ws.append(x)
                keys.append(is_key)
        text = " ".join(ws)
        if nk and text not in seen:
            seen.add(text)
            out.append(keyed_payload("select:piglatin", ws, keys))
    return tuple(out)


# ---- per-text decode (cached) --------------------------------------------------------------------------------------

@dataclass(frozen=True)
class TextDecode:
    payloads: tuple[Payload, ...]
    readable: bool            # a decoded candidate reads as words (names: name_reads)
    shape: str | None         # encoded_shape of the text


@lru_cache(maxsize=4096)
def decode_text(raw: str, name: bool = False, lex: Lexicon = BASE_LEX) -> TextDecode:
    """Unarmed payloads of one message text (or one display name): the decoders over the whole text and over each
    targeted segment, plus the reversal-only selective candidate."""
    t = normalize_text(raw)
    if not t:
        return TextDecode((), False, None)
    seen: set = set()
    mine: list[Payload] = []
    _decode_one(t, 1, seen, mine, "name:" if name else "")
    segs: list[Payload] = []
    if not name:
        for s in targeted_segments(t):
            _decode_one(s, 1, seen, segs, "seg:")
    if name:
        readable = any(name_reads(p) for p in mine)
    else:
        readable = any(reads_as_words(p) for p in mine) or any(reads_as_words(p) for p in segs)
    sel = selective_candidates(t, False, lex, "name:" if name else "")
    return TextDecode(tuple(mine + segs) + sel, readable, encoded_shape(t))


@lru_cache(maxsize=2048)
def forced_text(raw: str, name: bool = False, lex: Lexicon = BASE_LEX) -> tuple[Payload, ...]:
    """Armed-only payloads of one text or name: forced shifts of its short sources, rot-N selective candidates and
    pig-latin readings."""
    t = normalize_text(raw)
    if not t:
        return ()
    out: list[Payload] = []
    have: set = set()
    prefix = "name:" if name else ""
    sources = [t, *spaced_readings(t)] if name else _forced_sources(t)
    for s in sources:
        _force_shifts(s, prefix, out, have)
    out.extend(selective_candidates(t, True, lex, prefix))
    out.extend(pig_candidates(t, lex))
    return tuple(out)


# ---- the frozen public helpers --------------------------------------------------------------------------------------

def candidates(text: str, *, armed: bool = False, strict: bool = False) -> list[str]:
    """Plaintext candidates hidden in `text` (base64, hex, URL, rot-N, reversal, per-word reversal, atbash ...).

    Unarmed: only candidates that pass the decoder keep rules, plus selective per-word reversals. Armed: also every
    rot-N (all 25), the reversal, Atbash and per-word reversals of each short targeted segment and joined token, the
    rot-N selective candidates and pig-latin readings. On an internal error, returns what it has unless strict=True."""
    out: list[str] = []
    try:
        if not isinstance(text, str):
            return []
        for p in decode_text(text).payloads:
            if p.text not in out:
                out.append(p.text)
        if armed:
            for p in forced_text(text):
                if p.text not in out:
                    out.append(p.text)
    except Exception:
        if strict:
            raise
        return out
    return out


def _parts_reverse_known(n: str, lex: Lexicon = BASE_LEX) -> bool:
    parts = [p.lower() for p in split_letter_runs(n)]
    if len(parts) < 2 or any(len(p) < 3 for p in parts):
        return False
    return all(not lex.known(p) and lex.known(p[::-1]) for p in parts)


def joined_name_encoded(token: str) -> bool:
    """Recognize a joined two-word phrase under a Caesar shift or reversal.

    Do not flag an already recognizable word or compound. Short words produce
    too many accidental readings in ordinary handles.
    """
    n = normalize_text(token)
    if not n.isascii() or not n.isalpha() or not 8 <= len(n) <= 32:
        return False
    n = n.lower()
    def compound(s: str) -> bool:
        return any(BASE_LEX.known(s[:i]) and BASE_LEX.known(s[i:])
                   for i in range(4, len(s) - 3))
    if BASE_LEX.known(n) or compound(n):
        return False
    return compound(n[::-1]) or any(compound(caesar(n, shift)) for shift in range(1, 26))


def looks_encoded(token: str) -> bool:
    """True if a single token (e.g. a display name) looks like an encoded payload. Never raises (errors: True)."""
    try:
        n = normalize_text(token)
        if not n:
            return False
        for f in dict.fromkeys((n, n.replace("_", " ").strip())):
            if encoded_shape(f):
                return True
            if any(name_reads(p) for p in decode_text(f, True).payloads if p.keys is None):
                return True
        return _parts_reverse_known(n)
    except Exception:
        return True


_HANDLE_WHOLE = re.compile(r"^@[A-Za-z0-9_]+$")
_CLOSE_PUNCT = ",.!?:;)]}\"'”’…"


def split_joined(token: str) -> list[str]:
    """Split a joined token into its letter runs: any non-letter between two letter runs is a joiner (a letter is
    \\p{L} minus \\p{Lm}), and camelCase splits, including an upper run before lower case. An @-handle stays whole.
    Never raises (errors: the token unsplit)."""
    try:
        if not isinstance(token, str):
            return []
        # Split the original code points: normalization can delete joiners or
        # compose a combining mark into a letter and erase the boundary.
        t = token.strip()
        core = strip_ln_edges(t)
        if not core:
            return [t] if t else []
        h = t.rstrip(_CLOSE_PUNCT)
        if _HANDLE_WHOLE.match(h):
            return [h]
        runs = split_letter_runs(core)
        return runs if len(runs) >= 2 else [core]
    except Exception:
        return [token] if isinstance(token, str) else []
