from __future__ import annotations

import asyncio
from types import SimpleNamespace

from ._loader import load_personification_module

response_review = load_personification_module("plugin.personification.core.response_review")


def test_looks_like_recent_duplicate_detects_same_and_similar_text() -> None:
    assert response_review._looks_like_recent_duplicate("太真实了", ["太真实了", "别的"])
    assert response_review._looks_like_recent_duplicate("这也太典了吧", ["这也太典了"])
    assert not response_review._looks_like_recent_duplicate("完全不同", ["这也太典了"])


def test_arbitrate_reply_mode_handles_key_combinations() -> None:
    no_reply = response_review.arbitrate_reply_mode(
        intent_decision=SimpleNamespace(ambiguity_level="high", recommend_silence=True, confidence=0.9),
        is_private=False,
        is_direct_mention=False,
        is_random_chat=True,
        message_target="others",
        solo_speaker_follow=False,
    )
    clarify = response_review.arbitrate_reply_mode(
        intent_decision=SimpleNamespace(ambiguity_level="high", recommend_silence=True, confidence=0.5),
        is_private=True,
        is_direct_mention=False,
        is_random_chat=False,
        message_target="bot",
        solo_speaker_follow=False,
    )
    reply = response_review.arbitrate_reply_mode(
        intent_decision=SimpleNamespace(ambiguity_level="low", recommend_silence=False, confidence=0.2),
        is_private=False,
        is_direct_mention=False,
        is_random_chat=False,
        message_target="others",
        solo_speaker_follow=False,
    )

    assert no_reply == "no_reply"
    assert clarify == "clarify"
    assert reply == "no_reply"


def test_recent_bot_replies_only_include_personification_provenance() -> None:
    replies = response_review.extract_recent_bot_reply_texts(
        [
            {
                "content": "辅助性T细胞",
                "user_id": "bot-1",
                "is_bot": True,
                "source_kind": "plugin",
            },
            {
                "content": "这是人格回复",
                "user_id": "bot-1",
                "is_bot": True,
                "source_kind": "bot_reply",
            },
        ]
    )

    assert replies == ["这是人格回复"]


def test_arbitrate_reply_mode_silences_uncertain_random_chat_even_with_low_confidence() -> None:
    decision = response_review.arbitrate_reply_mode(
        intent_decision=SimpleNamespace(ambiguity_level="high", recommend_silence=True, confidence=0.18),
        is_private=False,
        is_direct_mention=False,
        is_random_chat=True,
        message_target="uncertain",
        solo_speaker_follow=False,
    )

    assert decision == "no_reply"


def test_arbitrate_reply_mode_keeps_direct_random_chat_replyable() -> None:
    decision = response_review.arbitrate_reply_mode(
        intent_decision=SimpleNamespace(ambiguity_level="high", recommend_silence=True, confidence=0.18),
        is_private=False,
        is_direct_mention=True,
        is_random_chat=True,
        message_target="bot",
        solo_speaker_follow=False,
    )

    assert decision == "clarify"


def test_is_agent_reply_ooc_detects_search_style_phrasing_and_urls() -> None:
    assert response_review.is_agent_reply_ooc("根据搜索结果，先给你两条相关链接：https://example.com/very/long/path")
    assert response_review.is_agent_reply_ooc("我查了一下，这个设定后来改过")
    assert response_review.is_agent_reply_ooc("我需要确认一下广州的天气。")
    assert response_review.is_agent_reply_ooc("**广州** 接下来雨不少")
    assert response_review.is_agent_reply_ooc("Step 1: 检查\nStep 2: 输出\n轻松")
    assert response_review.is_agent_reply_ooc("等下，这什么表情")
    assert response_review.is_agent_reply_ooc("被雷炸了，这也太刺激了吧")
    assert response_review.is_agent_reply_ooc("我先看看情况，等会再说")
    assert response_review.is_agent_reply_ooc("先围观一下，回头再聊")
    assert not response_review.is_agent_reply_ooc("这事儿大概就是后来改设定了")


def test_output_mode_hint_uses_turn_plan_lengths() -> None:
    hint = response_review._output_mode_hint(SimpleNamespace(output_mode="structured_help"))

    assert "structured_help" in hint
    assert "80-300" in hint


def test_review_blocks_no_reply_for_direct_mention() -> None:
    async def _fake_call(messages):  # noqa: ANN001
        assert "禁止输出 no_reply" in messages[0]["content"]
        return '{"action":"no_reply","text":"","reason":"bad"}'

    decision = asyncio.run(
        response_review.review_response_text(
            _fake_call,
            candidate_text="我在",
            raw_message_text="@bot 在吗",
            is_direct_mention=True,
        )
    )

    assert decision.action == "accept"
    assert decision.text == "我在"


def test_review_blocks_no_reply_for_required_private_turn() -> None:
    async def _fake_call(messages):  # noqa: ANN001
        assert "强交互消息，禁止输出 no_reply" in messages[0]["content"]
        return '{"action":"no_reply","text":"","reason":"bad"}'

    decision = asyncio.run(
        response_review.review_response_text(
            _fake_call,
            candidate_text="图里的文字是测试",
            raw_message_text="你翻译一下",
            is_private=True,
            reply_required=True,
        )
    )

    assert decision.action == "accept"
    assert decision.text == "图里的文字是测试"


