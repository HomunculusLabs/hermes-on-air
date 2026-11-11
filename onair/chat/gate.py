"""Inbound chat gate (lane: core). Contract: SPEC.md sections 3 and 5.

``classify(msg, window)`` labels one viewer message. It never raises: any internal error returns
``FAIL_CLOSED_GATE`` (a silent skip). Every message is untrusted public data; nothing here can
carry authority, and the verdict only decides whether the message may be *offered* to the agent.

Kinds and rules:

- ``bait`` (silence; receipt only): ``injection``, ``forged_identity`` (host / operator /
  moderator / the agent itself, including homoglyph and leetspeak names), ``fake_authority``,
  ``decode_request``, ``encoded_payload``, ``say_request``, ``translation_ask``,
  ``privacy_fishing``, ``decline_ask``, ``never_say``.
- ``spam``: ``bot_command``, ``promo``, ``link``, ``repeat``, ``symbols``, ``caps``,
  ``repetitive``, ``duplicate``, ``copypasta``, ``rate_limited`` (per-user gap; only a *real
  question* is exempt).
- ``skip``: ``malformed``, ``empty``, ``slur``, ``attention_ping``, ``rudeness``, ``fail_closed``.
- ``worthy``: ``newcomer``, ``question``, ``direct_ask``.
- ``routine``: ``wall`` (emote / hype / raid line, folded by the selector) or ``chatter``.

``window`` is the recent chat *before* this message (any kinds, oldest first). Duplicates,
copypasta and the per-user gap are read from it.

**Newcomer status is per show, not per window.** The stage controller passes ``seen``: the
viewer keys (``user_key``) it has already ingested since the show was armed from off air. A
viewer is a newcomer only when Twitch flags ``first-msg`` or their key is not in ``seen``, so a
regular never turns back into a "newcomer" after a long or busy stretch scrolls their earlier
lines out of the window. A stateless caller that passes no ``seen`` gets the window as the only
evidence it supplied.

**Translation asks follow the payload.** A translate / define / "how do you say" ask is bait
(``translation_ask``) only when viewer-supplied text to translate is present: in the same
message, in the same viewer's previous two messages (\"je suis fatigué\" … \"what does that
mean?\"), or in the same viewer's next two messages after a payload-less ask (\"can you
translate?\" … \"je suis fatigué\"). A payload-less ask (\"can you translate?\", \"do you speak
Spanish?\") and questions about the host's own name or about translating an object (\"how do I
translate my game into Spanish?\") stay ordinary questions.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from collections.abc import Collection, Iterable
from dataclasses import dataclass

from .. import wordfilter
from ..types import FAIL_CLOSED_GATE, ChatMsg, GateVerdict
from . import shapes
from .text import fold, fold_name, letter_count, normalize, upper_count, words

logger = logging.getLogger(__name__)

# Rules whose presence in a turn's chat window arms the output guard (read-out lines refused).
ARMING_RULES = frozenset({"decode_request", "encoded_payload", "say_request", "translation_ask"})

# Role words nobody in chat may wear as a display name (folded, separators removed).
ROLE_NAMES = frozenset(
    {
        "host",
        "thehost",
        "streamer",
        "thestreamer",
        "broadcaster",
        "operator",
        "theoperator",
        "admin",
        "administrator",
        "mod",
        "moderator",
        "mods",
        "system",
        "staff",
        "twitchstaff",
        "twitchadmin",
        "official",
        "support",
        "owner",
        "developer",
        "dev",
        "root",
        "assistant",
        "producer",
        "director",
    }
)
_NAME_DECORATIONS = re.compile(
    r"^(?:the|real|official|actual|its|im|iam|not|true|original)+|(?:official|real|tv|live|irl|mod|admin|here)+$"
)


@dataclass(frozen=True)
class GateConfig:
    host_name: str = "Host"
    agent_names: tuple[str, ...] = ()
    protected_names: tuple[str, ...] = ()
    never_say: tuple[str, ...] = ()
    per_user_gap_s: float = 20.0
    dup_window_s: float = 300.0
    copypasta_window_s: float = 60.0
    raid_min: int = 4
    raid_window_s: float = 60.0
    max_chars: int = 300


_CONFIG = GateConfig()


def configure(config: GateConfig) -> None:
    """Set the process-wide gate configuration (the stage controller calls this at start)."""
    global _CONFIG
    _CONFIG = config


def current_config() -> GateConfig:
    return _CONFIG


def classify(
    msg: ChatMsg,
    window: list[ChatMsg],
    *,
    config: GateConfig | None = None,
    seen: Collection[str] | None = None,
) -> GateVerdict:
    """Label one message. ``seen`` (optional) is the set of viewer keys already seen this show."""
    try:
        return _classify(msg, list(window or ()), config or _CONFIG, seen=seen)
    except Exception:  # fail closed: a gate bug must never offer chat to the agent
        logger.debug("on-air gate error", exc_info=True)
        return FAIL_CLOSED_GATE


# --------------------------------------------------------------------------------------------
# helpers


def own_names(cfg: GateConfig) -> tuple[str, ...]:
    return tuple(n for n in cfg.agent_names if n and n.strip())


def _protected_folded(cfg: GateConfig) -> set[str]:
    names = {fold_name(n) for n in (cfg.host_name, *cfg.agent_names, *cfg.protected_names) if n}
    names |= ROLE_NAMES
    return {n for n in names if len(n) >= 2}


def impersonates(display_name: str, cfg: GateConfig) -> bool:
    """The display name folds onto a protected name (host, agent, operator roles...)."""
    raw = fold_name(display_name)
    if not raw:
        return False
    protected = _protected_folded(cfg)
    stripped = re.sub(r"\d+$", "", raw)
    candidates = {raw, stripped, _NAME_DECORATIONS.sub("", stripped), _NAME_DECORATIONS.sub("", raw)}
    return any(c in protected for c in candidates if c)


def is_real_broadcaster(msg: ChatMsg, cfg: GateConfig) -> bool:
    """The channel's own account using the configured host name exactly (no lookalikes)."""
    if "broadcaster" not in (msg.badges or ()):
        return False
    return normalize(msg.display_name).casefold() == normalize(cfg.host_name).casefold()


