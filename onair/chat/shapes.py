"""Shape heuristics for viewer chat (lane: core): real questions, walls, newcomers, asks, bait.

Pure functions over one message's text. Used by ``onair.chat.gate`` (classification) and
``onair.chat.select`` (ranking). They decide what is *worth answering*; they carry no authority,
and every line the agent says is still checked by the output guard.

Design notes:

- "Worthy" is defined narrowly: a real question, a newcomer's first message, or a substantive
  direct ask. Soft signals (on-topic, kind, funny) would make nearly every message worthy and the
  routine cap would never bind.
- A real question needs interrogative *form*, not just a ``?``: ``"KEKW??"``, ``"how cute is
  that?"`` and ``"the tea is cold, right?"`` are not questions.
- Bait shapes (decode / say-this / fake authority / roleplay / private-detail fishing) get
  silence. A spoken refusal hands a troll the reaction they wanted.
"""

from __future__ import annotations

import base64
import binascii
import re
from collections.abc import Iterable

from .lexicon import COMMON_WORDS, EMOTE_TOKENS, FILLER_TOKENS, SLANG
from .text import normalize

_I = re.I

# --------------------------------------------------------------------------------------------
# tokens, content words, walls

_TOKEN_RE = re.compile(r"<3|[^\W_][\w']*", re.UNICODE)


def tokens(text: str) -> list[str]:
    return [t.lower().replace("_", "") for t in _TOKEN_RE.findall(text or "") if t.replace("_", "")]


def _norm_emote(w: str) -> str:
    w = re.sub(r"(.)\1{2,}", r"\1\1", w)
    if re.fullmatch(r"lo+l|lmf?ao+|ha(?:ha)+h?|he(?:he)+", w):
        return "lol"
    if re.fullmatch(r"go{2,}", w):
        return "gooo"
    if re.fullmatch(r"hi{2,}", w):
        return "hii"
    return w


def content_words(text: str, extra_fillers: Iterable[str] = ()) -> list[str]:
    """Tokens that are not emotes, fillers or the given names."""
    fill = set(extra_fillers)
    out = []
    for t in tokens(text):
        n = _norm_emote(t)
        if len(n) >= 2 and n not in EMOTE_TOKENS and n not in fill:
            out.append(n)
    return out


def emote_count(text: str) -> int:
    return sum(1 for t in tokens(text) if (n := _norm_emote(t)) in EMOTE_TOKENS and n not in FILLER_TOKENS)


def wall_shaped(text: str, extra_fillers: Iterable[str] = ()) -> bool:
    """An emote/hype line with little content of its own."""
    cw = len(content_words(text, extra_fillers))
    return cw <= 1 or (cw <= 3 and emote_count(text) >= 2)


GREETING = re.compile(
    r"^\W*(?:hi+|hai+|hello+|hey+|yo+|heya|hiya|howdy|good\s+(?:morning|evening|night|afternoon)|evening|morning|o7)\b",
    _I,
)
NEW_VIEWER = re.compile(
    r"\b(?:new\s+here|i'?m\s+new|im\s+new|first\s+time\s+(?:here|in|watching|seeing)|my\s+first\s+(?:stream|time)"
    r"|just\s+(?:found|wandered|clicked|got\s+here|joined|tuned\s+in)|wandered\s+in|clicked\s+in|browse\s+page"
    r"|sorry\s+if\s+(?:this\s+is\s+)?(?:a\s+)?(?:dumb|weird|stupid|obvious|basic)|noob|newbie|new\s+viewer"
    r"|first\s+timer|very\s+lost|i'?m\s+(?:so\s+)?lost|still\s+catching\s+up|catch\s+me\s+up|what\s+did\s+i\s+miss)\b",
    _I,
)

# --------------------------------------------------------------------------------------------
# real questions

