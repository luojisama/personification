from __future__ import annotations

import asyncio
import base64
import io
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import nonebot.adapters
from PIL import Image
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageEvent, MessageSegment, PrivateMessageEvent

from ._loader import load_personification_module


SATORI_ADAPTERS = Path(r"D:\test_artifacts\personification\implementation0906\satori-deps\nonebot\adapters")
if str(SATORI_ADAPTERS) not in nonebot.adapters.__path__:
    nonebot.adapters.__path__.append(str(SATORI_ADAPTERS))

from nonebot.adapters.satori.event import PrivateMessageCreatedEvent  # noqa: E402


bridge = load_personification_module("plugin.personification.handlers.satori_reply_bridge")
event_rules = load_personification_module("plugin.personification.handlers.event_rules")


def _private_event():
    return PrivateMessageCreatedEvent.model_validate(
        {
            "type": "message-created", "timestamp": int(datetime.now(timezone.utc).timestamp() * 1000),
            "login": {"sn": 1, "status": 1, "adapter": "test", "platform": "discord", "user": {"id": "bot"}},
            "channel": {"id": "dm-A", "type": 1}, "user": {"id": "user-A"},
            "message": {"id": "satori-msg", "content": "hello<img src=\"https://cdn.test/a.png\"/>"},
        }
    )


def test_satori_private_event_enters_existing_reply_buffer_and_core_with_namespaced_context() -> None:
    seen: dict = {}

    async def _rule(event, state):  # noqa: ANN001, ANN202
        seen["rule_event"] = event
        return True

    async def _core(bot, event, state, deps):  # noqa: ANN001, ANN202
        seen["send_result"] = await bot.send(event, "fake provider reply")
        seen.update(bot=bot, event=event, state=dict(state), types=deps.types)

    class _Bot:
        self_id = "bot"
        features = []

        async def send(self, *_args):  # noqa: ANN202
            return [{"id": "out"}]

    @dataclass
    class _Types:
        poke_event_cls: object = object
        message_event_cls: object = MessageEvent
        group_message_event_cls: object = GroupMessageEvent
        private_message_event_cls: object = PrivateMessageEvent
        message_cls: object = Message

    deps = SimpleNamespace(types=_Types())
    state: dict = {}
    asyncio.run(
        bridge.dispatch_satori_reply(
            _Bot(), _private_event(), state,
            personification_rule=_rule, process_response_logic=_core,
            reply_processor_deps=deps, msg_buffer={}, message_cls=Message,
            message_segment_cls=MessageSegment, message_event_cls=MessageEvent,
            group_message_event_cls=GroupMessageEvent, private_message_event_cls=PrivateMessageEvent,
            poke_event_cls=object, logger=SimpleNamespace(warning=lambda *_args: None),
            plugin_config=SimpleNamespace(personification_response_timeout=30),
        )
    )

    assert seen["event"].get_plaintext() == "hello"
    assert seen["event"].user_id.startswith("p14:satori/discord")
    assert seen["event"].message_id.endswith("satori-msg")
    assert seen["state"]["interaction_envelope"].platform == "satori/discord"
    assert seen["state"]["turn_media_context"][0]["ref"] == "https://cdn.test/a.png"
    assert isinstance(seen["event"], seen["types"].private_message_event_cls[1])
    assert isinstance(seen["event"], seen["types"].message_event_cls[1])
    assert seen["send_result"] == {"message_id": "out"}


def test_satori_bridge_uses_real_private_rule_and_never_exposes_qq_proxy_apis() -> None:
    seen: dict = {}

    async def _real_rule(event, state):  # noqa: ANN001, ANN202
        return await event_rules.personification_rule(
            event, state,
            sign_in_available=False, get_user_data=lambda _user: {}, user_blacklist={},
            logger=SimpleNamespace(debug=lambda *_a: None, info=lambda *_a: None, warning=lambda *_a: None),
            group_event_cls=GroupMessageEvent, private_event_cls=PrivateMessageEvent,
            is_group_whitelisted=lambda *_a: False, plugin_whitelist=[], load_prompt=lambda _id: {},
            load_proactive_state=lambda: {}, is_rest_time=lambda **_kw: True, probability=0.0,
            group_chat_follow_probability=0.0, looks_like_private_command=lambda _text: False,
        )

    async def _processor(bot, event, state, _deps):  # noqa: ANN001, ANN202
        seen.update(event=event, state=dict(state))
        assert state["attention_admitted"] is True
        assert not hasattr(bot, "get_group_member_info")
        # A QQ marker must not become visible text on Satori.
        assert await bot.send(event, "[CQ:face,id=14]") is None

    class _Bot:
        self_id = "bot"
        features = []

        async def send(self, *_args):  # noqa: ANN202
            raise AssertionError("unsupported CQ marker must not call Satori send")

    @dataclass
    class _Types:
        poke_event_cls: object = object
        message_event_cls: object = MessageEvent
        group_message_event_cls: object = GroupMessageEvent
        private_message_event_cls: object = PrivateMessageEvent
        message_cls: object = Message

    state: dict = {}
    asyncio.run(bridge.dispatch_satori_reply(
        _Bot(), _private_event(), state, personification_rule=_real_rule,
        process_response_logic=_processor, reply_processor_deps=SimpleNamespace(types=_Types()),
        msg_buffer={}, message_cls=Message, message_segment_cls=MessageSegment,
        message_event_cls=MessageEvent, group_message_event_cls=GroupMessageEvent,
        private_message_event_cls=PrivateMessageEvent, poke_event_cls=object,
        logger=SimpleNamespace(warning=lambda *_args: None),
        plugin_config=SimpleNamespace(personification_response_timeout=30),
    ))
    assert seen["event"].reply_to_message_id == ""
    media = seen["state"]["turn_media_context"][0]
    assert media["owner_user_id"] == seen["event"].user_id
    assert media["message_id"] == seen["event"].message_id
    assert media["group_id"].startswith("p14:satori/discord")


def test_satori_outbound_drops_file_and_cq_but_turns_checked_base64_into_data_url() -> None:
    payload = io.BytesIO()
    Image.new("RGB", (4, 4), "pink").save(payload, "PNG")
    encoded = base64.b64encode(payload.getvalue()).decode("ascii")
    parts = bridge._satori_outbound_parts(
        MessageSegment.image(f"base64://{encoded}"), allow_media=True
    )
    assert parts == [{"type": "image", "image_url": {"url": f"data:image/png;base64,{encoded}"}}]
    assert bridge._satori_outbound_parts(
        MessageSegment.image("file:///D:/private/sticker.png"), allow_media=True
    ) == []
    assert bridge._satori_outbound_parts("[CQ:image,file=x]", allow_media=True) == []