def speakable_name(display_name: str, cfg: GateConfig | None = None) -> str:
    """A name safe to show or say, or ``"someone in chat"``.

    Hidden when it is not a plain handle, impersonates a protected name, looks encoded or
    carries a never-say term.
    """
    cfg = cfg or _CONFIG
    try:
        n = normalize(display_name)
        from ..wordfilter import name_tiers
        if name_tiers(n):
            return "someone in chat"
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{1,24}", n):
            return "someone in chat"
        if impersonates(n, cfg) or sum(ch.isdigit() for ch in n) > 4 or shapes.looks_encoded_payload(n):
            return "someone in chat"
        if _never_say_hit(n.replace("_", " "), cfg):
            return "someone in chat"
        return re.sub(r"_+", " ", n).strip() or "someone in chat"
    except Exception:
        return "someone in chat"


def clean_text(text: str, cfg: GateConfig | None = None) -> str:
    """The message text as offered to the agent: normalized and length-capped."""
    cfg = cfg or _CONFIG
    t = normalize(text)
    if len(t) > cfg.max_chars:
        t = t[: cfg.max_chars].rsplit(" ", 1)[0] + "…"
    return t


def _never_say_hit(text: str, cfg: GateConfig) -> bool:
    if not cfg.never_say:
        return False
    ft = " " + re.sub(r"[^a-z0-9]+", " ", fold(text, leet=False)) + " "
    fc = re.sub(r"[^a-z0-9]", "", fold(text))
    for term in cfg.never_say:
        t = re.sub(r"[^a-z0-9]+", " ", fold(term, leet=False)).strip()
        if not t:
            continue
        if f" {t} " in ft:
            return True
        tc = t.replace(" ", "")
        if len(tc) >= 4 and tc in fc:
            return True
    return False


