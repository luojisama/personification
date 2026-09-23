from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from ._loader import load_personification_module

ai_routes = load_personification_module("plugin.personification.core.ai_routes")
context_budget = load_personification_module("plugin.personification.core.context_budget")
llm_context = load_personification_module("plugin.personification.core.llm_context")
reply_turn_trace = load_personification_module("plugin.personification.core.reply_turn_trace")
tool_impl = load_personification_module("plugin.personification.skills.skillpacks.tool_caller.scripts.impl")


class _FakeCaller:
    def __init__(self, name: str, responses: list[object]) -> None:
        self.name = name
        self._responses = list(responses)
        self.messages_seen: list[list[dict]] = []
        self.calls_seen: list[tuple[list[dict], list[dict], bool]] = []

    async def chat_with_tools(self, messages, tools, use_builtin_search):  # noqa: ANN001
        self.messages_seen.append(list(messages or []))
        self.calls_seen.append((list(messages or []), list(tools or []), use_builtin_search))
        response = self._responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response

    def build_tool_result_message(self, tool_call_id: str, tool_name: str, result: str) -> dict[str, str]:
        return {
            "caller": self.name,
            "tool_call_id": tool_call_id,
            "tool_name": tool_name,
            "result": result,
        }


def _http_error(status_code: int) -> RuntimeError:
    error = RuntimeError(f"HTTP {status_code}")
    error.status_code = status_code
    return error


def _valid_response(text: str = "ok") -> object:
    return tool_impl.ToolCallerResponse("stop", text, [], {})


def test_routed_tool_caller_falls_back_after_invalid_primary_response() -> None:
    empty = tool_impl.ToolCallerResponse(
        finish_reason="stop",
        content="",
        tool_calls=[],
        raw={},
    )
    valid = tool_impl.ToolCallerResponse(
        finish_reason="stop",
        content="最终结果",
        tool_calls=[],
        raw={},
    )
    routed = ai_routes.RoutedToolCaller(
        primary_callers=[_FakeCaller("primary", [empty])],
        fallback_caller=_FakeCaller("fallback", [valid]),
        logger=None,
    )

    response = asyncio.run(routed.chat_with_tools([], [], False))

    assert response.content == "最终结果"


def test_routed_tool_caller_routes_tool_result_back_to_originating_caller() -> None:
    response = tool_impl.ToolCallerResponse(
        finish_reason="tool_calls",
        content="",
        tool_calls=[tool_impl.ToolCall(id="call-1", name="web_search", arguments={})],
        raw={},
    )
    primary = _FakeCaller("primary", [response])
    routed = ai_routes.RoutedToolCaller(
        primary_callers=[primary],
        fallback_caller=None,
        logger=None,
    )

    actual = asyncio.run(routed.chat_with_tools([], [], False))
    tool_result = routed.build_tool_result_messages(actual, [(actual.tool_calls[0], "done")])[0]

    assert tool_result["caller"] == "primary"
    assert tool_result["tool_name"] == "web_search"


def test_routed_tool_caller_response_route_survives_duplicate_call_ids() -> None:
    first_response = tool_impl.ToolCallerResponse(
        finish_reason="tool_calls",
        content="",
        tool_calls=[tool_impl.ToolCall(id="same-id", name="first_tool", arguments={})],
        raw={},
    )
    second_response = tool_impl.ToolCallerResponse(
        finish_reason="tool_calls",
        content="",
        tool_calls=[tool_impl.ToolCall(id="same-id", name="second_tool", arguments={})],
        raw={},
    )
    route_error = RuntimeError("route unavailable")
    primary = _FakeCaller("primary", [first_response, route_error])
    fallback = _FakeCaller("fallback", [second_response])
    routed = ai_routes.RoutedToolCaller(
        primary_callers=[primary],
        fallback_caller=fallback,
        logger=None,
    )

    first = asyncio.run(routed.chat_with_tools([], [], False))
    second = asyncio.run(routed.chat_with_tools([], [], False))
    first_results = routed.build_tool_result_messages(first, [(first.tool_calls[0], "one")])
    second_results = routed.build_tool_result_messages(second, [(second.tool_calls[0], "two")])

    assert first_results[0]["caller"] == "primary"
    assert second_results[0]["caller"] == "fallback"
    assert first_results[0]["_personification_routed_caller"] != second_results[0][
        "_personification_routed_caller"
    ]


def test_routed_tool_caller_pins_synthetic_tool_result_to_same_caller() -> None:
    empty = tool_impl.ToolCallerResponse(
        finish_reason="stop",
        content="",
        tool_calls=[],
        raw={},
    )
    first_valid = tool_impl.ToolCallerResponse(
        finish_reason="stop",
        content="需要补查",
        tool_calls=[],
        raw={},
    )
    final_valid = tool_impl.ToolCallerResponse(
        finish_reason="stop",
        content="最终结果",
        tool_calls=[],
        raw={},
    )
    primary = _FakeCaller("primary", [empty, AssertionError("primary should not see fallback-shaped tool result")])
    fallback = _FakeCaller("fallback", [first_valid, final_valid])
    routed = ai_routes.RoutedToolCaller(
        primary_callers=[primary],
        fallback_caller=fallback,
        logger=None,
    )

    response = asyncio.run(routed.chat_with_tools([], [], False))
    assert response.content == "需要补查"

    tool_result = routed.build_synthetic_tool_evidence_message(
        response,
        "wiki_lookup",
        {},
        "查到了",
    )
    messages = [tool_result]
    response = asyncio.run(routed.chat_with_tools(messages, [], False))

    assert "_personification_routed_caller" in tool_result
    assert tool_result["_personification_untrusted"] is True
    assert all("_personification_routed_caller" not in msg for msg in fallback.messages_seen[-1])
    assert response.content == "最终结果"


