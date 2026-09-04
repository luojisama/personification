import asyncio
import hashlib
import json
import re
import time
from collections import OrderedDict
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Callable, Dict

from ..core import metrics
from ..core.context_cleanup import release_message_buffer_entry_resources
from ..core.message_relations import extract_mentioned_ids, extract_reply_message_id, extract_reply_sender_id
from ..core.command_runtime_context import has_runtime_command_prefix
from ..core.peer_bot_runtime import peer_bot_source_kind
from ..core.shared_content import normalize_merged_forward, parse_onebot_share_card
from ..core.target_inference import normalize_message_target_for_review
from ..core.group_followup_referent import get_group_followup_referent_resolver
from ..core.interrupted_reply import (
    attach_interrupted_reply_context,
    request_cooperative_reply_interruption,
)
from ..core.turn_deadline import HARD_TURN_TIMEOUT_SECONDS, attach_turn_deadline
from ..core.turn_media import (
    TurnMediaRef,
    coerce_turn_media,
    extract_turn_media_from_event,
    media_from_batched_events,
    resolve_onebot_quoted_media_refs,
    serialize_turn_media,
)
from .reply_commit import reply_lifecycle_snapshot


_GROUP_BATCH_DELAY_SECONDS = 1.2
_PRIVATE_BATCH_DELAY_SECONDS = 0.8
_BATCH_BASE_WAIT_SECONDS = 30.0
_BATCH_MIN_WAIT_SECONDS = 10.0
_BATCH_MAX_WAIT_SECONDS = 60.0
_MAX_BATCH_EVENTS = 8
_PROCESS_RESPONSE_TIMEOUT_SECONDS = 180.0
_ADMISSION_TIMEOUT_SECONDS = 15.0
_RECENT_MEDIA_TTL_SECONDS = 300.0
_RECENT_MEDIA_MAX_ENTRIES = 256
_recent_media_by_sender: OrderedDict[str, tuple[float, list[dict[str, Any]]]] = OrderedDict()
_SESSION_GENERATION_MAX_ENTRIES = 2048
_session_generation_ids: OrderedDict[str, int] = OrderedDict()


def _next_turn_generation(session_key: str) -> int:
    key = str(session_key or "")
    generation = int(_session_generation_ids.get(key, 0) or 0) + 1
    _session_generation_ids[key] = generation
    _session_generation_ids.move_to_end(key)
    while len(_session_generation_ids) > _SESSION_GENERATION_MAX_ENTRIES:
        _session_generation_ids.popitem(last=False)
    return generation


def _recent_media_key(session_key: str, user_id: str) -> str:
    return f"{str(session_key or '').strip()}\0{str(user_id or '').strip()}"


def _prune_recent_media(*, now: float) -> None:
    expired = [
        key
        for key, (expires_at, _refs) in _recent_media_by_sender.items()
        if float(expires_at or 0.0) <= now
    ]
    for key in expired:
        _recent_media_by_sender.pop(key, None)
    while len(_recent_media_by_sender) > _RECENT_MEDIA_MAX_ENTRIES:
        _recent_media_by_sender.popitem(last=False)


def _remember_recent_media(
    *,
    session_key: str,
    user_id: str,
    values: list[TurnMediaRef] | list[dict[str, Any]],
    now: float,
) -> None:
    normalized_user_id = str(user_id or "").strip()
    refs = [
        item
        for item in coerce_turn_media(values)
        if item.kind in {"video", "audio"}
        and item.owner_user_id == normalized_user_id
    ]
    if not refs or not normalized_user_id:
        return
    serialized = serialize_turn_media(refs)
    for item in serialized:
        item["origin"] = "batch"
    key = _recent_media_key(session_key, normalized_user_id)
    _recent_media_by_sender[key] = (now + _RECENT_MEDIA_TTL_SECONDS, serialized)
    _recent_media_by_sender.move_to_end(key)
    _prune_recent_media(now=now)


def _recent_media_for_followup(
    *,
    session_key: str,
    user_id: str,
    now: float,
) -> list[TurnMediaRef]:
    _prune_recent_media(now=now)
    key = _recent_media_key(session_key, user_id)
    cached = _recent_media_by_sender.get(key)
    if cached is None:
        return []
    expires_at, values = cached
    if float(expires_at or 0.0) <= now:
        _recent_media_by_sender.pop(key, None)
        return []
    _recent_media_by_sender.pop(key, None)
    return [
        item
        for item in coerce_turn_media(values)
        if item.owner_user_id == str(user_id or "").strip()
    ]


def _clear_recent_media_for_test() -> None:
    _recent_media_by_sender.clear()


async def _reset_attention_after_confirmed(state: dict[str, Any], session_key: str) -> None:
    if not bool(state.get("reply_delivery_complete", False)):
        return
    service = state.get("_attention_participation_service")
    reset = getattr(service, "reset_confirmed", None)
    if callable(reset):
        try:
            await reset(session_key)
        except Exception:
            pass


def _record_recovery_failure(
    *,
    bot: Any,
    event: Any,
    state: dict[str, Any],
    failure_stage: str,
    failure_class: str,
) -> int:
    """Persist only failed inbound messages; never store a generated reply."""

    from ..core.reply_recovery_queue import ReplyRecoveryQueue

    bot_id = str(getattr(bot, "self_id", "") or "").strip()
    if not bot_id:
        return 0
    fallback_group_id = str(getattr(event, "group_id", "") or "").strip()
    fallback_user_id = str(getattr(event, "user_id", "") or "").strip()
    batched = state.get("batched_events")
    entries = [item for item in batched if isinstance(item, dict)] if isinstance(batched, list) else []
    if not entries:
        state_media = state.get("turn_media_context")
        current_media = serialize_turn_media(
            extract_turn_media_from_event(event, current_origin="current")
        )
        # Policy gates run before normal batch serialization.  Merge any
        # already controlled state refs with event-derived refs so a media-only
        # inbound event is still recoverable, while serializing again gives us
        # the same media-id dedupe boundary as ordinary batch construction.
        recovered_media = serialize_turn_media(
            [
                *(state_media if isinstance(state_media, list) else []),
                *current_media,
            ]
        )
        entries = [
            {
                "message_id": str(getattr(event, "message_id", "") or "").strip(),
                "user_id": fallback_user_id,
                "group_id": fallback_group_id,
                "text": _extract_plain_text(event),
                "media": recovered_media,
            }
        ]
    queue = ReplyRecoveryQueue()
    recorded = 0
    for item in entries:
        message_id = str(item.get("message_id") or "").strip()
        group_id = str(item.get("group_id") or fallback_group_id).strip()
        user_id = str(item.get("user_id") or fallback_user_id).strip()
        conversation_kind = "group" if group_id else "private"
        conversation_id = group_id or user_id
        text = str(item.get("text") or "").strip()
        media = item.get("media") if isinstance(item.get("media"), list) else []
        if not conversation_id or (not text and not media):
            continue
        if not message_id:
            # Stable synthetic id preserves a no-message-id inbound failure
            # without embedding its body or session identifier in the queue.
            event_time = str(item.get("event_time", item.get("timestamp", getattr(event, "time", "missing"))) or "missing")
            try:
                media_digest = hashlib.sha256(
                    json.dumps(media, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
                ).hexdigest()[:16]
            except Exception:
                media_digest = "invalid-media"
            fingerprint = "\x1f".join((bot_id, conversation_kind, conversation_id, user_id, event_time, _normalize_repeat_key(text), media_digest))
            message_id = "synthetic:" + hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:24]
        queue.record_failure(
            bot_id=bot_id,
            conversation_kind=conversation_kind,
            conversation_id=conversation_id,
            original_message_id=message_id,
            normalized_text=text,
            media_refs=media,
            failure_stage=failure_stage,
            failure_class=failure_class,
            route_fingerprint=str(state.get("provider_route_fingerprint", "") or ""),
            trace_id=str(state.get("reply_trace_id", "") or ""),
            missing_part_indexes=state.get("delivery_missing_part_indexes") or (),
        )
        recorded += 1
    return recorded


async def _handle_reply_timeout(
    *,
    bot: Any,
    event: Any,
    state: dict[str, Any],
    session_key: str,
    timeout_seconds: float,
    logger: Any,
    commit_lock: asyncio.Lock | None = None,
) -> None:
    del logger, commit_lock
    delivery_started = bool(state.get("reply_delivery_started", False))
    delivery_confirmed = bool(state.get("reply_delivery_confirmed", False))
    delivery_complete = bool(state.get("reply_delivery_complete", False))
    lifecycle = reply_lifecycle_snapshot(state)
    if delivery_complete:
        delivery_state = "complete"
        outcome = "ok"
        diagnosis_code = "post_send_timeout"
    elif delivery_confirmed:
        delivery_state = "partial"
        outcome = "partial"
        diagnosis_code = "partial_reply_timeout"
    elif delivery_started:
        delivery_state = "dispatching"
        outcome = "outcome_unknown"
        diagnosis_code = "send_outcome_unknown"
    else:
        delivery_state = "not_started"
        outcome = "failed"
        diagnosis_code = "reply_timeout"
    if not delivery_complete:
        failure_class = (
            "delivery_partial"
            if delivery_confirmed
            else "delivery_unknown"
            if delivery_started
            else "generation_failed_before_send"
        )
        try:
            await asyncio.to_thread(
                _record_recovery_failure,
                bot=bot,
                event=event,
                state=state,
                failure_stage=diagnosis_code,
                failure_class=failure_class,
            )
        except Exception:
            pass
    try:
        from ..core import reply_turn_trace

        trace_id = str(state.get("reply_trace_id", "") or "")
        reply_turn_trace.record_stage(
            trace_id=trace_id,
            key="reply_timeout",
            label="回复超时",
            status="warn" if delivery_confirmed else "error",
            detail=(
                f"timeout_seconds={timeout_seconds:g} reply_required={str(bool(state.get('reply_required', False))).lower()} "
                f"delivery_started={str(delivery_started).lower()} "
                f"delivery_confirmed={str(delivery_confirmed).lower()} "
                f"delivery_complete={str(delivery_complete).lower()} "
                f"delivery_state={delivery_state} "
                f"last_phase={lifecycle['last_phase']} "
                f"phase_age_ms={lifecycle['phase_age_ms']} "
                f"elapsed_ms={lifecycle['elapsed_ms']}"
            ),
            hint="基础设施故障保持静默；检查 Provider、工具耗时、状态锁与发送回执",
        )
        reply_turn_trace.finish_trace(
            trace_id=trace_id,
            outcome=outcome,
            diagnosis_code=diagnosis_code,
            detail={
                "timeout_seconds": timeout_seconds,
                "reply_required": bool(state.get("reply_required", False)),
                "delivery_started": delivery_started,
                "delivery_confirmed": delivery_confirmed,
                "delivery_complete": delivery_complete,
                "delivery_state": delivery_state,
                "last_phase": lifecycle["last_phase"],
                "phase_age_ms": lifecycle["phase_age_ms"],
                "elapsed_ms": lifecycle["elapsed_ms"],
                "silent": True,
            },
        )
    except Exception:
        pass


