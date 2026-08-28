from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Dict, List, Optional

from nonebot import logger

from .context_policy import sanitize_message_content, stringify_history_content
from .db import connect_sync
from .group_context import render_group_context_structured
from .memory_defaults import (
    DEFAULT_COMPRESS_KEEP_RECENT,
    DEFAULT_COMPRESS_THRESHOLD,
    DEFAULT_GROUP_CONTEXT_EXPIRE_HOURS,
    DEFAULT_HISTORY_LEN,
    DEFAULT_MESSAGE_EXPIRE_HOURS,
)
from .memory_store import get_memory_store

SESSION_HISTORY_LIMIT = DEFAULT_HISTORY_LEN
GROUP_SESSION_PREFIX = "group_"
PRIVATE_SESSION_PREFIX = "private_"

_plugin_config: Any = None
_compress_tool_caller: Any = None
_compress_tasks: Dict[str, asyncio.Task[None]] = {}
_compress_failures: Dict[str, int] = {}
_compress_retry_tasks: Dict[str, asyncio.Task[None]] = {}
_COMPRESS_RETRY_DELAYS = (30.0, 120.0, 600.0)
_compress_sleep: Any = asyncio.sleep

# 向后兼容导出，历史已不再保存在内存 dict 中。
chat_histories: Dict[str, List[Dict[str, Any]]] = {}


def init_session_store(plugin_config: Any, compress_tool_caller: Any = None) -> None:
    global _plugin_config, _compress_tool_caller
    _plugin_config = plugin_config
    _compress_tool_caller = compress_tool_caller


def load_session_histories() -> Dict[str, List[Dict[str, Any]]]:
    with connect_sync() as conn:
        rows = conn.execute("SELECT DISTINCT session_id FROM session_messages").fetchall()
    return {
        str(row["session_id"]): get_session_messages(str(row["session_id"]))
        for row in rows
    }


def _get_compress_threshold() -> int:
    if _plugin_config is not None:
        return int(getattr(_plugin_config, "personification_compress_threshold", DEFAULT_COMPRESS_THRESHOLD))
    return DEFAULT_COMPRESS_THRESHOLD


def _get_keep_recent() -> int:
    if _plugin_config is not None:
        return int(getattr(_plugin_config, "personification_compress_keep_recent", DEFAULT_COMPRESS_KEEP_RECENT))
    return DEFAULT_COMPRESS_KEEP_RECENT


def _get_history_max_len() -> int:
    if _plugin_config is not None:
        value = int(getattr(_plugin_config, "personification_history_len", DEFAULT_HISTORY_LEN))
        return max(40, min(value, 800))
    return DEFAULT_HISTORY_LEN


def _get_compress_trigger() -> int:
    """Start compaction at the earlier configured threshold/target bound."""
    return min(_get_compress_threshold(), _get_history_max_len())


def _get_message_expire_hours() -> float:
    if _plugin_config is not None:
        value = getattr(_plugin_config, "personification_message_expire_hours", DEFAULT_MESSAGE_EXPIRE_HOURS)
        try:
            return max(0.0, float(value))
        except (TypeError, ValueError):
            return DEFAULT_MESSAGE_EXPIRE_HOURS
    return DEFAULT_MESSAGE_EXPIRE_HOURS


def _get_group_message_expire_hours() -> float:
    if _plugin_config is not None:
        raw = getattr(_plugin_config, "personification_group_context_expire_hours", DEFAULT_GROUP_CONTEXT_EXPIRE_HOURS)
        try:
            return max(0.0, float(raw))
        except (TypeError, ValueError):
            return DEFAULT_GROUP_CONTEXT_EXPIRE_HOURS
    return DEFAULT_GROUP_CONTEXT_EXPIRE_HOURS


def build_group_session_id(group_id: str) -> str:
    return f"{GROUP_SESSION_PREFIX}{group_id}"


def build_private_session_id(user_id: str) -> str:
    return f"{PRIVATE_SESSION_PREFIX}{user_id}"


