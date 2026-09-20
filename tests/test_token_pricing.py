from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from ._loader import load_personification_module


@pytest.fixture
def pricing_env(tmp_path: Path, monkeypatch):
    data_store = load_personification_module("plugin.personification.core.data_store")
    paths = load_personification_module("plugin.personification.core.paths")
    monkeypatch.setattr(paths, "get_data_dir", lambda _cfg=None: tmp_path)
    data_store.init_data_store(SimpleNamespace(personification_data_dir=str(tmp_path)))
    pricing = load_personification_module("plugin.personification.core.token_pricing")
    ledger = load_personification_module("plugin.personification.core.token_ledger")
    return pricing, ledger


def test_exact_model_precedes_explicit_route_default(pricing_env) -> None:
    pricing, _ledger = pricing_env
    default = pricing.create_price_version(
        route_id="route-a", currency="usd", effective_from=10,
        input_per_million="1", output_per_million="2",
    )
    exact = pricing.create_price_version(
        route_id="route-a", model="gpt-x", currency="USD", effective_from=10,
        input_per_million="3", output_per_million="4",
    )
    assert pricing.resolve_price_version(route="route-a", model="gpt-x", observed_at=11)["version_id"] == exact["version_id"]
    assert pricing.resolve_price_version(route="route-a", model="other", observed_at=11)["version_id"] == default["version_id"]
    assert pricing.resolve_price_version(route="route-b", model="gpt-x", observed_at=11) is None


def test_decimal_cost_categories_are_mutually_exclusive(pricing_env) -> None:
    pricing, _ledger = pricing_env
    price = pricing.create_price_version(
        route_id="route-a", model="gpt-x", currency="USD", effective_from=0,
        input_per_million="1", output_per_million="2", cache_read_per_million="0.5",
        cache_create_5m_per_million="1.25", cache_create_1h_per_million="2.5",
    )
    result = pricing.calculate_cost(
        {"input_tokens": 1000, "output_tokens": 100, "cache_read_tokens": 200,
         "cache_create_tokens": 100, "cache_create_5m_tokens": 40,
         "cache_create_1h_tokens": 60, "input_includes_cache": True},
        price,
    )
    assert result["ordinary_input_tokens"] == 700
    assert Decimal(result["cost_decimal"]) == Decimal("0.0012")
    assert result["pricing_complete"] is True


def test_zero_price_differs_from_missing_price(pricing_env) -> None:
    pricing, _ledger = pricing_env
    free = pricing.create_price_version(
        route_id="free", currency="USD", input_per_million="0", output_per_million="0",
    )
    assert pricing.calculate_cost(
        {"input_tokens": 10, "output_tokens": 5, "cache_read_tokens": 0,
         "cache_create_tokens": 0, "input_includes_cache": True}, free
    )["pricing_complete"] is True
    partial = pricing.create_price_version(route_id="partial", currency="USD", input_per_million="0")
    result = pricing.calculate_cost(
        {"input_tokens": 10, "output_tokens": 5, "cache_read_tokens": 0,
         "cache_create_tokens": 0, "input_includes_cache": True}, partial
    )
    assert result["pricing_complete"] is False
    assert result["missing_price_fields"] == ["output_per_million"]


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-1", "1e99", "0.1234567890123456789"])
def test_rejects_unsafe_prices(pricing_env, value: str) -> None:
    pricing, _ledger = pricing_env
    with pytest.raises(ValueError):
        pricing.create_price_version(route_id="r", currency="USD", input_per_million=value)


def test_usage_event_dedup_cache_semantics_and_currency_split(pricing_env) -> None:
    pricing, ledger = pricing_env
    pricing.create_price_version(
        route_id="anthropic-main", model="claude", currency="USD", effective_from=0,
        input_per_million="3", output_per_million="15", cache_read_per_million="0.3",
        cache_create_per_million="3.75",
    )
    pricing.create_price_version(
        route_id="gemini-main", model="gemini", currency="CNY", effective_from=0,
        input_per_million="1", output_per_million="2", cache_read_per_million="0.1",
    )
    assert ledger.record_llm_call(
        event_id="same", route_id="anthropic-main", provider="anthropic", model="claude",
        prompt_tokens=700, completion_tokens=100, cache_read_tokens=200,
        cache_create_tokens=100, bot_id="b1", group_id="g1",
    ) is True
    assert ledger.record_llm_call(
        event_id="same", route_id="anthropic-main", provider="anthropic", model="claude",
        prompt_tokens=700, completion_tokens=100, cache_read_tokens=200,
        cache_create_tokens=100, bot_id="b1", group_id="g1",
    ) is False
    ledger.record_llm_call(
        event_id="gem", route_id="gemini-main", provider="gemini", model="gemini",
        prompt_tokens=1000, completion_tokens=100, cache_read_tokens=0,
        bot_id="b1", group_id="g1",
    )
    result = ledger.query_usage_insights("month", bot_id="b1")
    assert result["call_count"] == 2
    assert result["input_tokens"] == 2000  # Anthropic additive + Gemini inclusive.
    assert result["cache_usage_coverage"] == 1.0
    assert result["cache_read_tokens"] == 200
    assert {item["currency"] for item in result["costs"]} == {"USD", "CNY"}


