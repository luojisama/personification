"""Optional Satori entrypoint that reuses the normal reply buffer and core."""

from __future__ import annotations

import asyncio
import base64
from copy import copy
from dataclasses import is_dataclass, replace
from typing import Any

from nonebot import on_message
from nonebot.adapters import Bot, Event
from nonebot.rule import Rule
from nonebot.typing import T_State

from ..core.interaction_adapter import InteractionEnvelope, SatoriInteractionAdapter
from ..core.turn_media import TurnMediaRef, serialize_turn_media
from .reply_buffer import handle_reply_event
from .reply_pipeline.processor import TypeDeps


def _namespaced_message_id(envelope: InteractionEnvelope) -> str:
    raw = envelope.message_id
    return f"{envelope.bot_namespace}:m{len(raw)}:{raw}" if raw else ""


def _namespaced_reply_id(envelope: InteractionEnvelope) -> str:
    raw = str(envelope.reply_to_message_id or "").strip()
    return f"{envelope.bot_namespace}:m{len(raw)}:{raw}" if raw else ""


def _pipeline_envelope(envelope: InteractionEnvelope) -> InteractionEnvelope:
    """Namespace every value that will enter legacy buffer/history structures."""
    message_id = _namespaced_message_id(envelope)
    conversation_id = envelope.conversation_namespace
    sender_id = envelope.sender_namespace
    media = tuple(
        replace(
            item,
            owner_user_id=sender_id,
            message_id=message_id,
            group_id=conversation_id,
        )
        for item in envelope.ordered_media
    )
    return replace(
        envelope,
        conversation_id=conversation_id,
        sender_id=sender_id,
        message_id=message_id,
        reply_to_message_id=_namespaced_reply_id(envelope),
        ordered_media=media,
    )


class _SatoriPipelineEvent:
    """Small trusted projection consumed by the existing OneBot-oriented core."""

    def __init__(self, *, source: Any, envelope: InteractionEnvelope, message: Any) -> None:
        self._satori_source_event = source
        self._personification_interaction_envelope = envelope
        self.message = message
        self.raw_message = envelope.text
        self.user_id = envelope.sender_id
        self.group_id = envelope.conversation_id if envelope.conversation_kind == "channel" else ""
        self.message_id = envelope.message_id
        self.self_id = envelope.bot_namespace
        self.to_me = bool(getattr(source, "to_me", False))
        self.reply_to_message_id = envelope.reply_to_message_id
        self.reply = None
        self.sender = type("SatoriSender", (), {"user_id": self.user_id, "nickname": ""})()

    def get_plaintext(self) -> str:
        return self.raw_message

    def is_tome(self) -> bool:
        return self.to_me


class _SatoriPipelineGroupEvent(_SatoriPipelineEvent):
    pass


class _SatoriPipelinePrivateEvent(_SatoriPipelineEvent):
    pass


def _onebot_message_from_envelope(envelope: InteractionEnvelope, *, message_cls: Any, segment_cls: Any) -> Any:
    message = message_cls()
    if envelope.reply_to_message_id:
        try:
            message.append(segment_cls.reply(envelope.reply_to_message_id))
        except Exception:
            pass
    if envelope.text:
        message.append(segment_cls.text(envelope.text))
    for media in envelope.ordered_media:
        try:
            if media.kind == "image":
                segment = segment_cls.image(media.ref)
                # The established processor reads ``url`` for incoming image
                # download/projection, while OneBot's constructor commonly
                # stores its positional value as ``file``.  This is the
                # trusted adapter-normalized ref, never a model-provided URL.
                data = getattr(segment, "data", None)
                if isinstance(data, dict) and not data.get("url"):
                    data["url"] = media.ref
                message.append(segment)
            elif media.kind == "audio":
                message.append(segment_cls.record(media.ref))
            elif media.kind == "video":
                message.append(segment_cls.video(media.ref))
        except Exception:
            # The media remains in envelope/state for the common media path;
            # no malformed segment is allowed to crash a whole text turn.
            continue
    return message


def _data_url_from_base64(value: str) -> str:
    raw = str(value or "").strip()
    if not raw.startswith("base64://"):
        return ""
    try:
        payload = base64.b64decode(raw.removeprefix("base64://"), validate=True)
        from PIL import Image
        import io

        with Image.open(io.BytesIO(payload)) as image:
            mime = Image.MIME.get(image.format or "", "")
            image.verify()
        if mime not in {"image/jpeg", "image/png", "image/webp", "image/gif"}:
            return ""
        return f"data:{mime};base64,{base64.b64encode(payload).decode('ascii')}"
    except Exception:
        return ""


def _satori_outbound_parts(content: Any, *, allow_media: bool) -> list[dict[str, Any]]:
    """Translate normal pipeline's OneBot message object into standard parts."""
    if isinstance(content, str):
        # Never reflect a OneBot CQ marker onto a non-OneBot platform.
        return [{"type": "text", "text": content}] if "[CQ:" not in content else []
    parts: list[dict[str, Any]] = []
    try:
        segments = [content] if hasattr(content, "type") and hasattr(content, "data") else list(content)
    except TypeError:
        return []
    for segment in segments:
        kind = str(getattr(segment, "type", "") or "").lower()
        data = getattr(segment, "data", {}) or {}
        if not isinstance(data, dict):
            data = {}
        if kind == "text":
            parts.append({"type": "text", "text": str(data.get("text", "") or "")})
        elif kind == "image":
            value = str(data.get("url") or data.get("file") or "").strip()
            if allow_media and value.startswith(("http://", "https://", "data:image/")):
                parts.append({"type": "image", "image_url": {"url": value}})
            elif allow_media:
                data_url = _data_url_from_base64(value)
                if data_url:
                    parts.append({"type": "image", "image_url": {"url": data_url}})
        elif kind in {"record", "audio"}:
            value = str(data.get("url") or data.get("file") or "").strip()
            if allow_media and value.startswith(("http://", "https://", "data:audio/")):
                parts.append({"type": "audio", "audio_url": {"url": value}})
        elif kind == "video":
            value = str(data.get("url") or data.get("file") or "").strip()
            if allow_media and value.startswith(("http://", "https://", "data:video/")):
                parts.append({"type": "video", "video_url": {"url": value}})
    return parts


