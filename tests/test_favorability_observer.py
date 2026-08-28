from __future__ import annotations

import asyncio
from types import SimpleNamespace

from ._loader import load_personification_module


favorability = load_personification_module("plugin.personification.core.favorability")
observer_module = load_personification_module("plugin.personification.core.favorability_observer")


class _Store:
    def __init__(self) -> None:
        self.payload: dict = {}

    def load_sync(self, _name: str):
        import copy

        return copy.deepcopy(self.payload)

    def mutate_sync(self, _name: str, mutator):
        import copy

        updated = mutator(copy.deepcopy(self.payload))
        self.payload = copy.deepcopy(updated)
        return copy.deepcopy(updated)


def _config(**overrides):
    data = {
        "personification_favorability_enabled": True,
        "personification_favorability_default_score": 0.0,
        "personification_favorability_group_default_score": 35.0,
        "personification_favorability_levels": favorability.DEFAULT_FAVORABILITY_LEVELS.copy(),
        "personification_favorability_event_deltas": favorability.DEFAULT_FAVORABILITY_EVENT_DELTAS.copy(),
        "personification_favorability_daily_positive_cap": 5.0,
        "personification_favorability_group_daily_positive_cap": 10.0,
        "personification_favorability_daily_negative_cap": 30.0,
        "personification_favorability_event_log_limit": 50,
        "personification_favorability_observer_mode": "shadow",
        "personification_favorability_observer_confidence_threshold": 0.65,
        "personification_favorability_observer_delta_cap": 1.5,
        "personification_favorability_observer_debounce_seconds": 0,
        "personification_favorability_observer_min_interval_seconds": 0,
        "personification_favorability_observer_batch_max_messages": 8,
        "personification_favorability_observer_batch_max_chars": 1200,
        "personification_favorability_observer_daily_quota": 500,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def test_parse_assessment_is_typed_and_bounded() -> None:
    assessment = observer_module.parse_favorability_assessment(
        {
            "decision": "increase",
            "requested_delta": 99,
            "confidence": 2,
            "behavior_tags": ["constructive", "not_allowed", "warm", "interesting"],
            "reason": "补充上下文",
            "evidence_summary": "窗口内多次建设性补充",
        }
    )
    assert assessment is not None
    assert assessment.requested_delta == 1.5
    assert assessment.confidence == 1.0
    assert assessment.behavior_tags == ("constructive", "warm", "interesting")
    assert observer_module.parse_favorability_assessment('{"decision":"wat"}') is None


def test_shadow_records_projection_without_score_change(monkeypatch) -> None:
    store = _Store()
    monkeypatch.setattr(favorability, "get_data_store", lambda: store)
    service = favorability.FavorabilityService(plugin_config=_config())
    assessment = observer_module.FavorabilityAssessment(
        decision="increase",
        requested_delta=1.2,
        confidence=0.9,
        behavior_tags=("constructive",),
        reason="持续补充上下文",
        evidence_summary="窗口内多次建设性补充",
    )
    result = service.apply_observer_assessment(
        user_id="10001",
        group_id="200",
        is_private=False,
        assessment=assessment,
        observation_id="obs-1",
        trace_id="trace-1",
        message_ids=["m-1"],
    )
    assert result["status"] == "projected"
    profile = service.peek_user_data("group_user_200_10001")
    assert profile["favorability"] == 0.0
    assert profile["favorability_shadow_events"][0]["projected_delta"] == 1.2
    duplicate = service.apply_observer_assessment(
        user_id="10001", group_id="200", is_private=False,
        assessment=assessment, observation_id="obs-1",
    )
    assert duplicate["status"] == "duplicate"


def test_apply_uses_group_override_and_configured_cap(monkeypatch) -> None:
    store = _Store()
    monkeypatch.setattr(favorability, "get_data_store", lambda: store)
    service = favorability.FavorabilityService(
        plugin_config=_config(
            personification_favorability_observer_mode="apply",
            personification_favorability_observer_delta_cap=0.6,
        )
    )
    service.update_user_data("10001", favorability=20.0)
    assessment = observer_module.FavorabilityAssessment(
        decision="decrease", requested_delta=1.5, confidence=0.95,
        behavior_tags=("hostile",), reason="持续越过边界", evidence_summary="窗口内持续不尊重",
    )
    result = service.apply_observer_assessment(
        user_id="10001", group_id="200", is_private=False,
        assessment=assessment, observation_id="obs-apply",
    )
    assert result["delta"] == -0.6
    assert service.peek_user_data("group_user_200_10001")["favorability"] == 19.4
    event = service.peek_user_data("group_user_200_10001")["favorability_events"][-1]
    assert event["source"] == "observer"
    assert event["mode"] == "apply"
    assert event["scope"] == "group_user"


def test_observer_delta_has_hard_cap_even_if_configuration_is_wider() -> None:
    assessment = observer_module.FavorabilityAssessment(decision="decrease", requested_delta=9, confidence=.9, behavior_tags=(), reason="x", evidence_summary="x")
    assert favorability.FavorabilityService._normalized_observer_delta(assessment, cap=9) == -1.5


def test_shadow_negative_projection_apply_cross_zero_and_low_confidence(monkeypatch) -> None:
    store = _Store(); monkeypatch.setattr(favorability, "get_data_store", lambda: store)
    decrease = observer_module.FavorabilityAssessment(decision="decrease", requested_delta=1.2, confidence=.9, behavior_tags=(), reason="x", evidence_summary="x")
    shadow = favorability.FavorabilityService(plugin_config=_config())
    result = shadow.apply_observer_assessment(user_id="u", group_id="", is_private=True, assessment=decrease, observation_id="shadow")
    profile = shadow.peek_user_data("u")
    assert result["status"] == "projected" and result["new"] == -1.2 and profile["favorability"] == 0
    assert not profile["favorability_events"] and profile["favorability_shadow_events"][-1]["new"] == -1.2
    apply = favorability.FavorabilityService(plugin_config=_config(personification_favorability_observer_mode="apply"))
    apply.update_user_data("v", favorability=.5)
    cross = apply.apply_observer_assessment(user_id="v", group_id="", is_private=True, assessment=observer_module.FavorabilityAssessment(decision="decrease", requested_delta=1, confidence=.9, behavior_tags=(), reason="x", evidence_summary="x"), observation_id="cross")
    assert cross["old"] == .5 and cross["new"] == -.5
    low = apply.apply_observer_assessment(user_id="w", group_id="", is_private=True, assessment=observer_module.FavorabilityAssessment(decision="decrease", requested_delta=1, confidence=.1, behavior_tags=(), reason="x", evidence_summary="x"), observation_id="low")
    assert low["status"] != "applied" and apply.peek_user_data("w") is None


def test_effective_profile_prefers_group_override_then_global(monkeypatch) -> None:
    store = _Store()
    monkeypatch.setattr(favorability, "get_data_store", lambda: store)
    service = favorability.FavorabilityService(plugin_config=_config(personification_favorability_observer_mode="apply"))
    service.update_user_data("10001", favorability=62.0)
    fallback = service.get_effective_profile("10001", "200")
    assert fallback["effective"]["scope_used"] == "global"
    assert fallback["effective"]["fallback_used"] is True
    assert fallback["effective"]["score"] == 62.0
    assessment = observer_module.FavorabilityAssessment(
        decision="increase", requested_delta=0.5, confidence=0.9,
        behavior_tags=("warm",), reason="积极回应", evidence_summary="窗口内回应积极",
    )
    service.apply_observer_assessment(
        user_id="10001", group_id="200", is_private=False,
        assessment=assessment, observation_id="obs-effective",
    )
    effective = service.get_effective_profile("10001", "200")
    assert effective["effective"]["scope_used"] == "group_user"
    assert effective["effective"]["score"] == 62.5


def test_observer_debounced_batch_calls_model_once(monkeypatch) -> None:
    store = _Store()
    monkeypatch.setattr(favorability, "get_data_store", lambda: store)
    calls: list[list[dict]] = []

    async def call_ai(messages, **_kwargs):
        calls.append(messages)
        return {
            "decision": "unchanged",
            "requested_delta": 4,
            "confidence": 0.9,
            "behavior_tags": ["insufficient_context"],
            "reason": "证据不足",
            "evidence_summary": "上下文不足",
        }

    cfg = _config(personification_favorability_observer_debounce_seconds=0)
    service = favorability.FavorabilityService(plugin_config=cfg)
    observer = observer_module.FavorabilityObserver(
        service=service, plugin_config=cfg, call_ai_api=call_ai,
    )

    class Event:
        self_id = "999"
        user_id = "10001"
        group_id = "200"
        message_id = "m-1"

        def get_plaintext(self):
            return "补充了一点上下文"

    async def run():
        assert observer.enqueue_event(Event(), source="group_message")
        Event.message_id = "m-2"
        assert observer.enqueue_event(Event(), source="group_message")
        return await observer.flush_all()

    results = asyncio.run(run())
    assert len(calls) == 1
    assert len(calls[0][1]["content"]) > 0
    assert results[0]["status"] == "projected"
