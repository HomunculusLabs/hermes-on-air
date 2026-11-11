"""Resolve credentials through the current Hermes profile, at the point of use."""


def get(name: str) -> str:
    try:
        from agent.secret_scope import get_secret
    except ImportError:
        # Unsupported/standalone runtime: never substitute another profile's environment.
        return ""
    return get_secret(name) or ""
