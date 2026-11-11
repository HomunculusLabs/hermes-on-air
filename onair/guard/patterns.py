"""Shared regular expressions: decode requests, arming terms, labels, frames and translation asks.

Ported from the private stage stack's decoded-echo guard and generalised: no persona or show names, vocatives are
any @handle or leading word. All patterns are case-insensitive and Unicode-aware (stdlib `re`; L / LN classes from
text.py stand in for \\p{L} / [\\p{L}\\p{N}]).
"""
from __future__ import annotations

import re

from .text import LN

I = re.I | re.U

LEET = r"(?:leet|l33t|1eet|l337|133t|1337)(?:\s*(?:speak|sp34k|sp3ak|spe4k))?"
ENC_NAME = (r"(?:rot\s*-?\s*\d{1,2}|caesar(?:\s+(?:cipher|shift))?(?:\s*(?:by\s+)?-?\s*\d{1,2}\b)?|morse|base\s*-?\s*(?:64|32)"
            r"|b64|hex|binary|atbash|a1z26|pig\s*latin|" + LEET + r")")
REQ_VERB = r"(?:de-?cod(?:e|ing)|deciph(?:er|ering)|decrypt(?:ing)?|unscrambl(?:e|ing)|un-?(?:rot|reverse|hex|base64|b64)\w*)"

# An explicit decode request: a decode verb asked of someone, given an object or a marker, or opening the message.
# A bare mention of an encoding, or a question about how decoding works ("how do you decode a JWT?"), is not one.
DECODE_REQUEST = re.compile("|".join([
    rf"\b(?:can|could|would|will|wanna|want\s+to)\s+(?:you|u|ya)\s+(?:\w+\s+)?{REQ_VERB}\b",
    rf"\b(?:pls|please|plz|try\s+(?:to|and)|help\s+me|go\s+ahead\s+and|now)\s+{REQ_VERB}\b",
    rf"\b{REQ_VERB}\s+(?:this|that|it|these|those|my|me|for\s+(?:me|us|chat)|pls|please|plz|and|twice|out\s+loud|aloud"
    rf"|the\s+following|the\s+(?:message|string|text|code|name|username))\b",
    rf"\b{REQ_VERB}\s*[:\-–—]",
    r"^\W*(?:@\w+\W+)?(?:\w+[,:!]?\s+)?(?:de-?code|decipher|decrypt|unscramble|un-?(?:rot|reverse|hex|base64|b64)\w*)\b",
    rf"\btranslate\s+(?:this|that|it|these|those|the\s+following|my\s+(?:name|username|message|code)|from\s+{ENC_NAME})\b",
    r"\b(?:read|say|spell)\s+(?:this|that|it|my\s+\w+)\s+(?:backwards?|in\s+reverse|reversed)\b",
    r"\b(?:reverse|flip)\s+(?:this|that|it|my\s+\w+)\b",
    rf"\b{ENC_NAME}\s*(?:(?:this|that|it|these|me|pls|please|for\s+the|my\s+\w+)\b|:)",
    rf"\b{ENC_NAME}\s*(?:->|to|into)\s*(?:text|english|plain|words|letters)\b",
    r"\bwhat\s+does\s+(?:this|that|it|my\s+(?:name|username))\s+(?:say|spell|read)\b",
]), I)

CIPHER = r"(?:cipher\s*text|cipher(?!\s*(?:suites?|modes?|blocks?|strength|list|spec)\b))"
ENCODED = r"encoded(?:\s+(?:msg|message|text|string|name|word|words|bit|line|version))?"
HIDDEN = r"(?:secret|hidden)\s+(?:message|msg|code|text|word|words|phrase|note)"
ARM_TERM = (rf"(?:{CIPHER}|{ENCODED}|{HIDDEN}|a1z26|pig\s*latin|{LEET}|backwards?|in\s+reverse|revers(?:e|ed)"
            r"|read\s+(?:this|that|it)\s+out(?:\s+loud)?)")

# A term announcing a payload: followed by ':', '=', '->' or a spaced dash ('hidden message: ...'), and any a1z26.
ARM_LABEL = re.compile(rf"\b{ARM_TERM}\s*(?:[:=;]|->|[-–—](?=\s|$))|\ba1z26\b", I)

