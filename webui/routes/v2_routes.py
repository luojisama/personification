from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from ...core.pagination import build_page, normalize_pagination
from ...core.reply_recovery_queue import ReplyRecoveryQueue, RecoveryItem
from ...core import reply_turn_trace
from ...core.route_capabilities import DEFAULT_ROUTE_CAPABILITY_REGISTRY
from ...core.runtime_events import get_runtime_event_bus
from ...core.runtime_events import publish_runtime_event
from ...core.visible_output import guard_visible_text
from ..deps import AdminIdentity, require_admin


_ROUTE_PROBE_TASKS: dict[str, dict[str, Any]] = {}


def _iso(value: Any) -> str | None:
    try:
        timestamp = float(value or 0)
    except (TypeError, ValueError):
        return None
    if timestamp <= 0:
        return None
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()


def _recovery_summary(item: RecoveryItem) -> dict[str, Any]:
    return {
        "id": item.id,
        "bot_id": item.bot_id,
        "conversation_kind": item.conversation_kind,
        "conversation_id": item.conversation_id,
        "original_message_id": item.original_message_id,
        "text_summary": item.normalized_text[:240],
        "safe_summary": item.normalized_text[:240],
        "media": [
            {
                "kind": str(media.get("kind") or "unknown")[:16],
                "origin": str(media.get("origin") or "current")[:16],
                "safe_summary": str(media.get("safe_summary") or "")[:160],
            }
            for media in item.media_refs[:12]
        ],
        "failure_stage": item.failure_stage,
        "last_failure_stage": item.last_failure_stage,
        "failure_class": item.failure_class,
        "missing_part_indexes": list(item.missing_part_indexes),
        "route_fingerprint": item.route_fingerprint,
        "first_failure_at": item.first_failure_at,
        "last_failure_at": item.last_failure_at,
        "attempt_count": item.attempt_count,
        "attempts": item.attempt_count,
        "status": item.status,
        "expires_at": _iso(item.expires_at),
        "next_attempt_at": _iso(item.next_attempt_at),
        "trace_id": item.trace_id,
        "recoverable": item.recoverable,
        "updated_at": _iso(item.updated_at),
        "session_type": item.conversation_kind,
        "session_id": item.conversation_id,
        "message_id": item.original_message_id,
        "first_failed_at": _iso(item.first_failure_at),
        "last_failed_at": _iso(item.last_failure_at),
        "outcome_unknown": item.failure_class == "delivery_unknown",
        "missing_segments": list(item.missing_part_indexes),
    }


def _trace_summary(trace: dict[str, Any]) -> dict[str, Any]:
    process = reply_turn_trace.build_process_view(trace, logs=[])
    summary = process.get("summary") if isinstance(process, dict) else {}
    return {
        "trace_id": str(trace.get("trace_id") or ""),
        "ts": float(trace.get("ts") or 0),
        "started_at": _iso(trace.get("ts")),
        "finished_at": _iso(trace.get("ts")) if trace.get("outcome") else None,
        "session_type": str(trace.get("session_type") or ""),
        "group_id": str(trace.get("group_id") or ""),
        "user_id": str(trace.get("user_id") or ""),
        "outcome": str(trace.get("outcome") or ""),
        "diagnosis_code": str(trace.get("diagnosis_code") or ""),
        "stage_count": int(summary.get("stage_count") or 0),
        "warn_count": int(summary.get("warn_count") or 0),
        "error_count": int(summary.get("error_count") or 0),
        "user_name": str(trace.get("user_id") or "未知用户"),
        "avatar_url": None,
        "input_summary": "",
        "elapsed_ms": next(
            (
                int(item.get("duration_ms"))
                for item in reversed(process.get("items") or [])
                if isinstance(item, dict) and isinstance(item.get("duration_ms"), int)
            ),
            None,
        ),
    }


