"""Explicit private-chat commands for two-party account linking."""
from __future__ import annotations

import asyncio
import logging
import re
from contextvars import ContextVar
from typing import Any, Callable

from nonebot.adapters import Bot, Event

from ..core.account_links import AccountLinkError, get_account_links, numeric_id

COMMANDS = frozenset({"bindqq", "bindtg", "bindstatus", "unbind", "unbindqq", "unbindtg", "cancelbind"})
_private_qq_send: ContextVar[bool] = ContextVar("private_qq_account_link_send", default=False)
_CONFIRM_IN_LOG = re.compile(r"(/bindtg(?:@[A-Za-z0-9_]+)?)(?:\s|\\[nrt])+[^\s\\\"'<>]+", re.IGNORECASE)


class _ConfirmationTransportLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if _private_qq_send.get():
            return False
        text = record.getMessage()
        safe = _CONFIRM_IN_LOG.sub(r"\1 [凭据已隐藏]", text)
        if safe != text:
            record.msg, record.args = safe, ()
        return True
_ERRORS = {
    "invalid_identity": "请使用真实的数字账号 ID。",
    "rate_limited": "操作过于频繁，请至少等待一分钟后重试。",
    "already_linked": "当前账号已绑定，请先查看 /bindstatus；要改绑请先 /unbind。",
    "target_already_linked": "这个 QQ 账号已在当前 Telegram Bot 上绑定，请先从 QQ 私聊 /unbind 后重试。",
    "invalid_or_expired_code": "确认凭据无效或已过期，请在发起绑定的 Telegram 私聊重新申请。",
}


def _text(event: Any) -> str:
    # b20 _check_tome mutates message; the original keeps the command intact.
    original = getattr(event, "original_message", None)
    if original is not None:
        return original.extract_plain_text().strip()
    try:
        return event.get_message().extract_plain_text().strip()
    except (AttributeError, ValueError):
        return ""


def parse_command(event: Any, bot: Any = None) -> tuple[str, list[str]] | None:
    text = _text(event)
    if not text.startswith("/"):
        return None
    parts = text.split()
    command, _, mention = parts[0][1:].partition("@")
    command = command.lower()
    if command not in COMMANDS:
        return None
    if mention and (bot is None or mention.lower() != str(getattr(bot, "username", "")).lower()):
        return None
    return command, parts[1:]


def has_confirmation_command(event: Any) -> bool:
    text = _text(event)
    return bool(text and text.split(maxsplit=1)[0].split("@", 1)[0].lower() == "/bindtg")


def protect_account_command_logs() -> None:
    """NoneBot prints events before its preprocessors, so mask at source.

    This changes log rendering only, preserving protocol text for validation.
    The command matcher blocks these events before archive/model processing.
    """
    from nonebot.adapters.onebot.v11 import MessageEvent as QQMessageEvent
    protect_qq_account_send_logs()
    classes = [QQMessageEvent]
    try:
        from nonebot.adapters.telegram.event import MessageEvent, EditedMessageEvent
        classes.extend((MessageEvent, EditedMessageEvent))
    except ImportError:
        pass
    for cls in classes:
        original = cls.get_log_string
        if getattr(original, "_personification_account_guard", False):
            continue

        def safe_log(event: Any, _original: Callable = original) -> str:
            # Mask even a command addressed to another Bot or sent in a group.
            if has_confirmation_command(event):
                return "[账号绑定确认；凭据已隐藏]"
            return _original(event)

        safe_log._personification_account_guard = True  # type: ignore[attr-defined]
        cls.get_log_string = safe_log


def protect_qq_account_send_logs() -> None:
    from nonebot.adapters.onebot.v11 import adapter
    from ..core.telegram_media import protect_transport_logs
    protect_transport_logs()
    # WebSocket drivers may log entire frames, before an Event is parsed.
    # Filters belong to the concrete emitters; parent filters do not propagate.
    for name in ("websockets.client", "websockets.server", "websockets.protocol",
                 "websockets.asyncio.client", "websockets.asyncio.server", "uvicorn.error"):
        logger = logging.getLogger(name)
        if not any(isinstance(item, _ConfirmationTransportLogFilter) for item in logger.filters):
            logger.addFilter(_ConfirmationTransportLogFilter())
    original = adapter.log
    if getattr(original, "_personification_account_send_guard", False):
        return

    def protected_log(*args: Any, **kwargs: Any) -> Any:
        if not _private_qq_send.get():
            return original(*args, **kwargs)
        return None

    protected_log._personification_account_send_guard = True  # type: ignore[attr-defined]
    adapter.log = protected_log


def _onebot_bots(get_bots: Callable[[], dict[str, Any]]) -> list[Any]:
    from nonebot.adapters.onebot.v11 import Bot
    return [bot for bot in (get_bots() or {}).values() if isinstance(bot, Bot)]