def _record_reply_admission_timeout(
    *,
    bot: Any | None = None,
    event: Any,
    state: dict[str, Any],
    session_key: str,
    wait_ms: int,
    mode: str,
) -> None:
    if bot is not None:
        # Admission has not started delivery, so this is a safe inbound-only
        # recovery record.  It is diagnostic only; the queue never replays an
        # unknown/partial outbound send automatically.
        try:
            _record_recovery_failure(
                bot=bot,
                event=event,
                state=state,
                failure_stage="reply_admission_timeout",
                failure_class="generation_failed_before_send",
            )
        except Exception:
            pass
    try:
        from ..core import reply_turn_trace

        group_id = str(getattr(event, "group_id", "") or "")
        user_id = str(getattr(event, "user_id", "") or "")
        try:
            incoming_text = str(event.get_plaintext() or "")[:2000]
        except Exception:
            incoming_text = str(
                state.get("raw_message_text")
                or getattr(event, "raw_message", "")
                or ""
            )[:2000]

        trace_id = reply_turn_trace.start_trace(
            trace_id=str(state.get("reply_trace_id", "") or ""),
            session_type="group" if group_id else "private",
            group_id=group_id,
            user_id=user_id,
            detail={
                "source": "reply_admission",
                "mode": mode,
                "message_id": str(getattr(event, "message_id", "") or ""),
                "incoming_text": incoming_text,
            },
        )
        state["reply_trace_id"] = trace_id
        reply_turn_trace.record_stage(
            trace_id=trace_id,
            key="reply_admission_timeout",
            label="回复排队超时",
            status="warn",
            detail=(
                f"mode={mode} wait_ms={max(0, int(wait_ms))} "
                f"reply_required={str(bool(state.get('reply_required', False))).lower()}"
            ),
            hint="检查回复并发、事件循环延迟和上游请求耗时",
        )
        reply_turn_trace.finish_trace(
            trace_id=trace_id,
            outcome="failed" if state.get("reply_required") else "no_reply",
            diagnosis_code="reply_admission_timeout",
            detail={
                "mode": mode,
                "wait_ms": max(0, int(wait_ms)),
                "reply_required": bool(state.get("reply_required", False)),
                "silent": True,
            },
        )
    except Exception:
        pass


def _pop_buffer_entry(
    msg_buffer: Dict[str, Dict[str, Any]],
    key: str,
) -> dict[str, Any] | None:
    entry = msg_buffer.pop(key, None)
    if isinstance(entry, dict):
        release_message_buffer_entry_resources(entry)
    return entry


def _retain_buffer_entry(
    *,
    entry: dict[str, Any],
    key: str,
    concurrency_controller: "ReplyConcurrencyController | None",
) -> None:
    if concurrency_controller is None or callable(entry.get("_release_concurrency_gate")):
        return
    concurrency_controller.retain_buffer_session(key)
    entry["_release_concurrency_gate"] = (
        lambda controller=concurrency_controller, session_key=key: controller.release_buffer_session(
            session_key
        )
    )


class ReplyAdmissionTimeout(asyncio.TimeoutError):
    def __init__(self, wait_ms: int) -> None:
        super().__init__("reply admission timed out")
        self.wait_ms = max(0, int(wait_ms))


@dataclass
class _SessionGate:
    semaphore: asyncio.Semaphore
    commit_lock: asyncio.Lock
    direct_idle: asyncio.Event
    refs: int = 0
    direct_count: int = 0
    waiters: int = 0
    active: int = 0


class ReplyConcurrencyController:
    def __init__(self, *, session_limit: int = 3, global_limit: int = 12) -> None:
        self._global_semaphore = asyncio.Semaphore(max(1, int(global_limit)))
        self._session_limit = max(1, int(session_limit))
        self._session_gates: dict[str, _SessionGate] = {}

    def _gate(self, key: str) -> _SessionGate:
        gate = self._session_gates.get(key)
        if gate is None:
            idle = asyncio.Event()
            idle.set()
            gate = _SessionGate(
                semaphore=asyncio.Semaphore(self._session_limit),
                commit_lock=asyncio.Lock(),
                direct_idle=idle,
            )
            self._session_gates[key] = gate
        return gate

    def _retain(self, key: str) -> _SessionGate:
        gate = self._gate(key)
        gate.refs += 1
        return gate

    def _release(self, key: str, gate: _SessionGate) -> None:
        gate.refs = max(0, gate.refs - 1)
        self._cleanup(key, gate)

    def _cleanup(self, key: str, gate: _SessionGate) -> None:
        if (
            gate.refs == 0
            and gate.direct_count == 0
            and gate.waiters == 0
            and gate.active == 0
            and not gate.commit_lock.locked()
            and self._session_gates.get(key) is gate
        ):
            self._session_gates.pop(key, None)

    def retain_buffer_session(self, key: str) -> None:
        self._retain(key)

    def release_buffer_session(self, key: str) -> None:
        gate = self._session_gates.get(key)
        if gate is not None:
            self._release(key, gate)

    def commit_lock(self, key: str) -> asyncio.Lock:
        return self._gate(key).commit_lock

    async def wait_for_direct_idle(self, key: str) -> None:
        gate = self._retain(key)
        try:
            await gate.direct_idle.wait()
        finally:
            self._release(key, gate)

    def snapshot(self) -> dict[str, int]:
        return {
            "active": sum(gate.active for gate in self._session_gates.values()),
            "waiting": sum(gate.waiters for gate in self._session_gates.values()),
            "session_gates": len(self._session_gates),
        }


    @staticmethod
    def _admission_deadline(deadline: float | None) -> float:
        now = time.monotonic()
        allowed = _ADMISSION_TIMEOUT_SECONDS
        if deadline is not None:
            allowed = min(allowed, max(0.0, float(deadline) - now))
        return now + max(0.0, allowed)

    async def _wait_until(self, awaitable: Any, *, deadline: float) -> None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            close = getattr(awaitable, "close", None)
            if callable(close):
                close()
            raise asyncio.TimeoutError
        await asyncio.wait_for(awaitable, timeout=remaining)

    @asynccontextmanager
    async def _turn(self, key: str, *, direct: bool, deadline: float | None = None):
        gate = self._retain(key)
        started_at = time.monotonic()
        acquired_session = False
        acquired_global = False
        admitted = False
        if direct:
            gate.direct_count += 1
            gate.direct_idle.clear()
        gate.waiters += 1
        try:
            try:
                admission_deadline = self._admission_deadline(deadline)
                if not direct:
                    await self._wait_until(gate.direct_idle.wait(), deadline=admission_deadline)
                await self._wait_until(gate.semaphore.acquire(), deadline=admission_deadline)
                acquired_session = True
                await self._wait_until(self._global_semaphore.acquire(), deadline=admission_deadline)
                acquired_global = True
            except asyncio.TimeoutError as exc:
                wait_ms = int(max(0.0, (time.monotonic() - started_at) * 1000.0))
                metrics.record_counter(
                    "reply_admission_timeout_total",
                    mode="direct" if direct else "buffered",
                )
                raise ReplyAdmissionTimeout(wait_ms) from exc
            gate.waiters = max(0, gate.waiters - 1)
            gate.active += 1
            admitted = True
            wait_ms = max(0.0, (time.monotonic() - started_at) * 1000.0)
            metrics.record_timing(
                "reply_admission_wait_ms",
                wait_ms,
                mode="direct" if direct else "buffered",
            )
            yield gate.commit_lock
        finally:
            if admitted:
                gate.active = max(0, gate.active - 1)
            else:
                gate.waiters = max(0, gate.waiters - 1)
            if acquired_global:
                self._global_semaphore.release()
            if acquired_session:
                gate.semaphore.release()
            if direct:
                gate.direct_count = max(0, gate.direct_count - 1)
                if gate.direct_count == 0:
                    gate.direct_idle.set()
            self._release(key, gate)

    @asynccontextmanager
    async def direct_turn(self, key: str, *, deadline: float | None = None):
        async with self._turn(key, direct=True, deadline=deadline) as commit_lock:
            yield commit_lock

    @asynccontextmanager
    async def buffered_turn(self, key: str, *, deadline: float | None = None):
        async with self._turn(key, direct=False, deadline=deadline) as commit_lock:
            yield commit_lock


