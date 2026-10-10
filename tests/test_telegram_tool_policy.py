from __future__ import annotations

import asyncio
from types import SimpleNamespace

from ._loader import load_personification_module

policy = load_personification_module("plugin.personification.core.telegram_tool_policy")
tools = load_personification_module("plugin.personification.agent.tool_registry")
normal = load_personification_module("plugin.personification.handlers.reply_pipeline.pipeline_context")


def registry():
    result = tools.ToolRegistry()
    async def handler(**_kwargs): return "fixture"
    for name in ("web_search", "memory_search", "send_image", "send_qq_face", "list_peer_bots", "invoke_peer_bot", "invoke_plugin", "list_groupmate_qzone_feeds", "get_group_list"):
        result.register(tools.AgentTool(name, name, {}, handler))
    return result


def names(reg):
    return {s["function"]["name"] for s in reg.openai_schemas()}


def test_filtered_clone_keeps_platform_neutral_tools_and_qq_base():
    base = registry()
    clone = normal.clone_tool_registry(base)
    policy.filter_telegram_tools(clone, SimpleNamespace(platform="telegram"))
    assert names(clone) == {"web_search", "memory_search", "send_image"}
    assert "send_qq_face" in names(base)
    assert policy.filter_telegram_tools(base, SimpleNamespace(platform="onebot")) is base


def test_yaml_actual_agent_schemas_exclude_qq_tools(monkeypatch):
    from . import test_yaml_dialogue_provenance_replay as replay
    captured = []
    async def agent(**kwargs):
        captured.append(names(kwargs["registry"]))
        return replay.agent_synthesis_module.AgentResult(text="回复", pending_actions=[])
    def forbidden(*_a, **_k):
        raise AssertionError("Telegram must not invoke QQ registrar")
    monkeypatch.setattr(replay.yaml_processor, "run_agent", agent)
    for name in ("register_peer_bot_tools", "register_send_qq_expression_tools", "register_qq_recall_tool", "register_current_user_avatar_tool", "register_moderation_for_turn"):
        monkeypatch.setattr(replay.yaml_processor, name, forbidden)
    monkeypatch.setattr(replay.yaml_processor, "build_send_image_tools", lambda *_a: [])
    event = replay._event(text="hello", message_id="tg")
    event.platform = "telegram"
    async def review(*_a, **_k): return '{"action":"accept","persona_verdict":"consistent","flags":[]}'
    def configure(config):
        config.personification_agent_enabled = True
        config.personification_context_only_chat_enabled = False
        config.personification_sticker_path = "__fixture_missing_stickers__"
    replay._run_yaml_turn(monkeypatch, history=[], event=event, candidate="unused", review_call=review, final_gate_enabled=False, agent_tool_caller=object(), tool_registry=registry(), configure=configure)
    assert captured == [{"web_search", "memory_search", "send_image"}]


def test_normal_actual_agent_schemas_exclude_qq_tools(monkeypatch, tmp_path):
    from scripts.quality_eval import pipeline_adapter
    from .test_quality_eval_pipeline import _LoopbackCaller, _case
    from pathlib import Path
    db = load_personification_module("plugin.personification.core.db")
    original_event = pipeline_adapter._event_from
    original_clone = normal.clone_tool_registry
    def event_from(*args):
        event = original_event(*args)
        event.platform = "telegram"
        return event
    def clone(_base):
        return original_clone(registry())
    captured = []
    original_agent = normal.run_agent
    async def agent(**kwargs):
        captured.append(names(kwargs["registry"]))
        return await original_agent(**kwargs)
    monkeypatch.setattr(pipeline_adapter, "_event_from", event_from)
    monkeypatch.setattr(normal, "clone_tool_registry", clone)
    monkeypatch.setattr(normal, "run_agent", agent)
    db.init_db_sync(tmp_path)
    try:
        asyncio.run(pipeline_adapter.run_full_path_case(_case("private"), caller=_LoopbackCaller(), isolated_dir=str(tmp_path), behavior_config={"personification_context_only_chat_enabled": False}))
    finally:
        asyncio.run(db.close_db())
    assert captured == [{"web_search", "memory_search", "send_image"}]
