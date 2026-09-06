"""Adapter-neutral inbound/outbound contract for personification surfaces.

This module deliberately has no matcher registration.  It turns an adapter
event into a trusted, namespaced envelope and performs only declared standard
send operations.  QQ-only APIs stay outside this boundary.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from html import escape
from typing import Any, Literal, Protocol

from .message_parts import extract_text_from_parts
from .message_relations import extract_reply_message_id, extract_send_message_id
from .turn_media import TurnMediaRef, extract_turn_media_from_event


ConversationKind = Literal["group", "private", "channel", "unknown"]
DeliveryState = Literal["sent", "unknown", "failed"]


def _text(value: Any) -> str:
    return str(value or "").strip()


def _attr(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _segment_data(segment: Any) -> dict[str, Any]:
    value = _attr(segment, "data", {})
    return dict(value) if isinstance(value, dict) else {}


def _segment_type(segment: Any) -> str:
    return _text(_attr(segment, "type", "")).lower()


@dataclass(frozen=True)
class AdapterCapabilities:
    text: bool = True
    reply: bool = False
    image: bool = False
    audio: bool = False
    video: bool = False
    qq_expression: bool = False
    group_moderation: bool = False


@dataclass(frozen=True)
class InteractionEnvelope:
    platform: str
    bot_id: str
    conversation_kind: ConversationKind
    conversation_id: str
    sender_id: str
    message_id: str
    reply_to_message_id: str = ""
    ordered_media: tuple[TurnMediaRef, ...] = ()
    text: str = ""

    @property
    def bot_namespace(self) -> str:
        # Length prefixes make raw platform/bot IDs (which may contain ':')
        # unambiguous without coercing Satori IDs to integers.
        return f"p{len(self.platform)}:{self.platform}b{len(self.bot_id)}:{self.bot_id}"

    @property
    def conversation_namespace(self) -> str:
        return f"{self.bot_namespace}:c{len(self.conversation_kind)}:{self.conversation_kind}i{len(self.conversation_id)}:{self.conversation_id}"

    @property
    def sender_namespace(self) -> str:
        return f"{self.bot_namespace}:u{len(self.sender_id)}:{self.sender_id}"

    @property
    def legacy_conversation_id(self) -> str:
        """Raw OneBot ID retained for reading pre-namespace records only."""
        return self.conversation_id if self.platform == "onebot" else ""


@dataclass(frozen=True)
class SendReceipt:
    state: DeliveryState
    message_id: str = ""
    code: str = ""


class InteractionAdapter(Protocol):
    platform: str

    def capabilities(self, bot: Any) -> AdapterCapabilities: ...

    def normalize(self, event: Any, *, bot_id: str) -> InteractionEnvelope: ...

    async def send(
        self, bot: Any, event: Any, envelope: InteractionEnvelope, content: Any, *, reply: bool = False
    ) -> SendReceipt: ...


def _stable_media_id(*, platform: str, owner: str, message_id: str, index: int, kind: str, ref: str) -> str:
    seed = "\0".join((platform, owner, message_id, str(index), kind, ref))
    return f"media_{hashlib.sha256(seed.encode('utf-8')).hexdigest()[:20]}"


def _satori_media(event: Any, *, owner: str, message_id: str, conversation_id: str) -> tuple[TurnMediaRef, ...]:
    message = _attr(event, "_message", None) or _attr(event, "message", ())
    try:
        segments = list(message or ())
    except TypeError:
        segments = []
    values: list[TurnMediaRef] = []
    for index, segment in enumerate(segments):
        segment_type = _segment_type(segment)
        kind = {"img": "image", "image": "image", "audio": "audio", "video": "video"}.get(segment_type)
        if kind is None:
            continue
        data = _segment_data(segment)
        ref = _text(data.get("src") or data.get("url"))
        if not ref:
            continue
        values.append(
            TurnMediaRef(
                media_id=_stable_media_id(platform="satori", owner=owner, message_id=message_id, index=index, kind=kind, ref=ref),
                ref=ref,
                origin="current",
                owner_user_id=owner,
                message_id=message_id,
                kind=kind,
                # A transport URL is not visual evidence and is never a
                # content fingerprint.  The controlled download stage fills
                # this only after bytes have been validated.
                content_hash="",
                group_id=conversation_id,
                reference_role="current",
            )
        )
    return tuple(values)


def _satori_reply_id(event: Any) -> str:
    """Satori quote may be parsed into the message instead of ``event.reply``."""
    explicit = extract_reply_message_id(event)
    if explicit:
        return explicit
    message = _attr(event, "_message", None) or _attr(event, "message", ())
    try:
        segments = list(message or ())
    except TypeError:
        return ""
    for segment in segments:
        if _segment_type(segment) in {"quote", "message"}:
            reply_id = _text(_segment_data(segment).get("id"))
            if reply_id:
                return reply_id
    return ""


class OneBotV11InteractionAdapter:
    platform = "onebot"

    def capabilities(self, bot: Any) -> AdapterCapabilities:
        return AdapterCapabilities(
            text=callable(getattr(bot, "send", None)),
            reply=True,
            image=True,
            audio=True,
            video=True,
            qq_expression=True,
            group_moderation=callable(getattr(bot, "set_group_ban", None)),
        )

    def normalize(self, event: Any, *, bot_id: str) -> InteractionEnvelope:
        group_id = _text(_attr(event, "group_id", ""))
        sender = _attr(event, "sender", None)
        sender_id = _text(_attr(sender, "user_id", "") or _attr(event, "user_id", ""))
        message_id = _text(_attr(event, "message_id", "") or _attr(event, "id", ""))
        message = _attr(event, "message", ())
        return InteractionEnvelope(
            platform=self.platform,
            bot_id=_text(bot_id),
            conversation_kind="group" if group_id else "private",
            conversation_id=group_id or sender_id,
            sender_id=sender_id,
            message_id=message_id,
            reply_to_message_id=extract_reply_message_id(event),
            ordered_media=tuple(extract_turn_media_from_event(event)),
            text=extract_text_from_parts(str(message)),
        )

    async def send(self, bot: Any, event: Any, envelope: InteractionEnvelope, content: Any, *, reply: bool = False) -> SendReceipt:
        if not self.capabilities(bot).text:
            return SendReceipt("failed", code="send_not_supported")
        try:
            result = await bot.send(event, content)
        except Exception as exc:
            return _send_exception_receipt(exc)
        message_id = extract_send_message_id(result)
        return SendReceipt("sent" if message_id else "unknown", message_id=message_id, code="onebot_send")


class SatoriInteractionAdapter:
    platform = "satori"

    def capabilities(self, bot: Any) -> AdapterCapabilities:
        features = {str(item).strip().lower() for item in (_attr(bot, "features", ()) or ()) if str(item).strip()}
        # A missing feature list means this adapter has not declared media
        # capability yet; only standard text send is safe to expose.
        media = bool(features & {"message.create", "message.send", "media", "message.media"})
        return AdapterCapabilities(
            text=callable(getattr(bot, "send", None)),
            reply=True,
            image=media,
            audio=media,
            video=media,
            # QQ-specific favorites/recommendations/faces never cross Satori.
            qq_expression=False,
            group_moderation=False,
        )

    def normalize(self, event: Any, *, bot_id: str) -> InteractionEnvelope:
        channel = _attr(event, "channel", None)
        user = _attr(event, "user", None)
        channel_id = _text(_attr(channel, "id", ""))
        channel_type = _attr(channel, "type", None)
        direct = str(getattr(channel_type, "name", channel_type)).upper() == "DIRECT" or str(channel_type) == "1"
        user_id = _text(_attr(user, "id", ""))
        message_object = _attr(event, "message", None)
        message_id = _text(_attr(message_object, "id", "") or _attr(event, "msg_id", ""))
        message = _attr(event, "_message", None) or message_object
        login = _attr(event, "login", None)
        concrete_platform = _text(_attr(login, "platform", "")) or "unknown"
        plaintext = ""
        get_message = getattr(event, "get_message", None)
        try:
            parsed = get_message() if callable(get_message) else message
            extract_plaintext = getattr(parsed, "extract_plain_text", None)
            plaintext = _text(extract_plaintext() if callable(extract_plaintext) else "")
        except Exception:
            plaintext = ""
        return InteractionEnvelope(
            platform=f"satori/{concrete_platform}",
            bot_id=_text(bot_id),
            conversation_kind="private" if direct else "channel" if channel_id else "unknown",
            conversation_id=channel_id or user_id,
            sender_id=user_id,
            message_id=message_id,
            reply_to_message_id=_satori_reply_id(event),
            ordered_media=_satori_media(event, owner=user_id, message_id=message_id, conversation_id=channel_id),
            text=plaintext,
        )

    @staticmethod
    def _render_content(content: Any, *, reply_to: str = "") -> str:
        if isinstance(content, str):
            rendered = escape(content)
        elif isinstance(content, list):
            rendered_parts: list[str] = []
            for part in content:
                if not isinstance(part, dict):
                    rendered_parts.append(escape(str(part)))
                    continue
                kind = _text(part.get("type")).lower()
                if kind == "text":
                    rendered_parts.append(escape(_text(part.get("text"))))
                    continue
                key = f"{kind}_url"
                value = part.get(key, {})
                if kind in {"image", "audio", "video"} and isinstance(value, dict) and _text(value.get("url")):
                    tag = "img" if kind == "image" else kind
                    rendered_parts.append(f'<{tag} src="{escape(_text(value["url"]), quote=True)}"/>')
            rendered = "".join(rendered_parts)
        else:
            rendered = escape(str(content or ""))
        return (f'<quote id="{escape(reply_to, quote=True)}"/>' if reply_to else "") + rendered

    async def send(self, bot: Any, event: Any, envelope: InteractionEnvelope, content: Any, *, reply: bool = False) -> SendReceipt:
        if not self.capabilities(bot).text:
            return SendReceipt("failed", code="send_not_supported")
        try:
            payload = self._render_content(content, reply_to=envelope.message_id if reply else "")
            result = await bot.send(event, payload)
        except Exception as exc:
            return _send_exception_receipt(exc)
        message_id = extract_send_message_id(result)
        return SendReceipt("sent" if message_id else "unknown", message_id=message_id, code="satori_send")


def adapter_for_event(event: Any) -> InteractionAdapter | None:
    module = type(event).__module__.lower()
    if ".satori" in module:
        return SatoriInteractionAdapter()
    if ".onebot.v11" in module:
        return OneBotV11InteractionAdapter()
    return None


def _send_exception_receipt(exc: BaseException) -> SendReceipt:
    """Unknown transport failures must not be mistaken for a safe retry."""
    status = _attr(exc, "status_code", None)
    retcode = _attr(exc, "retcode", None)
    try:
        rejected = (400 <= int(status) < 500 and int(status) not in {408, 409, 425, 429}) or int(retcode) in {400, 403, 404}
    except (TypeError, ValueError):
        rejected = False
    return SendReceipt("failed", code="api_rejected") if rejected else SendReceipt("unknown", code="delivery_unknown")


__all__ = [
    "AdapterCapabilities", "InteractionAdapter", "InteractionEnvelope", "OneBotV11InteractionAdapter",
    "SatoriInteractionAdapter", "SendReceipt", "adapter_for_event",
]
