from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace

import pytest
import httpx

from ._loader import load_personification_module
from .test_runner_render import _FakeLogger, _FakeToolCaller, _register_query_tool, tool_impl

runner = load_personification_module("plugin.personification.agent.runtime.runner")
llm_context = load_personification_module("plugin.personification.core.llm_context")
planner = load_personification_module("plugin.personification.agent.runtime.planner")


def _response(*, content="这局输得挺有节目效果。", calls=None):
    return tool_impl.ToolCallerResponse(finish_reason="stop", content=content, tool_calls=calls or [], raw={})


def _kwargs(caller, registry, **overrides):
    values = dict(
        messages=[{"role": "system", "content": "核心人格；群风格：简短；用户画像：爱玩游戏；近期上下文：输了一局。"},
                  {"role": "user", "content": "又寄了"}],
        registry=registry, tool_caller=caller,
        executor=SimpleNamespace(execute=lambda *_args, **_kwargs: pytest.fail("unexpected action")),
        plugin_config=SimpleNamespace(personification_agent_budget_mode="shadow", personification_tool_disclosure_mode="native",
                                      personification_model_builtin_search_enabled=True, personification_agent_max_steps=10),
        logger=_FakeLogger(),
        precomputed_intent=SimpleNamespace(chat_intent="banter", plugin_question_intent="", ambiguity_level="low"),
        turn_plan=planner.TurnPlan(reply_action="reply", speech_act="participate", research_need="none", output_mode="chat_short",
                                  tool_intent=["none"], session_goal="自然接话"),
        execution_policy=SimpleNamespace(is_context_only=True, reason="semantic_banter"),
        time_budget_seconds=150,
    )
    values.update(overrides)
    return values


