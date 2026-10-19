"""Official QQ inbound projection, verified against adapter-qq 1.7.3.

Source: adapters/qq/event.py QQMessageEvent/C2CMessageCreateEvent/
GroupMessageCreateEvent and models/qq.py Attachment. No outbound support yet.
"""
from __future__ import annotations
from typing import Any
import importlib
from urllib.parse import urlsplit
from .interaction_adapter import AdapterCapabilities, InteractionEnvelope, SendReceipt, _attr, _stable_media_id
from .turn_media import TurnMediaRef


def _required(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"missing QQ field: {field}")
    return value.strip()


def _sender():
    try:
        helper = importlib.import_module("qq_output")
    except ImportError:
        return None
    function = getattr(helper, "send_parts_output", None)
    return function if callable(function) else None


def _parts(content: Any) -> list[dict[str, Any]]:
    values = [{"type": "text", "text": content}] if isinstance(content, str) else content
    if not isinstance(values, list) or not values:
        raise ValueError("QQ output requires nonempty standard parts")
    result = []
    for part in values:
        if not isinstance(part, dict):
            raise ValueError("invalid QQ output part")
        if part.get("type") == "text" and isinstance(part.get("text"), str) and part["text"]:
            result.append({"type": "text", "text": part["text"]})
        elif part.get("type") == "image" and isinstance(part.get("data"), bytes) and part["data"]:
            result.append({"type": "image", "data": part["data"]})
        else:
            raise ValueError("unsupported QQ output part")
    return result


class QQOfficialInteractionAdapter:
    platform = "qq_official"

    def capabilities(self, bot: Any) -> AdapterCapabilities:
        available = _sender() is not None
        return AdapterCapabilities(text=available, reply=available, image=available)

    def normalize(self, event: Any, *, bot_id: str) -> InteractionEnvelope:
        bot_id = _required(bot_id, "bot_id")
        message_id = _required(_attr(event, "id"), "id")
        content = _attr(event, "content")
        if not isinstance(content, str):
            raise ValueError("missing QQ field: content")
        author = _attr(event, "author")
        group = _attr(event, "group_openid")
        if group is not None:
            conversation = _required(group, "group_openid")
            sender = _required(_attr(author, "member_openid"), "author.member_openid")
            kind = "group"
        else:
            sender = _required(_attr(author, "user_openid"), "author.user_openid")
            conversation, kind = sender, "private"
        attachments = _attr(event, "attachments") or []
        if not isinstance(attachments, (list, tuple)):
            raise ValueError("invalid QQ attachments")
        media = []
        for index, attachment in enumerate(attachments):
            mime = _attr(attachment, "content_type", "")
            if not isinstance(mime, str) or not mime.lower().startswith("image/"):
                continue
            ref = _required(_attr(attachment, "url"), "attachments.url")
            if ref.startswith("//"):
                ref = "https:" + ref
            elif not urlsplit(ref).scheme:
                ref = "https://" + ref
            parsed = urlsplit(ref)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                raise ValueError("invalid QQ image URL")
            media.append(TurnMediaRef(
                media_id=_stable_media_id(platform=self.platform, owner=sender,
                    message_id=conversation+":"+message_id, index=index, kind="image", ref=ref),
                ref=ref, origin="current", owner_user_id=sender, message_id=message_id,
                kind="image", content_hash="", group_id=conversation, reference_role="current"))
        reply = _attr(event, "reply")
        return InteractionEnvelope(platform=self.platform, bot_id=bot_id,
            conversation_kind=kind, conversation_id=conversation, sender_id=sender,
            message_id=message_id, text=content, ordered_media=tuple(media),
            reply_to_message_id=str(_attr(reply, "id", "") or ""))

    async def send(self, bot: Any, event: Any, envelope: InteractionEnvelope,
                   content: Any, *, reply: bool = False) -> Any:
        sender = _sender()
        if sender is None:
            raise NotImplementedError("official QQ output helper is unavailable")
        parts = _parts(content)
        # Validate the original event and destination, never reconstruct or
        # synthesize a message event from stored IDs for an active send.
        normalized = self.normalize(event, bot_id=envelope.bot_id)
        if (envelope.platform != self.platform or
            (normalized.conversation_id, normalized.sender_id, normalized.message_id) !=
            (envelope.conversation_id, envelope.sender_id, envelope.message_id)):
            raise ValueError("QQ reply envelope does not match the source event")
        if not hasattr(event, "_reply_seq"):
            raise ValueError("QQ passive reply event lacks reply sequence")
        # The helper validates media and holds one event lock across every part.
        # Exceptions, including partial delivery, propagate without any retry.
        results = await sender(bot, event, parts)
        if not isinstance(results, list) or len(results) != len(parts):
            return SendReceipt("unknown", code="qq_incomplete_receipts")
        ids = [_attr(result, "id", None) for result in results]
        if not all(isinstance(mid, str) and mid.strip() for mid in ids):
            return SendReceipt("unknown", code="qq_unconfirmed_receipt")
        return SendReceipt("sent", message_id=ids[-1], code="qq_passive_send")
