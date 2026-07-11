from __future__ import annotations

from types import SimpleNamespace

from ._loader import load_personification_module


prompting = load_personification_module("plugin.personification.agent.runtime.prompting")


def test_agent_prompting_includes_directed_exchange_behavior() -> None:
    messages: list[dict] = []

    prompting.append_agent_system_prompts(
        messages=messages,
        runtime_chat_intent="banter",
        plugin_query_intent="capability",
        intent_decision=SimpleNamespace(ambiguity_level="low"),
        rewritten_query=SimpleNamespace(
            primary_query="",
            query_candidates=[],
            context_clues=[],
            search_plan=[],
        ),
        turn_plan=SimpleNamespace(
            speech_act="tease",
            output_mode="chat_short",
            session_goal="接住对方的调侃",
        ),
        user_images=[],
        direct_image_input=False,
        is_group=True,
        is_direct_mention=True,
    )

    combined = "\n".join(str(item.get("content", "")) for item in messages)
    assert "直呼/@ 后的回应方式" in combined
    assert "否认、反击、自辩" in combined
    assert "2-4 条短消息" in combined
    assert "轻松调侃时允许一句不索要信息的反击式反问" in combined


def test_qzone_surface_skips_group_chat_reply_discipline() -> None:
    messages: list[dict] = []

    prompting.append_agent_system_prompts(
        messages=messages,
        runtime_chat_intent="general",
        plugin_query_intent="",
        intent_decision=SimpleNamespace(ambiguity_level="low"),
        rewritten_query=SimpleNamespace(
            primary_query="",
            query_candidates=[],
            context_clues=[],
            search_plan=[],
        ),
        turn_plan=None,
        user_images=[],
        direct_image_input=False,
        is_group=False,
        surface="qzone_post",
    )

    combined = "\n".join(str(item.get("content", "")) for item in messages)
    assert "非聊天生成面：qzone_post" in combined
    assert "最终对用户的回复必须自然、像群聊里的活人接话" not in combined
    assert "群聊里通常多个话题并行" not in combined


def test_agent_prompting_adds_strict_technical_evidence_policy() -> None:
    messages: list[dict] = []
    prompting.append_agent_system_prompts(
        messages=messages,
        runtime_chat_intent="lookup",
        plugin_query_intent="",
        intent_decision=SimpleNamespace(ambiguity_level="low"),
        rewritten_query=SimpleNamespace(primary_query="", query_candidates=[], context_clues=[], search_plan=[]),
        turn_plan=SimpleNamespace(
            speech_act="source_summary", output_mode="source_summary", session_goal="核验技术结论",
            domain_focus="technology", evidence_policy="strict", emotional_support=None,
        ),
        user_images=[], direct_image_input=False,
    )
    combined = "\n".join(str(item.get("content", "")) for item in messages)
    assert "关键 claim" in combined
    assert "freshness" in combined
    assert "两个相互独立的来源" in combined
    assert "当前人设的自然口吻" in combined


def test_agent_prompting_allows_game_tools_and_natural_meme_use() -> None:
    messages: list[dict] = []
    prompting.append_agent_system_prompts(
        messages=messages,
        runtime_chat_intent="lookup",
        plugin_query_intent="",
        intent_decision=SimpleNamespace(ambiguity_level="low"),
        rewritten_query=SimpleNamespace(primary_query="", query_candidates=[], context_clues=[], search_plan=[]),
        turn_plan=SimpleNamespace(
            speech_act="participate", output_mode="chat_short", session_goal="参与游戏讨论",
            domain_focus="game_anime", evidence_policy="light", emotional_support=None,
        ),
        user_images=[], direct_image_input=False,
    )
    combined = "\n".join(str(item.get("content", "")) for item in messages)
    assert "game_info" in combined
    assert "wiki_lookup" in combined
    assert "web_search" in combined
    assert "自然用一个梗" in combined
