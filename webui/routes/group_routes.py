from __future__ import annotations

import json
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from ...core.db import connect_sync
from ..deps import AdminIdentity, require_admin


def _profile_service(runtime) -> Any | None:
    bundle = getattr(runtime, "runtime_bundle", None)
    if bundle is None:
        return None
    return getattr(bundle, "profile_service", None)


def _memory_store(runtime) -> Any | None:
    bundle = getattr(runtime, "runtime_bundle", None)
    if bundle is None:
        return None
    return getattr(bundle, "memory_store", None)


def _load_group_style(group_id: str) -> dict[str, Any]:
    with connect_sync() as conn:
        row = conn.execute(
            "SELECT style_text, style_json, updated_at FROM group_style_snapshots WHERE group_id=?",
            (str(group_id or ""),),
        ).fetchone()
    if not row:
        return {"style_text": "", "style_json": {}, "updated_at": 0}
    try:
        payload = json.loads(row["style_json"] or "{}")
    except Exception:
        payload = {}
    return {
        "style_text": str(row["style_text"] or ""),
        "style_json": payload if isinstance(payload, dict) else {},
        "updated_at": float(row["updated_at"] or 0),
    }


def _save_group_style(group_id: str, style_text: str, style_json: dict[str, Any]) -> None:
    payload = json.dumps(style_json or {}, ensure_ascii=False)
    with connect_sync() as conn:
        conn.execute(
            """
            INSERT INTO group_style_snapshots(group_id, style_text, style_json, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(group_id) DO UPDATE SET
                style_text=excluded.style_text,
                style_json=excluded.style_json,
                updated_at=excluded.updated_at
            """,
            (str(group_id or ""), str(style_text or ""), payload, time.time()),
        )
        conn.commit()


def build_group_router(*, runtime) -> APIRouter:
    router = APIRouter(prefix="/api/groups", tags=["groups"])

    @router.get("")
    async def list_groups(_: AdminIdentity = Depends(require_admin)) -> dict:
        svc = _profile_service(runtime)
        if svc is None:
            return {"groups": [], "available": False}
        groups = svc.list_groups()
        return {"groups": groups, "available": True}

    @router.get("/{group_id}/personas")
    async def personas(group_id: str, _: AdminIdentity = Depends(require_admin)) -> dict:
        svc = _profile_service(runtime)
        if svc is None:
            raise HTTPException(status_code=503, detail="profile_service 未就绪")
        profiles = svc.list_local_profiles(group_id)
        return {
            "group_id": group_id,
            "profiles": [
                {
                    "user_id": p["user_id"],
                    "snippet": (p["profile_text"] or "")[:140],
                    "updated_at": p.get("updated_at", 0),
                }
                for p in profiles
            ],
        }

    @router.get("/{group_id}/style")
    async def style(group_id: str, _: AdminIdentity = Depends(require_admin)) -> dict:
        data = _load_group_style(group_id)
        data["group_id"] = group_id
        return data

    @router.get("/{group_id}/memory/recent")
    async def memory_recent(
        group_id: str,
        limit: int = 30,
        _: AdminIdentity = Depends(require_admin),
    ) -> dict:
        store = _memory_store(runtime)
        if store is None:
            raise HTTPException(status_code=503, detail="memory_store 未就绪")
        try:
            items = list(store.list_recent_memories(group_id=group_id, limit=max(1, min(int(limit), 100))))
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))
        return {"group_id": group_id, "items": items}

    return router
