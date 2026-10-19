from __future__ import annotations

from typing import Any, Callable, Iterable
from contextvars import ContextVar

from .telegram_connections import native_telegram
from .qq_interaction import native_qq
from .webui_admin_identity import is_qq_identity, telegram_identity_id


_private_telegram_notification: ContextVar[bool] = ContextVar("private_telegram_admin_notification", default=False)


def _protect_telegram_notification_logs() -> None:
    # b20 logs the complete API data at DEBUG. Keep this guard installed and
    # enable it only in the sending task; concurrent chat logs are unchanged.
    from nonebot.adapters.telegram import adapter
    from .telegram_media import protect_transport_logs

    protect_transport_logs()
    original = adapter.log
    if getattr(original, "_personification_private_guard", False):
        return

    def protected_log(*args: Any, **kwargs: Any) -> Any:
        if not _private_telegram_notification.get():
            return original(*args, **kwargs)
        return None

    protected_log._personification_private_guard = True  # type: ignore[attr-defined]
    adapter.log = protected_log


async def send_to_admin(get_bots: Callable[[], dict[str, Any]], qq: str, message: str) -> bool:
    """通过对应平台 bot 给指定管理员发私聊。
    True 表示发送已确认；Telegram 未确认时返回 False，禁止转其他 Bot 重放。
    """
    target = str(qq or "").strip()
    if not target:
        return False
    telegram_id = telegram_identity_id(target)
    if telegram_id is None and not is_qq_identity(target):
        return False
    try:
        bots = get_bots() or {}
    except Exception:
        bots = {}
    for bot in list(bots.values()):
        if native_qq(bot):
            continue
        is_telegram = native_telegram(bot)
        if is_telegram != (telegram_id is not None):
            continue
        try:
            if is_telegram:
                from nonebot.adapters.telegram import Bot

                if not isinstance(bot, Bot):
                    continue
                _protect_telegram_notification_logs()
                privacy = _private_telegram_notification.set(True)
                try:
                    result = await bot.send_message(chat_id=telegram_id, text=str(message or ""))
                finally:
                    _private_telegram_notification.reset(privacy)
                message_id = result.get("message_id") if isinstance(result, dict) else getattr(result, "message_id", None)
                # Do not turn an unconfirmed/empty response into a login grant.
                if isinstance(message_id, int) and not isinstance(message_id, bool) and message_id > 0:
                    return True
                return False
            await bot.call_api("send_private_msg", user_id=int(target), message=str(message or ""))
            return True
        except Exception:
            if is_telegram:
                # Unknown delivery must not replay a sensitive notification
                # through a second bot, and never falls back to QQ.
                return False
            continue
    return False


async def startup_notify_admins(
    *,
    get_bots: Callable[[], dict[str, Any]],
    superusers: Iterable[str],
    plugin_admins: Iterable[str],
    message: str,
) -> int:
    """启动后向所有 superuser 与 plugin_admin 推一条通知，返回成功数。"""
    seen: set[str] = set()
    targets: list[str] = []
    for source in (superusers or [], plugin_admins or []):
        for item in source:
            cleaned = str(item or "").strip()
            if cleaned and cleaned not in seen:
                seen.add(cleaned)
                targets.append(cleaned)
    success = 0
    for qq in targets:
        if await send_to_admin(get_bots, qq, message):
            success += 1
    return success


__all__ = ["send_to_admin", "startup_notify_admins"]