def _trace_detail(trace: dict[str, Any]) -> dict[str, Any]:
    base = _trace_summary(trace)
    process = reply_turn_trace.build_process_view(trace, logs=[])
    items = [item for item in process.get("items", []) if isinstance(item, dict)]
    inspection = process.get("agent_inspection") if isinstance(process.get("agent_inspection"), dict) else {}
    understanding = inspection.get("understanding") if isinstance(inspection.get("understanding"), dict) else {}
    tools = inspection.get("tools") if isinstance(inspection.get("tools"), list) else []
    raw_detail = trace.get("detail") if isinstance(trace.get("detail"), dict) else {}
    incoming = guard_visible_text(
        raw_detail.get("incoming_text", ""),
        surface="webui_v2_trace_input",
        allow_direct_media=False,
        enforce_role_integrity=False,
    )
    outgoing = guard_visible_text(
        raw_detail.get("outgoing_text", ""),
        surface="webui_v2_trace_output",
        allow_direct_media=True,
    )
    base["input_summary"] = incoming[:2000]
    status_map = {
        "info": "ok",
        "warning": "warn",
        "failed": "error",
    }
    return {
        **base,
        "bot_id": str(raw_detail.get("bot_id") or ""),
        "media_summary": [],
        "decision": {
            "summary": "；".join(f"{key}={value}" for key, value in list(understanding.items())[:6]),
            "action": str(understanding.get("action") or understanding.get("output") or "unknown"),
            "tier": raw_detail.get("attention_tier") if isinstance(raw_detail.get("attention_tier"), int) else None,
            "wait_seconds": raw_detail.get("attention_wait_seconds") if isinstance(raw_detail.get("attention_wait_seconds"), (int, float)) else None,
            "interest": raw_detail.get("attention_interest") if isinstance(raw_detail.get("attention_interest"), (int, float)) else None,
            "reason_code": str(raw_detail.get("attention_reason_code") or "decision_unavailable")[:96],
        },
        "stages": [
            {
                "key": str(item.get("key") or "unknown"),
                "label": str(item.get("label") or "未命名阶段"),
                "status": status_map.get(str(item.get("status") or ""), str(item.get("status") or "unknown")),
                "started_at": _iso(item.get("ts")),
                "finished_at": None,
                "duration_ms": item.get("duration_ms") if isinstance(item.get("duration_ms"), int) else None,
                "summary": str(item.get("detail") or "")[:1000],
                "detail_code": str(item.get("key") or "stage_unclassified")[:96],
                "remaining_ms": None,
            }
            for item in items[:200]
        ],
        "tools": [
            {
                "name": str(tool.get("tool") or "unknown_tool")[:96],
                "namespace": "runtime",
                "status": str(tool.get("status") or "unknown")[:32],
                "duration_ms": tool.get("duration_ms") if isinstance(tool.get("duration_ms"), int) else None,
                "argument_summary": "",
                "result_summary": str(tool.get("detail") or "")[:1000],
                "schema_hash": "",
                "detail_code": str(tool.get("stage") or "tool_unclassified")[:96],
            }
            for tool in tools[:100]
            if isinstance(tool, dict)
        ],
        "final_reply": outgoing[:6000],
        "send_status": str(raw_detail.get("outbound_delivery") or trace.get("outcome") or "unknown"),
        "history_status": str(raw_detail.get("history_status") or "unknown"),
        "recovery_ids": [],
    }


