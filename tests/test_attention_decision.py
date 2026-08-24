from __future__ import annotations

import inspect
import json

import pytest

from ._loader import load_personification_module


attention = load_personification_module("plugin.personification.core.attention_decision")


def _decision(**overrides):
    payload = {
        "action": "reply_candidate",
        "tier": 1,
        "wait_seconds": 30,
        "interest": 0.75,
        "reason_code": "direct_interaction",
    }
    payload.update(overrides)
    return attention.AttentionDecision.from_mapping(payload)


class _RandomProbe:
    def __init__(self, value: float = 0.0) -> None:
        self.value = value
        self.calls = 0

    def __call__(self) -> float:
        self.calls += 1
        return self.value


def test_schema_is_strict_and_exposes_locked_contract() -> None:
    schema = attention.AttentionDecision.json_schema()

    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {
        "action",
        "tier",
        "wait_seconds",
        "interest",
        "reason_code",
    }
    assert schema["properties"]["action"]["enum"] == [
        "reply_candidate",
        "observe",
    ]
    assert schema["properties"]["tier"]["enum"] == [1, 2, 3]
    assert schema["properties"]["wait_seconds"]["minimum"] == 10.0
    assert schema["properties"]["wait_seconds"]["maximum"] == 60.0
    assert schema["properties"]["interest"]["minimum"] == 0.0
    assert schema["properties"]["interest"]["maximum"] == 1.0
    assert "direct_interaction" in schema["properties"]["reason_code"]["enum"]


def test_schema_return_value_cannot_mutate_shared_contract() -> None:
    schema = attention.AttentionDecision.json_schema()
    schema["properties"]["action"]["enum"].append("mutated")

    fresh = attention.AttentionDecision.json_schema()

    assert "mutated" not in fresh["properties"]["action"]["enum"]


def test_structured_decision_round_trip() -> None:
    decision = _decision()

    assert decision.action is attention.AttentionAction.REPLY_CANDIDATE
    assert decision.tier == 1
    assert decision.wait_seconds == 30.0
    assert decision.interest == 0.75
    assert decision.reason_code is attention.AttentionReasonCode.DIRECT_INTERACTION
    assert decision.to_dict() == {
        "action": "reply_candidate",
        "tier": 1,
        "wait_seconds": 30.0,
        "interest": 0.75,
        "reason_code": "direct_interaction",
    }


def test_numeric_values_are_mechanically_clamped() -> None:
    low = _decision(wait_seconds=-50, interest=-2)
    high = _decision(wait_seconds=500, interest=4)

    assert low.wait_seconds == 10.0
    assert low.interest == 0.0
    assert high.wait_seconds == 60.0
    assert high.interest == 1.0


@pytest.mark.parametrize(
    ("override", "code"),
    [
        ({"action": "reply"}, "attention_invalid_action"),
        ({"tier": 4}, "attention_invalid_tier"),
        ({"tier": True}, "attention_invalid_tier"),
        ({"reason_code": "free_form_reason"}, "attention_invalid_reason_code"),
        ({"wait_seconds": float("nan")}, "attention_wait_seconds_not_finite"),
        ({"interest": "0.5"}, "attention_interest_not_number"),
    ],
)
def test_invalid_structured_values_are_rejected(override, code) -> None:
    with pytest.raises(attention.AttentionDecisionValidationError, match=code):
        _decision(**override)


def test_missing_and_unknown_fields_are_rejected() -> None:
    with pytest.raises(
        attention.AttentionDecisionValidationError,
        match="attention_missing_fields:interest",
    ):
        attention.AttentionDecision.from_mapping(
            {
                "action": "observe",
                "tier": 3,
                "wait_seconds": 60,
                "reason_code": "observe_low_interest",
            }
        )

    with pytest.raises(
        attention.AttentionDecisionValidationError,
        match="attention_unknown_fields:user_text",
    ):
        attention.AttentionDecision.from_mapping(
            {
                **_decision().to_dict(),
                "user_text": "must not enter the decision contract",
            }
        )


def test_base_probability_is_locked_to_80_60_30() -> None:
    assert attention.base_probability_for_tier(1) == 0.80
    assert attention.base_probability_for_tier(2) == 0.60
    assert attention.base_probability_for_tier(3) == 0.30


