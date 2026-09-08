from __future__ import annotations

import pytest

from ._loader import load_personification_module


budgeting = load_personification_module("plugin.personification.core.context_budget")
ai_routes = load_personification_module("plugin.personification.core.ai_routes")
trace_mod = load_personification_module("plugin.personification.core.reply_turn_trace")


def test_known_272k_route_preserves_output_and_margin() -> None:
    budget = budgeting.ContextBudget.from_route(
        {"context_window_tokens": 272_000, "max_output_tokens": 16_000}
    )
    assert budget.effective_input_limit == 136_000
    assert budget.safety_margin_tokens == 13_600
    assert (budget.history_tokens, budget.component_tokens, budget.reserve_tokens) == (95_200, 27_200, 13_600)


def test_known_105m_route_does_not_use_more_than_default_half_input() -> None:
    budget = budgeting.ContextBudget.from_route(
        {"context_window_tokens": 1_050_000, "max_output_tokens": 32_000}
    )
    assert budget.effective_input_limit == 525_000
    assert budget.effective_input_limit < budget.context_window_tokens - budget.max_output_tokens - budget.safety_margin_tokens


def test_service_input_capacity_does_not_override_continuity_ratio() -> None:
    budget = budgeting.ContextBudget.from_route(
        {"context_window_tokens": 272_000, "max_input_tokens": 180_000, "input_token_limit": 170_000}
    )
    assert budget.effective_input_limit == 136_000


def test_impossible_output_reserve_is_rejected_not_clamped_to_one_token() -> None:
    with pytest.raises(budgeting.ContextBudgetExceeded, match="reserves exceed"):
        budgeting.ContextBudget.from_route(
            {"context_window_tokens": 4_000, "max_output_tokens": 4_000}
        )


def test_unknown_route_is_conservative_and_large_tool_media_are_rejected() -> None:
    budget = budgeting.ContextBudget.from_route({"model": "gemini-name-is-not-a-profile"})
    assert budget.context_window_tokens == 32_768
    messages = [{"role": "system", "content": "required"}, {"role": "user", "content": {"inline_data": "x" * 50_000}}]
    tools = [{"type": "function", "function": {"name": "large", "parameters": {"description": "x" * 50_000}}}]
    with pytest.raises(budgeting.ContextBudgetExceeded, match="required request exceeds"):
        budgeting.fit_request_to_budget(messages, tools, budget)


def test_one_megabyte_base64_image_does_not_consume_text_token_budget() -> None:
    budget = budgeting.ContextBudget.from_route({"context_window_tokens": 272_000, "max_output_tokens": 16_000})
    image_part = {"inline_data": {"mime_type": "image/png", "data": "a" * (1024 * 1024)}}
    messages = [{"role": "system", "content": "persona"}, {"role": "user", "content": [image_part, {"type": "text", "text": "what is this?"}]}]
    fitted, detail = budgeting.fit_request_to_budget(messages, [], budget)
    assert fitted == messages
    assert detail["estimated_input_tokens"] < 20_000


def test_route_scoped_calibration_is_bounded_ewma() -> None:
    route = "test-route-no-content"
    initial = budgeting.route_estimation_multiplier(route)
    result = budgeting.record_usage_calibration(
        route_key=route, estimated_input_tokens=1_000, usage={"prompt_tokens": 9_000}
    )
    updated = budgeting.route_estimation_multiplier(route)
    assert result["route_estimation_multiplier"] == updated
    assert initial <= updated <= 2.0


def test_context_budget_trace_is_json_counters_only(monkeypatch) -> None:  # noqa: ANN001
    captured: list[dict] = []
    token = trace_mod.set_current_trace_id("trace-budget-test")
    monkeypatch.setattr(trace_mod, "record_stage", lambda **kwargs: captured.append(kwargs))
    try:
        ai_routes.RoutedToolCaller._record_context_budget_stage(
            {"provider": "safe-route"},
            {"estimated_input_tokens": 12, "input_token_limit": 100, "system_tokens": 4,
             "history_tokens": 8, "media_tokens": 0, "budget_source": "configured",
             "raw_prompt": "SECRET_DO_NOT_TRACE", "tool_args": "SECRET_DO_NOT_TRACE"},
        )
    finally:
        trace_mod.reset_current_trace_id(token)
    assert captured and captured[0]["key"] == "context_budget"
    assert "SECRET_DO_NOT_TRACE" not in captured[0]["detail"]
    assert '"estimated_input_tokens":12' in captured[0]["detail"]


