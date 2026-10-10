"""Additional observable cancellation/concurrency contracts for offline runs."""
from __future__ import annotations

import asyncio
import json
from collections import Counter
import pytest

from .test_context_only_agent import _kwargs, _response, _forbid_optional_stages, runner, llm_context, _register_query_tool


@pytest.mark.parametrize("pipeline", ["normal", "yaml"])
@pytest.mark.parametrize("surface", ["private", "group"])
def test_real_processor_call_ledger(monkeypatch, tmp_path, record_property, pipeline, surface):
    from scripts.quality_eval import pipeline_adapter
    from .test_context_only_memory import test_real_processors_derive_context_only_from_successful_llm
    original = pipeline_adapter.run_full_path_case
    observed = []
    async def capture(*args, **kwargs):
        result = await original(*args, **kwargs)
        observed.extend(kwargs["caller"].calls)
        return result
    monkeypatch.setattr(pipeline_adapter, "run_full_path_case", capture)
    test_real_processors_derive_context_only_from_successful_llm(monkeypatch, tmp_path, pipeline, surface)
    counts = Counter(purpose for purpose, _, _ in observed)
    assert counts["reply_generation"] == 1
    assert counts["reply_review"] >= 1
    assert all(tool_count == 0 and search is False for _, tool_count, search in observed)
    record_property("observed_model_purposes", json.dumps(dict(counts), sort_keys=True))
    record_property("observed_total_provider_calls", len(observed))
    record_property("observed_generation_calls", counts["reply_generation"])
    record_property("transport", "scripted provider; synthetic confirmed receipt")


def test_concurrent_context_only_turns_keep_scope_and_single_generation(monkeypatch, record_property):
    _forbid_optional_stages(monkeypatch)
    observations = []
    async def quality(result, **kwargs):
        await asyncio.sleep(0)
        observations.append(("review", llm_context.current_llm_context()["user_id"]))
        return result
    monkeypatch.setattr(runner, "finalize_agent_reply_quality", quality)
    async def scenario():
        gate = asyncio.Event()
        started = 0
        async def turn(user):
            class Caller:
                async def chat_with_tools(self, messages, tools, search):
                    nonlocal started
                    assert tools == [] and search is False
                    observations.append(("generation", llm_context.current_llm_context()["user_id"]))
                    started += 1
                    if started == 2:
                        gate.set()
                    await gate.wait()
                    assert llm_context.current_llm_context()["user_id"] == user
                    return _response(content=user)
            token = llm_context.set_llm_context(user_id=user, purpose="reply")
            try:
                result = await runner.run_agent(**_kwargs(Caller(), _register_query_tool(lambda **kw: pytest.fail("unexpected tool"))))
                assert result.text == user
                assert llm_context.current_llm_context()["purpose"] == "reply"
            finally:
                llm_context.reset_llm_context(token)
        await asyncio.wait_for(asyncio.gather(turn("isolated-a"), turn("isolated-b")), timeout=2)
    asyncio.run(scenario())
    assert sorted(observations) == [("generation", "isolated-a"), ("generation", "isolated-b"), ("review", "isolated-a"), ("review", "isolated-b")]
    record_property("observed_generation_calls", 2)
    record_property("concurrent_turns", 2)
    record_property("quality_boundary", "mocked, one per turn")


def test_context_only_owner_cancel_drains_provider_without_recovery(monkeypatch, record_property):
    _forbid_optional_stages(monkeypatch)
    calls, drained = [], []
    async def scenario():
        entered = asyncio.Event()
        class Caller:
            async def chat_with_tools(self, messages, tools, search):
                calls.append(1)
                entered.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    drained.append(1)
        task = asyncio.create_task(runner.run_agent(**_kwargs(Caller(), _register_query_tool(lambda **kw: pytest.fail("unexpected tool")))))
        await asyncio.wait_for(entered.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert task.cancelled()
    asyncio.run(scenario())
    assert calls == drained == [1]
    record_property("observed_generation_calls", len(calls))
    record_property("provider_finalizers", len(drained))
