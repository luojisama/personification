from __future__ import annotations

import copy
import math
import random
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any


MIN_WAIT_SECONDS = 10.0
DEFAULT_WAIT_SECONDS = 30.0
MAX_WAIT_SECONDS = 60.0
MAX_EFFECTIVE_PROBABILITY = 0.98


class AttentionAction(str, Enum):
    REPLY_CANDIDATE = "reply_candidate"
    OBSERVE = "observe"


class AttentionReasonCode(str, Enum):
    """Stable, low-cardinality reasons suitable for traces and metrics."""

    DIRECT_INTERACTION = "direct_interaction"
    CONVERSATION_CONTINUATION = "conversation_continuation"
    AMBIENT_PARTICIPATION = "ambient_participation"
    OBSERVE_LOW_INTEREST = "observe_low_interest"
    OBSERVE_CONTEXT_MISMATCH = "observe_context_mismatch"
    OBSERVE_TIMING = "observe_timing"
    FALLBACK_PRIVATE = "fallback_private"
    FALLBACK_AT_BOT = "fallback_at_bot"
    FALLBACK_REPLY_TO_BOT = "fallback_reply_to_bot"
    FALLBACK_CONTINUATION = "fallback_continuation"
    FALLBACK_GROUP_OBSERVE = "fallback_group_observe"


class ParticipationMode(str, Enum):
    OFF = "off"
    SHADOW = "shadow"
    ON = "on"


class AttentionDecisionSource(str, Enum):
    AGENT = "agent"
    FALLBACK = "fallback"


BASE_PROBABILITIES: Mapping[int, float] = MappingProxyType(
    {
        1: 0.80,
        2: 0.60,
        3: 0.30,
    }
)

_DECISION_FIELDS = frozenset(
    {
        "action",
        "tier",
        "wait_seconds",
        "interest",
        "reason_code",
    }
)

_ATTENTION_DECISION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "action",
        "tier",
        "wait_seconds",
        "interest",
        "reason_code",
    ],
    "properties": {
        "action": {
            "type": "string",
            "enum": [item.value for item in AttentionAction],
        },
        "tier": {"type": "integer", "enum": [1, 2, 3]},
        "wait_seconds": {
            "type": "number",
            "minimum": MIN_WAIT_SECONDS,
            "maximum": MAX_WAIT_SECONDS,
        },
        "interest": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "reason_code": {
            "type": "string",
            "enum": [item.value for item in AttentionReasonCode],
        },
    },
}


class AttentionDecisionValidationError(ValueError):
    """Raised when structured attention data violates the core contract."""