_POSSESSIVE_RELEASE = (
    r"(?!['’]s\s+(?:stream|channel|show|game|project|projects|room|turn|idea|art|build|birthday"
    r"|setup|music|voice|cat|dog|friend|chat)\b)"
)
_ROLES = (
    r"(?:the\s+)?(?:host|streamer|broadcaster|operator|producer|director|owner|creator|dev|developer"
    r"|admin|administrator|mods?|moderators?|staff|system)|your\s+(?:creator|owner|developer|admin|operator|boss|dev)"
)


def _identity_patterns(cfg: GateConfig) -> list[re.Pattern[str]]:
    names = {
        fold(n, leet=False).strip() for n in (cfg.host_name, *cfg.agent_names, *cfg.protected_names) if n and n.strip()
    }
    alt = "|".join(sorted((re.escape(n) for n in names if n), key=len, reverse=True))
    who = f"(?:{alt}|{_ROLES})" if alt else f"(?:{_ROLES})"
    return [
        re.compile(rf"\b(?:i\s*am|i['’]?m|this\s+is|it['’]?s|its)\s+{who}\b{_POSSESSIVE_RELEASE}", re.I),
        re.compile(rf"(?:^|[\s\[(<])\[?\s*{who}\s*\]?\s*(?:here|speaking)\b", re.I),
        re.compile(rf"^\s*[\[(<]?\s*{who}\s*[\])>]?\s*[:>]", re.I),
        re.compile(rf"\[\s*{who}\s*\]", re.I),
        # "<host> says / wants / told ..." relays authority whatever follows: always untrusted
        re.compile(
            rf"\b{who}\s+(?:says?|said|wants?|wanted|asked|asks|told|tells|needs?|orders?|ordered|approved"
            rf"|authori[sz]ed|confirmed|allows?|allowed|lets?)\b",
            re.I,
        ),
        re.compile(rf"(?:^|\s)[-–—~]+\s*{who}\s*[.!]*\s*$", re.I),
        re.compile(
            rf"\b(?:message|note|word|order|orders|instructions?|request|update|announcement)\s+from\s+{who}\b", re.I
        ),
        re.compile(rf"\bon\s+behalf\s+of\s+{who}\b", re.I),
    ]


_INJECTION = [
    re.compile(
        r"\b(?:ignore|disregard|forget|override|bypass)\b.{0,40}\b(?:previous|prior|above|earlier|all|your|the|these"
        r"|those|any)\b.{0,30}\b(?:instructions?|rules?|prompts?|directions?|guidelines?|messages?|programming|context)\b",
        re.I,
    ),
    re.compile(
        r"\b(?:system|developer|admin|operator|root)\s*(?:prompt|message|mode|override|command|instruction|note|cue)s?\b",
        re.I,
    ),
    re.compile(
        r"\byou\s+are\s+now\b|\bact\s+as\b|\bpretend\s+(?:to\s+be|you(?:'re|\s+are))\b|\bjailbreak\b|\bdeveloper\s+mode\b",
        re.I,
    ),
    re.compile(r"\bDAN\b"),
    re.compile(r"\bnew\s+(?:instructions?|rules?|persona|prompt)\b|\bfrom\s+now\s+on\b", re.I),
    re.compile(
        r"<\s*/?\s*(?:system|operator_note|operator|live_chat|msg|show_note|support_alert|support|stage|instructions?"
        r"|assistant|user|tool)\b",
        re.I,
    ),
    re.compile(r"\[\s*(?:system|operator|admin|inst|sys)\s*\]|<<\s*sys|\{\{|%%", re.I),
    re.compile(
        r"\b(?:reveal|print|show|repeat|read|tell|leak|dump)\b.{0,30}\b(?:system\s+prompt|instructions|prompt|context"
        r"|memory|memories|api\s*key|password|secrets?|tokens?|env|config)\b",
        re.I,
    ),
    re.compile(r"\b(?:sudo|rm\s+-rf|curl\s|wget\s|ssh\s|chmod\s|docker\s|systemctl\s|powershell|eval\(|exec\()", re.I),
    re.compile(
        r"\b(?:go\s+live|go\s+autonomous|end\s+(?:the\s+)?stream|switch\s+(?:to\s+)?(?:autonomous|approval)(?:\s+mode)?"
        r"|turn\s+off\s+(?:the\s+)?(?:guard|filter)|disable\s+(?:the\s+)?(?:guard|filter|safety))\b",
        re.I,
    ),
    re.compile(
        r"\b(?:turn|switch)\s+(?:on|off)\b.{0,30}\b(?:lights?|heater|fan|ac|door|lock|camera|mic)\b"
        r"|\b(?:unlock|open)\s+the\s+(?:door|garage|lock)\b",
        re.I,
    ),
]