def is_private_session_id(session_id: str) -> bool:
    return session_id.startswith(PRIVATE_SESSION_PREFIX)


def ensure_session_history(session_id: str, legacy_session_id: Optional[str] = None) -> List[Dict]:
    if legacy_session_id and legacy_session_id != session_id:
        with connect_sync() as conn:
            conn.execute(
                "UPDATE session_messages SET session_id=? WHERE session_id=?",
                (session_id, legacy_session_id),
            )
            conn.commit()
    return get_session_messages(session_id)


def get_session_history_limit(session_id: str) -> int:
    _ = session_id
    return _get_history_max_len()


def trim_session_history(session_id: str, legacy_session_id: Optional[str] = None) -> List[Dict]:
    return ensure_session_history(session_id, legacy_session_id=legacy_session_id)


def _is_summary_message(msg: Dict) -> bool:
    return bool(msg.get("is_summary")) or (
        msg.get("role") == "system" and str(msg.get("content", "")).startswith("【对话历史摘要】")
    )


def _dedupe_summary_messages(messages: List[Dict]) -> List[Dict]:
    summaries = [msg for msg in messages if _is_summary_message(msg)]
    non_summaries = [msg for msg in messages if not _is_summary_message(msg)]
    if not summaries:
        return non_summaries
    return [summaries[-1]] + non_summaries


def _filter_expired_messages(
    messages: List[Dict],
    expire_hours: float,
    *,
    keep_recent: bool = True,
    keep_summary: bool = True,
) -> List[Dict]:
    if expire_hours <= 0:
        return messages
    now_ts = time.time()
    expire_seconds = expire_hours * 3600
    keep_recent_count = _get_keep_recent() if keep_recent else 0
    result: List[Dict] = []
    recent_indices = set(range(max(0, len(messages) - keep_recent_count), len(messages)))
    for idx, msg in enumerate(messages):
        if _is_summary_message(msg):
            if keep_summary:
                result.append(msg)
            continue
        if idx in recent_indices:
            result.append(msg)
            continue
        msg_ts = float(msg.get("timestamp", 0) or 0)
        if not msg_ts or now_ts - msg_ts <= expire_seconds:
            result.append(msg)
    return result


def _deserialize_session_row(row: Any) -> Dict[str, Any]:
    try:
        content = json.loads(row["content"])
    except Exception:
        content = row["content"]
    try:
        metadata = json.loads(row["metadata"] or "{}")
    except Exception:
        metadata = {}
    if not isinstance(metadata, dict):
        metadata = {}
    return {
        "id": row["id"],
        "role": row["role"],
        "content": content,
        "timestamp": float(row["timestamp"] or 0),
        "is_summary": bool(row["is_summary"]),
        **metadata,
    }


def _fetch_session_messages_sync(session_id: str) -> List[Dict[str, Any]]:
    with connect_sync() as conn:
        rows = conn.execute(
            """
            SELECT id, role, content, is_summary, timestamp, metadata
            FROM session_messages
            WHERE session_id=?
            ORDER BY timestamp ASC, id ASC
            """,
            (session_id,),
        ).fetchall()
    return [_deserialize_session_row(row) for row in rows]


def get_session_messages(session_id: str, legacy_session_id: Optional[str] = None) -> List[Dict]:
    if legacy_session_id and legacy_session_id != session_id:
        ensure_session_history(session_id, legacy_session_id=legacy_session_id)
    raw_messages = _fetch_session_messages_sync(session_id)
    if is_private_session_id(session_id):
        expire_hours = _get_message_expire_hours()
        if expire_hours <= 0:
            return raw_messages
        return _filter_expired_messages(raw_messages, expire_hours)

    expire_hours = _get_group_message_expire_hours()
    if expire_hours <= 0:
        return _dedupe_summary_messages(raw_messages)
    filtered = _filter_expired_messages(raw_messages, expire_hours, keep_recent=True, keep_summary=True)
    return _dedupe_summary_messages(filtered)