def build_v2_router(*, runtime: Any) -> APIRouter:
    del runtime
    router = APIRouter(prefix="/api/v2", tags=["v2"])

    @router.get("/traces")
    async def traces(
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=20, ge=1, le=100),
        session_type: str = Query(default=""),
        group_id: str = Query(default=""),
        user_id: str = Query(default=""),
        _: AdminIdentity = Depends(require_admin),
    ) -> dict[str, Any]:
        params = normalize_pagination(page=page, page_size=page_size)
        rows, total = await run_in_threadpool(
            reply_turn_trace.query_page,
            limit=params.page_size,
            offset=params.offset,
            session_type=session_type,
            group_id=group_id,
            user_id=user_id,
        )
        return build_page(
            [_trace_summary(row) for row in rows],
            total=total,
            params=params,
        ).to_dict()

    @router.get("/traces/{trace_id}")
    async def trace_detail(
        trace_id: str,
        _: AdminIdentity = Depends(require_admin),
    ) -> dict[str, Any]:
        trace = await run_in_threadpool(reply_turn_trace.get_trace, trace_id)
        if trace is None:
            raise HTTPException(status_code=404, detail={"code": "trace_not_found", "message": "未找到该追踪。"})
        return _trace_detail(trace)

    @router.get("/recovery")
    async def recovery_items(
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=20, ge=1, le=100),
        status: str = Query(default=""),
        failure_class: str = Query(default=""),
        conversation_kind: str = Query(default=""),
        conversation_id: str = Query(default=""),
        _: AdminIdentity = Depends(require_admin),
    ) -> dict[str, Any]:
        params = normalize_pagination(page=page, page_size=page_size)
        queue = await run_in_threadpool(ReplyRecoveryQueue)
        filters = {
            "status": status,
            "failure_class": failure_class,
            "conversation_kind": conversation_kind,
            "conversation_id": conversation_id,
        }
        try:
            items = await run_in_threadpool(
                queue.list_items,
                **filters,
                limit=params.page_size,
                offset=params.offset,
            )
            total = await run_in_threadpool(queue.count_items, **filters)
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail={"code": "recovery_filter_invalid", "message": "恢复队列筛选条件无效。"},
            ) from exc
        return build_page(
            [_recovery_summary(item) for item in items],
            total=total,
            params=params,
        ).to_dict()

    @router.get("/recovery/counts")
    async def recovery_counts(
        _: AdminIdentity = Depends(require_admin),
    ) -> dict[str, Any]:
        queue = await run_in_threadpool(ReplyRecoveryQueue)
        return {"statuses": await run_in_threadpool(queue.status_counts)}

    @router.post("/recovery-queue/{item_id}/abandon")
    async def abandon_recovery(
        item_id: int,
        _: AdminIdentity = Depends(require_admin),
    ) -> dict[str, Any]:
        queue = await run_in_threadpool(ReplyRecoveryQueue)
        changed = await run_in_threadpool(queue.abandon, (item_id,))
        return {
            "ok": changed > 0,
            "code": "recovery_abandoned" if changed else "recovery_not_changed",
            "phase": "operation_complete",
            "title": "恢复项已放弃" if changed else "恢复项未发生变化",
            "message": "该入站消息不会再由恢复 worker 消费。" if changed else "该恢复项可能已经结束或不存在。",
            "retryable": False,
            "partial": False,
            "outcome_unknown": False,
            "warnings": [],
            "steps": [],
        }

    @router.post("/recovery-queue/{item_id}/retry")
    async def retry_recovery(
        item_id: int,
        _: AdminIdentity = Depends(require_admin),
    ) -> dict[str, Any]:
        queue = await run_in_threadpool(ReplyRecoveryQueue)
        try:
            await run_in_threadpool(queue.confirm_not_sent, (item_id,))
        except ValueError as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "recovery_retry_not_allowed",
                    "message": "只有经管理员确认未发送的未知结果项才可重新开放。",
                },
            ) from exc
        return {
            "ok": True,
            "code": "recovery_confirmed_not_sent",
            "phase": "operation_complete",
            "title": "已确认未发送",
            "message": "该入站消息已重新进入待恢复区，后续会使用当前上下文重新生成。",
            "retryable": False,
            "partial": False,
            "outcome_unknown": False,
            "warnings": [],
            "steps": [],
        }

    @router.get("/model-routes/capabilities")
    async def route_capabilities(
        _: AdminIdentity = Depends(require_admin),
    ) -> dict[str, Any]:
        return {"items": DEFAULT_ROUTE_CAPABILITY_REGISTRY.snapshot()}

    @router.get("/routes/capabilities")
    async def paged_route_capabilities(
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=20, ge=1, le=100),
        search: str = Query(default=""),
        _: AdminIdentity = Depends(require_admin),
    ) -> dict[str, Any]:
        params = normalize_pagination(page=page, page_size=page_size)
        needle = str(search or "").strip().casefold()
        rows = []
        for item in DEFAULT_ROUTE_CAPABILITY_REGISTRY.snapshot():
            route = item.get("route") if isinstance(item.get("route"), dict) else {}
            flat = {
                "route_fingerprint": item.get("route_fingerprint", ""),
                "provider": route.get("provider", ""),
                "api_type": route.get("api_type", ""),
                "model": route.get("model", ""),
                "media_protocol": route.get("media_protocol", ""),
                "capabilities": item.get("capabilities", {}),
                "probe_status": _ROUTE_PROBE_TASKS.get(str(item.get("route_fingerprint") or ""), {}).get("status", "idle"),
            }
            haystack = " ".join(str(value) for value in flat.values()).casefold()
            if not needle or needle in haystack:
                rows.append(flat)
        return build_page(
            rows[params.offset : params.offset + params.page_size],
            total=len(rows),
            params=params,
        ).to_dict()

    async def _finish_probe(route_fingerprint: str) -> None:
        await asyncio.sleep(0)
        task = _ROUTE_PROBE_TASKS.get(route_fingerprint)
        if task is None:
            return
        task.update({"status": "finished", "finished_at": time.time(), "detail_code": "probe_caller_unavailable"})
        publish_runtime_event(
            "provider.status_changed",
            payload={
                "route_fingerprint": route_fingerprint,
                "probe_status": "finished",
                "detail_code": "probe_caller_unavailable",
            },
        )

    @router.post("/routes/capabilities/{route_fingerprint}/probes")
    async def queue_route_probe(
        route_fingerprint: str,
        _: AdminIdentity = Depends(require_admin),
    ) -> dict[str, Any]:
        known = {str(item.get("route_fingerprint") or "") for item in DEFAULT_ROUTE_CAPABILITY_REGISTRY.snapshot()}
        if route_fingerprint not in known:
            raise HTTPException(status_code=404, detail={"code": "route_not_found", "message": "未找到该模型路由。"})
        _ROUTE_PROBE_TASKS[route_fingerprint] = {"status": "queued", "queued_at": time.time()}
        asyncio.create_task(_finish_probe(route_fingerprint))
        return {
            "ok": True,
            "code": "route_probe_queued",
            "phase": "queued",
            "title": "能力探针已排队",
            "message": "探针在管理任务中异步执行，不占聊天回合预算。当前进程未保留可调用路由时会保持未知。",
            "retryable": False,
            "partial": False,
            "outcome_unknown": False,
            "warnings": [],
            "steps": [],
        }

    @router.get("/overview")
    async def overview(_: AdminIdentity = Depends(require_admin)) -> dict[str, Any]:
        traces, _ = await run_in_threadpool(reply_turn_trace.query_page, limit=8, offset=0)
        queue = await run_in_threadpool(ReplyRecoveryQueue)
        route_counts = {"supported": 0, "unsupported": 0, "unknown": 0}
        for route in DEFAULT_ROUTE_CAPABILITY_REGISTRY.snapshot():
            for capability in (route.get("capabilities") or {}).values():
                state = str((capability or {}).get("state") or "unknown")
                route_counts[state if state in route_counts else "unknown"] += 1
        bus = get_runtime_event_bus()
        return {
            "generated_at": datetime.now(tz=timezone.utc).isoformat(),
            "runtime_status": "healthy",
            "active_turns": 0,
            "events_last_hour": len(bus),
            "p95_turn_ms": None,
            "route_counts": route_counts,
            "recovery_counts": await run_in_threadpool(queue.status_counts),
            "latest_traces": [_trace_summary(trace) for trace in traces],
            "diagnostics": [],
        }

    @router.get("/settings")
    async def settings(_: AdminIdentity = Depends(require_admin)) -> dict[str, Any]:
        return {
            "revision": "api_v2_r2",
            "participation_v2_mode": "shadow",
        }

    @router.get("/events")
    async def events(
        request: Request,
        _: AdminIdentity = Depends(require_admin),
    ) -> StreamingResponse:
        last_event_id = request.headers.get("last-event-id")
        bus = get_runtime_event_bus()
        return StreamingResponse(
            bus.stream(last_event_id=last_event_id),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    return router


__all__ = ["build_v2_router"]
