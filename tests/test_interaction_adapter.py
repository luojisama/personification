from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

import nonebot.adapters

from ._loader import load_personification_module


SATORI_ADAPTERS = Path(r"D:\test_artifacts\personification\implementation0906\satori-deps\nonebot\adapters")
if str(SATORI_ADAPTERS) not in nonebot.adapters.__path__:
    nonebot.adapters.__path__.append(str(SATORI_ADAPTERS))

from nonebot.adapters.satori.event import PrivateMessageCreatedEvent, PublicMessageCreatedEvent  # noqa: E402


interaction = load_personification_module("plugin.personification.core.interaction_adapter")


def _satori_event(event_cls, *, channel_type: int, content: str, message_id: str = "msg-1"):
    return event_cls.model_validate(
        {
            "type": "message-created",
            "timestamp": int(datetime.now(timezone.utc).timestamp() * 1000),
            "login": {"sn": 1, "status": 1, "adapter": "test", "platform": "discord", "user": {"id": "bot-A"}},
            "channel": {"id": "channel-A", "type": channel_type},
            "guild": {"id": "guild-A"},
            "user": {"id": "user-A", "name": "Alice"},
            "member": {"user": {"id": "user-A"}, "nick": "Alice"},
            "message": {"id": message_id, "content": content},
        }
    )


def test_satori_public_event_normalizes_real_event_quote_and_ordered_media() -> None:
    event = _satori_event(
        PublicMessageCreatedEvent,
        channel_type=0,
        content=(
            "hello<quote id=\"quoted-1\"/>"
            "<img src=\"https://cdn.test/one.png\"/>"
            "<audio src=\"https://cdn.test/two.mp3\"/>"
            "<video src=\"https://cdn.test/three.mp4\"/>"
        ),
    )
    envelope = interaction.SatoriInteractionAdapter().normalize(event, bot_id="bot-A")

    assert envelope.platform == "satori/discord"
    assert envelope.bot_namespace == "p14:satori/discordb5:bot-A"
    assert envelope.conversation_namespace == "p14:satori/discordb5:bot-A:c7:channeli9:channel-A"
    assert envelope.sender_namespace == "p14:satori/discordb5:bot-A:u6:user-A"
    assert envelope.legacy_conversation_id == ""
    assert envelope.reply_to_message_id == "quoted-1"
    assert [item.kind for item in envelope.ordered_media] == ["image", "audio", "video"]
    assert [item.ref for item in envelope.ordered_media] == [
        "https://cdn.test/one.png", "https://cdn.test/two.mp3", "https://cdn.test/three.mp4",
    ]
    assert all(item.owner_user_id == "user-A" and item.message_id == "msg-1" for item in envelope.ordered_media)
    assert interaction.adapter_for_event(event).__class__ is interaction.SatoriInteractionAdapter


def test_satori_private_event_and_capabilities_do_not_expose_qq_features() -> None:
    event = _satori_event(PrivateMessageCreatedEvent, channel_type=1, content="private")
    adapter = interaction.SatoriInteractionAdapter()
    envelope = adapter.normalize(event, bot_id="discord-bot")
    capabilities = adapter.capabilities(object())

    assert envelope.conversation_kind == "private"
    assert envelope.conversation_id == "channel-A"
    assert capabilities.qq_expression is False
    assert capabilities.group_moderation is False
    assert not capabilities.image and not capabilities.audio and not capabilities.video


def test_satori_send_uses_standard_bot_send_and_records_receipt() -> None:
    event = _satori_event(PublicMessageCreatedEvent, channel_type=0, content="in")
    adapter = interaction.SatoriInteractionAdapter()
    envelope = adapter.normalize(event, bot_id="bot-A")

    class _Bot:
        def __init__(self) -> None:
            self.calls: list[tuple[object, str]] = []

        async def send(self, sent_event, payload):  # noqa: ANN001, ANN201
            self.calls.append((sent_event, payload))
            return [type("Receipt", (), {"id": "sent-42"})()]

    bot = _Bot()
    receipt = asyncio.run(
        adapter.send(
            bot, event, envelope,
            [
                {"type": "text", "text": "reply"},
                {"type": "image", "image_url": {"url": "https://cdn.test/out.png"}},
                {"type": "audio", "audio_url": {"url": "https://cdn.test/out.mp3"}},
                {"type": "video", "video_url": {"url": "https://cdn.test/out.mp4"}},
            ],
            reply=True,
        )
    )

    assert receipt.state == "sent" and receipt.message_id == "sent-42"
    assert bot.calls[0][0] is event
    assert bot.calls[0][1] == (
        '<quote id="msg-1"/>reply<img src="https://cdn.test/out.png"/>'
        '<audio src="https://cdn.test/out.mp3"/><video src="https://cdn.test/out.mp4"/>'
    )


def test_satori_unknown_and_failed_delivery_are_not_reported_as_sent() -> None:
    event = _satori_event(PublicMessageCreatedEvent, channel_type=0, content="in")
    adapter = interaction.SatoriInteractionAdapter()
    envelope = adapter.normalize(event, bot_id="bot-A")

    class _UnknownBot:
        async def send(self, *_args):  # noqa: ANN202
            return []

    class _FailedBot:
        async def send(self, *_args):  # noqa: ANN202
            raise RuntimeError("network down")

    assert asyncio.run(adapter.send(_UnknownBot(), event, envelope, "reply")).state == "unknown"
    failed = asyncio.run(adapter.send(_FailedBot(), event, envelope, "reply"))
    assert failed.state == "unknown" and failed.code == "delivery_unknown"


def test_onebot_envelope_keeps_raw_legacy_id_but_namespaces_new_scope() -> None:
    class _Event:
        group_id = 12345
        user_id = 67890
        message_id = 24680
        message = [{"type": "image", "data": {"url": "https://cdn.test/one.png", "file_id": "f1"}}]
        sender = type("Sender", (), {"user_id": 67890})()
        reply = None

    envelope = interaction.OneBotV11InteractionAdapter().normalize(_Event(), bot_id="112233")
    assert envelope.legacy_conversation_id == "12345"
    assert envelope.conversation_namespace == "p6:onebotb6:112233:c5:groupi5:12345"
    assert envelope.sender_namespace == "p6:onebotb6:112233:u5:67890"
    assert envelope.ordered_media[0].kind == "image"
