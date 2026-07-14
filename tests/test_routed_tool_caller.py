from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from ._loader import load_personification_module

ai_routes = load_personification_module("plugin.personification.core.ai_routes")
llm_context = load_personification_module("plugin.personification.core.llm_context")
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
        if isinstance(response, Exception):
            raise response
        return response

    def build_tool_result_message(self, tool_call_id: str, tool_name: str, result: str) -> dict[str, str]:
        return {
            "caller": self.name,
            "tool_call_id": tool_call_id,
            "tool_name": tool_name,
            "result": result,
        }


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
        tool_calls=[SimpleNamespace(id="call-1")],
        raw={},
    )
    primary = _FakeCaller("primary", [response])
    routed = ai_routes.RoutedToolCaller(
        primary_callers=[primary],
        fallback_caller=None,
        logger=None,
    )

    asyncio.run(routed.chat_with_tools([], [], False))
    tool_result = routed.build_tool_result_message("call-1", "web_search", "done")

    assert tool_result["caller"] == "primary"
    assert tool_result["tool_name"] == "web_search"


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

    tool_result = routed.build_tool_result_message("fallback-wiki_lookup-1", "wiki_lookup", "查到了")
    messages = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "fallback-wiki_lookup-1",
                    "type": "function",
                    "function": {"name": "wiki_lookup", "arguments": "{}"},
                }
            ],
        },
        tool_result,
    ]
    response = asyncio.run(routed.chat_with_tools(messages, [], False))

    assert tool_result["caller"] == "fallback"
    assert "_personification_routed_caller" in tool_result
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
    asyncio.run(routed.chat_with_tools([], [], False))
    primary_calls_before_pinned_turn = len(primary.calls_seen)
    tool_result = routed.build_tool_result_message("fallback-search-1", "search", "done")

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
    assert all("private" not in str(item) for item in error.route_attempts)


def test_qzone_probe_does_not_mutate_routed_tool_result_state() -> None:
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
    assert routed._default_result_caller is primary
    assert routed._tool_call_callers == {}
