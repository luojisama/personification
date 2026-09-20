from __future__ import annotations

from types import SimpleNamespace

import pytest

from ._loader import load_personification_module


segments_module = load_personification_module("plugin.personification.agent.runtime.context_segments")
prompting = load_personification_module("plugin.personification.agent.runtime.prompting")


def _prompt_kwargs() -> dict:
    return {
        "runtime_chat_intent": "lookup",
        "plugin_query_intent": "",
        "intent_decision": SimpleNamespace(ambiguity_level="high"),
        "rewritten_query": SimpleNamespace(
            primary_query="当前查询",
            query_candidates=["候选"],
            context_clues=["本轮线索"],
            search_plan=["先查官方来源"],
        ),
        "turn_plan": SimpleNamespace(
            speech_act="source_summary",
            output_mode="source_summary",
            reply_shape="auto",
            session_goal="回答当前问题",
            domain_focus="technology",
            evidence_policy="strict",
            emotional_support=None,
            meme_turn_context=None,
        ),
        "user_images": ["image-ref"],
        "direct_image_input": False,
        "is_group": True,
        "reply_required": True,
        "turn_media_context": [],
        "bot_avatar_context": SimpleNamespace(
            has_insight=True,
            neutral_summary="本轮头像摘要",
            acg_candidates=[],
        ),
    }


def test_prompt_segmentation_preserves_exact_wire_messages() -> None:
    baseline: list[dict] = [{"role": "user", "content": "当前用户消息"}]
    messages = [dict(item) for item in baseline]
    sink: list = []

    report = prompting.append_agent_system_prompts(
        messages=messages,
        segment_sink=sink,
        **_prompt_kwargs(),
    )

    assert messages[:1] == baseline
    assert messages[1:] == [
        {"role": segment.role, "content": segment.content} for segment in sink
    ]
    assert report.segment_count == len(sink)
    assert report.preceding_messages == "unknown"


def test_turn_specific_prompt_sources_are_dynamic() -> None:
    messages: list[dict] = []
    sink: list = []
    report = prompting.append_agent_system_prompts(
        messages=messages,
        segment_sink=sink,
        **_prompt_kwargs(),
    )
    by_source = {segment.source: segment.stability for segment in sink}

    for source in (
        "command_runtime",
        "reply_required",
        "reply_length",
        "speech_act",
        "domain_evidence",
        "ambiguity",
        "rewritten_query",
        "user_images",
        "bot_avatar",
    ):
        assert by_source[source] == "dynamic"
    assert by_source["prompt_injection_guard"] == "stable"
    assert report.preceding_messages == "none"
    # This describes the current append order; it does not claim provider cache behavior.
    assert report.stable_prefix_available is True
    assert report.stable_prefix_contiguous is False


def test_contiguous_prefix_guard_rejects_stable_after_dynamic() -> None:
    PromptSegment = segments_module.PromptSegment
    invalid = [
        PromptSegment("system", "stable-a", "stable", "a"),
        PromptSegment("system", "turn-data", "dynamic", "turn"),
        PromptSegment("system", "stable-b", "stable", "b"),
    ]

    with pytest.raises(ValueError, match="contiguous prefix"):
        segments_module.require_contiguous_stable_prefix(invalid)


def test_surface_early_return_still_reports_segments() -> None:
    messages: list[dict] = []
    sink: list = []
    kwargs = _prompt_kwargs()
    kwargs["surface"] = "qzone_post"
    report = prompting.append_agent_system_prompts(
        messages=messages,
        segment_sink=sink,
        **kwargs,
    )

    assert report.segment_count == len(sink)
    assert any(segment.source == "surface" for segment in sink)
    assert all(message == {"role": segment.role, "content": segment.content}
               for message, segment in zip(messages, sink, strict=True))