def _render_structured_speakers(messages: List[Dict[str, Any]]) -> str:
    structured = render_group_context_structured(messages)
    if structured:
        return structured

    lines: List[str] = []
    for msg in messages:
        speaker = str(msg.get("speaker") or msg.get("role") or "未知")
        content = stringify_history_content(msg.get("content", ""))[:120]
        if not content:
            continue
        lines.append(f"[对话][{speaker}|uid={msg.get('user_id', '') or ''}|普通发言] {content}")
    return "\n".join(lines)


def _build_compress_prompt(messages: List[Dict]) -> str:
    return (
        "以下是一段聊天历史，请压缩为背景摘要（150字以内）。\n"
        "如果是群聊，必须保留：谁提出了什么请求 谁回应了谁 当前结论 Bot是否已介入。\n"
        "用昵称区分不同发言人，不要丢失回复、提及和对话指向关系。\n"
        "只输出摘要文本，不要任何前缀。\n\n"
        f"{_render_structured_speakers(messages)}"
    )


def _replace_history_with_summary_sync(
    session_id: str,
    summary_text: str,
    *,
    candidate_ids: list[int],
    cutoff_id: int,
    keep_boundary_timestamp: float,
) -> bool:
    """Commit a summary only when the original snapshot still exists.

    The summary call runs outside SQLite.  This short IMMEDIATE transaction
    therefore cannot delete arrivals made after the snapshot or resurrect a
    session an administrator cleared while the model was running.
    """
    if not candidate_ids:
        return False
    with connect_sync() as conn:
        conn.execute("BEGIN IMMEDIATE")
        placeholders = ",".join("?" for _ in candidate_ids)
        rows = conn.execute(
            f"SELECT id,is_summary,metadata FROM session_messages WHERE session_id=? AND id IN ({placeholders})",
            (session_id, *candidate_ids),
        ).fetchall()
        if {int(row["id"]) for row in rows} != set(candidate_ids):
            conn.rollback()
            return False
        snapshot_summary_ids = {int(row["id"]) for row in rows if bool(row["is_summary"])}
        current_summary_ids = {int(row["id"]) for row in conn.execute("SELECT id FROM session_messages WHERE session_id=? AND is_summary=1", (session_id,)).fetchall()}
        if current_summary_ids != snapshot_summary_ids:
            conn.rollback()
            return False
        previous_covered = 0
        previous_cutoff = 0
        for row in rows:
            if not bool(row["is_summary"]):
                continue
            try:
                meta = json.loads(row["metadata"] or "{}")
                previous_covered += int(meta.get("covered_count", 0) or 0)
                previous_cutoff = max(previous_cutoff, int(meta.get("compacted_through_id", 0) or 0))
            except Exception:
                pass
        conn.execute(
            f"DELETE FROM session_messages WHERE session_id=? AND id IN ({placeholders})",
            (session_id, *candidate_ids),
        )
        conn.execute(
            """
            INSERT INTO session_messages(session_id, role, content, is_summary, timestamp, metadata)
            VALUES (?, ?, ?, 1, ?, ?)
            """,
            (
                session_id,
                "system",
                json.dumps(f"【对话历史摘要】{summary_text}", ensure_ascii=False),
                keep_boundary_timestamp - 0.000001,
                json.dumps({"compacted_through_id": max(previous_cutoff, cutoff_id), "covered_count": previous_covered + sum(not bool(row["is_summary"]) for row in rows), "summary_version": 2}, ensure_ascii=False),
            ),
        )
        conn.commit()
        return True


