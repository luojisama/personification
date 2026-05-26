from __future__ import annotations

import hashlib
import json
import time
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request

from ...core import webui_audit_log
from ...core.db import connect_sync
from ...core.meme_dictionary import delete_meme_entry, list_meme_entries, upsert_meme_entry
from ...core.onebot_cache import get_group_name, get_user_nickname
from ..deps import AdminIdentity, get_client_ip, require_admin


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


def _get_first_bot(runtime) -> Any | None:
    bundle = getattr(runtime, "runtime_bundle", None)
    if bundle is None:
        return None
    get_bots = getattr(bundle, "get_bots", None)
    if not callable(get_bots):
        return None
    try:
        bots = get_bots() or {}
    except Exception:
        return None
    return next(iter(bots.values()), None) if bots else None


_REBUILD_RATELIMIT_NS = "group_style_rebuild_ratelimit"
_REBUILD_WINDOW_SECONDS = 300
_REBUILD_MAX_PER_WINDOW = 3


def build_group_router(*, runtime) -> APIRouter:
    router = APIRouter(prefix="/api/groups", tags=["groups"])

    @router.get("")
    async def list_groups(_: AdminIdentity = Depends(require_admin)) -> dict:
        svc = _profile_service(runtime)
        if svc is None:
            return {"groups": [], "available": False}
        groups = svc.list_groups()
        bot = _get_first_bot(runtime)
        items: list[dict[str, Any]] = []
        for gid in groups:
            gid_str = str(gid)
            items.append(
                {
                    "group_id": gid_str,
                    "group_name": await get_group_name(bot, gid_str),
                }
            )
        return {"groups": items, "available": True}

    @router.get("/{group_id}/personas")
    async def personas(group_id: str, _: AdminIdentity = Depends(require_admin)) -> dict:
        svc = _profile_service(runtime)
        if svc is None:
            raise HTTPException(status_code=503, detail="profile_service 未就绪")
        profiles = svc.list_local_profiles(group_id)
        bot = _get_first_bot(runtime)
        items: list[dict[str, Any]] = []
        for p in profiles:
            uid = p["user_id"]
            items.append(
                {
                    "user_id": uid,
                    "nickname": await get_user_nickname(bot, uid),
                    "snippet": (p["profile_text"] or "")[:140],
                    "updated_at": p.get("updated_at", 0),
                }
            )
        return {"group_id": group_id, "profiles": items}

    @router.get("/{group_id}/style")
    async def style(group_id: str, _: AdminIdentity = Depends(require_admin)) -> dict:
        from ...core.group_style_autobuild import list_style_snapshots

        snapshots = list_style_snapshots(group_id, limit=3)
        latest = snapshots[0] if snapshots else None
        return {
            "group_id": group_id,
            "snapshots": snapshots,
            "style_text": latest["style_text"] if latest else "",
            "style_json": latest["style_json"] if latest else {},
            "updated_at": latest["created_at"] if latest else 0,
        }

    @router.post("/{group_id}/style/rebuild")
    async def style_rebuild(
        group_id: str,
        request: Request,
        body: dict = Body(default_factory=dict),
        admin: AdminIdentity = Depends(require_admin),
    ) -> dict:
        # 限流：同设备 5 分钟最多 3 次（避免误点滥用 token）
        from ...core.data_store import get_data_store

        rate_key = hashlib.sha256(f"{admin.device_id}:{group_id}".encode("utf-8")).hexdigest()[:24]
        store = get_data_store()
        now_ts = time.time()
        rl_data = store.load_sync(_REBUILD_RATELIMIT_NS) or {}
        if not isinstance(rl_data, dict):
            rl_data = {}
        bucket = rl_data.get(rate_key) if isinstance(rl_data.get(rate_key), dict) else {}
        if not isinstance(bucket, dict) or now_ts - float(bucket.get("window_start", 0) or 0) > _REBUILD_WINDOW_SECONDS:
            bucket = {"window_start": now_ts, "count": 0}
        if int(bucket.get("count", 0) or 0) >= _REBUILD_MAX_PER_WINDOW:
            raise HTTPException(status_code=429, detail=f"重建过于频繁，请 {int(_REBUILD_WINDOW_SECONDS / 60)} 分钟后重试")
        bucket["count"] = int(bucket.get("count", 0) or 0) + 1
        rl_data[rate_key] = bucket
        store.save_sync(_REBUILD_RATELIMIT_NS, rl_data)

        from ...core.group_style_autobuild import build_group_style, _load_messages_since, _format_chat_summary

        store_mem = _memory_store(runtime)
        if store_mem is None:
            raise HTTPException(status_code=503, detail="memory_store 未就绪")
        bundle = getattr(runtime, "runtime_bundle", None)
        deps = getattr(bundle, "reply_processor_deps", None) if bundle else None
        runtime_inner = getattr(deps, "runtime", None) if deps else None
        tool_caller = getattr(runtime_inner, "agent_tool_caller", None) if runtime_inner else None
        if tool_caller is None:
            raise HTTPException(status_code=503, detail="tool_caller 未就绪")
        # 取最近 250 条对话强行喂给 LLM；跳过 daily_limit
        rows = _load_messages_since(memory_store=store_mem, group_id=group_id, since_ts=0, limit=250)
        if len(rows) < 20:
            raise HTTPException(status_code=400, detail=f"该群消息不足 20 条（当前 {len(rows)}），样本太少不构建")
        chat_summary = _format_chat_summary(rows)
        built = await build_group_style(
            tool_caller=tool_caller,
            memory_store=store_mem,
            group_id=group_id,
            chat_summary=chat_summary,
        )
        if not built:
            webui_audit_log.record(
                action="style_rebuild",
                qq=admin.qq,
                device_id=admin.device_id,
                target=group_id,
                outcome="llm_failed",
            )
            raise HTTPException(status_code=500, detail="LLM 返回的风格 JSON 解析失败")
        from ...core.group_style_autobuild import list_style_snapshots

        webui_audit_log.record(
            action="style_rebuild",
            qq=admin.qq,
            device_id=admin.device_id,
            target=group_id,
            detail={"snapshot_id": built.get("id")},
        )
        return {
            "success": True,
            "new_snapshot": built,
            "snapshots": list_style_snapshots(group_id, limit=3),
        }

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

    @router.get("/{group_id}/knowledge")
    async def group_knowledge(
        group_id: str,
        limit: int = 50,
        _: AdminIdentity = Depends(require_admin),
    ) -> dict:
        store = _memory_store(runtime)
        if store is None:
            raise HTTPException(status_code=503, detail="memory_store 未就绪")
        try:
            items = list(
                store.list_recent_memories(
                    group_id=group_id,
                    limit=max(1, min(int(limit), 200)),
                    memory_type="group_knowledge",
                )
            )
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))
        knowledge = []
        for item in items:
            knowledge.append(
                {
                    "term": item.get("term", "") or item.get("summary", "").split(":")[0],
                    "definition": item.get("definition", "") or item.get("summary", ""),
                    "source_kind": item.get("source_kind", ""),
                    "confidence": float(item.get("confidence", 0) or 0),
                    "updated_at": float(item.get("updated_at", 0) or 0),
                }
            )
        return {"group_id": group_id, "knowledge": knowledge}

    @router.get("/{group_id}/memes")
    async def group_memes(
        group_id: str,
        limit: int = 100,
        _: AdminIdentity = Depends(require_admin),
    ) -> dict:
        try:
            items = list_meme_entries(group_id=group_id, limit=max(1, min(int(limit), 300)))
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))
        return {"group_id": group_id, "memes": items}

    @router.post("/{group_id}/memes")
    async def save_group_meme(
        group_id: str,
        request: Request,
        body: dict = Body(default_factory=dict),
        admin: AdminIdentity = Depends(require_admin),
    ) -> dict:
        payload = dict(body or {})
        scope = str(payload.get("scope", "group") or "group").strip().lower()
        if scope not in {"group", "concept"}:
            scope = "group"
        payload["scope"] = scope
        payload["group_id"] = str(group_id)
        ok = upsert_meme_entry(payload)
        if not ok:
            raise HTTPException(status_code=400, detail="term 和 meaning/definition 不能为空")
        webui_audit_log.record(
            action="meme_upsert",
            qq=admin.qq,
            device_id=admin.device_id,
            target=group_id,
            ip_hash=get_client_ip(request),
            detail={"term": payload.get("term"), "scope": scope},
        )
        return {"success": True, "entry": payload}

    @router.delete("/{group_id}/memes/{term}")
    async def delete_group_meme(
        group_id: str,
        term: str,
        request: Request,
        scope: str = "group",
        admin: AdminIdentity = Depends(require_admin),
    ) -> dict:
        normalized_scope = scope if scope in {"group", "concept"} else "group"
        changed = delete_meme_entry(term=term, scope=normalized_scope, group_id=group_id)
        webui_audit_log.record(
            action="meme_delete",
            qq=admin.qq,
            device_id=admin.device_id,
            target=group_id,
            ip_hash=get_client_ip(request),
            detail={"term": term, "scope": normalized_scope, "changed": changed},
        )
        return {"success": True, "deleted": changed}

    return router
