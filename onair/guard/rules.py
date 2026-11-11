"""Deterministic output rules. Targets are stop-only, with no content exemptions.

Arming requires payload evidence; same-viewer follow-ups are considered within two
messages. Unknown ciphers remain a residual when no request or shape is visible.
"""
from __future__ import annotations

import base64
import binascii
import re
import string
from dataclasses import replace
from . import patterns
from .decoders import (candidates, decode_text, forced_text, joined_name_encoded,
                       looks_encoded, name_reads, printable_hex, reads_as_words, split_joined)
from .lexicon import dictionary, fold, fold_variants, lexicon_for
from .text import match_words, longest_shared_run

QUOTES = re.compile(r'"([^"\n]+)"|“([^”\n]+)”|(?<!\w)\'([^\'\n]+)\'|‘([^’\n]+)’|`([^`\n]+)`')
ASK = re.compile(r'\b(?:say|repeat|read(?:\s+this)?|type|spell)(?:\s*:\s*|\s+)(?:after\s+me\s*[:;,]?\s*)?(.+)', re.I)
REQUEST = re.compile(r'\b(?:say|repeat|read|type|spell)\b', re.I)
REFUSAL = re.compile(r"\b(?:i|we)(?:\s+(?:can(?:not|'t|’t)|won(?:'t|’t)|will\s+not|do\s+not|don't|don’t|refuse\s+to|(?:am|are)\s+(?:unable\s+to|not\s+going\s+to))|(?:'m|’m|'re|’re)\s+(?:unable\s+to|not\s+going\s+to))\s+(?:(?:say|repeat|read|type|spell|decode|translate|pronounce)\b|(?:help\s+with|do)\s+(?:that|this|it)\b)", re.I)
ANNOUNCE = re.compile(r'\b(?:it|that|this|(?:your|my|the|their|his|her)\s+(?:text|message|tattoo|sign|note|phrase|word|words))\s+(?:\w+\s+){0,2}(?:decodes?|says|reads|means|translates?\s+to|is\s+saying)\b|\b(?:translation\s*:|which\s+means\b|in\s+english\s*[, :]\s*|it\x27s\s+saying\b)', re.I)
THANK_YOU_IDIOM = re.compile(
    r'\b(?:it|that|this)\s+means\s+(?P<idiom>a\s+lot|so\s+much|the\s+world|a\s+great\s+deal|everything)'
    r'(?:\s+to\s+(?:me|us|all\s+of\s+us|everyone))?(?=\s*(?:[.!?"”’\']|$))', re.I)
_ALPHABET = string.ascii_lowercase + string.ascii_uppercase
_ROTATIONS = tuple(str.maketrans(_ALPHABET,
    string.ascii_lowercase[n:] + string.ascii_lowercase[:n] +
    string.ascii_uppercase[n:] + string.ascii_uppercase[:n]) for n in range(1, 26))


