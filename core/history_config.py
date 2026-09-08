"""Provenance-aware compatibility for legacy and long-context history limits.

ConfigManager stores complete snapshots, so a key merely present in env.json is
not proof an administrator selected it.  Only explicit pydantic/environment
provenance can make an old limit constrain the new defaults.
"""
from __future__ import annotations

from typing import Any


def explicit_config_fields(config: Any) -> set[str]:
    info = getattr(config, "_personification_env_config_info", None)
    # ConfigManager captured this before applying env.json.  Prefer durable
    # provenance once it exists; post-load pydantic fields_set is polluted by
    # setattr and is intentionally never consulted here.
    explicit: set[str] = set()
    if isinstance(info, dict) and any(name in info for name in ("provenance_fields", "pre_load_explicit_fields", "imported_fields", "provenance_unknown")):
        explicit.update(str(item) for item in info.get("provenance_fields", []) if str(item).startswith("personification_"))
        explicit.update(str(item) for item in info.get("pre_load_explicit_fields", []) if str(item).startswith("personification_"))
        explicit.update(str(item) for item in info.get("imported_fields", []) if str(item).startswith("personification_"))
        return explicit
    # Before ConfigManager runs, fields_set is authoritative.
    fields = getattr(config, "__pydantic_fields_set__", set())
    explicit.update(str(item) for item in fields or () if str(item).startswith("personification_"))
    return explicit


def _positive_int(config: Any, field: str, default: int, *, floor: int, ceiling: int) -> int:
    try:
        value = int(getattr(config, field, default))
    except (TypeError, ValueError):
        value = default
    return max(floor, min(value, ceiling))


def effective_history_message_limit(config: Any, *, private: bool) -> tuple[int, bool]:
    """Return effective long-context cap and whether a legacy explicit cap won.

    New fields win when explicitly set.  Otherwise an explicitly set legacy
    bound narrows the new default via ``min``.  This preserves the safety
    intent of old operator limits without treating a persisted default snapshot
    as deliberate configuration.
    """
    if private:
        new_field, new_default = "personification_private_history_max_messages", 4000
        old_field, old_default, ceiling = "personification_private_history_turns", 60, 50000
    else:
        new_field, new_default = "personification_group_history_max_messages", 12000
        old_field, old_default, ceiling = "personification_history_len", 320, 100000
    explicit = explicit_config_fields(config)
    new_limit = _positive_int(config, new_field, new_default, floor=1, ceiling=ceiling)
    info = getattr(config, "_personification_env_config_info", None)
    provenance_unknown = bool(info.get("provenance_unknown")) if isinstance(info, dict) else False
    # Pre-provenance snapshots cannot identify an intentional legacy choice.
    # Preserve their old cap until the administrator explicitly writes a new
    # configuration, rather than silently expanding retained data.
    if new_field in explicit or (old_field not in explicit and not provenance_unknown):
        return new_limit, False
    old_limit = _positive_int(config, old_field, old_default, floor=1, ceiling=ceiling)
    return min(new_limit, old_limit), True


def effective_history_days(config: Any, *, private: bool) -> tuple[float | None, bool]:
    """Return day scope, preserving explicit legacy hours when new days absent.

    ``None`` means unlimited.  Legacy zero has that same meaning and must not
    be replaced by the new finite default without an explicit operator choice.
    """
    new_field, new_default = (
        ("personification_private_history_days", 14)
        if private else ("personification_group_history_days", 7)
    )
    old_field, old_default = (
        ("personification_message_expire_hours", 72.0)
        if private else ("personification_group_context_expire_hours", 18.0)
    )
    explicit = explicit_config_fields(config)
    info = getattr(config, "_personification_env_config_info", None)
    provenance_unknown = bool(info.get("provenance_unknown")) if isinstance(info, dict) else False
    try:
        new_days = max(1, min(int(getattr(config, new_field, new_default)), 3650))
    except (TypeError, ValueError):
        new_days = new_default
    if new_field in explicit or (old_field not in explicit and not provenance_unknown):
        return new_days, False
    try:
        legacy_hours = float(getattr(config, old_field, old_default))
    except (TypeError, ValueError):
        legacy_hours = old_default
    if legacy_hours <= 0:
        return None, True
    # Preserve exact sub-day legacy ranges; zero alone means unlimited.
    return min(legacy_hours / 24.0, 3650.0), True