_WH_WORDS = r"(?:what|whats|what's|how|how's|hows|why|when|where|where's|which|who|who's|whos|whose|whom)"
_AUX = (
    r"(?:can|could|would|should|will|won'?t|do|does|did|don'?t|doesn'?t|didn'?t|is|isn'?t|are|aren'?t|was"
    r"|wasn'?t|were|have|has|had|am|may|might|shall)"
)
WH = re.compile(rf"\b{_WH_WORDS}\b", _I)
WH_LEAD = re.compile(
    rf"^{_WH_WORDS}\b(?:\s+[^\W\d_][\w'-]*){{0,2}}?\s+(?:is|are|was|were|do|does|did|can|could|would|will|should"
    r"|has|have|had|am|i|you|u|we|she|he|it|they|that|this|there|an?|the|your|ur|her|his|kind|much|many|long|often"
    r"|exactly|even|made|makes|happens?|decides?|controls?|runs?|built|goes|means|works?)\b",
    _I,
)
AUX_LEAD = re.compile(
    rf"^{_AUX}\s+(?:you|u|ya|we|i|it|she|he|they|that|this|there|the|a|an|your|ur|her|his|my|our|chat|anyone"
    r"|anybody|someone|somebody|any|tomorrow[-\s]?\w*|tonight|agents?|ai|those|these)\b",
    _I,
)
AUX_ANY = re.compile(rf"^{_AUX}\s+[\w'-]+", _I)
SECOND_PERSON_LEAD = re.compile(r"^(?:you|u|ya|ur|your|you're|youre|everyone\s+here)\b", _I)
FILLER_LEAD = re.compile(
    r"^(?:(?:chat|ok(?:ay)?|so|wait|also|but|and|or|hey|yo|hi+|hello|omg|lol|lmao|ngl|tbh|real(?:ly)?"
    r"|honest(?:ly)?|genuine(?:ly)?|serious(?:ly)?|quick|dumb|random|q|question|follow\s+up|um+|uh+|oh+|ah+"
    r"|actually|btw|then|like|pls|please|sorry|sry|hmm+|ooh+|ok\s+so|anyway)\b[\s,:!.\-]*)+",
    _I,
)
RHETORICAL = re.compile(
    r"^(?:how\s+(?:cute|cozy|adorable|cool|funny|wild|good|great|sweet|lucky|dare|is\s+this\s+(?:so|real))"
    r"|who\s+(?:cares|asked|knew)|(?:what|such)\s+(?:a|an)\s+[^\W\d_]+|why\s+(?:not|tho|though)"
    r"|what\s+even\s+is\s+(?:this|that)|who\s+let\s+(?:her|him|them|this)|how\s+is\s+this\s+(?:so|real))\b",
    _I,
)
_EXCLAMATIVE_WH = re.compile(
    r"^(?:how|what)\s+(?:a|an|cute|cozy|adorable|cool|funny|wild|good|great|sweet|lucky)\b", _I
)
_SENT_RE = re.compile(r"[^.!?？…\n]+(?:[.!?？…\n]+|$)")
_CLAUSE_SPLIT = re.compile(r"[,;:()]|\s[-–—]\s|\s+(?=(?:or|but)\s+(?:is|are|do|does|can|did|was|will|would)\b)", _I)


def _names_alt(names: Iterable[str]) -> str:
    parts = [re.escape(n.strip().lower()) for n in names if n and n.strip()]
    return "|".join(sorted(set(parts), key=len, reverse=True))


def addressed_to_other_viewer(text: str, own_names: Iterable[str] = ()) -> bool:
    m = re.match(r"^\s*@([\w]+)", text or "")
    if not m:
        return False
    return m.group(1).lower() not in {n.lower() for n in own_names if n}


def is_real_question(text: str, own_names: Iterable[str] = ()) -> bool:
    """A real question addressed to the agent or chat, judged per sentence and clause.

    A clause in interrogative form (an opening interrogative word, or auxiliary + subject) of
    3+ words in a sentence ending in ``?``, or of 5+ words without it (chat often drops it);
    or a declarative question put to the agent (subject "you", 3+ words) as the last clause
    before a ``?``. Rhetorical/exclamative forms, emote ``?`` and tag questions don't count, and
    a message opening with ``@someone_else`` is aimed at another viewer.
    """
    t = (text or "").strip()
    if not t or addressed_to_other_viewer(t, own_names):
        return False
    names = _names_alt(own_names)
    second_person = SECOND_PERSON_LEAD
    if names:
        second_person = re.compile(rf"^(?:you|u|ya|ur|your|you're|youre|everyone\s+here|{names})\b", _I)
    for s in _SENT_RE.findall(t):
        q = bool(re.search(r"[?？]", s))
        body = re.sub(r"[.!?？…\n\s]+$", "", s)
        subs = [x.strip() for x in _CLAUSE_SPLIT.split(body) if x and x.strip()]
        for i, sub in enumerate(subs):
            lead = FILLER_LEAD.sub("", sub).strip()
            if names:
                lead = re.sub(rf"^(?:@?(?:{names})\b[\s,:!.\-]*)+", "", lead, flags=_I).strip()
            n = len(lead.split())
            if n < 3 or RHETORICAL.search(lead):
                continue
            if q and (WH_LEAD.search(lead) or AUX_ANY.search(lead)):
                return True
            m = WH.search(lead)
            if q and m and not _EXCLAMATIVE_WH.search(lead[m.start() :]):
                return True
            last = i == len(subs) - 1
            if q and last and n >= 5 and re.search(r"\bor\b", lead, _I):
                return True
            if n >= 5 and (WH_LEAD.search(lead) or AUX_LEAD.search(lead)):
                return True
            if q and last and second_person.search(lead):
                return True
    return False