async def _run_compress(session_id: str) -> None:
    committed_successfully = False
    try:
        history = _fetch_session_messages_sync(session_id)
        threshold = _get_compress_trigger()
        # A successful summary counts as one history entry.  Preserve no more
        # raw rows than the configured post-summary target permits.
        keep = min(_get_keep_recent(), max(0, _get_history_max_len() - 1))
        if len(history) < threshold:
            return
        # Keep the newest *raw* conversation rows.  Old summaries always
        # participate, even if a legacy timestamp made one appear newer than
        # a raw row, otherwise a second compaction can leave multiple valid
        # summaries forever.
        raw_history = [item for item in history if not bool(item.get("is_summary"))]
        retained_raw_ids = {
            int(item["id"])
            for item in (raw_history[-keep:] if keep > 0 else [])
            if item.get("id") is not None
        }
        to_compress = [
            item for item in history
            if bool(item.get("is_summary")) or int(item.get("id") or 0) not in retained_raw_ids
        ]
        if not to_compress:
            return

        summary_text = ""
        caller = _compress_tool_caller
        if caller is not None:
            try:
                prompt = _build_compress_prompt(to_compress)
                timeout_seconds = float(getattr(_plugin_config, "personification_compress_timeout_seconds", 30.0) if _plugin_config is not None else 30.0)
                response = await asyncio.wait_for(
                    caller.chat_with_tools(
                        messages=[{"role": "user", "content": prompt}],
                        tools=[],
                        use_builtin_search=False,
                    ),
                    timeout=max(0.1, timeout_seconds),
                )
                summary_text = str(response.content or "").strip()
            except Exception as exc:
                logger.warning(f"[session_store] compress LLM call failed for {session_id}: {exc}")
        if not summary_text:
            failures = _compress_failures.get(session_id, 0) + 1
            _compress_failures[session_id] = failures
            logger.warning("[session_store] compress_empty_or_failed code=session_compress_summary_unavailable attempt=%s", failures)
            _schedule_compress_retry(session_id, failures)
            return
        candidate_ids = [int(item["id"]) for item in to_compress if item.get("id") is not None]
        committed = await asyncio.to_thread(
            _replace_history_with_summary_sync,
            session_id,
            summary_text,
            candidate_ids=candidate_ids,
            cutoff_id=max((int(item["id"]) for item in to_compress if not item.get("is_summary") and item.get("id") is not None), default=0),
            keep_boundary_timestamp=(
                float(next(item["timestamp"] for item in raw_history if int(item.get("id") or 0) in retained_raw_ids))
                if retained_raw_ids
                else float(max((item["timestamp"] for item in to_compress if not bool(item.get("is_summary"))), default=time.time()))
            ),
        )
        if committed:
            _compress_failures.pop(session_id, None)
            committed_successfully = True
        else:
            logger.warning("[session_store] compress conditional commit abandoned code=session_compress_snapshot_conflict")
    except Exception as exc:
        logger.warning(f"[session_store] compress error for {session_id}: {exc}")
        failures = _compress_failures.get(session_id, 0) + 1
        _compress_failures[session_id] = failures
        _schedule_compress_retry(session_id, failures)
    finally:
        if _compress_tasks.get(session_id) is asyncio.current_task():
            _compress_tasks.pop(session_id, None)
            # A concurrent append is never deleted by this commit.  Schedule
            # another bounded pass only if it left the post-summary target
            # strictly exceeded; equality must not create a loop.
            if committed_successfully and len(_fetch_session_messages_sync(session_id)) > _get_history_max_len():
                _schedule_compress(session_id)


def _schedule_compress(session_id: str) -> None:
    if session_id in _compress_tasks:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    task = loop.create_task(_run_compress(session_id))
    _compress_tasks[session_id] = task


def _schedule_compress_retry(session_id: str, attempt: int) -> None:
    if attempt > len(_COMPRESS_RETRY_DELAYS) or session_id in _compress_retry_tasks:
        return
    async def _retry() -> None:
        try:
            await _compress_sleep(_COMPRESS_RETRY_DELAYS[attempt - 1])
            _schedule_compress(session_id)
        finally:
            if _compress_retry_tasks.get(session_id) is asyncio.current_task():
                _compress_retry_tasks.pop(session_id, None)
    try:
        _compress_retry_tasks[session_id] = asyncio.get_running_loop().create_task(_retry())
    except RuntimeError:
        return