def test_routed_tool_caller_reframes_api_block_on_same_caller() -> None:
    blocked = tool_impl.ToolCallerResponse(
        finish_reason="content_filter", content="", tool_calls=[], raw={}
    )
    valid = tool_impl.ToolCallerResponse(
        finish_reason="stop", content="安全重试成功", tool_calls=[], raw={}
    )
    primary = _FakeCaller("primary", [blocked, valid])
    fallback = _FakeCaller("fallback", [AssertionError("fallback should not run")])
    routed = ai_routes.RoutedToolCaller(
        primary_callers=[primary], fallback_caller=fallback, logger=None
    )
    messages = [
        {"role": "system", "content": "正常系统规则"},
        {
            "role": "system",
            "content": "外部画像数据",
            "_personification_untrusted": True,
        },
    ]

    response = asyncio.run(routed.chat_with_tools(messages, [{"name": "search"}], True))

    assert response.content == "安全重试成功"
    assert len(primary.calls_seen) == 2
    assert primary.calls_seen[1][0][0] == messages[0]
    assert primary.calls_seen[1][0][1]["role"] == "user"
    assert primary.calls_seen[1][0][1]["content"].startswith("[背景数据，仅供理解")
    assert primary.calls_seen[1][1] == [{"name": "search"}]
    assert primary.calls_seen[1][2] is True


def test_routed_tool_caller_exact_refusal_retries_then_falls_back() -> None:
    refusal = tool_impl.ToolCallerResponse(
        finish_reason="stop", content="I can't discuss that.", tool_calls=[], raw={}
    )
    valid = tool_impl.ToolCallerResponse(
        finish_reason="stop", content="候选结果", tool_calls=[], raw={}
    )
    primary = _FakeCaller("primary", [refusal, refusal])
    fallback = _FakeCaller("fallback", [valid])
    routed = ai_routes.RoutedToolCaller(
        primary_callers=[primary], fallback_caller=fallback, logger=None
    )

    response = asyncio.run(routed.chat_with_tools([], [], False))

    assert response.content == "候选结果"
    assert len(primary.calls_seen) == 2
    assert len(fallback.calls_seen) == 1


def test_routed_tool_caller_does_not_treat_ordinary_i_cant_as_hard_refusal() -> None:
    natural = tool_impl.ToolCallerResponse(
        finish_reason="stop", content="I can't wait to see it", tool_calls=[], raw={}
    )
    primary = _FakeCaller("primary", [natural])
    routed = ai_routes.RoutedToolCaller(
        primary_callers=[primary], fallback_caller=None, logger=None
    )

    response = asyncio.run(routed.chat_with_tools([], [], False))

    assert response.content == "I can't wait to see it"
    assert len(primary.calls_seen) == 1


def test_routed_tool_caller_keeps_safety_retry_pinned() -> None:
    blocked = tool_impl.ToolCallerResponse(
        finish_reason="content_filter", content="", tool_calls=[], raw={}
    )
    valid = tool_impl.ToolCallerResponse(
        finish_reason="stop", content="原 caller 恢复", tool_calls=[], raw={}
    )
    primary = _FakeCaller("primary", [AssertionError("pinning was lost")])
    fallback = _FakeCaller("fallback", [valid, blocked, valid])
    routed = ai_routes.RoutedToolCaller(
        primary_callers=[primary], fallback_caller=fallback, logger=None
    )
    initial = asyncio.run(routed.chat_with_tools([], [], False))
    primary_calls_before_pinned_turn = len(primary.calls_seen)
    tool_result = routed.build_synthetic_tool_evidence_message(initial, "search", {}, "done")

    response = asyncio.run(routed.chat_with_tools([tool_result], [], False))

    assert response.content == "原 caller 恢复"
    assert len(primary.calls_seen) == primary_calls_before_pinned_turn


def test_qzone_routed_tool_caller_preserves_all_safe_route_attempts() -> None:
    candidate_error = RuntimeError("private candidate response")
    candidate_error.code = "provider_model_candidate_unavailable"
    candidate_error.status_code = 404
    candidate_error.retryable = True
    request_error = RuntimeError("private request response")
    request_error.status_code = 422
    primary = _FakeCaller("primary", [candidate_error])
    fallback = _FakeCaller("fallback", [request_error])
    routed = ai_routes.RoutedToolCaller(
        primary_callers=[primary],
        fallback_caller=fallback,
        logger=None,
        route_descriptors=[
            {"name": "antigravity", "api_type": "antigravity_cli", "model": "auto-gemini-3"},
            {"name": "backup", "api_type": "openai", "model": "backup-model"},
        ],
    )
    token = llm_context.set_llm_context(
        purpose="qzone_generation",
        retry_policy=llm_context.LLM_RETRY_POLICY_SINGLE_ATTEMPT,
    )
    try:
        with pytest.raises(ai_routes.RoutedToolCallerError) as caught:
            asyncio.run(routed.chat_with_tools([], [], False))
    finally:
        llm_context.reset_llm_context(token)

    error = caught.value
    assert error.code == "provider_model_candidate_unavailable"
    assert error.status_code == 404
    assert error.retryable is True
    assert [item["provider"] for item in error.route_attempts] == ["antigravity", "backup"]
    assert [item["status_code"] for item in error.route_attempts] == [404, 422]
    assert [item["request_count"] for item in error.route_attempts] == [1, 1]
    assert all("private" not in str(item) for item in error.route_attempts)


