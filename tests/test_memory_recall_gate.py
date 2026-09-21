from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from ._loader import load_personification_module


gate = load_personification_module("plugin.personification.core.memory_recall_gate")
llm_context = load_personification_module("plugin.personification.core.llm_context")


def test_gate_scopes_optional_purpose_and_propagates_external_cancellation() -> None:
    seen = []
    cancelled = []

    class Caller:
        async def chat_with_tools(self, **_kwargs):
            seen.append(dict(llm_context.current_llm_context()))
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.append(True)
                raise

    async def scenario():
        outer = llm_context.set_llm_context(
            group_id="g", user_id="u", platform="onebot", bot_id="bot",
            purpose="reply", retry_policy=llm_context.LLM_RETRY_POLICY_SINGLE_ATTEMPT,
            deadline_monotonic=123.0,
        )
        wire = llm_context.set_wire_retry_disabled(usage_route_id="route", usage_provider="gemini")
        try:
            task = asyncio.create_task(gate.gate_memory_candidates(
                candidates=[{"memory_id": "m1", "summary": "当前问题", "score": 0.99}],
                query="当前问题", tool_caller=Caller(), minimum_score=0.2, timeout_seconds=30,
            ))
            while not seen:
                await asyncio.sleep(0)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            restored = llm_context.current_llm_context()
            assert restored["purpose"] == "reply"
            assert restored["usage_route_id"] == "route"
        finally:
            llm_context.reset_llm_context(wire)
            llm_context.reset_llm_context(outer)

    asyncio.run(scenario())
    assert cancelled == [True]
    assert seen and seen[0]["purpose"] == "memory_recall_gate"
    assert {key: seen[0][key] for key in ("group_id", "user_id", "platform", "bot_id", "retry_policy", "deadline_monotonic", "usage_route_id", "usage_provider")} == {
        "group_id": "g", "user_id": "u", "platform": "onebot", "bot_id": "bot",
        "retry_policy": llm_context.LLM_RETRY_POLICY_SINGLE_ATTEMPT,
        "deadline_monotonic": 123.0, "usage_route_id": "route", "usage_provider": "gemini",
    }


def test_gate_filters_expired_unverified_social_and_limits_three() -> None:
    class _Caller:
        async def chat_with_tools(self, **_kwargs):  # noqa: ANN003
            return SimpleNamespace(content='{"keep_memory_ids":["m0","m1","m2","m3"],"drop_memory_ids":[],"reason":"相关"}')

    result = asyncio.run(
        gate.gate_memory_candidates(
            candidates=[
                {"memory_id": "expired", "summary": "当前问题", "score": 0.99, "expires_at": 1},
                {
                    "memory_id": "social_candidate",
                    "summary": "当前问题的社交摘要",
                    "score": 0.99,
                    "source_kind": "social_mcp_summary",
                    "summary_status": "candidate",
                    "auto_context_eligible": False,
                },
                *[
                    {"memory_id": f"m{i}", "summary": f"当前问题 资料 {i}", "score": 0.98, "confidence": 0.95}
                    for i in range(5)
                ],
            ],
            query="当前问题",
            tool_caller=_Caller(),
            maximum=3,
            minimum_score=0.2,
        )
    )
    assert [item["memory_id"] for item in result] == ["m0", "m1", "m2"]
    assert all(item["memory_trust"] == "untrusted_data_only" for item in result)

    assert asyncio.run(
        gate.gate_memory_candidates(
            candidates=[{"memory_id": "m1", "summary": "当前问题", "score": 0.99}],
            query="当前问题",
            tool_caller=None,
            minimum_score=0.2,
        )
    ) == []


def test_gate_uses_strict_json_second_stage_and_fail_closes() -> None:
    class _Caller:
        async def chat_with_tools(self, **_kwargs):  # noqa: ANN003
            return SimpleNamespace(content='{"keep_memory_ids":["m2"],"drop_memory_ids":["m1"],"reason":"仅 m2 相关"}')

    result = asyncio.run(
        gate.gate_memory_candidates(
            candidates=[
                {"memory_id": "m1", "summary": "不相关资料", "score": 0.98, "confidence": 0.95},
                {"memory_id": "m2", "summary": "当前问题相关资料", "score": 0.98, "confidence": 0.95},
            ],
            query="当前问题",
            turn_plan=SimpleNamespace(session_goal="回答当前问题"),
            tool_caller=_Caller(),
            maximum=3,
            minimum_score=0.2,
        )
    )
    assert [item["memory_id"] for item in result] == ["m2"]

    class _Broken:
        async def chat_with_tools(self, **_kwargs):  # noqa: ANN003
            raise TimeoutError

    assert asyncio.run(
        gate.gate_memory_candidates(
            candidates=[{"memory_id": "m1", "summary": "当前问题", "score": 0.99, "confidence": 0.99}],
            query="当前问题",
            tool_caller=_Broken(),
            minimum_score=0.2,
        )
    ) == []


def test_gate_does_not_cross_private_memory_owners() -> None:
    class _Caller:
        async def chat_with_tools(self, **_kwargs):  # noqa: ANN003
            return SimpleNamespace(
                content='{"keep_memory_ids":["other-user-private"],"drop_memory_ids":[],"reason":"相关"}'
            )

    result = asyncio.run(
        gate.gate_memory_candidates(
            candidates=[
                {
                    "memory_id": "other-user-private",
                    "summary": "另一位用户的私人偏好",
                    "score": 0.99,
                    "confidence": 0.99,
                    "permission_type": "private_fact",
                    "user_id": "other-user",
                }
            ],
            query="私人偏好",
            tool_caller=_Caller(),
            minimum_score=0.2,
            private_owner_id="current-user",
        )
    )

    assert result == []
