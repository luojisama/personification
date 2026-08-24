from __future__ import annotations

from types import SimpleNamespace

from ._loader import load_personification_module


timing_module = load_personification_module("plugin.personification.core.reply_buffer_timing")


def test_fresh_config_uses_30_10_60_contract() -> None:
    timing = timing_module.resolve_reply_buffer_timing(SimpleNamespace())
    assert timing.base_wait_seconds == 30.0
    assert timing.min_wait_seconds == 10.0
    assert timing.max_wait_seconds == 60.0
    assert timing.legacy_debounce_seconds is None
    assert timing.legacy_reply_backoff_seconds is None


def test_explicit_legacy_debounce_is_preserved_inside_new_bounds() -> None:
    timing = timing_module.resolve_reply_buffer_timing(
        SimpleNamespace(
            personification_batch_debounce_seconds=20.0,
            personification_reply_backoff_seconds=15.0,
        )
    )
    assert timing.base_wait_seconds == 20.0
    assert timing.legacy_debounce_seconds == 20.0
    assert timing.legacy_reply_backoff_seconds == 15.0


def test_legacy_values_cannot_escape_10_60_boundary() -> None:
    lower = timing_module.resolve_reply_buffer_timing(
        SimpleNamespace(personification_batch_debounce_seconds=3.0)
    )
    upper = timing_module.resolve_reply_buffer_timing(
        SimpleNamespace(personification_batch_debounce_seconds=90.0)
    )
    assert lower.base_wait_seconds == 10.0
    assert upper.base_wait_seconds == 60.0


def test_invalid_bounds_are_normalized_fail_closed() -> None:
    timing = timing_module.resolve_reply_buffer_timing(
        SimpleNamespace(
            personification_batch_base_wait_seconds=-1,
            personification_batch_min_wait_seconds=90,
            personification_batch_max_wait_seconds=5,
        )
    )
    assert timing.min_wait_seconds == 60.0
    assert timing.max_wait_seconds == 60.0
    assert timing.base_wait_seconds == 60.0