async def _friend(bot: Any, qq_user_id: str) -> bool | None:
    try:
        friends = await asyncio.wait_for(bot.call_api("get_friend_list"), 15)
        if not isinstance(friends, list):
            return None
        return any(str(item.get("user_id")) == qq_user_id for item in friends if isinstance(item, dict))
    except Exception:
        return None


def _receipt_confirmed(result: Any) -> bool:
    message_id = result.get("message_id") if isinstance(result, dict) else getattr(result, "message_id", None)
    return isinstance(message_id, int) and not isinstance(message_id, bool) and message_id != 0


async def handle_account_link_command(bot: Any, event: Any, *, get_bots: Callable[[], dict[str, Any]],
                                      service: Any = None) -> str | None:
    """Returns a safe response; the caller must block consumed commands."""
    from nonebot.adapters.onebot.v11 import Bot as QQBot, PrivateMessageEvent as QQPrivateEvent
    from ..core.telegram_connections import native_telegram

    parsed = parse_command(event, bot)
    if parsed is None:
        return None
    command, args = parsed
    is_tg = native_telegram(bot)
    if is_tg:
        chat, sender = getattr(event, "chat", None), getattr(event, "from_", None)
        if (getattr(chat, "type", None) != "private" or getattr(event, "sender_chat", None)
                or sender is None or getattr(sender, "is_bot", False) or str(getattr(chat, "id", "")) != str(getattr(sender, "id", ""))):
            return "账号绑定、查询和解绑请在机器人私聊中操作。"
        if any(getattr(event, key, None) for key in ("forward_from", "forward_from_chat", "forward_sender_name", "forward_date", "edit_date")):
            return "请本人直接发送绑定命令。"
        user = str(sender.id)
    elif isinstance(bot, QQBot):
        if not isinstance(event, QQPrivateEvent) or event.sub_type != "friend":
            return "账号绑定、查询和解绑请在 QQ 好友私聊中操作。"
        user = str(event.user_id)
    else:
        return None
    service = service or get_account_links()
    try:
        bot_id, user = numeric_id(bot.self_id), numeric_id(user)
        if command == "bindstatus":
            if args:
                return "用法：/bindstatus"
            if is_tg:
                link = service.resolve_telegram(bot_id, user)
                if link:
                    return f"已绑定 QQ {link.qq_user_id}（QQ Bot {link.qq_bot_id}）。共享个人资料、关系、经历卡、双方私聊记忆和历史；管理员权限独立。\n解绑：/unbind"
                pending = service.pending_telegram(bot_id, user)
                if pending:
                    state = "等待 QQ 本人确认" if pending["state"] == "awaiting_confirmation" else "等待 QQ 侧可用后重新申请投递"
                    return f"待绑定 QQ {pending['qq_user_id']}：{state}。十分钟内有效；取消：/cancelbind"
            else:
                links = service.links_for_qq(bot_id, user)
                pending = service.pending_for_qq(bot_id, user)
                if links:
                    lines = [f"Telegram {link.tg_user_id}（TG Bot {link.tg_bot_id}）" for link in links]
                    return "当前绑定：\n" + "\n".join(lines) + "\n共享个人资料、关系、经历卡、双方私聊记忆和历史；管理员权限独立。\n解绑全部：/unbind"
                if pending:
                    return "存在待确认请求，请只确认本人刚发起的绑定。拒绝全部请求：/cancelbind"
            return "当前没有已验证绑定。Telegram 私聊发起：/bindqq <QQ号>"
        if command in {"unbind", "unbindqq", "unbindtg", "cancelbind"}:
            if args:
                return "用法：/unbind 或 /cancelbind"
            if command == "cancelbind":
                removed = service.cancel_pending_telegram(bot_id, user) if is_tg else service.cancel_pending_qq(bot_id, user)
                return "已撤销待确认请求；已验证绑定保持生效。" if removed else "当前没有待确认请求。"
            removed = service.revoke_telegram(bot_id, user) if is_tg else service.revoke_qq(bot_id, user)
            return "已解除账号绑定并撤销待确认请求；资料仍保留在各自原账号下。" if removed else "当前没有绑定或待确认请求。"
        if command == "bindtg":
            if is_tg:
                return "请在接到确认消息的 QQ 好友私聊发送 /bindtg <确认凭据>。"
            if len(args) != 1:
                return "用法：/bindtg <确认凭据>"
            service.reserve_command_attempt(platform="qq", bot_id=bot_id, user_id=user, action="confirm")
            if await _friend(bot, user) is not True:
                return "暂时无法核实 QQ 好友关系，请稍后重试。"
            link = service.confirm(qq_bot_id=bot_id, qq_user_id=user, code=args[0])
            return f"已确认绑定 Telegram {link.tg_user_id}（TG Bot {link.tg_bot_id}）。\n双方可用 /bindstatus 查询，或 /unbind 随时解除；管理员权限独立。"
        if not is_tg:
            return "请在 Telegram 机器人私聊发送 /bindqq <QQ号>，再回来确认。"
        if len(args) not in {1, 2}:
            return "用法：/bindqq <QQ号>；有多个 QQ Bot 时使用 /bindqq <QQ号> <QQ Bot号>"
        qq_user = numeric_id(args[0])
        service.reserve_command_attempt(platform="telegram", bot_id=bot_id, user_id=user, action="begin")
        # An existing request pins the QQ Bot, including while it is offline.
        pending = service.pending_telegram(bot_id, user)
        explicit_bot = numeric_id(args[1]) if len(args) == 2 else ""
        pinned_bot = explicit_bot or (str(pending.get("qq_bot_id") or "") if pending and pending["qq_user_id"] == qq_user else "")
        qq_bots = _onebot_bots(get_bots)
        if not pinned_bot and len(qq_bots) > 1:
            return "当前有多个 QQ Bot，请指定好友所属机器人：/bindqq <QQ号> <QQ Bot号>"
        selected = next((candidate for candidate in qq_bots if str(candidate.self_id) == pinned_bot), None) if pinned_bot else next(iter(qq_bots), None)
        if selected is None:
            service.begin(tg_bot_id=bot_id, tg_user_id=user, qq_user_id=qq_user, qq_bot_id=pinned_bot)
            return "QQ 侧暂时离线，已记录待确认请求，尚未建立绑定。请在 QQ Bot 上线后重新发送同一 /bindqq 命令。"
        qq_bot_id = numeric_id(selected.self_id)
        friendship = await _friend(selected, qq_user)
        if friendship is None:
            service.begin(tg_bot_id=bot_id, tg_user_id=user, qq_user_id=qq_user, qq_bot_id=qq_bot_id)
            return "QQ 侧暂时无法核实好友关系，已记录待确认请求，尚未建立绑定。请稍后重新发送同一 /bindqq 命令。"
        if not friendship:
            return "QQ 好友关系尚未核实，当前未建立绑定。请先添加指定 QQ Bot 为好友，再稍后重试。"
        claim = service.begin(tg_bot_id=bot_id, tg_user_id=user, qq_user_id=qq_user, qq_bot_id=qq_bot_id, issue_code=True)
        message = (f"Telegram 账号 {user} 请求关联你的 QQ（TG Bot {bot_id}）。\n"
                   "确认后将共享双方个人资料、关系、经历卡和私聊记忆。只有你本人刚发起时才确认；否则用 /cancelbind 拒绝。\n"
                   f"十分钟内在当前 QQ 好友私聊发送：/bindtg {claim.code}\n"
                   "确认凭据请勿转发。此操作不共享管理员权限。")
        confirmed = False
        protect_qq_account_send_logs()
        privacy = _private_qq_send.set(True)
        try:
            result = await asyncio.wait_for(selected.call_api("send_private_msg", user_id=int(qq_user), message=message, auto_escape=True), 15)
            confirmed = _receipt_confirmed(result)
        except asyncio.CancelledError:
            service.mark_delivery(claim.request_id, confirmed=False)
            raise
        except Exception:
            pass  # Unknown delivery is never replayed or treated as consent.
        finally:
            _private_qq_send.reset(privacy)
        marked = service.mark_delivery(claim.request_id, confirmed=confirmed)
        if confirmed and marked:
            return "已向指定 QQ 好友私聊投递确认请求。请本人在 QQ 侧十分钟内确认；当前仍未建立绑定。\n查询：/bindstatus；取消：/cancelbind"
        return "QQ 确认请求尚未确认送达，当前未建立绑定，凭据已撤销。请至少一分钟后重新 /bindqq。"
    except AccountLinkError as error:
        return _ERRORS.get(error.code, "账号绑定暂时不可用，请稍后重试。")


def register_account_link_commands(*, plugin_config: Any, get_bots: Callable[[], dict[str, Any]], service: Any = None) -> dict[str, Any]:
    from nonebot import on, on_message
    from nonebot.rule import Rule
    protect_account_command_logs()

    async def rule(bot: Bot, event: Event) -> bool:
        return has_confirmation_command(event) or parse_command(event, bot) is not None

    matcher = on_message(rule=Rule(rule), priority=1, block=True)

    @matcher.handle()
    async def handle(bot: Bot, event: Event) -> None:
        response = await handle_account_link_command(bot, event, get_bots=get_bots, service=service)
        if response is not None:
            await matcher.finish(response)

    # Edited Telegram credentials cannot be archived or executed.
    edited = on(type="edit_message", rule=Rule(rule), priority=1, block=True)
    return {"account_link_commands": matcher, "account_link_edited_guard": edited}
