from __future__ import annotations

import asyncio
from types import SimpleNamespace

from ._loader import load_personification_module


referent = load_personification_module("plugin.personification.core.group_followup_referent")
turn_media = load_personification_module("plugin.personification.core.turn_media")
response_review = load_personification_module("plugin.personification.core.response_review")
group_context = load_personification_module("plugin.personification.core.group_context")


def _event(user_id: str, message_id: str, text: str, message: list[object] | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        user_id=user_id,
        message_id=message_id,
        group_id="g-1",
        message=message or [SimpleNamespace(type="text", data={"text": text})],
        get_plaintext=lambda: text,
    )


def _image(ref: str, file_id: str) -> SimpleNamespace:
    return SimpleNamespace(type="image", data={"url": ref, "file": file_id})


def test_reply_to_bot_can_semantically_select_same_user_antecedent_media() -> None:
    resolver = referent.GroupFollowupReferentResolver()
    prior = _event("alice", "before", "")
    prior_media = turn_media.extract_turn_media_from_event(
        _event("alice", "before", "", [_image("https://img.example/a.png", "alice-file")])
    )
    resolver.remember(
        bot_self_id="self",
        group_id="g-1",
        event=prior,
        media=prior_media,
        now=100.0,
    )
    current = _event("alice", "now", "我发的这个表情包不可爱吗")
    current.reply = SimpleNamespace(message_id="bot-message", sender=SimpleNamespace(user_id="self"), message=[])

    async def _model(*_args, **_kwargs):  # noqa: ANN202
        return '{"referent":"antecedent","message_id":"before","confidence":0.91,"evidence_tags":["same_sender_context"]}'

    result = asyncio.run(
        resolver.resolve(
            bot_self_id="self", group_id="g-1", event=current,
            current_media=turn_media.extract_turn_media_from_event(current),
            addressing_target="bot", call_ai_api=_model, now=101.0,
        )
    )

    assert result.addressing_target == "bot"
    assert result.semantic_referent == "antecedent"
    assert result.selected_message_id == "before"
    assert [(item.owner_user_id, item.message_id, item.origin, item.reference_role) for item in result.active_media] == [
        ("alice", "before", "antecedent", "selected_referent"),
    ]


def test_quoted_media_is_address_only_until_model_selects_it() -> None:
    resolver = referent.GroupFollowupReferentResolver()
    current = _event("alice", "now", "你看这个")
    current.reply = SimpleNamespace(
        message_id="bot-message", sender=SimpleNamespace(user_id="self"),
        message=[_image("https://img.example/bot.png", "bot-file")],
    )
    result = asyncio.run(
        resolver.resolve(
            bot_self_id="self", group_id="g-1", event=current,
            current_media=turn_media.extract_turn_media_from_event(current),
            addressing_target="bot", call_ai_api=None, now=101.0,
        )
    )

    assert result.semantic_referent == "unclear"
    assert result.active_media == ()


def test_model_can_select_quoted_media_without_confusing_addressing_target() -> None:
    resolver = referent.GroupFollowupReferentResolver()
    current = _event("alice", "now", "这张图里的字是什么")
    current.reply = SimpleNamespace(
        message_id="quoted", sender=SimpleNamespace(user_id="self"),
        message=[SimpleNamespace(type="text", data={"text": "之前发的截图"}), _image("https://img.example/bot.png", "bot-file")],
    )

    async def _model(*_args, **_kwargs):  # noqa: ANN202
        return '{"referent":"quoted","message_id":"quoted","confidence":0.95,"evidence_tags":["quote_content"]}'

    result = asyncio.run(
        resolver.resolve(
            bot_self_id="self", group_id="g-1", event=current,
            current_media=turn_media.extract_turn_media_from_event(current),
            addressing_target="bot", call_ai_api=_model, now=101.0,
        )
    )
    assert result.addressing_target == "bot"
    assert result.semantic_referent == "quoted"
    assert [(item.message_id, item.reference_role) for item in result.active_media] == [("quoted", "selected_referent")]
    assert [(item.message_id, item.reference_role) for item in result.media_manifest] == [("quoted", "selected_referent")]