def test_required_reply_recovery_preserves_successful_actions() -> None:
    assert response_review.required_reply_needs_recovery(
        "[NO_REPLY]",
        reply_required=True,
    )
    assert not response_review.required_reply_needs_recovery(
        "[SILENCE]",
        reply_required=True,
        pending_actions=[{"kind": "send_image"}],
    )
    assert not response_review.required_reply_needs_recovery(
        "[NO_REPLY]",
        reply_required=False,
    )
    assert response_review.required_reply_needs_recovery(
        "<output><message>[SILENCE]</message></output>",
        reply_required=True,
    )


def test_review_prompt_rejects_empty_affirmation_and_status_announcement() -> None:
    async def _fake_call(messages):  # noqa: ANN001
        content = messages[0]["content"]
        assert "附和感叹/转述聊天" in content
        assert "等会再说" in content
        return '{"action":"rewrite","text":"那就先卡这个点聊，别绕远。","reason":"empty_affirmation"}'

    decision = asyncio.run(
        response_review.review_response_text(
            _fake_call,
            candidate_text="确实，这也太真实了吧",
            raw_message_text="这题写不动了",
        )
    )

    assert decision.action == "rewrite"
    assert decision.text == "那就先卡这个点聊，别绕远。"


def test_care_review_fail_closed_on_unparseable_result() -> None:
    async def _fake_call(_messages):  # noqa: ANN001
        return "not json"

    frame = SimpleNamespace(
        requires_emotional_care=True,
        emotional_support=SimpleNamespace(needed=True, advice_permission="ask_first", risk_level="concern"),
    )
    decision = asyncio.run(
        response_review.review_response_text(
            _fake_call,
            candidate_text="你这就是抑郁，保证明天就好了",
            raw_message_text="我最近很难受",
            semantic_frame=frame,
            is_direct_mention=False,
        )
    )
    assert decision.action == "no_reply"
    assert decision.reason == "care_review_unparseable"


def test_care_review_flags_fail_closed_to_safe_direct_reply() -> None:
    async def _fake_call(messages):  # noqa: ANN001
        assert "medicalizing" in messages[0]["content"]
        return '{"action":"accept","text":"","reason":"unsafe","flags":["medicalizing","overpromise"]}'

    frame = SimpleNamespace(requires_emotional_care=True, emotional_support=SimpleNamespace(needed=True))
    decision = asyncio.run(
        response_review.review_response_text(
            _fake_call,
            candidate_text="你肯定没事，我会永远陪着你",
            raw_message_text="我撑不住了",
            semantic_frame=frame,
            is_direct_mention=True,
        )
    )
    assert decision.action == "rewrite"
    assert decision.text == "先不用急着把话说完整，我听着。"
    assert decision.flags == ("medicalizing", "overpromise")


def test_care_rewrite_is_safety_revalidated() -> None:
    calls = 0

    async def _fake_call(_messages):  # noqa: ANN001
        nonlocal calls
        calls += 1
        if calls == 1:
            return '{"action":"rewrite","text":"我会永远只陪着你","reason":"改写","flags":[]}'
        return '{"action":"no_reply","text":"","reason":"dependency","flags":["dependency_encouragement"]}'

    frame = SimpleNamespace(
        requires_emotional_care=True,
        emotional_support=SimpleNamespace(needed=True, risk_level="high"),
    )
    decision = asyncio.run(
        response_review.review_response_text(
            _fake_call,
            candidate_text="没事",
            raw_message_text="测试",
            semantic_frame=frame,
            is_private=True,
        )
    )

    assert calls == 2
    assert decision.action == "rewrite"
    assert "当地急救或警方" in decision.text
    assert decision.reason == "care_rewrite_unverified"


def test_review_consumes_safe_visual_evidence_for_social_attribution() -> None:
    async def _fake_call(messages):  # noqa: ANN001
        system_prompt = messages[0]["content"]
        user_prompt = messages[1]["content"]
        assert "现实社交归因是否有" in system_prompt
        assert "unsupported_visual_social_attribution" in system_prompt
        assert "画中主体只是媒体内容，不是聊天参与者" in user_prompt
        assert "owner_user_id=user_a" in user_prompt
        assert "动漫插画中有多人看向画面中央" in user_prompt
        return (
            '{"action":"rewrite","text":"这只是图里的构图，先别往现实群友身上套。",'
            '"reason":"视觉证据不支持现实归因","flags":["unsupported_visual_social_attribution"]}'
        )

    media_context = [
        {
            "media_id": "media-anime",
            "ref": "",
            "origin": "current",
            "owner_user_id": "user_a",
            "message_id": "message_a",
            "kind": "image",
            "content_hash": "abc123",
            "file_id": "file-a",
            "safe_summary": "动漫插画中有多人看向画面中央",
            "confidence": 0.65,
            "summary_scope": "single_media",
        }
    ]
    decision = asyncio.run(
        response_review.review_response_text(
            _fake_call,
            candidate_text="群友都在围观你，压力拉满了",
            raw_message_text="[图片]",
            turn_media_context=media_context,
        )
    )

    assert decision.action == "rewrite"
    assert decision.flags == ("unsupported_visual_social_attribution",)
