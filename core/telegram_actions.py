"""Telegram outbound transport and permission-checked moderation.

Uses official Bot API methods. All parts are validated before the first send;
transport uncertainty stops the batch without replaying any confirmed part.
"""
from __future__ import annotations

import base64
import binascii
import ipaddress
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .interaction_adapter import SendReceipt

_MAX_UPLOAD = 20 * 1024 * 1024
_MEDIA = {"image": "photo", "photo": "photo", "audio": "voice", "voice": "voice", "video": "video", "document": "document", "file": "document", "sticker": "sticker", "animation": "animation"}
_PERMISSION_FIELDS = ("can_send_messages", "can_send_audios", "can_send_documents", "can_send_photos", "can_send_videos", "can_send_video_notes", "can_send_voice_notes", "can_send_polls", "can_send_other_messages", "can_add_web_page_previews")


def _get(value: Any, key: str, default=None):
    return value.get(key, default) if isinstance(value, dict) else getattr(value, key, default)


@dataclass(frozen=True)
class TelegramSendResult:
    state: str
    message_ids: tuple[str, ...]
    receipts: tuple[SendReceipt, ...]
    code: str

    @property
    def partial(self) -> bool:
        return bool(self.message_ids) and self.state != "sent"

    def to_receipt(self) -> SendReceipt:
        # A mixed batch can never be advertised as wholly safe to replay.
        state = "unknown" if self.partial else self.state
        return SendReceipt(state, message_id=self.message_ids[-1] if self.message_ids else "", code=self.code)


def split_text(text: str, limit: int = 4096) -> list[str]:
    """Split by UTF-16 budget without dropping whitespace or code points."""
    if limit < 2:
        raise ValueError("invalid_text_limit")
    parts, current, units = [], [], 0
    for char in text:
        width = 2 if ord(char) > 0xFFFF else 1
        if units + width > limit:
            parts.append("".join(current)); current, units = [], 0
        current.append(char); units += width
    if current:
        parts.append("".join(current))
    return parts


