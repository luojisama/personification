import pytest
from types import SimpleNamespace
from plugin.personification.core.bot_avatar_context import (
    BotAvatarInsightContext,
    get_bot_avatar_insight_context,
    render_bot_avatar_vision_prompt,
)
from plugin.personification.agent.runtime.prompting import append_agent_system_prompts


def test_render_bot_avatar_vision_prompt_empty_when_no_insight():
    assert render_bot_avatar_vision_prompt(None) == ""
    ctx = BotAvatarInsightContext(bot_self_id="12345", has_insight=False)
    assert render_bot_avatar_vision_prompt(ctx) == ""


def test_render_bot_avatar_vision_prompt_with_valid_insight():
    ctx = BotAvatarInsightContext(
        bot_self_id="12345",
        asset_kind="acg_character",
        neutral_summary="粉色长发扎红色发带的动漫少女，穿着粉色宽松运动服",
        acg_candidates=["绪山真寻", "别当欧尼酱了！"],
        confidence=0.92,
        has_insight=True,
    )
    rendered = render_bot_avatar_vision_prompt(ctx)
    assert "自身形象与二创自知" in rendered
    assert "粉色长发扎红色发带" in rendered
    assert "绪山真寻" in rendered
    assert "二创" in rendered or "同人" in rendered


def test_get_bot_avatar_insight_context_from_mock_profile():
    class DummyProfileService:
        def get_core_profile(self, uid: str):
            return SimpleNamespace(
                profile_json={
                    "qq_profile": {
                        "avatar_insight": {
                            "asset_kind": "acg_character",
                            "neutral_summary": "白发红瞳猫耳少女",
                            "acg_candidates": ["原创角色"],
                            "confidence": 0.85,
                            "subject_count": 1,
                            "contains_text": False,
                        }
                    }
                }
            )

    svc = DummyProfileService()
    ctx = get_bot_avatar_insight_context(svc, "99999")
    assert ctx.bot_self_id == "99999"
    assert ctx.has_insight is True
    assert "白发红瞳猫耳少女" in ctx.neutral_summary
    assert "原创角色" in ctx.acg_candidates


def test_append_agent_system_prompts_injects_bot_avatar_prompt():
    messages = []
    ctx = BotAvatarInsightContext(
        bot_self_id="12345",
        neutral_summary="粉白渐变长发少女",
        acg_candidates=["绪山真寻"],
        has_insight=True,
    )
    append_agent_system_prompts(
        messages=messages,
        runtime_chat_intent="banter",
        plugin_query_intent="none",
        intent_decision=SimpleNamespace(ambiguity_level="low"),
        rewritten_query=SimpleNamespace(primary_query="", query_candidates=[], context_clues=[], search_plan=[]),
        turn_plan=None,
        user_images=["http://example.com/fanart.jpg"],
        direct_image_input=True,
        bot_avatar_context=ctx,
    )
    system_contents = [m["content"] for m in messages if m.get("role") == "system"]
    matched = any("自身形象与二创自知" in c and "粉白渐变长发少女" in c for c in system_contents)
    assert matched, "Expected bot avatar vision prompt to be injected into system messages"
