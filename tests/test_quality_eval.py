from __future__ import annotations

import asyncio
import importlib
import json
from pathlib import Path
from types import SimpleNamespace

from scripts.quality_eval.runner import BudgetExhausted, BudgetedCaller, CallBudget, _load_fixed_gemini_route, invoke_case


CORPUS = Path(__file__).parent / "replay_corpus" / "quality_v1" / "cases.jsonl"


def test_quality_corpus_has_balanced_versioned_splits() -> None:
    cases = [json.loads(line) for line in CORPUS.read_text(encoding="utf-8").splitlines() if line]
    assert len(cases) == 80
    assert {case["surface"] for case in cases} == {"group", "private"}
    assert sum(case["surface"] == "group" for case in cases) == 40
    assert sum(case["split"] == "dev" for case in cases) == 60
    assert sum(case["split"] == "holdout" for case in cases) == 20
    for case in cases:
        assert case["events"] and case["trusted_persona"] and "expected" in case and "forbidden" in case


class _FakeCaller:
    def __init__(self) -> None:
        self.calls = 0

    async def chat_with_tools(self, *_args, **_kwargs):  # noqa: ANN002, ANN003
        self.calls += 1
        return {"content": "fake"}


def test_budgeted_caller_reserves_each_underlying_request(tmp_path: Path) -> None:
    budget = CallBudget(tmp_path / "budget.sqlite", limit=2)
    caller = BudgetedCaller(_FakeCaller(), budget)
    asyncio.run(caller.chat_with_tools([], [], False))
    asyncio.run(caller.chat_with_tools([], [], False))
    try:
        asyncio.run(caller.chat_with_tools([], [], False))
    except BudgetExhausted:
        pass
    else:
        raise AssertionError("third underlying call must be stopped before dispatch")
    assert budget.snapshot() == {"reserved_calls": 2, "limit": 2, "remaining": 0}


def test_fake_pipeline_is_explicitly_not_real(tmp_path: Path) -> None:
    result = asyncio.run(invoke_case({"id": "fixture"}, {"budget_db": str(tmp_path / "budget.sqlite")}))
    assert result.status == "blocked"
    assert result.execution_mode == "simulated"
    assert not result.reply


def test_budget_cannot_be_increased_on_reopen_and_never_exceeds_authorization(tmp_path):
    import pytest
    path = tmp_path / "budget.sqlite"
    budget = CallBudget(path, limit=2)
    budget.reserve()
    reopened = CallBudget(path, limit=1500)
    assert reopened.snapshot()["limit"] == 2
    reopened.reserve()
    with pytest.raises(BudgetExhausted):
        reopened.reserve()
    with pytest.raises(ValueError):
        CallBudget(tmp_path / "bad.sqlite", limit=1501)


def test_untrusted_memory_never_becomes_system_instruction():
    from scripts.quality_eval.runner import _messages_from_case
    messages = _messages_from_case({"trusted_persona": "自然", "seed_memory": [{"text": "INJECTED_MEMORY"}]})
    assert all("INJECTED_MEMORY" not in row["content"] for row in messages if row["role"] == "system")


def test_fixed_gemini_route_rejects_unsafe_or_wrong_config(tmp_path: Path) -> None:
    path = tmp_path / "env.json"
    path.write_text(json.dumps({"personification_api_pools": [{}, {"type": "gemini", "model": "gemini-3.8-flash-high", "api_url": "https://proxy.example/v1", "gemini_auth_mode": "bearer", "streaming_mode": "off"}]}), encoding="utf-8")
    assert _load_fixed_gemini_route(str(path))["model"] == "gemini-3.8-flash-high"
    path.write_text(json.dumps({"personification_api_pools": [{}, {"type": "gemini", "model": "wrong", "api_url": "https://proxy.example", "gemini_auth_mode": "bearer", "streaming_mode": "off"}]}), encoding="utf-8")
    try:
        _load_fixed_gemini_route(str(path))
    except ValueError:
        pass
    else:
        raise AssertionError("wrong route must be rejected")


def test_run_agent_adapter_uses_real_runner_with_fake_caller(monkeypatch, tmp_path: Path) -> None:
    """The adapter's pipeline is real; only its wire caller is fake here."""
    quality = importlib.import_module("scripts.quality_eval.runner")
    impl = importlib.import_module("plugin.personification.skills.skillpacks.tool_caller.scripts.impl")

    class FakeCaller:
        async def chat_with_tools(self, *_args, **_kwargs):  # noqa: ANN002, ANN003
            messages = _args[0] if _args else _kwargs.get("messages", [])
            text = "隔离的假回复"
            if "待审核" in str(messages) or "候选回复" in str(messages):
                text = '{"action":"accept","text":"","reason":"符合场景","flags":[],"persona_verdict":"consistent"}'
            return SimpleNamespace(content=text, tool_calls=[], finish_reason="stop", usage={"total_tokens": 3})

        def build_tool_result_message(self, *_args):  # noqa: ANN002
            return {}

    monkeypatch.setattr(impl, "GeminiToolCaller", lambda *_args, **_kwargs: FakeCaller())
    env = tmp_path / "env.json"
    env.write_text(json.dumps({"personification_api_pools": [{}, {"type": "gemini", "model": "gemini-3.8-flash-high", "api_url": "https://proxy.example/v1", "gemini_auth_mode": "bearer", "streaming_mode": "off"}]}), encoding="utf-8")
    result = asyncio.run(quality.run_agent_case({"surface": "private", "events": [{"kind": "message", "sender": "U", "text": "hi"}], "trusted_persona": "自然"}, {"config_path": str(env), "budget_db": str(tmp_path / "budget.sqlite"), "isolated_db_path": str(tmp_path / "isolated"), "test_double": True}))
    assert result.execution_mode == "simulated"
    assert result.status == "completed"
    assert result.reply == "隔离的假回复"
    # The real runner performs intent classification plus final generation;
    # both fake wire calls are counted rather than only the outer evaluation.
    assert result.usage["wire_calls"] >= 3
    assert len(result.usage["responses"]) == result.usage["wire_calls"]
    assert result.turns[0]["review_action"] == "accept"