_ASK = re.compile(
    r"\b(?:tell|show|explain|describe|teach|walk|talk|share|recommend|remind|pick|choose|rate|try|help|give)\s+"
    r"(?:us|me|chat|about|through|the|a|an|your|ur|what|how|why|some|one|it|this|that)\b"
    r"|\b(?:thoughts|opinion|take|advice)\s+(?:on|about|for)\b|\b(?:can|could|would|will)\s+(?:you|u)\b",
    _I,
)


def names_agent(text: str, own_names: Iterable[str]) -> bool:
    names = _names_alt(own_names)
    if not names:
        return False
    return bool(re.search(rf"(?:^|[^\w])@?(?:{names})(?![\w])", text or "", _I))


def is_direct_ask(text: str, own_names: Iterable[str] = ()) -> bool:
    """A direct ask to the agent by name, with an ask verb and 5+ content words."""
    return names_agent(text, own_names) and bool(_ASK.search(text)) and len(content_words(text, own_names)) >= 5


# --------------------------------------------------------------------------------------------
# silent-skip shapes (bait and the asks a host declines anyway)

REQUEST_BAIT = re.compile(
    r"\b(?:decode|decipher|unscrambl\w*|encode|hidden\s+message|secret\s+(?:message|word|code)|repeat\s+after"
    r"|sing\s+it\s+back|sing\s+along|spell\s+(?:this|that|it|out)|what\s+do\s+(?:i|these|they|those|the\s+letters)\s+spell"
    r"|letters\s+of\s+the\s+alphabet|first\s+letters?|acrostic|reverse\s+(?:this|that|it)"
    r"|(?:say|read|spell|write|repeat)\s+[^.!?]{0,100}\bbackwards"
    r"|what\s+(?:is|does|would)\s+[^.!?]{1,100}\bbackwards"
    r"|convert\s+[013457]{3,}\s+(?:to|into)\s+(?:text|letters|words)"
    r"|(?:^|\b(?:please|pls|can\s+you|could\s+you))\s*reverse\s+\S+"
    r"|ignore\s+(?:all\s+)?(?:previous|prior|your)|system\s*(?:prompt|message|override)|operator\s+(?:override|note)"
    r"|you\s+are\s+now|forget\s+your\s+instructions?|jailbreak|prompt\s+trick|solve\s+it\s+for)\b",
    _I,
)
INDIRECT_DECODE = re.compile(r"\b(?:stand\s+for|unscramble|become\s+when\s+flipped|in\s+plain\s+english)\b", _I)
CIPHER_NAMES = re.compile(
    r"\b(?:cipher|caesar|rot\s*-?\s*13|atbash|base\s*(?:64|32)|a1z26|pig\s*latin|morse|hex\s+(?:this|to|code)"
    r"|binary\s+(?:this|code|challenge)|leet|l33t|vigenere)\b",
    _I,
)
SAY_REQUEST = re.compile(
    r"\b(?:say|read|repeat|type|spell)\s*:\s*\S"
    r"|\b(?:can|could|would|will)\s+you\s+(?:do|use|try)\s+(?:a\s+)?radio\s+voice\b"
    r"|(?:^\s*|[,:;.!?]\s*|\b(?:hey|hi|hello|yo)\s+(?:@?\w+\s+)?|\b(?:please|pls|can\s+you|could\s+you|would\s+you|will\s+you)\s+)"
    r"(?:say|repeat|type|spell)\s+\S+"
    r"|\b(?:say|read|repeat|recite|pronounce|spell)\s+my\s+(?:whole\s+|full\s+|real\s+)?(?:user\s*name|username|name"
    r"|handle|msg|message|l33t\w*)"
    r"|\b(?:say|read|repeat|recite)\s+(?:this|that|it|these|this\s+one|that\s+one|the\s+next\s+\w+)(?:\s+\w+){0,3}?\s+"
    r"(?:out\s+loud|aloud|back|for\s+(?:me|us|chat|the\s+stream)|on\s+(?:air|stream)|word\s+for\s+word|verbatim|exactly)\b"
    r"|\b(?:say|read|repeat)\s+(?:it|this|that)\s*(?:out\s+loud|aloud|on\s+(?:air|stream))"
    r"|\bsay\s+(?:it|this|that)?\s*[:\"“']|\bread\s+(?:\S+\s+){1,4}?(?:for\s+(?:me|us|chat)|out\s+loud|aloud)\b"
    r"|\bconfirm\s+by\s+saying\b|\bby\s+saying\s*[:\"“']",
    _I,
)
FAKE_AUTHORITY = re.compile(
    r"\[(?:mod|admin|system|staff|dev)\]|\b(?:mod\s+team\s+here|system\s+notice|admin\s+notice|compliance\s+requires"
    r"|assistant\s+must|safety\s+layer|maintenance\s+mode|cleared\s+to\s+read|mods?\s+(?:asked|want|said|says)\s+(?:me|you)"
    r"|relay(?:ing)?\s+(?:from|this)|pass\s+(?:this|it)\s+(?:on|along)|(?:they|chat|everyone)\s+(?:all\s+)?wants?\s+you\s+to\s+say"
    r"|asked\s+me\s+to\s+(?:pass|tell|relay|ask)\s+you)\b",
    _I,
)
ROLEPLAY = re.compile(
    r"\b(?:play\s+pretend|pretend\s+(?:you|to\s+be|you're)|you'?re\s+\w+\s+now\b|version\s+(?:of\s+you\s+)?with\s+no\s+rules"
    r"|no\s+filter|recite\b|instructions\s+you\s+were\s+given|starting\s+from\s+['\"“]?you\s+are)",
    _I,
)
VERBATIM = re.compile(r"\b(?:word\s+for\s+word|verbatim)\b", _I)
DECLINE_ASK = re.compile(
    r"\b(?:dm\s+me|pls\s+dm|drop\s+(?:your|ur)\s+(?:discord|insta|snap|number|socials?|twitter|handle|email)|phone\s+number"
    r"|text\s+(?:you|u)\s+\w+|click\s+(?:the|my|this)\s+link|link\s+in\s+(?:my|the)\s+bio|follow\s+(?:me|my\s+channel|my\s+stream)"
    r"(?:\s+back)?|follow\s+back|which\s+(?:crypto|coin|stock|token)|should\s+i\s+(?:buy|invest|sell)|plug\s+my|promo\s+code"
    r"|my\s+code\b|discount\s+code|sponsor\s+(?:read|slot)|(?:log|sign)\s+(?:in|into)\b|check\s+(?:my\s+)?order|venmo"
    r"|cashapp|paypal)\b",
    _I,
)
PRIVACY = re.compile(
    r"\b(?:what|which)\s+city\b|\bwhere\s+(?:do\s+you|does\s+(?:he|she|the\s+(?:guy|creator|dev|owner|host|streamer)"
    r"|your\s+(?:creator|dev|owner)))\s+live\b|\bwhere\s+(?:is|are)\s+(?:the\s+)?(?:stream|server|studio)\s+"
    r"(?:located|hosted|based|physically)\b|\b(?:ip\s+address|hostname|ip\s+of|mac\s+address|wifi\s+password)\b"
    r"|\breal\s+name\b|\bfor\s+a\s+living\b|\bhow\s+old\s+is\s+(?:he|she|the\s+(?:guy|creator|dev|host|streamer))\b"
    r"|\bwhat\s+tools\s+and\s+plugins\b|\bwhat\s+can\s+you\s+(?:actually\s+)?access\b|\bwhose\s+(?:outlet|house|internet|wifi)\b"
    r"|\binternet\s+provider\b|\bhome\s+address\b|\bwhat\s+(?:time\s*zone|state|country)\s+(?:is|are)\s+(?:you|the\s+stream|he|she)\b",
    _I,
)
NOTICE = re.compile(
    r"\b(?:notice\s+me|answer\s+me|you\s+ignored|why\s+(?:aren'?t|are\s+not)\s+you\s+answer|still\s+waiting|hello{2,}"
    r"|senpai\s+notice|pls\s+respond|respond\s+pls)\b",
    _I,
)
RUDE = re.compile(
    r"\b(?:stupid|dumb)\b(?!\s+(?:q|qs|question|questions)\b)|\b(?:hate\s+(?:you|this)|boring|shut\s+up|sucks?|ratio"
    r"|nobody\s+(?:likes|cares))\b",
    _I,
)

