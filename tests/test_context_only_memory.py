from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from ._loader import load_personification_module

policy = load_personification_module("plugin.personification.core.turn_execution_policy")
memory = load_personification_module("plugin.personification.core.memory_context")
temporal = load_personification_module("plugin.personification.core.temporal_memory")


def frame(**overrides):
    planner = load_personification_module("plugin.personification.agent.runtime.planner")
    return SimpleNamespace(llm_source="primary", chat_intent="banter", ambiguity_level="low", turn_plan=planner.TurnPlan(), **overrides)


@pytest.mark.parametrize("overrides", [
    {"fallback_reason": "semantic_frame_timeout"}, {"research_need": "low"},
    {"tool_intent": ["memory"]}, {"memory_queries": ["去年旅行"]},
    {"memory_need": "deep"}, {"vision_need": "summary"},
    {"speech_act": "execute_action"}, {"future_commitment_candidate": True},
    {"evidence_policy": "prefer_sources"},
])
def test_required_capabilities_preserve_agent(overrides):
    assert not policy.derive_turn_execution_policy(semantic_frame=frame(**overrides)).is_context_only


def test_only_runtime_confirmed_semantics_select_context():
    assert policy.derive_turn_execution_policy(semantic_frame=frame(memory_need="light")).is_context_only
    assert not policy.derive_turn_execution_policy(semantic_frame=frame(), enabled=False).is_context_only
    assert not policy.derive_turn_execution_policy(semantic_frame=frame(), has_media=True).is_context_only
    assert not policy.derive_turn_execution_policy(semantic_frame=SimpleNamespace(chat_intent="banter")).is_context_only


