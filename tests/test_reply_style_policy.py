from __future__ import annotations

from ._loader import load_personification_module

reply_style_policy = load_personification_module("plugin.personification.core.reply_style_policy")


def test_reply_style_policy_keeps_visual_context_internal() -> None:
    prompt = reply_style_policy.build_reply_style_policy_prompt(
        has_visual_context=True,
        photo_like=False,
    )

    assert "内部上下文" in prompt
    assert "媒体占位纪律" in prompt
    assert "不要假装知道画面内容" in prompt
    assert "最近上下文已经说明原因" in prompt
    assert "不要主动讲图里是什么" in prompt
    assert "不要堆砌互联网热词" in prompt
    assert "等下/等一下/你这也/这图也" in prompt
    assert "。。。/……/..." in prompt
    assert "仅供理解" in prompt
    assert "不要把判断过程说给用户" in prompt
    assert "不要讲解、复述、总结或分析画面内容" in prompt


def test_context_continuity_policy_covers_media_only_and_direct_cues() -> None:
    prompt = reply_style_policy.build_context_continuity_policy_prompt()

    assert "低信息跟帖或媒体占位" in prompt
    assert "优先保持沉默" in prompt
    assert "相邻图片、表情或截图不能覆盖直接 cue 的文字问题" in prompt
    assert "优先回应这个问题本身" in prompt


def test_direct_visual_guard_does_not_ask_to_describe_image() -> None:
    prompt = reply_style_policy.build_direct_visual_identity_guard()

    assert "不要主动讲解、复述或分析画面" in prompt
    assert "不要把分类或判断过程说出来" in prompt
    assert "描述或评论图片内容" not in prompt


def test_media_understanding_policy_allows_internal_distinction_only() -> None:
    prompt = reply_style_policy.build_media_understanding_output_policy_prompt()

    assert "内部语境证据" in prompt
    assert "表情包、梗图、截图还是真实照片" in prompt
    assert "不要把判断过程说给用户" in prompt
    assert "没有证据时宁可短句承认不确定或保持沉默" in prompt
