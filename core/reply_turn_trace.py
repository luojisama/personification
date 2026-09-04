from __future__ import annotations

import contextvars
import json
import math
import re
import time
import uuid
from typing import Any

from .db import connect_sync
from .plugin_runtime_logs import sanitize_text
from .sensitive_data import sanitize_object


_CURRENT_TRACE_ID: contextvars.ContextVar[str] = contextvars.ContextVar(
    "personification_reply_trace_id",
    default="",
)
_ELAPSED_RE = re.compile(r"(?:elapsed_ms=|耗时\s*)(\d{1,9})(?:\s*ms)?", re.I)
_SIGNAL_KEY_RE = re.compile(
    r"(?:^|\s)(action|reply_action|speech_act|output|intent|ambiguity|tool|arg_keys|result_len|evidence|media_routes|budget|suggested_steps|actual_steps|suggested_seconds|actual_seconds|topic_thread|topic_speaker|reply_to_bot|bot_in_thread|parallel_threads|participants|reason|source|flags|revision|chars|address_mode|quote|at|target|query|finish|silence|recommend_silence|emotion|bot_emotion|emotion_intensity|reply_shape|relationship_progress|relationship_progress_confidence|conversation_scenario|scenario|media_only|media_grounding|available_evidence_fields|grounded_evidence_fields|grounded_anchor_count|recovery_method|media_delivery)=([^\s]+)"
)
_TRACE_TRUNCATION_KEY = "trace_truncated"
_CRITICAL_STAGE_KEYS = frozenset({"incoming_message", "outgoing_message"})
_MAX_STAGES = 80
_SEMANTIC_ENUMS: dict[str, frozenset[str]] = {
    "action": frozenset({"reply", "silence", "ask_clarify"}),
    "intent": frozenset(
        {"banter", "explanation", "lookup", "plugin_question", "image_generation", "expression"}
    ),
    "ambiguity": frozenset({"low", "medium", "high"}),
    "speech_act": frozenset(
        {"participate", "answer", "ask_followup", "clarify", "tease", "execute_action", "source_summary", "silence"}
    ),
    "output": frozenset(
        {"chat_short", "chat_answer", "structured_help", "source_summary", "qzone_reply"}
    ),
    "reply_shape": frozenset({"auto", "micro", "fragment", "sentence", "compact"}),
    "relationship_progress": frozenset({"none", "meaningful", "resonant", "milestone"}),
    "conversation_scenario": frozenset(
        {"normal", "casual_banter", "sarcasm_irony", "argument", "inside_joke", "multi_thread", "private_topic"}
    ),
    "address_mode": frozenset({"auto", "none", "at", "quote", "at_quote"}),
    "emotion_intensity": frozenset({"low", "medium", "high"}),
}
_SAFE_TOOL_KEY_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]{0,39}\Z")
_SAFE_TOOL_RESULT_TOKEN_RE = re.compile(r"[A-Za-z0-9_.:,@|=\-]{1,180}\Z")


def _trace_truncation_marker(*, discarded_count: int, clipped_fields: bool) -> dict[str, Any]:
    """Return a content-free marker for bounded Trace persistence."""

    discarded = max(0, int(discarded_count or 0))
    return {
        "key": _TRACE_TRUNCATION_KEY,
        "label": "Trace 记录截断",
        "status": "warn",
        "detail": f"discarded_count={discarded} clipped_fields={str(bool(clipped_fields)).lower()}",
        "discarded_count": discarded,
        "clipped_fields": bool(clipped_fields),
    }


def _split_truncation_marker(values: list[Any]) -> tuple[list[Any], int, bool]:
    items: list[Any] = []
    discarded_count = 0
    clipped_fields = False
    for item in values:
        if isinstance(item, dict) and str(item.get("key") or "") == _TRACE_TRUNCATION_KEY:
            try:
                discarded_count += max(0, int(item.get("discarded_count") or 0))
            except (TypeError, ValueError):
                pass
            clipped_fields = clipped_fields or bool(item.get("clipped_fields"))
            continue
        items.append(item)
    return items, discarded_count, clipped_fields


def new_trace_id() -> str:
    return uuid.uuid4().hex[:16]


def current_trace_id() -> str:
    return str(_CURRENT_TRACE_ID.get("") or "")