def test_trim_keeps_current_request_and_old_tool_pair_is_atomic() -> None:
    budget = budgeting.ContextBudget.from_route({"context_window_tokens": 4_000, "max_output_tokens": 1_000})
    messages = [
        {"role": "system", "content": "rules"},
        {"role": "user", "content": "old " + "x" * 3_500},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "one", "function": {"name": "lookup"}}]},
        {"role": "tool", "tool_call_id": "one", "content": "evidence " + "x" * 3_500},
        {"role": "user", "content": "current"},
    ]
    fitted, detail = budgeting.fit_request_to_budget(messages, [], budget)
    assert detail["estimated_input_tokens"] <= budget.effective_input_limit
    assert fitted[0]["role"] == "system"
    assert fitted[-1] == {"role": "user", "content": "current"}
    assert not any(item.get("role") == "tool" for item in fitted)
    assert not any(item.get("tool_calls") for item in fitted)


def test_structured_history_is_trimmed_before_yaml_can_flatten_it() -> None:
    budget = budgeting.ContextBudget.from_route({"context_window_tokens": 10_000, "max_output_tokens": 2_000})
    history, detail = budgeting.fit_history_to_budget(
        [
            {"role": "user", "content": "old " + "x" * 8_000},
            {"role": "assistant", "content": "old reply " + "x" * 8_000},
            {"role": "user", "content": "recent"},
        ],
        fixed_messages=[{"role": "system", "content": "persona"}, {"role": "user", "content": "current request"}],
        budget=budget,
    )
    assert history == [{"role": "user", "content": "recent"}]
    assert detail["history_estimated_tokens"] <= detail["history_token_limit"]


def test_usage_calibration_is_safe_and_has_no_prompt_content() -> None:
    result = budgeting.usage_calibration(
        estimated_input_tokens=200,
        usage={"prompt_tokens": 100},
    )
    assert result["usage_calibration"] == "conservative"
    assert "content" not in result


def test_yaml_private_history_metadata_is_refit_and_never_sent() -> None:
    budget = budgeting.ContextBudget.from_route({"context_window_tokens": 12_000, "max_output_tokens": 2_000})
    old = {"role": "user", "content": "old " + "x" * 9_000}
    recent = {"role": "user", "content": "recent"}
    rendered = "[old]\n[recent]\n"
    messages = [
        {"role": "system", "content": "persona"},
        {
            "role": "user",
            "content": f"history:\n{rendered}current request",
            "_context_history": [old, recent],
            "_context_history_rendered": rendered,
            "_context_history_render_items": ["[old]\n", "[recent]\n"],
        },
    ]
    fitted, _ = budgeting.fit_request_to_budget(messages, [], budget)
    content = fitted[-1]["content"]
    assert "[old]" not in content and "[recent]" in content
    assert all(not any(key.startswith("_context_history") for key in message) for message in fitted)


def test_yaml_private_history_refit_handles_multimodal_content() -> None:
    budget = budgeting.ContextBudget.from_route({"context_window_tokens": 12_000, "max_output_tokens": 2_000})
    old = {"role": "user", "content": "old " + "x" * 9_000}
    recent = {"role": "user", "content": "recent"}
    messages = [{"role": "system", "content": "persona"}, {"role": "user", "content": [
        {"type": "text", "text": "history:\n[old]\n[recent]\ncurrent"},
        {"type": "image_url", "image_url": {"url": "https://example.invalid/a.png"}},
    ], "_context_history": [old, recent], "_context_history_rendered": "[old]\n[recent]\n",
        "_context_history_renderer": lambda entries: "".join("[recent]\n" for x in entries if x == recent)}]
    fitted, _ = budgeting.fit_request_to_budget(messages, [], budget)
    text = fitted[-1]["content"][0]["text"]
    assert "[old]" not in text and "[recent]" in text
    assert not any(key.startswith("_context_history") for key in fitted[-1])
