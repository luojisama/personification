from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from ...core.pagination import build_page, normalize_pagination, resolve_sort
from ...core.reply_recovery_queue import ReplyRecoveryQueue, RecoveryItem
from ...core import reply_turn_trace
from ...core.route_capabilities import DEFAULT_ROUTE_CAPABILITY_REGISTRY
from ...core.route_capabilities import CapabilityObservation, RouteKey
from ...core.qzone_capability_matrix import DEFAULT_QZONE_CAPABILITY_MATRIX
from ...core.runtime_events import get_runtime_event_bus
from ...core.runtime_events import publish_runtime_event
from ...core.visible_output import guard_visible_text
from ..deps import AdminIdentity, require_admin


_ROUTE_PROBE_TASKS: dict[str, dict[str, Any]] = {}
_STICKER_INDEX_TASKS: dict[str, dict[str, Any]] = {}


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


def _runtime_service(runtime: Any, name: str) -> Any:
    bundle = getattr(runtime, "runtime_bundle", None)
    return getattr(bundle, name, None) if bundle is not None else None


def _cached_persona_rows(
    runtime: Any,
    *,
    search: str,
    group_id: str,
    favorability_level: str,
    sort_by: str,
    direction: str,
) -> list[dict[str, Any]]:
    """Build cached profile summaries without any OneBot request."""

    service = _runtime_service(runtime, "profile_service")
    if service is None:
        return []
    profiles = service.list_core_profiles()
    group_users: set[str] | None = None
    normalized_group = str(group_id or "").strip()
    if normalized_group:
        group_users = {
            str(item.get("user_id", "") or "")
            for item in service.list_local_profiles(normalized_group)
            if isinstance(item, dict)
        }
    favorability = _runtime_service(runtime, "favorability_service")
    try:
        favorability_snapshot = favorability.snapshot_profiles() if favorability is not None else {}
    except Exception:
        favorability_snapshot = {}
    needle = str(search or "").strip().casefold()
    level_filter = str(favorability_level or "").strip()
    rows: list[dict[str, Any]] = []
    for raw in profiles:
        if not isinstance(raw, dict):
            continue
        user_id = str(raw.get("user_id", "") or "")
        if group_users is not None and user_id not in group_users:
            continue
        profile_json = raw.get("profile_json") if isinstance(raw.get("profile_json"), dict) else {}
        qq_profile = profile_json.get("qq_profile") if isinstance(profile_json.get("qq_profile"), dict) else {}
        nickname = next(
            (
                str(qq_profile.get(key, "") or "").strip()
                for key in ("remark", "card", "nickname")
                if str(qq_profile.get(key, "") or "").strip()
            ),
            user_id,
        )
        if needle and needle not in user_id.casefold() and needle not in nickname.casefold():
            continue
        fav_profile = favorability_snapshot.get(user_id)
        try:
            score = float((fav_profile or {}).get("favorability", 0.0) or 0.0)
        except (TypeError, ValueError):
            score = 0.0
        try:
            level = str(favorability.get_level_name(score) or "") if favorability is not None else ""
        except Exception:
            level = ""
        if level_filter and level != level_filter:
            continue
        profile_text = str(raw.get("profile_text", "") or "").strip()
        rows.append(
            {
                "user_id": user_id,
                "nickname": nickname,
                "avatar_url": str(qq_profile.get("avatar_url", "") or ""),
                "recent_group_id": str(qq_profile.get("last_group_id", "") or ""),
                "snippet": profile_text[:240],
                "favorability": {"score": round(score, 2), "level": level},
                "updated_at": float(raw.get("updated_at", 0) or 0),
                "source": str(raw.get("source", "") or ""),
                "cache_only": True,
            }
        )
    selection = resolve_sort(
        sort_by,
        allowed={
            "updated_at": lambda item: float(item.get("updated_at", 0) or 0),
            "favorability": lambda item: float((item.get("favorability") or {}).get("score", 0) or 0),
            "user_id": lambda item: str(item.get("user_id", "") or ""),
            "nickname": lambda item: str(item.get("nickname", "") or "").casefold(),
        },
        default="updated_at",
        direction=direction,
    )
    rows.sort(key=selection.value, reverse=selection.direction == "desc")
    return rows


