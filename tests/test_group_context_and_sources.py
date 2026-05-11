from __future__ import annotations

from types import SimpleNamespace

from ._loader import load_personification_module

event_rules = load_personification_module("plugin.personification.handlers.event_rules")
group_context = load_personification_module("plugin.personification.core.group_context")
target_inference = load_personification_module("plugin.personification.core.target_inference")


def test_target_inference_treats_reply_to_non_bot_as_others() -> None:
    event = SimpleNamespace(
        message=[],
        reply=SimpleNamespace(message_id="m-human", sender=SimpleNamespace(user_id="10001")),
    )

    result = target_inference.infer_message_target(
        event,
        bot_self_id="bot-1",
        recent_group_msgs=[
            {"message_id": "m-human", "user_id": "10001", "is_bot": False},
            {"message_id": "m-bot", "user_id": "bot-1", "is_bot": True},
        ],
    )

    assert result == target_inference.TARGET_OTHERS


def test_target_inference_treats_reply_to_bot_as_bot_target() -> None:
    event = SimpleNamespace(
        message=[],
        reply=SimpleNamespace(message_id="m-bot", sender=SimpleNamespace(user_id="bot-1")),
    )

    result = target_inference.infer_message_target(
        event,
        bot_self_id="bot-1",
        recent_group_msgs=[{"message_id": "m-bot", "user_id": "bot-1", "is_bot": True}],
    )

    assert result == target_inference.TARGET_BOT


def test_record_message_marks_same_bot_generic_output_as_plugin_source() -> None:
    captured: dict[str, object] = {}

    def _record_group_msg(*args, **kwargs):  # noqa: ANN001
        captured["args"] = args
        captured["kwargs"] = kwargs
        return 1

    event = SimpleNamespace(
        group_id="123",
        user_id="bot-1",
        self_id="bot-1",
        sender=SimpleNamespace(card="", nickname="bot"),
        message_id="msg-1",
        message=None,
        reply=None,
        get_plaintext=lambda: "其他插件生成的结果",
    )

    group_id, should_analyze = event_rules.resolve_record_message(
        event,
        get_custom_title=lambda _uid: "",
        record_group_msg=_record_group_msg,
    )

    assert group_id == "123"
    assert should_analyze is False
    assert captured["kwargs"]["source_kind"] == "plugin"


def test_group_context_renders_plugin_source() -> None:
    text = group_context.render_group_context_structured(
        [
            {
                "nickname": "bot",
                "user_id": "bot-1",
                "content": "查询结果",
                "source_kind": "plugin",
                "message_id": "m1",
            }
        ]
    )

    assert "来源=其他插件输出" in text


def test_group_conversation_context_tracks_quote_chain_and_bot_replies() -> None:
    context = group_context.build_group_conversation_context(
        recent_messages=[
            {
                "message_id": "m1",
                "nickname": "甲",
                "user_id": "u1",
                "content": "先说第一句",
            },
            {
                "message_id": "m2",
                "reply_to_msg_id": "m1",
                "nickname": "bot",
                "user_id": "bot-1",
                "content": "我接了一句",
                "source_kind": "bot_reply",
            },
            {
                "message_id": "m3",
                "reply_to_msg_id": "m2",
                "nickname": "乙",
                "user_id": "u2",
                "content": "那继续呢",
            },
        ],
        trigger_msg_id="m3",
        trigger_user_id="u2",
        bot_self_id="bot-1",
        repeat_clusters=[{"text": "那继续呢", "count": 2}],
        bot_recent_replies=["我接了一句"],
    )

    rendered = group_context.render_group_conversation_context(context)

    assert [item["message_id"] for item in context.quote_chain] == ["m1", "m2", "m3"]
    assert context.speaker_relations["u2"] == "乙"
    assert "引用链" in rendered
    assert "bot 最近回复：我接了一句" in rendered
    assert "近段发言线索" in rendered
