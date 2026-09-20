from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from ._loader import load_personification_module


@pytest.fixture
def usage_modules(tmp_path: Path, monkeypatch):
    data_store = load_personification_module("plugin.personification.core.data_store")
    paths = load_personification_module("plugin.personification.core.paths")
    monkeypatch.setattr(paths, "get_data_dir", lambda _cfg=None: tmp_path)
    data_store.init_data_store(SimpleNamespace(personification_data_dir=str(tmp_path)))
    impl = load_personification_module(
        "plugin.personification.skills.skillpacks.tool_caller.scripts.impl"
    )
    ledger = load_personification_module("plugin.personification.core.token_ledger")
    return impl, ledger


@pytest.mark.parametrize(
    ("raw", "provider", "expected"),
    [
        (
            {"usage": {"prompt_tokens": 120, "completion_tokens": 20, "total_tokens": 140,
                       "prompt_tokens_details": {"cached_tokens": 40}}},
            "openai", (120, 20, 40, None),
        ),
        (
            {"usage": {"input_tokens": 80, "output_tokens": 10,
                       "cache_read_input_tokens": 30, "cache_creation_input_tokens": 20}},
            "anthropic", (80, 10, 30, 20),
        ),
        (
            {"usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 12,
                               "totalTokenCount": 112, "cachedContentTokenCount": 25}},
            "gemini", (100, 12, 25, None),
        ),
    ],
)
def test_provider_final_response_fixtures_preserve_cache_semantics(
    usage_modules, raw, provider, expected
) -> None:
    impl, ledger = usage_modules
    usage = impl._extract_usage(raw)
    assert (
        usage["prompt_tokens"], usage["completion_tokens"],
        usage.get("cache_read_input_tokens"), usage.get("cache_creation_input_tokens"),
    ) == expected
    response = SimpleNamespace(
        usage=usage, model_used=f"{provider}-model", usage_event_id=f"{provider}-event",
        usage_route_id=f"{provider}-route", usage_provider=provider,
    )
    assert ledger.record_response_usage(response) is True
    event = ledger.query_usage_insights("all")["recent_events"][0]
    assert event["cache_read_tokens"] == expected[2]
    expected_input = expected[0] + ((expected[2] or 0) + (expected[3] or 0) if provider == "anthropic" else 0)
    assert event["input_tokens"] == expected_input


def test_stream_final_usage_is_recorded_once_even_if_multiple_layers_observe_it(usage_modules) -> None:
    impl, ledger = usage_modules
    # OpenAI-compatible streams report aggregate usage on the final chunk.
    final_chunk = {
        "choices": [],
        "usage": {"prompt_tokens": 50, "completion_tokens": 7, "total_tokens": 57,
                  "prompt_tokens_details": {"cached_tokens": 0}},
    }
    response = SimpleNamespace(
        usage=impl._extract_usage(final_chunk), model_used="stream-model",
        usage_event_id="stream-final", usage_route_id="stream-route", usage_provider="openai",
    )
    assert ledger.record_response_usage(response) is True
    assert ledger.record_response_usage(response) is False
    result = ledger.query_usage_insights("all")
    assert result["call_count"] == 1
    assert result["input_tokens"] == 50
    assert result["output_tokens"] == 7


def test_incomplete_or_malformed_usage_is_not_priced_as_zero(usage_modules) -> None:
    _impl, ledger = usage_modules
    incomplete = SimpleNamespace(
        usage={"prompt_tokens": 0, "completion_tokens": 0, "usage_complete": False},
        model_used="bad", usage_event_id="bad", usage_route_id="route", usage_provider="openai",
    )
    assert ledger.record_response_usage(incomplete) is False
    assert ledger.query_usage_insights("all")["call_count"] == 0


@pytest.mark.parametrize(
    ("provider", "raw"),
    [
        ("openai", {"usage": {"prompt_tokens": 120, "completion_tokens": 20,
                               "prompt_tokens_details": {"cached_tokens": 40}}}),
        ("gemini", {"usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 12,
                                       "cachedContentTokenCount": 25}}),
        ("anthropic", {"usage": {"input_tokens": 80, "output_tokens": 10,
                                  "cache_read_input_tokens": 30,
                                  "cache_creation_input_tokens": 20}}),
    ],
)
def test_real_provider_usage_is_fully_priceable_under_its_own_contract(
    usage_modules, provider: str, raw: dict
) -> None:
    impl, ledger = usage_modules
    pricing = load_personification_module("plugin.personification.core.token_pricing")
    route = f"{provider}-priced"
    model = f"{provider}-model"
    pricing.create_price_version(
        route_id=route, model=model, currency="USD", effective_from=0,
        input_per_million="1", output_per_million="2",
        cache_read_per_million="0.1", cache_create_per_million="1.25",
    )
    usage = impl._extract_usage(raw)
    response = SimpleNamespace(
        usage=usage, model_used=model, usage_event_id=f"{provider}-priced-event",
        usage_route_id=route, usage_provider=provider,
    )
    assert ledger.record_response_usage(response) is True
    event = ledger.query_usage_insights("all")["recent_events"][0]
    assert event["pricing_complete"] is True
    assert event["currency"] == "USD"
    assert event["cost_decimal"] is not None
    if provider in {"openai", "gemini"}:
        # Creation is not part of these providers' response contract and stays unknown.
        assert event["cache_creation_tokens"] is None