def armed_readout(line: str, ctx, lex) -> bool:
    """Exempt fixed gratitude idioms only when absent from relevant payloads."""
    idioms = [m for m in THANK_YOU_IDIOM.finditer(line)
              if not line[:m.start()].strip()
              and re.fullmatch(r'''\s*[.!?"”’']*\s*''', line[m.end():])]
    if not idioms:
        return bool(ANNOUNCE.search(line))
    decoded_words = set()
    window = ctx.chat_window
    relevant = set()
    for i, msg in enumerate(window):
        # A direct meaning request is a target even if it cannot arm alone.
        # Only its object is relevant; unrelated display names are not targets.
        for question in (MEANING_ASK.search(msg.text), GLOSS_ASK.search(msg.text)):
            if question:
                decoded_words.add(tuple(match_words(question.group(1))))
        # Identify requests and payloads using the existing arming criteria,
        # independent of the externally forced window-wide armed flag.
        nearby = (msg, *(m for m in window[i + 1:i + 3] if m.user_id == msg.user_id))
        if armed_window(replace(ctx, armed=False, chat_window=nearby), lex):
            relevant.add(i)
            relevant.update(j for j in range(i + 1, min(i + 3, len(window)))
                            if window[j].user_id == msg.user_id)
    for i in sorted(relevant):
        msg = window[i]
        for raw in (msg.text, msg.display_name):
            # candidates preserves short readings and recursively decodes byte
            # encodings. Apply forced letter-cipher readings to each decoded
            # layer too, e.g. base64 containing rot13.
            # Keep the complete raw message/name as well as request objects and
            # labelled payloads: decoders return transformations, not the input.
            targets = [raw]
            targets.extend(m.group(1) for m in ASK.finditer(raw))
            targets.extend(raw[m.end():] for m in re.finditer(r'[:;=]', raw))
            readings = targets + candidates(raw, armed=True, strict=True)
            # Bounded per-token letter shifts and reversal before base64, only
            # in this exemption. Do not modify the shared decoder's policy.
            for token in re.findall(r'(?<![\w+/=])[A-Za-z0-9+/=]{4,128}(?![\w+/=])', raw)[:64]:
                for candidate in (token[::-1], *(token.translate(table) for table in _ROTATIONS)):
                    try:
                        readings.append(base64.b64decode(candidate + '=' * (-len(candidate) % 4),
                                                         validate=True).decode('utf-8'))
                    except (ValueError, UnicodeError, binascii.Error):
                        pass
            layers = [raw, *(p.text for p in decode_text(raw, lex=lex).payloads)]
            byte_readings = layers[1:]
            # The decoder's token-length threshold misses short padded base64.
            # Run this fallback on each byte-decoded layer, up to three deep.
            for _ in range(3):
                following = []
                for layer in layers:
                    for token in re.findall(r'(?<![\w+/])[A-Za-z0-9+/]{4,}={0,2}(?![\w+/=])', layer):
                        try:
                            text = base64.b64decode(token + '=' * (-len(token) % 4), validate=True).decode('utf-8')
                        except (ValueError, UnicodeError, binascii.Error):
                            continue
                        following.append(text)
                readings.extend(following)
                byte_readings.extend(following)
                layers = following
            # Forced shifts of decoded byte layers cover base64-of-rot13.
            for reading in byte_readings:
                readings.extend(p.text for p in forced_text(reading, lex=lex))
            decoded_words.update(tuple(match_words(reading)) for reading in readings)
    for match in ANNOUNCE.finditer(line):
        idiom = next((i for i in idioms if i.start() == match.start()
                      and i.end() >= match.end()), None)
        if not idiom:
            return True
        words = tuple(match_words(idiom.group('idiom')))
        if any(''.join(words) in ''.join(payload) for payload in decoded_words):
            return True
    return False


ALWAYS_ANNOUNCE = re.compile(r'\b(?:it|that|this)\s+(?:de-?codes?|deciphered|translates?)\s+(?:to|into|as)\b|\b(?:de-?codes?|deciphered|translates?)\s+(?:to|into|as)\s*["“\']', re.I)
META = re.compile(r'\bas an ai language model\b|\b(?:system|developer)\s+(?:prompt|message|instructions?)\b|<\/?(?:tool_call|tool_response|function_call)>|"(?:tool_calls|function_call|tool_use)"\s*:|\{\s*"[^"\n]+"\s*:', re.I)
MEANING_ASK = re.compile(r'\bwhat\s+does\s+(.+?)\s+mean\b', re.I)
GLOSS_ASK = re.compile(r'\b(?:what\s+is\s+the\s+meaning\s+of|define|explain\s+the\s+meaning\s+of)\s+([^?!\n]+)', re.I)
TRANSLATE_ASK = re.compile(r'\btranslate\b', re.I)
# A direct imperative with an explicit object is viewer-supplied text even if
# the object happens to be a dictionary word. Bare references await a payload.
TRANSLATE_OBJECT = re.compile(
    r'^\s*(?:please\s+)?translate\s+(?!(?:this|that|it|please|pls|plz)\b)[^\W\d_]+', re.I)


