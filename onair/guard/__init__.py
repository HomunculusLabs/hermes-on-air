"""Pure, fail-closed per-line and support-name checks.

These deterministic checks are a backstop, not an isolation boundary. Unknown
unannounced ciphers require the separate never-decode instruction and admission gate.
"""
from __future__ import annotations

from ..types import GuardContext, Verdict, fail_closed_verdict
from ..system_text import system_text_category
from .lexicon import fold, fold_variants, lexicon_for
from .rules import (ALWAYS_ANNOUNCE, META, REFUSAL, armed_readout, armed_window, decoded_echo, decoded_name, encoded_carrier,
                    forged, never_say, protected_lookalike, repeat_target)


def check_line(line: str, ctx: GuardContext, *, profanity: str = 'mask') -> Verdict:
    try:
        return _check_line(line, ctx, profanity=profanity)
    except Exception as exc:
        return fail_closed_verdict(f"guard error: {type(exc).__name__}")


def _wordfilter_verdict(text: str, profanity: str, *, name: bool = False) -> Verdict | None:
    # Lazy import avoids a cycle: wordfilter uses guard.lexicon for folding.
    from ..wordfilter import line_tiers, name_tiers

    tiers = name_tiers(text) if name else line_tiers(text)
    if 'slur' in tiers:
        return Verdict(False, 'slur', 'filtered language')
    if profanity != 'allow' and 'profanity' in tiers:
        return Verdict(False, 'profanity', 'filtered language')
    return None


def _check_line(line: str, ctx: GuardContext, *, profanity: str = 'mask') -> Verdict:
    if not isinstance(line, str) or not isinstance(ctx, GuardContext):
        raise TypeError('invalid guard input')
    if profanity not in ('mask', 'block', 'allow'):
        raise ValueError('invalid profanity policy')
    if system_text_category(line):
        return Verdict(False, 'system_text', 'system text never airs')
    filtered = _wordfilter_verdict(line, profanity)
    if filtered:
        return filtered
    lex = lexicon_for((ctx.host_name, *ctx.protected_names))
    if never_say(line, ctx.never_say):
        return Verdict(False, 'never_say', 'configured term')
    if forged(line, ctx):
        return Verdict(False, 'forged_authority', 'protected identity claim')
    if protected_lookalike(line, ctx):
        return Verdict(False, 'protected_name', 'protected identity')
    if META.search(line):
        return Verdict(False, 'meta', 'internal syntax or instruction')
    if repeat_target(line, ctx.chat_window):
        return Verdict(False, 'repeat_on_request', 'viewer-supplied target')
    if ALWAYS_ANNOUNCE.search(line):
        return Verdict(False, 'announce', 'decode announcement')
    armed = armed_window(ctx, lex)
    if armed and REFUSAL.search(line):
        return Verdict(False, 'armed_refusal', 'refusal in armed window')
    if armed and armed_readout(line, ctx, lex):
        return Verdict(False, 'armed_readout', 'read-out in armed window')
    if decoded_echo(line, ctx, armed, lex):
        return Verdict(False, 'decoded_echo', 'decoded viewer content')
    if encoded_carrier(line, ctx):
        return Verdict(False, 'encoded_carrier', 'encoded output')
    return Verdict(True, 'allow', 'passed')


def check_name(name: str, ctx: GuardContext) -> Verdict:
    """Check untrusted display names before alerts or spoken thank-yous."""
    try:
        if not isinstance(name, str) or not isinstance(ctx, GuardContext) or not name.strip():
            raise TypeError('invalid name')
        filtered = _wordfilter_verdict(name, 'block', name=True)
        if filtered:
            return filtered
        if never_say(name, ctx.never_say):
            return Verdict(False, 'never_say', 'configured term')
        protected_names = tuple(n for n in (ctx.host_name, *ctx.protected_names) if n)
        if any(name.casefold() == n.casefold() for n in protected_names):
            return Verdict(True, 'allow', 'exact protected name')
        readings = fold_variants(name)
        for protected in protected_names:
            if protected and fold(protected) in readings:
                return Verdict(False, 'protected_name', 'protected identity')
        if decoded_name(name, ctx):
            return Verdict(False, 'decoded_name', 'encoded name or protected content')
        return Verdict(True, 'allow', 'passed')
    except Exception as exc:
        return fail_closed_verdict(f"guard error: {type(exc).__name__}")