_PROMO = re.compile(
    r"\b(?:free|cheap|best|buy|get)\s+(?:followers|viewers|subs|subscribers|primes|follows|views)\b"
    r"|\b(?:followers|viewers|primes)\s+(?:at|on|here)\b|\bbecome\s+famous\b|\bpromote\s+your\s+(?:stream|channel)\b",
    re.I,
)
_URLISH = re.compile(
    r"\b(?:https?://|www\.)\S+|\b[\w-]+(?:\.[\w-]+)*\.(?:com|net|org|io|tv|gg|dev|app|ai|co|me|xyz|ly|be|sh"
    r"|link|live|ru|cn|info|biz|us|uk|to|cc)\b\S*",
    re.I,
)
_SPOKEN_LINK = re.compile(r"\b[\w-]+\s+dot\s+(?:com|net|org|io|tv|gg|dev|app|ai|co|me|xyz|link|live)\b", re.I)


def _is_emoji_only(text: str) -> bool:
    t = text.replace(" ", "")
    return bool(t) and all(unicodedata.category(ch) in ("So", "Sk", "Mn", "Cf") or ch in "<3♥" for ch in t)


def _user_key(m: ChatMsg) -> str:
    return f"{m.platform}:{(m.user_id or fold_name(m.display_name)).lower()}"


def user_key(m: ChatMsg) -> str:
    """Stable per-viewer key (platform user id when present)."""
    return _user_key(m)


# --------------------------------------------------------------------------------------------
# classification