def _cached_group_rows(
    runtime: Any,
    *,
    search: str,
    sort_by: str,
    direction: str,
) -> list[dict[str, Any]]:
    from ...core.group_directory import list_cached_group_union
    from ...utils import is_group_whitelisted

    config_whitelist = list(getattr(runtime.plugin_config, "personification_whitelist", []) or [])
    needle = str(search or "").strip().casefold()
    rows: list[dict[str, Any]] = []
    for raw in list_cached_group_union(runtime):
        group_id = str(raw.get("group_id", "") or "")
        group_name = str(raw.get("group_name", "") or "")
        if needle and needle not in group_id.casefold() and needle not in group_name.casefold():
            continue
        rows.append(
            {
                **raw,
                "enabled": bool(is_group_whitelisted(group_id, config_whitelist)),
                "cache_only": True,
            }
        )
    selection = resolve_sort(
        sort_by,
        allowed={
            "group_id": lambda item: str(item.get("group_id", "") or ""),
            "group_name": lambda item: str(item.get("group_name", "") or "").casefold(),
            "freshness": lambda item: float(item.get("freshness", 0) or 0),
        },
        default="group_id",
        direction=direction,
    )
    rows.sort(key=selection.value, reverse=selection.direction == "desc")
    return rows


def _config_rows(runtime: Any, *, search: str, group: str) -> list[dict[str, Any]]:
    from ...core import config_registry
    from ...core.sensitive_data import sanitize_object

    needle = str(search or "").strip().casefold()
    group_filter = str(group or "").strip()
    rows: list[dict[str, Any]] = []
    for entry in config_registry.get_config_entries():
        if group_filter and entry.group != group_filter:
            continue
        haystack = " ".join(
            (entry.field_name, entry.display_name, entry.description, entry.group)
        ).casefold()
        if needle and needle not in haystack:
            continue
        value = config_registry.read_config_value(
            entry,
            plugin_config=runtime.plugin_config,
        )
        if entry.secret:
            safe_value: Any = "***" if value not in (None, "", [], {}) else ""
        else:
            safe_value = sanitize_object(value)
        rows.append(
            {
                "key": entry.key,
                "field_name": entry.field_name,
                "display_name": entry.display_name,
                "description": entry.description,
                "group": entry.group,
                "scope": entry.scope,
                "value_type": entry.value_type,
                "value": safe_value,
                "default": "***" if entry.secret and entry.default else sanitize_object(entry.default),
                "secret": bool(entry.secret),
                "advanced": bool(entry.advanced),
                "hot_reloadable": bool(entry.hot_reloadable),
                "choices": list(entry.choices),
            }
        )
    rows.sort(key=lambda item: (str(item["group"]), str(item["display_name"]), str(item["field_name"])))
    return rows


def _sticker_root(runtime: Any) -> Any:
    from ...core.sticker_library import resolve_sticker_dir

    configured = getattr(runtime.plugin_config, "personification_sticker_path", None)
    return resolve_sticker_dir(configured or "data/stickers", create=True)


def _route_probe_target(runtime: Any, route_fingerprint: str) -> tuple[str, RouteKey, dict[str, Any]] | None:
    bundle = getattr(runtime, "runtime_bundle", None)
    getter = getattr(bundle, "get_configured_api_providers", None)
    try:
        providers = list(getter() or []) if callable(getter) else []
    except Exception:
        providers = []
    config = getattr(runtime, "plugin_config", None)
    if not providers and config is not None:
        providers = [
            {
                "name": "legacy_primary",
                "api_type": getattr(config, "personification_api_type", ""),
                "api_url": getattr(config, "personification_api_url", ""),
                "api_key": getattr(config, "personification_api_key", ""),
                "model": getattr(config, "personification_model", ""),
                "media_protocol": getattr(config, "personification_media_protocol", "auto"),
            }
        ]
    snapshots = DEFAULT_ROUTE_CAPABILITY_REGISTRY.snapshot()
    route_name = next(
        (
            str(item.get("route_name", "") or "")
            for item in snapshots
            if str(item.get("route_fingerprint", "") or "") == route_fingerprint
        ),
        "",
    )
    if not route_name:
        return None
    route_key = DEFAULT_ROUTE_CAPABILITY_REGISTRY.route_key(route_name)
    if route_key is None:
        return None
    for provider in providers:
        if not isinstance(provider, dict):
            continue
        candidate = RouteKey.from_config(
            provider=provider.get("name") or "legacy_primary",
            api_type=provider.get("api_type"),
            api_url=provider.get("api_url"),
            model=provider.get("model"),
            media_protocol=provider.get("media_protocol") or "auto",
        )
        if candidate.fingerprint == route_fingerprint:
            return route_name, route_key, dict(provider)
    return None