def test_normal_routed_tool_caller_raises_structured_aggregate() -> None:
    permission_error = RuntimeError("private permission response")
    permission_error.status_code = 403
    permission_error.code = "opaque-secret-code"
    permission_error.auth_mode = "bearer"
    permission_error.request_count = 2
    request_error = RuntimeError("private request response")
    request_error.status_code = 400
    routed = ai_routes.RoutedToolCaller(
        primary_callers=[_FakeCaller("primary", [permission_error])],
        fallback_caller=_FakeCaller("fallback", [request_error]),
        logger=None,
        route_descriptors=[
            {
                "name": "gemini-primary",
                "api_type": "gemini",
                "model": "gemini-test",
                "gemini_auth_mode": "auto",
            },
            {"name": "openai-fallback", "api_type": "openai", "model": "gpt-test"},
        ],
    )

    with pytest.raises(ai_routes.RoutedToolCallerError) as caught:
        asyncio.run(routed.chat_with_tools([], [], False))

    error = caught.value
    assert error.code == "provider_permission_denied"
    assert error.status_code == 403
    assert [item["code"] for item in error.route_attempts] == [
        "provider_permission_denied",
        "provider_request_rejected",
    ]
    assert error.route_attempts[0]["auth_mode"] == "bearer"
    assert error.route_attempts[0]["request_count"] == 2
    assert "opaque-secret-code" not in str(error.route_attempts)
    assert all("private" not in str(item) for item in error.route_attempts)


def test_provider_route_trace_summary_is_allowlisted_and_redacted() -> None:
    error = ai_routes.RoutedToolCallerError(
        [
            {
                "provider": "zellon primary",
                "api_type": "gemini",
                "model": "gemini-3-flash-agent",
                "status_code": 400,
                "code": "provider_request_rejected",
                "wire_tools_count": 47,
                "tool_schema_hash": "abcdef123456",
                "request_count": 1,
                "upstream_status": "INVALID_ARGUMENT",
                "upstream_detail_code": "function_response_mismatch",
                "exception_type": "GatewaySchemaError",
                "schema_rejection_code": "schema_rejected",
                "api_url": "https://private.example/v1beta?key=secret",
                "prompt": "raw private prompt",
                "response_body": "raw private response",
            }
        ]
    )

    summary = ai_routes.summarize_provider_route_attempts(error)

    assert "provider:zellon_primary" in summary
    assert "api:gemini" in summary
    assert "model:gemini-3-flash-agent" in summary
    assert "http:400" in summary
    assert "tools:47" in summary
    assert "schema:abcdef123456" in summary
    assert "requests:1" in summary
    assert "code:provider_request_rejected" in summary
    assert "exception:GatewaySchemaError" in summary
    assert "schema_rejection:schema_rejected" in summary
    assert "upstream:INVALID_ARGUMENT/function_response_mismatch" in summary
    assert "private.example" not in summary
    assert "secret" not in summary
    assert "raw private" not in summary


def test_context_budget_error_keeps_canonical_code_and_safe_summary() -> None:
    error = context_budget.ContextBudgetExceeded("required request exceeds route input budget")

    _status, code, retryable, *_rest = ai_routes._exception_route_metadata(error)
    summary = ai_routes.summarize_provider_route_attempts(
        ai_routes.RoutedToolCallerError(
            [
                {
                    "provider": "budget-route",
                    "api_type": "openai",
                    "status_code": 0,
                    "code": code,
                    "retryable": retryable,
                    "exception_type": type(error).__name__,
                    "schema_rejection_code": "provider_error_unknown",
                }
            ]
        )
    )

    assert code == "provider_context_budget_exceeded"
    assert retryable is False
    assert "code:provider_context_budget_exceeded" in summary
    assert "exception:ContextBudgetExceeded" in summary
    assert "schema_rejection:provider_error_unknown" in summary


def test_provider_request_trace_includes_safe_error_diagnostics(monkeypatch) -> None:  # noqa: ANN001
    stages: list[dict[str, object]] = []
    monkeypatch.setattr(reply_turn_trace, "record_stage", lambda **kwargs: stages.append(kwargs))
    routed = ai_routes.RoutedToolCaller(
        primary_callers=[_FakeCaller("primary", [])],
        fallback_caller=None,
        logger=None,
        route_descriptors=[{"name": "safe-route", "api_type": "openai", "model": "safe-model"}],
    )
    error = RuntimeError("private provider detail")
    error.status_code = 400

    routed._record_provider_request(
        routed._primary_callers[0],
        request_shape={"request_kind": "function_calling"},
        elapsed_ms=1,
        error=error,
    )

    detail = str(stages[-1]["detail"])
    assert "exception_type=RuntimeError" in detail
    assert "schema_rejection=provider_request_rejected" in detail
    assert "private provider detail" not in detail


