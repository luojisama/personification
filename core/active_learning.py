from __future__ import annotations

import asyncio
import json
import logging
import re
import sqlite3
import time
from typing import Any

from .generation_fence import assert_current_generation

_ACTIVE_LEARNING_DAILY_QUOTA = 5
_LEARNING_DIAGNOSTIC_TYPES: set[str] = set()


def _diagnose(exc: Exception) -> None:
    error_type = type(exc).__name__
    if error_type not in _LEARNING_DIAGNOSTIC_TYPES:
        _LEARNING_DIAGNOSTIC_TYPES.add(error_type)
        logging.getLogger(__name__).warning(
            "[learning] active learning unavailable error_type=%s", error_type,
        )
_ACTIVE_LEARNING_PROMPT = (
    "你是事实学习器。基于用户提出的不确定性问题和已有工具证据，"
    "做一次低成本的深度查证。用你的内置知识做一次简短研究，输出 JSON："
    '{"learned_fact":"150字内的事实陈述","confidence":0.0,"sources_note":"来源说明"}\n'
    "只输出 JSON，不要 markdown。如果实在无法确认，把 learned_fact 设为空字符串。"
)


async def run_active_learning(
    *,
    tool_caller: Any,
    memory_store: Any,
    uncertainty_notes: list[str],
    group_id: str,
    research_followup_query: str,
    plugin_config: Any,
) -> bool:
    if not tool_caller or not memory_store or not uncertainty_notes:
        return False
    if not bool(getattr(plugin_config, "personification_active_learning_enabled", False)):
        return False

    daily_quota = int(getattr(plugin_config, "personification_active_learning_daily_quota", _ACTIVE_LEARNING_DAILY_QUOTA) or _ACTIVE_LEARNING_DAILY_QUOTA)
    today = time.strftime("%Y-%m-%d")
    used_key = f"active_learning_quota_{group_id}_{today}"
    try:
        used = _read_quota(memory_store, used_key)
    except (sqlite3.Error, OSError) as exc:
        _diagnose(exc)
        return False
    if used >= daily_quota:
        return False

    query = str(research_followup_query or "").strip() or "；".join(uncertainty_notes[:3])
    assert_current_generation()
    try:
        response = await asyncio.wait_for(
            tool_caller.chat_with_tools(
                messages=[
                    {"role": "system", "content": _ACTIVE_LEARNING_PROMPT},
                    {"role": "user", "content": f"不确定问题：{'；'.join(uncertainty_notes[:5])}\n研究查询：{query[:300]}"},
                ],
                tools=[],
                use_builtin_search=False,
            ),
            timeout=15.0,
        )
    except Exception as exc:
        if isinstance(exc, (TimeoutError, ConnectionError, sqlite3.Error, OSError)) or getattr(exc, "status_code", None) is not None:
            _diagnose(exc)
            return False
        raise

    content = str(getattr(response, "content", "") or "").strip()
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", content, flags=re.DOTALL)
        if not match:
            return False
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return False

    if not isinstance(data, dict):
        return False
    fact = str(data.get("learned_fact", "") or "").strip()
    if not fact:
        return False

    confidence = max(0.0, min(1.0, float(data.get("confidence", 0.5) or 0.5)))
    assert_current_generation()
    try:
        _write_quota(memory_store, used_key, used + 1)
        assert_current_generation()
        memory_store.write_memory_item({
            "memory_id": f"lf_{group_id}_{int(time.time())}",
            "memory_type": "learned_fact",
            "summary": fact[:200],
            "group_id": str(group_id),
            "source_kind": "active_learning",
            "confidence": confidence,
            "salience": 0.4,
        })
    except (sqlite3.Error, OSError) as exc:
        _diagnose(exc)
        return False
    return True


def _read_quota(memory_store: Any, key: str) -> int:
    item = memory_store.get_memory_item(key)
    if item and isinstance(item, dict):
        return max(0, int(item.get("counter", 0) or 0))
    if item is None:
        return 0
    raise ValueError("invalid active learning quota record")


def _write_quota(memory_store: Any, key: str, value: int) -> None:
    memory_store.write_memory_item({
            "memory_id": key,
            "memory_type": "quota_tracker",
            "summary": f"active_learning_quota:{value}",
            "counter": value,
            "confidence": 1.0,
            "salience": 0.0,
    })


__all__ = ["run_active_learning"]
