"""Mechanical final-text punctuation policy shared by normal and YAML replies.

This runs only for ordinary text bubbles after review/splitting.  It never
inspects dialogue meaning and deliberately leaves QQ control/media payloads to
their own send paths.
"""

from __future__ import annotations

from typing import Any, Iterable


_STRIPPABLE_TERMINALS = frozenset({"，", ",", "。", "！", "!"})
_CLOSING_TERMINALS = frozenset({"”", "’", "》", "）", ")", "】", "」", "』", "〉", "〕", "］", "}"})


def normalize_terminal_punctuation_policy(value: Any) -> str:
    return "preserve" if str(value or "").strip().lower() == "preserve" else "strip_common"


def apply_terminal_punctuation_policy(text: Any, *, policy: Any = "strip_common") -> str:
    """Apply the selected policy without touching quoted/parenthesized endings.

    A final closing delimiter is intentionally a hard boundary: removing the
    punctuation before it would alter a quoted title or parenthetical unit.
    """
    value = str(text or "")
    if normalize_terminal_punctuation_policy(policy) != "strip_common" or not value:
        return value
    # Preserve whitespace exactly; only an actual last visible character may be
    # removed.  This avoids changing platform control strings accidentally.
    tail = len(value)
    while tail > 0 and value[tail - 1].isspace():
        tail -= 1
    if tail <= 0:
        return value
    terminal = value[tail - 1]
    if terminal in _CLOSING_TERMINALS or terminal not in _STRIPPABLE_TERMINALS:
        return value
    return value[: tail - 1] + value[tail:]


def apply_terminal_punctuation_to_segments(
    segments: Iterable[Any], *, policy: Any = "strip_common"
) -> list[str]:
    return [apply_terminal_punctuation_policy(item, policy=policy) for item in segments]


__all__ = [
    "apply_terminal_punctuation_policy",
    "apply_terminal_punctuation_to_segments",
    "normalize_terminal_punctuation_policy",
]