@pytest.mark.parametrize(
    ("response", "expected_code", "retryable"),
    [
        (
            tool_impl.ToolCallerResponse("stop", "", [], {}),
            "provider_invalid_response",
            True,
        ),
        (
            tool_impl.ToolCallerResponse(
                "stop",
                "请求被安全策略阻止",
                [],
                {"response": {"candidates": [{"finishReason": "SAFETY"}]}},
            ),
            "provider_safety_block",
            False,
        ),
    ],
)
def test_routed_tool_caller_structures_non_exception_exhaustion(
    response,
    expected_code: str,
    retryable: bool,
) -> None:  # noqa: ANN001
    routed = ai_routes.RoutedToolCaller(
        primary_callers=[_FakeCaller("primary", [response])],
        fallback_caller=None,
        logger=None,
    )
    token = llm_context.set_llm_context(
        purpose="provider_probe",
        retry_policy=llm_context.LLM_RETRY_POLICY_SINGLE_ATTEMPT,
    )
    try:
        with pytest.raises(ai_routes.RoutedToolCallerError) as caught:
            asyncio.run(routed.chat_with_tools([], [], False))
    finally:
        llm_context.reset_llm_context(token)

    assert caught.value.code == expected_code
    assert caught.value.retryable is retryable


def test_routed_tool_caller_marks_timeout_retryable() -> None:
    routed = ai_routes.RoutedToolCaller(
        primary_callers=[_FakeCaller("primary", [TimeoutError("private timeout")])],
        fallback_caller=None,
        logger=None,
    )

    token = llm_context.set_llm_context(
        purpose="legacy_timeout_assertion",
        retry_policy=llm_context.LLM_RETRY_POLICY_SINGLE_ATTEMPT,
    )
    try:
        with pytest.raises(ai_routes.RoutedToolCallerError) as caught:
            asyncio.run(routed.chat_with_tools([], [], False))
    finally:
        llm_context.reset_llm_context(token)

    assert caught.value.code == "provider_timeout"
    assert caught.value.retryable is True


def test_routed_tool_caller_preserves_structured_model_error_on_http_400() -> None:
    inner = RuntimeError("private invalid model detail")
    inner.code = "invalid_model"
    error = RuntimeError("safe outer rejection")
    error.status_code = 400
    error.code = "provider_request_rejected"
    error.wire_tools_count = 3
    error.__cause__ = inner
    routed = ai_routes.RoutedToolCaller(
        primary_callers=[_FakeCaller("primary", [error])],
        fallback_caller=None,
        logger=None,
    )

    with pytest.raises(ai_routes.RoutedToolCallerError) as caught:
        asyncio.run(routed.chat_with_tools([], [{"type": "function"}] * 3, False))

    assert caught.value.code == "provider_model_unavailable"
    assert caught.value.status_code == 400
    assert caught.value.wire_tools_count == 3


def test_routed_tool_caller_recognizes_wrapped_model_error_text_on_http_400() -> None:
    inner = RuntimeError("model is invalid")
    error = RuntimeError("safe outer rejection")
    error.status_code = 400
    error.code = "provider_request_rejected"
    error.wire_tools_count = 1
    error.__cause__ = inner
    routed = ai_routes.RoutedToolCaller(
        primary_callers=[_FakeCaller("primary", [error])],
        fallback_caller=None,
        logger=None,
    )

    with pytest.raises(ai_routes.RoutedToolCallerError) as caught:
        asyncio.run(routed.chat_with_tools([], [{"type": "function"}], False))

    assert caught.value.code == "provider_model_unavailable"


def test_routed_tool_caller_prefers_recoverable_schema_rejection_over_model_error() -> None:
    error = ai_routes.RoutedToolCallerError([
        {
            "provider": "bad-model",
            "status_code": 400,
            "code": "provider_model_unavailable",
            "retryable": False,
            "wire_tools_count": 3,
        },
        {
            "provider": "schema-gateway",
            "status_code": 400,
            "code": "provider_request_rejected",
            "retryable": False,
            "wire_tools_count": 2,
        },
    ])

    assert error.code == "provider_request_rejected"
    assert error.wire_tools_count == 2


def test_routed_safety_error_keeps_actual_wire_schema_count() -> None:
    response = tool_impl.ToolCallerResponse(
        "stop",
        "请求被安全策略阻止",
        [],
        {"response": {"candidates": [{"finishReason": "SAFETY"}]}},
        wire_tools_count=0,
    )
    routed = ai_routes.RoutedToolCaller(
        primary_callers=[_FakeCaller("primary", [response])],
        fallback_caller=None,
        logger=None,
    )
    token = llm_context.set_llm_context(
        purpose="provider_probe",
        retry_policy=llm_context.LLM_RETRY_POLICY_SINGLE_ATTEMPT,
    )
    try:
        with pytest.raises(ai_routes.RoutedToolCallerError) as caught:
            asyncio.run(routed.chat_with_tools([], [{"type": "function"}], False))
    finally:
        llm_context.reset_llm_context(token)

    assert caught.value.code == "provider_safety_block"
    assert caught.value.tools_count == 1
    assert caught.value.wire_tools_count == 0


