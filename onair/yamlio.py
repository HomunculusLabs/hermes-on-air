"""YAML reading for On Air without a hard PyYAML dependency.

Hermes itself depends on ``ruamel.yaml``; PyYAML is NOT part of the supported Python 3.14 environment.
Every On Air module that reads a stage ``config.yaml`` goes through :func:`safe_load`, which prefers
ruamel's pure safe loader and falls back to PyYAML only where that happens to be installed. No shim is
installed and no dependency is added.
"""
from __future__ import annotations

from typing import Any


class YAMLUnavailable(ValueError):
    """Neither ruamel.yaml (Hermes's own) nor PyYAML is importable."""


def _errors() -> tuple[type[BaseException], ...]:
    found: list[type[BaseException]] = [ValueError]
    try:
        from ruamel.yaml.error import YAMLError as RuamelError
        found.append(RuamelError)
    except ImportError:
        pass
    try:
        import yaml  # type: ignore[import-untyped]
        found.append(yaml.YAMLError)
    except ImportError:
        pass
    return tuple(found)


#: Catch this tuple (it always includes ValueError) around :func:`safe_load`.
YAMLError = _errors()


def safe_load(text: str) -> Any:
    """Parse YAML text with safe semantics (plain dicts/lists/scalars, no arbitrary objects)."""
    try:
        from ruamel.yaml import YAML
    except ImportError:
        YAML = None  # noqa: N806
    if YAML is not None:
        return YAML(typ="safe", pure=True).load(text)
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError:
        raise YAMLUnavailable("reading YAML needs ruamel.yaml (ships with Hermes) or PyYAML") from None
    return yaml.safe_load(text)
