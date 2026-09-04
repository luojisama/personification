"""Provider-type normalization shared by runtime and management surfaces.

The removed Claude Code OAuth/CLI route is intentionally represented by a
non-callable tombstone instead of being silently reinterpreted as another
transport.  Keep this module dependency-free so dynamically loaded skillpacks
can use the same boundary before they inspect any route credentials.
"""

from __future__ import annotations

from typing import Any


PROVIDER_TYPE_REMOVED = "provider_type_removed"
_REMOVED_PROVIDER_TYPE_ALIASES = frozenset(
    {
        "claude_code",
        "claudecode",
        "claude_cli",
    }
)


def normalize_removed_provider_type(value: Any) -> str:
    """Return the canonical non-callable tombstone for removed route aliases."""

    normalized = str(value or "").strip().lower().replace("-", "_")
    if normalized in _REMOVED_PROVIDER_TYPE_ALIASES or normalized == PROVIDER_TYPE_REMOVED:
        return PROVIDER_TYPE_REMOVED
    return normalized


def is_removed_provider_type(value: Any) -> bool:
    """Whether *value* names the removed Claude Code OAuth/CLI transport."""

    return normalize_removed_provider_type(value) == PROVIDER_TYPE_REMOVED


def removed_provider_migration_hint() -> str:
    """Safe, user-facing migration guidance with no credential-derived data."""

    return "Claude Code OAuth CLI Provider 已移除；请改用标准 anthropic API，并重新填写 API 地址、API Key 与模型。"


def removed_provider_tombstone(*, source: str, index: int | None = None) -> dict[str, Any]:
    """Return a safe management projection for a removed provider route.

    This deliberately omits route URLs, API keys, auth paths, projects, and
    even the former raw type.  Those values are diagnostic inputs only.
    """

    result: dict[str, Any] = {
        "api_type": PROVIDER_TYPE_REMOVED,
        "enabled": False,
        "diagnostic_code": PROVIDER_TYPE_REMOVED,
        "migration_hint": removed_provider_migration_hint(),
        "source": str(source or "unknown"),
    }
    if index is not None:
        result["route_index"] = max(0, int(index))
    return result