def test_routed_tool_caller_records_safe_request_shape() -> None:
    error = RuntimeError("private schema response")
    error.status_code = 400
    error.wire_tools_count = 0
    routed = ai_routes.RoutedToolCaller(
        primary_callers=[_FakeCaller("primary", [error])],
        fallback_caller=None,
        logger=None,
    )
    messages = [
        {"role": "system", "content": "system text"},
        {"role": "user", "content": [{"type": "text", "text": "hello"}]},
    ]
    tools = [
        {
            "type": "function",
            "function": {
                "name": "safe_lookup",
                "description": "private description must not be copied",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]

    with pytest.raises(ai_routes.RoutedToolCallerError) as caught:
        asyncio.run(routed.chat_with_tools(messages, tools, True))

    attempt = caught.value.route_attempts[0]
    assert attempt["request_kind"] == "function_calling"
    assert attempt["message_count"] == 2
    assert attempt["prompt_chars"] == len("system texthello")
    assert attempt["tools_count"] == 1
    assert attempt["wire_tools_count"] == 0
    assert len(attempt["tool_names_hash"]) == 12
    assert len(attempt["tool_schema_hash"]) == 12
    assert attempt["builtin_search"] is True
    assert caught.value.tools_count == 1
    assert caught.value.wire_tools_count == 0
    assert caught.value.tool_names_hash == attempt["tool_names_hash"]
    assert "private description" not in str(attempt)


def test_routed_tool_caller_traces_schema_prepare_and_real_multimodal_request(monkeypatch) -> None:  # noqa: ANN001
    stages: list[dict[str, object]] = []
    monotonic_values = iter((10.0, 10.0, 20.0, 20.037))
    monkeypatch.setattr(ai_routes, "time", SimpleNamespace(monotonic=lambda: next(monotonic_values)))
    monkeypatch.setattr(reply_turn_trace, "record_stage", lambda **kwargs: stages.append(kwargs))
    response = tool_impl.ToolCallerResponse("stop", "done", [], {})
    routed = ai_routes.RoutedToolCaller(
        primary_callers=[_FakeCaller("primary", [response])],
        fallback_caller=None,
        logger=None,
        route_descriptors=[{"name": "safe-route", "api_type": "openai", "model": "safe-model"}],
    )
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "private prompt"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,SECRET"}},
                {"type": "image_file", "image_file": {"path": "D:/private.png"}},
                {"type": "image", "source": {"type": "base64", "data": "ANTHROPIC_SECRET"}},
                {"type": "input_audio", "input_audio": {"data": "AUDIO_SECRET", "format": "wav"}},
                {"inlineData": {"mimeType": "video/mp4", "data": "GEMINI_SECRET"}},
                {"type": "video_url", "video_url": {"url": "https://private.invalid/video"}},
            ],
        }
    ]

    actual = asyncio.run(routed.chat_with_tools(messages, [], False))

    assert actual.content == "done"
    assert [stage["key"] for stage in stages] == ["provider_schema_prepare", "provider_request"]
    assert stages[0]["elapsed_ms"] == 0
    assert stages[1]["elapsed_ms"] == 36
    detail = str(stages[1]["detail"])
    assert "image_count=3" in detail
    assert "multimodal=anthropic_image,gemini_inline_data,image_file,image_url,input_audio,video_url" in detail
    assert "multimodal_counts=anthropic_image:1,gemini_inline_data:1,image_file:1,image_url:1,input_audio:1,video_url:1" in detail
    assert "private prompt" not in detail
    assert "private.invalid" not in detail
    assert "SECRET" not in detail
    assert "ANTHROPIC_SECRET" not in detail
    assert "AUDIO_SECRET" not in detail
    assert "GEMINI_SECRET" not in detail


def test_routed_tool_caller_records_budget_preflight_failures_for_each_route(monkeypatch) -> None:  # noqa: ANN001
    stages: list[dict[str, object]] = []
    monkeypatch.setattr(reply_turn_trace, "record_stage", lambda **kwargs: stages.append(kwargs))
    gemini = _FakeCaller("gemini", [])
    openai = _FakeCaller("openai", [])
    routed = ai_routes.RoutedToolCaller(
        primary_callers=[gemini, openai],
        fallback_caller=None,
        logger=None,
        route_descriptors=[
            {"name": "gemini-route", "api_type": "gemini", "model": "gemini-test", "context_window_tokens": 10_000, "max_output_tokens": 1_000},
            {"name": "openai-route", "api_type": "openai", "model": "grok-test", "context_window_tokens": 10_000, "max_output_tokens": 1_000},
        ],
    )
    messages = [{"role": "system", "content": "PRIVATE_SYSTEM_BODY " * 4_000}]
    tools = [{"type": "function", "function": {"name": "safe_tool", "description": "PRIVATE_TOOL_BODY", "parameters": {"type": "object"}}}]

    with pytest.raises(ai_routes.RoutedToolCallerError) as caught:
        asyncio.run(routed.chat_with_tools(messages, tools, False))

    error = caught.value
    assert gemini.calls_seen == []
    assert openai.calls_seen == []
    assert error.code == "provider_context_budget_exceeded"
    assert [item["code"] for item in error.route_attempts] == [
        "provider_context_budget_exceeded",
        "provider_context_budget_exceeded",
    ]
    assert [item["exception_type"] for item in error.route_attempts] == [
        "ContextBudgetExceeded",
        "ContextBudgetExceeded",
    ]
    request_details = [str(item["detail"]) for item in stages if item["key"] == "provider_request"]
    assert len(request_details) == 2
    for detail in request_details:
        assert "http=0" in detail
        assert "exception_type=ContextBudgetExceeded" in detail
        assert "schema_rejection=provider_error_unknown" in detail
        assert "PRIVATE_SYSTEM_BODY" not in detail
        assert "PRIVATE_TOOL_BODY" not in detail


