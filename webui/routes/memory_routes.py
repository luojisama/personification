from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from ..deps import AdminIdentity, require_admin


def _memory_store(runtime) -> Any | None:
    bundle = getattr(runtime, "runtime_bundle", None)
    if bundle is None:
        return None
    return getattr(bundle, "memory_store", None)


def build_memory_router(*, runtime) -> APIRouter:
    router = APIRouter(prefix="/api/memory", tags=["memory"])

    @router.get("/recent")
    async def recent(
        limit: int = Query(default=40, ge=1, le=200),
        memory_type: str = Query(default=""),
        group_id: str = Query(default=""),
        _: AdminIdentity = Depends(require_admin),
    ) -> dict:
        store = _memory_store(runtime)
        if store is None:
            return {"items": [], "palace_enabled": False}
        try:
            palace_on = bool(store.palace_enabled())
        except Exception:
            palace_on = False
        if not palace_on:
            return {"items": [], "palace_enabled": False}
        try:
            items = list(store.list_recent_memories(
                group_id=str(group_id or "").strip(),
                limit=int(limit),
                memory_type=str(memory_type or "").strip(),
            ))
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))
        rendered = []
        for item in items:
            rendered.append({
                "memory_id": str(item.get("memory_id", "") or ""),
                "memory_type": str(item.get("memory_type", "") or ""),
                "group_id": str(item.get("group_id", "") or ""),
                "user_id": str(item.get("user_id", "") or ""),
                "summary": str(item.get("summary", "") or "")[:300],
                "source_kind": str(item.get("source_kind", "") or ""),
                "confidence": float(item.get("confidence", 0) or 0),
                "salience": float(item.get("salience", 0) or 0),
                "updated_at": float(item.get("updated_at", 0) or 0),
            })
        return {"items": rendered, "palace_enabled": True}

    @router.get("/inner-state")
    async def inner_state_view(_: AdminIdentity = Depends(require_admin)) -> dict:
        try:
            from ...agent.inner_state import load_inner_state
            from ...core.paths import get_data_dir

            data_dir = get_data_dir(getattr(runtime, "plugin_config", None))
            data = await load_inner_state(data_dir)
            return {"available": True, "state": data}
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    return router