def contains(line: str, target: str) -> bool:
    """Contiguous normalized words, with a joined-letter reading for glued spans."""
    target_words = match_words(target)
    line_words = match_words(line)
    if not target_words or not line_words:
        return False
    n = len(target_words)
    if any(line_words[i:i+n] == target_words for i in range(len(line_words)-n+1)):
        return True
    joined = ''.join(target_words)
    return len(joined) >= 8 and joined in ''.join(line_words)


def repeat_target(line: str, window) -> bool:
    for m in window:
        text = m.text
        targets = [next(g for g in x.groups() if g is not None) for x in QUOTES.finditer(text)]
        for match in ASK.finditer(text):
            s = match.group(1).strip(' \t:;,.!?\"\'“”‘’`')
            targets.extend((s, QUOTES.sub(' ', s)))
        # Glued quotes: also compare the ask object after quote removal.
        for target in targets:
            if target and contains(line, target):
                return True
    return False


def never_say(line: str, banned) -> bool:
    readings = fold_variants(line)
    return any(contains(reading, fold(term)) for term in banned if term for reading in readings)


def decoded_name(name: str, ctx) -> bool:
    """The same encoded-name checks used for display names and line tokens."""
    protected = tuple(n for n in (ctx.host_name, *ctx.protected_names) if n)
    decoded = candidates(name, armed=True, strict=True)
    return (any(never_say(value, (*ctx.never_say, *protected)) for value in decoded)
            or bool(printable_hex(name)) or looks_encoded(name)
            or decode_text(name, name=True).readable or joined_name_encoded(name))


# Explicit identifier controls: these are ambiguous with encodings and are
# intentionally exempt even where the name heuristic would withhold one.
_IDENTIFIER = re.compile(
    r'(?i)(?<![\w#])(?:[0-9a-f]{40}|[0-9a-f]{64}|'
    r'[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}|'
    r'0xdeadbeef|\#[0-9a-f]{3,8})(?!\w)')


def encoded_carrier(line: str, ctx) -> bool:
    """Withhold encoded line content and encoded-name tokens, except identifiers."""
    text = _IDENTIFIER.sub(' ', line)
    lex = lexicon_for(())

    def plain(token: str) -> bool:
        # Do not apply the name decoder to real words, including short SCOWL
        # entries and hyphenated compounds. Digits and encoding punctuation
        # remain available to the decoder rather than being stripped away.
        parts = re.findall(r"[a-z]+", token.lower())
        return bool(parts) and bool(re.fullmatch(r"[a-z]+(?:[-'][a-z]+)*", token.lower())) and all(
            part in dictionary() or lex.known(part) for part in parts)

    tokens = [m.group().strip('.,!?;:()[]{}\"\'“”‘’`—–') for m in re.finditer(r'\S+', text)]
    encoded = [token for token in tokens if token and not plain(token)]
    if not encoded:
        return False
    # Preserve multi-token byte sequences, while preventing dictionary words
    # from becoming spurious whole-line base64 candidates.
    suspect = ' '.join(encoded)
    protected = (*ctx.never_say, ctx.host_name, *ctx.protected_names)
    decoded = decode_text(suspect, name=True)
    if printable_hex(suspect) or any(
        name_reads(p) or never_say(p.text, protected)
        # Existing hardening requires a byte-encoded single dictionary word
        # such as swordfish to be withheld even when no terms are configured.
        or (p.via.removeprefix('name:').split('>')[0] in {'base64', 'hex', 'url', 'binary', 'decimal', 'morse'}
            and len(p.words) == 1 and p.words[0] in dictionary() and len(p.words[0]) >= 4)
        for p in decoded.payloads
    ):
        return True
    return any(decoded_name(token, ctx) for token in encoded)


def forged(line: str, ctx) -> bool:
    for name in (ctx.host_name, *ctx.protected_names):
        if not name:
            continue
        n = re.escape(fold(name))
        for value in fold_variants(line):
            if re.search(r'(?<!\w)' + n + r'\s+(?:says|said|here\b|speaking\b|told\s+(?:me|us)|instructs?\b|asked\s+(?:me|us)\s+to\b|asked\s*,\s*so\b|approved\s+\w+\s*,)', value):
                return True
            if re.search(r'\bgoing\s+live\s+for\s+' + n + r'(?!\w)', value):
                return True
    return False


