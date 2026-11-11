"""On Air presentation/output slice: an agent-owned public presentation surface plus OBS control.

``register(ctx)`` is the single entry for the integrator; see ``agent.py`` and docs/presentation.md.
Import-light: nothing heavy loads until register() is called.
"""


def register(ctx, config=None, client=None):
    from .agent import register as _register

    return _register(ctx, config=config, client=client)


def teardown():
    from .agent import teardown as _teardown

    _teardown()