def _media_value(value: Any) -> Any:
    if isinstance(value, (bytes, bytearray)):
        raw = bytes(value)
        if not raw or len(raw) > _MAX_UPLOAD:
            raise ValueError("telegram_upload_size")
        return raw
    if not isinstance(value, str):
        raise ValueError("telegram_media_invalid")
    if value.startswith("data:"):
        header, sep, payload = value.partition(",")
        if not sep or not re.fullmatch(r"data:(image|audio|video|application)/[A-Za-z0-9.+-]+;base64", header) or len(payload) > (_MAX_UPLOAD * 4 // 3 + 4):
            raise ValueError("telegram_media_invalid")
        try:
            return _media_value(base64.b64decode(payload, validate=True))
        except (ValueError, binascii.Error):
            raise ValueError("telegram_media_invalid") from None
    url = urlsplit(value)
    if url.scheme in {"https", "http"} and url.hostname and not url.username and not url.password:
        host = url.hostname.lower()
        if host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
            raise ValueError("telegram_media_url_invalid")
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address is not None and not address.is_global:
            raise ValueError("telegram_media_url_invalid")
        # Credential-bearing Telegram download URLs may not leave this process.
        if re.search(r"/(?:file/)?bot[0-9]+:", url.path, re.IGNORECASE):
            raise ValueError("telegram_media_url_invalid")
        return value
    # Official adapter interprets existing strings as local paths. Only opaque
    # Telegram IDs without path syntax are accepted, and existing paths rejected.
    if re.fullmatch(r"[A-Za-z0-9_-]{20,512}", value) and not Path(value).exists():
        return value
    raise ValueError("telegram_media_path_forbidden")


def _plan_parts(content: Any) -> list[tuple[str, dict]]:
    parts = [{"type": "text", "text": content}] if isinstance(content, str) else content
    if not isinstance(parts, (list, tuple)):
        raise ValueError("telegram_parts_invalid")
    planned = []
    for part in parts:
        if not isinstance(part, dict):
            raise ValueError("telegram_part_invalid")
        kind = str(part.get("type") or "")
        if kind == "text":
            text = part.get("text", "")
            if not isinstance(text, str):
                raise ValueError("telegram_text_invalid")
            text.encode("utf-8")  # Reject invalid surrogate input before any send.
            planned.extend(("send_message", {"text": text}) for text in split_text(text))
        elif kind in _MEDIA:
            media_type = _MEDIA[kind]
            value = part.get(f"{kind}_url") or part.get("data") or part.get("url") or part.get("file")
            if isinstance(value, dict):
                value = value.get("url") or value.get("data") or value.get("file_id")
            planned.append((f"send_{media_type}", {media_type: _media_value(value)}))
        else:
            raise ValueError("telegram_part_unsupported")
    if not planned or len(planned) > 100:
        raise ValueError("telegram_parts_budget")
    return planned


def _failure(exc: BaseException) -> SendReceipt:
    code = str(getattr(exc, "code", ""))
    rejected = {"invalid_token", "forbidden", "receiver_conflict", "rate_limited", "invalid_request"}
    if code in rejected:
        return SendReceipt("failed", code="telegram_" + code)
    # Native adapter ActionFailed confirms the server rejected the request.
    if type(exc).__name__ in {"ActionFailed", "ApiNotAvailable"}:
        return SendReceipt("failed", code="telegram_api_rejected")
    return SendReceipt("unknown", code="telegram_delivery_unknown")


def _numeric(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("telegram_id_invalid")
    return int(str(value))


async def send_parts(bot: Any, chat_id: str | int, content: Any, *, topic_id: str | int = "", reply_to_message_id: str | int = "") -> TelegramSendResult:
    try:
        planned = _plan_parts(content)
        common: dict[str, Any] = {"chat_id": _numeric(chat_id)}
        if topic_id:
            common["message_thread_id"] = _numeric(topic_id)
        if reply_to_message_id:
            common["reply_parameters"] = {"message_id": _numeric(reply_to_message_id), "allow_sending_without_reply": False}
    except (ValueError, TypeError, OverflowError, OSError):
        receipt = SendReceipt("failed", code="telegram_content_invalid")
        return TelegramSendResult("failed", (), (receipt,), receipt.code)
    receipts, ids = [], []
    for method, payload in planned:
        try:
            result = await getattr(bot, method)(**common, **payload)
            message_id = str(_get(result, "message_id", "") or "")
            receipt = SendReceipt("sent" if message_id else "unknown", message_id=message_id, code="telegram_sent" if message_id else "telegram_delivery_unknown")
        except Exception as exc:
            receipt = _failure(exc)
        receipts.append(receipt)
        if receipt.state != "sent":
            return TelegramSendResult(receipt.state, tuple(ids), tuple(receipts), "telegram_partial_delivery" if ids else receipt.code)
        ids.append(receipt.message_id)
    return TelegramSendResult("sent", tuple(ids), tuple(receipts), "telegram_sent")


async def moderate(bot: Any, action: str, chat_id: str | int, *, target_id: str | int = "", message_id: str | int = "", until_date: int | None = None) -> SendReceipt:
    """Caller owns human authorization; fresh Bot/target authority is checked here."""
    action = {"ban": "mute", "unban": "unmute", "remove": "kick", "recall": "delete"}.get(action, action)
    if action not in {"mute", "unmute", "kick", "delete", "pin"}:
        return SendReceipt("failed", code="telegram_action_unsupported")
    try:
        chat = _numeric(chat_id)
        self_id = _numeric(bot.self_id)
        target = _numeric(target_id) if target_id else None
        message = _numeric(message_id) if message_id else None
        if action in {"mute", "unmute", "kick"} and (target is None or target == self_id):
            raise ValueError("invalid_target")
        if action in {"delete", "pin"} and message is None:
            raise ValueError("invalid_message")
        if until_date is not None and not time.time() + 30 <= until_date <= time.time() + 366 * 86400:
            raise ValueError("invalid_duration")
    except (ValueError, TypeError, OverflowError):
        return SendReceipt("failed", code="telegram_action_invalid")
    try:
        own = await bot.get_chat_member(chat_id=chat, user_id=self_id)
        status = str(_get(own, "status", ""))
        required = "can_delete_messages" if action == "delete" else "can_pin_messages" if action == "pin" else "can_restrict_members"
        if status != "creator" and (status != "administrator" or not _get(own, required, False)):
            return SendReceipt("failed", code="telegram_permission_denied")
        if target is not None:
            member = await bot.get_chat_member(chat_id=chat, user_id=target)
            if str(_get(member, "status", "")) in {"creator", "administrator"}:
                return SendReceipt("failed", code="telegram_target_protected")
        if action in {"mute", "unmute"}:
            permissions = {field: False for field in _PERMISSION_FIELDS}
            if action == "unmute":
                info = await bot.get_chat(chat_id=chat)
                defaults = _get(info, "permissions")
                if defaults is None:
                    return SendReceipt("failed", code="telegram_permissions_unavailable")
                permissions = {field: bool(_get(defaults, field, False)) for field in _PERMISSION_FIELDS}
            result = await bot.restrict_chat_member(chat_id=chat, user_id=target, permissions=permissions, use_independent_chat_permissions=True, **({"until_date": until_date} if until_date is not None else {}))
        elif action == "kick":
            result = await bot.ban_chat_member(chat_id=chat, user_id=target, revoke_messages=False)
            if result is not True:
                return SendReceipt("unknown", code="telegram_delivery_unknown")
            try:
                result = await bot.unban_chat_member(chat_id=chat, user_id=target, only_if_banned=True)
            except Exception:
                return SendReceipt("unknown", code="telegram_kick_partial")
        elif action == "delete":
            result = await bot.delete_message(chat_id=chat, message_id=message)
        else:
            result = await bot.pin_chat_message(chat_id=chat, message_id=message, disable_notification=True)
        return SendReceipt("sent" if result is True else "unknown", message_id=str(message or ""), code="telegram_action_confirmed" if result is True else "telegram_delivery_unknown")
    except Exception as exc:
        return _failure(exc)
