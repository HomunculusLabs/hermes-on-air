"""Known words (the "is this a real word" test) and the homoglyph fold used for names and the never-say list.

Known words = the bundled SCOWL list (wordlist_en.py, size 50, en_US; license in WORDLIST-LICENSE.txt) plus the
guard's own plain-word lists, plus the show's own names (ctx.host_name, ctx.protected_names). Names are an
ADDITIVE list: they only ever make a word known, which (a) stops the gap rule from arming on "hi <name> and
<name>!" and (b) lets the selective per-word candidate decode a reversed or shifted name ('tsoh' -> 'host').
Two-letter words count as known only when they come from the plain lists (a dictionary holds many odd 2-letter
entries).
"""
from __future__ import annotations

import codecs
import unicodedata
from functools import lru_cache

from . import wordlist_en
from .text import COMMON, STOP

_DICT: frozenset[str] | None = None


def dictionary() -> frozenset[str]:
    global _DICT
    if _DICT is None:
        words = set(wordlist_en.WORDS.split())
        # Dictionary words the word filter also lists ship ROT13-encoded (see tools/make_wordlist.py).
        words.update(codecs.decode(getattr(wordlist_en, "CONCEALED_ROT13", ""), "rot_13").split())
        _DICT = frozenset(words)
    return _DICT


# Plain words that are not dictionary words but are ordinary in chat (and the 2-letter words we accept).
PLAIN_EXTRA = frozenset((
    "ah eh ha uh um hm oh ok hi yo ya ye aw ow lol lmao lmfao rofl xd omg omfg wtf idk idc tbh ngl imo imho btw fr "
    "frfr ong istg smh fyi afaik iirc rn bc cuz tho gg wp ez glhf gl hf pog poggers pogchamp kekw lul lulw sadge copium "
    "hopium bruh bro yall fam bestie lit yay yep yup nope nah hmm hahaha heh lolol tysm thx ty np kk ur ima imma gotta "
    "wanna lemme gimme dunno aint cant dont wont im ive id youre thats whats hows wheres theres lets didnt doesnt isnt "
    "wasnt vod vods emote emotes uwu owo pls plz brb gm gn tyvm hiii heyy"
).split())


class Lexicon:
    """The known-word test for one context (the dictionary plus that context's names)."""

    __slots__ = ("extra", "extra2")

    def __init__(self, names: tuple[str, ...] = ()) -> None:
        extra = set()
        for n in names:
            for part in fold(n).split():
                p = "".join(c for c in part if c.isalpha())
                if p:
                    extra.add(p)
        self.extra = frozenset(extra)
        self.extra2 = frozenset(w for w in self.extra if len(w) == 2)

    def known(self, w: str) -> bool:
        n = len(w)
        if n <= 1:
            return True
        if n == 2:
            return w in _PLAIN2 or w in self.extra2
        return w in self.extra or w in _PLAIN or w in dictionary()


_PLAIN = frozenset(PLAIN_EXTRA | COMMON | STOP)
_PLAIN2 = frozenset(w for w in _PLAIN if len(w) == 2)


@lru_cache(maxsize=64)
def lexicon_for(names: tuple[str, ...]) -> Lexicon:
    return Lexicon(names)


BASE_LEX = Lexicon(())

