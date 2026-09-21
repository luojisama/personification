from __future__ import annotations

import asyncio
import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.quality_eval.runner import BudgetedCaller, CallBudget, _resolve_pipeline_status, run_agent_case
from ._loader import load_personification_module


llm_context = load_personification_module("plugin.personification.core.llm_context")


def test_budgeted_caller_preserves_full_context_and_route_affinity(tmp_path: Path) -> None:
    seen = []

    class Caller:
        async def chat_with_tools(self, *_args, **_kwargs):
            seen.append(dict(llm_context.current_llm_context()))
            llm_context.remember_successful_route("route-affinity")
            return SimpleNamespace(content="ok")

    async def scenario():
        outer = llm_context.set_llm_context(
            group_id="g", user_id="u", platform="onebot", bot_id="bot",
            purpose="reply", deadline_monotonic=123.0,
        )
        try:
            caller = BudgetedCaller(Caller(), CallBudget(tmp_path / "budget.sqlite", limit=1))
            await caller.chat_with_tools([], [], False)
            restored = llm_context.current_llm_context()
            assert {key: restored[key] for key in ("group_id", "user_id", "platform", "bot_id", "purpose", "deadline_monotonic")} == {
                "group_id": "g", "user_id": "u", "platform": "onebot", "bot_id": "bot", "purpose": "reply", "deadline_monotonic": 123.0,
            }
            assert llm_context.successful_route_key() == "route-affinity"
        finally:
            llm_context.reset_llm_context(outer)

    asyncio.run(scenario())
    assert seen and seen[0]["purpose"] == "reply"
    assert seen[0]["platform"] == "onebot" and seen[0]["bot_id"] == "bot"
    assert seen[0]["retry_policy"] == llm_context.LLM_RETRY_POLICY_SINGLE_ATTEMPT
    assert seen[0]["usage_route_id"] == "quality_eval_pool_1"


def test_pipeline_optional_memory_cancellation_is_diagnostic_but_main_cancellation_is_failed(tmp_path: Path) -> None:
    class SlowCaller:
        async def chat_with_tools(self, *_args, **_kwargs):
            await asyncio.Event().wait()

    async def cancelled_for(purpose: str) -> BudgetedCaller:
        token = llm_context.set_llm_context(purpose=purpose)
        try:
            caller = BudgetedCaller(SlowCaller(), CallBudget(tmp_path / f"{purpose}.sqlite", limit=1))
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(caller.chat_with_tools([], [], False), timeout=0.01)
            return caller
        finally:
            llm_context.reset_llm_context(token)

    optional = asyncio.run(cancelled_for("memory_query_plan"))
    assert optional.failure_types == ["CancelledError"]
    assert optional.failure_events == [{"purpose": "memory_query_plan", "error": "CancelledError"}]
    assert _resolve_pipeline_status("completed", optional) == (
        "completed", [{"purpose": "memory_query_plan", "error": "CancelledError"}], [],
    )
    assert _resolve_pipeline_status("no_reply", optional) == (
        "no_reply", [{"purpose": "memory_query_plan", "error": "CancelledError"}], [],
    )

    critical = asyncio.run(cancelled_for("reply"))
    assert critical.failure_events == [{"purpose": "reply", "error": "CancelledError"}]
    assert _resolve_pipeline_status("completed", critical) == (
        "failed", [], [{"purpose": "reply", "error": "CancelledError"}],
    )


def test_pipeline_budget_exhaustion_stays_terminal_even_with_optional_failure(tmp_path: Path) -> None:
    class Caller:
        async def chat_with_tools(self, *_args, **_kwargs):
            return SimpleNamespace(content="ok")

    caller = BudgetedCaller(Caller(), CallBudget(tmp_path / "budget.sqlite", limit=1))
    caller.failure_events.append({"purpose": "memory_recall_gate", "error": "CancelledError"})
    caller.failure_types.append("CancelledError")
    caller.exhausted = True
    assert _resolve_pipeline_status("completed", caller) == (
        "budget_exhausted", [{"purpose": "memory_recall_gate", "error": "CancelledError"}], [],
    )


def test_run_agent_pipeline_keeps_confirmed_capture_completed_after_optional_timeout(monkeypatch, tmp_path: Path) -> None:
    """Exercise runner projection after the real optional planner catches timeout."""
    impl = importlib.import_module("plugin.personification.skills.skillpacks.tool_caller.scripts.impl")
    adapter = importlib.import_module("scripts.quality_eval.pipeline_adapter")
    query = load_personification_module("plugin.personification.core.memory_query")

    class Caller:
        async def chat_with_tools(self, *_args, **_kwargs):
            await asyncio.Event().wait()

    async def captured_pipeline(_case, *, caller, **_kwargs):
        planned = await query.plan_memory_query("current topic", [], caller, timeout=0.01)
        assert planned.status == "timeout"
        return {
            "status": "completed", "reply": "capture reply", "trace": "fixture-trace",
            "coverage": "pipeline-fixture", "synthetic_receipts": ["confirmed"],
            "send_attempt_count": 1, "confirmed_history": 1, "delivery": "capture_confirmed",
        }

    config_path = tmp_path / "env.json"
    config_path.write_text(json.dumps({"personification_api_pools": [{}, {
        "type": "gemini", "model": "gemini-3.8-flash-high", "api_url": "https://proxy.example/v1",
        "gemini_auth_mode": "bearer", "streaming_mode": "off",
    }]}), encoding="utf-8")
    monkeypatch.setattr(impl, "GeminiToolCaller", lambda *_args, **_kwargs: Caller())
    monkeypatch.setattr(adapter, "run_full_path_case", captured_pipeline)

    result = asyncio.run(run_agent_case({"id": "optional-fixture", "surface": "private"}, {
        "config_path": str(config_path), "budget_db": str(tmp_path / "budget.sqlite"),
        "isolated_db_path": str(tmp_path / "isolated"), "runtime_path": "pipeline", "test_double": True,
    }))

    assert result.status == "completed"
    assert result.delivery == "capture_confirmed" and result.confirmed_history == 1
    assert result.optional_diagnostics == [{"purpose": "memory_query_plan", "error": "CancelledError"}]
    assert result.failure_events == result.optional_diagnostics
    assert result.error == ""
