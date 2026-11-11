"""On Air: let your Hermes agent co-host a live stream, safely.

Entry point stays import-light: the platform adapter (and anything heavy) is imported
inside register(), so plugin discovery is cheap.
"""


def register(ctx):
    from .onair.adapter import register as _register

    _register(ctx)
