from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from ._loader import load_personification_module


capabilities = load_personification_module("plugin.personification.core.route_capabilities")
visual_capabilities = load_personification_module("plugin.personification.core.visual_capabilities")


def _route(**overrides):  # noqa: ANN003, ANN202
    values = {
        "provider": "Primary",
        "api_type": "openai-compatible",
        "api_url": "https://user:secret@example.test:443/v1/?api_key=private",
        "model": "Example-Model",
        "media_protocol": "auto",
    }
    values.update(overrides)
    return capabilities.RouteKey.from_config(**values)


def test_route_key_uses_safe_normalized_url_fingerprint() -> None:
    first = _route()
    equivalent = _route(api_url="https://example.test/v1?api_key=private")
    changed = _route(api_url="https://example.test/v2?api_key=private")

    assert first == equivalent
    assert first != changed
    assert len(first.api_url_fingerprint) == 24
    assert len(first.fingerprint) == 24
    serialized = json.dumps(first.to_safe_dict(), ensure_ascii=False)
    assert "secret" not in serialized
    assert "private" not in serialized
    assert "example.test" not in serialized


def test_route_identity_includes_provider_protocol_model_and_media_protocol() -> None:
    baseline = _route()
    assert _route(provider="backup") != baseline
    assert _route(api_type="anthropic") != baseline
    assert _route(model="other-model") != baseline
    assert _route(media_protocol="gemini-native") != baseline


def test_unverified_capability_is_unknown_not_unsupported() -> None:
    registry = capabilities.RouteCapabilityRegistry(clock=lambda: 100.0)

    record = registry.get(_route(), "image_input")

    assert record.state == capabilities.CapabilityState.UNKNOWN
    assert record.source == capabilities.CapabilitySource.HEURISTIC
    assert record.checked_at is None
    assert record.detail_code == "capability_unverified"
    assert record.verification_state.value == "not_run"


def test_module_exposes_a_shared_registry_for_runtime_integration() -> None:
    assert isinstance(
        capabilities.DEFAULT_ROUTE_CAPABILITY_REGISTRY,
        capabilities.RouteCapabilityRegistry,
    )


@pytest.mark.parametrize(
    ("observation", "expected_state", "expected_verification"),
    [
        ("success", "supported", "verified"),
        ("explicit_unsupported", "unsupported", "verified"),
        ("timeout", "unknown", "inconclusive"),
        ("network_error", "unknown", "inconclusive"),
        ("server_error", "unknown", "inconclusive"),
        ("parse_error", "unknown", "inconclusive"),
        ("provider_rejected", "unknown", "inconclusive"),
        ("empty_response", "unknown", "inconclusive"),
    ],
)
def test_probe_observations_preserve_three_state_contract(
    observation: str,
    expected_state: str,
    expected_verification: str,
) -> None:
    registry = capabilities.RouteCapabilityRegistry(clock=lambda: 100.0)
    key = _route()

    record = registry.record_observation(key, "image_input", observation)

    assert record.state.value == expected_state
    assert record.source.value == "probe"
    assert record.verification_state.value == expected_verification


def test_probe_unavailable_keeps_capability_unknown_without_claiming_unsupported() -> None:
    registry = capabilities.RouteCapabilityRegistry(clock=lambda: 100.0)

    record = registry.record_observation(_route(), "audio_input", "probe_unavailable")

    assert record.state.value == "unknown"
    assert record.verification_state.value == "probe_unavailable"


def test_expired_evidence_is_reported_as_stale_unknown_not_reset_to_never_run() -> None:
    registry = capabilities.RouteCapabilityRegistry(clock=lambda: 100.0)
    key = _route()
    registry.record_observation(
        key,
        "function_call",
        "success",
        checked_at=10,
        ttl_seconds=5,
    )

    record = registry.get(key, "function_call", now=20)

    assert record.state.value == "unknown"
    assert record.verification_state.value == "stale"
    assert record.checked_at == 10


def test_evidence_priority_is_manual_runtime_probe_catalog_model_heuristic() -> None:
    registry = capabilities.RouteCapabilityRegistry(clock=lambda: 100.0)
    key = _route()
    registry.record(
        key,
        "function_call",
        state="unsupported",
        source="heuristic",
        checked_at=99,
    )
    registry.record(
        key,
        "function_call",
        state="unsupported",
        source="model_catalog",
        checked_at=98,
    )
    registry.record(
        key,
        "function_call",
        state="unsupported",
        source="provider_catalog",
        checked_at=97,
    )
    registry.record_observation(
        key,
        "function_call",
        "success",
        source="probe",
        checked_at=101,
    )
    registry.record_runtime_success(key, "function_call", checked_at=96)
    registry.record_manual_override(key, "function_call", "unsupported", checked_at=95)

    selected = registry.get(key, "function_call", now=102)

    assert selected.state == capabilities.CapabilityState.UNSUPPORTED
    assert selected.source == capabilities.CapabilitySource.MANUAL


