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