def _forbid_optional_stages(monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("context-only route entered an optional tool/research stage")
    for name in ("contextual_query_rewriter", "handle_model_stop", "_select_tool_schemas",
                 "start_active_learning_task", "_infer_intent_decision_with_context", "synthesize_evidence_with_llm"):
        monkeypatch.setattr(runner, name, forbidden)


def test_context_only_wire_is_empty_and_shared_quality_preserves_scope(monkeypatch):
    _forbid_optional_stages(monkeypatch)
    stages = []
    monkeypatch.setattr(runner, "_record_reply_trace_stage", lambda **stage: stages.append(stage))
    qualities = []
    purposes = []

    class Caller(_FakeToolCaller):
        supports_builtin_search = True
        async def chat_with_tools(self, messages, tools, use_builtin_search):
            purposes.append(llm_context.current_llm_context().get("purpose"))
            return await super().chat_with_tools(messages, tools, use_builtin_search)
        async def chat_with_deferred_tools(self, *_args):
            pytest.fail("context-only must not disclose deferred tools")

    async def quality(result, **kwargs):
        qualities.append((result, kwargs, llm_context.current_llm_context().copy()))
        return result
    monkeypatch.setattr(runner, "finalize_agent_reply_quality", quality)
    caller = Caller([_response()])
    registry = _register_query_tool(lambda **_kwargs: pytest.fail("unexpected tool"))
    token = llm_context.set_llm_context(user_id="isolated_user", group_id="isolated_group", purpose="reply")
    try:
        result = asyncio.run(runner.run_agent(**_kwargs(caller, registry)))
        assert llm_context.current_llm_context()["purpose"] == "reply"
    finally:
        llm_context.reset_llm_context(token)
    assert result.text == "这局输得挺有节目效果。"
    assert result.suppress_reply_recovery and not result.tool_calls_made
    assert len(caller.calls) == 1
    assert caller.calls[0]["tools"] == []
    assert caller.calls[0]["use_builtin_search"] is False
    assert purposes == ["reply_generation"]
    assert len(qualities) == 1
    assert qualities[0][2]["purpose"] == "reply_review"
    assert qualities[0][2]["group_id"] == "isolated_group"
    prompt = "\n".join(str(m["content"]) for m in caller.calls[0]["messages"])
    assert "用户画像：爱玩游戏" in prompt and "近期上下文：输了一局" in prompt
    assert "必须先快速查清楚" not in prompt
    assert "当前检索意图主查询" not in prompt
    summary = next(stage for stage in stages if stage["key"] == "agent_execution_summary")
    assert "executed_steps=1" in summary["detail"] and "tool_calls_executed=0" in summary["detail"]
    assert "effective_max_steps=1" in summary["detail"]


def test_context_only_rejects_unexpected_tool_call_and_never_recovers(monkeypatch):
    _forbid_optional_stages(monkeypatch)
    caller = _FakeToolCaller([_response(content="我先查一下", calls=[SimpleNamespace(name="search_web", arguments={})])])
    registry = _register_query_tool(lambda **_kwargs: pytest.fail("unexpected tool"))
    result = asyncio.run(runner.run_agent(**_kwargs(caller, registry)))
    assert result.text == "[NO_REPLY]"
    assert result.failure_code == "context_only_tool_call_rejected"
    assert result.suppress_reply_recovery and not result.tool_calls_made
    assert len(caller.calls) == 1


def test_context_only_phase_reserves_quality_without_negative_tool_phase():
    phases = runner._derive_agent_phase_deadlines(100.0, context_only=True)
    assert phases.tool_deadline is None
    assert phases.synthesis_deadline == 95.0
    assert phases.quality_deadline == 100.0
    assert runner._derive_agent_phase_deadlines(100.0).tool_deadline == 80.0


def test_context_only_generation_timeout_is_bounded_by_remaining_budget(monkeypatch):
    _forbid_optional_stages(monkeypatch)
    cancelled = []
    class SlowCaller:
        async def chat_with_tools(self, messages, tools, builtin):
            assert tools == [] and builtin is False
            try:
                await asyncio.sleep(1)
            finally:
                cancelled.append(True)
    registry = _register_query_tool(lambda **_kwargs: pytest.fail("unexpected tool"))
    started = time.monotonic()
    result = asyncio.run(runner.run_agent(**_kwargs(SlowCaller(), registry, time_budget_seconds=5.02)))
    assert time.monotonic() - started < 0.5
    assert cancelled == [True]
    assert result.failure_code == "agent_model_timeout" and result.suppress_reply_recovery


def test_context_only_quality_timeout_fails_closed(monkeypatch):
    _forbid_optional_stages(monkeypatch)
    monkeypatch.setattr(runner, "_AGENT_QUALITY_RESERVE_SECONDS", 0.02)
    async def quality(*_args, **_kwargs):
        await asyncio.sleep(1)
    monkeypatch.setattr(runner, "finalize_agent_reply_quality", quality)
    registry = _register_query_tool(lambda **_kwargs: pytest.fail("unexpected tool"))
    caller = _FakeToolCaller([_response()])
    started = time.monotonic()
    result = asyncio.run(runner.run_agent(**_kwargs(caller, registry, time_budget_seconds=0.04)))
    assert time.monotonic() - started < 0.5
    assert result.text == "[NO_REPLY]" and result.failure_code == "agent_quality_timeout"
    assert result.suppress_reply_recovery
    assert len(caller.calls) == 1


def test_context_only_inherited_deadline_cannot_be_extended(monkeypatch):
    _forbid_optional_stages(monkeypatch)
    registry = _register_query_tool(lambda **_kwargs: pytest.fail("unexpected tool"))
    caller = _FakeToolCaller([])
    token = llm_context.set_llm_context(deadline_monotonic=time.monotonic() - 1)
    try:
        result = asyncio.run(runner.run_agent(**_kwargs(caller, registry)))
    finally:
        llm_context.reset_llm_context(token)
    assert caller.calls == []
    assert result.failure_code == "agent_model_timeout"


def test_context_only_does_not_run_second_intent_classification(monkeypatch):
    _forbid_optional_stages(monkeypatch)
    registry = _register_query_tool(lambda **_kwargs: pytest.fail("unexpected tool"))
    caller = _FakeToolCaller([_response()])
    result = asyncio.run(runner.run_agent(**_kwargs(caller, registry, precomputed_intent=None)))
    assert result.text == "这局输得挺有节目效果。" and len(caller.calls) == 1


def test_context_only_native_gemini_wire_has_no_function_or_search_tools(monkeypatch):
    _forbid_optional_stages(monkeypatch)
    captured = []
    original_client = httpx.AsyncClient
    def respond(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, json={"candidates": [{"content": {"role": "model", "parts": [{"text": "这局输得挺有节目效果。"}]},
                                                          "finishReason": "STOP"}]})
    monkeypatch.setattr(tool_impl.httpx, "AsyncClient", lambda **kwargs: original_client(transport=httpx.MockTransport(respond), **kwargs))
    caller = tool_impl.GeminiToolCaller(api_key="isolated-test-value", base_url="https://isolated.example/v1beta",
                                       model="gemini-3.8-flash", auth_mode="header")
    registry = _register_query_tool(lambda **_kwargs: pytest.fail("unexpected tool"))
    result = asyncio.run(runner.run_agent(**_kwargs(caller, registry)))
    assert result.text == "这局输得挺有节目效果。"
    assert len(captured) == 1
    assert "tools" not in captured[0]
    assert "functionDeclarations" not in json.dumps(captured[0])
    assert "googleSearch" not in json.dumps(captured[0])


def test_context_only_preserves_provider_and_programming_exception_diagnostics(monkeypatch):
    _forbid_optional_stages(monkeypatch)
    registry = _register_query_tool(lambda **_kwargs: pytest.fail("unexpected tool"))
    error = RuntimeError("isolated provider failure")
    error.code = "provider_request_budget_exhausted"
    caller = _FakeToolCaller([error])
    with pytest.raises(RuntimeError) as caught:
        asyncio.run(runner.run_agent(**_kwargs(caller, registry)))
    assert caught.value is error
    assert caught.value.code == "provider_request_budget_exhausted"
    assert len(caller.calls) == 1


def test_run_agent_derives_policy_from_confirmed_existing_semantics(monkeypatch):
    _forbid_optional_stages(monkeypatch)
    registry = _register_query_tool(lambda **_kwargs: pytest.fail("unexpected tool"))
    caller = _FakeToolCaller([_response()])
    kwargs = _kwargs(caller, registry, execution_policy=None)
    kwargs["precomputed_intent"].llm_source = "primary"
    kwargs["turn_plan"].ambiguity_level = "low"
    result = asyncio.run(runner.run_agent(**kwargs))
    assert result.text == "这局输得挺有节目效果。" and len(caller.calls) == 1
    assert caller.calls[0]["tools"] == [] and caller.calls[0]["use_builtin_search"] is False
