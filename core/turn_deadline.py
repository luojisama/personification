from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable


DEFAULT_TURN_TIMEOUT_SECONDS = 180.0
HARD_TURN_TIMEOUT_SECONDS = 600.0
SEND_CONFIRM_RESERVE_SECONDS = 15.0
FINALIZATION_RESERVE_SECONDS = 5.0
GENERATION_RESERVE_SECONDS = SEND_CONFIRM_RESERVE_SECONDS + FINALIZATION_RESERVE_SECONDS


@dataclass(frozen=True)
class TurnDeadline:
    """One monotonic deadline shared by every phase of a reply turn."""

    started_at: float
    expires_at: float
    timeout_seconds: float
    _clock: Callable[[], float] = field(default=time.monotonic, repr=False, compare=False)

    @classmethod
    def start(
        cls,
        *,
        timeout_seconds: float = DEFAULT_TURN_TIMEOUT_SECONDS,
        started_at: float | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> "TurnDeadline":
        started = float(clock() if started_at is None else started_at)
        timeout = min(
            HARD_TURN_TIMEOUT_SECONDS,
            max(0.001, float(timeout_seconds or DEFAULT_TURN_TIMEOUT_SECONDS)),
        )
        return cls(started, started + timeout, timeout, clock)

    def remaining(self, *, reserve_seconds: float = 0.0, now: float | None = None) -> float:
        current = float(self._clock() if now is None else now)
        return max(0.0, self.expires_at - current - max(0.0, float(reserve_seconds)))

    def timeout_for(
        self,
        requested_seconds: float | None,
        *,
        reserve_seconds: float = 0.0,
        now: float | None = None,
    ) -> float:
        available = self.remaining(reserve_seconds=reserve_seconds, now=now)
        if requested_seconds is None:
            return available
        return min(available, max(0.0, float(requested_seconds)))

    def can_start(
        self,
        *,
        minimum_seconds: float = 0.001,
        reserve_seconds: float = 0.0,
        now: float | None = None,
    ) -> bool:
        return self.remaining(reserve_seconds=reserve_seconds, now=now) >= max(
            0.0, float(minimum_seconds)
        )

    def snapshot(self, *, stage: str = "", now: float | None = None) -> dict[str, Any]:
        current = float(self._clock() if now is None else now)
        return {
            "stage": str(stage or ""),
            "timeout_seconds": self.timeout_seconds,
            "elapsed_seconds": max(0.0, current - self.started_at),
            "remaining_seconds": self.remaining(now=current),
            "generation_remaining_seconds": self.remaining(
                reserve_seconds=GENERATION_RESERVE_SECONDS,
                now=current,
            ),
            "send_remaining_seconds": self.remaining(
                reserve_seconds=FINALIZATION_RESERVE_SECONDS,
                now=current,
            ),
            "expired": current >= self.expires_at,
        }


def attach_turn_deadline(
    state: dict[str, Any],
    *,
    timeout_seconds: float = DEFAULT_TURN_TIMEOUT_SECONDS,
    started_at: float | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> TurnDeadline:
    existing = state.get("turn_deadline")
    if isinstance(existing, TurnDeadline):
        state["response_deadline"] = existing.expires_at
        return existing
    deadline = TurnDeadline.start(
        timeout_seconds=timeout_seconds,
        started_at=started_at,
        clock=clock,
    )
    state["turn_deadline"] = deadline
    state["turn_started_at"] = deadline.started_at
    state["response_deadline"] = deadline.expires_at
    return deadline


def get_turn_deadline(state: Any) -> TurnDeadline | None:
    if not isinstance(state, dict):
        return None
    value = state.get("turn_deadline")
    return value if isinstance(value, TurnDeadline) else None


__all__ = [
    "DEFAULT_TURN_TIMEOUT_SECONDS",
    "FINALIZATION_RESERVE_SECONDS",
    "GENERATION_RESERVE_SECONDS",
    "HARD_TURN_TIMEOUT_SECONDS",
    "SEND_CONFIRM_RESERVE_SECONDS",
    "TurnDeadline",
    "attach_turn_deadline",
    "get_turn_deadline",
]