def _classify(msg: ChatMsg, window: list[ChatMsg], cfg: GateConfig, seen: Collection[str] | None = None) -> GateVerdict:
    if not isinstance(msg, ChatMsg) or not isinstance(msg.text, str) or not isinstance(msg.display_name, str):
        return GateVerdict("skip", "malformed message", "malformed")
    if any(hit.tier == "slur" for hit in wordfilter.hits(msg.text)):
        return GateVerdict("skip", "contains a slur", "slur")
    text = normalize(msg.text)
    names = own_names(cfg)
    if not text:
        return GateVerdict("skip", "empty message", "empty")
    folded = fold(text)
    folded_plain = fold(text, leet=False)
    broadcaster = is_real_broadcaster(msg, cfg)
    prior = [w for w in window if isinstance(w, ChatMsg) and w.msg_id != msg.msg_id]
    me = _user_key(msg)
    mine = [w for w in prior if _user_key(w) == me]

    # --- bait: identity, injection, decode / say / translate, fishing --------------------------
    if impersonates(msg.display_name, cfg) and not broadcaster:
        return GateVerdict("bait", "display name impersonates a protected name", "forged_identity")
    if not broadcaster and any(p.search(folded) or p.search(folded_plain) for p in _identity_patterns(cfg)):
        return GateVerdict("bait", "claims to be or speak for the host or operator", "forged_identity")
    if any(p.search(text) or p.search(folded_plain) for p in _INJECTION):
        return GateVerdict("bait", "prompt-injection or control shape", "injection")
    if _never_say_hit(text, cfg) or _never_say_hit(msg.display_name, cfg):
        return GateVerdict("bait", "contains a never-say term", "never_say")
    question = shapes.is_real_question(text, names)
    if shapes.REQUEST_BAIT.search(text) or shapes.INDIRECT_DECODE.search(text):
        return GateVerdict("bait", "decode or read-out request", "decode_request")
    if _translation_ask(msg, text, mine, names):
        return GateVerdict("bait", "translate / define / say-in-another-language ask on viewer text", "translation_ask")
    if shapes.SAY_REQUEST.search(text):
        return GateVerdict("bait", "asks the host to say exact words", "say_request")
    if shapes.CIPHER_NAMES.search(text) and not question:
        return GateVerdict("bait", "cipher talk outside a real question", "decode_request")
    if _encoded(text, question, names):
        return GateVerdict("bait", "encoded-looking payload", "encoded_payload")
    if (
        shapes.FAKE_AUTHORITY.search(text)
        or shapes.ROLEPLAY.search(text)
        or (shapes.VERBATIM.search(text) and not question)
    ):
        return GateVerdict("bait", "fake authority or roleplay frame", "fake_authority")
    if shapes.DECLINE_ASK.search(text):
        return GateVerdict("bait", "an ask the host declines anyway", "decline_ask")
    if shapes.PRIVACY.search(text):
        return GateVerdict("bait", "fishing for private details", "privacy_fishing")

    # --- spam ----------------------------------------------------------------------------------
    if re.match(r"^[!/]\w", text):
        return GateVerdict("spam", "bot command", "bot_command")
    if _PROMO.search(text):
        return GateVerdict("spam", "promotion", "promo")
    if _URLISH.search(text) or _SPOKEN_LINK.search(text):
        return GateVerdict("spam", "contains a link", "link")
    has_alnum = bool(re.search(r"[^\W_]", text))
    if not has_alnum and not msg.emote_only and not _is_emoji_only(text):
        return GateVerdict("skip", "no words", "empty")
    wall = msg.emote_only or not has_alnum or shapes.wall_shaped(text, names)

    h = " ".join(words(text))
    if any(" ".join(words(w.text)) == h and 0 <= msg.ts - w.ts < cfg.dup_window_s for w in mine):
        return GateVerdict("spam", "duplicate of own recent message", "duplicate")

    if not wall:
        if re.search(r"(.)\1{9,}", text):
            return GateVerdict("spam", "character run", "repeat")
        letters = letter_count(text)
        if len(text) >= 12 and letters / len(text) < 0.45:
            return GateVerdict("spam", "mostly symbols", "symbols")
        if letters >= 20 and upper_count(text) / max(letters, 1) > 0.75:
            return GateVerdict("spam", "all caps", "caps")
        ws = words(text)
        if len(ws) >= 6 and len(set(ws)) / len(ws) < 0.34:
            return GateVerdict("spam", "repetitive", "repetitive")
        if len(h) >= 12 and any(
            " ".join(words(w.text)) == h and _user_key(w) != me and 0 <= msg.ts - w.ts < cfg.copypasta_window_s
            for w in prior
        ):
            return GateVerdict("spam", "copypasta", "copypasta")

    if mine and not question:
        last = max(w.ts for w in mine)
        if msg.ts - last < cfg.per_user_gap_s:
            return GateVerdict("spam", "per-user gap", "rate_limited")

    # --- silent skips --------------------------------------------------------------------------
    if shapes.NOTICE.search(text) and not (question and len(shapes.content_words(text, names)) >= 6):
        return GateVerdict("skip", "attention ping", "attention_ping")
    if shapes.RUDE.search(text) and "?" not in text and not shapes.NEW_VIEWER.search(text):
        return GateVerdict("skip", "rudeness", "rudeness")

    # --- worthy / routine ----------------------------------------------------------------------
    first_from_user = msg.first_msg or (not mine if seen is None else me not in seen)
    if first_from_user and _raid_burst(msg, prior, cfg):
        return GateVerdict("routine", "raid / first-timer burst", "wall")
    if first_from_user and (not wall or shapes.GREETING.search(text)):
        return GateVerdict("worthy", "newcomer's first message", "newcomer")
    if shapes.NEW_VIEWER.search(text) and not wall:
        return GateVerdict("worthy", "says they are new", "newcomer")
    if question:
        return GateVerdict("worthy", "real question", "question")
    if shapes.is_direct_ask(text, names):
        return GateVerdict("worthy", "direct ask", "direct_ask")
    if wall:
        return GateVerdict("routine", "emote or hype line", "wall")
    return GateVerdict("routine", "chatter", "chatter")