async def _run_route_visual_probe(runtime: Any, route_fingerprint: str) -> tuple[str, str]:
    target = _route_probe_target(runtime, route_fingerprint)
    if target is None:
        return "unknown", "probe_route_caller_unavailable"
    route_name, route_key, provider = target
    from ...core.ai_routes import build_single_provider_caller
    from ...core.visual_capabilities import probe_tool_caller_vision

    try:
        caller = build_single_provider_caller(runtime.plugin_config, provider)
    except Exception as exc:
        DEFAULT_ROUTE_CAPABILITY_REGISTRY.record_observation(
            route_key,
            "image_input",
            CapabilityObservation.NETWORK_ERROR,
            detail_code=f"probe_caller_build_failed:{type(exc).__name__}",
        )
        return "unknown", "probe_caller_build_failed"
    result = await probe_tool_caller_vision(
        route_name=route_name,
        caller=caller,
        api_type=str(provider.get("api_type", "") or ""),
        model=str(provider.get("model", "") or ""),
        logger=getattr(runtime, "logger", None) or _SilentProbeLogger(),
        timeout_seconds=getattr(
            runtime.plugin_config,
            "personification_visual_probe_timeout_seconds",
            45.0,
        ),
    )
    if result is True:
        observation = CapabilityObservation.SUCCESS
        state, code = "supported", "probe_visual_succeeded"
    elif result is False:
        observation = CapabilityObservation.EXPLICIT_UNSUPPORTED
        state, code = "unsupported", "probe_visual_explicitly_unsupported"
    else:
        observation = CapabilityObservation.PARSE_ERROR
        state, code = "unknown", "probe_visual_unknown"
    DEFAULT_ROUTE_CAPABILITY_REGISTRY.record_observation(
        route_key,
        "image_input",
        observation,
        detail_code=code,
    )
    return state, code


class _SilentProbeLogger:
    def warning(self, _message: str) -> None:
        return None


