from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Callable


async def refresh_qzone_cookie_on_available_bot(
    *,
    enabled: bool,
    get_bots: Callable[[], dict[str, Any]],
    update_qzone_cookie: Callable[[Any], Awaitable[tuple[bool, str]]],
    logger: Any,
    wait_seconds: float = 60.0,
    poll_interval: float = 2.0,
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
) -> bool:
    """Refresh every connected Bot once after startup without cross-Bot reuse."""
    if not enabled:
        return False

    deadline = time.monotonic() + max(0.0, float(wait_seconds or 0.0))
    bot_items: list[tuple[str, Any]] = []
    while True:
        try:
            bots = get_bots() or {}
        except Exception as exc:
            bots = {}
            logger.warning(
                "拟人插件：启动时读取 Bot 实例失败，暂缓刷新 Qzone Cookie："
                f"{type(exc).__name__}"
            )
        if bots:
            seen_ids: set[str] = set()
            for key, bot in bots.items():
                bot_id = str(getattr(bot, "self_id", key) or key).strip()
                if not bot_id or bot_id in seen_ids:
                    continue
                seen_ids.add(bot_id)
                bot_items.append((bot_id, bot))
            if not bot_items:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    logger.warning("拟人插件：启动后未找到有效 Bot 实例，跳过 Qzone Cookie 自动刷新。")
                    return False
                await sleep(min(max(0.1, float(poll_interval or 0.1)), remaining))
                continue
            break
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            logger.warning("拟人插件：启动后未找到有效 Bot 实例，跳过 Qzone Cookie 自动刷新。")
            return False
        await sleep(min(max(0.1, float(poll_interval or 0.1)), remaining))

    connected_bot_ids = tuple(item[0] for item in bot_items)

    async def _refresh_one(bot_id: str, bot: Any) -> tuple[str, bool, str]:
        try:
            ok, message = await update_qzone_cookie(
                bot,
                connected_bot_ids=connected_bot_ids,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return bot_id, False, f"qzone_startup_refresh_exception_{type(exc).__name__}"
        return bot_id, bool(ok), str(message or "qzone_startup_refresh_failed")[:96]

    results = await asyncio.gather(
        *(_refresh_one(bot_id, bot) for bot_id, bot in bot_items),
        return_exceptions=False,
    )
    successes = [bot_id for bot_id, ok, _message in results if ok]
    failures = [bot_id for bot_id, ok, _message in results if not ok]
    if successes:
        logger.info(
            "拟人插件：启动时 Qzone Cookie 已完成 Bot 隔离刷新，"
            f"成功 {len(successes)}/{len(bot_items)}。"
        )
    if failures:
        logger.warning(
            "拟人插件：启动时部分 Qzone Cookie 刷新未完成，"
            f"失败 {len(failures)}/{len(bot_items)}。"
        )
    return bool(successes)
