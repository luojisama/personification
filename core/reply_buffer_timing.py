from __future__ import annotations

from dataclasses import dataclass
from typing import Any


DEFAULT_BASE_WAIT_SECONDS = 30.0
DEFAULT_MIN_WAIT_SECONDS = 10.0
DEFAULT_MAX_WAIT_SECONDS = 60.0
HARD_MIN_WAIT_SECONDS = 10.0
HARD_MAX_WAIT_SECONDS = 60.0


@dataclass(frozen=True)
class ReplyBufferTiming:
    base_wait_seconds: float
    min_wait_seconds: float
    max_wait_seconds: float
    legacy_debounce_seconds: float | None = None
    legacy_reply_backoff_seconds: float | None = None

    def clamp(self, requested: float | None = None) -> float:
        value = self.base_wait_seconds if requested is None else float(requested)
        return min(self.max_wait_seconds, max(self.min_wait_seconds, value))


def _optional_non_negative(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return None


def resolve_reply_buffer_timing(config: Any) -> ReplyBufferTiming:
    """Resolve the 30/10/60 contract and explicit legacy overrides.

    Legacy fields intentionally default to ``None``. Therefore an old value can
    only affect the contract when it was actually supplied by runtime config;
    the retired 3/20/15 defaults never leak into a fresh installation.
    """

    configured_min = _optional_non_negative(
        getattr(config, "personification_batch_min_wait_seconds", DEFAULT_MIN_WAIT_SECONDS)
    )
    configured_max = _optional_non_negative(
        getattr(config, "personification_batch_max_wait_seconds", DEFAULT_MAX_WAIT_SECONDS)
    )
    min_wait = min(
        HARD_MAX_WAIT_SECONDS,
        max(HARD_MIN_WAIT_SECONDS, configured_min or DEFAULT_MIN_WAIT_SECONDS),
    )
    max_wait = min(
        HARD_MAX_WAIT_SECONDS,
        max(min_wait, configured_max or DEFAULT_MAX_WAIT_SECONDS),
    )
    base_wait = _optional_non_negative(
        getattr(config, "personification_batch_base_wait_seconds", DEFAULT_BASE_WAIT_SECONDS)
    )
    legacy_debounce = _optional_non_negative(
        getattr(config, "personification_batch_debounce_seconds", None)
    )
    legacy_backoff = _optional_non_negative(
        getattr(config, "personification_reply_backoff_seconds", None)
    )
    if legacy_debounce is not None:
        base_wait = legacy_debounce
    base_wait = min(max_wait, max(min_wait, base_wait or DEFAULT_BASE_WAIT_SECONDS))
    return ReplyBufferTiming(
        base_wait_seconds=base_wait,
        min_wait_seconds=min_wait,
        max_wait_seconds=max_wait,
        legacy_debounce_seconds=legacy_debounce,
        legacy_reply_backoff_seconds=legacy_backoff,
    )


__all__ = [
    "DEFAULT_BASE_WAIT_SECONDS",
    "DEFAULT_MAX_WAIT_SECONDS",
    "DEFAULT_MIN_WAIT_SECONDS",
    "HARD_MAX_WAIT_SECONDS",
    "HARD_MIN_WAIT_SECONDS",
    "ReplyBufferTiming",
    "resolve_reply_buffer_timing",
]
