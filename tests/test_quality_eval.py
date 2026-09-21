from __future__ import annotations

import asyncio
import importlib
import json
from pathlib import Path
from types import SimpleNamespace

from scripts.quality_eval.runner import (
    BEHAVIOR_KEYS,
    BudgetExhausted,
    BudgetedCaller,
    CallBudget,
    _load_fixed_gemini_route,
    describe_behavior_snapshot,
    invoke_case,
    load_behavior_snapshot,
)


CORPUS = Path(__file__).parent / "replay_corpus" / "quality_v1" / "cases.jsonl"


def test_quality_corpus_has_balanced_versioned_splits() -> None:
    cases = [json.loads(line) for line in CORPUS.read_text(encoding="utf-8").splitlines() if line]
    assert len(cases) == 80
    assert {case["surface"] for case in cases} == {"group", "private"}
    assert sum(case["surface"] == "group" for case in cases) == 40
    assert sum(case["split"] == "dev" for case in cases) == 60
    assert sum(case["split"] == "holdout" for case in cases) == 20
    assert sum(case["split"] == "holdout" and case["surface"] == "group" for case in cases) == 10
    assert sum(case["split"] == "holdout" and case["surface"] == "private" for case in cases) == 10
    assert {case["pipeline"] for case in cases} == {"normal", "yaml"}
    assert {case["schema_version"] for case in cases} == {"quality_v1"}
    categories = {case["category"] for case in cases}
    assert {"grounded_dialogue", "persona_relationship", "reply_discretion", "multiturn_continuity", "memory_scope", "provenance", "media_grounding", "tool_boundary", "delivery_contract"} <= categories
    for case in cases:
        assert case["events"] and case["trusted_persona"] and "expected" in case and "forbidden" in case
        assert isinstance(case["coverage_requires"], list)
        event_kinds = {event["kind"] for event in case["events"]}
        required = set(case["coverage_requires"])
        if "tool_result" in event_kinds:
            assert "tool_fixture" in required
        if "image" in event_kinds:
            assert "image_fixture" in required
        if "video" in event_kinds:
            assert "video_fixture" in required
        if "audio" in event_kinds:
            assert "audio_fixture" in required
        if "send_receipt" in event_kinds:
            assert "delivery_fixture" in required


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


def test_behavior_snapshot_uses_env_json_authority_defaults_and_never_exports_secret(tmp_path: Path) -> None:
    config_path = tmp_path / "env.json"
    config_path.write_text(json.dumps({
        "personification_response_timeout": "301",
        "personification_agent_max_steps": 8,
        "personification_api_key": "must-not-be-copied",
        "personification_api_pools": [{"api_key": "nested-secret"}],
        "personification_skill_cache_dir": "D:/not-a-behavior-path",
        "personification_qzone_enabled": True,
    }), encoding="utf-8")

    snapshot = describe_behavior_snapshot(str(config_path))
    assert snapshot["effective_fields"]["personification_response_timeout"] == 301
    assert snapshot["effective_fields"]["personification_agent_max_steps"] == 8
    assert snapshot["effective_fields"]["personification_turn_planner_enabled"] is False
    assert snapshot["sources"]["personification_response_timeout"] == "env.json"
    assert snapshot["sources"]["personification_turn_planner_enabled"] == "defaults"
    assert set(snapshot["effective_fields"]) == set(BEHAVIOR_KEYS)
    rendered = json.dumps(snapshot, ensure_ascii=False)
    assert "must-not-be-copied" not in rendered
    assert "nested-secret" not in rendered
    assert "not-a-behavior-path" not in rendered
    assert "personification_qzone_enabled" not in rendered
    assert snapshot["credentials_exported"] is False
    assert snapshot["paths_exported"] is False


def test_behavior_snapshot_description_uses_one_atomic_read(monkeypatch, tmp_path: Path) -> None:
    import scripts.quality_eval.runner as quality

    config_path = tmp_path / "env.json"
    config_path.write_text(json.dumps({"personification_response_timeout": 301}), encoding="utf-8")
    original = Path.read_text
    reads = 0

    def counting_read(path, *args, **kwargs):  # noqa: ANN001
        nonlocal reads
        if path == config_path:
            reads += 1
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", counting_read)
    snapshot = quality.describe_behavior_snapshot(str(config_path))
    assert reads == 1
    assert snapshot["effective_fields"]["personification_response_timeout"] == 301
    assert snapshot["sources"]["personification_response_timeout"] == "env.json"




def test_explicit_redacted_profile_is_the_only_opt_in_legacy_behavior_input(tmp_path: Path) -> None:
    config_path = tmp_path / "env.json"
    config_path.write_text(json.dumps({"personification_agent_max_steps": 3}), encoding="utf-8")
    profile_path = tmp_path / "behavior-profile.json"
    profile_path.write_text(json.dumps({
        "behavior": {"personification_agent_max_steps": "9", "personification_response_timeout": 250},
        "behavior_sources": {"personification_agent_max_steps": ".env.prod"},
        "api_key": "must-not-be-read",
    }), encoding="utf-8")

    assert load_behavior_snapshot(str(config_path))["personification_agent_max_steps"] == 3
    profile = describe_behavior_snapshot(str(config_path), str(profile_path))
    assert profile["effective_fields"]["personification_agent_max_steps"] == 9
    assert profile["sources"]["personification_agent_max_steps"] == "explicit_redacted_snapshot"
    assert ".env.prod" not in json.dumps(profile)
    assert "must-not-be-read" not in json.dumps(profile)


def test_invoke_uses_the_manifest_frozen_behavior_config(monkeypatch, tmp_path: Path) -> None:
    import scripts.quality_eval.runner as quality

    seen: dict[str, object] = {}

    async def fake_run_agent_case(_case, config):  # noqa: ANN001
        seen["behavior"] = config["behavior_config"]
        return quality.EvalResult(status="completed", execution_mode="real")

    monkeypatch.setattr(quality, "run_agent_case", fake_run_agent_case)
    frozen = {key: None for key in BEHAVIOR_KEYS}
    result = asyncio.run(invoke_case({"id": "frozen"}, {
        "execution_mode": "real", "config_path": str(tmp_path / "route.json"),
        "behavior_source": "server", "behavior_config": frozen,
    }))
    assert result.status == "completed"
    assert seen["behavior"] == frozen


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