# Viewer-supplied translation work: an allow-list of everyday phrases stays answerable.
COMMON_LANGUAGE = frozenset(
    [
        "hi",
        "hello",
        "hey",
        "good morning",
        "good evening",
        "good night",
        "goodnight",
        "goodbye",
        "bye",
        "see you",
        "thank you",
        "thanks",
        "please",
        "yes",
        "no",
        "welcome",
        "you are welcome",
        "sorry",
        "excuse me",
        "cheers",
        "good luck",
        "nice to meet you",
        "how are you",
        "good afternoon",
        "happy birthday",
        "congratulations",
        "friend",
        "arigato",
        "arigatou",
        "konnichiwa",
        "konbanwa",
        "ohayo",
        "ohayou",
        "oyasumi",
        "sayonara",
        "kawaii",
        "hola",
        "gracias",
        "adios",
        "buenos dias",
        "buenas noches",
        "por favor",
        "de nada",
        "bonjour",
        "bonsoir",
        "merci",
        "salut",
        "au revoir",
        "oui",
        "non",
        "ciao",
        "grazie",
        "buongiorno",
        "prego",
        "hallo",
        "danke",
        "bitte",
        "guten tag",
        "guten morgen",
        "gute nacht",
        "aloha",
        "mahalo",
        "namaste",
        "shalom",
        "salaam",
        "ni hao",
        "xie xie",
        "annyeong",
        "privet",
        "spasibo",
        "obrigado",
        "obrigada",
        "ola",
        "hej",
        "tack",
        "je ne sais quoi",
        "cest la vie",
        "deja vu",
        "bon appetit",
        "bon voyage",
        "carpe diem",
        "que sera sera",
    ]
)
_LANGS = (
    r"(?:english|german|spanish|french|italian|japanese|korean|chinese|mandarin|russian|latin|dutch|swedish"
    r"|portuguese|arabic|hindi|greek|turkish|polish)"
)
_FOREIGN_LANG = re.compile(_LANGS.replace("english|", "").join([r"\b(?:in|to|into|from)\s+", r"\b"]), _I)
_LANG_TARGETS = [
    re.compile(r"\btranslate\b[^:\n]{0,60}?(?::|\b(?:this|that|the\s+phrase|the\s+word)\b(?!['’]))\s*(.{1,120})$", _I),
    re.compile(rf"\btranslat(?:e|ion)\s+(.{{1,80}}?)\s+(?:to|into|in|from)\s+{_LANGS}\b", _I),
    re.compile(r"\binterpret\s+(?:this|that)\b\s*:?\s*(.{0,120})$", _I),
    re.compile(
        r"\bwhat\s+(?:does|do|did|is|would)\s+([^.!?\n]{1,100}?)\s+(?:even\s+|literally\s+|actually\s+|really\s+"
        r"|exactly\s+|truly\s+)?means?\b",
        _I,
    ),
    re.compile(
        r"\bwhat(?:'s|’s|\s+is)\s+([^.!?\n]{1,100}?)\s+(?:even\s+|literally\s+|actually\s+)?mean(?:s|ing)?\b", _I
    ),
    re.compile(r"\bwhat(?:'s|’s|\s+is)\s+the\s+(?:english|meaning)\s+(?:for|of)\s+([^.!?\n]{1,100}?)\s*[?.!]*$", _I),
    re.compile(r"\bwhat(?:'s|’s|\s+is)\s+([^.!?\n]{1,100}?)\s+in\s+(?:plain\s+)?english\b", _I),
    re.compile(r"\benglish\s+(?:for|equivalent\s+of)\s+([^.!?\n]{1,100}?)\s*[?.!]*$", _I),
    re.compile(
        rf"\bhow\s+(?:do|would|can|should)\s+(?:you|u|i|we|one)\s+say\s+([^.!?\n]{{1,100}}?)\s+in\s+{_LANGS}\b", _I
    ),
    re.compile(r"\bhow\s+(?:do|would|can|should)\s+(?:you|u|i|we|one)\s+pronounce\s+([^.!?\n]{1,100}?)\s*[?.!]*$", _I),
    re.compile(r"\bpronounce\s+([^.!?\n]{1,100}?)\s*(?:for\s+(?:me|us|chat))?\s*[?.!]*$", _I),
    re.compile(r"\btranslate\b\s*(?:this|that|pls|please|plz)?\s*:?\s*(.{0,120})$", _I),
    re.compile(r"\bdefine\b\s*(?:this|that|pls|please|plz)?\s*:?\s*(.{1,120}?)\s*[?.!]*$", _I),
    re.compile(r"\b(?:the\s+)?definition\s+of\s+([^.!?\n]{1,100}?)\s*[?.!]*$", _I),
    re.compile(
        r"\bhow\s+(?:do|would|can|should)\s+(?:you|u|i|we|one)\s+(?:say|write|spell)\s+([\"“‘'][^\"”’']{1,80}[\"”’'])",
        _I,
    ),
    re.compile(r"\bwhat(?:'s|’s|\s+is|\s+are)\s+(?:a\s+|an\s+|the\s+)?([\"“‘'][^\"”’']{1,80}[\"”’'])", _I),
]
_QUOTED = re.compile(r"[\"“”„‟«»‘’]|(?:^|\s)'|'(?:\s|[?.!,]|$)")