def test_unknown_cache_is_not_zero_and_reprice_does_not_mutate(pricing_env) -> None:
    pricing, ledger = pricing_env
    version = pricing.create_price_version(
        route_id="r", model="m", currency="USD", effective_from=0,
        input_per_million="1", output_per_million="2",
    )
    ledger.record_llm_call(event_id="e1", route_id="r", model="m", prompt_tokens=10, completion_tokens=5)
    before = ledger.query_usage_insights("month")
    assert before["cache_usage_coverage"] == 0.0
    assert before["cache_read_input_ratio"] is None
    preview = ledger.reprice_usage_preview(version_id=version["version_id"], event_ids=["e1"])
    assert preview["persisted"] is False
    assert preview["event_count"] == 1
    assert ledger.query_usage_insights("month") == before


def test_unknown_cache_keeps_only_safe_subtotal(pricing_env) -> None:
    pricing, _ledger = pricing_env
    price = pricing.create_price_version(
        route_id="r", currency="USD", input_per_million="10", output_per_million="20",
        cache_read_per_million="1", cache_create_per_million="2",
    )
    inclusive = pricing.calculate_cost(
        {"input_tokens": 1000, "output_tokens": 100, "input_includes_cache": True}, price
    )
    assert inclusive["pricing_complete"] is False
    assert inclusive["cost_decimal"] == "0.002"
    assert "cache_read_tokens" in inclusive["missing_usage_fields"]
    additive = pricing.calculate_cost(
        {"input_tokens": 1000, "output_tokens": 100, "input_includes_cache": False}, price
    )
    assert additive["pricing_complete"] is False
    assert additive["cost_decimal"] == "0.012"


def test_inconsistent_cache_usage_is_incomplete(pricing_env) -> None:
    pricing, _ledger = pricing_env
    price = pricing.create_price_version(
        route_id="r", currency="USD", input_per_million="1", output_per_million="1",
        cache_read_per_million="1", cache_create_per_million="1",
    )
    result = pricing.calculate_cost(
        {"input_tokens": 10, "output_tokens": 1, "cache_read_tokens": 8,
         "cache_create_tokens": 4, "cache_create_5m_tokens": 5,
         "cache_create_1h_tokens": 0, "input_includes_cache": True}, price
    )
    assert result["pricing_complete"] is False
    assert set(result["invalid_usage_fields"]) == {
        "cache_tokens_exceed_input_tokens", "cache_ttl_tokens_exceed_creation_tokens"
    }
    assert "cache_read" not in result["breakdown"]
    assert "cache_create_5m" not in result["breakdown"]


def test_anthropic_partial_ttl_creation_is_incomplete(pricing_env) -> None:
    pricing, _ledger = pricing_env
    price = pricing.create_price_version(
        route_id="a", currency="USD", input_per_million="1", output_per_million="2",
        cache_read_per_million="0.1", cache_create_5m_per_million="1.25",
    )
    result = pricing.calculate_cost(
        {"provider": "anthropic", "input_tokens": 10, "output_tokens": 1,
         "cache_read_tokens": 2, "cache_create_5m_tokens": 3,
         "cache_create_1h_tokens": None, "input_includes_cache": False},
        price,
    )
    assert result["pricing_complete"] is False
    assert result["missing_usage_fields"] == ["cache_creation_tokens"]


def test_anthropic_read_known_but_creation_unknown_is_excluded_from_ratio(pricing_env) -> None:
    _pricing, ledger = pricing_env
    ledger.record_llm_call(
        event_id="anthropic-partial", route_id="a", provider="anthropic", model="claude",
        prompt_tokens=100, completion_tokens=1, cache_read_tokens=50,
    )
    result = ledger.query_usage_insights("all")
    assert result["cache_read_tokens"] == 50  # Preserve explicitly reported raw total.
    assert result["cache_read_known_calls"] == 1
    assert result["cache_usage_complete_calls"] == 0
    assert result["cache_usage_coverage"] == 0.0
    assert result["cache_read_input_ratio"] is None