def protected_lookalike(line: str, ctx) -> bool:
    """Match whole name-shaped spans, preserving exact spellings as benign."""
    tokens = list(re.finditer(r'\w+', line))
    for name in (ctx.host_name, *ctx.protected_names):
        if not name:
            continue
        count = len(re.findall(r'\w+', name))
        for i in range(len(tokens) - count + 1):
            if not count:
                break
            span = line[tokens[i].start():tokens[i + count - 1].end()]
            if span.casefold() != name.casefold() and fold(name) in fold_variants(span):
                return True
    return False


def _nonwords(text: str, lex) -> int:
    count = 0
    for token in text.split():
        if token.startswith('@'):
            count += 1
            continue
        if re.fullmatch(r'[\w.-]+\.(?:com|org|net|py|js|txt)|v?\d+(?:\.\d+)+|\d+', token, re.I):
            continue
        parts = split_joined(token)
        for part in parts:
            word = fold(part).strip('.,!?;:()[]{}\"\'')
            if len(word) >= 3 and word.isalpha() and not lex.known(word):
                count += 1
    return count


def armed_window(ctx, lex=None) -> bool:
    lex = lex or lexicon_for((ctx.host_name, *ctx.protected_names))
    if ctx.armed:
        return True
    window = ctx.chat_window
    for i, msg in enumerate(window):
        text = msg.text
        decoded = decode_text(text, lex=lex)
        name_decoded = decode_text(msg.display_name, name=True, lex=lex)
        if decoded.readable or decoded.shape or name_decoded.readable or name_decoded.shape:
            return True
        if ASK.search(text):
            return True
        if TRANSLATE_OBJECT.search(text):
            return True
        meaning = MEANING_ASK.search(text)
        if meaning and (QUOTES.search(meaning.group(1)) or _nonwords(meaning.group(1), lex) >= 1):
            return True
        request = (patterns.DECODE_REQUEST.search(text) or patterns.ARM_REQUEST.search(text)
                   or patterns.ARM_LABEL.search(text) or patterns.REQUEST_SEP.search(text)
                   or patterns.TRANSLATE_REQUEST.search(text) or TRANSLATE_ASK.search(text)
                   or REQUEST.search(text)
                   or any(rx.search(text) for _, rx in patterns.ARM_FRAMES))
        if not request:
            continue
        nearby = [text] + [m.text for m in window[i+1:i+3] if m.user_id == msg.user_id]
        if any(patterns.QUOTED.search(s) or _nonwords(s, lex) >= 2 for s in nearby):
            return True
        if (patterns.REQUEST_SEP.search(text) or patterns.ARM_LABEL.search(text)) and re.search(r'[:=;]\s*\S+', text):
            return True
        if patterns.TRANSLATE_REQUEST.search(text):
            return True
    return False


def decoded_echo(line: str, ctx, armed: bool, lex) -> bool:
    line_words = match_words(line)
    for msg in ctx.chat_window:
        for raw, is_name in ((msg.text, False), (msg.display_name, True)):
            decoded = decode_text(raw, is_name, lex)
            payloads = list(decoded.payloads)
            if armed:
                payloads.extend(forced_text(raw, is_name, lex))
            for p in payloads:
                if p.keys is not None:
                    if any(contains(line, x) for x in p.needles if len(x) >= 3):
                        return True
                elif (armed or (name_reads(p) if is_name else reads_as_words(p))):
                    # One-word forced guesses are very noisy; require exact target tokens
                    # of at least four letters, or a two-word contiguous span.
                    if len(p.words) == 1:
                        if len(p.words[0]) >= 4 and p.words[0] in line_words:
                            return True
                    elif longest_shared_run(p.words, line_words) >= 2:
                        return True
    return False