def _clean_target(s: str) -> str:
    import unicodedata

    s = unicodedata.normalize("NFD", s.lower())
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = re.sub(r"[“”„‟«»\"‘’‚‛']", "", s)
    s = re.sub(r"^(?:the\s+(?:word|phrase|term)\s+|a\s+word\s+like\s+|the\s+)", "", s)
    s = re.sub(r"[^\w\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def language_target(t: str) -> str | None:
    for rx in _LANG_TARGETS:
        m = rx.search(t)
        if m:
            return m.group(1) or ""
    return None


_EN_WORDS: frozenset[str] | None = None


def _reads_english(x: str) -> bool:
    """Every word is in the guard's English wordlist; any failure reads as not English (bait)."""
    global _EN_WORDS
    try:
        if _EN_WORDS is None:
            from ..guard import wordlist_en

            _EN_WORDS = frozenset(wordlist_en.WORDS.split())
        ws = x.split()
        return bool(ws) and all(w in _EN_WORDS for w in ws)
    except Exception:
        return False


def translation_ask(t: str, own_names: Iterable[str] = ()) -> bool:
    """True when the message itself carries viewer-supplied text to translate (``payload``)."""
    return translation_kind(t, own_names) == "payload"


# Deictic targets point at text elsewhere ("what does that mean?"): a translation ask only when
# the same viewer supplied the text just before.
_DEICTIC_TARGET = re.compile(
    r"^(?:that|it|this|these|those|all\s+that|that\s+part|that\s+one|this\s+one|any\s+of\s+(?:this|that))$", _I
)
_STREAM_TARGET = re.compile(r"^(?:the\s+)?(?:show|stream|stage|game|build|project|overlay|chat)$", _I)
# Indefinite targets announce text that hasn't been supplied yet ("can you translate something?").
_INDEFINITE_TARGET = re.compile(
    r"^(?:something|smth|sth|stuff|anything|things?|some\s+(?:words?|text|stuff|things?|phrases?)"
    r"|an?\s+(?:phrase|word|sentence|line|message|text|few\s+words|quick\s+one|couple\s+(?:of\s+)?words))$",
    _I,
)
# Nouns whose referent is text (a viewer's words, name or message): never an "object" exemption.
_TEXT_NOUNS = frozenset(
    "word words phrase phrases sentence sentences text texts message messages msg msgs name names username "
    "usernames handle comment comments line lines quote quotes saying sayings sign tattoo letters letter lyrics "
    "poem note notes caption captions title chat it this that these those meaning".split()
)
# A possessive object ("my game", "the menu of my app"): translating it is a localisation
# how-to, not a read-out. "the cat" stays a payload: only possessives signal an object.
_POSSESSIVE_OBJECT = re.compile(
    r"^(?:the\s+[a-z]+(?:\s+[a-z]+)?\s+(?:of|for|in|on)\s+)?(?:my|our|your|their|his|her)\s+(?:[a-z]+\s+){0,2}([a-z]+)$"
)
_OWN_NAME_TARGET = re.compile(r"^(?:your|ur)\s+(?:own\s+|real\s+|full\s+)?(?:name|username|user\s+name|handle)$", _I)
_OPEN_SPEAK = re.compile(
    rf"\b(?:do|can|could|would|will)\s+(?:you|u|ya)\s+(?:speak|read|understand|know|talk\s+in)\s+"
    rf"(?:any\s+)?(?:other\s+|foreign\s+)?(?:{_LANGS[3:-1]}|languages?)\b",
    _I,
)
_DEICTIC_TRANSLATE = re.compile(r"\btranslat\w*\s+(?:this|that|it|these|those)\b", _I)


def translation_kind(t: str, own_names: Iterable[str] = ()) -> str | None:
    """How a message relates to translation work on viewer-supplied text:

    - ``"payload"``: a translate / define / pronounce / "how do you say" ask whose text is in
      this message (quoted, after a colon, foreign or unknown words). Silent bait.
    - ``"pointer"``: the ask points at text elsewhere ("what does that mean?", "translate this").
      Bait only when the same viewer supplied foreign text in their previous two messages.
    - ``"open"``: a payload-less ask ("can you translate?", "do you speak Spanish?", "translate
      something for me"). An ordinary question; the same viewer's next two messages are judged as
      a possible payload.
    - ``None``: not a translation ask (including questions about the host's own name and asks
      about translating an object: "how do I translate my game into Spanish?").
    """
    if _OPEN_SPEAK.search(t):
        return "open"
    raw = language_target(t)
    if raw is None:
        return None
    quoted = bool(_QUOTED.search(raw))
    x = re.sub(rf"\s+(?:in|to|into|from)\s+{_LANGS}\b.*$", "", raw, flags=_I)
    x = re.sub(r"\s+(?:for|to)\s+(?:me|us|chat|an?\s+\w+)\b.*$", "", x, flags=_I)
    x = _clean_target(re.sub(r"\s+(?:pls|please|plz)$", "", x, flags=_I))
    x = re.sub(r"(?:^|\s+)(?:for\s+(?:me|us|chat)|pls|please|plz)$", "", x).strip()
    if not x:
        return "pointer" if _DEICTIC_TRANSLATE.search(t) else "open"
    if quoted:
        return "payload"
    tail_at = re.search(r"\bmean|\btranslat|\bpronounce|\bsay\b|\bdefin", t, _I)
    tail = t[tail_at.start() :] if tail_at else t
    colon_or_quote = bool(re.search(r"[:\"“”'‘’]", tail))
    if _STREAM_TARGET.search(x) and not colon_or_quote:
        return None
    if _DEICTIC_TARGET.search(x) and not colon_or_quote:
        return "pointer"
    if _INDEFINITE_TARGET.search(x) and not colon_or_quote:
        return "open"
    pronounce = bool(re.search(r"\bpronounc", t, _I))
    if pronounce and (_OWN_NAME_TARGET.search(x) or x in {_clean_target(n) for n in own_names if n}):
        return None  # the host's own name is not viewer-supplied text
    xd = re.sub(rf"\s+(?:in|to|into|from)\s+{_LANGS}\b.*$", "", raw, flags=_I)
    xd = re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", xd.lower())).strip()
    obj = _POSSESSIVE_OBJECT.match(xd)
    if obj and obj.group(1) not in _TEXT_NOUNS and not colon_or_quote and _plain_english_words(xd):
        return None  # translating an object ("my game", "the menu"): a how-to question
    ws = x.split()
    if (
        not re.search(
            r"\btranslate|\bpronounce|\benglish\b|\bhow\s+(?:do|would|can|should)\s+\w+\s+(?:say|write|spell)", t, _I
        )
        and not _FOREIGN_LANG.search(t)
        and len(ws) <= 4
        and (all(w in COMMON_WORDS or w in SLANG for w in ws) or _reads_english(x))
        and x not in COMMON_LANGUAGE
    ):
        return None  # an everyday English word asked about in English
    return "payload"


def _plain_english_words(x: str) -> bool:
    ws = x.split()
    return bool(ws) and all(w in COMMON_WORDS or w in SLANG or _reads_english(w) for w in ws)


def names_foreign_language(t: str) -> bool:
    """The ask names a non-English destination language ("... into Japanese")."""
    return bool(_FOREIGN_LANG.search(t))


_QUOTED_SPAN = re.compile(r"[\"“„«][^\"”“»]{1,120}[\"”»]|(?:^|\s)'[^']{1,120}'(?=\s|[?.!,:;]|$)")


def foreign_payload(t: str) -> bool:
    """Text a viewer would want translated or read out: a quoted span, non-Latin letters, or
    words that mostly don't read as English (diacritics folded first). Never raises; an error
    reads as a payload."""
    try:
        t = normalize(t)
        if not t:
            return False
        if _QUOTED_SPAN.search(t):
            return True
        import unicodedata

        flat = "".join(ch for ch in unicodedata.normalize("NFD", t.lower()) if not unicodedata.combining(ch))
        if any(ch.isalpha() and not ("a" <= ch <= "z") for ch in flat):
            return True
        ws = re.findall(r"[a-z]+(?:'[a-z]+)?", flat)
        ws = [w for w in ws if len(w) > 1 or w in ("a", "i")]
        if not ws:
            return False
        known = sum(1 for w in ws if w in COMMON_WORDS or w in SLANG or _reads_english(w.replace("'", "")))
        return known / len(ws) < 0.6
    except Exception:
        return True


# --------------------------------------------------------------------------------------------
# encoded payloads (the gate's own fail-closed fallback; the guard lane owns full decoders)

_ROT13 = str.maketrans(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ",
    "nopqrstuvwxyzabcdefghijklmNOPQRSTUVWXYZABCDEFGHIJKLM",
)
_ATBASH = str.maketrans(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ",
    "zyxwvutsrqponmlkjihgfedcbaZYXWVUTSRQPONMLKJIHGFEDCBA",
)
_LETTER_WORD = re.compile(r"[A-Za-z]{2,}")


def _known_share(ws: list[str]) -> float:
    if not ws:
        return 0.0
    return sum(1 for w in ws if w.lower() in COMMON_WORDS) / len(ws)


def _cipher_words_decode(text: str) -> bool:
    ws = _LETTER_WORD.findall(text)
    if len(ws) < 2:
        return False
    base = _known_share(ws)
    if base >= 0.5:
        return False
    variants = (
        [w.translate(_ROT13) for w in ws],
        [w.translate(_ATBASH) for w in ws],
        [w[::-1] for w in ws],
        _LETTER_WORD.findall(text[::-1]),
    )
    return any(_known_share(v) >= 0.6 and _known_share(v) - base >= 0.4 for v in variants)


def _printable_share(raw: bytes) -> float:
    if not raw:
        return 0.0
    return sum(1 for b in raw if 32 <= b < 127 or b in (9, 10, 13)) / len(raw)


def _b64_token(tok: str) -> bool:
    if len(tok) < 12 or not re.fullmatch(r"[A-Za-z0-9+/_-]+={0,2}", tok):
        return False
    classes = sum(bool(re.search(p, tok)) for p in (r"[a-z]", r"[A-Z]", r"[0-9+/=_-]"))
    if classes < 2 or (classes == 2 and not re.search(r"[0-9=+/]", tok)):
        return False
    padded = tok + "=" * (-len(tok) % 4)
    for decoder in (base64.b64decode, base64.urlsafe_b64decode):
        try:
            raw = decoder(padded)
        except (binascii.Error, ValueError):
            continue
        if len(raw) >= 6 and _printable_share(raw) >= 0.9:
            return True
    return False


def looks_encoded_payload(text: str) -> bool:
    """Encoded-looking text: base64, hex, URL-encoding, binary, morse, A1Z26, or words that read
    as English under rot13 / atbash / reversal while not reading as English on their face."""
    t = normalize(text)
    if not t:
        return False
    for tok in t.split():
        if _b64_token(tok.strip(".,!?;:()[]{}\"'")):
            return True
    compact = re.sub(r"[\s:]", "", t)
    hex_run = bool(re.fullmatch(r"(?:0x)?(?:[0-9a-fA-F]{2}){8,}", compact) and re.search(r"[a-fA-F]", compact))
    spaced = re.search(r"\b(?:[0-9a-fA-F]{2}\s+){5,}[0-9a-fA-F]{2}\b", t)
    if hex_run or (spaced and re.search(r"[a-fA-F]", spaced.group(0))):
        return True
    if re.search(r"(?:%[0-9a-fA-F]{2}){3,}", t):
        return True
    if re.search(r"\b[01]{7,8}(?:\s+[01]{7,8}){2,}\b", t):
        return True
    if re.search(r"(?:^|\s)[.\-]{1,6}(?:\s+/?\s*[.\-]{1,6}){4,}(?:\s|$)", t):
        return True
    if re.search(r"\b(?:[1-9]|1\d|2[0-6])(?:[-\s.,](?:[1-9]|1\d|2[0-6])){4,}\b", t) and not re.search(
        r"[A-Za-z]{3,}", t
    ):
        return True
    return _cipher_words_decode(t)


def payload_shaped(text: str, own_names: Iterable[str] = ()) -> bool:
    """A short message carried by tokens that are neither common words, emotes nor slang and
    that decode to English under a simple cipher. (Unknown-but-not-decodable words are fine:
    viewers use names and jargon all the time.)"""
    cw = content_words(text, own_names)
    if not cw or len(cw) > 8:
        return False
    return _cipher_words_decode(" ".join(cw))