def test_routed_tool_caller_keeps_safe_shape_across_503_then_400(monkeypatch) -> None:  # noqa: ANN001
    stages: list[dict[str, object]] = []
    monkeypatch.setattr(reply_turn_trace, "record_stage", lambda **kwargs: stages.append(kwargs))
    primary_error = RuntimeError("private 503 body")
    primary_error.status_code = 503
    fallback_error = RuntimeError("private 400 body")
    fallback_error.status_code = 400
    routed = ai_routes.RoutedToolCaller(
        primary_callers=[_FakeCaller("primary", [primary_error])],
        fallback_caller=_FakeCaller("fallback", [fallback_error]),
        logger=None,
        route_descriptors=[
            {"name": "primary", "api_type": "gemini", "model": "model-a"},
            {"name": "fallback", "api_type": "openai", "model": "model-b"},
        ],
    )
    messages = [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "https://private.invalid/a"}}]}]

    token = llm_context.set_llm_context(
        purpose="safe_shape_route_assertion",
        retry_policy=llm_context.LLM_RETRY_POLICY_SINGLE_ATTEMPT,
    )
    try:
        with pytest.raises(ai_routes.RoutedToolCallerError) as caught:
            asyncio.run(routed.chat_with_tools(messages, [], False))
    finally:
        llm_context.reset_llm_context(token)

    assert [item["status_code"] for item in caught.value.route_attempts] == [503, 400]
    assert [item["code"] for item in caught.value.route_attempts] == [
        "provider_call_failed",
        "provider_request_rejected",
    ]
    request_stages = [item for item in stages if item["key"] == "provider_request"]
    assert len(request_stages) == 2
    assert all(item["status"] == "error" for item in request_stages)
    combined = " ".join(str(item["detail"]) for item in request_stages)
    assert "http=503" in combined
    assert "http=400" in combined
    assert "image_count=1" in combined
    assert "private.invalid" not in combined
    assert "private 503" not in combined
    assert "private 400" not in combined


def test_routed_tool_caller_cancellation_propagates_without_fallback_or_retry(monkeypatch) -> None:  # noqa: ANN001
    stages: list[dict[str, object]] = []
    monkeypatch.setattr(reply_turn_trace, "record_stage", lambda **kwargs: stages.append(kwargs))
    primary = _FakeCaller("primary", [asyncio.CancelledError()])
    fallback = _FakeCaller("fallback", [AssertionError("cancellation must not fallback")])
    routed = ai_routes.RoutedToolCaller(
        primary_callers=[primary],
        fallback_caller=fallback,
        logger=None,
    )

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(routed.chat_with_tools([], [], False))

    assert len(primary.calls_seen) == 1
    assert fallback.calls_seen == []
    request_stages = [item for item in stages if item["key"] == "provider_request"]
    assert len(request_stages) == 1
    assert request_stages[0]["status"] == "warn"
    assert "code=provider_cancelled" in str(request_stages[0]["detail"])


def test_routed_tool_caller_vision_fallback_success_pins_synthetic_continuation(monkeypatch) -> None:  # noqa: ANN001
    stages: list[dict[str, object]] = []
    monkeypatch.setattr(reply_turn_trace, "record_stage", lambda **kwargs: stages.append(kwargs))
    unavailable = tool_impl.ToolCallerResponse("stop", "", [], {}, vision_unavailable=True)
    fallback_first = tool_impl.ToolCallerResponse("stop", "视觉补救已接手", [], {})
    fallback_continuation = tool_impl.ToolCallerResponse("stop", "最终结果", [], {})
    primary = _FakeCaller("primary", [unavailable, AssertionError("synthetic continuation lost its fallback binding")])
    fallback = _FakeCaller("fallback", [fallback_first, fallback_continuation])
    routed = ai_routes.RoutedToolCaller(
        primary_callers=[primary],
        fallback_caller=fallback,
        logger=None,
        route_descriptors=[
            {"name": "primary", "api_type": "openai", "model": "model-a"},
            {"name": "fallback", "api_type": "gemini", "model": "model-b"},
        ],
    )

    first = asyncio.run(routed.chat_with_tools([], [], False))
    synthetic = routed.build_synthetic_tool_evidence_message(first, "vision_analyze", {}, "safe evidence")
    final = asyncio.run(routed.chat_with_tools([synthetic], [], False))

    assert first.route_key == synthetic["_personification_routed_caller"]
    assert final.content == "最终结果"
    assert len(primary.calls_seen) == 1
    assert len(fallback.calls_seen) == 2
    request_stages = [item for item in stages if item["key"] == "provider_request"]
    assert "code=provider_vision_unavailable" in str(request_stages[0]["detail"])