def test_context_only_retains_profile_history_states_without_remote_calls(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("context-only attempted remote recall or state extraction")
    monkeypatch.setattr(temporal, "load_current_states", lambda scope: [{"state_id": "known", "statement": "已有计划"}])
    monkeypatch.setattr(temporal, "update_current_states", forbidden)
    profiles = SimpleNamespace(get_scoped_document_v3=lambda **scope: {"document": {"likes": "猫"}},
                               get_shared_claims=lambda **scope: [{"claim": "轻松说话"}])
    runtime = SimpleNamespace(plugin_config=SimpleNamespace(), scoped_profile_service=profiles,
                              memory_store=SimpleNamespace(arecall_memories=forbidden),
                              lite_tool_caller=SimpleNamespace(chat_with_tools=forbidden))
    result = asyncio.run(memory.prepare_memory_context(
        runtime=runtime, event=SimpleNamespace(user_id="alice"), bot=SimpleNamespace(self_id="bot"),
        messages=[{"role": "user", "content": "还在吗", "id": 1}],
        execution_policy=policy.derive_turn_execution_policy(semantic_frame=frame())))
    assert result.status == "local_context_only"
    assert len(result.profiles) == 2 and result.states[0]["state_id"] == "known"
    assert "还在吗" in result.history[0]["content"]
    assert result.diagnostics["state_refresh"] == "skipped"
    assert result.diagnostics["injected_count"] == 0


def test_candidate_recall_skipped_before_store_and_config_access():
    pipeline = load_personification_module("plugin.personification.handlers.reply_pipeline.pipeline_context")
    result = asyncio.run(pipeline._recall_agent_candidate_memories(
        runtime=object(), event=object(), messages=[], execution_policy=policy.TurnExecutionPolicy("context_only", "test")))
    assert result == []


def test_context_budget_wrapped_error_remains_provider_diagnosis():
    processor = load_personification_module("plugin.personification.handlers.reply_pipeline.processor")
    routes = load_personification_module("plugin.personification.core.ai_routes")
    budget = load_personification_module("plugin.personification.core.context_budget")
    error = routes.RoutedToolCallerError([{"code": "provider_context_budget_exceeded", "retryable": False}])
    error.__cause__ = budget.ContextBudgetExceeded("isolated oversized prompt")
    assert processor._provider_diagnosis_code(error) == "provider_context_budget_exceeded"
    assert processor._reply_failure_outcome({}, processor._provider_diagnosis_code(error)) == (
        "not_started", "failed", "provider_context_budget_exceeded")


def test_conflicting_or_silent_semantics_preserve_agent():
    assert not policy.derive_turn_execution_policy(semantic_frame=frame(),
        intent_decision=SimpleNamespace(chat_intent="lookup", ambiguity_level="low")).is_context_only
    assert not policy.derive_turn_execution_policy(semantic_frame=frame(recommend_silence=True)).is_context_only
    assert not policy.derive_turn_execution_policy(semantic_frame=frame(), turn_plan=SimpleNamespace(ambiguity_level="low")).is_context_only
    assert not policy.derive_turn_execution_policy(semantic_frame=frame(),
        turn_plan=SimpleNamespace(reply_action="silence", ambiguity_level="low")).is_context_only
    assert not policy.derive_turn_execution_policy(semantic_frame=SimpleNamespace(
        llm_source="primary", chat_intent="banter", ambiguity_level="low")).is_context_only


def test_yaml_public_entry_propagates_policy_deadline_and_preserves_capture(monkeypatch):
    import time
    from .test_yaml_dialogue_provenance_replay import (
        _event, _run_yaml_turn, yaml_processor, planner, tool_registry_module, agent_synthesis_module,
    )
    captured = {}
    captures = []
    async def fake_agent(**kwargs):
        captured["agent"] = kwargs
        captured["generation_started"] = time.monotonic()
        await asyncio.sleep(0.03)
        return agent_synthesis_module.AgentResult(text="在呀", pending_actions=[])
    original_gate = yaml_processor.final_dialogue_gate
    async def gate(*args, **kwargs):
        captured["review_deadline"] = kwargs["response_deadline"]
        captured["review_remaining"] = kwargs["response_deadline"] - time.monotonic()
        return await original_gate(*args, **kwargs)
    monkeypatch.setattr(yaml_processor, "run_agent", fake_agent)
    monkeypatch.setattr(yaml_processor, "final_dialogue_gate", gate)
    async def review(*args, **kwargs):
        return '{"action":"accept","persona_verdict":"consistent","flags":[]}'
    plan = planner.TurnPlan()
    plan.llm_source = "primary"
    semantic = planner.turn_plan_to_semantic_frame(plan)
    local_policy = policy.derive_turn_execution_policy(semantic_frame=semantic)
    assert local_policy.is_context_only
    bot, _, reviews, _ = _run_yaml_turn(
        monkeypatch, history=[], event=_event(text="还在吗", message_id="context-only"),
        candidate="fallback", review_call=review, final_gate_enabled=True,
        agent_tool_caller=object(), tool_registry=tool_registry_module.ToolRegistry(),
        configure=lambda cfg: setattr(cfg, "personification_agent_enabled", True),
        semantic_frame=semantic, execution_policy=local_policy,
        prepared_memory_context=memory.PreparedMemoryContext(profiles=[{"document": {"likes": "猫"}}]),
        memory_curator=SimpleNamespace(schedule_turn_capture=lambda **kwargs: captures.append(kwargs)),
    )
    agent = captured["agent"]
    assert agent["execution_policy"] is local_policy and agent["candidate_memories"] == []
    assert 0 < captured["review_remaining"] < agent["time_budget_seconds"] <= 18
    assert "猫" in str(agent["messages"])
    assert bot.sent == ["在呀"] and reviews and len(captures) == 1
    assert captures[0]["user_utterance"] == "还在吗"


@pytest.mark.parametrize("pipeline", ["normal", "yaml"])
@pytest.mark.parametrize("surface", ["private", "group"])
def test_real_processors_derive_context_only_from_successful_llm(monkeypatch, tmp_path, pipeline, surface):
    import json
    from scripts.quality_eval.pipeline_adapter import run_full_path_case
    db = load_personification_module("plugin.personification.core.db")
    identity = load_personification_module("plugin.personification.core.llm_context")
    class Caller:
        def __init__(self):
            self.calls = []
        async def chat_with_tools(self, messages, tools, use_builtin_search):
            purpose = identity.current_llm_context().get("purpose", "")
            self.calls.append((purpose, len(tools), use_builtin_search))
            system = "\n".join(str(item.get("content", "")) for item in messages if item.get("role") == "system")
            assert purpose not in {"memory_query_plan", "memory_recall_gate", "memory_state_update"}
            if purpose == "reply_generation":
                content = "<output><message>在呀</message></output>" if pipeline == "yaml" else "在呀"
            elif "speech_act" in system and "reply_action" in system:
                content = json.dumps({"reply_action": "reply", "speech_act": "participate", "memory_need": "none",
                    "research_need": "none", "vision_need": "none", "output_mode": "chat_short", "tool_intent": ["none"],
                    "ambiguity_level": "low", "message_target": "bot", "confidence": 0.95, "reason": "日常接话"})
            elif "chat_intent" in system and "plugin_question_intent" in system:
                content = json.dumps({"chat_intent": "banter", "ambiguity_level": "low", "confidence": 0.95,
                    "reason": "日常接话", "evidence_policy": "none", "vision_need": "none"})
            else:
                content = '{"action":"accept","text":"","flags":[],"persona_verdict":"consistent"}'
            return SimpleNamespace(content=content, tool_calls=[], finish_reason="stop", raw={})
    def forbidden(*args, **kwargs):
        raise AssertionError("idle chat attempted recall or state-model extraction")
    prepared = []
    original_prepare = memory.prepare_memory_context
    processor = load_personification_module("plugin.personification.handlers.reply_pipeline.processor")
    pipeline_context = load_personification_module("plugin.personification.handlers.reply_pipeline.pipeline_context")
    yaml_processor = load_personification_module("plugin.personification.handlers.yaml_pipeline.processor")
    monkeypatch.setattr(processor, "_capture_user_protocol_profile", forbidden)
    monkeypatch.setattr(processor, "compress_context_if_needed", forbidden)
    monkeypatch.setattr(processor, "render_command_runtime_prompt", forbidden)
    monkeypatch.setattr(pipeline_context, "clone_tool_registry", forbidden)
    monkeypatch.setattr(yaml_processor, "_clone_tool_registry", forbidden)
    monkeypatch.setattr(yaml_processor, "render_command_runtime_prompt", forbidden)
    monkeypatch.setattr(pipeline_context, "get_cached_friend_ids", forbidden)
    async def capture_prepare(**kwargs):
        kwargs["runtime"].memory_store = SimpleNamespace(arecall_memories=forbidden)
        result = await original_prepare(**kwargs)
        prepared.append(result)
        return result
    monkeypatch.setattr(memory, "prepare_memory_context", capture_prepare)
    monkeypatch.setattr(temporal, "update_current_states", forbidden)
    caller = Caller()
    case = {"id": f"context-{surface}-{pipeline}", "surface": surface, "pipeline": pipeline,
        "trusted_persona": "自然接话的群友", "events": [{"kind": "mention" if surface == "group" else "message",
            "sender": "Alice", "text": "还在吗", "message_id": "context-message"}]}
    db.init_db_sync(tmp_path)
    try:
        result = asyncio.run(run_full_path_case(case, caller=caller, isolated_dir=str(tmp_path),
            behavior_config={"personification_agent_enabled": True, "personification_turn_planner_enabled": True,
                             "personification_final_dialogue_gate_enabled": True}))
    finally:
        asyncio.run(db.close_db())
    assert prepared and prepared[0].status == "local_context_only"
    assert any(purpose == "reply_generation" for purpose, _, _ in caller.calls), caller.calls
    assert all(count == 0 and search is False for _, count, search in caller.calls)
    assert any(purpose == "reply_review" for purpose, _, _ in caller.calls)
    assert result["status"] == "completed" and result["turns"][0]["reply_delivery_confirmed"]
    assert result["synthetic_receipts"] == ["confirmed"]