def test_effective_probability_uses_accumulation_formula() -> None:
    assert attention.effective_reply_probability(1, 1) == pytest.approx(0.80)
    assert attention.effective_reply_probability(2, 2) == pytest.approx(0.84)
    assert attention.effective_reply_probability(3, 3) == pytest.approx(0.657)
    assert attention.effective_reply_probability(3, 0) == 0.0


def test_effective_probability_is_capped_at_098() -> None:
    assert attention.effective_reply_probability(1, 100) == 0.98
    assert attention.effective_reply_probability(2, 100) == 0.98
    assert attention.effective_reply_probability(3, 100) == 0.98


@pytest.mark.parametrize("count", [-1, True, 1.5, "2"])
def test_invalid_interaction_count_is_rejected(count) -> None:
    with pytest.raises(
        attention.AttentionDecisionValidationError,
        match="attention_invalid_interaction_count",
    ):
        attention.effective_reply_probability(1, count)


def test_reply_candidate_is_sampled_with_injected_random_source() -> None:
    random_probe = _RandomProbe(0.79)

    result = attention.evaluate_attention_decision(
        _decision(),
        mode="on",
        unanswered_interactions=1,
        legacy_should_reply=False,
        random_source=random_probe,
        clock=lambda: 123.5,
    )

    assert random_probe.calls == 1
    assert result.sampling_performed is True
    assert result.random_draw == 0.79
    assert result.v2_should_reply is True
    assert result.actual_should_reply is True
    assert result.evaluated_monotonic == 123.5


def test_probability_boundary_does_not_pass_on_equal_draw() -> None:
    result = attention.evaluate_attention_decision(
        _decision(),
        mode="on",
        unanswered_interactions=1,
        legacy_should_reply=True,
        random_source=lambda: 0.80,
    )

    assert result.v2_should_reply is False
    assert result.actual_should_reply is False


def test_observe_never_samples_and_stays_silent_in_on_mode() -> None:
    random_probe = _RandomProbe(0.0)
    decision = _decision(
        action="observe",
        tier=3,
        wait_seconds=60,
        interest=0,
        reason_code="observe_low_interest",
    )

    result = attention.evaluate_attention_decision(
        decision,
        mode="on",
        unanswered_interactions=4,
        legacy_should_reply=True,
        random_source=random_probe,
    )

    assert random_probe.calls == 0
    assert result.sampling_performed is False
    assert result.random_draw is None
    assert result.v2_should_reply is False
    assert result.actual_should_reply is False


def test_off_mode_preserves_legacy_result_without_sampling() -> None:
    random_probe = _RandomProbe(0.0)

    result = attention.evaluate_attention_decision(
        _decision(),
        mode="off",
        unanswered_interactions=3,
        legacy_should_reply=True,
        random_source=random_probe,
    )

    assert random_probe.calls == 0
    assert result.mode is attention.ParticipationMode.OFF
    assert result.base_probability is None
    assert result.effective_probability is None
    assert result.v2_should_reply is None
    assert result.actual_should_reply is True


def test_shadow_mode_computes_v2_but_preserves_legacy_result() -> None:
    result = attention.evaluate_attention_decision(
        _decision(),
        mode="shadow",
        unanswered_interactions=1,
        legacy_should_reply=False,
        random_source=lambda: 0.1,
    )

    assert result.mode is attention.ParticipationMode.SHADOW
    assert result.v2_should_reply is True
    assert result.actual_should_reply is False


def test_on_mode_applies_v2_result_instead_of_legacy_result() -> None:
    result = attention.evaluate_attention_decision(
        _decision(),
        mode="on",
        unanswered_interactions=1,
        legacy_should_reply=True,
        random_source=lambda: 0.95,
    )

    assert result.mode is attention.ParticipationMode.ON
    assert result.v2_should_reply is False
    assert result.actual_should_reply is False


