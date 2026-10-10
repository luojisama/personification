"""Telegram event projection. File IDs are references, never visual evidence."""
from __future__ import annotations

from typing import Any

from .interaction_adapter import AdapterCapabilities, InteractionEnvelope, _attr, _stable_media_id
from .turn_media import TurnMediaRef


class TelegramInteractionAdapter:
    platform = "telegram"

    def capabilities(self, bot: Any) -> AdapterCapabilities:
        return AdapterCapabilities(text=True, reply=True, image=True, audio=True, video=True, group_moderation=True)

    def normalize(self, event: Any, *, bot_id: str) -> InteractionEnvelope:
        chat = _attr(event, "chat", {})
        user = _attr(event, "from_", {})
        sender_chat = _attr(event, "sender_chat", None)
        sender_id = "chat:" + str(_attr(sender_chat, "id")) if sender_chat else str(_attr(user, "id", ""))
        chat_id = str(_attr(chat, "id", ""))
        mid = str(_attr(event, "message_id", ""))
        media: list[TurnMediaRef] = []
        original = list(_attr(event, "original_message", ()) or _attr(event, "message", ()) or ())
        for field, kind in (("photo", "image"), ("sticker", "image"), ("animation", "video"), ("video", "video"), ("voice", "audio"), ("audio", "audio"), ("document", "file")):
            value = _attr(event, field, None)
            if isinstance(value, list):
                value = value[-1] if value else None
            file_id = str(_attr(value, "file_id", "") or "")
            if not file_id:
                segment = next((seg for seg in original if _attr(seg, "type", "") == field), None)
                file_id = str(_attr(_attr(segment, "data", {}), "file", "") or "")
            if not file_id:
                continue
            if field == "sticker" and _attr(value, "is_video", False):
                kind = "video"
            if field == "sticker" and _attr(value, "is_animated", False):
                # TGS animation needs a renderer; do not pretend it is a photo.
                kind = "file"
            ref = "tgfile:" + file_id
            media.append(TurnMediaRef(media_id=_stable_media_id(platform="telegram", owner=sender_id, message_id=chat_id + ":" + mid, index=len(media), kind=kind, ref=ref), ref=ref, origin="current", owner_user_id=sender_id, message_id=mid, kind=kind, content_hash="", group_id=chat_id, reference_role="current"))
        text = str(_attr(event, "text", "") or _attr(event, "caption", "") or "")
        if not text:
            try:
                message = _attr(event, "original_message", None) or event.get_message()
                text = message.extract_plain_text()
            except (AttributeError, ValueError):
                pass
        if any(_attr(event, name, None) for name in ("forward_from", "forward_from_chat", "forward_sender_name", "forward_date")):
            text = "[转发资料；原作者可能不可见，不代表发送者本人观点或经历]\n" + text
        update = _attr(event, "telegram_model", {})
        return InteractionEnvelope(platform="telegram", bot_id=str(bot_id), conversation_kind="private" if _attr(chat, "type", "") == "private" else "group", conversation_id=chat_id, sender_id=sender_id, message_id=mid, reply_to_message_id=str(_attr(_attr(event, "reply_to_message", {}), "message_id", "") or ""), ordered_media=tuple(media), text=text, topic_id=str(_attr(event, "message_thread_id", "") or ""), update_id=str(_attr(update, "update_id", "") or ""), revision=str(_attr(event, "edit_date", "") or ""))

    async def send(self, bot: Any, event: Any, envelope: InteractionEnvelope, content: Any, *, reply: bool = False) -> Any:
        from .telegram_actions import send_parts
        async def operation():
            return await send_parts(bot, str(_attr(_attr(event, "chat", {}), "id", "")), content, topic_id=str(_attr(event, "message_thread_id", "") or ""), reply_to_message_id=str(_attr(event, "message_id", "")) if reply else "")
        service = getattr(bot.adapter, "personification_connections", None)
        result = await service.send_guarded(bot, operation) if service else await operation()
        return result.to_receipt()