def buffer_runtime_snapshot(msg_buffer: Dict[str, Dict[str, Any]]) -> dict[str, int]:
    """Counts only queue state; no session key or message data leaves this API."""
    now = time.monotonic()
    entries = [entry for entry in msg_buffer.values() if isinstance(entry, dict)]
    buffered_sessions = sum(
        bool(entry.get("items") or entry.get("pending_items") or entry.get("active_items") or entry.get("processing"))
        for entry in entries
    )
    # queued_items mirrors pending_items for observability, so never count it
    # twice.  active_items are owned by an in-flight generation and must be
    # included in the real buffer total.
    def _entry_items(entry: dict[str, Any]) -> list[dict[str, Any]]:
        seen: set[str] = set()
        result: list[dict[str, Any]] = []
        for field in ("items", "pending_items", "active_items"):
            for item in entry.get(field) or []:
                if not isinstance(item, dict):
                    continue
                identity = str(item.get("dedupe_key") or f"object:{id(item)}")
                if identity not in seen:
                    seen.add(identity)
                    result.append(item)
        return result
    all_items = [_entry_items(entry) for entry in entries]
    buffered_messages = sum(len(items) for items in all_items)
    processing = sum(bool(entry.get("processing")) for entry in entries)
    ages = []
    for entry, items in zip(entries, all_items):
        received = [float(item.get("received_at", 0.0) or 0.0) for item in items]
        received = [value for value in received if value > 0]
        fallback = float(entry.get("batch_started_at", 0.0) or entry.get("pending_started_at", 0.0) or entry.get("processing_started_at", 0.0) or now)
        if items:
            ages.append(now - min(received, default=fallback))
    fire_times = [float(entry.get("next_fire_at", 0.0) or 0.0) for entry in entries if not entry.get("processing") and float(entry.get("next_fire_at", 0.0) or 0.0) > now]
    return {"buffered_sessions": buffered_sessions, "buffered_messages": buffered_messages, "processing_buffer_sessions": processing, "oldest_buffer_age_ms": int(max(ages, default=0.0) * 1000), "next_buffer_fire_ms": int(max(0.0, min(fire_times) - now) * 1000) if fire_times else 0}


def _has_reply_semantics(event: Any) -> bool:
    for attr in ("reply", "quoted", "quote"):
        value = getattr(event, attr, None)
        if value:
            return True
    if getattr(event, "reply_to_message_id", None):
        return True
    return False


def _extract_sender_name(event: Any) -> str:
    sender = getattr(event, "sender", None)
    if sender is None:
        return str(getattr(event, "user_id", "") or "未知")
    return str(
        getattr(sender, "card", None)
        or getattr(sender, "nickname", None)
        or getattr(event, "user_id", "")
        or "未知"
    ).strip()


def _extract_plain_text(event: Any) -> str:
    parts: list[str] = []
    message = getattr(event, "message", None)
    if message is None:
        return ""
    try:
        for seg in message:
            seg_type = getattr(seg, "type", None)
            data = getattr(seg, "data", {}) or {}
            if seg_type == "text":
                parts.append(str(data.get("text", "") or ""))
            elif seg_type == "mface":
                from ..core.qq_expression_library import semantic_text_for_qq_expression_segment

                parts.append(semantic_text_for_qq_expression_segment("mface", data, default_mface_kind="super"))
            elif seg_type == "face":
                from ..core.qq_expression_library import semantic_text_for_qq_expression_segment

                parts.append(semantic_text_for_qq_expression_segment("face", data))
    except Exception:
        return ""
    return "".join(parts).strip()


def _extract_shared_content_context(event: Any) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    shared: list[dict[str, Any]] = []
    forward_bundle: dict[str, Any] | None = None
    try:
        for segment in getattr(event, "message", None) or []:
            segment_type = str(getattr(segment, "type", "") or "").strip().lower()
            data = getattr(segment, "data", {}) or {}
            if segment_type in {"json", "xml", "share"}:
                item = parse_onebot_share_card(
                    {"type": segment_type, "data": dict(data)},
                    segment_type=segment_type,
                )
                shared.append(item.to_dict())
            elif segment_type in {"forward", "node"}:
                payload = data.get("content") or data.get("nodes") or data
                forward_bundle = normalize_merged_forward(payload).to_dict()
    except Exception:
        return shared[:12], forward_bundle
    return shared[:12], forward_bundle


def _is_sticker_only_event(event: Any) -> bool:
    """判断一条消息是否「纯表情」（只有 face/mface/image 段，无实质文本）。

    按 segment type 判定，不靠占位符文本匹配——避免把表情误当成复读内容。
    """
    message = getattr(event, "message", None)
    if message is None:
        return False
    saw_sticker = False
    try:
        for seg in message:
            seg_type = getattr(seg, "type", None)
            data = getattr(seg, "data", {}) or {}
            if seg_type == "text":
                if str(data.get("text", "") or "").strip():
                    return False
            elif seg_type in {"face", "mface", "image"}:
                saw_sticker = True
            elif seg_type in {"at", "reply"}:
                continue
            else:
                # 其它段（语音、视频、json 卡片等）不算纯表情
                return False
    except Exception:
        return False
    return saw_sticker


def _normalize_repeat_key(text: str) -> str:
    normalized = re.sub(r"\s+", "", str(text or "").strip().lower())
    normalized = re.sub(r"[^\w\u4e00-\u9fff]+", "", normalized)
    return normalized[:120]


def _stable_item_key(session_key: str, event: Any, received_at: float) -> str:
    message_id = str(getattr(event, "message_id", "") or "").strip()
    if message_id:
        return f"id:{message_id}"
    event_time = getattr(event, "time", None)
    if event_time is None:
        event_time = getattr(event, "timestamp", None)
    try:
        stable_time = f"{float(event_time):.3f}"
    except (TypeError, ValueError):
        # Absence is itself a stable bucket; never use handler-local monotonic
        # receive time or duplicate redelivery becomes a fresh event.
        stable_time = "missing"
    body = re.sub(r"\s+", " ", _extract_plain_text(event)).strip().lower()
    body = re.sub(r"[\x00-\x1f\x7f]+", " ", body)
    media_kinds = ",".join(sorted(
        str(getattr(segment, "type", "") or "").strip().lower() + ":" + re.sub(r"\s+", " ", json.dumps(getattr(segment, "data", {}) or {}, sort_keys=True, ensure_ascii=False, default=str)).strip()
        for segment in (getattr(event, "message", None) or [])
        if str(getattr(segment, "type", "") or "").strip().lower() not in {"text", "at", "reply"}
    ))
    raw = "\x1f".join((str(session_key), str(getattr(event, "user_id", "") or ""), stable_time, body, media_kinds))
    return "fallback:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _is_direct_mention(event: Any, bot_self_id: str) -> bool:
    if not bot_self_id:
        return False
    try:
        for seg in getattr(event, "message", []) or []:
            if getattr(seg, "type", None) != "at":
                continue
            qq = str((getattr(seg, "data", {}) or {}).get("qq", "")).strip()
            if qq == bot_self_id:
                return True
    except Exception:
        return False
    return False


def _is_reply_to_bot(event: Any, bot_self_id: str, *, message_target: str = "") -> bool:
    if not bot_self_id:
        return False
    normalized_target = normalize_message_target_for_review(message_target)
    if normalized_target in {"external_plugin", "others"}:
        return False
    reply = getattr(event, "reply", None)
    if not reply:
        return False
    sender = getattr(reply, "sender", None)
    if sender is None and isinstance(reply, dict):
        sender = reply.get("sender")
    reply_user_id = ""
    if isinstance(sender, dict):
        reply_user_id = str(sender.get("user_id", "") or "").strip()
    elif sender is not None:
        reply_user_id = str(getattr(sender, "user_id", "") or "").strip()
    if not reply_user_id:
        reply_user_id = str(getattr(reply, "self_id", "") or "").strip()
        if not reply_user_id and isinstance(reply, dict):
            reply_user_id = str(reply.get("self_id", "") or "").strip()
    if normalized_target == "bot":
        return True
    return bool(reply_user_id and reply_user_id == bot_self_id)


def _select_merged_event(events: list[Any]) -> Any:
    for event in reversed(events):
        if _has_reply_semantics(event):
            return event
    return events[-1]


def _serialize_batched_event(
    item: dict[str, Any],
    *,
    selected_event: Any = None,
) -> dict[str, Any]:
    event = item.get("event")
    current_origin = "current" if event is selected_event else "batch"
    reply_message_id = extract_reply_message_id(event)
    reply = getattr(event, "reply", None)
    mentioned_ids, is_at_bot = extract_mentioned_ids(
        getattr(event, "message", None) or [],
        bot_self_id=str(getattr(getattr(event, "bot", None), "self_id", "") or ""),
    )
    sender = getattr(event, "sender", None)
    sender_role = str(
        getattr(sender, "role", "") if sender is not None else ""
    ).strip()
    return {
        "message_id": str(getattr(event, "message_id", "") or "").strip(),
        "user_id": str(getattr(event, "user_id", "") or "").strip(),
        "group_id": str(getattr(event, "group_id", "") or "").strip(),
        "event_time": str(getattr(event, "time", None) or getattr(event, "timestamp", None) or "").strip(),
        "sender_name": _extract_sender_name(event),
        # The shared envelope performs the explicit 2,000-char boundary and
        # records truncation diagnostics.  Do not silently narrow it here.
        "text": _extract_plain_text(event),
        "reply_to_msg_id": str(reply_message_id or "").strip(),
        "reply_to_user_id": extract_reply_sender_id(reply),
        "mentioned_ids": mentioned_ids,
        "is_direct_mention": bool(item.get("is_direct_mention") or is_at_bot),
        "is_reply_to_bot": bool(item.get("is_reply_to_bot")),
        "has_reply_semantics": _has_reply_semantics(event),
        "is_current_trigger": event is selected_event,
        "sender_role": sender_role,
        "media": serialize_turn_media(
            extract_turn_media_from_event(
                event,
                current_origin=current_origin,
            )
        ),
    }


