from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from ...core.pagination import build_page, normalize_pagination
from ...core.reply_recovery_queue import ReplyRecoveryQueue, RecoveryItem
from ...core import reply_turn_trace
from ...core.route_capabilities import DEFAULT_ROUTE_CAPABILITY_REGISTRY
from ...core.runtime_events import get_runtime_event_bus
from ..deps import AdminIdentity, require_admin


def _recovery_summary(item: RecoveryItem) -> dict[str, Any]:
    return {
        "id": item.id,
        "bot_id": item.bot_id,
        "conversation_kind": item.conversation_kind,
        "conversation_id": item.conversation_id,
        "original_message_id": item.original_message_id,
        "text_summary": item.normalized_text[:240],
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
        "status": item.status,
        "expires_at": item.expires_at,
        "next_attempt_at": item.next_attempt_at,
        "trace_id": item.trace_id,
        "recoverable": item.recoverable,
        "updated_at": item.updated_at,
    }


def _trace_summary(trace: dict[str, Any]) -> dict[str, Any]:
    process = reply_turn_trace.build_process_view(trace, logs=[])
    summary = process.get("summary") if isinstance(process, dict) else {}
    return {
        "trace_id": str(trace.get("trace_id") or ""),
        "ts": float(trace.get("ts") or 0),
        "session_type": str(trace.get("session_type") or ""),
        "group_id": str(trace.get("group_id") or ""),
        "user_id": str(trace.get("user_id") or ""),
        "outcome": str(trace.get("outcome") or ""),
        "diagnosis_code": str(trace.get("diagnosis_code") or ""),
        "stage_count": int(summary.get("stage_count") or 0),
        "warn_count": int(summary.get("warn_count") or 0),
        "error_count": int(summary.get("error_count") or 0),
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
        return {
            "trace": _trace_summary(trace),
            "process": reply_turn_trace.build_process_view(trace, logs=[]),
        }

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

    @router.get("/model-routes/capabilities")
    async def route_capabilities(
        _: AdminIdentity = Depends(require_admin),
    ) -> dict[str, Any]:
        return {"items": DEFAULT_ROUTE_CAPABILITY_REGISTRY.snapshot()}

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