ARM_VERB = (r"(?:solv(?:e|ing)|crack(?:ing)?|break(?:ing)?|find(?:ing)?|read(?:ing)?|translat(?:e|ing)|de-?cod(?:e|ing)"
            r"|deciph(?:er|ering)|decrypt(?:ing)?|unscrambl(?:e|ing)|figure\s+out|work\s+out|guess|spot)")
ARM_REQ_TERM = rf"(?:{CIPHER}|{ENCODED}|{HIDDEN}|a1z26|pig\s*latin|{LEET})"
ARM_DET = r"(?:the|this|that|these|those|my|our|his|her|their|your|a|an|its|it|me|us|chat|out|in|from|some)"
NLN = rf"(?!{LN})"
# A request verb (or "what is the ...") before a term. Arms only with a payload in the window (pending otherwise).
ARM_REQUEST = re.compile("|".join([
    rf"\b{ARM_VERB}\s+(?:{ARM_DET}\s+){{0,3}}(?:{LN}+\s+)?{ARM_REQ_TERM}{NLN}",
    rf"\bwhat(?:'?s|\s+is|\s+does|\s+do)\s+{ARM_DET}\s+(?:{LN}+\s+)?{ARM_REQ_TERM}{NLN}",
    rf"\b{ARM_REQ_TERM}\s+(?:pls|please|plz|for\s+(?:me|us|chat))\b",
]), I)

FRAME_TERM = rf"(?:{CIPHER}|encoded\s+(?:msg|message|text|string|word|words|line|bit)|{HIDDEN}|a1z26|pig\s*latin|{LEET})"
MSG_TERM = rf"(?:{CIPHER}|encoded\s+(?:msg|message|text|string|word|words|line|bit)|{HIDDEN})"
ADJ = rf"(?:{LN}+\s+)?"
_ADDR = r"(?:you|u|ya|chat|y'?all|everyone)"
# Frames that present a payload under one of the terms ('here is a cipher', 'my secret message is').
ARM_FRAMES = [
    ("here-is", re.compile(rf"\bhere(?:'?s|\s+is|\s+are|\s+comes)\s+(?:a|an|my|the|some|this|your|another|one\s+more)\s+{ADJ}{FRAME_TERM}{NLN}", I)),
    ("there-is-here", re.compile(rf"\bthere(?:'?s|\s+is)\s+(?:a|an)\s+{ADJ}{FRAME_TERM}\s+(?:here|below|for\s+{_ADDR}|in\s+(?:this|my|here|chat))\b", I)),
    ("my-message-is", re.compile(rf"\b(?:my|our)\s+{ADJ}{MSG_TERM}\s+(?:is|reads|goes|says)\b(?!\s+(?:about|of)\b)", I)),
    ("i-wrote-a", re.compile(rf"\b(?:i|we)\s+(?:have|got|made|wrote|left|hid|hide|typed|sent)\s+(?:{_ADDR}\s+)?(?:a|an|some|this)\s+{ADJ}{FRAME_TERM}{NLN}", I)),
    ("term-for-you", re.compile(rf"\b(?:a|an|my|this|one)\s+{ADJ}{FRAME_TERM}\s+for\s+{_ADDR}\b", I)),
    ("try-this", re.compile(rf"\btry\s+(?:this|my|our|a|an)\s+{ADJ}{FRAME_TERM}{NLN}", I)),
]

SEP = r"\s*(?:[:=;]|->|[-–—](?=\s|$))"
SEP_VERB = (r"(?:translat(?:e|ing)|solv(?:e|ing)|crack(?:ing)?|de-?cod(?:e|ing)|deciph(?:er|ering)|decrypt(?:ing)?"
            r"|unscrambl(?:e|ing)|figure\s+(?:(?:this|it|that)\s+)?out)")
SEP_FILL = (r"(?:\s+(?:this|that|it|these|those|one|ones|mine|pls|please|plz|for\s+(?:me|us|chat)|real\s+quick|quick(?:ly)?"
            r"|now|again|too|bro|lol)){0,3}")
# A request verb followed by a separator arms on its own ('translate: ...', "what's this: ...").
REQUEST_SEP = re.compile("|".join([
    rf"\b{SEP_VERB}{SEP_FILL}{SEP}",
    rf"\bwhat(?:['’]?s|\s+is|\s+are|\s+was)\s+(?:this|that|it|these|those){SEP_FILL}{SEP}",
]), I)