def test_model_cannot_select_quoted_without_an_actual_quote() -> None:
    resolver = referent.GroupFollowupReferentResolver()
    prior = _event("alice", "before", "上一句")
    resolver.remember(bot_self_id="self", group_id="g-1", event=prior, media=[], now=100.0)

    async def _model(*_args, **_kwargs):  # noqa: ANN202
        return '{"referent":"quoted","message_id":"made-up","confidence":0.95,"evidence_tags":["quote_content"]}'

    result = asyncio.run(
        resolver.resolve(
            bot_self_id="self",
            group_id="g-1",
            event=_event("alice", "now", "你怎么看"),
            current_media=[],
            addressing_target="bot",
            call_ai_api=_model,
            now=101.0,
        )
    )
    assert result.semantic_referent == "unclear"
    assert result.diagnostic_code == "followup_referent_classifier_failed"


def test_low_confidence_or_invalid_json_never_promotes_antecedent() -> None:
    resolver = referent.GroupFollowupReferentResolver()
    prior = _event("alice", "before", "上一句")
    resolver.remember(bot_self_id="self", group_id="g-1", event=prior, media=[], now=100.0)
    current = _event("alice", "now", "你怎么看")

    async def _low(*_args, **_kwargs):  # noqa: ANN202
        return '{"referent":"antecedent","message_id":"before","confidence":0.79,"evidence_tags":[]}'

    result = asyncio.run(
        resolver.resolve(bot_self_id="self", group_id="g-1", event=current, current_media=[], addressing_target="bot", call_ai_api=_low, now=101.0)
    )
    assert result.semantic_referent == "unclear"
    assert result.diagnostic_code == "followup_referent_low_confidence"

    async def _bad(*_args, **_kwargs):  # noqa: ANN202
        return "not json"

    failed = asyncio.run(
        resolver.resolve(bot_self_id="self", group_id="g-1", event=current, current_media=[], addressing_target="bot", call_ai_api=_bad, now=101.0)
    )
    assert failed.semantic_referent == "unclear"
    assert failed.diagnostic_code == "followup_referent_classifier_failed"


def test_cache_never_keeps_data_or_absolute_media_references() -> None:
    resolver = referent.GroupFollowupReferentResolver()
    prior = _event("alice", "before", "")
    refs = turn_media.extract_turn_media_from_event(
        _event("alice", "before", "", [_image("data:image/png;base64,YWJj", "token")])
    )
    resolver.remember(bot_self_id="self", group_id="g-1", event=prior, media=refs, now=100.0)
    cached = next(iter(resolver._entries.values()))[0]  # noqa: SLF001 - cache boundary regression
    assert cached["media"][0]["ref"] == ""
    assert "data:" not in str(cached)


def test_cache_is_same_sender_thread_bounded_and_keeps_all_media_kinds() -> None:
    resolver = referent.GroupFollowupReferentResolver()
    prior = _event("alice", "before", "图片和媒体")
    prior.thread_id = "thread-a"
    media_event = _event(
        "alice", "before", "", [
            _image("https://img.example/image.png", "image"),
            SimpleNamespace(type="image", data={"url": "https://img.example/sticker.png", "file": "sticker", "sub_type": 1}),
            SimpleNamespace(type="gif", data={"url": "https://img.example/gif.gif", "file": "gif"}),
            SimpleNamespace(type="video", data={"url": "https://img.example/video.mp4", "file": "video"}),
            SimpleNamespace(type="record", data={"file": "audio"}),
        ],
    )
    resolver.remember(
        bot_self_id="self", group_id="g-1", event=prior,
        media=turn_media.extract_turn_media_from_event(media_event), now=100.0,
    )
    cached = next(iter(resolver._entries.values()))[0]  # noqa: SLF001
    assert {item["kind"] for item in cached["media"]} == {"image", "sticker", "gif", "video", "audio"}

    current = _event("alice", "now", "你怎么看")
    current.thread_id = "thread-b"
    async def _unexpected(*_args, **_kwargs):  # noqa: ANN202
        raise AssertionError("different thread must not call the resolver model")
    different_thread = asyncio.run(
        resolver.resolve(bot_self_id="self", group_id="g-1", event=current, current_media=[], addressing_target="bot", call_ai_api=_unexpected, now=101.0)
    )
    assert different_thread.diagnostic_code == "followup_referent_no_candidate"

    expired = asyncio.run(
        resolver.resolve(bot_self_id="self", group_id="g-1", event=_event("alice", "later", "你怎么看"), current_media=[], addressing_target="bot", call_ai_api=_unexpected, now=221.0)
    )
    assert expired.diagnostic_code == "followup_referent_no_candidate"


