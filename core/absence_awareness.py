from __future__ import annotations

import time
from typing import Optional

from .db import connect_sync
from .prompt_hooks import HookContext, register_prompt_hook

# 缓存群聊最后发言时间，避免高频打库: group_id -> (cache_time, last_reply_ts)
_LAST_REPLY_CACHE: dict[str, tuple[float, float]] = {}
_CACHE_TTL: float = 5.0


def _get_bot_last_reply_ts(group_id: str) -> float:
    """获取 Bot 在指定群聊中的最后发言时间戳（带 5 秒 TTL 缓存）。"""
    if not group_id:
        return 0.0

    now = time.time()
    cached = _LAST_REPLY_CACHE.get(group_id)
    if cached is not None:
        cache_time, result_ts = cached
        if now - cache_time < _CACHE_TTL:
            return result_ts

    result_ts = 0.0
    try:
        with connect_sync() as conn:
            row = conn.execute(
                "SELECT MAX(timestamp) FROM group_messages WHERE group_id=? AND is_bot=1",
                (group_id,),
            ).fetchone()
            if row and row[0] is not None:
                result_ts = float(row[0])
    except Exception:
        result_ts = 0.0

    _LAST_REPLY_CACHE[group_id] = (now, result_ts)
    return result_ts


def _format_absence_duration(seconds: float) -> str:
    """将离场秒数转为自然语言中文描述。"""
    if seconds < 300:
        return ""
    if seconds < 600:
        return "几分钟"
    if seconds < 1800:
        return "十几分钟"
    if seconds < 3600:
        return "半小时左右"
    if seconds < 7200:
        return "一个多小时"
    if seconds < 10800:
        return "两三个小时"
    if seconds < 43200:
        return "好几个小时"
    if seconds < 64800:
        return "大半天"
    if seconds < 86400:
        return "将近一天"
    if seconds < 172800:
        return "一两天"
    if seconds < 259200:
        return "两三天"
    return "好几天"


async def _absence_awareness_hook(ctx: HookContext) -> Optional[str]:
    """离场感知 Hook：根据 Bot 离开群聊的时长，在 Prompt 中注入在场状态感知提示。"""
    if ctx.is_private or not ctx.group_id:
        return None

    last_ts = _get_bot_last_reply_ts(ctx.group_id)
    if last_ts <= 0.0:
        return (
            "## 在场状态感知\n"
            "- 你似乎还没有在这个群里说过话，这是你第一次在这里发言。"
        )

    absence_seconds = time.time() - last_ts
    if absence_seconds < 0:
        return None

    duration_text = _format_absence_duration(absence_seconds)
    if not duration_text:
        return None

    if absence_seconds < 1800:
        return (
            "## 在场状态感知\n"
            f"- 你在这个群已经 {duration_text} 没有说话了。\n"
            '- 如果有人说你"活了"、"冒泡了"之类的，顺着接就好，不用特意解释。'
        )

    if absence_seconds < 10800:
        return (
            "## 在场状态感知\n"
            f"- 你在这个群已经 {duration_text} 没有说话了。\n"
            '- 你并不是一直在看群消息。如果有人提到你"终于出现了"、"活了"、"回来了"等，\n'
            "  自然地回应就好，不要否认自己离开过。\n"
            "- 不要每次都主动解释去哪了，有时候直接接话更自然。"
        )

    return (
        "## 在场状态感知\n"
        f"- 你已经 {duration_text} 没有在这个群里说话了。\n"
        "- 你不知道你离开期间群里聊了什么，不要假装自己一直在看。\n"
        '- 如果群友对你的出现表示惊讶（如"活了"、"诈尸了"、"终于冒泡"），\n'
        "  坦然回应，可以自然地表达刚看到消息的感觉。\n"
        "- 先观察当前话题再接话，不要突兀地插入无关内容。"
    )


def register_absence_awareness_hook() -> None:
    """注册离场感知 Prompt 钩子。"""
    register_prompt_hook(
        "absence_awareness",
        _absence_awareness_hook,
        priority=23,
        phase="system_context",
    )