def build_v2_router(*, runtime: Any) -> APIRouter:
    router = APIRouter(prefix="/api/v2", tags=["v2"])

    @router.get("/personas")
    async def personas(
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=20, ge=1, le=100),
        search: str = Query(default="", max_length=120),
        group_id: str = Query(default="", max_length=64),
        favorability_level: str = Query(default="", max_length=64),
        sort_by: str = Query(default="updated_at", max_length=32),
        direction: str = Query(default="desc", pattern="^(asc|desc)$"),
        _: AdminIdentity = Depends(require_admin),
    ) -> dict[str, Any]:
        params = normalize_pagination(page=page, page_size=page_size)
        try:
            rows = await run_in_threadpool(
                _cached_persona_rows,
                runtime,
                search=search,
                group_id=group_id,
                favorability_level=favorability_level,
                sort_by=sort_by,
                direction=direction,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail={"code": "persona_sort_invalid", "message": "画像排序字段无效。"},
            ) from exc
        return build_page(
            rows[params.offset : params.offset + params.page_size],
            total=len(rows),
            params=params,
        ).to_dict()

    @router.get("/groups")
    async def groups(
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=20, ge=1, le=100),
        search: str = Query(default="", max_length=120),
        sort_by: str = Query(default="group_id", max_length=32),
        direction: str = Query(default="asc", pattern="^(asc|desc)$"),
        _: AdminIdentity = Depends(require_admin),
    ) -> dict[str, Any]:
        params = normalize_pagination(page=page, page_size=page_size)
        try:
            rows = await run_in_threadpool(
                _cached_group_rows,
                runtime,
                search=search,
                sort_by=sort_by,
                direction=direction,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail={"code": "group_sort_invalid", "message": "群列表排序字段无效。"},
            ) from exc
        return build_page(
            rows[params.offset : params.offset + params.page_size],
            total=len(rows),
            params=params,
        ).to_dict()

    async def _finish_sticker_index_rebuild(root_key: str, root: Any) -> None:
        from ...core.sticker_catalog_index import rebuild_sticker_catalog_index

        task = _STICKER_INDEX_TASKS.setdefault(root_key, {})
        task.update({"status": "running", "started_at": time.time()})
        try:
            snapshot = await run_in_threadpool(rebuild_sticker_catalog_index, root)
        except Exception as exc:
            task.update(
                {
                    "status": "failed",
                    "finished_at": time.time(),
                    "detail_code": f"sticker_index_rebuild_failed:{type(exc).__name__}",
                }
            )
            return
        task.update(
            {
                "status": "finished",
                "finished_at": time.time(),
                "detail_code": "sticker_index_ready",
                "item_count": len(snapshot.get("items") or []),
            }
        )

    def _queue_sticker_rebuild(root: Any) -> dict[str, Any]:
        root_key = str(root.resolve())
        current = _STICKER_INDEX_TASKS.get(root_key)
        if not current or current.get("status") not in {"queued", "running"}:
            _STICKER_INDEX_TASKS[root_key] = {
                "status": "queued",
                "queued_at": time.time(),
                "detail_code": "sticker_index_queued",
            }
            asyncio.create_task(_finish_sticker_index_rebuild(root_key, root))
        return dict(_STICKER_INDEX_TASKS[root_key])

    @router.get("/stickers")
    async def stickers(
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=20, ge=1, le=100),
        search: str = Query(default="", max_length=120),
        labeled: bool | None = Query(default=None),
        sort_by: str = Query(default="filename", max_length=32),
        direction: str = Query(default="asc", pattern="^(asc|desc)$"),
        _: AdminIdentity = Depends(require_admin),
    ) -> dict[str, Any]:
        from ...core.sticker_catalog_index import load_sticker_catalog_index

        params = normalize_pagination(page=page, page_size=page_size)
        root = _sticker_root(runtime)
        snapshot = await run_in_threadpool(load_sticker_catalog_index, root)
        task = _STICKER_INDEX_TASKS.get(str(root.resolve()), {"status": "idle"})
        if bool(snapshot.get("stale", True)):
            task = _queue_sticker_rebuild(root)
        needle = str(search or "").strip().casefold()
        rows = []
        for item in snapshot.get("items") or []:
            if not isinstance(item, dict):
                continue
            if labeled is not None and bool(item.get("labeled", False)) != labeled:
                continue
            searchable = " ".join(
                [
                    str(item.get("filename", "") or ""),
                    str(item.get("description", "") or ""),
                    *[str(value or "") for value in item.get("mood_tags") or []],
                    *[str(value or "") for value in item.get("scene_tags") or []],
                ]
            ).casefold()
            if not needle or needle in searchable:
                rows.append(dict(item))
        try:
            selection = resolve_sort(
                sort_by,
                allowed={
                    "filename": lambda item: str(item.get("filename", "") or "").casefold(),
                    "size_bytes": lambda item: int(item.get("size_bytes", 0) or 0),
                    "modified_at": lambda item: float(item.get("modified_at", 0) or 0),
                },
                default="filename",
                direction=direction,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail={"code": "sticker_sort_invalid", "message": "贴纸排序字段无效。"},
            ) from exc
        rows.sort(key=selection.value, reverse=selection.direction == "desc")
        payload = build_page(
            rows[params.offset : params.offset + params.page_size],
            total=len(rows),
            params=params,
        ).to_dict()
        payload.update(
            {
                "index_status": task.get("status", "idle"),
                "index_detail_code": task.get("detail_code", "sticker_index_idle"),
                "index_updated_at": snapshot.get("updated_at", 0.0),
                "index_stale": bool(snapshot.get("stale", True)),
            }
        )
        return payload

    @router.post("/stickers/index/rebuild")
    async def rebuild_sticker_index(
        _: AdminIdentity = Depends(require_admin),
    ) -> dict[str, Any]:
        task = _queue_sticker_rebuild(_sticker_root(runtime))
        return {
            "ok": True,
            "code": "sticker_index_queued",
            "phase": task.get("status", "queued"),
            "title": "贴纸索引重建已排队",
            "message": "目录扫描在后台管理任务中执行，列表请求继续读取上一个已知索引。",
            "retryable": False,
            "partial": False,
            "outcome_unknown": False,
            "warnings": [],
            "steps": [],
        }

    @router.get("/config")
    async def config_entries(
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=20, ge=1, le=100),
        search: str = Query(default="", max_length=120),
        group: str = Query(default="", max_length=80),
        _: AdminIdentity = Depends(require_admin),
    ) -> dict[str, Any]:
        params = normalize_pagination(page=page, page_size=page_size)
        rows = await run_in_threadpool(_config_rows, runtime, search=search, group=group)
        return build_page(
            rows[params.offset : params.offset + params.page_size],
            total=len(rows),
            params=params,
        ).to_dict()

    @router.get("/multimodal/routes")
    async def multimodal_routes(
        _: AdminIdentity = Depends(require_admin),
    ) -> dict[str, Any]:
        config = runtime.plugin_config
        from ...core.media_understanding import (
            audio_route_available,
            normalize_video_route_mode,
            primary_route_supports_native_audio,
            primary_route_supports_native_video,
        )

        return {
            "audio": {
                "enabled": bool(getattr(config, "personification_audio_transcription_enabled", True)),
                "primary_native": bool(primary_route_supports_native_audio(runtime)),
                "route_available": bool(audio_route_available(runtime)),
                "asr_provider": str(getattr(config, "personification_audio_transcription_provider", "auto") or "auto"),
                "asr_model": str(getattr(config, "personification_audio_transcription_model", "") or ""),
                "fallback_order": ["primary_native", "external_fullmodal", "gemini_web", "configured_asr"],
            },
            "video": {
                "enabled": bool(getattr(config, "personification_video_understanding_enabled", False)),
                "route_mode": normalize_video_route_mode(
                    getattr(config, "personification_video_route_mode", "auto")
                ),
                "primary_native": bool(primary_route_supports_native_video(runtime)),
                "gemini_web_enabled": bool(getattr(config, "personification_gemini_web_enabled", False)),
                "external_fallback_enabled": bool(getattr(config, "personification_video_fallback_enabled", True)),
                "storyboard_fallback_enabled": bool(getattr(config, "personification_video_storyboard_fallback_enabled", True)),
                "fallback_order": ["primary_native", "gemini_web", "external_fullmodal", "storyboard", "subtitle_asr"],
            },
            "diagnostic_code": "multimodal_route_snapshot_local_only",
            "production_verified": False,
        }

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
        task = _ROUTE_PROBE_TASKS.get(route_fingerprint)
        if task is None:
            return
        task.update({"status": "running", "started_at": time.time()})
        try:
            capability_state, detail_code = await _run_route_visual_probe(runtime, route_fingerprint)
        except Exception as exc:
            capability_state = "unknown"
            detail_code = f"probe_internal_failed:{type(exc).__name__}"
        task.update(
            {
                "status": "finished",
                "finished_at": time.time(),
                "capability_state": capability_state,
                "detail_code": detail_code,
            }
        )
        publish_runtime_event(
            "provider.status_changed",
            payload={
                "route_fingerprint": route_fingerprint,
                "probe_status": "finished",
                "capability_state": capability_state,
                "detail_code": detail_code,
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
            "title": "视觉能力探针已排队",
            "message": "视觉探针在管理任务中异步执行，不占聊天回合预算。当前进程未保留该路由调用器时会保持未知。",
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
        config = getattr(runtime, "plugin_config", None)
        return {
            "revision": "api_v2_r4",
            "participation_v2_mode": str(
                getattr(config, "personification_participation_v2_mode", "shadow") or "shadow"
            ),
            "tool_disclosure_mode": str(
                getattr(config, "personification_tool_disclosure_mode", "off") or "off"
            ),
            "emotion_v2_mode": str(
                getattr(config, "personification_emotion_v2_mode", "shadow") or "shadow"
            ),
            "reply_wait_contract": {
                "min_seconds": float(getattr(config, "personification_batch_min_wait_seconds", 10.0) or 10.0),
                "base_seconds": float(getattr(config, "personification_batch_base_wait_seconds", 30.0) or 30.0),
                "max_seconds": float(getattr(config, "personification_batch_max_wait_seconds", 60.0) or 60.0),
            },
            "whole_backup": {
                "state_export_available": callable(getattr(runtime, "whole_plugin_export_datasets", None)),
                "secret_export_available": callable(getattr(runtime, "whole_plugin_export_secrets", None)),
                "restore_available": getattr(runtime, "whole_plugin_restore_backend", None) is not None,
                "step_up_required": True,
                "secret_https_required": True,
            },
        }

    @router.get("/qzone/capabilities")
    async def qzone_capabilities(
        bot_id: str = Query(default="", max_length=64),
        _: AdminIdentity = Depends(require_admin),
    ) -> dict[str, Any]:
        from ...core.qzone_service import get_qzone_auth_status, get_qzone_capability_status

        plugin_config = getattr(runtime, "plugin_config", None)
        enabled = bool(getattr(plugin_config, "personification_qzone_enabled", True))
        return DEFAULT_QZONE_CAPABILITY_MATRIX.snapshot(
            bot_id,
            enabled=enabled,
            auth_status=get_qzone_auth_status(bot_id),
            aggregate_status=get_qzone_capability_status(bot_id, enabled=enabled),
        )

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