def test_other_member_media_never_becomes_alice_antecedent() -> None:
    resolver = referent.GroupFollowupReferentResolver()
    bob = _event("bob", "bob-image", "")
    resolver.remember(
        bot_self_id="self", group_id="g-1", event=bob,
        media=turn_media.extract_turn_media_from_event(_event("bob", "bob-image", "", [_image("https://img.example/b.png", "b")])) ,
        now=100.0,
    )
    current = _event("alice", "now", "我刚发的图")
    async def _unexpected(*_args, **_kwargs):  # noqa: ANN202
        raise AssertionError("Bob must not be a same-sender candidate for Alice")
    result = asyncio.run(
        resolver.resolve(bot_self_id="self", group_id="g-1", event=current, current_media=[], addressing_target="bot", call_ai_api=_unexpected, now=101.0)
    )
    assert result.active_media == ()
    assert result.diagnostic_code == "followup_referent_no_candidate"


def test_non_human_or_self_events_never_enter_followup_cache() -> None:
    resolver = referent.GroupFollowupReferentResolver()
    event = _event("peer", "p1", "[MC] result")
    resolver.remember(bot_self_id="self", group_id="g-1", event=event, media=[], source_kind="peer_bot_reply", now=1.0)
    own_event = _event("self", "s1", "my reply")
    resolver.remember(bot_self_id="self", group_id="g-1", event=own_event, media=[], source_kind="user", now=1.0)
    assert resolver._entries == {}  # noqa: SLF001 - cache boundary regression


def test_unknown_evidence_tags_are_dropped_from_strict_result() -> None:
    resolver = referent.GroupFollowupReferentResolver()
    prior = _event("alice", "before", "上一句")
    resolver.remember(bot_self_id="self", group_id="g-1", event=prior, media=[], now=100.0)
    async def _model(*_args, **_kwargs):  # noqa: ANN202
        return '{"referent":"antecedent","message_id":"before","confidence":0.91,"evidence_tags":["same_sender_context","unbounded free form"]}'
    result = asyncio.run(
        resolver.resolve(bot_self_id="self", group_id="g-1", event=_event("alice", "now", "你怎么看"), current_media=[], addressing_target="bot", call_ai_api=_model, now=101.0)
    )
    assert result.evidence_tags == ("same_sender_context",)


def test_final_dialogue_gate_gets_only_structured_manifest_roles() -> None:
    captured: list[dict] = []

    async def _review(messages, **_kwargs):  # noqa: ANN202
        captured.extend(messages)
        return '{"action":"accept","text":"","reason":"ok","flags":[],"segments":["好"]}'

    decision = asyncio.run(
        response_review.final_dialogue_gate(
            _review,
            candidate_text="好",
            raw_message_text="你怎么看",
            followup_referent={"addressing_target": "bot", "semantic_referent": "antecedent", "confidence": 0.91},
            followup_media_manifest=[
                {"reference_role": "address_only", "ref": "C:\\private\\media.png"},
                {"reference_role": "selected_referent", "ref": "https://private.example/image.png"},
            ],
        )
    )
    assert decision.action == "accept"
    review_prompt = str(captured[-1]["content"])
    assert '"role": "address_only"' in review_prompt
    assert "private.example" not in review_prompt
    assert "C:\\private" not in review_prompt


def test_group_context_accepts_only_anonymized_followup_manifest() -> None:
    context = group_context.build_group_conversation_context(
        recent_messages=[],
        followup_referent={"addressing_target": "bot", "semantic_referent": "antecedent", "confidence": 0.91},
        followup_media_manifest=[
            {
                "reference_role": "selected_referent",
                "owner_user_id": "123456",
                "message_id": "987654",
                "kind": "image",
                "ref": "C:\\private\\media.png",
            }
        ],
    )
    rendered = group_context.render_group_conversation_context(context)
    assert "role=selected_referent owner=owner_1 message=message_1 kind=image" in rendered
    assert "123456" not in rendered
    assert "987654" not in rendered
    assert "C:\\private" not in rendered


def test_final_dialogue_gate_fails_closed_when_followup_media_is_ambiguous() -> None:
    async def _failed(*_args, **_kwargs):  # noqa: ANN202
        raise RuntimeError("review unavailable")

    decision = asyncio.run(
        response_review.final_dialogue_gate(
            _failed,
            candidate_text="这就是你刚发的图吧",
            raw_message_text="你怎么看",
            followup_referent={"addressing_target": "bot", "semantic_referent": "unclear"},
            followup_media_manifest=[{"reference_role": "address_only", "owner_user_id": "bot"}],
        )
    )
    assert decision.action == "no_reply"
    assert decision.reason == "protected_review_failed"