def test_impossible_inclusive_cache_is_excluded_from_ratio(pricing_env) -> None:
    _pricing, ledger = pricing_env
    ledger.record_llm_call(
        event_id="impossible", route_id="o", provider="openai", model="gpt",
        prompt_tokens=10, completion_tokens=1, cache_read_tokens=11,
    )
    result = ledger.query_usage_insights("all")
    assert result["cache_read_tokens"] == 11
    assert result["cache_usage_complete_calls"] == 0
    assert result["cache_usage_coverage"] == 0.0
    assert result["cache_read_input_ratio"] is None


def test_purpose_filter_matches_functional_prefix_exactly(pricing_env) -> None:
    _pricing, ledger = pricing_env
    ledger.record_llm_call(
        event_id="chat", provider="openai", model="gpt", purpose="chat",
        prompt_tokens=10, completion_tokens=1,
    )
    ledger.record_llm_call(
        event_id="chat-percent", provider="openai", model="gpt", purpose="chat%",
        prompt_tokens=20, completion_tokens=2,
    )
    result = ledger.query_usage_insights("all", purpose="chat")
    assert result["call_count"] == 1
    assert result["recent_events"][0]["event_id"] == "chat"
    assert result["filters"]["purpose"] == "chat"


def test_recent_event_projects_ttl_creation_total_and_raw_parts(pricing_env) -> None:
    _pricing, ledger = pricing_env
    ledger.record_llm_call(
        event_id="ttl", provider="anthropic", model="claude",
        prompt_tokens=10, completion_tokens=1, cache_read_tokens=2,
        cache_create_5m_tokens=3, cache_create_1h_tokens=4,
    )
    event = ledger.query_usage_insights("all")["recent_events"][0]
    assert event["cache_creation_tokens"] == 7
    assert event["cache_creation_5m_tokens"] == 3
    assert event["cache_creation_1h_tokens"] == 4
    result = ledger.query_usage_insights("all")
    assert result["legacy_unattributed"] == result["legacy_unattributed_call_count"]


def test_cache_only_anthropic_usage_is_recorded(pricing_env) -> None:
    _pricing, ledger = pricing_env
    assert ledger.record_llm_call(
        event_id="cache-only", route_id="anthropic", provider="anthropic", model="claude",
        prompt_tokens=0, completion_tokens=0, cache_read_tokens=50,
    ) is True
    result = ledger.query_usage_insights("all")
    assert result["call_count"] == 1
    assert result["input_tokens"] == 50
    assert result["cache_read_tokens"] == 50
    assert result["series"][0]["cache_read_tokens"] == 50
    assert result["recent_events"][0]["event_id"] == "cache-only"
    assert result["dimensions"]["route_ids"] == ["anthropic"]


@pytest.mark.parametrize("prompt,completion", [(True, 1), (-1, 1), (1.5, 1), (1, "2")])
def test_invalid_required_usage_is_rejected(pricing_env, prompt, completion) -> None:
    _pricing, ledger = pricing_env
    assert ledger.record_llm_call(
        event_id="invalid", model="m", prompt_tokens=prompt, completion_tokens=completion
    ) is False
    assert ledger.query_usage_insights("all")["call_count"] == 0


def test_reprice_rejects_cross_route_and_cross_model(pricing_env) -> None:
    pricing, ledger = pricing_env
    wrong_route = pricing.create_price_version(
        route_id="other", currency="USD", input_per_million="1", output_per_million="1",
    )
    wrong_model = pricing.create_price_version(
        route_id="r", model="other-model", currency="USD",
        input_per_million="1", output_per_million="1",
    )
    route_default = pricing.create_price_version(
        route_id="r", currency="USD", input_per_million="1", output_per_million="1",
    )
    ledger.record_llm_call(
        event_id="event", route_id="r", model="m", prompt_tokens=10, completion_tokens=5
    )
    with pytest.raises(ValueError, match="route"):
        ledger.reprice_usage_preview(version_id=wrong_route["version_id"], event_ids=["event"])
    with pytest.raises(ValueError, match="model"):
        ledger.reprice_usage_preview(version_id=wrong_model["version_id"], event_ids=["event"])
    assert ledger.reprice_usage_preview(
        version_id=route_default["version_id"], event_ids=["event"]
    )["event_count"] == 1