def _build_repeat_clusters(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    clusters: dict[str, dict[str, Any]] = {}
    for item in items:
        event = item.get("event")
        # 纯表情消息不参与复读簇统计：多人刷表情不应被当成「反复在说」而触发吐槽/跟读。
        if _is_sticker_only_event(event):
            continue
        text = _extract_plain_text(event)
        key = _normalize_repeat_key(text)
        if not key:
            continue
        cluster = clusters.setdefault(
            key,
            {
                "text": text.strip()[:120],
                "count": 0,
                "speaker_ids": set(),
                "speakers": [],
            },
        )
        cluster["count"] += 1
        user_id = str(getattr(item.get("event"), "user_id", "") or "").strip()
        speaker = _extract_sender_name(item.get("event"))
        if user_id and user_id not in cluster["speaker_ids"]:
            cluster["speaker_ids"].add(user_id)
            cluster["speakers"].append(speaker)

    results: list[dict[str, Any]] = []
    for cluster in clusters.values():
        speaker_count = len(cluster["speaker_ids"])
        count = int(cluster["count"] or 0)
        if speaker_count >= 2 or count >= 3:
            results.append(
                {
                    "text": str(cluster["text"] or ""),
                    "count": count,
                    "speakers": list(cluster["speakers"]),
                }
            )
    results.sort(key=lambda item: (-int(item.get("count", 0) or 0), str(item.get("text", "") or "")))
    return results[:3]


def _select_batch_trigger(items: list[dict[str, Any]]) -> tuple[dict[str, Any], str]:
    for item in reversed(items):
        if item.get("is_direct_mention"):
            return item, "direct_mention"
    for item in reversed(items):
        if item.get("is_reply_to_bot"):
            return item, "reply_to_bot"
    for item in reversed(items):
        state = item.get("state") if isinstance(item.get("state"), dict) else {}
        try:
            confidence = float(state.get("message_target_confidence", 0.0) or 0.0)
        except (TypeError, ValueError, OverflowError):
            confidence = 0.0
        if (
            normalize_message_target_for_review(state.get("message_target")) == "bot"
            and confidence >= 0.78
        ):
            return item, "high_confidence_target_bot"
    # A human-to-human reply remains represented in its own batch envelope,
    # but it must not become the delivery trigger for unrelated later arrivals.
    return items[-1], "latest"


def _build_combined_message(
    items: list[dict[str, Any]],
    *,
    message_cls: Any,
    message_segment_cls: Any,
) -> Any:
    combined_message = message_cls()
    for index, item in enumerate(items):
        event = item.get("event")
        if event is None:
            continue
        if index > 0:
            combined_message.append(message_segment_cls.text(" "))
        combined_message.extend(getattr(event, "message", message_cls()))
    return combined_message


def _session_key(
    event: Any,
    *,
    group_message_event_cls: Any,
    bot_self_id: str = "",
) -> str:
    user_id = str(getattr(event, "user_id", "") or "")
    if isinstance(event, group_message_event_cls):
        scope = str(getattr(event, "group_id", "") or "")
    else:
        scope = f"private_{user_id}"
    return f"{bot_self_id}:{scope}" if bot_self_id else scope


def _batch_delay(event: Any, *, group_message_event_cls: Any) -> float:
    return _GROUP_BATCH_DELAY_SECONDS if isinstance(event, group_message_event_cls) else _PRIVATE_BATCH_DELAY_SECONDS


def _new_entry(delay: float) -> dict[str, Any]:
    return {
        "items": [],
        "pending_items": [],
        # Explicit names for observability/state-machine contracts.  Existing
        # callers retain items/pending_items compatibility during migration.
        "active_items": [],
        "queued_items": [],
        "delivery_state": "not_started",
        "processing": False,
        "active_task": None,
        "processing_started_at": 0.0,
        "current_trigger_type": "",
        "current_is_random_chat": False,
        "timer_task": None,
        "delay": delay,
        "batch_started_at": 0.0,
        "pending_started_at": 0.0,
        "last_item_at": 0.0,
        "pending_ready": False,
        "current_generation": 0,
        # The owner token protects a replacement generation from an older
        # cancelled task's finally block.
        "active_generation_token": 0,
        "superseded_generation": 0,
        "newer_batch_for_current": False,
        # Generation-scoped cooperative interruption is distinct from stale
        # pre-send cancellation.  Draft bodies live in the dedicated core
        # contract and never enter diagnostics.
        "interrupt_requested_generation": 0,
        "interrupted_outgoing_drafts": None,
        # Delayed until an actual turn starts.  Values are deliberately
        # aggregate-only: no session key, sender, message id or message body.
        "buffer_diagnostics": [],
    }


def _note_buffer_diagnostic(entry: dict[str, Any], code: str, *, count: int = 0) -> None:
    values = entry.setdefault("buffer_diagnostics", [])
    if not isinstance(values, list):
        return
    values.append({"code": str(code), "count": max(0, int(count)), "at": time.monotonic()})
    del values[:-24]


def _record_buffer_failure_trace(state: dict[str, Any], code: str, *, count: int, generation: int, wait_ms: int) -> None:
    trace_id = str(state.get("reply_trace_id", "") or "")
    if not trace_id:
        return
    try:
        from ..core import reply_turn_trace
        reply_turn_trace.record_stage(trace_id=trace_id, key="buffer_failure", label="缓冲失败", status="error", detail=f"code={code} count={max(0, count)} generation={max(0, generation)} wait_ms={max(0, wait_ms)}")
    except Exception:
        pass


def _take_buffer_diagnostics(entry: dict[str, Any], *, generation: int, wait_ms: int = 0, dequeue_count: int = 0, queued_count: int = 0) -> list[dict[str, int | str]]:
    values = list(entry.get("buffer_diagnostics") or [])
    entry["buffer_diagnostics"] = []
    now = time.monotonic()
    result = [{"code": str(item.get("code") or "buffer_event"), "count": max(0, int(item.get("count") or 0)), "generation": max(0, int(generation)), "wait_ms": max(0, int((now - float(item.get("at") or now)) * 1000))} for item in values]
    result.append({"code": "dequeue", "count": max(0, int(dequeue_count)), "generation": max(0, int(generation)), "wait_ms": max(0, int(wait_ms))})
    if queued_count:
        result.append({"code": "chunk", "count": max(0, int(queued_count)), "generation": max(0, int(generation)), "wait_ms": max(0, int(wait_ms))})
    return result


def _trim_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Compatibility name: keep FIFO data intact; batching happens at dequeue."""
    return list(items)


def _schedule_delay(started_at: float, delay: float, *, immediate: bool = False) -> float:
    if immediate or started_at <= 0:
        return 0.0
    elapsed = max(0.0, time.monotonic() - started_at)
    return max(0.0, float(delay) - elapsed)


def _compute_batch_fire_at(
    *,
    first_at: float,
    last_at: float,
    last_reply_at: float,
    base_wait_seconds: float,
    min_wait_seconds: float,
    max_wait_seconds: float,
    legacy_reply_backoff_seconds: float | None,
    now: float,
) -> float:
    """计算该批应回复的绝对时间点。

    每次新消息都从最后一条重新计算动态等待，同时以首条消息后的
    ``max_wait_seconds`` 为硬截止。生产配置会把等待限制在 10–60 秒；
    测试可注入同比缩短后的数值，不需要真实等待 30 秒。

    ``legacy_reply_backoff_seconds`` 只兼容旧配置中显式提供的值，新安装
    不再继承旧版 15 秒默认值。
    """
    lower = max(0.0, float(min_wait_seconds or 0.0))
    upper = max(lower, float(max_wait_seconds or lower))
    requested = min(upper, max(lower, float(base_wait_seconds or lower)))
    fire_at = last_at + requested
    legacy_backoff = max(0.0, float(legacy_reply_backoff_seconds or 0.0))
    if legacy_backoff > 0 and last_reply_at > 0 and now - last_reply_at < legacy_backoff:
        fire_at = max(fire_at, last_reply_at + legacy_backoff)
    return min(first_at + upper, fire_at)


def _schedule_debounce_wait(
    *,
    first_at: float,
    last_at: float,
    last_reply_at: float,
    base_wait_seconds: float,
    min_wait_seconds: float,
    max_wait_seconds: float,
    legacy_reply_backoff_seconds: float | None,
    immediate: bool,
    now: float | None = None,
) -> float:
    if immediate:
        return 0.0
    if first_at <= 0 or last_at <= 0:
        return max(0.0, float(base_wait_seconds or 0.0))
    now = time.monotonic() if now is None else now
    target = _compute_batch_fire_at(
        first_at=first_at,
        last_at=last_at,
        last_reply_at=last_reply_at,
        base_wait_seconds=base_wait_seconds,
        min_wait_seconds=min_wait_seconds,
        max_wait_seconds=max_wait_seconds,
        legacy_reply_backoff_seconds=legacy_reply_backoff_seconds,
        now=now,
    )
    return max(0.0, target - now)


_SESSION_LAST_REPLY_AT: Dict[str, float] = {}
_SESSION_REPLY_TTL_SECONDS = 600.0
_PRIVATE_DIRECT_STATE: Dict[str, dict[str, Any]] = {}


def _note_session_reply(session_key: str) -> None:
    key = str(session_key or "")
    if not key:
        return
    now = time.monotonic()
    _SESSION_LAST_REPLY_AT[key] = now
    expired = [
        k for k, ts in _SESSION_LAST_REPLY_AT.items() if now - ts > _SESSION_REPLY_TTL_SECONDS
    ]
    for k in expired:
        _SESSION_LAST_REPLY_AT.pop(k, None)


def _session_last_reply_at(session_key: str) -> float:
    return float(_SESSION_LAST_REPLY_AT.get(str(session_key or ""), 0.0) or 0.0)


async def _await_private_direct_backoff(
    session_key: str,
    *,
    debounce_seconds: float,
    max_wait_seconds: float,
    backoff_seconds: float,
) -> None:
    """私聊刚回复完对方又连发时，推迟本轮处理到「安静 debounce 秒」之后。

    只影响刚回过话后的连发；距上次回复较久的首条消息直接正常处理，不打断原有直连节奏。
    不丢弃消息，只是统一推迟处理，避免 bot 对每条连发私聊都秒回。
    触发点取「最后一条消息 + debounce」与「上次回复 + backoff」的较晚者，
    并以本轮首条消息 + max_wait 封顶；两者都是固定时点，循环必然收敛。
    """
    if debounce_seconds <= 0 or max_wait_seconds <= 0 or backoff_seconds <= 0:
        return
    key = str(session_key or "")
    now = time.monotonic()
    last_reply = _session_last_reply_at(key)
    if last_reply <= 0 or now - last_reply >= backoff_seconds:
        return
    st = _PRIVATE_DIRECT_STATE.setdefault(key, {})
    st["last_message_at"] = now
    if float(st.get("burst_start", 0.0) or 0.0) <= last_reply:
        st["burst_start"] = now
    while True:
        now = time.monotonic()
        last_message_at = float(st.get("last_message_at", now) or now)
        fire_at = min(
            float(st.get("burst_start", now) or now) + max_wait_seconds,
            max(last_message_at + debounce_seconds, last_reply + backoff_seconds),
        )
        wait = fire_at - now
        if wait <= 0:
            break
        await asyncio.sleep(min(wait, 0.1))
    if now - last_reply > max(max_wait_seconds, backoff_seconds) + 60:
        _PRIVATE_DIRECT_STATE.pop(key, None)


def _cancel_timer(entry: dict[str, Any]) -> None:
    timer_task = entry.get("timer_task")
    if timer_task:
        timer_task.cancel()
    entry["timer_task"] = None


def _schedule_timer(
    *,
    entry: dict[str, Any],
    key: str,
    bot: Any,
    wait_seconds: float,
    start_buffer_timer: Callable[[str, Any, float], Any],
) -> None:
    _cancel_timer(entry)
    _note_buffer_diagnostic(entry, "reschedule", count=len(entry.get("items") or []) + len(entry.get("pending_items") or []))
    entry["next_fire_at"] = time.monotonic() + max(0.0, wait_seconds)
    entry["timer_task"] = start_buffer_timer(key, bot, wait_seconds)


def _promote_pending_batch(entry: dict[str, Any]) -> None:
    entry["items"] = list(entry.get("pending_items", []))
    entry["pending_items"] = []
    entry["batch_started_at"] = float(entry.get("pending_started_at", 0.0) or time.monotonic())
    entry["pending_started_at"] = 0.0
    entry["pending_ready"] = False
    _note_buffer_diagnostic(entry, "promote_next_generation", count=len(entry.get("items") or []))


def _entry_timing(
    entry: dict[str, Any],
    *,
    base_wait_seconds: float,
    min_wait_seconds: float,
    max_wait_seconds: float,
    legacy_reply_backoff_seconds: float | None,
) -> tuple[float, float, float, float | None]:
    """Use timing captured by the most recent enqueue/reorder.

    A sleeping timer deliberately keeps its old deadline; when a new arrival
    causes a requeue, ``handle_reply_event`` records the freshly resolved
    values here and the following generation consumes those values.
    """
    values = entry.get("timing") if isinstance(entry.get("timing"), dict) else {}
    return (
        float(values.get("base", base_wait_seconds)),
        float(values.get("min", min_wait_seconds)),
        float(values.get("max", max_wait_seconds)),
        values.get("legacy", legacy_reply_backoff_seconds),
    )


def _should_preempt_current_batch(entry: dict[str, Any], *, immediate_flush: bool) -> bool:
    if not immediate_flush or not bool(entry.get("processing")):
        return False
    state = entry.get("active_state") if isinstance(entry.get("active_state"), dict) else {}
    if any(bool(state.get(key, False)) for key in ("reply_delivery_started", "reply_delivery_confirmed", "reply_delivery_complete", "delivery_unknown")):
        return False
    return bool(entry.get("current_is_random_chat"))


async def run_buffer_timer(
    key: str,
    bot: Any,
    *,
    msg_buffer: Dict[str, Dict[str, Any]],
    process_response_logic: Callable[[Any, Any, Dict[str, Any]], Any],
    message_event_cls: Any,
    message_cls: Any,
    message_segment_cls: Any,
    logger: Any,
    finished_exception_cls: Any = None,
    delay: float = 0.0,
    response_timeout_seconds: float = _PROCESS_RESPONSE_TIMEOUT_SECONDS,
    batch_base_wait_seconds: float = _BATCH_BASE_WAIT_SECONDS,
    batch_min_wait_seconds: float = _BATCH_MIN_WAIT_SECONDS,
    batch_max_wait_seconds: float = _BATCH_MAX_WAIT_SECONDS,
    legacy_reply_backoff_seconds: float | None = None,
    concurrency_controller: ReplyConcurrencyController | None = None,
    user_policy_gate: Any = None,
    timing_resolver: Callable[[], Any] | None = None,
) -> None:
    started_at = time.monotonic()
    await asyncio.sleep(max(0.0, float(delay or 0.0)))
    # Cancellation is normally a lifecycle/shutdown signal.  It becomes a
    # replay signal only when this exact generation was explicitly superseded
    # by the direct-turn preemption path below.
    cancelled_without_preempt = False
    entry = msg_buffer.get(key)
    if not isinstance(entry, dict):
        return

    if entry.get("processing"):
        if entry.get("pending_items"):
            entry["pending_ready"] = True
        entry["timer_task"] = None
        return

    all_items = list(entry.get("items") or [])
    # A generation owns at most eight arrivals.  Later arrivals remain FIFO in
    # the next generation instead of silently evicting the earliest speakers.
    items = all_items[:_MAX_BATCH_EVENTS]
    overflow_items = all_items[_MAX_BATCH_EVENTS:]
    if user_policy_gate is not None and items:
        allowed = await asyncio.gather(
            *(
                user_policy_gate.allows_current(item.get("event"))
                for item in items
            ),
            return_exceptions=True,
        )
        allowed = [value if isinstance(value, bool) else False for value in allowed]
        for item, is_allowed in zip(items, allowed):
            if is_allowed:
                continue
            try:
                await asyncio.to_thread(
                    _record_recovery_failure,
                    bot=bot,
                    event=item.get("event"),
                    state=dict(item.get("state") or {}),
                    failure_stage="permission_revoked",
                    # A policy revocation is not evidence that delivery began,
                    # but this queue has no dedicated policy class.  Reuse the
                    # existing quarantine/manual-confirm boundary so an
                    # operator must explicitly confirm it was not sent before
                    # any recovery can be claimed.
                    failure_class="delivery_unknown",
                )
            except Exception:
                pass
        items = [item for item, is_allowed in zip(items, allowed) if is_allowed]
    # Do not mutate overflow ownership before an awaitable policy check: a
    # new direct arrival may cancel/reorder this generation while the gate is
    # pending.  Once it returns, derive the tail from the live FIFO source.
    live_items = list(entry.get("items") or [])
    overflow_items = live_items[_MAX_BATCH_EVENTS:]
    if overflow_items:
        entry["pending_items"] = overflow_items + list(entry.get("pending_items") or [])
        entry["queued_items"] = list(entry["pending_items"])
        entry["pending_started_at"] = float(overflow_items[0].get("received_at", time.monotonic()) or time.monotonic())
        entry["pending_ready"] = True
    if not items:
        if entry.get("pending_items"):
            _promote_pending_batch(entry)
            _schedule_timer(
                entry=entry,
                key=key,
                bot=bot,
                wait_seconds=0.0,
                start_buffer_timer=lambda _key, _bot, _delay: asyncio.create_task(
                    run_buffer_timer(
                        _key,
                        _bot,
                        msg_buffer=msg_buffer,
                        process_response_logic=process_response_logic,
                        message_event_cls=message_event_cls,
                        message_cls=message_cls,
                        message_segment_cls=message_segment_cls,
                        logger=logger,
                        finished_exception_cls=finished_exception_cls,
                        delay=_delay,
                        response_timeout_seconds=response_timeout_seconds,
                        batch_base_wait_seconds=batch_base_wait_seconds,
                        batch_min_wait_seconds=batch_min_wait_seconds,
                        batch_max_wait_seconds=batch_max_wait_seconds,
                        legacy_reply_backoff_seconds=legacy_reply_backoff_seconds,
                        concurrency_controller=concurrency_controller,
                        user_policy_gate=user_policy_gate,
                    )
                ),
            )
            return
        _pop_buffer_entry(msg_buffer, key)
        return

    entry["processing"] = True
    entry["active_items"] = list(items)
    entry["delivery_state"] = "generating"
    entry["active_task"] = asyncio.current_task()
    entry["processing_started_at"] = time.monotonic()
    entry["timer_task"] = None
    entry["next_fire_at"] = 0.0
    entry["current_generation"] = int(entry.get("current_generation", 0) or 0) + 1
    current_generation = int(entry["current_generation"])
    entry["active_generation_token"] = current_generation
    entry["newer_batch_for_current"] = False
    entry["interrupt_requested_generation"] = 0
    entry["items"] = []
    entry["batch_started_at"] = 0.0

    trigger_item, trigger_type = _select_batch_trigger(items)
    selected_event = trigger_item.get("event")
    if selected_event is None:
        entry["processing"] = False
        _pop_buffer_entry(msg_buffer, key)
        return

    state = dict(trigger_item.get("state") or {})
    entry["active_state"] = state
    events = [item.get("event") for item in items if isinstance(item.get("event"), message_event_cls)]
    state["buffer_trace_diagnostics"] = _take_buffer_diagnostics(entry, generation=current_generation, wait_ms=int((time.monotonic() - started_at) * 1000), dequeue_count=len(events), queued_count=len(overflow_items))
    if not events:
        entry["processing"] = False
        _pop_buffer_entry(msg_buffer, key)
        return

    combined_message = None
    try:
        combined_message = _build_combined_message(
            items,
            message_cls=message_cls,
            message_segment_cls=message_segment_cls,
        )
    except Exception as exc:
        logger.warning(f"拟人插件：拼接消息构建失败，回退单条处理: {exc}")

    serialized_items = [
        _serialize_batched_event(item, selected_event=selected_event)
        for item in items
    ]
    repeat_clusters = _build_repeat_clusters(items)
    if combined_message is not None:
        state["concatenated_message"] = combined_message
    state["merged_event_context"] = {
        "event_count": len(events),
        "selected_event_index": max(0, events.index(selected_event)),
    }
    state["batched_events"] = serialized_items
    state["turn_media_context"] = serialize_turn_media(media_from_batched_events(serialized_items))
    state["batch_trigger"] = {
        "type": trigger_type,
        "message_id": str(getattr(selected_event, "message_id", "") or "").strip(),
        "user_id": str(getattr(selected_event, "user_id", "") or "").strip(),
    }
    state["repeat_clusters"] = repeat_clusters
    state["batch_event_count"] = len(events)
    state["batch_session_key"] = key
    state["batch_runtime_ref"] = {
        "entry": entry,
        "generation": current_generation,
    }
    interrupted_context = attach_interrupted_reply_context(state, entry)
    if interrupted_context is not None:
        _note_buffer_diagnostic(
            entry,
            "consume_interrupted_drafts",
            count=len(interrupted_context.get("segments") or []),
        )
    state["turn_generation_id"] = _next_turn_generation(key)
    entry["current_trigger_type"] = trigger_type
    entry["current_is_random_chat"] = bool(state.get("is_random_chat", False))
    timeout_seconds = min(
        HARD_TURN_TIMEOUT_SECONDS,
        max(30.0, float(response_timeout_seconds or _PROCESS_RESPONSE_TIMEOUT_SECONDS)),
    )
    first_received_at = min(
        (
            float(item.get("received_at", 0.0) or 0.0)
            for item in items
            if float(item.get("received_at", 0.0) or 0.0) > 0
        ),
        default=time.monotonic(),
    )
    turn_deadline = attach_turn_deadline(
        state,
        timeout_seconds=timeout_seconds,
        started_at=first_received_at,
    )

    try:
        if concurrency_controller is None:
            await asyncio.wait_for(
                process_response_logic(bot, selected_event, state),
                timeout=max(0.001, float(state["response_deadline"]) - time.monotonic()),
            )
        else:
            async with concurrency_controller.buffered_turn(
                key,
                deadline=float(state["response_deadline"]),
            ) as commit_lock:
                state["reply_commit_lock"] = commit_lock
                await asyncio.wait_for(
                    process_response_logic(bot, selected_event, state),
                    timeout=max(0.001, float(state["response_deadline"]) - time.monotonic()),
                )
    except ReplyAdmissionTimeout as exc:
        logger.warning(
            f"拟人插件：会话 {key} 缓冲回复排队超时，已静默放弃。"
        )
        _record_reply_admission_timeout(
            bot=bot,
            event=selected_event,
            state=state,
            session_key=key,
            wait_ms=exc.wait_ms,
            mode="buffered",
        )
        _note_buffer_diagnostic(entry, "failure_admission_timeout", count=len(items))
        _record_buffer_failure_trace(state, "admission_timeout", count=len(items), generation=current_generation, wait_ms=exc.wait_ms)
    except asyncio.TimeoutError:
        logger.warning(
            f"拟人插件：会话 {key} 单轮回复超时（>{timeout_seconds:.0f}s），已放弃旧批次。"
        )
        await _handle_reply_timeout(
            bot=bot,
            event=selected_event,
            state=state,
            session_key=key,
            timeout_seconds=timeout_seconds,
            logger=logger,
        )
        _note_buffer_diagnostic(entry, "failure_generation_timeout", count=len(items))
        _record_buffer_failure_trace(state, "generation_timeout", count=len(items), generation=current_generation, wait_ms=int((time.monotonic() - started_at) * 1000))
    except asyncio.CancelledError:
        if int(entry.get("superseded_generation", 0) or 0) >= current_generation:
            logger.info(f"拟人插件：会话 {key} 当前批次已被新的直呼消息抢占。")
        else:
            cancelled_without_preempt = True
        raise
    except Exception as exc:
        if finished_exception_cls and isinstance(exc, finished_exception_cls):
            logger.debug("拟人插件：拼接消息处理提前结束（FinishedException）")
        else:
            delivery_state = (
                "complete"
                if state.get("reply_delivery_complete")
                else "partial"
                if state.get("reply_delivery_confirmed")
                else "dispatching"
                if state.get("reply_delivery_started")
                else "not_started"
            )
            logger.error(
                f"拟人插件：处理拼接消息失败，保持静默: "
                f"type={type(exc).__name__} delivery_state={delivery_state}"
            )
            try:
                failure_class = (
                    "delivery_partial"
                    if state.get("reply_delivery_confirmed")
                    else "delivery_unknown"
                    if state.get("reply_delivery_started")
                    else "generation_failed_before_send"
                )
                await asyncio.to_thread(
                    _record_recovery_failure,
                    bot=bot,
                    event=selected_event,
                    state=state,
                    failure_stage="reply_processing_failed",
                    failure_class=failure_class,
                )
                _note_buffer_diagnostic(entry, "failure_processing", count=len(items))
            except Exception:
                pass
            _record_buffer_failure_trace(state, "processing_failure", count=len(items), generation=current_generation, wait_ms=int((time.monotonic() - started_at) * 1000))
    finally:
        # A superseded task must not erase queue state prepared by a newer
        # generation.  The owning task still performs the one safe hand-off
        # from a pre-send random batch to its replay queue.
        if int(entry.get("active_generation_token", 0) or 0) != current_generation:
            if (
                int(entry.get("superseded_generation", 0) or 0) >= current_generation
                and entry.get("pending_items")
            ):
                # This is the one cancellation that has a proven no-send
                # boundary.  The cancelled owner hands its FIFO snapshot to a
                # fresh timer; ordinary shutdown cancellation never reaches
                # this branch.
                entry["processing"] = False
                entry["active_task"] = None
                entry["active_items"] = []
                _promote_pending_batch(entry)
                _schedule_timer(entry=entry, key=key, bot=bot, wait_seconds=0.0, start_buffer_timer=start_buffer_timer)
            return
        entry["processing"] = False
        entry["active_task"] = None
        entry["active_items"] = []
        entry["delivery_state"] = (
            "complete" if state.get("reply_delivery_complete") else
            "partial" if state.get("reply_delivery_confirmed") else
            "unknown" if state.get("reply_delivery_started") else "not_started"
        )
        entry["processing_started_at"] = 0.0
        entry["current_trigger_type"] = ""
        entry["current_is_random_chat"] = False
        if bool(state.get("reply_delivery_started", False)):
            _note_session_reply(key)
        await _reset_attention_after_confirmed(state, key)
        if cancelled_without_preempt:
            # Shutdown/task cancellation is not a delivery-safe replay
            # signal.  Keep current entry state for lifecycle cleanup but do
            # not schedule a new generation from pending/overflow items.
            return
        if entry.get("pending_items"):
            entry["queued_items"] = list(entry.get("pending_items") or [])
            if entry.get("pending_ready"):
                _promote_pending_batch(entry)
                _schedule_timer(
                    entry=entry,
                    key=key,
                    bot=bot,
                    wait_seconds=0.0,
                    start_buffer_timer=lambda _key, _bot, _delay: asyncio.create_task(
                        run_buffer_timer(
                            _key,
                            _bot,
                            msg_buffer=msg_buffer,
                            process_response_logic=process_response_logic,
                            message_event_cls=message_event_cls,
                            message_cls=message_cls,
                            message_segment_cls=message_segment_cls,
                            logger=logger,
                            finished_exception_cls=finished_exception_cls,
                            delay=_delay,
                            response_timeout_seconds=response_timeout_seconds,
                            batch_base_wait_seconds=batch_base_wait_seconds,
                            batch_min_wait_seconds=batch_min_wait_seconds,
                            batch_max_wait_seconds=batch_max_wait_seconds,
                            legacy_reply_backoff_seconds=legacy_reply_backoff_seconds,
                            concurrency_controller=concurrency_controller,
                            user_policy_gate=user_policy_gate,
                        )
                    ),
                )
            else:
                effective_base, effective_min, effective_max, effective_legacy = _entry_timing(
                    entry,
                    base_wait_seconds=batch_base_wait_seconds,
                    min_wait_seconds=batch_min_wait_seconds,
                    max_wait_seconds=batch_max_wait_seconds,
                    legacy_reply_backoff_seconds=legacy_reply_backoff_seconds,
                )
                remaining = _schedule_debounce_wait(
                    first_at=float(entry.get("pending_started_at", 0.0) or 0.0),
                    last_at=float(entry.get("last_item_at", 0.0) or 0.0),
                    last_reply_at=_session_last_reply_at(key),
                    base_wait_seconds=effective_base,
                    min_wait_seconds=effective_min,
                    max_wait_seconds=effective_max,
                    legacy_reply_backoff_seconds=effective_legacy,
                    immediate=False,
                )
                _promote_pending_batch(entry)
                _schedule_timer(
                    entry=entry,
                    key=key,
                    bot=bot,
                    wait_seconds=remaining,
                    start_buffer_timer=lambda _key, _bot, _delay: asyncio.create_task(
                        run_buffer_timer(
                            _key,
                            _bot,
                            msg_buffer=msg_buffer,
                            process_response_logic=process_response_logic,
                            message_event_cls=message_event_cls,
                            message_cls=message_cls,
                            message_segment_cls=message_segment_cls,
                            logger=logger,
                            finished_exception_cls=finished_exception_cls,
                            delay=_delay,
                            response_timeout_seconds=response_timeout_seconds,
                            batch_base_wait_seconds=batch_base_wait_seconds,
                            batch_min_wait_seconds=batch_min_wait_seconds,
                            batch_max_wait_seconds=batch_max_wait_seconds,
                            legacy_reply_backoff_seconds=legacy_reply_backoff_seconds,
                            concurrency_controller=concurrency_controller,
                            user_policy_gate=user_policy_gate,
                        )
                    ),
                )
        elif not entry.get("items"):
            _pop_buffer_entry(msg_buffer, key)


async def handle_reply_event(
    bot: Any,
    event: Any,
    state: Dict[str, Any],
    *,
    poke_event_cls: Any,
    message_event_cls: Any,
    group_message_event_cls: Any,
    process_response_logic: Callable[[Any, Any, Dict[str, Any]], Any],
    msg_buffer: Dict[str, Dict[str, Any]],
    start_buffer_timer: Callable[[str, Any, float], Any],
    logger: Any,
    concurrency_controller: ReplyConcurrencyController | None = None,
    response_timeout_seconds: float = _PROCESS_RESPONSE_TIMEOUT_SECONDS,
    batch_base_wait_seconds: float = _BATCH_BASE_WAIT_SECONDS,
    batch_min_wait_seconds: float = _BATCH_MIN_WAIT_SECONDS,
    batch_max_wait_seconds: float = _BATCH_MAX_WAIT_SECONDS,
    legacy_reply_backoff_seconds: float | None = None,
    finished_exception_cls: Any = None,
    user_policy_gate: Any = None,
    timing_resolver: Callable[[], Any] | None = None,
) -> None:
    policy_allowed = True
    if user_policy_gate is not None:
        try:
            policy_allowed = bool(await user_policy_gate.allows_current(event))
        except Exception:
            policy_allowed = False
    if not policy_allowed:
        # The policy can change after enqueue.  Preserve the inbound event for
        # operator diagnosis/recovery, but never invent a replay of any reply.
        try:
            await asyncio.to_thread(
                _record_recovery_failure,
                bot=bot,
                event=event,
                state=state,
                failure_stage="permission_revoked",
                # See the per-item buffered gate above: revoked policy input
                # is quarantined rather than replayed automatically.
                failure_class="delivery_unknown",
            )
        except Exception:
            pass
        return
    if isinstance(event, poke_event_cls):
        if concurrency_controller is None:
            await process_response_logic(bot, event, state)
            return
        bot_self_id = str(getattr(bot, "self_id", "") or "")
        group_id = str(getattr(event, "group_id", "") or "")
        user_id = str(getattr(event, "user_id", "") or "")
        scope = group_id or f"private_{user_id}"
        session_key = f"{bot_self_id}:{scope}" if bot_self_id else scope
        direct_state = dict(state)
        direct_state["batch_session_key"] = session_key
        direct_state["reply_required"] = True
        timeout_seconds = min(
            HARD_TURN_TIMEOUT_SECONDS,
            max(30.0, float(response_timeout_seconds or _PROCESS_RESPONSE_TIMEOUT_SECONDS)),
        )
        attach_turn_deadline(
            direct_state,
            timeout_seconds=timeout_seconds,
            started_at=time.monotonic(),
        )
        try:
            async with concurrency_controller.direct_turn(
                session_key,
                deadline=float(direct_state["response_deadline"]),
            ) as commit_lock:
                direct_state["reply_commit_lock"] = commit_lock
                await asyncio.wait_for(
                    process_response_logic(bot, event, direct_state),
                    timeout=max(
                        0.001,
                        float(direct_state["response_deadline"]) - time.monotonic(),
                    ),
                )
        except ReplyAdmissionTimeout as exc:
            logger.warning(f"拟人插件：会话 {session_key} poke turn 排队超时，已静默放弃。")
            _record_reply_admission_timeout(
                bot=bot,
                event=event,
                state=direct_state,
                session_key=session_key,
                wait_ms=exc.wait_ms,
                mode="direct",
            )
        except asyncio.TimeoutError:
            logger.warning(
                f"拟人插件：会话 {session_key} poke turn 超时（>{timeout_seconds:.0f}s），已终止本轮。"
            )
            await _handle_reply_timeout(
                bot=bot,
                event=event,
                state=direct_state,
                session_key=session_key,
                timeout_seconds=timeout_seconds,
                logger=logger,
            )
        return

    if not isinstance(event, message_event_cls):
        return

    # A config hot update affects a newly enqueued/reordered generation only;
    # an already sleeping timer is intentionally left alone until new input.
    if callable(timing_resolver):
        try:
            timing = timing_resolver()
            batch_base_wait_seconds = float(getattr(timing, "base_wait_seconds", batch_base_wait_seconds))
            batch_min_wait_seconds = float(getattr(timing, "min_wait_seconds", batch_min_wait_seconds))
            batch_max_wait_seconds = float(getattr(timing, "max_wait_seconds", batch_max_wait_seconds))
            legacy_reply_backoff_seconds = getattr(timing, "legacy_reply_backoff_seconds", legacy_reply_backoff_seconds)
        except Exception:
            pass

    bot_self_id = str(getattr(bot, "self_id", "") or "")
    session_key = _session_key(
        event,
        group_message_event_cls=group_message_event_cls,
        bot_self_id=bot_self_id,
    )
    delay = _batch_delay(event, group_message_event_cls=group_message_event_cls)
    is_private_session = not isinstance(event, group_message_event_cls)
    received_monotonic_at = time.monotonic()
    is_direct_mention = _is_direct_mention(event, bot_self_id)
    is_reply_to_bot = _is_reply_to_bot(
        event,
        bot_self_id,
        message_target=str(state.get("message_target", "") or ""),
    )
    targets_bot = normalize_message_target_for_review(state.get("message_target")) == "bot"
    reply_required = bool(
        not state.get("is_random_chat", False)
        and (is_private_session or is_direct_mention or is_reply_to_bot or targets_bot)
    )
    state["reply_required"] = reply_required
    state.setdefault("received_wall_at", time.time())
    event_plain_text = _extract_plain_text(event)
    shared_contents, forward_bundle = _extract_shared_content_context(event)
    if shared_contents:
        state["shared_contents"] = shared_contents
        state["shared_content_trust"] = "untrusted_data_only"
    if forward_bundle is not None:
        state["forward_bundle"] = forward_bundle
        state["forward_content_unavailable"] = bool(forward_bundle.get("unavailable_nodes"))
    event_media = extract_turn_media_from_event(event, current_origin="current")
    # Keep a process-local, bounded copy for a later group follow-up such as
    # “@Bot 你觉得我刚发的图怎么样”。  The resolver performs the semantic
    # choice later; this is deliberately only source/timing/media provenance.
    if not is_private_session:
        declared_source_kind = str(
            state.get("source_kind")
            or peer_bot_source_kind(event)
            or getattr(event, "_personification_peer_bot_source_kind", "")
            or getattr(event, "source_kind", "")
            or getattr(event, "message_source_kind", "")
            or "user"
        ).strip().lower()
        if has_runtime_command_prefix(event_plain_text):
            declared_source_kind = "plugin_command"
        if declared_source_kind in {"peer_bot_reply", "peer_bot_candidate", "peer_bot_command", "bot_reply", "plugin", "plugin_command", "system"}:
            declared_source_kind = "non_human"
        get_group_followup_referent_resolver().remember(
            bot_self_id=str(getattr(bot, "self_id", "") or ""),
            group_id=str(getattr(event, "group_id", "") or ""),
            event=event,
            media=event_media,
            source_kind=declared_source_kind,
        )
    if event_media and not event_plain_text:
        _remember_recent_media(
            session_key=session_key,
            user_id=str(getattr(event, "user_id", "") or ""),
            values=event_media,
            now=time.monotonic(),
        )
    immediate_flush = reply_required
    # Group turns, including the very first @/reply cue, always share the
    # session buffer lane.  Private messages retain the direct-turn path.
    if immediate_flush and is_private_session and concurrency_controller is not None:
        entry = msg_buffer.get(session_key)
        if isinstance(entry, dict):
            entry["timing"] = {
                "base": batch_base_wait_seconds,
                "min": batch_min_wait_seconds,
                "max": batch_max_wait_seconds,
                "legacy": legacy_reply_backoff_seconds,
            }
            # A waiting group batch and an explicit cue form one ordered turn.
            # Do not bypass it through the private/direct lane.
            if not entry.get("processing") and entry.get("items"):
                queued = {
                    "event": event,
                    "state": dict(state),
                    "is_direct_mention": is_direct_mention,
                    "is_reply_to_bot": is_reply_to_bot,
                    "received_at": received_monotonic_at,
                    "dedupe_key": _stable_item_key(session_key, event, received_monotonic_at),
                }
                if not any(item.get("dedupe_key") == queued["dedupe_key"] for item in entry.get("items", []) if isinstance(item, dict)):
                    entry["items"].append(queued)
                    _note_buffer_diagnostic(entry, "enqueue_direct", count=1)
                entry["queued_items"] = list(entry["items"])
                entry["pending_ready"] = True
                _schedule_timer(entry=entry, key=session_key, bot=bot, wait_seconds=0.0, start_buffer_timer=start_buffer_timer)
                return
            if entry.get("processing"):
                direct_item = {
                    "event": event,
                    "state": dict(state),
                    "is_direct_mention": is_direct_mention,
                    "is_reply_to_bot": is_reply_to_bot,
                    "received_at": received_monotonic_at,
                    "dedupe_key": _stable_item_key(session_key, event, received_monotonic_at),
                }
                if _should_preempt_current_batch(entry, immediate_flush=True):
                    # Return the pre-send snapshot exactly once, followed by
                    # already queued arrivals and the explicit cue.
                    # This is the sole replayable case: random generation is
                    # known pre-send.  Requeue the full inbound snapshot in
                    # arrival order; no delivery can be duplicated.
                    replay = [*list(entry.get("active_items") or []), *list(entry.get("pending_items") or []), direct_item]
                    seen: set[str] = set()
                    entry["pending_items"] = [item for item in replay if isinstance(item, dict) and not (item.get("dedupe_key") in seen or seen.add(str(item.get("dedupe_key") or "")))]
                    entry["queued_items"] = list(entry["pending_items"])
                    entry["pending_ready"] = True
                    _note_buffer_diagnostic(entry, "preempt_requeue", count=len(entry["pending_items"]))
                    entry["newer_batch_for_current"] = True
                    entry["superseded_generation"] = max(int(entry.get("superseded_generation", 0) or 0), int(entry.get("current_generation", 0) or 0))
                    active_task = entry.get("active_task")
                    if active_task and not active_task.done():
                        active_task.cancel()
                    return
                # Once dispatch has begun (or the current turn is itself a
                # must-reply turn), it is unsafe to cancel: send outcome may
                # already be partial or unknown.  Keep the direct cue FIFO in
                # the next generation and let the active turn finalize it.
                queued_keys = {
                    str(item.get("dedupe_key") or "")
                    for item in [*(entry.get("active_items") or []), *(entry.get("pending_items") or [])]
                    if isinstance(item, dict)
                }
                if direct_item["dedupe_key"] not in queued_keys:
                    entry.setdefault("pending_items", []).append(direct_item)
                    entry["queued_items"] = list(entry.get("pending_items") or [])
                entry["pending_ready"] = True
                if not float(entry.get("pending_started_at", 0.0) or 0.0):
                    entry["pending_started_at"] = received_monotonic_at
                entry["last_item_at"] = received_monotonic_at
                if request_cooperative_reply_interruption(entry):
                    _note_buffer_diagnostic(entry, "interrupt_after_confirmed", count=1)
                return
        direct_state = dict(state)
        direct_state["batch_session_key"] = session_key
        direct_state["turn_generation_id"] = _next_turn_generation(session_key)
        direct_state["batched_events"] = []
        direct_media = await resolve_onebot_quoted_media_refs(event, bot)
        recent_media: list[TurnMediaRef] = []
        if not any(item.kind in {"video", "audio"} for item in direct_media) and event_plain_text:
            recent_media = _recent_media_for_followup(
                session_key=session_key,
                user_id=str(getattr(event, "user_id", "") or ""),
                now=time.monotonic(),
            )
            direct_media.extend(recent_media)
        media_reference_unavailable = bool(
            event_plain_text
            and extract_reply_message_id(event)
            and not direct_media
        )
        direct_state["media_reference_unavailable"] = media_reference_unavailable
        direct_state["batch_event_count"] = 1 + int(bool(recent_media))
        direct_state["turn_media_context"] = serialize_turn_media(
            coerce_turn_media(direct_media)
        )
        try:
            from ..core import reply_turn_trace

            media_counts = {
                "current": sum(item.origin == "current" for item in direct_media),
                "quoted": sum(item.origin == "quoted" for item in direct_media),
                "recent": len(recent_media),
                "video": sum(item.kind == "video" for item in direct_media),
                "audio": sum(item.kind == "audio" for item in direct_media),
            }
            if any(media_counts.values()) or media_reference_unavailable:
                reply_turn_trace.record_stage(
                    key="turn_media_resolved",
                    label="轮次媒体解析",
                    status="warn" if media_reference_unavailable else "ok",
                    detail=(
                        " ".join(f"{key}={value}" for key, value in media_counts.items())
                        + f" reference_unavailable={str(media_reference_unavailable).lower()}"
                    ),
                    hint="引用媒体通过 message_id 回查；近期媒体只在同一会话与同一发送者内承接",
                )
        except Exception:
            pass
        if is_private_session:
            await _await_private_direct_backoff(
                session_key,
                debounce_seconds=batch_base_wait_seconds,
                max_wait_seconds=batch_max_wait_seconds,
                backoff_seconds=float(legacy_reply_backoff_seconds or 0.0),
            )
        timeout_seconds = min(
            HARD_TURN_TIMEOUT_SECONDS,
            max(30.0, float(response_timeout_seconds or _PROCESS_RESPONSE_TIMEOUT_SECONDS)),
        )
        attach_turn_deadline(
            direct_state,
            timeout_seconds=timeout_seconds,
            started_at=received_monotonic_at,
        )
        try:
            async with concurrency_controller.direct_turn(
                session_key,
                deadline=float(direct_state["response_deadline"]),
            ) as commit_lock:
                direct_state["reply_commit_lock"] = commit_lock
                await asyncio.wait_for(
                    process_response_logic(bot, event, direct_state),
                    timeout=max(
                        0.001,
                        float(direct_state["response_deadline"]) - time.monotonic(),
                    ),
                )
        except ReplyAdmissionTimeout as exc:
            logger.warning(f"拟人插件：会话 {session_key} direct turn 排队超时，已静默放弃。")
            _record_reply_admission_timeout(
                bot=bot,
                event=event,
                state=direct_state,
                session_key=session_key,
                wait_ms=exc.wait_ms,
                mode="direct",
            )
        except asyncio.TimeoutError:
            logger.warning(
                f"拟人插件：会话 {session_key} direct turn 超时（>{timeout_seconds:.0f}s），已终止本轮。"
            )
            await _handle_reply_timeout(
                bot=bot,
                event=event,
                state=direct_state,
                session_key=session_key,
                timeout_seconds=timeout_seconds,
                logger=logger,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if finished_exception_cls and isinstance(exc, finished_exception_cls):
                logger.debug("拟人插件：direct turn 提前结束（FinishedException）")
            else:
                delivery_state = (
                    "complete"
                    if direct_state.get("reply_delivery_complete")
                    else "partial"
                    if direct_state.get("reply_delivery_confirmed")
                    else "dispatching"
                    if direct_state.get("reply_delivery_started")
                    else "not_started"
                )
                logger.error(
                    f"拟人插件：会话 {session_key} direct turn 处理失败，保持静默: "
                    f"type={type(exc).__name__} delivery_state={delivery_state}"
                )
                try:
                    failure_class = (
                        "delivery_partial"
                        if direct_state.get("reply_delivery_confirmed")
                        else "delivery_unknown"
                        if direct_state.get("reply_delivery_started")
                        else "generation_failed_before_send"
                    )
                    await asyncio.to_thread(
                        _record_recovery_failure,
                        bot=bot,
                        event=event,
                        state=direct_state,
                        failure_stage="reply_processing_failed",
                        failure_class=failure_class,
                    )
                except Exception:
                    pass
        if is_private_session and bool(direct_state.get("reply_delivery_started", False)):
            _note_session_reply(session_key)
        await _reset_attention_after_confirmed(direct_state, session_key)
        return
    entry = msg_buffer.setdefault(session_key, _new_entry(delay))
    _retain_buffer_entry(
        entry=entry,
        key=session_key,
        concurrency_controller=concurrency_controller,
    )
    entry["delay"] = delay
    entry["timing"] = {
        "base": batch_base_wait_seconds,
        "min": batch_min_wait_seconds,
        "max": batch_max_wait_seconds,
        "legacy": legacy_reply_backoff_seconds,
    }
    now_ts = received_monotonic_at
    item = {
        "event": event,
        "state": dict(state),
        "is_direct_mention": is_direct_mention,
        "is_reply_to_bot": is_reply_to_bot,
        "received_at": now_ts,
    }
    item["dedupe_key"] = _stable_item_key(session_key, event, now_ts)
    if any(existing.get("dedupe_key") == item["dedupe_key"] for existing in [*(entry.get("items") or []), *(entry.get("pending_items") or [])] if isinstance(existing, dict)):
        return
    if concurrency_controller is not None:
        item["state"]["reply_commit_lock"] = concurrency_controller.commit_lock(session_key)

    _note_buffer_diagnostic(entry, "enqueue", count=1)

    if entry.get("processing"):
        # The buffered lane owns every group turn.  Only a demonstrably
        # pre-send random generation may be superseded; return its snapshot
        # before queued arrivals and the new explicit cue, exactly once.
        if immediate_flush and _should_preempt_current_batch(entry, immediate_flush=True):
            replay = [*list(entry.get("active_items") or []), *list(entry.get("pending_items") or []), item]
            seen: set[str] = set()
            entry["pending_items"] = [
                candidate for candidate in replay
                if isinstance(candidate, dict)
                and not (str(candidate.get("dedupe_key") or "") in seen or seen.add(str(candidate.get("dedupe_key") or "")))
            ]
            entry["queued_items"] = list(entry["pending_items"])
            entry["pending_ready"] = True
            entry["newer_batch_for_current"] = True
            entry["superseded_generation"] = max(
                int(entry.get("superseded_generation", 0) or 0),
                int(entry.get("current_generation", 0) or 0),
            )
            _note_buffer_diagnostic(entry, "preempt_requeue", count=len(entry["pending_items"]))
            active_task = entry.get("active_task")
            if active_task and not active_task.done():
                active_task.cancel()
            return
        pending_items = list(entry.get("pending_items") or [])
        pending_items.append(item)
        entry["pending_items"] = _trim_items(pending_items)
        if not float(entry.get("pending_started_at", 0.0) or 0.0):
            entry["pending_started_at"] = now_ts
        entry["last_item_at"] = now_ts
        if request_cooperative_reply_interruption(entry):
            _note_buffer_diagnostic(entry, "interrupt_after_confirmed", count=1)
        if immediate_flush:
            entry["pending_ready"] = True
        dynamic_base_wait = max(
            batch_min_wait_seconds,
            min(
                batch_max_wait_seconds,
                float(state.get("attention_wait_seconds", batch_base_wait_seconds) or batch_base_wait_seconds),
            ),
        )
        wait_seconds = _schedule_debounce_wait(
            first_at=float(entry.get("pending_started_at", now_ts) or now_ts),
            last_at=now_ts,
            last_reply_at=_session_last_reply_at(session_key),
            base_wait_seconds=dynamic_base_wait,
            min_wait_seconds=batch_min_wait_seconds,
            max_wait_seconds=batch_max_wait_seconds,
            legacy_reply_backoff_seconds=legacy_reply_backoff_seconds,
            immediate=bool(entry.get("pending_ready")),
            now=now_ts,
        )
        _schedule_timer(
            entry=entry,
            key=session_key,
            bot=bot,
            wait_seconds=wait_seconds,
            start_buffer_timer=start_buffer_timer,
        )
        logger.debug(f"拟人插件：会话 {session_key} 正在处理中，新消息进入下一批。")
        return

    current_items = list(entry.get("items") or [])
    first_in_batch = not float(entry.get("batch_started_at", 0.0) or 0.0)
    current_items.append(item)
    entry["items"] = _trim_items(current_items)
    if first_in_batch:
        entry["batch_started_at"] = now_ts
    entry["last_item_at"] = now_ts

    dynamic_base_wait = max(
        batch_min_wait_seconds,
        min(
            batch_max_wait_seconds,
            float(state.get("attention_wait_seconds", batch_base_wait_seconds) or batch_base_wait_seconds),
        ),
    )
    wait_seconds = _schedule_debounce_wait(
        first_at=float(entry.get("batch_started_at", now_ts) or now_ts),
        last_at=now_ts,
        last_reply_at=_session_last_reply_at(session_key),
        base_wait_seconds=dynamic_base_wait,
        min_wait_seconds=batch_min_wait_seconds,
        max_wait_seconds=batch_max_wait_seconds,
        legacy_reply_backoff_seconds=legacy_reply_backoff_seconds,
        immediate=bool(immediate_flush or len(entry["items"]) >= _MAX_BATCH_EVENTS),
        now=now_ts,
    )
    _schedule_timer(
        entry=entry,
        key=session_key,
        bot=bot,
        wait_seconds=wait_seconds,
        start_buffer_timer=start_buffer_timer,
    )
    logger.debug(f"拟人插件：已缓冲会话 {session_key} 的消息，等待后续...")
