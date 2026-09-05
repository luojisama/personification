"""Single, fail-closed policy gate for every expression source.

The model can request an expression, but it never gets to assert which source
the expression came from.  Sources are assigned by the integration and are
checked again immediately before a queued action is dispatched.
"""
from __future__ import annotations

from typing import Any

EXPRESSION_SOURCES = frozenset({"local", "qq_favorite", "qq_recommended", "native"})


def _enabled(config: Any, name: str, default: bool) -> bool:
    value = getattr(config, name, default)
    return bool(default if value is None else value)


def _source_override(config: Any, name: str) -> bool | None:
    """New source switches default to inheritance, not an implicit enable."""
    value = getattr(config, name, None)
    return value if isinstance(value, bool) else None


def expression_source_enabled(config: Any, source: str) -> bool:
    """Return whether a program-assigned source may be used right now.

    Legacy ``personification_qq_expression_enabled=False`` and a zero legacy
    probability are preserved as an explicit disable intent for QQ sources.
    New source switches deliberately do not make a disabled legacy deployment
    start emitting expressions after an upgrade.
    """
    normalized = str(source or "").strip().lower()
    if normalized not in EXPRESSION_SOURCES:
        return False
    if not _enabled(config, "personification_expression_enabled", True):
        return False
    if normalized == "local":
        override = _source_override(config, "personification_local_expression_enabled")
        legacy_probability = getattr(config, "personification_sticker_probability", None)
        inherited = not (legacy_probability is not None and float(legacy_probability or 0) <= 0)
        return inherited if override is None else override

    legacy_enabled = _enabled(config, "personification_qq_expression_enabled", True)
    if not legacy_enabled:
        return False
    if normalized == "native":
        override = _source_override(config, "personification_native_expression_enabled")
        legacy_probability = getattr(config, "personification_qq_expression_probability", None)
        inherited = not (legacy_probability is not None and float(legacy_probability or 0) <= 0)
        return inherited if override is None else override
    if normalized == "qq_favorite":
        override = _source_override(config, "personification_qq_favorite_expression_enabled")
    else:
        override = _source_override(config, "personification_qq_recommended_expression_enabled")
    # The older favorite probability controlled both remotely fetched image
    # sources.  Preserve an explicit zero unless an administrator sets the
    # new, source-specific switch deliberately.
    legacy_remote_probability = getattr(config, "personification_qq_favorite_expression_probability", None)
    inherited = not (legacy_remote_probability is not None and float(legacy_remote_probability or 0) <= 0)
    return inherited if override is None else override


def expression_action_allowed(config: Any, action_type: str, params: Any) -> bool:
    """Final dispatcher gate; source is never inferred from model text."""
    action = str(action_type or "").strip()
    values = params if isinstance(params, dict) else {}
    if action == "send_sticker":
        return expression_source_enabled(config, "local")
    if action in {"send_qq_face", "send_qq_mface"}:
        return expression_source_enabled(config, "native")
    if action == "send_qq_image_expression":
        source = str(values.get("expression_source") or "")
        return source in {"qq_favorite", "qq_recommended"} and expression_source_enabled(config, source)
    return True


__all__ = ["EXPRESSION_SOURCES", "expression_action_allowed", "expression_source_enabled"]