class _SatoriBotProxy:
    def __init__(self, bot: Any, event: Any, envelope: InteractionEnvelope, adapter: SatoriInteractionAdapter) -> None:
        self._bot, self._event, self._envelope, self._adapter = bot, event, envelope, adapter
        self.self_id = envelope.bot_namespace

    async def send(self, _event: Any, content: Any, **_kwargs: Any) -> Any:
        parts = _satori_outbound_parts(
            content, allow_media=self._adapter.capabilities(self._bot).image
        )
        if not parts:
            return None
        receipt = await self._adapter.send(
            self._bot, self._event, self._envelope, parts, reply=False
        )
        if receipt.state == "sent":
            return {"message_id": receipt.message_id}
        return None


async def dispatch_satori_reply(
    bot: Bot,
    event: Event,
    state: T_State,
    *,
    personification_rule: Any,
    process_response_logic: Any,
    reply_processor_deps: Any,
    msg_buffer: dict[str, dict[str, Any]],
    message_cls: Any,
    message_segment_cls: Any,
    message_event_cls: Any,
    group_message_event_cls: Any,
    private_message_event_cls: Any,
    poke_event_cls: Any,
    logger: Any,
    plugin_config: Any,
    run_buffer_timer: Any = None,
) -> None:
    adapter = SatoriInteractionAdapter()
    if not adapter.capabilities(bot).text:
        return
    source_envelope = adapter.normalize(event, bot_id=str(getattr(bot, "self_id", "") or ""))
    if not source_envelope.sender_id or not source_envelope.conversation_id:
        return
    envelope = _pipeline_envelope(source_envelope)
    pipeline_event_cls = _SatoriPipelinePrivateEvent if source_envelope.conversation_kind == "private" else _SatoriPipelineGroupEvent
    pipeline_event = pipeline_event_cls(
        source=event,
        envelope=envelope,
        message=_onebot_message_from_envelope(envelope, message_cls=message_cls, segment_cls=message_segment_cls),
    )
    state["interaction_envelope"] = envelope
    state["turn_media_context"] = serialize_turn_media(envelope.ordered_media)
    if not await personification_rule(pipeline_event, state):
        return
    types = reply_processor_deps.types
    scoped_types = TypeDeps(
        poke_event_cls=types.poke_event_cls,
        message_event_cls=(message_event_cls, _SatoriPipelineEvent),
        group_message_event_cls=(group_message_event_cls, _SatoriPipelineGroupEvent),
        private_message_event_cls=(private_message_event_cls, _SatoriPipelinePrivateEvent),
        message_cls=types.message_cls,
    )
    scoped_deps = (
        replace(reply_processor_deps, types=scoped_types)
        if is_dataclass(reply_processor_deps)
        else copy(reply_processor_deps)
    )
    if not is_dataclass(reply_processor_deps):
        scoped_deps.types = scoped_types

    async def _process(proxy_bot: Any, projected_event: Any, projected_state: dict[str, Any]) -> None:
        await process_response_logic(proxy_bot, projected_event, projected_state, scoped_deps)

    async def _start_timer(key: str, proxy_bot: Any, wait_seconds: float) -> None:
        if not callable(run_buffer_timer):
            return
        await run_buffer_timer(
            key, proxy_bot,
            msg_buffer=msg_buffer,
            process_response_logic=_process,
            message_event_cls=(message_event_cls, _SatoriPipelineEvent),
            message_cls=message_cls,
            message_segment_cls=message_segment_cls,
            logger=logger,
            delay=wait_seconds,
            response_timeout_seconds=float(getattr(plugin_config, "personification_response_timeout", 180) or 180),
        )

    await handle_reply_event(
        _SatoriBotProxy(bot, event, envelope, adapter), pipeline_event, state,
        poke_event_cls=poke_event_cls,
        message_event_cls=(message_event_cls, _SatoriPipelineEvent),
        group_message_event_cls=(group_message_event_cls, _SatoriPipelineGroupEvent),
        process_response_logic=_process,
        msg_buffer=msg_buffer,
        start_buffer_timer=lambda key, proxy_bot, wait: asyncio.create_task(_start_timer(key, proxy_bot, wait)),
        logger=logger,
        response_timeout_seconds=float(getattr(plugin_config, "personification_response_timeout", 180) or 180),
    )


def register_satori_reply_bridge(**kwargs: Any) -> dict[str, Any]:
    """Register only when the optional adapter is installed in this venv."""
    try:
        from nonebot.adapters.satori.event import MessageCreatedEvent
    except Exception:
        return {}

    async def _satori_rule(event: Any) -> bool:
        return isinstance(event, MessageCreatedEvent)

    matcher = on_message(rule=Rule(_satori_rule), priority=100, block=True)

    @matcher.handle()
    async def _handle(bot: Bot, event: Event, state: T_State) -> None:
        await dispatch_satori_reply(bot, event, state, **kwargs)

    return {"satori_reply_matcher": matcher}


__all__ = ["dispatch_satori_reply", "register_satori_reply_bridge"]