def _finite_number(value: Any, *, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AttentionDecisionValidationError(f"attention_{field}_not_number")
    number = float(value)
    if not math.isfinite(number):
        raise AttentionDecisionValidationError(f"attention_{field}_not_finite")
    return number


def _bounded_number(
    value: Any,
    *,
    field: str,
    minimum: float,
    maximum: float,
) -> float:
    number = _finite_number(value, field=field)
    return max(minimum, min(maximum, number))


def _normalize_tier(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value not in BASE_PROBABILITIES:
        raise AttentionDecisionValidationError("attention_invalid_tier")
    return value


def _normalize_mode(value: Any) -> ParticipationMode:
    if isinstance(value, ParticipationMode):
        return value
    normalized = str(value or "").strip().lower()
    try:
        return ParticipationMode(normalized)
    except ValueError as exc:
        raise AttentionDecisionValidationError("attention_invalid_mode") from exc


def _normalize_source(value: Any) -> AttentionDecisionSource:
    if isinstance(value, AttentionDecisionSource):
        return value
    normalized = str(value or "").strip().lower()
    try:
        return AttentionDecisionSource(normalized)
    except ValueError as exc:
        raise AttentionDecisionValidationError("attention_invalid_source") from exc


@dataclass(frozen=True)
class AttentionDecision:
    """Agent-selected participation intent with mechanically bounded values.

    This type intentionally accepts no message text. The model owns semantic
    judgement; the core only validates enums and clamps numeric boundaries.
    """

    action: AttentionAction
    tier: int
    wait_seconds: float
    interest: float
    reason_code: AttentionReasonCode

    @classmethod
    def json_schema(cls) -> dict[str, Any]:
        return copy.deepcopy(_ATTENTION_DECISION_SCHEMA)

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "AttentionDecision":
        if not isinstance(raw, Mapping):
            raise AttentionDecisionValidationError("attention_decision_not_object")

        unknown = sorted(str(key) for key in raw if key not in _DECISION_FIELDS)
        if unknown:
            raise AttentionDecisionValidationError(
                f"attention_unknown_fields:{','.join(unknown)}"
            )

        missing = sorted(field for field in _DECISION_FIELDS if field not in raw)
        if missing:
            raise AttentionDecisionValidationError(
                f"attention_missing_fields:{','.join(missing)}"
            )

        try:
            action = AttentionAction(str(raw["action"] or "").strip())
        except ValueError as exc:
            raise AttentionDecisionValidationError("attention_invalid_action") from exc

        try:
            reason_code = AttentionReasonCode(str(raw["reason_code"] or "").strip())
        except ValueError as exc:
            raise AttentionDecisionValidationError("attention_invalid_reason_code") from exc

        return cls(
            action=action,
            tier=_normalize_tier(raw["tier"]),
            wait_seconds=_bounded_number(
                raw["wait_seconds"],
                field="wait_seconds",
                minimum=MIN_WAIT_SECONDS,
                maximum=MAX_WAIT_SECONDS,
            ),
            interest=_bounded_number(
                raw["interest"],
                field="interest",
                minimum=0.0,
                maximum=1.0,
            ),
            reason_code=reason_code,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action.value,
            "tier": self.tier,
            "wait_seconds": self.wait_seconds,
            "interest": self.interest,
            "reason_code": self.reason_code.value,
        }


@dataclass(frozen=True)
class AttentionFallbackContext:
    """Trusted structural facts used only when the attention service fails."""

    is_private: bool = False
    is_at_bot: bool = False
    is_reply_to_bot: bool = False
    is_continuation: bool = False


def fallback_attention_decision(context: AttentionFallbackContext) -> AttentionDecision:
    """Fail safely from trusted protocol structure, without reading message text."""

    if not isinstance(context, AttentionFallbackContext):
        raise AttentionDecisionValidationError("attention_invalid_fallback_context")

    if context.is_private:
        reason = AttentionReasonCode.FALLBACK_PRIVATE
    elif context.is_at_bot:
        reason = AttentionReasonCode.FALLBACK_AT_BOT
    elif context.is_reply_to_bot:
        reason = AttentionReasonCode.FALLBACK_REPLY_TO_BOT
    else:
        reason = None

    if reason is not None:
        return AttentionDecision(
            action=AttentionAction.REPLY_CANDIDATE,
            tier=1,
            wait_seconds=MIN_WAIT_SECONDS,
            interest=1.0,
            reason_code=reason,
        )

    if context.is_continuation:
        return AttentionDecision(
            action=AttentionAction.REPLY_CANDIDATE,
            tier=2,
            wait_seconds=DEFAULT_WAIT_SECONDS,
            interest=0.6,
            reason_code=AttentionReasonCode.FALLBACK_CONTINUATION,
        )

    return AttentionDecision(
        action=AttentionAction.OBSERVE,
        tier=3,
        wait_seconds=MAX_WAIT_SECONDS,
        interest=0.0,
        reason_code=AttentionReasonCode.FALLBACK_GROUP_OBSERVE,
    )


def base_probability_for_tier(tier: Any) -> float:
    return BASE_PROBABILITIES[_normalize_tier(tier)]


def effective_reply_probability(tier: Any, unanswered_interactions: Any) -> float:
    """Apply ``1 - (1 - p) ** n`` and cap the result at 0.98."""

    normalized_tier = _normalize_tier(tier)
    if (
        isinstance(unanswered_interactions, bool)
        or not isinstance(unanswered_interactions, int)
        or unanswered_interactions < 0
    ):
        raise AttentionDecisionValidationError("attention_invalid_interaction_count")
    if unanswered_interactions == 0:
        return 0.0
    base = BASE_PROBABILITIES[normalized_tier]
    accumulated = 1.0 - (1.0 - base) ** unanswered_interactions
    return min(MAX_EFFECTIVE_PROBABILITY, accumulated)


def _draw_random(random_source: Callable[[], float]) -> float:
    try:
        value = random_source()
    except Exception as exc:
        raise AttentionDecisionValidationError("attention_random_source_failed") from exc
    draw = _finite_number(value, field="random_draw")
    if draw < 0.0 or draw >= 1.0:
        raise AttentionDecisionValidationError("attention_random_draw_out_of_range")
    return draw


@dataclass(frozen=True)
class ParticipationEvaluation:
    mode: ParticipationMode
    decision_source: AttentionDecisionSource
    decision: AttentionDecision
    evaluated_monotonic: float
    unanswered_interactions: int
    legacy_should_reply: bool
    sampling_performed: bool
    base_probability: float | None
    effective_probability: float | None
    random_draw: float | None
    v2_should_reply: bool | None
    actual_should_reply: bool

    def to_metrics(self) -> dict[str, Any]:
        """Return a fixed, text-free, low-cardinality metrics projection."""

        return {
            "mode": self.mode.value,
            "decision_source": self.decision_source.value,
            "action": self.decision.action.value,
            "tier": self.decision.tier,
            "wait_seconds": self.decision.wait_seconds,
            "interest": self.decision.interest,
            "reason_code": self.decision.reason_code.value,
            "unanswered_interactions": self.unanswered_interactions,
            "sampling_performed": self.sampling_performed,
            "base_probability": self.base_probability,
            "effective_probability": self.effective_probability,
            "v2_should_reply": self.v2_should_reply,
            "legacy_should_reply": self.legacy_should_reply,
            "actual_should_reply": self.actual_should_reply,
        }


def evaluate_attention_decision(
    decision: AttentionDecision,
    *,
    mode: ParticipationMode | str,
    unanswered_interactions: int,
    legacy_should_reply: bool,
    decision_source: AttentionDecisionSource | str = AttentionDecisionSource.AGENT,
    random_source: Callable[[], float] = random.random,
    clock: Callable[[], float] = time.monotonic,
) -> ParticipationEvaluation:
    """Evaluate participation while preserving ``off`` and ``shadow`` behavior.

    ``off`` and ``observe`` never consume the random source. ``shadow`` computes
    the v2 result but preserves the legacy result. ``on`` applies the v2 result.
    """

    if not isinstance(decision, AttentionDecision):
        raise AttentionDecisionValidationError("attention_invalid_decision")
    normalized_mode = _normalize_mode(mode)
    normalized_source = _normalize_source(decision_source)
    if not isinstance(legacy_should_reply, bool):
        raise AttentionDecisionValidationError("attention_invalid_legacy_result")

    # Validate the count even in off mode so metrics cannot contain malformed
    # state that later changes behavior when an administrator enables shadow/on.
    effective = effective_reply_probability(
        decision.tier,
        unanswered_interactions,
    )
    evaluated_monotonic = _finite_number(clock(), field="clock")

    if normalized_mode is ParticipationMode.OFF:
        return ParticipationEvaluation(
            mode=normalized_mode,
            decision_source=normalized_source,
            decision=decision,
            evaluated_monotonic=evaluated_monotonic,
            unanswered_interactions=unanswered_interactions,
            legacy_should_reply=legacy_should_reply,
            sampling_performed=False,
            base_probability=None,
            effective_probability=None,
            random_draw=None,
            v2_should_reply=None,
            actual_should_reply=legacy_should_reply,
        )

    base = base_probability_for_tier(decision.tier)
    if decision.action is AttentionAction.REPLY_CANDIDATE:
        draw = _draw_random(random_source)
        v2_should_reply = draw < effective
        sampling_performed = True
    else:
        draw = None
        v2_should_reply = False
        sampling_performed = False

    actual_should_reply = (
        legacy_should_reply
        if normalized_mode is ParticipationMode.SHADOW
        else v2_should_reply
    )
    return ParticipationEvaluation(
        mode=normalized_mode,
        decision_source=normalized_source,
        decision=decision,
        evaluated_monotonic=evaluated_monotonic,
        unanswered_interactions=unanswered_interactions,
        legacy_should_reply=legacy_should_reply,
        sampling_performed=sampling_performed,
        base_probability=base,
        effective_probability=effective,
        random_draw=draw,
        v2_should_reply=v2_should_reply,
        actual_should_reply=actual_should_reply,
    )


__all__ = [
    "AttentionAction",
    "AttentionDecision",
    "AttentionDecisionSource",
    "AttentionDecisionValidationError",
    "AttentionFallbackContext",
    "AttentionReasonCode",
    "BASE_PROBABILITIES",
    "DEFAULT_WAIT_SECONDS",
    "MAX_EFFECTIVE_PROBABILITY",
    "MAX_WAIT_SECONDS",
    "MIN_WAIT_SECONDS",
    "ParticipationEvaluation",
    "ParticipationMode",
    "base_probability_for_tier",
    "effective_reply_probability",
    "evaluate_attention_decision",
    "fallback_attention_decision",
]
