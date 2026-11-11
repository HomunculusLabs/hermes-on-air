"""Recompute deterministic per-rule price, including overlapping hits.

Run: python tests/guard/measure_price.py
"""
from collections import Counter
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from benign import LINES
from language_benign import ROWS
from onair.types import ChatMsg, GuardContext
from onair.guard import check_line
from onair.guard.lexicon import lexicon_for
from onair.guard.rules import (ALWAYS_ANNOUNCE, META, REFUSAL, armed_readout, armed_window,
                               decoded_echo, encoded_carrier, forged, never_say, repeat_target)


def measure(rows=None):
    counts = Counter()
    if rows is None:
        # Some chat quotes the very thing the benign answer would repeat.
        rows = [('What do you think about "the parser"?' if i % 4 == 0 else
                 'Hello, thanks for the stream!', line) for i, line in enumerate(LINES)]
    total = len(rows)
    for text, line in rows:
        msg = ChatMsg('test', 'test', '1', '1', 'viewer_42', text, 0)
        ctx = GuardContext(chat_window=(msg,), never_say=('CANARY-PINEAPPLE',))
        lex = lexicon_for((ctx.host_name, *ctx.protected_names))
        armed = armed_window(ctx, lex)
        rules = {
            'never_say': never_say(line, ctx.never_say),
            'forged_authority': forged(line, ctx),
            'meta': bool(META.search(line)),
            'repeat_on_request': repeat_target(line, ctx.chat_window),
            'announce': bool(ALWAYS_ANNOUNCE.search(line)),
            'armed_refusal': armed and bool(REFUSAL.search(line)),
            'armed_readout': armed and armed_readout(line, ctx, lex),
            'decoded_echo': decoded_echo(line, ctx, armed, lex),
            'encoded_carrier': encoded_carrier(line, ctx),
            'overall': not check_line(line, ctx).release,
        }
        counts.update(k for k, v in rules.items() if v)
    return total, counts


if __name__ == '__main__':
    for name, rows in (('general', None), ('language', ROWS)):
        total, counts = measure(rows)
        print(f'{name} lines: {total}')
        for rule in ('never_say', 'forged_authority', 'meta', 'repeat_on_request',
                     'announce', 'armed_refusal', 'armed_readout', 'decoded_echo', 'encoded_carrier', 'overall'):
            print(f'{rule}: {counts[rule]}/{total} ({100 * counts[rule] / total:.2f}%)')