def test_real_runtime_success_is_not_erased_by_transient_probe_failure() -> None:
    registry = capabilities.RouteCapabilityRegistry(clock=lambda: 100.0)
    key = _route()
    registry.record_runtime_success(key, "image_input", checked_at=90)
    registry.record_observation(key, "image_input", "timeout", checked_at=110)

    selected = registry.get(key, "image_input", now=120)

    assert selected.state == capabilities.CapabilityState.SUPPORTED
    assert selected.source == capabilities.CapabilitySource.RUNTIME_SUCCESS


def test_expired_stronger_evidence_falls_back_to_weaker_current_evidence() -> None:
    registry = capabilities.RouteCapabilityRegistry(clock=lambda: 100.0)
    key = _route()
    registry.record(
        key,
        "reasoning",
        state="supported",
        source="provider_catalog",
        checked_at=10,
        expires_at=20,
    )
    registry.record(
        key,
        "reasoning",
        state="unsupported",
        source="heuristic",
        checked_at=15,
        expires_at=200,
    )

    selected = registry.get(key, "reasoning", now=30)

    assert selected.state == capabilities.CapabilityState.UNSUPPORTED
    assert selected.source == capabilities.CapabilitySource.HEURISTIC


def test_route_reconfiguration_invalidates_orphaned_old_evidence() -> None:
    registry = capabilities.RouteCapabilityRegistry(clock=lambda: 100.0)
    old_key = registry.configure_route(
        "agent",
        provider="primary",
        api_type="openai",
        api_url="https://old.example/v1",
        model="m1",
    )
    registry.record_runtime_success(old_key, "function_call")

    new_key = registry.configure_route(
        "agent",
        provider="primary",
        api_type="openai",
        api_url="https://new.example/v1",
        model="m1",
    )

    assert new_key != old_key
    assert registry.get(old_key, "function_call").state == capabilities.CapabilityState.UNKNOWN
    assert registry.route_key("agent") == new_key


def test_identical_route_bindings_share_evidence_without_premature_invalidation() -> None:
    registry = capabilities.RouteCapabilityRegistry(clock=lambda: 100.0)
    key = _route()
    registry.bind_route("reply", key)
    registry.bind_route("agent", key)
    registry.record_runtime_success(key, "image_input")

    registry.bind_route("reply", _route(model="new-model"))

    assert registry.get(key, "image_input").state == capabilities.CapabilityState.SUPPORTED
    assert registry.route_key("agent") == key


def test_capability_matrix_contains_all_contract_fields() -> None:
    registry = capabilities.RouteCapabilityRegistry(clock=lambda: 100.0)
    key = _route()
    registry.record_runtime_success(key, "audio_input")

    matrix = registry.get_capabilities(key).to_dict()

    assert tuple(matrix) == capabilities.CAPABILITY_NAMES
    assert matrix["audio_input"]["state"] == "supported"
    assert all(
        set(value)
        == {"state", "verification_state", "source", "checked_at", "expires_at", "detail_code"}
        for value in matrix.values()
    )


def test_runtime_success_rejects_non_success_state() -> None:
    registry = capabilities.RouteCapabilityRegistry()

    with pytest.raises(ValueError, match="runtime_success"):
        registry.record(
            _route(),
            "video_input",
            state="unknown",
            source="runtime_success",
        )


def test_unknown_capability_name_is_rejected() -> None:
    registry = capabilities.RouteCapabilityRegistry()

    with pytest.raises(ValueError, match="unsupported route capability"):
        registry.get(_route(), "telepathy")


def test_visual_probe_default_and_configurable_timeout_bounds() -> None:
    assert visual_capabilities._PROBE_TIMEOUT_SECONDS == 45.0
    assert visual_capabilities._normalize_probe_timeout_seconds(1) == 5.0
    assert visual_capabilities._normalize_probe_timeout_seconds(75) == 75.0
    assert visual_capabilities._normalize_probe_timeout_seconds(999) == 120.0
    assert visual_capabilities._normalize_probe_timeout_seconds(float("nan")) == 45.0


def test_unparseable_visual_probe_response_remains_unknown() -> None:
    class Caller:
        async def chat_with_tools(self, *_args):  # noqa: ANN002, ANN202
            return SimpleNamespace(
                content="我看到了四种颜色，但不按要求回答",
                finish_reason="stop",
                vision_unavailable=False,
            )

    class Logger:
        def warning(self, _message: str) -> None:
            return None

    result = asyncio.run(
        visual_capabilities.probe_tool_caller_vision(
            route_name="unparseable_probe_route",
            caller=Caller(),
            api_type="openai",
            model="probe-model",
            logger=Logger(),
            timeout_seconds=5,
        )
    )

    assert result is None
    assert visual_capabilities.get_visual_capability_record(
        "unparseable_probe_route",
        "openai",
        "probe-model",
    ) is None
