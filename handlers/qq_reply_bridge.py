"""Passive official QQ reply bridge; no matcher is installed at import time."""
from __future__ import annotations
import base64
import io
import json
import time
from datetime import datetime, timezone
from typing import Any
from nonebot.adapters import Bot, Event
from nonebot.typing import T_State
from ..core.qq_interaction import QQOfficialInteractionAdapter
from ..core.data_store import get_data_store
from .satori_reply_bridge import dispatch_satori_reply, _satori_outbound_parts

MAX_IMAGE_BYTES = 8 * 1024 * 1024


def _standard_parts(content: Any) -> list[dict[str, Any]]:
    # Reject unsupported segments before the generic translator can omit them.
    expected_images = 0
    if not isinstance(content, str):
        segments = [content] if hasattr(content, "type") else list(content)
        for segment in segments:
            kind = str(getattr(segment, "type", ""))
            if kind not in {"text", "image"}:
                raise ValueError("unsupported official QQ output segment")
            if kind == "image":
                expected_images += 1
                data = getattr(segment, "data", {})
                ref = str(data.get("url") or data.get("file") or "")
                if not ref.startswith(("data:image/", "base64://")):
                    raise ValueError("QQ image requires inline bytes")
                if len(ref) > MAX_IMAGE_BYTES * 4 // 3 + 1024:
                    raise ValueError("QQ inline image too large")
        content = segments
    parts = _satori_outbound_parts(content, allow_media=True)
    if sum(part.get("type") == "image" for part in parts) != expected_images:
        raise ValueError("invalid inline image was not translated")
    if not parts:
        raise ValueError("unsupported official QQ output")
    result = []
    for part in parts:
        if part["type"] == "text":
            if part["text"]:
                result.append(part)
            continue
        ref = part.get("image_url", {}).get("url", "")
        header, separator, payload = ref.partition(",")
        if not separator or header not in {"data:image/png;base64", "data:image/jpeg;base64", "data:image/webp;base64", "data:image/gif;base64"}:
            raise ValueError("unsupported QQ inline image")
        if len(payload) > MAX_IMAGE_BYTES * 4 // 3 + 4:
            raise ValueError("QQ inline image too large")
        raw = base64.b64decode(payload, validate=True)
        if not raw or len(raw) > MAX_IMAGE_BYTES:
            raise ValueError("QQ inline image too large")
        from PIL import Image
        with Image.open(io.BytesIO(raw)) as image:
            if max(image.size) > 4096:
                raise ValueError("QQ image dimensions exceeded")
            image.verify()
        result.append({"type": "image", "data": raw})
    if not result:
        raise ValueError("empty QQ output")
    return result


class QQBotProxy:
    def __init__(self, bot: Any, event: Any, envelope: Any, adapter: Any) -> None:
        self._bot, self._event, self._adapter = bot, event, adapter
        # dispatch provides a namespaced pipeline envelope; outbound validation
        # must instead compare the untouched official source event and raw IDs.
        self._source_envelope = adapter.normalize(event, bot_id=str(bot.self_id))
        self.self_id = envelope.bot_namespace
        self.adapter = bot.adapter

    async def send(self, _event: Any, content: Any, **kwargs: Any) -> Any:
        parts = _standard_parts(content)
        receipt = await self._adapter.send(self._bot, self._event,
            self._source_envelope, parts, reply=False)
        if receipt.state != "sent" or not receipt.message_id:
            raise RuntimeError("official QQ delivery is unconfirmed")
        return {"message_id": receipt.message_id}


async def dispatch_qq_reply(bot: Any, event: Any, state: Any, **kwargs: Any) -> None:
    adapter = QQOfficialInteractionAdapter()
    envelope = adapter.normalize(event, bot_id=str(bot.self_id))
    timestamp = getattr(event, 'timestamp', None)
    if isinstance(timestamp, str):
        timestamp = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
    if not isinstance(timestamp, datetime) or timestamp.tzinfo is None:
        raise ValueError('QQ event timestamp required')
    expires = timestamp.timestamp() + (300 if envelope.conversation_kind == 'group' else 3600)
    now = time.time()
    if expires <= now:
        return
    key = json.dumps([envelope.bot_id, envelope.conversation_kind,
                      envelope.conversation_id, envelope.message_id])
    accepted = False
    def claim(current):
        nonlocal accepted
        entries = {k:v for k,v in (current or {}).items() if isinstance(v,(int,float)) and v > now}
        if key not in entries:
            entries[key] = expires
            accepted = True
        return entries
    get_data_store().mutate_sync('qq_official_received', claim)
    if not accepted:
        return
    # Full-stream group messages are not directed at the bot. Preserve this
    # distinction for the normal model-driven participation policy.
    from nonebot.adapters.qq.event import C2CMessageCreateEvent, GroupAtMessageCreateEvent
    object.__setattr__(event, "to_me", isinstance(event, (C2CMessageCreateEvent, GroupAtMessageCreateEvent)))
    original_rule = kwargs.pop("personification_rule", None)
    if original_rule is not None:
        async def admitted(projected: Any, projected_state: dict[str, Any]) -> bool:
            from nonebot.adapters.qq.event import GroupMessageCreateEvent
            # Platform delivery grants conversation scope, not direct-address
            # status or administrative permission. Existing policy checks stay.
            projected_state["qq_official_scope_admitted"] = bool(
                isinstance(event, GroupMessageCreateEvent)
                and getattr(kwargs.get("plugin_config"), "personification_qq_official_enabled", False)
            )
            return await original_rule(projected, projected_state)
        kwargs["personification_rule"] = admitted
    await dispatch_satori_reply(bot, event, state, interaction_adapter=adapter,
        source_envelope=envelope, proxy_factory=QQBotProxy, **kwargs)


def register_qq_reply_bridge(**kwargs: Any) -> dict[str, Any]:
    try:
        from nonebot.adapters.qq.event import C2CMessageCreateEvent, GroupMessageCreateEvent
    except ImportError as exc:
        raise RuntimeError('official QQ adapter required for enabled bridge') from exc
    from nonebot import on_message
    from nonebot.rule import Rule
    async def rule(event: Event) -> bool:
        return isinstance(event, (C2CMessageCreateEvent, GroupMessageCreateEvent)) and not bool(getattr(getattr(event, 'author', None), 'bot', False))
    matcher = on_message(rule=Rule(rule), priority=100, block=True)
    @matcher.handle()
    async def handler(bot: Bot, event: Event, state: T_State):
        await dispatch_qq_reply(bot, event, state, **kwargs)
    return {"qq_reply_matcher": matcher}