LANG = (r"(?:english|german|deutsch|spanish|español|espanol|french|français|francais|italian|portuguese|brazilian|dutch"
        r"|flemish|russian|ukrainian|polish|czech|slovak|hungarian|romanian|bulgarian|serbian|croatian|greek|turkish"
        r"|arabic|hebrew|persian|farsi|hindi|urdu|bengali|punjabi|tamil|japanese|chinese|mandarin|cantonese|korean"
        r"|vietnamese|thai|indonesian|malay|tagalog|filipino|swahili|swedish|norwegian|danish|finnish|icelandic|irish"
        r"|gaelic|welsh|latin|esperanto|klingon|elvish|sindarin|quenya|romaji|pig\s*latin)")
LANG_WORD = re.compile(rf"^{LANG}$", I)

# Viewer text into English, or a translation said out loud, is a decode request ('how do you say ... in english').
TRANSLATE_REQUEST = re.compile("|".join([
    r"\bhow\s+(?:do|would|did|does|can|could|should|to|is|r|are)\s+(?:(?:you|u|ya|one|i|we|it)\s+)?(?:say|translate|read"
    r"|write|pronounce|put|spell)\b[^.!?\n]{0,160}?\b(?:in|to|into)\s+(?:plain\s+)?english\b",
    r"\btranslat(?:e|ing)\b[^.!?\n]{0,120}?\band\s+(?:then\s+)?(?:say|read|spell|type|tell|speak|shout|sing)\b",
    r"\b(?:say|read|speak|tell\s+(?:us|chat|me))\s+(?:the|its|the\s+(?:plain|proper))?\s*(?:english|translation)\b",
    r"\b(?:the\s+)?english\s+(?:for|of|word\s+for|translation|version|meaning)\b",
    r"\b(?:mean|means|meaning|say|says|said|translates?|translated|translation)\s+in\s+(?:plain\s+)?english\b",
    r"\btranslat(?:e|ing|ion)\b[^.!?\n]{0,120}?\b(?:to|into|in)\s+(?:plain\s+)?english\b",
    rf"\btranslat(?:e|ing|ion)\s+(?:(?:this|that|it|these|those|one|pls|please|plz)\s+){{0,2}}from\s+{LANG}\b",
    r"\btranslat(?:e|ing)\s+(?:(?:this|that|it|these|those|one)\s+)?for\s+(?:me|us|chat|everyone"
    r"|the\s+(?:chat|artist|stream|tattoo|captions?))\b",
    rf"\b{LANG}\s*(?:->|→|to|into)\s*english\b",
]), I)
TRANSLATE_VERB = re.compile(r"\btranslat(?:e|ing|ion|ions)\b|\bin\s+english\b", I)

# A quoted or code-fenced segment (payload evidence for a pending request).
QUOTED = re.compile(rf"[“\"]([^\"“”]*{LN}[^\"“”]*)[”\"]|(?:^|\s)['‘]([^'‘’]*{LN}[^'‘’]*)['’](?=$|[\s.,!?;:])|`[^`]*{LN}[^`]*`", I)

# The term points at a payload: 'solve this cipher', 'the cipher above', 'the hidden message I just posted'.
DEICTIC = [
    re.compile(rf"\b(?:this|that|these|those|dis|dat)\s+(?:(?!(?:the|a|an|my|your|our)\b){LN}+\s+)?{ARM_REQ_TERM}{NLN}"
               r"(?!\s+(?:from|in|on|at|of|about|during|since|last|yesterday|earlier|back)\b)", I),
    re.compile(rf"{ARM_REQ_TERM}\s+(?:above|below|here|up\s+there|down\s+there|in\s+(?:chat|the\s+chat|here"
               r"|my\s+(?:name|username|last\s+message))|(?:i|we)\s+(?:just\s+)?(?:posted|sent|wrote|typed|dropped|put|left"
               r"|gave\s+(?:you|u)))\b", I),
]

DECODE_REQUEST_G = DECODE_REQUEST
ENC_TOKEN_G = re.compile(rf"\b{ENC_NAME}{NLN}|\b(?:backwards?|in\s+reverse|reversed)\b", I)
RUN_TERM_G = re.compile(rf"\b(?:{ARM_TERM}|{ENC_NAME}){NLN}", I)
REQ_TERM_G = re.compile(rf"{ARM_REQ_TERM}{NLN}", I)
TERM_SPAN_G = re.compile(rf"{ARM_REQ_TERM}{NLN}|\b{ARM_VERB}\b", I)