@pytest.mark.parametrize(
    ("context", "reason_code"),
    [
        (attention.AttentionFallbackContext(is_private=True), "fallback_private"),
        (attention.AttentionFallbackContext(is_at_bot=True), "fallback_at_bot"),
        (
            attention.AttentionFallbackContext(is_reply_to_bot=True),
            "fallback_reply_to_bot",
        ),
    ],
)
def test_service_failure_direct_structure_falls_back_to_tier_one(
    context,
    reason_code,
) -> None:
    decision = attention.fallback_attention_decision(context)

    assert decision.action is attention.AttentionAction.REPLY_CANDIDATE
    assert decision.tier == 1
    assert decision.wait_seconds == 10.0
    assert decision.reason_code.value == reason_code


def test_service_failure_continuation_falls_back_to_tier_two() -> None:
    decision = attention.fallback_attention_decision(
        attention.AttentionFallbackContext(is_continuation=True)
    )

    assert decision.action is attention.AttentionAction.REPLY_CANDIDATE
    assert decision.tier == 2
    assert decision.wait_seconds == 30.0
    assert decision.reason_code is attention.AttentionReasonCode.FALLBACK_CONTINUATION


def test_service_failure_ordinary_group_message_stays_silent() -> None:
    random_probe = _RandomProbe(0.0)
    decision = attention.fallback_attention_decision(
        attention.AttentionFallbackContext()
    )

    result = attention.evaluate_attention_decision(
        decision,
        mode="on",
        unanswered_interactions=1,
        legacy_should_reply=True,
        decision_source="fallback",
        random_source=random_probe,
    )

    assert decision.action is attention.AttentionAction.OBSERVE
    assert decision.tier == 3
    assert decision.reason_code is attention.AttentionReasonCode.FALLBACK_GROUP_OBSERVE
    assert random_probe.calls == 0
    assert result.actual_should_reply is False


def test_direct_structure_has_precedence_over_continuation_fallback() -> None:
    decision = attention.fallback_attention_decision(
        attention.AttentionFallbackContext(
            is_reply_to_bot=True,
            is_continuation=True,
        )
    )

    assert decision.tier == 1
    assert decision.reason_code is attention.AttentionReasonCode.FALLBACK_REPLY_TO_BOT


def test_metrics_projection_is_fixed_and_contains_no_message_text() -> None:
    result = attention.evaluate_attention_decision(
        _decision(),
        mode="shadow",
        unanswered_interactions=2,
        legacy_should_reply=False,
        random_source=lambda: 0.5,
        clock=lambda: 987.0,
    )

    metrics = result.to_metrics()
    rendered = json.dumps(metrics, ensure_ascii=False, sort_keys=True)

    assert set(metrics) == {
        "mode",
        "decision_source",
        "action",
        "tier",
        "wait_seconds",
        "interest",
        "reason_code",
        "unanswered_interactions",
        "sampling_performed",
        "base_probability",
        "effective_probability",
        "v2_should_reply",
        "legacy_should_reply",
        "actual_should_reply",
    }
    assert "message" not in rendered.lower()
    assert "prompt" not in rendered.lower()
    assert "user_text" not in rendered.lower()
    assert 987.0 == result.evaluated_monotonic


def test_public_core_api_accepts_no_user_text_or_message_payload() -> None:
    fallback_parameters = inspect.signature(
        attention.fallback_attention_decision
    ).parameters
    evaluate_parameters = inspect.signature(
        attention.evaluate_attention_decision
    ).parameters

    assert set(fallback_parameters) == {"context"}
    assert "text" not in evaluate_parameters
    assert "message" not in evaluate_parameters


@pytest.mark.parametrize("mode", ["apply", "enabled", "", None])
def test_only_off_shadow_on_modes_are_accepted(mode) -> None:
    with pytest.raises(
        attention.AttentionDecisionValidationError,
        match="attention_invalid_mode",
    ):
        attention.evaluate_attention_decision(
            _decision(),
            mode=mode,
            unanswered_interactions=1,
            legacy_should_reply=False,
        )


@pytest.mark.parametrize("draw", [-0.1, 1.0, float("inf"), True])
def test_invalid_random_draw_is_rejected(draw) -> None:
    with pytest.raises(attention.AttentionDecisionValidationError):
        attention.evaluate_attention_decision(
            _decision(),
            mode="on",
            unanswered_interactions=1,
            legacy_should_reply=False,
            random_source=lambda: draw,
        )