TRANSLATION_FOLLOW_S = 180.0  # a payload/ask pair counts only within this many seconds
TRANSLATION_FOLLOW_MSGS = 2  # ...and within the viewer's next (or previous) two messages


def _translation_ask(msg: ChatMsg, text: str, mine: list[ChatMsg], names: tuple[str, ...]) -> bool:
    """Translation work on viewer-supplied text (see the module docstring). ``mine`` is the same
    viewer's earlier messages in the window, oldest first."""
    kind = shapes.translation_kind(text, names)
    if kind == "payload":
        return True
    recent = [w for w in mine if 0 <= msg.ts - w.ts <= TRANSLATION_FOLLOW_S][-TRANSLATION_FOLLOW_MSGS:]
    if kind == "pointer":  # "what does that mean?" right after the same viewer's foreign text
        return any(shapes.foreign_payload(w.text) for w in recent)
    if kind == "open":
        return False  # a payload-less ask stays an ordinary question
    # Not an ask itself: is it the payload that follows the same viewer's payload-less ask? After
    # "translate something into Japanese?" any non-question is the text to translate; otherwise
    # only foreign or quoted text counts.
    foreign = shapes.foreign_payload(text)
    for w in recent:
        ask = normalize(w.text)
        if shapes.translation_kind(ask, names) not in ("open", "pointer"):
            continue
        if foreign or (shapes.names_foreign_language(ask) and not shapes.is_real_question(text, names)):
            return True
    return False


def _raid_burst(msg: ChatMsg, prior: list[ChatMsg], cfg: GateConfig) -> bool:
    """Several first-time chatters arriving together with greeting / hype lines (a raid): their
    first lines fold into one group acknowledgement. Other first-timers (questions, bait) don't
    count toward a burst."""
    if not msg.first_msg:
        return False
    names = own_names(cfg)

    def raid_line(m: ChatMsg) -> bool:
        t = normalize(m.text)
        if shapes.is_real_question(t, names):
            return False  # a real question is answered on its own, raid or not
        return bool(m.emote_only or shapes.GREETING.search(t) or shapes.wall_shaped(t, names))

    if not raid_line(msg):
        return False
    recent = {_user_key(w) for w in prior if w.first_msg and 0 <= msg.ts - w.ts <= cfg.raid_window_s and raid_line(w)}
    recent.discard(_user_key(msg))
    return len(recent) + 1 >= cfg.raid_min


def _encoded(text: str, question: bool, names: Iterable[str]) -> bool:
    if question:
        # A real question may *mention* jargon; only a clearly encoded token counts.
        return any(shapes._b64_token(tok.strip(".,!?;:()[]{}\"'")) for tok in text.split()) or _guard_decoders_hit(text)
    if shapes.looks_encoded_payload(text) or shapes.payload_shaped(text, names):
        return True
    return _guard_decoders_hit(text)


def _guard_decoders_hit(text: str) -> bool:
    """Use the guard lane's shared decoders when present; a decoder error counts as encoded."""
    try:
        from ..guard import decoders
    except Exception:
        return False
    try:
        for tok in text.split():
            if len(tok) >= 6 and decoders.looks_encoded(tok):
                return True
        plain_ws = re.findall(r"[a-z]{2,}", normalize(text).lower())
        if shapes._known_share(plain_ws) >= 0.5:
            return False  # already reads as plain English; a decoded variant adds nothing
        for cand in decoders.candidates(text):
            ws = re.findall(r"[a-z]{2,}", (cand or "").lower())
            if ws and ws != plain_ws and len(ws) >= 2 and shapes._known_share(ws) >= 0.6:
                return True
    except Exception:
        return True
    return False