# ---- homoglyph fold ------------------------------------------------------------------------------------------------
# NFKD + combining marks dropped + casefold handles accents, fullwidth, mathematical and circled letters. The table
# below maps the common look-alikes that do not decompose: Cyrillic, Greek and Armenian letters that render like
# Latin ones, and Latin letters with built-in strokes.
_CONFUSABLE = {
    # Cyrillic
    "а": "a", "в": "b", "с": "c", "ԁ": "d", "е": "e", "ё": "e", "һ": "h", "і": "i", "ї": "i", "ј": "j", "к": "k",
    "ӏ": "l", "м": "m", "н": "h", "о": "o", "р": "p", "ԛ": "q", "г": "r", "ѕ": "s", "т": "t", "у": "y", "ѵ": "v",
    "ԝ": "w", "х": "x", "ү": "y", "ь": "b", "п": "n", "ш": "w",
    # Latin small capitals (IPA) that read as letters in display names
    "ʙ": "b", "ɢ": "g", "ʜ": "h", "ɪ": "i", "ʟ": "l", "ɴ": "n", "ʀ": "r", "ʏ": "y",
    # Greek
    "α": "a", "β": "b", "γ": "y", "ε": "e", "ζ": "z", "η": "n", "θ": "o", "ι": "i", "κ": "k",
    "μ": "u", "ν": "v", "ο": "o", "π": "n", "ρ": "p", "σ": "o", "ς": "c", "τ": "t", "υ": "u",
    "χ": "x", "ω": "w", "ϲ": "c", "ϳ": "j",
    # Greek capitals whose shape differs from their lower-case form: Eta reads as H (eta as n), Mu as M (mu as u),
    # Nu as N (nu as v), Upsilon as Y (upsilon as u). fold() looks the original character up before its lower-case.
    "Η": "h", "Μ": "m", "Ν": "n", "Υ": "y",
    # Armenian look-alikes that show up in name spoofing
    "օ": "o", "ս": "u", "հ": "h", "ո": "n", "ց": "g", "ք": "p", "ա": "w", "զ": "q",
    # Latin with built-in strokes or special forms
    "ı": "i", "ȷ": "j", "ł": "l", "ø": "o", "đ": "d", "ħ": "h", "ŧ": "t", "ƀ": "b", "ɍ": "r", "ɨ": "i", "ʉ": "u",
    "ɑ": "a", "ɡ": "g", "ɩ": "i", "ɵ": "o", "ꞁ": "l", "ſ": "s", "ƅ": "b", "ɓ": "b", "ɗ": "d", "ƒ": "f", "ɦ": "h",
    "æ": "ae", "œ": "oe", "ß": "ss", "þ": "th", "ð": "d", "ŋ": "n", "ʃ": "s", "ɛ": "e", "ɔ": "c", "ǝ": "e", "ə": "e",
    "ℓ": "l", "⁄": "/",
}
# Leetspeak substitutions inside a token that also holds letters ('h0st', 'k1ll', '$ay').
_LEET = {"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "8": "b", "9": "g", "@": "a", "$": "s", "!": "i",
         "|": "l", "+": "t", "€": "e", "£": "l"}


@lru_cache(maxsize=8192)
def fold(s: str) -> str:
    """Case-, diacritic- and homoglyph-insensitive form (no leetspeak mapping): 'Hоst' (Cyrillic о) -> 'host'."""
    t = unicodedata.normalize("NFKC", s)
    t = unicodedata.normalize("NFKD", t)
    out = []
    for c in t:
        if unicodedata.category(c) == "Mn":
            continue
        lc = c.lower()
        # The character itself first: a capital can look like a different letter than its lower-case form.
        out.append(_CONFUSABLE.get(c, _CONFUSABLE.get(lc, lc)))
    return unicodedata.normalize("NFKC", "".join(out)).casefold()


def _leet_token(tok: str, one: str) -> str:
    if not any(c.isalpha() for c in tok):
        return tok
    return "".join(one if c in "1|!" else _LEET.get(c, c) for c in tok)


@lru_cache(maxsize=4096)
def fold_variants(s: str) -> tuple[str, ...]:
    """The folded readings of a line used for name and never-say matching.

    1. fold(s);  2. capital I read as l before lower-casing ('Hоst' -> 'host') and 1 | ! read as l;
    3. leetspeak inside letter tokens with 1 | ! read as i ('k1ll' -> 'kill').  Matching any reading counts."""
    a = fold(s)
    b = fold(s.replace("I", "l").replace("\u0406", "l").replace("\u04c0", "l"))
    b = " ".join(_leet_token(t, "l") for t in b.split(" "))
    c = " ".join(_leet_token(t, "i") for t in a.split(" "))
    out = []
    for v in (a, b, c):
        if v not in out:
            out.append(v)
    return tuple(out)