def append_session_message(
    session_id: str,
    role: str,
    content: Any,
    legacy_session_id: Optional[str] = None,
    **metadata: Any,
) -> List[Dict]:
    if legacy_session_id and legacy_session_id != session_id:
        ensure_session_history(session_id, legacy_session_id=legacy_session_id)

    sanitized_content = sanitize_message_content(content)
    timestamp = time.time()
    safe_metadata = {key: value for key, value in metadata.items() if value is not None}
    with connect_sync() as conn:
        conn.execute(
            """
            INSERT INTO session_messages(session_id, role, content, is_summary, timestamp, metadata)
            VALUES (?, ?, ?, 0, ?, ?)
            """,
            (
                session_id,
                role,
                json.dumps(sanitized_content, ensure_ascii=False),
                timestamp,
                json.dumps(safe_metadata, ensure_ascii=False),
            ),
        )
        # The configured history length is a post-summary target.  Deleting
        # before a summary commits loses concurrent conversation permanently.
        conn.commit()

    try:
        memory_store = get_memory_store()
        if session_id.startswith(GROUP_SESSION_PREFIX):
            group_id = session_id.removeprefix(GROUP_SESSION_PREFIX)
            memory_store.append_group_message(
                group_id=group_id,
                role=role,
                content=sanitized_content,
                metadata=safe_metadata,
            )
        elif legacy_session_id and not str(legacy_session_id).startswith(PRIVATE_SESSION_PREFIX):
            memory_store.append_group_message(
                group_id=str(legacy_session_id),
                role=role,
                content=sanitized_content,
                metadata=safe_metadata,
            )
    except Exception:
        pass

    # After all bounded automatic retries were exhausted, a *new* inbound
    # message starts a fresh 30/120/600 retry cycle.  No timer is resurrected
    # merely because the previous cycle failed.
    if _compress_failures.get(session_id, 0) > len(_COMPRESS_RETRY_DELAYS):
        _compress_failures.pop(session_id, None)
    current = get_session_messages(session_id)
    if len(current) >= _get_compress_trigger() and session_id not in _compress_retry_tasks:
        _schedule_compress(session_id)
    return current


def save_session_histories() -> None:
    return None


async def clear_all_session_histories() -> int:
    tasks = list(_compress_tasks.values())
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    _compress_tasks.clear()
    retry_tasks = list(_compress_retry_tasks.values())
    for task in retry_tasks:
        task.cancel()
    if retry_tasks:
        await asyncio.gather(*retry_tasks, return_exceptions=True)
    _compress_retry_tasks.clear()
    _compress_failures.clear()
    with connect_sync() as conn:
        row = conn.execute("SELECT COUNT(DISTINCT session_id) AS cnt FROM session_messages").fetchone()
        count = int(row["cnt"] if row else 0)
        conn.execute("DELETE FROM session_messages")
        conn.commit()
    return count


def clear_session_history(session_id: str, legacy_session_id: Optional[str] = None) -> int:
    task = _compress_tasks.pop(session_id, None)
    if task is not None:
        task.cancel()
    retry = _compress_retry_tasks.pop(session_id, None)
    if retry is not None:
        retry.cancel()
    _compress_failures.pop(session_id, None)
    if legacy_session_id:
        _compress_failures.pop(legacy_session_id, None)
    if legacy_session_id and legacy_session_id in _compress_tasks:
        leg_task = _compress_tasks.pop(legacy_session_id, None)
        if leg_task is not None:
            leg_task.cancel()
    with connect_sync() as conn:
        if legacy_session_id and legacy_session_id != session_id:
            cursor = conn.execute(
                "DELETE FROM session_messages WHERE session_id IN (?, ?)",
                (session_id, legacy_session_id),
            )
        else:
            cursor = conn.execute(
                "DELETE FROM session_messages WHERE session_id=?",
                (session_id,),
            )
        conn.commit()
        return int(cursor.rowcount if cursor is not None else 0)