def test_runtime_builder_wraps_legacy_caller_when_provider_list_is_empty(monkeypatch) -> None:  # noqa: ANN001
    raw_error = RuntimeError("private raw provider error")
    legacy = _FakeCaller("legacy", [raw_error])
    monkeypatch.setattr(ai_routes, "_get_primary_provider_list", lambda *_args: [])
    monkeypatch.setattr(ai_routes, "resolve_global_fallback_provider", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(ai_routes, "_build_tool_caller", lambda _config: legacy)
    monkeypatch.setattr(
        ai_routes,
        "get_primary_provider_config",
        lambda *_args: {"name": "legacy", "api_type": "openai", "model": "legacy-model"},
    )

    caller = ai_routes.build_routed_tool_caller(SimpleNamespace(), logger=None)

    assert isinstance(caller, ai_routes.RoutedToolCaller)
    with pytest.raises(ai_routes.RoutedToolCallerError) as caught:
        asyncio.run(caller.chat_with_tools([], [], False))
    assert caught.value.code == "provider_call_failed"
    assert "private raw provider error" not in str(caught.value.route_attempts)


def test_qzone_probe_uses_response_scoped_route_state() -> None:
    empty = tool_impl.ToolCallerResponse(
        finish_reason="stop",
        content="",
        tool_calls=[],
        raw={},
    )
    tool_response = tool_impl.ToolCallerResponse(
        finish_reason="tool_calls",
        content="",
        tool_calls=[SimpleNamespace(id="probe-call")],
        raw={},
    )
    primary = _FakeCaller("primary", [empty])
    fallback = _FakeCaller("fallback", [tool_response])
    routed = ai_routes.RoutedToolCaller(
        primary_callers=[primary],
        fallback_caller=fallback,
        logger=None,
    )
    token = llm_context.set_llm_context(
        purpose="qzone_provider_probe",
        retry_policy=llm_context.LLM_RETRY_POLICY_SINGLE_ATTEMPT,
    )
    try:
        response = asyncio.run(routed.chat_with_tools([], [{"type": "function"}], False))
    finally:
        llm_context.reset_llm_context(token)

    assert response.tool_calls[0].id == "probe-call"
    assert response.route_key == routed._caller_route_keys[id(fallback)]
    assert not hasattr(routed, "_default_result_caller")
    assert not hasattr(routed, "_tool_call_callers")


def test_provider_retry_makes_exactly_four_wire_attempts(monkeypatch: pytest.MonkeyPatch) -> None:
    caller = _FakeCaller(
        "primary",
        [_http_error(503), _http_error(503), _http_error(503), _valid_response("fourth")],
    )
    routed = ai_routes.RoutedToolCaller(primary_callers=[caller], fallback_caller=None, logger=None)

    async def no_sleep(_delay: float) -> None:
        return None

    monkeypatch.setattr(ai_routes.asyncio, "sleep", no_sleep)
    response = asyncio.run(routed.chat_with_tools([], [], False))

    assert response.content == "fourth"
    assert len(caller.calls_seen) == 4


def test_provider_timeout_retries_while_turn_deadline_remains(monkeypatch: pytest.MonkeyPatch) -> None:
    caller = _FakeCaller("primary", [TimeoutError("upstream timeout"), _valid_response("recovered")])
    routed = ai_routes.RoutedToolCaller(primary_callers=[caller], fallback_caller=None, logger=None)

    async def no_sleep(_delay: float) -> None:
        return None

    monkeypatch.setattr(ai_routes.asyncio, "sleep", no_sleep)

    async def run() -> str:
        token = llm_context.set_llm_context(
            purpose="reply", deadline_monotonic=asyncio.get_running_loop().time() + 10
        )
        try:
            return (await routed.chat_with_tools([], [], False)).content
        finally:
            llm_context.reset_llm_context(token)

    assert asyncio.run(run()) == "recovered"
    assert len(caller.calls_seen) == 2


def test_provider_retry_does_not_replay_explicit_400(monkeypatch: pytest.MonkeyPatch) -> None:
    caller = _FakeCaller("primary", [_http_error(400)])
    routed = ai_routes.RoutedToolCaller(primary_callers=[caller], fallback_caller=None, logger=None)

    async def no_sleep(_delay: float) -> None:
        return None

    monkeypatch.setattr(ai_routes.asyncio, "sleep", no_sleep)
    with pytest.raises(ai_routes.RoutedToolCallerError) as caught:
        asyncio.run(routed.chat_with_tools([], [], False))

    assert len(caller.calls_seen) == 1
    assert caught.value.code == "provider_request_rejected"


def test_provider_retry_exposes_later_400_after_transient(monkeypatch: pytest.MonkeyPatch) -> None:
    caller = _FakeCaller("primary", [_http_error(503), _http_error(400)])
    routed = ai_routes.RoutedToolCaller(primary_callers=[caller], fallback_caller=None, logger=None)

    async def no_sleep(_delay: float) -> None:
        return None

    monkeypatch.setattr(ai_routes.asyncio, "sleep", no_sleep)
    with pytest.raises(ai_routes.RoutedToolCallerError) as caught:
        asyncio.run(routed.chat_with_tools([], [], False))

    assert len(caller.calls_seen) == 2
    assert caught.value.code == "provider_request_rejected"
    assert caught.value.status_code == 400


def test_single_attempt_policy_disables_outer_retry() -> None:
    caller = _FakeCaller("primary", [_http_error(503)])
    routed = ai_routes.RoutedToolCaller(primary_callers=[caller], fallback_caller=None, logger=None)
    token = llm_context.set_llm_context(
        purpose="capability_probe",
        retry_policy=llm_context.LLM_RETRY_POLICY_SINGLE_ATTEMPT,
    )
    try:
        with pytest.raises(ai_routes.RoutedToolCallerError):
            asyncio.run(routed.chat_with_tools([], [], False))
    finally:
        llm_context.reset_llm_context(token)

    assert len(caller.calls_seen) == 1


def test_outer_wire_attempt_disables_sdk_retry_without_disabling_shape_policy() -> None:
    class _PolicyCaller(_FakeCaller):
        async def chat_with_tools(self, messages, tools, use_builtin_search):  # noqa: ANN001
            assert llm_context.use_single_wire_attempt_policy() is True
            # The outer runtime must not masquerade as a probe/QZone policy:
            # internal one-time request-shape compatibility remains separately
            # controlled by the original policy flag.
            assert llm_context.use_single_attempt_retry_policy() is False
            return await super().chat_with_tools(messages, tools, use_builtin_search)

    routed = ai_routes.RoutedToolCaller(
        primary_callers=[_PolicyCaller("primary", [_valid_response()])], fallback_caller=None, logger=None
    )
    assert asyncio.run(routed.chat_with_tools([], [], False)).content == "ok"


def test_provider_retry_respects_absolute_deadline() -> None:
    class _SlowCaller(_FakeCaller):
        async def chat_with_tools(self, messages, tools, use_builtin_search):  # noqa: ANN001
            self.messages_seen.append(list(messages or []))
            self.calls_seen.append((list(messages or []), list(tools or []), use_builtin_search))
            await asyncio.sleep(1)
            return _valid_response()

    caller = _SlowCaller("slow", [])
    routed = ai_routes.RoutedToolCaller(primary_callers=[caller], fallback_caller=None, logger=None)

    async def run() -> None:
        token = llm_context.set_llm_context(
            purpose="reply", deadline_monotonic=asyncio.get_running_loop().time() + 0.02
        )
        try:
            with pytest.raises(ai_routes.RoutedToolCallerError) as caught:
                await routed.chat_with_tools([], [], False)
            assert caught.value.code == "provider_timeout"
        finally:
            llm_context.reset_llm_context(token)

    asyncio.run(run())
    assert len(caller.calls_seen) == 1


def test_provider_retry_sleep_cannot_open_a_new_deadline_window() -> None:
    caller = _FakeCaller("primary", [_http_error(503), _valid_response("must not run")])
    routed = ai_routes.RoutedToolCaller(primary_callers=[caller], fallback_caller=None, logger=None)

    async def run() -> None:
        token = llm_context.set_llm_context(
            purpose="reply", deadline_monotonic=asyncio.get_running_loop().time() + 0.02
        )
        try:
            with pytest.raises(ai_routes.RoutedToolCallerError) as caught:
                await routed.chat_with_tools([], [], False)
            assert caught.value.code == "provider_timeout"
        finally:
            llm_context.reset_llm_context(token)

    asyncio.run(run())
    assert len(caller.calls_seen) == 1


def test_successful_route_scope_survives_wait_for_and_resets_next_turn() -> None:
    primary = _FakeCaller(
        "primary",
        [tool_impl.ToolCallerResponse("stop", "", [], {}), _valid_response("new-turn")],
    )
    fallback = _FakeCaller("fallback", [_valid_response("first"), _valid_response("scoped")])
    routed = ai_routes.RoutedToolCaller(primary_callers=[primary], fallback_caller=fallback, logger=None)

    async def run() -> tuple[str, str, str]:
        token = llm_context.set_llm_context(purpose="reply")
        try:
            # The fallback route succeeds *inside* wait_for's child task.
            # Its affinity must reach the parent turn through the explicitly
            # shared state object, rather than a child-only ContextVar value.
            first = await asyncio.wait_for(routed.chat_with_tools([], [], False), 1)
            second = await routed.chat_with_tools([], [], False)
        finally:
            llm_context.reset_llm_context(token)
        next_token = llm_context.set_llm_context(purpose="reply")
        try:
            third = await routed.chat_with_tools([], [], False)
        finally:
            llm_context.reset_llm_context(next_token)
        return first.content, second.content, third.content

    assert asyncio.run(run()) == ("first", "scoped", "new-turn")
    assert len(fallback.calls_seen) == 2
    # First turn probes primary before fallback; only the fresh context for
    # the third call probes it again.  The second call remained on fallback.
    assert len(primary.calls_seen) == 2


def test_tool_continuation_stays_pinned_despite_turn_route_preference() -> None:
    primary = _FakeCaller("primary", [_valid_response("pinned")])
    fallback = _FakeCaller("fallback", [AssertionError("must not run")])
    routed = ai_routes.RoutedToolCaller(primary_callers=[primary], fallback_caller=fallback, logger=None)
    token = llm_context.set_llm_context(purpose="reply")
    try:
        llm_context.remember_successful_route(routed._caller_route_keys[id(fallback)])
        result = asyncio.run(
            routed.chat_with_tools(
                [{"role": "tool", "content": "done", "_personification_routed_caller": routed._caller_route_keys[id(primary)]}],
                [],
                False,
            )
        )
    finally:
        llm_context.reset_llm_context(token)

    assert result.content == "pinned"
    assert len(primary.calls_seen) == 1
    assert len(fallback.calls_seen) == 0