def set_current_trace_id(trace_id: str) -> contextvars.Token[str]:
    return _CURRENT_TRACE_ID.set(str(trace_id or ""))


def reset_current_trace_id(token: contextvars.Token[str]) -> None:
    _CURRENT_TRACE_ID.reset(token)


def _trace_identity(value: Any, *, limit: int) -> str:
    """Normalize optional identity fields without letting blank retries erase history."""

    return str(value or "").strip()[:limit]


def _clip_json_strings(value: Any, *, string_limit: int) -> Any:
    if isinstance(value, str):
        return value[: max(0, int(string_limit))]
    if isinstance(value, list):
        return [_clip_json_strings(item, string_limit=string_limit) for item in value]
    if isinstance(value, tuple):
        return [_clip_json_strings(item, string_limit=string_limit) for item in value]
    if isinstance(value, dict):
        return {
            str(key)[:128]: _clip_json_strings(item, string_limit=string_limit)
            for key, item in value.items()
        }
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if value is None or isinstance(value, (bool, int)):
        return value
    return sanitize_text(value)[: max(0, int(string_limit))]


def _safe_stage_list_json(values: list[Any], *, cap: int) -> str:
    original_items, previous_discarded, _previous_clipped = _split_truncation_marker(values)
    string_limit = min(1000, max(16, cap // 8))
    clipped_items = original_items
    while string_limit >= 16:
        clipped_items = _clip_json_strings(original_items, string_limit=string_limit)
        marker = _trace_truncation_marker(
            discarded_count=previous_discarded,
            clipped_fields=True,
        )
        rendered = json.dumps([marker, *clipped_items], ensure_ascii=False, separators=(",", ":"))
        if len(rendered) <= cap:
            return rendered
        string_limit //= 2

    candidates = list(enumerate(clipped_items))
    selected: set[int] = set()

    def _render(indices: set[int]) -> str:
        discarded = previous_discarded + len(candidates) - len(indices)
        marker = _trace_truncation_marker(
            discarded_count=discarded,
            clipped_fields=True,
        )
        ordered_items = [item for index, item in candidates if index in indices]
        return json.dumps([marker, *ordered_items], ensure_ascii=False, separators=(",", ":"))

    critical_indices = [
        index
        for index, item in candidates
        if isinstance(item, dict) and str(item.get("key") or "") in _CRITICAL_STAGE_KEYS
    ]
    recent_indices = [index for index, _item in reversed(candidates) if index not in critical_indices]
    for index in [*critical_indices, *recent_indices]:
        candidate = {*selected, index}
        if len(_render(candidate)) <= cap:
            selected = candidate
    rendered = _render(selected)
    if len(rendered) <= cap:
        return rendered
    # Only an unrealistically small custom cap can miss the compact marker.
    # Returning an empty list is still structurally valid and fail-closed.
    return "[]"


def _safe_json(value: Any, *, limit: int = 128000) -> str:
    """Serialize to valid JSON within a character budget.

    Slicing an already serialized string can corrupt its closing quote/bracket;
    the next stage append would then parse the whole trace as empty.  We instead
    clip values structurally and, only as a final fallback, keep a bounded set
    of complete list/dict entries.
    """

    cap = max(32, int(limit or 0))
    # Python's encoder accepts NaN/Infinity by default, while browser
    # JSON.parse correctly rejects them.  Normalize every scalar first so a
    # malformed model/legacy value cannot corrupt the Vue Trace endpoint.
    value = _clip_json_strings(value, string_limit=max(cap, 1_000_000))
    try:
        payload = json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )
    except Exception:
        payload = json.dumps({"value": sanitize_text(value)}, ensure_ascii=False, separators=(",", ":"))
    if len(payload) <= cap:
        return payload

    if isinstance(value, list):
        return _safe_stage_list_json(value, cap=cap)

    string_limit = min(1000, max(16, cap // 8))
    clipped: Any = value
    while string_limit >= 16:
        clipped = _clip_json_strings(value, string_limit=string_limit)
        payload = json.dumps(clipped, ensure_ascii=False, separators=(",", ":"))
        if len(payload) <= cap:
            return payload
        string_limit //= 2

    if isinstance(clipped, dict):
        kept_dict: dict[str, Any] = {}
        for key, item in clipped.items():
            candidate = {**kept_dict, str(key): item}
            rendered = json.dumps(candidate, ensure_ascii=False, separators=(",", ":"))
            if len(rendered) <= cap:
                kept_dict[str(key)] = item
        return json.dumps(kept_dict, ensure_ascii=False, separators=(",", ":"))

    fallback = json.dumps(_clip_json_strings(value, string_limit=max(1, cap // 4)), ensure_ascii=False)
    return fallback if len(fallback) <= cap else "null"


def _load_stages(conn: Any, trace_id: str) -> list[dict[str, Any]]:
    row = conn.execute(
        "SELECT stages FROM reply_turn_traces WHERE trace_id=?",
        (trace_id,),
    ).fetchone()
    if not row:
        return []
    try:
        loaded = json.loads(row["stages"] or "[]")
    except Exception:
        loaded = []
    return loaded if isinstance(loaded, list) else []


def _load_detail(conn: Any, trace_id: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT detail FROM reply_turn_traces WHERE trace_id=?",
        (trace_id,),
    ).fetchone()
    if not row:
        return {}
    try:
        loaded = json.loads(row["detail"] or "{}")
    except Exception:
        loaded = {}
    return loaded if isinstance(loaded, dict) else {}


def _merge_detail(
    existing: dict[str, Any] | None,
    incoming: dict[str, Any] | None,
) -> dict[str, Any]:
    merged = dict(existing or {})
    merged.update(dict(incoming or {}))
    sanitized = sanitize_object(merged)
    return sanitized if isinstance(sanitized, dict) else {}


def start_trace(
    *,
    trace_id: str = "",
    session_type: str = "",
    group_id: str = "",
    user_id: str = "",
    detail: dict[str, Any] | None = None,
) -> str:
    trace = str(trace_id or "").strip() or new_trace_id()
    now = time.time()
    effective_session_type = _trace_identity(session_type, limit=24)
    effective_group_id = _trace_identity(group_id, limit=32)
    effective_user_id = _trace_identity(user_id, limit=32)
    try:
        with connect_sync() as conn:
            existing_row = conn.execute(
                "SELECT session_type, group_id, user_id FROM reply_turn_traces WHERE trace_id=?",
                (trace,),
            ).fetchone()
            if existing_row:
                effective_session_type = effective_session_type or _trace_identity(
                    existing_row["session_type"], limit=24
                )
                effective_group_id = effective_group_id or _trace_identity(
                    existing_row["group_id"], limit=32
                )
                effective_user_id = effective_user_id or _trace_identity(
                    existing_row["user_id"], limit=32
                )
            payload = _merge_detail(_load_detail(conn, trace), detail)
            # ``ts`` remains the last-activity timestamp used for ordering;
            # preserve the actual start separately across repeated start calls.
            payload.setdefault("started_at", now)
            conn.execute(
                """
                INSERT INTO reply_turn_traces(
                    trace_id, ts, session_type, group_id, user_id, stages,
                    outcome, diagnosis_code, detail
                )
                VALUES (?, ?, ?, ?, ?, '[]', '', '', ?)
                ON CONFLICT(trace_id) DO UPDATE SET
                    ts=excluded.ts,
                    session_type=excluded.session_type,
                    group_id=excluded.group_id,
                    user_id=excluded.user_id,
                    detail=excluded.detail
                """,
                (
                    trace,
                    now,
                    effective_session_type,
                    effective_group_id,
                    effective_user_id,
                    _safe_json(payload, limit=4000),
                ),
            )
            conn.commit()
    except Exception:
        pass
    try:
        from .runtime_events import publish_runtime_event

        publish_runtime_event(
            "turn.started",
            trace_id=trace,
            payload={
                "session_type": effective_session_type,
                "group_id": effective_group_id,
                "user_id": effective_user_id,
            },
        )
    except Exception:
        pass
    return trace


def record_stage(
    *,
    trace_id: str = "",
    key: str,
    label: str = "",
    status: str = "info",
    detail: Any = "",
    hint: str = "",
    elapsed_ms: int | None = None,
) -> None:
    trace = str(trace_id or current_trace_id() or "").strip()
    if not trace:
        return
    stage = {
        "ts": time.time(),
        "key": str(key or "")[:64],
        "label": str(label or key or "")[:80],
        "status": str(status or "info")[:16],
        "detail": sanitize_text(detail)[:1000],
        "hint": sanitize_text(hint)[:500],
    }
    if elapsed_ms is not None:
        try:
            stage["elapsed_ms"] = max(0, int(elapsed_ms))
        except (TypeError, ValueError):
            pass
    try:
        with connect_sync() as conn:
            stages = _load_stages(conn, trace)
            previous_stages, previous_discarded, previous_clipped = _split_truncation_marker(stages)
            previous_stages.append(stage)
            if len(previous_stages) > _MAX_STAGES:
                indexed_stages = list(enumerate(previous_stages))
                critical_indices = [
                    index
                    for index, item in indexed_stages
                    if isinstance(item, dict)
                    and str(item.get("key") or "") in _CRITICAL_STAGE_KEYS
                ]
                selected_indices = set(critical_indices)
                for index, _item in reversed(indexed_stages):
                    if index in selected_indices:
                        continue
                    selected_indices.add(index)
                    if len(selected_indices) >= _MAX_STAGES:
                        break
                previous_discarded += len(previous_stages) - len(selected_indices)
                previous_stages = [
                    item for index, item in indexed_stages if index in selected_indices
                ]
            stages = previous_stages
            if previous_discarded or previous_clipped:
                stages = [
                    _trace_truncation_marker(
                        discarded_count=previous_discarded,
                        clipped_fields=previous_clipped,
                    ),
                    *stages,
                ]
            conn.execute(
                """
                UPDATE reply_turn_traces
                SET ts=?, stages=?
                WHERE trace_id=?
                """,
                (time.time(), _safe_json(stages), trace),
            )
            conn.commit()
    except Exception:
        pass
    try:
        from .runtime_events import publish_runtime_event

        publish_runtime_event(
            "turn.stage",
            trace_id=trace,
            payload={
                "key": stage["key"],
                "label": stage["label"],
                "status": stage["status"],
                "elapsed_ms": stage.get("elapsed_ms"),
            },
        )
    except Exception:
        pass


def finish_trace(
    *,
    trace_id: str = "",
    outcome: str,
    diagnosis_code: str = "",
    detail: dict[str, Any] | None = None,
) -> None:
    trace = str(trace_id or current_trace_id() or "").strip()
    if not trace:
        return
    now = time.time()
    try:
        with connect_sync() as conn:
            merged_detail = _merge_detail(_load_detail(conn, trace), detail)
            merged_detail["finished_at"] = now
            conn.execute(
                """
                UPDATE reply_turn_traces
                SET ts=?, outcome=?, diagnosis_code=?, detail=?
                WHERE trace_id=?
                """,
                (
                    now,
                    str(outcome or "")[:32],
                    str(diagnosis_code or "")[:64],
                    _safe_json(merged_detail, limit=4000),
                    trace,
                ),
            )
            conn.commit()
    except Exception:
        pass
    try:
        from .runtime_events import publish_runtime_event

        publish_runtime_event(
            "turn.finished",
            trace_id=trace,
            payload={
                "outcome": str(outcome or "")[:32],
                "diagnosis_code": str(diagnosis_code or "")[:64],
            },
        )
    except Exception:
        pass


def get_trace(trace_id: str) -> dict[str, Any] | None:
    trace = str(trace_id or "").strip()
    if not trace:
        return None
    with connect_sync() as conn:
        row = conn.execute(
            """
            SELECT trace_id, ts, session_type, group_id, user_id, stages,
                   outcome, diagnosis_code, detail
            FROM reply_turn_traces
            WHERE trace_id=?
            """,
            (trace,),
        ).fetchone()
    if not row:
        return None
    return _row_to_dict(row)


def query_recent(
    *,
    limit: int = 50,
    session_type: str = "",
    group_id: str = "",
    user_id: str = "",
) -> list[dict[str, Any]]:
    clauses = ["1=1"]
    params: list[Any] = []
    if session_type:
        clauses.append("session_type = ?")
        params.append(str(session_type)[:24])
    if group_id:
        clauses.append("group_id = ?")
        params.append(str(group_id)[:32])
    if user_id:
        clauses.append("user_id = ?")
        params.append(str(user_id)[:32])
    params.append(max(1, min(int(limit or 50), 200)))
    with connect_sync() as conn:
        rows = conn.execute(
            f"""
            SELECT trace_id, ts, session_type, group_id, user_id, stages,
                   outcome, diagnosis_code, detail
            FROM reply_turn_traces
            WHERE {' AND '.join(clauses)}
            ORDER BY ts DESC
            LIMIT ?
            """,
            tuple(params),
        ).fetchall()
    return [_row_to_dict(row) for row in rows]


def query_page(
    *,
    limit: int = 20,
    offset: int = 0,
    session_type: str = "",
    group_id: str = "",
    user_id: str = "",
    search: str = "",
) -> tuple[list[dict[str, Any]], int]:
    clauses = ["1=1"]
    params: list[Any] = []
    if session_type:
        clauses.append("session_type = ?")
        params.append(str(session_type)[:24])
    if group_id:
        clauses.append("group_id = ?")
        params.append(str(group_id)[:32])
    if user_id:
        clauses.append("user_id = ?")
        params.append(str(user_id)[:32])
    search_text = str(search or "").strip()[:160]
    if search_text:
        # Escape LIKE metacharacters so the WebUI search box performs a literal
        # text search instead of accidentally widening to every trace.
        escaped = (
            search_text.replace("^", "^^").replace("%", "^%").replace("_", "^_")
        )
        pattern = f"%{escaped}%"
        searchable_columns = (
            "trace_id",
            "session_type",
            "group_id",
            "user_id",
            "outcome",
            "diagnosis_code",
        )
        clauses.append(
            "(" + " OR ".join(f"{column} LIKE ? ESCAPE '^'" for column in searchable_columns) + ")"
        )
        params.extend([pattern] * len(searchable_columns))
    where = " AND ".join(clauses)
    with connect_sync() as conn:
        total_row = conn.execute(
            f"SELECT COUNT(*) AS count FROM reply_turn_traces WHERE {where}",
            tuple(params),
        ).fetchone()
        rows = conn.execute(
            f"""
            SELECT trace_id, ts, session_type, group_id, user_id, stages,
                   outcome, diagnosis_code, detail
            FROM reply_turn_traces
            WHERE {where}
            ORDER BY ts DESC
            LIMIT ? OFFSET ?
            """,
            (*params, max(1, min(int(limit or 20), 100)), max(0, int(offset))),
        ).fetchall()
    return [_row_to_dict(row) for row in rows], int(total_row["count"] if total_row else 0)


def _stage_category(stage: dict[str, Any]) -> str:
    key = str(stage.get("key") or "").strip().lower()
    label = str(stage.get("label") or "").strip()
    text = f"{key} {label}"
    if "tool" in key or "工具" in label:
        return "tool"
    if key.startswith("agent_") or "agent" in text.lower():
        return "agent"
    if key.startswith("semantic") or key.startswith("turn_plan") or "语义" in label:
        return "semantic"
    if key.startswith("send") or "发送" in label:
        return "send"
    if key.startswith("capture") or key.startswith("reply_timeout") or "捕获" in label:
        return "capture"
    if key.startswith("rule") or key.startswith("buffer") or "缓冲" in label:
        return "dispatch"
    return "runtime"


def _elapsed_from_detail(detail: Any) -> int | None:
    match = _ELAPSED_RE.search(str(detail or ""))
    if not match:
        return None
    try:
        return max(0, int(match.group(1)))
    except Exception:
        return None


def _finite_stage_timestamp(value: Any) -> float:
    try:
        timestamp = float(value or 0)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    return timestamp if timestamp > 0 and math.isfinite(timestamp) else 0.0


def _signals_from_detail(detail: Any) -> dict[str, str]:
    signals: dict[str, str] = {}
    for match in _SIGNAL_KEY_RE.finditer(str(detail or "")):
        key = str(match.group(1) or "").strip()
        value = sanitize_text(match.group(2) or "")[:80]
        if key and value and key not in signals:
            signals[key] = value
    return signals


def _compact_value(value: Any, *, limit: int = 80) -> str:
    text = sanitize_text(value)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def _append_unique(values: list[str], value: Any, *, limit: int = 80, max_items: int = 6) -> None:
    text = _compact_value(value, limit=limit)
    if text and text not in values and len(values) < max_items:
        values.append(text)


def _is_semantic_inspection_stage(key: str) -> bool:
    normalized = str(key or "").strip().lower()
    if normalized.startswith("yaml_"):
        normalized = normalized[5:]
    return bool(
        normalized in {"agent_intent", "agent_result"}
        or normalized.startswith("semantic_frame")
        or normalized.startswith("turn_plan")
    )


def _enum_signal(signals: dict[str, Any], name: str, *aliases: str) -> str:
    allowed = _SEMANTIC_ENUMS.get(name, frozenset())
    for source_name in (name, *aliases):
        normalized = str(signals.get(source_name) or "").strip().lower()
        if normalized in allowed:
            return normalized
    return ""


def _boolean_signal(signals: dict[str, Any], *names: str) -> str:
    for name in names:
        normalized = str(signals.get(name) or "").strip().lower()
        if normalized in {"true", "false"}:
            return normalized
    return ""


def _confidence_band(value: Any) -> str:
    try:
        confidence = float(value)
    except (TypeError, ValueError, OverflowError):
        return ""
    if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
        return ""
    if confidence < 0.5:
        return "low"
    if confidence < 0.8:
        return "medium"
    return "high"


def _tool_argument_summary(signals: dict[str, Any]) -> str:
    raw = str(signals.get("arg_keys") or "").strip()
    if not raw or raw == "-":
        return ""
    keys: list[str] = []
    for item in raw.split(","):
        key = item.strip()
        if _SAFE_TOOL_KEY_RE.fullmatch(key) and key not in keys:
            keys.append(key)
        if len(keys) >= 12:
            break
    return f"arg_keys={','.join(keys)}" if keys else ""


def _tool_result_summary(signals: dict[str, Any]) -> str:
    parts: list[str] = []
    result_len = str(signals.get("result_len") or "").strip()
    if result_len.isdigit():
        parts.append(f"result_len={min(int(result_len), 999999999)}")
    evidence = str(signals.get("evidence") or "").strip().lower()
    if evidence in {"opaque", "structured", "empty"}:
        parts.append(f"evidence={evidence}")
    media_routes = str(signals.get("media_routes") or "").strip()
    if _SAFE_TOOL_RESULT_TOKEN_RE.fullmatch(media_routes):
        parts.append(f"media_routes={media_routes}")
    return " ".join(parts)[:500]


def _build_agent_inspection(items: list[dict[str, Any]]) -> dict[str, Any]:
    understanding: dict[str, str] = {}
    addressing: dict[str, str] = {}
    tools: list[dict[str, Any]] = []
    questions: list[str] = []
    quality: list[str] = []
    budget: dict[str, str] = {}
    for item in items:
        key = str(item.get("key") or "")
        detail = str(item.get("detail") or "")
        signals = item.get("signals") if isinstance(item.get("signals"), dict) else {}
        if _is_semantic_inspection_stage(key):
            semantic_values = {
                "action": _enum_signal(signals, "action", "reply_action"),
                "intent": _enum_signal(signals, "intent"),
                "ambiguity": _enum_signal(signals, "ambiguity"),
                "speech_act": _enum_signal(signals, "speech_act"),
                "output": _enum_signal(signals, "output"),
                "reply_shape": _enum_signal(signals, "reply_shape"),
                "relationship_progress": _enum_signal(signals, "relationship_progress"),
                "conversation_scenario": _enum_signal(
                    signals, "conversation_scenario", "scenario"
                ),
                "address_mode": _enum_signal(signals, "address_mode"),
            }
            silence = _boolean_signal(signals, "silence", "recommend_silence")
            if silence:
                semantic_values["silence"] = silence
            emotion_intensity = _enum_signal(signals, "emotion_intensity")
            if emotion_intensity:
                semantic_values["emotion"] = emotion_intensity
            elif signals.get("emotion") or signals.get("bot_emotion"):
                # The model-owned emotion description is free text.  Expose
                # only its presence rather than copying it into the WebUI.
                semantic_values["emotion"] = "set"
            relationship_confidence = _confidence_band(
                signals.get("relationship_progress_confidence")
            )
            if relationship_confidence:
                semantic_values["relationship_confidence"] = relationship_confidence
            for name, value in semantic_values.items():
                if value and name not in understanding:
                    understanding[name] = value
            if "action" not in understanding and silence == "true":
                understanding["action"] = "silence"
        normalized_key = key.lower()
        if normalized_key.startswith("yaml_"):
            normalized_key = normalized_key[5:]
        if normalized_key == "addressing_plan":
            # Trace text is untrusted.  Only expose finite, protocol-shaped
            # addressing values; sources and target identifiers can otherwise
            # turn this summary into a free-text data leak.
            address_mode = _enum_signal(signals, "address_mode")
            if address_mode:
                addressing["address_mode"] = address_mode
            for name in ("quote", "at"):
                value = _boolean_signal(signals, name)
                if value:
                    addressing[name] = value
        if key in {"agent_tool_call", "agent_tool_result"} or item.get("category") == "tool":
            tool_name = str(signals.get("tool") or "")
            if not tool_name:
                match = re.search(r"(?:tool|name)=([^\s]+)", detail)
                tool_name = str(match.group(1)) if match else ""
            entry = {
                "stage": "result" if "result" in key else "call",
                "tool": _compact_value(tool_name or item.get("label") or key, limit=48),
                "status": str(item.get("status") or ""),
                "argument_summary": _tool_argument_summary(signals) if "result" not in key else "",
                "result_summary": _tool_result_summary(signals) if "result" in key else "",
                "duration_ms": item.get("duration_ms"),
            }
            if entry["tool"] or entry["argument_summary"] or entry["result_summary"]:
                tools.append(entry)
        if key in {"agent_query_rewrite", "agent_budget", "semantic_frame"}:
            for name in ("query", "reason"):
                if signals.get(name):
                    _append_unique(questions, signals[name])
            if key == "agent_query_rewrite" and detail:
                _append_unique(questions, detail, limit=140)
        if key == "agent_reply_quality":
            _append_unique(quality, detail, limit=160)
        if key == "agent_budget":
            for name in ("budget", "suggested_steps", "actual_steps", "suggested_seconds", "actual_seconds", "source"):
                if signals.get(name):
                    budget[name] = str(signals[name])
    return {
        "understanding": understanding,
        "addressing": addressing,
        "tools": tools[:10],
        "questions": questions[:6],
        "quality": quality[:4],
        "budget": budget,
    }


def build_process_view(trace: dict[str, Any] | None, *, logs: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Build a WebUI-safe process timeline.

    This intentionally summarizes observable stages only. It does not expose
    model hidden reasoning, prompts, raw tool arguments, or full tool results.
    """

    if not isinstance(trace, dict):
        trace = {}
    raw_stages = trace.get("stages") if isinstance(trace.get("stages"), list) else []
    stages = [stage for stage in raw_stages if isinstance(stage, dict)]
    base_ts = 0.0
    for stage in stages:
        ts = _finite_stage_timestamp(stage.get("ts"))
        if ts > 0:
            base_ts = ts
            break

    items: list[dict[str, Any]] = []
    status_counts: dict[str, int] = {}
    category_counts: dict[str, int] = {}
    for index, stage in enumerate(stages):
        ts = _finite_stage_timestamp(stage.get("ts"))
        next_ts = 0.0
        if index + 1 < len(stages):
            next_ts = _finite_stage_timestamp(stages[index + 1].get("ts"))
        detail = sanitize_text(stage.get("detail", ""))[:1000]
        hint = sanitize_text(stage.get("hint", ""))[:500]
        status = str(stage.get("status") or "info")[:16]
        category = _stage_category(stage)
        try:
            explicit_elapsed_ms = stage.get("elapsed_ms")
            elapsed_ms = max(0, int(explicit_elapsed_ms)) if explicit_elapsed_ms is not None else None
        except (TypeError, ValueError, OverflowError):
            elapsed_ms = None
        if elapsed_ms is None:
            elapsed_ms = _elapsed_from_detail(detail)
        if elapsed_ms is None and ts > 0 and next_ts > ts:
            elapsed_ms = int((next_ts - ts) * 1000)
        item = {
            "index": index + 1,
            "key": str(stage.get("key") or "")[:64],
            "label": str(stage.get("label") or stage.get("key") or "")[:80],
            "status": status,
            "category": category,
            "detail": detail,
            "signals": _signals_from_detail(detail),
            "hint": hint,
            "ts": ts,
            "offset_ms": int((ts - base_ts) * 1000) if ts > 0 and base_ts > 0 else 0,
            "duration_ms": elapsed_ms,
        }
        items.append(item)
        status_counts[status] = status_counts.get(status, 0) + 1
        category_counts[category] = category_counts.get(category, 0) + 1

    log_rows = logs if isinstance(logs, list) else []
    log_levels: dict[str, int] = {}
    for row in log_rows:
        if not isinstance(row, dict):
            continue
        level = str(row.get("level") or "INFO").upper()
        log_levels[level] = log_levels.get(level, 0) + 1

    slow_items = [
        {
            "index": item["index"],
            "label": item["label"],
            "key": item["key"],
            "duration_ms": item["duration_ms"],
        }
        for item in items
        if isinstance(item.get("duration_ms"), int) and int(item["duration_ms"]) >= 1000
    ]
    slow_items.sort(key=lambda item: int(item.get("duration_ms") or 0), reverse=True)

    outcome = str(trace.get("outcome") or "")
    diagnosis_code = str(trace.get("diagnosis_code") or "")
    trace_detail = trace.get("detail") if isinstance(trace.get("detail"), dict) else {}
    completion = {
        key: _compact_value(trace_detail.get(key), limit=80)
        for key in (
            "tool_execution",
            "evidence_delivery",
            "outbound_delivery",
            "social_coverage_status",
            "evidence_recovered",
            "media_delivery",
            "media_grounding",
            "media_only",
            "available_evidence_fields",
            "grounded_evidence_fields",
            "grounded_anchor_count",
            "recovery_method",
        )
        if trace_detail.get(key) not in {None, ""}
    }
    return {
        "summary": {
            "trace_id": str(trace.get("trace_id") or ""),
            "outcome": outcome,
            "diagnosis_code": diagnosis_code,
            "stage_count": len(items),
            "error_count": sum(status_counts.get(name, 0) for name in ("error", "failed")),
            "warn_count": status_counts.get("warn", 0) + status_counts.get("warning", 0),
            "log_count": len(log_rows),
            "status_counts": status_counts,
            "category_counts": category_counts,
            "log_levels": log_levels,
            "slow_stages": slow_items[:5],
            "completion": completion,
        },
        "items": items,
        "agent_inspection": _build_agent_inspection(items),
    }


def prune_old_entries(*, retention_days: int = 7, max_entries: int = 2000) -> int:
    cutoff = time.time() - max(1, int(retention_days or 7)) * 86400
    max_keep = max(100, int(max_entries or 2000))
    deleted = 0
    with connect_sync() as conn:
        cursor = conn.execute("DELETE FROM reply_turn_traces WHERE ts < ?", (cutoff,))
        deleted += int(cursor.rowcount or 0)
        cursor = conn.execute(
            """
            DELETE FROM reply_turn_traces
            WHERE trace_id NOT IN (
                SELECT trace_id FROM reply_turn_traces ORDER BY ts DESC LIMIT ?
            )
            """,
            (max_keep,),
        )
        deleted += int(cursor.rowcount or 0)
        conn.commit()
    return deleted


def _row_to_dict(row: Any) -> dict[str, Any]:
    try:
        stages = json.loads(row["stages"] or "[]")
    except Exception:
        stages = []
    try:
        detail = json.loads(row["detail"] or "{}")
    except Exception:
        detail = {}
    return {
        "trace_id": str(row["trace_id"] or ""),
        "ts": float(row["ts"] or 0),
        "session_type": str(row["session_type"] or ""),
        "group_id": str(row["group_id"] or ""),
        "user_id": str(row["user_id"] or ""),
        "stages": stages if isinstance(stages, list) else [],
        "outcome": str(row["outcome"] or ""),
        "diagnosis_code": str(row["diagnosis_code"] or ""),
        "detail": detail if isinstance(detail, dict) else {},
    }


__all__ = [
    "current_trace_id",
    "finish_trace",
    "get_trace",
    "build_process_view",
    "new_trace_id",
    "prune_old_entries",
    "query_recent",
    "query_page",
    "record_stage",
    "reset_current_trace_id",
    "set_current_trace_id",
    "start_trace",
]
