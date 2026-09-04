from __future__ import annotations

import asyncio
import hashlib
import math
import shutil
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse, StreamingResponse
from starlette.concurrency import run_in_threadpool

from ...core.pagination import build_page, normalize_pagination, resolve_sort
from ...core.reply_recovery_queue import ReplyRecoveryQueue, RecoveryItem
from ...core import reply_turn_trace
from ...core.operation_diagnostics import diagnostic as operation_diagnostic
from ...core.operation_diagnostics import step as operation_step
from ...core.route_capabilities import CAPABILITY_NAMES, DEFAULT_ROUTE_CAPABILITY_REGISTRY
from ...core.route_capabilities import CapabilityObservation, RouteKey
from ...core.qzone_capability_matrix import DEFAULT_QZONE_CAPABILITY_MATRIX
from ...core.runtime_events import get_runtime_event_bus
from ...core.runtime_events import publish_runtime_event
from ...core.subscription_quota import query_subscription_quotas
from ...core import proactive_diagnostics, webui_audit_log
from ...core.visible_output import guard_visible_text
from ...core.sensitive_data import sanitize_text
from ..deps import AdminIdentity, get_client_ip, require_admin
from ..v2_services import (
    apply_config_patch,
    build_agent_runtime_snapshot,
    config_revision,
    group_avatar_url,
    list_bot_identities,
    qq_avatar_url,
)
from .metrics_routes import build_metrics_summary


_ROUTE_PROBE_TASKS: dict[tuple[str, str], dict[str, Any]] = {}
_STICKER_INDEX_TASKS: dict[str, dict[str, Any]] = {}
_FUNCTIONAL_TEST_RUNS: dict[str, dict[str, Any]] = {}
_ADMIN_INDEX_TASKS: dict[str, dict[str, Any]] = {}
_CONFIG_SNAPSHOT_CACHE: dict[str, list[dict[str, Any]]] = {}

_FUNCTIONAL_TEST_CATALOG: tuple[dict[str, Any], ...] = (
    {"id": "core", "label": "核心运行", "category": "核心", "group": "核心运行", "risk": "local_read", "execution_kind": "local_readonly"},
    {"id": "model", "label": "主模型", "category": "模型调用", "group": "模型与媒体", "risk": "external_read", "execution_kind": "provider_probe"},
    {"id": "submodels", "label": "子模型", "category": "LLM 子模型", "group": "模型与媒体", "risk": "external_read", "execution_kind": "provider_probe"},
    {"id": "vision", "label": "图片理解", "category": "视觉能力", "group": "模型与媒体", "risk": "external_read", "execution_kind": "provider_probe"},
    {"id": "video", "label": "视频理解", "category": "视频理解", "group": "模型与媒体", "risk": "external_read", "execution_kind": "provider_probe"},
    {"id": "storage", "label": "存储", "category": "存储", "group": "存储与记忆", "risk": "local_read", "execution_kind": "local_readonly"},
    {"id": "memory", "label": "记忆", "category": "记忆", "group": "存储与记忆", "risk": "local_read", "execution_kind": "local_readonly"},
    {"id": "personas", "label": "画像", "category": "用户画像", "group": "存储与记忆", "risk": "local_read", "execution_kind": "local_readonly"},
    {"id": "groups", "label": "群聊", "category": "群聊", "group": "QQ 与群聊", "risk": "local_read", "execution_kind": "local_readonly"},
    {"id": "stickers", "label": "表情包", "category": "表情包", "group": "QQ 与群聊", "risk": "local_read", "execution_kind": "local_readonly"},
    {"id": "tts", "label": "TTS", "category": "TTS 语音", "group": "模型与媒体", "risk": "external_read", "execution_kind": "provider_probe"},
    {"id": "qzone", "label": "QQ 空间", "category": "QQ 空间", "group": "QZone", "risk": "external_write", "execution_kind": "qzone_canary"},
    {"id": "web_search", "label": "联网搜索", "category": "联网搜索", "group": "模型与媒体", "risk": "external_read", "execution_kind": "provider_probe"},
    {"id": "skills", "label": "Skill", "category": "Skill 扩展", "group": "后台任务与权限", "risk": "local_read", "execution_kind": "local_readonly"},
    {"id": "proactive", "label": "主动社交", "category": "主动社交", "group": "QQ 与群聊", "risk": "external_write", "execution_kind": "qq_canary"},
    {"id": "persona", "label": "人设", "category": "人设", "group": "存储与记忆", "risk": "local_read", "execution_kind": "local_readonly"},
    {"id": "protocol", "label": "协议端", "category": "协议端", "group": "QQ 与群聊", "risk": "external_read", "execution_kind": "provider_probe"},
    {"id": "webui_security", "label": "WebUI 安全", "category": "WebUI 安全", "group": "后台任务与权限", "risk": "local_read", "execution_kind": "local_readonly"},
)


_ROUTE_MEDIA_PROBE_SPECS: dict[str, dict[str, Any]] = {
    "audio_input": {
        "probe_id": "audio_upload",
        "accepted_mime_types": (
            "audio/wav",
            "audio/x-wav",
            "audio/mpeg",
            "audio/mp4",
            "audio/aac",
            "audio/ogg",
            "audio/opus",
            "audio/flac",
            "audio/x-flac",
            "audio/amr",
        ),
        "mime_suffixes": {
            "audio/wav": (".wav",),
            "audio/x-wav": (".wav",),
            "audio/mpeg": (".mp3",),
            "audio/mp4": (".m4a",),
            "audio/aac": (".aac",),
            "audio/ogg": (".ogg", ".opus"),
            "audio/opus": (".opus",),
            "audio/flac": (".flac",),
            "audio/x-flac": (".flac",),
            "audio/amr": (".amr",),
        },
        # This is intentionally a small administrative sample rather than a
        # general media-upload facility.  It is copied to the existing
        # health-probe root and removed once this one probe finishes.
        "max_upload_bytes": 12 * 1024 * 1024,
    },
    "video_input": {
        "probe_id": "video_upload",
        "accepted_mime_types": (
            "video/mp4",
            "video/quicktime",
            "video/webm",
            "video/x-matroska",
            "video/x-msvideo",
            "video/avi",
            "video/x-m4v",
        ),
        "mime_suffixes": {
            "video/mp4": (".mp4", ".m4v"),
            "video/quicktime": (".mov",),
            "video/webm": (".webm",),
            "video/x-matroska": (".mkv",),
            "video/x-msvideo": (".avi",),
            "video/avi": (".avi",),
            "video/x-m4v": (".m4v",),
        },
        "max_upload_bytes": 32 * 1024 * 1024,
    },
}


_ROUTE_PROBE_CATALOG: dict[str, dict[str, Any]] = {
    "image_input": {
        "probe_id": "vision",
        "available": True,
        "risk": "external_read",
        "confirmation_required": True,
        "reason_code": "vision_probe_available",
    },
    "function_call": {
        "probe_id": "function_call_noop",
        "available": True,
        "risk": "external_read",
        "confirmation_required": True,
        "reason_code": "function_call_noop_probe_available",
    },
    "audio_input": {
        "probe_id": _ROUTE_MEDIA_PROBE_SPECS["audio_input"]["probe_id"],
        "available": True,
        "risk": "external_read",
        "confirmation_required": True,
        "reason_code": "audio_probe_upload_available",
        "input_kind": "media_upload",
        "accepted_mime_types": _ROUTE_MEDIA_PROBE_SPECS["audio_input"]["accepted_mime_types"],
        "max_upload_bytes": _ROUTE_MEDIA_PROBE_SPECS["audio_input"]["max_upload_bytes"],
    },
    "video_input": {
        "probe_id": _ROUTE_MEDIA_PROBE_SPECS["video_input"]["probe_id"],
        "available": True,
        "risk": "external_read",
        "confirmation_required": True,
        "reason_code": "video_probe_upload_available",
        "input_kind": "media_upload",
        "accepted_mime_types": _ROUTE_MEDIA_PROBE_SPECS["video_input"]["accepted_mime_types"],
        "max_upload_bytes": _ROUTE_MEDIA_PROBE_SPECS["video_input"]["max_upload_bytes"],
    },
    "reasoning": {
        "probe_id": "reasoning_minimal",
        "available": True,
        "risk": "external_read",
        "confirmation_required": True,
        "reason_code": "reasoning_minimal_probe_available",
    },
    "native_web_search": {
        "probe_id": "native_search_readonly",
        "available": True,
        "risk": "external_read",
        "confirmation_required": True,
        "reason_code": "native_search_readonly_probe_available",
    },
    "external_network_access": {
        "probe_id": "none",
        "available": False,
        "risk": "external_read",
        "confirmation_required": True,
        "reason_code": "external_network_probe_unavailable",
    },
}


def _iso(value: Any) -> str | None:
    try:
        timestamp = float(value or 0)
    except (TypeError, ValueError, OverflowError):
        return None
    if timestamp <= 0 or not math.isfinite(timestamp):
        return None
    try:
        return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


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


def _trace_stage_detail(trace: dict[str, Any], key: str, *, limit: int) -> str:
    stages = trace.get("stages") if isinstance(trace.get("stages"), list) else []
    for stage in reversed(stages):
        if not isinstance(stage, dict) or str(stage.get("key") or "") != key:
            continue
        value = str(stage.get("detail") or "")
        if value:
            return value[:limit]
    return ""


def _trace_visible_message(
    trace: dict[str, Any],
    *,
    detail_key: str,
    stage_key: str,
    surface: str,
    allow_direct_media: bool,
    limit: int,
) -> str:
    detail = trace.get("detail") if isinstance(trace.get("detail"), dict) else {}
    raw = detail.get(detail_key) or _trace_stage_detail(trace, stage_key, limit=limit)
    return guard_visible_text(
        sanitize_text(raw, limit=limit),
        surface=surface,
        allow_direct_media=allow_direct_media,
        enforce_role_integrity=False if detail_key == "incoming_text" else True,
    )[:limit]


def _finite_trace_timestamp(value: Any) -> float:
    try:
        timestamp = float(value or 0)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    return timestamp if timestamp > 0 and math.isfinite(timestamp) else 0.0


def _trace_timestamps(trace: dict[str, Any]) -> tuple[float, float | None]:
    detail = trace.get("detail") if isinstance(trace.get("detail"), dict) else {}
    stages = trace.get("stages") if isinstance(trace.get("stages"), list) else []
    first_stage_ts = 0.0
    for stage in stages:
        if not isinstance(stage, dict):
            continue
        first_stage_ts = _finite_trace_timestamp(stage.get("ts"))
        if first_stage_ts > 0:
            break
    started_at = _finite_trace_timestamp(detail.get("started_at"))
    if started_at <= 0:
        started_at = first_stage_ts or _finite_trace_timestamp(trace.get("ts"))
    finished_at = _finite_trace_timestamp(detail.get("finished_at"))
    if finished_at <= 0 and trace.get("outcome"):
        finished_at = _finite_trace_timestamp(trace.get("ts"))
    return started_at, (finished_at if finished_at > 0 else None)


def _trace_summary(trace: dict[str, Any]) -> dict[str, Any]:
    process = reply_turn_trace.build_process_view(trace, logs=[])
    summary = process.get("summary") if isinstance(process, dict) else {}
    started_at, finished_at = _trace_timestamps(trace)
    input_summary = _trace_visible_message(
        trace,
        detail_key="incoming_text",
        stage_key="incoming_message",
        surface="webui_v2_trace_input",
        allow_direct_media=False,
        limit=2000,
    )
    return {
        "trace_id": str(trace.get("trace_id") or ""),
        "ts": _finite_trace_timestamp(trace.get("ts")),
        "started_at": _iso(started_at),
        "finished_at": _iso(finished_at),
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
        "input_summary": input_summary,
        "elapsed_ms": (
            int(max(0.0, finished_at - started_at) * 1000)
            if finished_at is not None and started_at > 0
            else None
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
    incoming = _trace_visible_message(
        trace,
        detail_key="incoming_text",
        stage_key="incoming_message",
        surface="webui_v2_trace_input",
        allow_direct_media=False,
        limit=2000,
    )
    outgoing = _trace_visible_message(
        trace,
        detail_key="outgoing_text",
        stage_key="outgoing_message",
        surface="webui_v2_trace_output",
        # New traces persist confirmed media as semantic placeholders.  Old
        # records may still contain a direct IMAGE_URL/IMAGE_B64 envelope;
        # never copy that payload into the administrative JSON response.
        allow_direct_media=False,
        limit=6000,
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
                "argument_summary": str(tool.get("argument_summary") or "")[:500],
                "result_summary": str(tool.get("result_summary") or "")[:1000],
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
        rows.append(
            {
                "qq_id": user_id,
                "user_id": user_id,
                "nickname": nickname,
                "avatar_url": qq_avatar_url(user_id),
                "recent_group_id": str(qq_profile.get("last_group_id", "") or ""),
                "favorability_score": round(score, 2),
                "favorability_level": level,
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
    membership_state: str,
    include_unconfirmed: bool,
    enabled: str,
    bot_id: str,
    sort_by: str,
    direction: str,
) -> list[dict[str, Any]]:
    from ...core.group_directory import list_cached_group_union
    from ...utils import is_group_whitelisted

    config_whitelist = list(getattr(runtime.plugin_config, "personification_whitelist", []) or [])
    needle = str(search or "").strip().casefold()
    state_filter = str(membership_state or "").strip()
    bot_filter = str(bot_id or "").strip()
    enabled_filter = str(enabled or "").strip().lower()
    rows: list[dict[str, Any]] = []
    for raw in list_cached_group_union(runtime):
        group_id = str(raw.get("group_id", "") or "")
        group_name = str(raw.get("group_name", "") or "")
        if needle and needle not in group_id.casefold() and needle not in group_name.casefold():
            continue
        sources = {str(value or "") for value in raw.get("sources") or []}
        memberships = [item for item in raw.get("memberships") or [] if isinstance(item, dict)]
        provenance = {
            str(key or "")
            for item in memberships
            for key in (item.get("provenance") or {})
        }
        if provenance.intersection({"onebot_group_list", "onebot_group_info_probe", "message_event"}):
            relationship = "confirmed"
        elif sources.intersection({"config_whitelist", "dynamic_whitelist", "group_config"}):
            relationship = "configured"
        else:
            relationship = "unconfirmed"
        if relationship == "unconfirmed" and not include_unconfirmed:
            continue
        if state_filter and relationship != state_filter:
            continue
        bot_ids = [str(value or "") for value in raw.get("bot_self_ids") or [] if str(value or "")]
        if bot_filter and bot_ids and bot_filter not in bot_ids:
            continue
        is_enabled = bool(is_group_whitelisted(group_id, config_whitelist))
        if enabled_filter in {"true", "1", "enabled"} and not is_enabled:
            continue
        if enabled_filter in {"false", "0", "disabled"} and is_enabled:
            continue
        rows.append(
            {
                **raw,
                "avatar_url": group_avatar_url(group_id),
                "bot_ids": bot_ids,
                "membership_state": relationship,
                "enabled": is_enabled,
                "member_count": raw.get("member_count") if isinstance(raw.get("member_count"), int) else None,
                "last_active_at": float(raw.get("freshness", 0) or 0) or None,
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


def _rebuild_admin_index(runtime: Any) -> dict[str, Any]:
    from ...core.sticker_catalog_index import load_sticker_catalog_index
    from ...core.webui_admin_index import get_webui_admin_index
    from ...utils import load_group_configs, load_whitelist

    personas = _cached_persona_rows(
        runtime,
        search="",
        group_id="",
        favorability_level="",
        sort_by="updated_at",
        direction="desc",
    )
    groups = _cached_group_rows(
        runtime,
        search="",
        membership_state="",
        include_unconfirmed=True,
        enabled="",
        bot_id="",
        sort_by="group_id",
        direction="asc",
    )
    configured = {str(value or "") for value in getattr(runtime.plugin_config, "personification_whitelist", []) or []}
    dynamic = {str(value or "") for value in load_whitelist()}
    group_configs = load_group_configs()
    for row in groups:
        group_id = str(row.get("group_id") or "")
        config = group_configs.get(group_id, {}) if isinstance(group_configs, dict) else {}
        if isinstance(config, dict) and "enabled" in config:
            source = "group_config"
        elif group_id in configured:
            source = "config_file"
        elif group_id in dynamic:
            source = "dynamic"
        else:
            source = "none"
        row["source"] = source
        row["static_config_readonly"] = group_id in configured
    snapshot = load_sticker_catalog_index(_sticker_root(runtime))
    stickers = [dict(item) for item in snapshot.get("items") or [] if isinstance(item, dict)]
    index = get_webui_admin_index(getattr(runtime, "plugin_config", None))
    return index.rebuild(personas=personas, groups=groups, stickers=stickers)


def _get_admin_index(runtime: Any) -> Any:
    from ...core.webui_admin_index import get_webui_admin_index

    return get_webui_admin_index(getattr(runtime, "plugin_config", None))


async def _queue_admin_index_rebuild(runtime: Any, *, force: bool = False) -> dict[str, Any]:
    index = _get_admin_index(runtime)
    key = str(index.path.resolve())
    current = _ADMIN_INDEX_TASKS.get(key)
    if current and current.get("state") in {"queued", "running"} and not force:
        return dict(current)
    task = {
        "state": "queued",
        "queued_at": time.time(),
        "finished_at": None,
        "diagnostic_code": "admin_index_rebuild_queued",
    }
    _ADMIN_INDEX_TASKS[key] = task

    async def runner() -> None:
        task.update({"state": "running", "started_at": time.time(), "diagnostic_code": "admin_index_rebuilding"})
        publish_runtime_event("admin_index.updated", payload={"state": "running", "diagnostic_code": task["diagnostic_code"]})
        try:
            status = await run_in_threadpool(_rebuild_admin_index, runtime)
        except Exception as exc:
            task.update({"state": "failed", "finished_at": time.time(), "diagnostic_code": f"admin_index_rebuild_failed:{type(exc).__name__}"})
        else:
            task.update({"state": "succeeded", "finished_at": time.time(), "diagnostic_code": "admin_index_ready", "index": status})
        publish_runtime_event("admin_index.updated", payload={"state": task["state"], "diagnostic_code": task["diagnostic_code"]})

    asyncio.create_task(runner())
    return dict(task)


async def _admin_index(runtime: Any) -> Any:
    index = _get_admin_index(runtime)
    status = await run_in_threadpool(index.status)
    indexed_at = float(status.get("indexed_at", 0) or 0)
    if str(status.get("state") or "") != "ready" or indexed_at <= 0 or time.time() - indexed_at > 300:
        await _queue_admin_index_rebuild(runtime)
    return index


def _config_rows(runtime: Any, *, search: str, group: str) -> list[dict[str, Any]]:
    from ...core import config_registry
    from ...core.sensitive_data import sanitize_object
    from .config_routes import _mask_api_pool_config

    needle = str(search or "").strip().casefold()
    group_filter = str(group or "").strip()
    rows: list[dict[str, Any]] = []
    for entry in config_registry.get_config_entries():
        if entry.field_name in {
            "personification_quota_anthropic_monthly_tokens",
            "personification_quota_openai_monthly_tokens",
            "personification_quota_gemini_cli_monthly_tokens",
            "personification_quota_codex_monthly_tokens",
        }:
            # One-release compatibility fields remain readable by the legacy
            # configuration API but are not editable in the Vue console.
            continue
        if group_filter and entry.group != group_filter:
            continue
        haystack = " ".join(
            (
                entry.field_name,
                entry.display_name,
                entry.description,
                entry.group,
                entry.category,
                " ".join(entry.help_aliases),
            )
        ).casefold()
        if needle and needle not in haystack:
            continue
        value = config_registry.read_config_value(
            entry,
            plugin_config=runtime.plugin_config,
        )
        if entry.secret:
            safe_value: Any = "***" if value not in (None, "", [], {}) else ""
        elif entry.field_name == "personification_api_pools":
            safe_value = _mask_api_pool_config(value)
        else:
            safe_value = sanitize_object(value)
        rows.append(
            {
                "key": entry.key,
                "field_name": entry.field_name,
                "display_name": entry.display_name,
                "description": entry.description,
                "group": entry.group,
                "category": entry.category,
                "scope": entry.scope,
                "kind": entry.kind,
                "value_type": entry.value_type,
                "value": safe_value,
                "default": "***" if entry.secret and entry.default else sanitize_object(entry.default),
                "secret": bool(entry.secret),
                "advanced": bool(entry.advanced),
                "hot_reloadable": bool(entry.hot_reloadable),
                "restart_required": not bool(entry.hot_reloadable),
                "required": bool(entry.required),
                "modified": value != entry.default,
                "aliases": list(entry.help_aliases),
                "choices": list(entry.choices),
                "min_value": entry.min_value,
                "max_value": entry.max_value,
                "ui_schema": dict(getattr(entry, "ui_schema", None) or {}),
            }
        )
    rows.sort(key=lambda item: (str(item["group"]), str(item["display_name"]), str(item["field_name"])))
    return rows


def _config_snapshot(runtime: Any) -> tuple[str, list[dict[str, Any]]]:
    revision = config_revision(runtime.plugin_config)
    cached = _CONFIG_SNAPSHOT_CACHE.get(revision)
    if cached is not None:
        return revision, [dict(item) for item in cached]
    rows = _config_rows(runtime, search="", group="")
    _CONFIG_SNAPSHOT_CACHE.clear()
    _CONFIG_SNAPSHOT_CACHE[revision] = [dict(item) for item in rows]
    return revision, rows


def _sticker_root(runtime: Any) -> Any:
    from ...core.sticker_library import resolve_sticker_dir

    configured = getattr(runtime.plugin_config, "personification_sticker_path", None)
    return resolve_sticker_dir(configured or "data/stickers", create=True)


def _binary_dependency(name: str) -> dict[str, Any]:
    executable = shutil.which(name)
    if not executable:
        return {"available": False, "version": "", "diagnostic_code": f"{name}_missing"}
    try:
        completed = subprocess.run(
            [executable, "-version"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        first_line = str(completed.stdout or completed.stderr or "").splitlines()[0][:240]
        return {
            "available": completed.returncode == 0,
            "version": first_line,
            "diagnostic_code": f"{name}_ready" if completed.returncode == 0 else f"{name}_version_failed",
        }
    except Exception as exc:
        return {"available": False, "version": "", "diagnostic_code": f"{name}_check_failed:{type(exc).__name__}"}


def _route_probe_catalog() -> dict[str, dict[str, Any]]:
    """Return stable, non-sensitive capability probe metadata for the WebUI."""

    return {
        capability: dict(
            _ROUTE_PROBE_CATALOG.get(
                capability,
                {
                    "probe_id": "none",
                    "available": False,
                    "risk": "external_read",
                    "confirmation_required": False,
                    "reason_code": "probe_unavailable",
                },
            )
        )
        for capability in CAPABILITY_NAMES
    }


def _route_probe_task_key(route_fingerprint: str, capability: str) -> tuple[str, str]:
    return str(route_fingerprint or ""), str(capability or "")


def _route_key_for_fingerprint(route_fingerprint: str) -> tuple[str, RouteKey] | None:
    target_fingerprint = str(route_fingerprint or "")
    for item in DEFAULT_ROUTE_CAPABILITY_REGISTRY.snapshot():
        if str(item.get("route_fingerprint") or "") != target_fingerprint:
            continue
        route_name = str(item.get("route_name") or "")
        route_key = DEFAULT_ROUTE_CAPABILITY_REGISTRY.route_key(route_name)
        if route_name and route_key is not None:
            return route_name, route_key
    return None


def _route_probe_statuses(route_fingerprint: str) -> dict[str, str]:
    return {
        capability: str(
            _ROUTE_PROBE_TASKS.get(
                _route_probe_task_key(route_fingerprint, capability), {}
            ).get("status")
            or "idle"
        )
        for capability in CAPABILITY_NAMES
    }


def _route_probe_status(route_fingerprint: str) -> str:
    statuses = set(_route_probe_statuses(route_fingerprint).values())
    for state in ("running", "queued", "failed", "finished"):
        if state in statuses:
            return state
    return "idle"


def _route_probe_timeout_seconds(runtime: Any) -> float:
    raw = getattr(
        getattr(runtime, "plugin_config", None),
        "personification_visual_probe_timeout_seconds",
        45.0,
    )
    try:
        timeout = float(raw)
    except (TypeError, ValueError):
        timeout = 45.0
    return max(5.0, min(120.0, timeout))


def _route_probe_observation_from_error(exc: BaseException) -> CapabilityObservation:
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return CapabilityObservation.TIMEOUT
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None)
    try:
        if int(status_code) >= 500:
            return CapabilityObservation.SERVER_ERROR
    except (TypeError, ValueError):
        pass
    name = type(exc).__name__.lower()
    if any(token in name for token in ("connect", "network", "transport", "socket")):
        return CapabilityObservation.NETWORK_ERROR
    return CapabilityObservation.PROVIDER_REJECTED


def _error_explicitly_rejects_function_calling(exc: BaseException) -> bool:
    """Recognize only unambiguous tool/function capability refusals.

    The text is used solely for local classification and is never retained or
    returned, so an upstream error body cannot become a WebUI diagnostic.
    """

    values = (
        str(getattr(exc, "code", "") or ""),
        str(getattr(exc, "type", "") or ""),
        str(exc or ""),
    )
    normalized = " ".join(values).casefold().replace("_", " ").replace("-", " ")
    phrases = (
        "function calling is not supported",
        "function call is not supported",
        "tool calling is not supported",
        "tool calls are not supported",
        "tools are not supported",
        "tool use is not supported",
        "does not support function calling",
        "does not support tool calling",
        "does not support tool calls",
        "does not support tools",
    )
    if any(phrase in normalized for phrase in phrases):
        return True
    capability_terms = ("function calling", "function call", "tool calling", "tool calls", "tool use", "tools")
    return any(term in normalized for term in capability_terms) and (
        "not supported" in normalized or "unsupported" in normalized
    )


def _error_explicitly_rejects_native_search(exc: BaseException) -> bool:
    """Classify only an unambiguous native-search capability refusal locally."""

    values = (
        str(getattr(exc, "code", "") or ""),
        str(getattr(exc, "type", "") or ""),
        str(exc or ""),
    )
    normalized = " ".join(values).casefold().replace("_", " ").replace("-", " ")
    phrases = (
        "web search is not supported",
        "web search tool is not supported",
        "native search is not supported",
        "browsing is not supported",
        "does not support web search",
        "does not support browsing",
        "web search unavailable",
        "browsing unavailable",
    )
    return any(phrase in normalized for phrase in phrases)


def _error_explicitly_rejects_reasoning(exc: BaseException) -> bool:
    """Classify only an explicit official thinking/reasoning parameter refusal."""

    values = (
        str(getattr(exc, "code", "") or ""),
        str(getattr(exc, "type", "") or ""),
        str(exc or ""),
    )
    normalized = " ".join(values).casefold().replace("_", " ").replace("-", " ")
    phrases = (
        "reasoning is not supported",
        "thinking is not supported",
        "does not support reasoning",
        "does not support thinking",
        "reasoning config is not supported",
        "thinking config is not supported",
        "reasoning effort is not supported",
        "thinking budget is not supported",
        "unexpected keyword argument reasoning",
        "unexpected keyword argument thinking",
    )
    return any(phrase in normalized for phrase in phrases)


def _error_explicitly_rejects_media_input(capability: str, exc: BaseException) -> bool:
    """Classify a media-input refusal without retaining the upstream body."""

    values = (
        str(getattr(exc, "code", "") or ""),
        str(getattr(exc, "type", "") or ""),
        str(exc or ""),
    )
    normalized = " ".join(values).casefold().replace("_", " ").replace("-", " ")
    kind = "audio" if capability == "audio_input" else "video"
    phrases = (
        f"{kind} input is not supported",
        f"{kind} is not supported",
        f"does not support {kind}",
        f"unsupported {kind} input",
        f"unsupported {kind}",
    )
    return any(phrase in normalized for phrase in phrases)


def _normalized_route_probe_api_type(value: Any) -> str:
    """Mirror the caller's public provider families without importing private internals."""

    normalized = str(value or "openai").strip().lower().replace("-", "_")
    if normalized in {"gemini", "gemini_official"}:
        return "gemini_official"
    if normalized in {"gemini_cli", "geminicli"}:
        return "gemini_cli"
    if normalized in {"antigravity_cli", "antigravity", "agy", "agy_cli"}:
        return "antigravity_cli"
    if normalized == "anthropic":
        return "anthropic"
    if normalized in {"openai_codex", "codex"}:
        return "openai_codex"
    return "openai"


def _route_supports_reasoning_probe(provider: dict[str, Any]) -> bool:
    """Return whether the existing caller will send a known official parameter.

    A generic OpenAI-compatible route only has a verified parameter path for
    GPT-5-family models in the existing caller.  Codex intentionally is not
    used here because its caller requests encrypted reasoning continuation
    material, which a health probe must neither request nor retain.
    """

    api_type = _normalized_route_probe_api_type(provider.get("api_type"))
    if api_type == "openai":
        return "gpt-5" in str(provider.get("model", "") or "").casefold()
    return api_type in {"gemini_official", "gemini_cli", "antigravity_cli", "anthropic"}


def _route_probe_media_suffix(capability: str, original_name: str, content_type: str) -> str | None:
    spec = _ROUTE_MEDIA_PROBE_SPECS.get(capability)
    if spec is None:
        return None
    safe_name = Path(str(original_name or "").strip()).name
    if not safe_name or safe_name != str(original_name or "").strip():
        return None
    suffix = Path(safe_name).suffix.casefold()
    allowed_suffixes = tuple(spec.get("mime_suffixes", {}).get(content_type, ()))
    return suffix if suffix and suffix in allowed_suffixes else None


def _route_probe_media_magic_matches(capability: str, suffix: str, header: bytes) -> bool:
    """Perform a small, format-specific signature check before any Provider call.

    MIME/filename checks protect routing while this inexpensive signature check
    rejects obvious arbitrary bytes.  It is deliberately not a media decoder:
    decoder probing would substantially expand the upload surface and is not
    needed before the selected provider receives a bounded one-shot sample.
    """

    prefix = bytes(header or b"")[:32]
    suffix = str(suffix or "").casefold()
    if capability == "audio_input":
        if suffix == ".wav":
            return len(prefix) >= 12 and prefix[:4] == b"RIFF" and prefix[8:12] == b"WAVE"
        if suffix == ".mp3":
            return prefix.startswith(b"ID3") or (
                len(prefix) >= 2 and prefix[0] == 0xFF and (prefix[1] & 0xE0) == 0xE0
            )
        if suffix in {".m4a"}:
            return len(prefix) >= 8 and prefix[4:8] == b"ftyp"
        if suffix == ".aac":
            return len(prefix) >= 2 and prefix[0] == 0xFF and (prefix[1] & 0xF6) == 0xF0
        if suffix in {".ogg", ".opus"}:
            return prefix.startswith(b"OggS")
        if suffix == ".flac":
            return prefix.startswith(b"fLaC")
        if suffix == ".amr":
            return prefix.startswith(b"#!AMR\\n")
        return False
    if capability == "video_input":
        if suffix in {".mp4", ".mov", ".m4v"}:
            return len(prefix) >= 8 and prefix[4:8] == b"ftyp"
        if suffix in {".webm", ".mkv"}:
            return prefix.startswith(b"\x1aE\xdf\xa3")
        if suffix == ".avi":
            return len(prefix) >= 12 and prefix[:4] == b"RIFF" and prefix[8:12] == b"AVI "
    return False


def _cleanup_route_probe_upload(probe_dir: Path) -> None:
    """Remove the generated one-shot media directory without reporting paths."""

    root = probe_dir.parent
    shutil.rmtree(probe_dir, ignore_errors=True)
    try:
        if root.exists() and not any(root.iterdir()):
            root.rmdir()
    except Exception:
        pass


class _RouteMediaProbeConfig:
    """Disable every fallback around one selected native media route."""

    _FALSE_FIELDS = {
        "personification_gemini_web_enabled",
        "personification_gemini_web_risk_acknowledged",
        "personification_mimo_web_asr_enabled",
        "personification_mimo_web_asr_risk_acknowledged",
        "personification_fullmodal_provider_enabled",
        "personification_fallback_enabled",
        "personification_video_fallback_enabled",
        "personification_audio_transcription_enabled",
    }

    def __init__(self, original: Any) -> None:
        self._original = original

    def __getattr__(self, name: str) -> Any:
        if name in self._FALSE_FIELDS:
            return False
        if name == "personification_video_route_mode":
            return "primary"
        if name == "personification_video_storyboard_fallback_enabled":
            return False
        if name == "personification_video_understanding_enabled":
            return True
        return getattr(self._original, name)


class _RouteMediaProbeRuntime:
    """Expose exactly one provider to the existing media-understanding entrypoint."""

    def __init__(self, runtime: Any, provider: dict[str, Any]) -> None:
        self.plugin_config = _RouteMediaProbeConfig(getattr(runtime, "plugin_config", None))
        self.logger = getattr(runtime, "logger", None)
        self._provider = dict(provider)

    def get_configured_api_providers(self) -> list[dict[str, Any]]:
        return [dict(self._provider)]


def _record_route_probe_observation(
    route_key: RouteKey,
    capability: str,
    observation: CapabilityObservation,
    detail_code: str,
) -> None:
    DEFAULT_ROUTE_CAPABILITY_REGISTRY.record_observation(
        route_key,
        capability,
        observation,
        detail_code=detail_code,
    )


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
    route = _route_key_for_fingerprint(route_fingerprint)
    if route is None:
        return None
    route_name, route_key = route
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


def _record_unavailable_route_probe(
    route_fingerprint: str,
    capability: str,
    detail_code: str = "probe_route_caller_unavailable",
) -> None:
    route = _route_key_for_fingerprint(route_fingerprint)
    if route is None:
        return
    _route_name, route_key = route
    _record_route_probe_observation(
        route_key,
        capability,
        CapabilityObservation.PROBE_UNAVAILABLE,
        detail_code,
    )


async def _run_route_visual_probe(runtime: Any, route_fingerprint: str) -> tuple[str, str]:
    target = _route_probe_target(runtime, route_fingerprint)
    if target is None:
        _record_unavailable_route_probe(route_fingerprint, "image_input")
        return "unknown", "probe_route_caller_unavailable"
    route_name, route_key, provider = target
    from ...core.ai_routes import build_single_provider_caller
    from ...core.visual_capabilities import probe_tool_caller_vision

    try:
        caller = build_single_provider_caller(runtime.plugin_config, provider)
    except Exception:
        _record_route_probe_observation(
            route_key,
            "image_input",
            CapabilityObservation.NETWORK_ERROR,
            "probe_caller_build_failed",
        )
        return "unknown", "probe_caller_build_failed"
    try:
        result = await probe_tool_caller_vision(
            route_name=route_name,
            caller=caller,
            api_type=str(provider.get("api_type", "") or ""),
            model=str(provider.get("model", "") or ""),
            logger=getattr(runtime, "logger", None) or _SilentProbeLogger(),
            timeout_seconds=_route_probe_timeout_seconds(runtime),
        )
    except Exception as exc:
        observation = _route_probe_observation_from_error(exc)
        code = f"probe_visual_{observation.value}"
        _record_route_probe_observation(route_key, "image_input", observation, code)
        return "unknown", code
    if result is True:
        observation = CapabilityObservation.SUCCESS
        state, code = "supported", "probe_visual_succeeded"
    elif result is False:
        observation = CapabilityObservation.EXPLICIT_UNSUPPORTED
        state, code = "unsupported", "probe_visual_explicitly_unsupported"
    else:
        observation = CapabilityObservation.PARSE_ERROR
        state, code = "unknown", "probe_visual_inconclusive"
    _record_route_probe_observation(
        route_key,
        "image_input",
        observation,
        code,
    )
    return state, code


_FUNCTION_CALL_NOOP_TOOL = {
    "type": "function",
    "function": {
        "name": "personification_capability_noop",
        "description": "Capability probe only. This tool has no side effects and must not be executed.",
        "parameters": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
}


def _has_structured_noop_tool_call(response: Any) -> bool:
    for tool_call in list(getattr(response, "tool_calls", None) or []):
        if isinstance(tool_call, dict):
            name = str(tool_call.get("name") or "")
            arguments = tool_call.get("arguments")
            if not name and isinstance(tool_call.get("function"), dict):
                function = tool_call["function"]
                name = str(function.get("name") or "")
                arguments = function.get("arguments", arguments)
        else:
            name = str(getattr(tool_call, "name", "") or "")
            arguments = getattr(tool_call, "arguments", None)
        if name == "personification_capability_noop" and isinstance(arguments, dict):
            return True
    return False


async def _run_route_function_call_probe(runtime: Any, route_fingerprint: str) -> tuple[str, str]:
    target = _route_probe_target(runtime, route_fingerprint)
    if target is None:
        _record_unavailable_route_probe(route_fingerprint, "function_call")
        return "unknown", "probe_route_caller_unavailable"
    _route_name, route_key, provider = target
    from ...core.ai_routes import build_single_provider_caller

    try:
        caller = build_single_provider_caller(runtime.plugin_config, provider)
    except Exception:
        _record_route_probe_observation(
            route_key,
            "function_call",
            CapabilityObservation.NETWORK_ERROR,
            "probe_caller_build_failed",
        )
        return "unknown", "probe_caller_build_failed"

    try:
        response = await asyncio.wait_for(
            caller.chat_with_tools(
                [
                    {
                        "role": "user",
                        "content": (
                            "仅调用提供的 personification_capability_noop 工具。"
                            "它没有副作用；不要输出解释，也不要执行任何工具。"
                        ),
                    }
                ],
                [_FUNCTION_CALL_NOOP_TOOL],
                False,
            ),
            timeout=_route_probe_timeout_seconds(runtime),
        )
    except Exception as exc:
        if _error_explicitly_rejects_function_calling(exc):
            observation = CapabilityObservation.EXPLICIT_UNSUPPORTED
            code = "function_call_probe_explicitly_unsupported"
        else:
            observation = _route_probe_observation_from_error(exc)
            code = f"function_call_probe_{observation.value}"
        _record_route_probe_observation(route_key, "function_call", observation, code)
        return ("unsupported" if observation == CapabilityObservation.EXPLICIT_UNSUPPORTED else "unknown"), code

    if _has_structured_noop_tool_call(response):
        _record_route_probe_observation(
            route_key,
            "function_call",
            CapabilityObservation.SUCCESS,
            "function_call_noop_structured_tool_call",
        )
        return "supported", "function_call_noop_structured_tool_call"

    _record_route_probe_observation(
        route_key,
        "function_call",
        CapabilityObservation.PARSE_ERROR,
        "function_call_probe_inconclusive",
    )
    return "unknown", "function_call_probe_inconclusive"


async def _run_route_native_search_probe(runtime: Any, route_fingerprint: str) -> tuple[str, str]:
    """Run one confirmed, read-only native-search call against the selected route.

    The response is intentionally never returned, logged, or saved.  Evidence
    is limited to the caller's structural ``used_builtin_search`` marker and
    whether there is a non-empty visible answer.
    """

    target = _route_probe_target(runtime, route_fingerprint)
    if target is None:
        _record_unavailable_route_probe(route_fingerprint, "native_web_search")
        return "unknown", "probe_route_caller_unavailable"
    _route_name, route_key, provider = target
    from ...core.ai_routes import build_single_provider_caller

    try:
        caller = build_single_provider_caller(runtime.plugin_config, provider)
    except Exception:
        _record_route_probe_observation(
            route_key,
            "native_web_search",
            CapabilityObservation.NETWORK_ERROR,
            "probe_caller_build_failed",
        )
        return "unknown", "probe_caller_build_failed"

    try:
        response = await asyncio.wait_for(
            caller.chat_with_tools(
                [
                    {
                        "role": "user",
                        "content": (
                            "请使用当前 Provider 的内置联网搜索完成一个公开、低风险的确定性查询："
                            "太阳系中离太阳最近的行星是什么？只给出简短可见答案。"
                        ),
                    }
                ],
                [],
                True,
            ),
            timeout=_route_probe_timeout_seconds(runtime),
        )
    except Exception as exc:
        if _error_explicitly_rejects_native_search(exc):
            observation = CapabilityObservation.EXPLICIT_UNSUPPORTED
            code = "native_search_probe_explicitly_unsupported"
        else:
            observation = _route_probe_observation_from_error(exc)
            code = f"native_search_probe_{observation.value}"
        _record_route_probe_observation(route_key, "native_web_search", observation, code)
        return ("unsupported" if observation == CapabilityObservation.EXPLICIT_UNSUPPORTED else "unknown"), code

    # Do not retain the content.  Both conditions are required so a provider
    # that merely answers from memory after a search fallback is not verified.
    visible_answer = bool(str(getattr(response, "content", "") or "").strip())
    used_builtin_search = getattr(response, "used_builtin_search", None) is True
    if visible_answer and used_builtin_search:
        _record_route_probe_observation(
            route_key,
            "native_web_search",
            CapabilityObservation.SUCCESS,
            "native_search_readonly_visible_answer",
        )
        return "supported", "native_search_readonly_visible_answer"

    _record_route_probe_observation(
        route_key,
        "native_web_search",
        CapabilityObservation.PARSE_ERROR,
        "native_search_probe_inconclusive",
    )
    return "unknown", "native_search_probe_inconclusive"


async def _run_route_reasoning_probe(runtime: Any, route_fingerprint: str) -> tuple[str, str]:
    """Verify an existing official thinking parameter path without observing thought.

    The probe uses a fixed low budget and only checks that the caller completed
    with a visible answer.  It deliberately never accesses ``raw``, provider
    history, thought signatures, encrypted reasoning, or response internals.
    """

    target = _route_probe_target(runtime, route_fingerprint)
    if target is None:
        _record_unavailable_route_probe(route_fingerprint, "reasoning")
        return "unknown", "probe_route_caller_unavailable"
    _route_name, route_key, provider = target
    if not _route_supports_reasoning_probe(provider):
        _record_route_probe_observation(
            route_key,
            "reasoning",
            CapabilityObservation.PROBE_UNAVAILABLE,
            "reasoning_probe_official_path_unavailable",
        )
        return "unknown", "reasoning_probe_official_path_unavailable"

    from ...core.ai_routes import build_single_provider_caller

    try:
        caller = build_single_provider_caller(
            runtime.plugin_config,
            provider,
            thinking_mode_override="low",
        )
    except Exception:
        _record_route_probe_observation(
            route_key,
            "reasoning",
            CapabilityObservation.NETWORK_ERROR,
            "probe_caller_build_failed",
        )
        return "unknown", "probe_caller_build_failed"

    try:
        response = await asyncio.wait_for(
            caller.chat_with_tools(
                [
                    {
                        "role": "user",
                        "content": "请完成一个确定性短任务：只输出 2 + 2 的十进制结果。不要解释推理过程。",
                    }
                ],
                [],
                False,
            ),
            timeout=_route_probe_timeout_seconds(runtime),
        )
    except Exception as exc:
        if _error_explicitly_rejects_reasoning(exc):
            observation = CapabilityObservation.EXPLICIT_UNSUPPORTED
            code = "reasoning_probe_explicitly_unsupported"
        else:
            observation = _route_probe_observation_from_error(exc)
            code = f"reasoning_probe_{observation.value}"
        _record_route_probe_observation(route_key, "reasoning", observation, code)
        return ("unsupported" if observation == CapabilityObservation.EXPLICIT_UNSUPPORTED else "unknown"), code

    # OpenAI-compatible callers intentionally retry without ``reasoning`` for
    # an SDK-level unexpected-keyword refusal.  Treat that explicit fallback
    # as a refusal instead of claiming a successful reasoning verification.
    if getattr(caller, "_supports_reasoning", None) is False:
        _record_route_probe_observation(
            route_key,
            "reasoning",
            CapabilityObservation.EXPLICIT_UNSUPPORTED,
            "reasoning_probe_explicitly_unsupported",
        )
        return "unsupported", "reasoning_probe_explicitly_unsupported"

    if bool(str(getattr(response, "content", "") or "").strip()):
        _record_route_probe_observation(
            route_key,
            "reasoning",
            CapabilityObservation.SUCCESS,
            "reasoning_minimal_visible_answer",
        )
        return "supported", "reasoning_minimal_visible_answer"

    _record_route_probe_observation(
        route_key,
        "reasoning",
        CapabilityObservation.PARSE_ERROR,
        "reasoning_probe_inconclusive",
    )
    return "unknown", "reasoning_probe_inconclusive"


async def _run_route_media_probe(
    runtime: Any,
    route_fingerprint: str,
    capability: str,
    media_path: Path | None,
) -> tuple[str, str]:
    """Run one selected-route native media probe and retain no media output."""

    if capability not in _ROUTE_MEDIA_PROBE_SPECS:
        return "unknown", "probe_unavailable"
    target = _route_probe_target(runtime, route_fingerprint)
    if target is None:
        _record_unavailable_route_probe(route_fingerprint, capability)
        return "unknown", "probe_route_caller_unavailable"
    _route_name, route_key, provider = target
    if media_path is None or not media_path.is_file():
        _record_route_probe_observation(
            route_key,
            capability,
            CapabilityObservation.PROBE_UNAVAILABLE,
            "media_probe_upload_required",
        )
        return "unknown", "media_probe_upload_required"

    from ...core.media_provider_adapters import resolve_media_provider_adapter

    adapter = resolve_media_provider_adapter(provider)
    supported = adapter.supports_audio if capability == "audio_input" else adapter.supports_video
    if not supported:
        _record_route_probe_observation(
            route_key,
            capability,
            CapabilityObservation.PROBE_UNAVAILABLE,
            "media_probe_primary_route_unavailable",
        )
        return "unknown", "media_probe_primary_route_unavailable"

    from ...core.media_understanding import (
        analyze_audios_with_route_or_fallback,
        analyze_videos_with_route_or_fallback,
    )

    probe_runtime = _RouteMediaProbeRuntime(runtime, provider)
    try:
        if capability == "audio_input":
            response, _route = await asyncio.wait_for(
                analyze_audios_with_route_or_fallback(
                    runtime=probe_runtime,
                    prompt="请仅回复“已接收”。不要转写、描述、引用或保存音频内容。",
                    audio_refs=[str(media_path)],
                ),
                timeout=_route_probe_timeout_seconds(runtime),
            )
        else:
            response, _route = await asyncio.wait_for(
                analyze_videos_with_route_or_fallback(
                    runtime=probe_runtime,
                    prompt="请仅回复“已接收”。不要转写、描述、引用或保存视频内容。",
                    video_refs=[str(media_path)],
                ),
                timeout=_route_probe_timeout_seconds(runtime),
            )
    except Exception as exc:
        if _error_explicitly_rejects_media_input(capability, exc):
            observation = CapabilityObservation.EXPLICIT_UNSUPPORTED
            code = f"{capability}_probe_explicitly_unsupported"
        else:
            observation = _route_probe_observation_from_error(exc)
            code = f"{capability}_probe_{observation.value}"
        _record_route_probe_observation(route_key, capability, observation, code)
        return ("unsupported" if observation == CapabilityObservation.EXPLICIT_UNSUPPORTED else "unknown"), code

    # Only the existence of a visible answer is observed; the media-derived
    # content, local path, route attempts, and any raw provider payload are
    # never persisted or sent back to the browser.
    if bool(str(response or "").strip()):
        code = f"{capability}_native_media_visible_answer"
        _record_route_probe_observation(route_key, capability, CapabilityObservation.SUCCESS, code)
        return "supported", code

    code = f"{capability}_probe_inconclusive"
    _record_route_probe_observation(route_key, capability, CapabilityObservation.PARSE_ERROR, code)
    return "unknown", code


async def _run_route_capability_probe(
    runtime: Any,
    route_fingerprint: str,
    capability: str,
    *,
    media_path: Path | None = None,
) -> tuple[str, str]:
    if capability == "image_input":
        return await _run_route_visual_probe(runtime, route_fingerprint)
    if capability == "function_call":
        return await _run_route_function_call_probe(runtime, route_fingerprint)
    if capability == "native_web_search":
        return await _run_route_native_search_probe(runtime, route_fingerprint)
    if capability == "reasoning":
        return await _run_route_reasoning_probe(runtime, route_fingerprint)
    if capability in _ROUTE_MEDIA_PROBE_SPECS:
        return await _run_route_media_probe(runtime, route_fingerprint, capability, media_path)
    return "unknown", "probe_unavailable"


class _SilentProbeLogger:
    def warning(self, _message: str) -> None:
        return None


class _NoSendCaptureBot:
    """Delegate read APIs but capture every send API without touching QQ."""

    def __init__(self, real: Any, *, trace_id: str) -> None:
        self._real = real
        self.self_id = getattr(real, "self_id", "")
        self.trace_id = trace_id
        self.captured: list[str] = []

    def _capture(self, message: Any) -> dict[str, Any]:
        visible = guard_visible_text(
            str(message or ""),
            surface="webui_video_turn_capture",
            allow_direct_media=True,
        )[:6000]
        if visible:
            self.captured.append(visible)
        reply_turn_trace.record_stage(
            trace_id=self.trace_id,
            key="send_suppressed",
            label="无发送测试捕获",
            status="ok",
            detail="可见回复已捕获，未调用 OneBot 发送接口。",
        )
        return {"message_id": 0, "captured": True, "not_sent": True}

    async def send(self, _event: Any, message: Any, **_kwargs: Any) -> dict[str, Any]:
        return self._capture(message)

    async def call_api(self, api: str, **data: Any) -> Any:
        from .health_routes import _SEND_API_ACTIONS

        if str(api or "").strip() in _SEND_API_ACTIONS:
            return self._capture(data.get("message", ""))
        return await self._real.call_api(api, **data)

    async def send_msg(self, *, message: Any = "", **_data: Any) -> dict[str, Any]:
        return self._capture(message)

    async def send_group_msg(self, *, message: Any = "", **_data: Any) -> dict[str, Any]:
        return self._capture(message)

    async def send_private_msg(self, *, message: Any = "", **_data: Any) -> dict[str, Any]:
        return self._capture(message)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)


def _functional_test_definition(test_id: str) -> dict[str, Any] | None:
    normalized = str(test_id or "").strip()
    return next((dict(item) for item in _FUNCTIONAL_TEST_CATALOG if item["id"] == normalized), None)


def _functional_step_plan(run: dict[str, Any], *, status: str, message: str) -> tuple[Any, ...]:
    execution_kind = str(run.get("execution_kind") or "local_readonly")
    if execution_kind == "qq_canary":
        stage_status = "skipped" if status == "skipped" else "pending"
        return (
            operation_step("rules", "规则判断", stage_status, "专用 canary 才会进入真实规则判断。"),
            operation_step("buffer", "上下文缓冲", stage_status, "专用 canary 才会构建受控上下文。"),
            operation_step("model", "模型生成", stage_status, "专用 canary 才会调用已确认的模型路由。"),
            operation_step("review", "可见输出审核", stage_status, "专用 canary 必须先完成可见输出审核。"),
            operation_step("ledger", "出站账本", stage_status, "专用 canary 必须建立可对账的出站账本记录。"),
            operation_step("send", "QQ 发送", "skipped", "本体检页面和自动测试绝不调用 QQ 发送接口。"),
        )
    if execution_kind == "provider_probe":
        label = "Provider 外部读取探针"
        key = "provider_probe"
    elif execution_kind == "qzone_canary":
        label = "QZone canary"
        key = "qzone_canary"
    else:
        label = "本地只读检查"
        key = "local_readonly"
    return (
        operation_step(key, label, status, message),
        operation_step(
            "delivery",
            "QQ 交付",
            "skipped",
            "本次体检不会通过 QQ 发送消息。真实 QQ canary 必须在专用页面完成。",
        ),
    )


def _set_functional_run_diagnostic(
    run: dict[str, Any],
    *,
    ok: bool,
    code: str,
    phase: str,
    title: str,
    message: str,
    steps: tuple[Any, ...],
    suggestion: str = "",
) -> None:
    diagnostic = operation_diagnostic(
        ok=ok,
        code=code,
        phase=phase,
        title=title,
        message=message,
        steps=steps,
        suggestion=suggestion,
        retryable=False,
        partial=False,
        outcome_unknown=False,
        operation_id=str(run.get("id") or ""),
        trace_id=str(run.get("trace_id") or ""),
    )
    run["diagnostic_code"] = str(diagnostic.get("code") or code)
    run["diagnostic"] = diagnostic
    run["steps"] = list(diagnostic.get("steps") or [])


def _functional_test_view(run: dict[str, Any]) -> dict[str, Any]:
    diagnostic = run.get("diagnostic") if isinstance(run.get("diagnostic"), dict) else {}
    return {
        "id": str(run.get("id") or ""),
        "test_id": str(run.get("test_id") or ""),
        "label": str(run.get("label") or ""),
        "group": str(run.get("group") or "核心运行"),
        "risk": str(run.get("risk") or "local_read"),
        "execution_kind": str(run.get("execution_kind") or "local_readonly"),
        "state": str(run.get("state") or "prepared"),
        "target_summary": str(run.get("target_summary") or "") or None,
        "route_fingerprint": str(run.get("route_fingerprint") or "") or None,
        "trace_id": str(run.get("trace_id") or "") or None,
        "diagnostic_code": str(run.get("diagnostic_code") or "test_prepared"),
        "created_at": _iso(run.get("created_at")),
        "started_at": _iso(run.get("started_at")),
        "finished_at": _iso(run.get("finished_at")),
        "duration_ms": run.get("duration_ms") if isinstance(run.get("duration_ms"), int) else None,
        "steps": list(run.get("steps") or []),
        "diagnostic": dict(diagnostic),
        "result_summary": run.get("result_summary") if isinstance(run.get("result_summary"), dict) else {},
        "delivery_status": str(run.get("delivery_status") or "not_applicable"),
    }


async def _execute_functional_test(runtime: Any, operation_id: str) -> None:
    from ...core.diagnostics import run_diagnostics

    run = _FUNCTIONAL_TEST_RUNS.get(operation_id)
    if run is None:
        return
    started = time.monotonic()
    run.update({"state": "running", "started_at": time.time()})
    _set_functional_run_diagnostic(
        run,
        ok=True,
        code="functional_test_running",
        phase="diagnostic_run",
        title="功能体检运行中",
        message="正在执行受控体检步骤；不会发送 QQ 消息。",
        steps=_functional_step_plan(run, status="running", message="体检检查正在执行。"),
    )
    publish_runtime_event(
        "test_run.updated",
        payload={"operation_id": operation_id, "state": "running", "test_id": run["test_id"]},
    )
    try:
        result = await run_diagnostics(
            plugin_config=getattr(runtime, "plugin_config", None),
            bundle=getattr(runtime, "runtime_bundle", None),
            superusers=getattr(runtime, "superusers", set()),
            get_bots=getattr(runtime, "get_bots", None),
            logger=getattr(runtime, "logger", None),
            only=str(run.get("category") or ""),
            probe_video=False,
        )
        status = str(result.get("overall") or result.get("status") or "unknown") if isinstance(result, dict) else "unknown"
        ok = status not in {"error", "failed", "unknown"}
        categories = result.get("categories") if isinstance(result, dict) and isinstance(result.get("categories"), list) else []
        checks = [
            check
            for category in categories
            if isinstance(category, dict)
            for check in category.get("checks") or []
            if isinstance(check, dict)
        ]
        trace_id = str(result.get("trace_id") or "") if isinstance(result, dict) else ""
        if trace_id:
            run["trace_id"] = trace_id[:128]
        result_summary = {
            "overall": status,
            "check_count": len(checks),
            "failed_count": sum(
                1
                for item in checks
                if isinstance(item, dict)
                and str(item.get("status") or "") not in {"ok", "healthy", "passed", "success"}
            ),
            "execution_kind": str(run.get("execution_kind") or "local_readonly"),
            "delivery_status": "not_applicable",
        }
        run.update(
            {
                "state": "succeeded" if ok else "failed",
                "result_summary": result_summary,
                "delivery_status": "not_applicable",
            }
        )
        code = "functional_test_warning" if ok and status == "warn" else (
            "functional_test_succeeded" if ok else "functional_test_failed"
        )
        _set_functional_run_diagnostic(
            run,
            ok=ok,
            code=code,
            phase="diagnostic_complete",
            title="功能体检完成" if ok else "功能体检未通过",
            message=(
                "体检已完成；结果仅覆盖本地检查或已确认的 Provider 外部读取探针。"
                if ok
                else "体检没有得到明确成功结果；请查看脱敏阶段信息后再处理。"
            ),
            steps=_functional_step_plan(
                run,
                status="warn" if ok and status == "warn" else ("ok" if ok else "error"),
                message="体检步骤已完成。" if ok else "体检步骤未得到明确成功结果。",
            ),
            suggestion="真实 QQ 交付需在专用 canary 流程中由管理员确认。",
        )
    except Exception:
        run.update(
            {
                "state": "failed",
                "result_summary": {},
                "delivery_status": "not_applicable",
            }
        )
        _set_functional_run_diagnostic(
            run,
            ok=False,
            code="functional_test_internal_error",
            phase="diagnostic_run",
            title="功能体检未完成",
            message="体检执行时发生内部异常；未发送 QQ 消息。",
            steps=_functional_step_plan(run, status="error", message="体检执行异常，请查看脱敏诊断。"),
            suggestion="根据 Trace ID 或服务端脱敏日志核对异常类型后再重试。",
        )
    finally:
        run["finished_at"] = time.time()
        run["duration_ms"] = int(max(0.0, time.monotonic() - started) * 1000)
        publish_runtime_event(
            "test_run.updated",
            payload={
                "operation_id": operation_id,
                "state": run["state"],
                "test_id": run["test_id"],
                "diagnostic_code": run["diagnostic_code"],
            },
        )


def build_v2_router(*, runtime: Any) -> APIRouter:
    router = APIRouter(prefix="/api/v2", tags=["v2"])

    @router.get("/bots")
    async def bots(_: AdminIdentity = Depends(require_admin)) -> dict[str, Any]:
        items = await list_bot_identities(runtime)
        return {"items": items, "total": len(items), "diagnostic_code": "bot_identity_snapshot"}

    @router.get("/admin-index/status")
    async def admin_index_status(_: AdminIdentity = Depends(require_admin)) -> dict[str, Any]:
        index = _get_admin_index(runtime)
        status = await run_in_threadpool(index.status)
        task = _ADMIN_INDEX_TASKS.get(str(index.path.resolve()), {"state": "idle", "diagnostic_code": "admin_index_task_idle"})
        return {"index": status, "task": dict(task), "diagnostic_code": "admin_index_status_ready"}

    @router.post("/admin-index/rebuild")
    async def rebuild_admin_index(
        request: Request,
        admin: AdminIdentity = Depends(require_admin),
    ) -> dict[str, Any]:
        task = await _queue_admin_index_rebuild(runtime)
        webui_audit_log.record(
            action="admin_index_rebuild",
            qq=admin.qq,
            device_id=admin.device_id,
            ip_hash=get_client_ip(request),
            detail={"state": task.get("state"), "diagnostic_code": task.get("diagnostic_code")},
            outcome="ok",
        )
        return {
            "ok": True,
            "code": str(task.get("diagnostic_code") or "admin_index_rebuild_queued"),
            "phase": str(task.get("state") or "queued"),
            "title": "管理投影重建已排队",
            "message": "页面继续读取上一个已知投影；后台完成后通过 SSE 发布索引状态。",
            "retryable": False,
            "partial": False,
            "outcome_unknown": False,
            "warnings": [],
            "steps": [],
        }

    @router.get("/health")
    async def health(_: AdminIdentity = Depends(require_admin)) -> dict[str, Any]:
        from ...core.diagnostics import get_cached_diagnostics

        cached = get_cached_diagnostics()
        return {
            "tests": [dict(item) for item in _FUNCTIONAL_TEST_CATALOG],
            "cached": cached if isinstance(cached, dict) else None,
            "diagnostic_code": "functional_test_catalog_ready",
        }

    @router.post("/test-runs/prepare")
    async def prepare_test_run(
        body: dict[str, Any] = Body(default_factory=dict),
        _: AdminIdentity = Depends(require_admin),
    ) -> dict[str, Any]:
        definition = _functional_test_definition(str(body.get("test_id") or ""))
        if definition is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "functional_test_not_found", "message": "未找到该体检项目。"},
            )
        operation_id = uuid.uuid4().hex
        risk = definition["risk"]
        run = {
            "id": operation_id,
            "test_id": definition["id"],
            "label": definition["label"],
            "category": definition["category"],
            "group": definition["group"],
            "risk": risk,
            "execution_kind": definition["execution_kind"],
            "state": "prepared" if risk == "local_read" else "awaiting_confirmation",
            "target_summary": str(body.get("target_summary") or "")[:240],
            "route_fingerprint": str(body.get("route_fingerprint") or "")[:128],
            "trace_id": "",
            "created_at": time.time(),
            "started_at": 0.0,
            "finished_at": 0.0,
            "duration_ms": None,
            "steps": [],
            "diagnostic": {},
            "result_summary": {},
            "delivery_status": (
                "not_started"
                if definition["execution_kind"] in {"qq_canary", "qzone_canary"}
                else "not_applicable"
            ),
        }
        if risk == "local_read":
            _set_functional_run_diagnostic(
                run,
                ok=True,
                code="test_prepared",
                phase="prepared",
                title="本地只读体检已准备",
                message="将执行本地只读检查；不会访问 Provider，也不会发送 QQ 消息。",
                steps=_functional_step_plan(run, status="pending", message="等待本地体检任务开始。"),
            )
        else:
            _set_functional_run_diagnostic(
                run,
                ok=True,
                code="test_confirmation_required",
                phase="confirmation",
                title="体检等待管理员确认",
                message="该项目可能调用外部 Provider 或进入专用 canary 流程，尚未执行。",
                steps=_functional_step_plan(run, status="pending", message="等待管理员确认外部调用范围。"),
            )
        _FUNCTIONAL_TEST_RUNS[operation_id] = run
        if risk == "local_read":
            asyncio.create_task(_execute_functional_test(runtime, operation_id))
        return _functional_test_view(run)

    @router.post("/test-runs/{operation_id}/confirm")
    async def confirm_test_run(
        operation_id: str,
        request: Request,
        body: dict[str, Any] = Body(default_factory=dict),
        _: AdminIdentity = Depends(require_admin),
    ) -> dict[str, Any]:
        run = _FUNCTIONAL_TEST_RUNS.get(str(operation_id or ""))
        if run is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "functional_test_run_not_found", "message": "测试任务不存在或已失效。"},
            )
        if run.get("state") != "awaiting_confirmation":
            raise HTTPException(
                status_code=409,
                detail={"code": "functional_test_state_conflict", "message": "该测试任务当前不等待确认。"},
            )
        if body.get("confirmed") is not True:
            raise HTTPException(
                status_code=422,
                detail={"code": "functional_test_confirmation_missing", "message": "必须明确确认本次外部调用。"},
            )
        if run.get("risk") == "external_write":
            host = str(request.client.host if request.client else "")
            secure = request.url.scheme == "https" or host in {"127.0.0.1", "::1", "localhost", "testclient"}
            if not secure:
                raise HTTPException(
                    status_code=403,
                    detail={"code": "external_write_https_required", "message": "公网 HTTP 连接禁止执行外部写测试。"},
                )
            expected = str(run.get("target_summary") or "")
            if not expected or str(body.get("target_confirmation") or "") != expected:
                raise HTTPException(
                    status_code=422,
                    detail={"code": "external_write_target_mismatch", "message": "目标复核文本不匹配，未执行写操作。"},
                )
            now = time.time()
            run.update(
                {
                    "state": "unknown",
                    "started_at": now,
                    "finished_at": now,
                    "duration_ms": 0,
                    "result_summary": {
                        "execution_kind": str(run.get("execution_kind") or ""),
                        "message": "请在对应 QQ/QZone 专用页面完成带目标字段的单次 canary。",
                    },
                    "delivery_status": "dedicated_canary_required",
                }
            )
            _set_functional_run_diagnostic(
                run,
                ok=False,
                code="external_write_dedicated_canary_required",
                phase="delivery_canary",
                title="真实外部写 canary 需要专用入口",
                message="本体检页面不会发送 QQ 或写入 QZone；管理员确认后仍需在专用页面完成单目标 canary。",
                steps=_functional_step_plan(
                    run,
                    status="skipped",
                    message="为避免无目标外部写入，本页没有执行 canary。",
                ),
                suggestion="在 QQ 或 QZone 专用页面填写明确目标后，再执行一次可对账的 canary。",
            )
            return _functional_test_view(run)
        run.update({"state": "prepared"})
        _set_functional_run_diagnostic(
            run,
            ok=True,
            code="test_confirmed",
            phase="confirmation",
            title="外部读取体检已确认",
            message="管理员已确认 Provider 外部读取范围；任务即将开始，不会发送 QQ 消息。",
            steps=_functional_step_plan(run, status="pending", message="等待受控 Provider 探针任务开始。"),
        )
        asyncio.create_task(_execute_functional_test(runtime, operation_id))
        return _functional_test_view(run)

    @router.get("/test-runs/{operation_id}")
    async def get_test_run(
        operation_id: str,
        _: AdminIdentity = Depends(require_admin),
    ) -> dict[str, Any]:
        run = _FUNCTIONAL_TEST_RUNS.get(str(operation_id or ""))
        if run is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "functional_test_run_not_found", "message": "测试任务不存在或已失效。"},
            )
        return _functional_test_view(run)

    @router.post("/tests/video-turn")
    async def full_video_turn_test(
        request: Request,
        text: str = Query(default="请根据视频内容做简短说明。", max_length=1000),
        admin: AdminIdentity = Depends(require_admin),
    ) -> dict[str, Any]:
        """Run an uploaded video through the production turn chain without sending QQ."""

        from ...core.diagnostics import _video_probe_root
        from ...core.media_refs import is_supported_video_filename
        from .health_routes import (
            _HEALTH_VIDEO_MAX_UPLOAD_BYTES,
            _HEALTH_VIDEO_MIME_SUFFIXES,
            _build_probe_event,
            _dispatch_via_plugin_path,
            _first_bot,
            _interaction_wait_seconds,
            _response_timeout_seconds,
            _stage,
        )

        cfg = getattr(runtime, "plugin_config", None)
        bot = _first_bot(runtime)
        if cfg is None or bot is None:
            raise HTTPException(
                status_code=503,
                detail={"code": "video_turn_runtime_unavailable", "message": "Bot 或回复运行时尚未就绪。"},
            )
        filename = Path(str(request.headers.get("x-personification-video-filename") or "video.bin")).name
        content_type = str(request.headers.get("content-type") or "").split(";", 1)[0].lower()
        suffix = Path(filename).suffix.lower() if is_supported_video_filename(filename) else _HEALTH_VIDEO_MIME_SUFFIXES.get(content_type, "")
        if suffix not in _HEALTH_VIDEO_MIME_SUFFIXES.values():
            raise HTTPException(
                status_code=400,
                detail={"code": "video_turn_invalid_type", "message": "仅支持 MP4、MOV、M4V、WEBM、MKV 或 AVI。"},
            )
        operation_id = uuid.uuid4().hex
        root = _video_probe_root(cfg).resolve()
        probe_dir = root / f"turn-{operation_id}"
        target = probe_dir / f"video{suffix}"
        total = 0
        trace_id = ""
        try:
            probe_dir.mkdir(parents=True, exist_ok=False)
            with target.open("wb") as sink:
                async for chunk in request.stream():
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > _HEALTH_VIDEO_MAX_UPLOAD_BYTES:
                        raise HTTPException(
                            status_code=413,
                            detail={"code": "video_turn_payload_too_large", "message": "视频超过完整回合测试大小上限。"},
                        )
                    sink.write(chunk)
            if total <= 0:
                raise HTTPException(
                    status_code=400,
                    detail={"code": "video_turn_empty_upload", "message": "没有收到视频内容。"},
                )
            user_id = str(admin.qq or "")
            if not user_id.isdigit():
                raise HTTPException(
                    status_code=403,
                    detail={"code": "video_turn_admin_identity_invalid", "message": "当前管理员身份不能用于构造受控回合。"},
                )
            trace_id = reply_turn_trace.start_trace(
                session_type="private",
                group_id="",
                user_id=user_id,
                detail={
                    "source": "webui_video_turn_test",
                    "operator_qq": user_id,
                    "media_kind": "video",
                    "media_size_bytes": total,
                    "text_length": len(text),
                    "text_hash": hashlib.sha256(text.encode("utf-8")).hexdigest()[:16],
                    "outbound_mode": "capture_only",
                },
            )
            stages: list[dict[str, Any]] = []
            event = _build_probe_event(bot, group_id="", user_id=user_id, text=text)
            from nonebot.adapters.onebot.v11 import MessageSegment

            segment = MessageSegment.video(file=target.as_uri())
            event.message += segment
            event.original_message = event.message
            event.raw_message = f"{text}[CQ:video,file=controlled-upload]"
            _stage(stages, trace_id, "video_turn_upload", "受控视频上传", "ok", f"kind=video size_bytes={total}")
            proxy = _NoSendCaptureBot(bot, trace_id=trace_id)
            started = time.monotonic()
            token = reply_turn_trace.set_current_trace_id(trace_id)
            try:
                result = await _dispatch_via_plugin_path(
                    runtime=runtime,
                    bot=bot,
                    proxy=proxy,
                    event=event,
                    trace_id=trace_id,
                    stages=stages,
                    target_label="private",
                    target_detail={"group_id": "", "user_id": user_id},
                    started=started,
                    interaction_wait_seconds=_interaction_wait_seconds(cfg),
                    response_timeout_seconds=_response_timeout_seconds(cfg),
                )
            finally:
                reply_turn_trace.reset_current_trace_id(token)
            if result is None:
                reply_turn_trace.finish_trace(
                    trace_id=trace_id,
                    outcome="failed",
                    diagnosis_code="video_turn_plugin_path_unavailable",
                    detail={"outbound_mode": "capture_only"},
                )
                result = {
                    "replied": False,
                    "reply": "",
                    "trace_id": trace_id,
                    "diagnosis_code": "video_turn_plugin_path_unavailable",
                    "duration_ms": int((time.monotonic() - started) * 1000),
                }
            trace = reply_turn_trace.get_trace(trace_id) or {}
            process = reply_turn_trace.build_process_view(trace, logs=[])
            inspection = process.get("agent_inspection") if isinstance(process, dict) else {}
            tools = inspection.get("tools") if isinstance(inspection, dict) and isinstance(inspection.get("tools"), list) else []
            video_evidence = [
                {
                    "tool": str(item.get("tool") or ""),
                    "status": str(item.get("status") or ""),
                    "detail": str(item.get("detail") or "")[:240],
                }
                for item in tools
                if isinstance(item, dict) and str(item.get("tool") or "") == "vision_analyze"
            ]
            return {
                "ok": bool(result.get("replied")) and bool(video_evidence),
                "code": "video_turn_evidence_complete" if result.get("replied") and video_evidence else "video_turn_evidence_incomplete",
                "operation_id": operation_id,
                "trace_id": trace_id,
                "reply": str(result.get("reply") or "")[:6000],
                "duration_ms": result.get("duration_ms"),
                "diagnosis_code": str(result.get("diagnosis_code") or ""),
                "media_evidence": video_evidence,
                "outbound": "captured_not_sent",
            }
        finally:
            shutil.rmtree(probe_dir, ignore_errors=True)
            try:
                if root.exists() and not any(root.iterdir()):
                    root.rmdir()
            except Exception:
                pass

    @router.post("/tests/video-route")
    async def video_route_probe(
        _: AdminIdentity = Depends(require_admin),
    ) -> RedirectResponse:
        return RedirectResponse(url="/personification/api/health/video-probe", status_code=307)

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
        try:
            index = await _admin_index(runtime)
            payload = await run_in_threadpool(
                index.personas_page,
                page=page,
                page_size=page_size,
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
        for item in payload["items"]:
            item["avatar_url"] = qq_avatar_url(str(item.get("qq_id") or ""))
        payload["index"] = index.status()
        return payload

    @router.get("/groups")
    async def groups(
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=20, ge=1, le=100),
        search: str = Query(default="", max_length=120),
        membership_state: str = Query(default="", pattern="^(|confirmed|configured|unconfirmed)$"),
        include_unconfirmed: bool = Query(default=False),
        enabled: str = Query(default="", pattern="^(|true|false|1|0|enabled|disabled)$"),
        bot_id: str = Query(default="", max_length=64),
        sort_by: str = Query(default="group_id", max_length=32),
        direction: str = Query(default="asc", pattern="^(asc|desc)$"),
        _: AdminIdentity = Depends(require_admin),
    ) -> dict[str, Any]:
        try:
            index = await _admin_index(runtime)
            payload = await run_in_threadpool(
                index.groups_page,
                page=page,
                page_size=page_size,
                search=search,
                membership_state=membership_state,
                include_unconfirmed=include_unconfirmed,
                enabled=enabled,
                bot_id=bot_id,
                sort_by=sort_by,
                direction=direction,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail={"code": "group_sort_invalid", "message": "群列表排序字段无效。"},
            ) from exc
        for item in payload["items"]:
            item["avatar_url"] = group_avatar_url(str(item.get("group_id") or ""))
        payload["index"] = index.status()
        return payload

    @router.get("/group-switches")
    async def group_switches(
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=20, ge=1, le=100),
        search: str = Query(default="", max_length=120),
        enabled: str = Query(default="", max_length=16),
        membership_state: str = Query(default="", max_length=24),
        bot_id: str = Query(default="", max_length=32),
        _: AdminIdentity = Depends(require_admin),
    ) -> dict[str, Any]:
        index = await _admin_index(runtime)
        payload = await run_in_threadpool(
            index.groups_page,
            page=page,
            page_size=page_size,
            search=search,
            membership_state=membership_state,
            include_unconfirmed=False,
            enabled=enabled,
            bot_id=bot_id,
            sort_by="group_id",
            direction="asc",
        )
        for item in payload["items"]:
            item["avatar_url"] = group_avatar_url(str(item.get("group_id") or ""))
        counts = await run_in_threadpool(index.group_switch_counts, bot_id=bot_id)
        payload["enabled_total"] = counts["enabled"]
        payload["disabled_total"] = counts["disabled"]
        payload["diagnostic_code"] = "group_switch_page_ready"
        payload["index"] = index.status()
        return payload

    @router.post("/group-switches/{group_id}")
    async def update_group_switch(
        group_id: str,
        request: Request,
        body: dict[str, Any] = Body(default_factory=dict),
        admin: AdminIdentity = Depends(require_admin),
    ) -> dict[str, Any]:
        from ...utils import is_group_whitelisted, set_group_enabled

        normalized = str(group_id or "").strip()
        if not normalized.isdigit():
            raise HTTPException(status_code=422, detail={"code": "group_id_invalid", "message": "群号必须为纯数字。"})
        if not isinstance(body.get("enabled"), bool):
            raise HTTPException(status_code=422, detail={"code": "group_switch_value_invalid", "message": "enabled 必须是布尔值。"})
        target = bool(body["enabled"])
        try:
            await run_in_threadpool(set_group_enabled, normalized, target)
            confirmed = bool(is_group_whitelisted(normalized, list(getattr(runtime.plugin_config, "personification_whitelist", []) or [])))
        except Exception as exc:
            webui_audit_log.record(action="group_switch_update", qq=admin.qq, device_id=admin.device_id, target=normalized, ip_hash=get_client_ip(request), detail={"enabled": target, "code": type(exc).__name__}, outcome="unknown")
            raise HTTPException(status_code=500, detail={"code": "group_switch_update_unknown", "phase": "persistence", "message": "群开关写入结果未知，请刷新状态后再决定是否重试。", "outcome_unknown": True, "retryable": False}) from exc
        if confirmed != target:
            raise HTTPException(status_code=409, detail={"code": "group_switch_confirmation_mismatch", "phase": "verification", "message": "写入后的群开关状态与目标不一致。", "outcome_unknown": True, "retryable": False})
        index = await _admin_index(runtime)
        await run_in_threadpool(index.update_group_enabled, normalized, target, source="group_config")
        webui_audit_log.record(action="group_switch_update", qq=admin.qq, device_id=admin.device_id, target=normalized, ip_hash=get_client_ip(request), detail={"enabled": target}, outcome="ok")
        publish_runtime_event("group_switch.updated", payload={"group_id": normalized, "enabled": target})
        return {
            "ok": True,
            "code": "group_switch_enabled" if target else "group_switch_disabled",
            "phase": "operation_complete",
            "title": "群功能已启用" if target else "群功能已停用",
            "message": "权威群配置已保存并重新读取确认。",
            "retryable": False,
            "partial": False,
            "outcome_unknown": False,
            "warnings": [],
            "steps": [
                {"key": "persist", "label": "保存群配置", "status": "ok", "message": "group_config.enabled 已更新。"},
                {"key": "verify", "label": "重新读取确认", "status": "ok", "message": "读取结果与目标状态一致。"},
                {"key": "audit", "label": "记录管理员操作", "status": "ok", "message": "审计记录已写入。"},
            ],
        }

    @router.get("/proactive/stats")
    async def proactive_stats(
        scope: str = Query(default="", max_length=24),
        since_hours: float = Query(default=72.0, ge=1.0, le=720.0),
        _: AdminIdentity = Depends(require_admin),
    ) -> dict[str, Any]:
        counts = await run_in_threadpool(proactive_diagnostics.query_skip_reason_stats, scope=scope, since_seconds=since_hours * 3600)
        return {
            "scope": scope or "all",
            "since_hours": since_hours,
            "counts": counts,
            "sent": int(counts.get("sent", 0)),
            "skip": sum(int(value) for key, value in counts.items() if str(key).startswith("skip_")),
            "total": sum(int(value) for value in counts.values()),
        }

    @router.get("/proactive/recent")
    async def proactive_recent(
        scope: str = Query(default="", max_length=24),
        outcome: str = Query(default="", max_length=32),
        target: str = Query(default="", max_length=64),
        cursor: int = Query(default=0, ge=0),
        limit: int = Query(default=50, ge=1, le=100),
        _: AdminIdentity = Depends(require_admin),
    ) -> dict[str, Any]:
        return await run_in_threadpool(proactive_diagnostics.query_page, scope=scope, outcome=outcome, target=target, cursor=cursor, limit=limit)

    @router.get("/proactive/next-eligible")
    async def proactive_next_eligible(
        scope: str = Query(default="", max_length=24),
        _: AdminIdentity = Depends(require_admin),
    ) -> dict[str, Any]:
        items = await run_in_threadpool(proactive_diagnostics.query_next_eligible, scope=scope)
        return {"items": items, "total": len(items), "diagnostic_code": "proactive_next_eligible_ready"}

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
        try:
            await run_in_threadpool(_rebuild_admin_index, runtime)
        except Exception as exc:
            task.update({"detail_code": f"sticker_index_ready_admin_projection_failed:{type(exc).__name__}"})

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

        root = _sticker_root(runtime)
        snapshot = await run_in_threadpool(load_sticker_catalog_index, root)
        task = _STICKER_INDEX_TASKS.get(str(root.resolve()), {"status": "idle"})
        if bool(snapshot.get("stale", True)):
            task = _queue_sticker_rebuild(root)
        try:
            index = await _admin_index(runtime)
            payload = await run_in_threadpool(
                index.stickers_page,
                page=page,
                page_size=page_size,
                search=search,
                labeled=labeled,
                sort_by=sort_by,
                direction=direction,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail={"code": "sticker_sort_invalid", "message": "贴纸排序字段无效。"},
            ) from exc
        payload.update(
            {
                "index_status": task.get("status", "idle"),
                "index_detail_code": task.get("detail_code", "sticker_index_idle"),
                "index_updated_at": snapshot.get("updated_at", 0.0),
                "index_stale": bool(snapshot.get("stale", True)),
                "admin_index": index.status(),
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
        modified: bool = Query(default=False),
        restart_required: bool = Query(default=False),
        hot_reloadable: bool = Query(default=False),
        advanced: bool = Query(default=False),
        secret: bool = Query(default=False),
        invalid: bool = Query(default=False),
        _: AdminIdentity = Depends(require_admin),
    ) -> dict[str, Any]:
        params = normalize_pagination(page=page, page_size=page_size)
        revision, snapshot = await run_in_threadpool(_config_snapshot, runtime)
        needle_tokens = [token for token in str(search or "").strip().casefold().split() if token]
        rows: list[dict[str, Any]] = []
        for item in snapshot:
            if group and str(item.get("group") or "") != group:
                continue
            haystack = " ".join(
                [
                    str(item.get("field_name") or ""),
                    str(item.get("display_name") or ""),
                    str(item.get("description") or ""),
                    str(item.get("group") or ""),
                    " ".join(str(value or "") for value in item.get("aliases") or []),
                    str(item.get("value") or "") if not item.get("secret") else "",
                ]
            ).casefold()
            if needle_tokens and not all(token in haystack for token in needle_tokens):
                continue
            if modified and not item.get("modified"):
                continue
            if restart_required and not item.get("restart_required"):
                continue
            if hot_reloadable and not item.get("hot_reloadable"):
                continue
            if advanced and not item.get("advanced"):
                continue
            if secret and not item.get("secret"):
                continue
            if invalid and not item.get("validation_error"):
                continue
            rows.append(item)
        payload = build_page(
            rows[params.offset : params.offset + params.page_size],
            total=len(rows),
            params=params,
        ).to_dict()
        counts: dict[str, int] = {}
        modified_counts: dict[str, int] = {}
        for item in snapshot:
            category = str(item.get("group") or "其他")
            counts[category] = counts.get(category, 0) + 1
            if item.get("modified"):
                modified_counts[category] = modified_counts.get(category, 0) + 1
        payload.update(
            {
                "revision": revision,
                "groups": sorted(counts),
                "group_counts": counts,
                "modified_counts": modified_counts,
            }
        )
        return payload

    @router.get("/config/meta")
    async def config_meta(_: AdminIdentity = Depends(require_admin)) -> dict[str, Any]:
        revision, snapshot = await run_in_threadpool(_config_snapshot, runtime)
        counts: dict[str, int] = {}
        modified_counts: dict[str, int] = {}
        for item in snapshot:
            category = str(item.get("group") or "其他")
            counts[category] = counts.get(category, 0) + 1
            if item.get("modified"):
                modified_counts[category] = modified_counts.get(category, 0) + 1
        return {
            "revision": revision,
            "groups": sorted(counts),
            "group_counts": counts,
            "modified_counts": modified_counts,
            "total": len(snapshot),
            "diagnostic_code": "config_metadata_ready",
        }

    @router.patch("/config/values")
    async def patch_config_values(
        body: dict[str, Any] = Body(default_factory=dict),
        admin: AdminIdentity = Depends(require_admin),
    ) -> dict[str, Any]:
        revision = str(body.get("revision") or "")
        values = body.get("values") if isinstance(body.get("values"), dict) else None
        if values is None:
            raise HTTPException(
                status_code=422,
                detail={"code": "config_patch_values_invalid", "message": "values 必须是配置键值对象。"},
            )
        try:
            result = await apply_config_patch(runtime, revision=revision, values=values)
        except RuntimeError as exc:
            if str(exc) == "config_revision_conflict":
                raise HTTPException(
                    status_code=409,
                    detail={"code": "config_revision_conflict", "message": "配置已被其他操作更新，请刷新后重试。"},
                ) from exc
            raise
        except KeyError as exc:
            raise HTTPException(
                status_code=422,
                detail={"code": "config_key_unknown", "message": "提交中包含未注册配置键。"},
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail={"code": "config_value_invalid", "message": f"配置 {exc} 未通过类型或范围校验。"},
            ) from exc
        except OSError as exc:
            raise HTTPException(
                status_code=500,
                detail={"code": "config_batch_persist_failed", "message": "配置未能原子写入，运行时未修改。"},
            ) from exc
        from ...core import webui_audit_log

        webui_audit_log.record(
            action="config_batch_update_v2",
            qq=admin.qq,
            device_id=admin.device_id,
            target=",".join(result["updated_keys"])[:128],
            detail={"keys": result["updated_keys"], "warning_codes": [item["code"] for item in result["warnings"]]},
            outcome="ok" if not result["warnings"] else "partial",
        )
        return result

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
            "dependencies": {
                "ffmpeg": await run_in_threadpool(_binary_dependency, "ffmpeg"),
                "ffprobe": await run_in_threadpool(_binary_dependency, "ffprobe"),
            },
        }

    @router.get("/traces")
    async def traces(
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=20, ge=1, le=100),
        session_type: str = Query(default=""),
        group_id: str = Query(default=""),
        user_id: str = Query(default=""),
        search: str = Query(default="", max_length=160),
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
            search=search,
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
                "probe_catalog": _route_probe_catalog(),
                "probe_statuses": _route_probe_statuses(str(item.get("route_fingerprint") or "")),
                "probe_status": _route_probe_status(str(item.get("route_fingerprint") or "")),
            }
            haystack = " ".join(str(value) for value in flat.values()).casefold()
            if not needle or needle in haystack:
                rows.append(flat)
        return build_page(
            rows[params.offset : params.offset + params.page_size],
            total=len(rows),
            params=params,
        ).to_dict()

    async def _finish_probe(
        route_fingerprint: str,
        capability: str,
        *,
        media_path: Path | None = None,
        cleanup_dir: Path | None = None,
    ) -> None:
        task = _ROUTE_PROBE_TASKS.get(_route_probe_task_key(route_fingerprint, capability))
        if task is None:
            if cleanup_dir is not None:
                _cleanup_route_probe_upload(cleanup_dir)
            return
        try:
            task.update({"status": "running", "started_at": time.time()})
            try:
                capability_state, detail_code = await _run_route_capability_probe(
                    runtime,
                    route_fingerprint,
                    capability,
                    media_path=media_path,
                )
            except Exception:
                capability_state = "unknown"
                detail_code = "probe_internal_failed"
                route = _route_key_for_fingerprint(route_fingerprint)
                if route is not None:
                    _record_route_probe_observation(
                        route[1],
                        capability,
                        CapabilityObservation.PARSE_ERROR,
                        detail_code,
                    )
            route = _route_key_for_fingerprint(route_fingerprint)
            verification_state = "not_run"
            if route is not None:
                verification_state = DEFAULT_ROUTE_CAPABILITY_REGISTRY.get(
                    route[1], capability
                ).verification_state.value
            task.update(
                {
                    "status": "finished",
                    "finished_at": time.time(),
                    "capability_state": capability_state,
                    "verification_state": verification_state,
                    "detail_code": detail_code,
                }
            )
            publish_runtime_event(
                "provider.status_changed",
                payload={
                    "route_fingerprint": route_fingerprint,
                    "capability": capability,
                    "probe_status": "finished",
                    "capability_state": capability_state,
                    "verification_state": verification_state,
                    "detail_code": detail_code,
                },
            )
        finally:
            if cleanup_dir is not None:
                _cleanup_route_probe_upload(cleanup_dir)

    @router.post("/routes/capabilities/{route_fingerprint}/probes")
    async def queue_route_probe(
        route_fingerprint: str,
        body: dict[str, Any] = Body(default_factory=dict),
        _: AdminIdentity = Depends(require_admin),
    ) -> dict[str, Any]:
        known = {str(item.get("route_fingerprint") or "") for item in DEFAULT_ROUTE_CAPABILITY_REGISTRY.snapshot()}
        if route_fingerprint not in known:
            raise HTTPException(status_code=404, detail={"code": "route_not_found", "message": "未找到该模型路由。"})
        capability = str(body.get("capability") or "image_input").strip().lower()
        if capability not in CAPABILITY_NAMES:
            raise HTTPException(
                status_code=422,
                detail={"code": "route_probe_capability_invalid", "message": "未识别的路由能力，未执行探针。"},
            )
        catalog = _route_probe_catalog()[capability]
        if catalog["confirmation_required"] and body.get("confirmed") is not True:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "route_probe_confirmation_required",
                    "message": "该探针可能消耗 Provider 额度或网络请求，必须由管理员明确确认。",
                },
            )
        if catalog.get("input_kind") == "media_upload":
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "route_probe_media_upload_required",
                    "message": "音频和视频能力探针必须由管理员选择一个受限样例上传；未写入文件，也未调用 Provider。",
                },
            )
        if not catalog["available"]:
            route = _route_key_for_fingerprint(route_fingerprint)
            if route is not None:
                _record_route_probe_observation(
                    route[1],
                    capability,
                    CapabilityObservation.PROBE_UNAVAILABLE,
                    str(catalog["reason_code"]),
                )
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "route_probe_unavailable",
                    "message": "当前运行时没有可安全执行的该能力探针；未发送 Provider 请求。",
                    "reason_code": str(catalog["reason_code"]),
                },
            )
        task_key = _route_probe_task_key(route_fingerprint, capability)
        _ROUTE_PROBE_TASKS[task_key] = {
            "status": "queued",
            "queued_at": time.time(),
            "capability": capability,
        }
        asyncio.create_task(_finish_probe(route_fingerprint, capability))
        return operation_diagnostic(
            ok=True,
            code="route_probe_queued",
            phase="queued",
            title="路由能力探针已排队",
            message="探针在管理任务中异步执行，不占聊天回合预算，也不会发送 QQ 消息。",
            steps=(operation_step("probe", "执行能力探针", "pending", "等待 Provider 探针任务开始。"),),
            retryable=False,
            operation_id=f"{route_fingerprint}:{capability}",
        )

    @router.post("/routes/capabilities/{route_fingerprint}/probes/media")
    async def upload_route_media_probe(
        route_fingerprint: str,
        request: Request,
        capability: str = Query(default=""),
        confirmed: bool = Query(default=False),
        _: AdminIdentity = Depends(require_admin),
    ) -> dict[str, Any]:
        """Stream one small admin-selected media sample to the selected route.

        This intentionally follows the established health-video upload lifetime:
        raw stream -> generated directory under the health-probe root -> one
        asynchronous probe -> unconditional cleanup.  It stores neither a
        filename, media content, nor a local path in the task or capability
        snapshot.
        """

        known = {
            str(item.get("route_fingerprint") or "")
            for item in DEFAULT_ROUTE_CAPABILITY_REGISTRY.snapshot()
        }
        if route_fingerprint not in known:
            raise HTTPException(
                status_code=404,
                detail={"code": "route_not_found", "message": "未找到该模型路由。"},
            )
        capability = str(capability or "").strip().lower()
        if capability not in _ROUTE_MEDIA_PROBE_SPECS:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "route_probe_media_capability_invalid",
                    "message": "媒体上传探针仅支持 audio_input 或 video_input。",
                },
            )
        if not confirmed:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "route_probe_confirmation_required",
                    "message": "该探针会把管理员选择的受限媒体样例发送给当前 Provider，必须明确确认。",
                },
            )

        target = _route_probe_target(runtime, route_fingerprint)
        if target is None:
            _record_unavailable_route_probe(route_fingerprint, capability)
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "route_probe_target_unavailable",
                    "message": "当前配置中找不到该路由的可执行 Provider；未写入媒体，也未调用 Provider。",
                },
            )
        _route_name, route_key, provider = target
        from ...core.media_provider_adapters import resolve_media_provider_adapter

        adapter = resolve_media_provider_adapter(provider)
        route_supports_media = (
            adapter.supports_audio if capability == "audio_input" else adapter.supports_video
        )
        if not route_supports_media:
            _record_route_probe_observation(
                route_key,
                capability,
                CapabilityObservation.PROBE_UNAVAILABLE,
                "media_probe_primary_route_unavailable",
            )
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "route_probe_media_protocol_unavailable",
                    "message": "该路由当前没有已声明的安全原生媒体协议；未写入媒体，也未调用 Provider。",
                },
            )

        spec = _ROUTE_MEDIA_PROBE_SPECS[capability]
        original_name = str(
            request.headers.get("x-personification-media-filename")
            or request.headers.get("x-personification-video-filename")
            or ""
        ).strip()
        content_type = str(request.headers.get("content-type", "") or "").split(";", 1)[0].strip().lower()
        suffix = _route_probe_media_suffix(capability, original_name, content_type)
        if suffix is None:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "route_probe_media_invalid_type",
                    "message": "媒体样例的文件名扩展名与 MIME 类型必须匹配受支持格式；未写入文件，也未调用 Provider。",
                },
            )
        max_bytes = int(spec["max_upload_bytes"])
        content_length = str(request.headers.get("content-length", "") or "").strip()
        if content_length.isdigit() and int(content_length) > max_bytes:
            raise HTTPException(
                status_code=413,
                detail={
                    "code": "route_probe_media_payload_too_large",
                    "message": "媒体样例超过能力探针上限；未调用 Provider。",
                },
            )

        from ...core.diagnostics import _video_probe_root

        root = _video_probe_root(getattr(runtime, "plugin_config", None)).resolve()
        operation_id = uuid.uuid4().hex
        probe_dir = root / f"route-capability-{operation_id}"
        target_path = probe_dir / f"sample{suffix}"
        total = 0
        header = bytearray()
        handed_to_probe = False
        try:
            probe_dir.mkdir(parents=True, exist_ok=False)
            with target_path.open("wb") as sink:
                async for chunk in request.stream():
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > max_bytes:
                        raise HTTPException(
                            status_code=413,
                            detail={
                                "code": "route_probe_media_payload_too_large",
                                "message": "媒体样例超过能力探针上限；未调用 Provider。",
                            },
                        )
                    if len(header) < 32:
                        header.extend(chunk[: 32 - len(header)])
                    sink.write(chunk)
            if total <= 0:
                raise HTTPException(
                    status_code=400,
                    detail={
                        "code": "route_probe_media_empty_upload",
                        "message": "没有收到媒体样例；未调用 Provider。",
                    },
                )
            if not _route_probe_media_magic_matches(capability, suffix, bytes(header)):
                raise HTTPException(
                    status_code=400,
                    detail={
                        "code": "route_probe_media_format_invalid",
                        "message": "媒体样例未通过格式签名校验；未调用 Provider。",
                    },
                )

            task_key = _route_probe_task_key(route_fingerprint, capability)
            _ROUTE_PROBE_TASKS[task_key] = {
                "status": "queued",
                "queued_at": time.time(),
                "capability": capability,
                "media_upload": True,
            }
            asyncio.create_task(
                _finish_probe(
                    route_fingerprint,
                    capability,
                    media_path=target_path,
                    cleanup_dir=probe_dir,
                )
            )
            handed_to_probe = True
            return operation_diagnostic(
                ok=True,
                code="route_probe_queued",
                phase="queued",
                title="媒体能力探针已排队",
                message="受限样例将仅用于这一次当前 Provider 验证，完成后立即删除；不会发送 QQ 或保存媒体内容。",
                steps=(
                    operation_step("upload", "校验并接收受限媒体样例", "ok", "样例已进入一次性探针生命周期。"),
                    operation_step("probe", "执行当前 Provider 媒体探针", "pending", "等待异步探针完成。"),
                    operation_step("cleanup", "删除临时媒体样例", "pending", "探针结束后自动执行。"),
                ),
                retryable=False,
                operation_id=f"{route_fingerprint}:{capability}",
            )
        finally:
            if not handed_to_probe:
                _cleanup_route_probe_upload(probe_dir)

    @router.get("/plugin-knowledge")
    async def plugin_knowledge(
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=20, ge=1, le=100),
        search: str = Query(default="", max_length=120),
        category: str = Query(default="", max_length=80),
        _: AdminIdentity = Depends(require_admin),
    ) -> dict[str, Any]:
        from .plugin_knowledge_routes import list_plugin_knowledge_items, plugin_knowledge_available

        params = normalize_pagination(page=page, page_size=page_size)
        needle = str(search or "").strip().casefold()
        category_filter = str(category or "").strip()
        try:
            source_rows = await run_in_threadpool(list_plugin_knowledge_items, runtime)
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail={"code": "plugin_knowledge_catalog_unavailable", "message": "插件知识索引暂时不可用。"},
            ) from exc
        rows = []
        for item in source_rows:
            if category_filter and str(item.get("category") or "") != category_filter:
                continue
            haystack = " ".join(
                (
                    str(item.get("plugin_name") or ""),
                    str(item.get("display_name") or ""),
                    str(item.get("summary") or ""),
                    " ".join(str(value or "") for value in item.get("keywords") or []),
                )
            ).casefold()
            if not needle or needle in haystack:
                rows.append(item)
        payload = build_page(
            rows[params.offset : params.offset + params.page_size],
            total=len(rows),
            params=params,
        ).to_dict()
        payload["available"] = plugin_knowledge_available(runtime)
        return payload

    @router.get("/mcp")
    async def mcp_installations(
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=20, ge=1, le=100),
        search: str = Query(default="", max_length=120),
        _: AdminIdentity = Depends(require_admin),
    ) -> dict[str, Any]:
        from .mcp_routes import list_mcp_installations

        params = normalize_pagination(page=page, page_size=page_size)
        try:
            source_rows = await list_mcp_installations(runtime)
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail={"code": "mcp_catalog_unavailable", "message": "MCP 安装目录暂时不可用。"},
            ) from exc
        needle = str(search or "").strip().casefold()
        rows = [
            item
            for item in source_rows
            if not needle
            or needle
            in " ".join(
                str(item.get(key) or "")
                for key in ("installation_id", "name", "server_name", "source_id", "status")
            ).casefold()
        ]
        return build_page(
            rows[params.offset : params.offset + params.page_size],
            total=len(rows),
            params=params,
        ).to_dict()

    @router.get("/skills")
    async def skills(
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=20, ge=1, le=100),
        search: str = Query(default="", max_length=120),
        category: str = Query(default="", max_length=80),
        _: AdminIdentity = Depends(require_admin),
    ) -> dict[str, Any]:
        from .skill_routes import skill_catalog_payload

        params = normalize_pagination(page=page, page_size=page_size)
        catalog = await run_in_threadpool(skill_catalog_payload, runtime)
        needle = str(search or "").strip().casefold()
        category_filter = str(category or "").strip()
        rows = []
        for item in catalog.get("skills") or []:
            if category_filter and str(item.get("category") or "") != category_filter:
                continue
            haystack = " ".join(
                str(item.get(key) or "") for key in ("name", "description", "category", "source_kind")
            ).casefold()
            if not needle or needle in haystack:
                rows.append(item)
        payload = build_page(
            rows[params.offset : params.offset + params.page_size],
            total=len(rows),
            params=params,
        ).to_dict()
        payload.update(
            {
                "available": bool(catalog.get("available", False)),
                "summary": catalog.get("summary") or {},
            }
        )
        return payload

    @router.get("/tool-tasks")
    async def tool_tasks(
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=20, ge=1, le=100),
        search: str = Query(default="", max_length=120),
        status: str = Query(default="", max_length=40),
        _: AdminIdentity = Depends(require_admin),
    ) -> dict[str, Any]:
        from ...core.tool_creator import get_tool_creator_service

        params = normalize_pagination(page=page, page_size=page_size)
        service = get_tool_creator_service(runtime)
        rows, total = await run_in_threadpool(
            service.list_page,
            limit=params.page_size,
            offset=params.offset,
            status=status,
            search=search,
        )
        return build_page(rows, total=total, params=params).to_dict()

    @router.get("/memories")
    async def memories(
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=20, ge=1, le=100),
        memory_type: str = Query(default="", max_length=64),
        group_id: str = Query(default="", max_length=64),
        user_id: str = Query(default="", max_length=64),
        palace_zone: str = Query(default="", max_length=64),
        source_kind: str = Query(default="", max_length=64),
        include_self: bool = Query(default=False),
        _: AdminIdentity = Depends(require_admin),
    ) -> dict[str, Any]:
        params = normalize_pagination(page=page, page_size=page_size)
        store = _runtime_service(runtime, "memory_store")
        if store is None or not callable(getattr(store, "list_recent_memories_page", None)):
            payload = build_page([], total=0, params=params).to_dict()
            payload.update({"available": False, "hidden_self_count": 0})
            return payload
        rows, total, hidden = await run_in_threadpool(
            store.list_recent_memories_page,
            group_id=group_id,
            user_id=user_id,
            palace_zone=palace_zone,
            limit=params.page_size,
            offset=params.offset,
            source_kind=source_kind,
            memory_type=memory_type,
            include_self=include_self,
        )
        safe_rows = [
            {
                "memory_id": str(item.get("memory_id") or ""),
                "memory_type": str(item.get("memory_type") or ""),
                "group_id": str(item.get("group_id") or ""),
                "user_id": str(item.get("user_id") or ""),
                "summary": guard_visible_text(
                    item.get("summary", ""),
                    surface="webui_v2_memory_summary",
                    allow_direct_media=False,
                    enforce_role_integrity=False,
                )[:300],
                "source_kind": str(item.get("source_kind") or ""),
                "tier": str(item.get("tier") or ""),
                "palace_zone": str(item.get("palace_zone") or ""),
                "confidence": float(item.get("confidence") or 0),
                "salience": float(item.get("salience") or 0),
                "updated_at": float(item.get("updated_at") or 0),
            }
            for item in rows
            if isinstance(item, dict)
        ]
        payload = build_page(safe_rows, total=total, params=params).to_dict()
        payload.update({"available": True, "hidden_self_count": hidden, "include_self": include_self})
        return payload

    @router.get("/logs")
    async def logs(
        limit: int = Query(default=100, ge=1, le=500),
        cursor: int = Query(default=0, ge=0),
        level: str = Query(default="", max_length=16),
        search: str = Query(default="", max_length=120),
        trace_id: str = Query(default="", max_length=64),
        _: AdminIdentity = Depends(require_admin),
    ) -> dict[str, Any]:
        from ...core import plugin_runtime_logs

        result = await run_in_threadpool(
            plugin_runtime_logs.query_page,
            limit=limit,
            cursor=cursor,
            level=level,
            q=search,
            trace_id=trace_id,
        )
        return {
            "items": result.get("entries") or [],
            "next_cursor": int(result.get("next_cursor") or 0),
            "has_more": bool(result.get("has_more", False)),
            "limit": int(result.get("limit") or limit),
            "filters": result.get("filters") or {},
        }

    @router.get("/metrics/summary")
    async def metrics_summary(
        window: str = Query(default="24h", pattern="^(24h|7d|30d|all)$"),
        bot_id: str = Query(default="", max_length=64),
        _: AdminIdentity = Depends(require_admin),
    ) -> dict[str, Any]:
        data = await build_metrics_summary(runtime, "30d" if window == "all" else window)
        if window == "all":
            cumulative = dict(data.get("total_consumption") or {})
            cumulative["provider_usage"] = data.get("provider_usage") or []
            cumulative["billing"] = data.get("billing") or {}
            cumulative["dashboard_overview"] = data.get("dashboard_overview") or {}
            cumulative["window"] = "all"
            cumulative["generated_at"] = data.get("generated_at")
            data = cumulative
        data["bot_id"] = str(bot_id or "")
        return data

    @router.get("/metrics/subscription-quotas")
    async def metrics_subscription_quotas(
        force: bool = Query(default=False),
        _: AdminIdentity = Depends(require_admin),
    ) -> dict[str, Any]:
        return await query_subscription_quotas(
            getattr(runtime, "plugin_config", None),
            force=force,
        )

    @router.get("/runtime/agent")
    async def agent_runtime(
        bot_id: str = Query(default="", max_length=64),
        _: AdminIdentity = Depends(require_admin),
    ) -> dict[str, Any]:
        return await build_agent_runtime_snapshot(runtime, bot_id)

    @router.get("/overview")
    async def overview(_: AdminIdentity = Depends(require_admin)) -> dict[str, Any]:
        traces, _ = await run_in_threadpool(reply_turn_trace.query_page, limit=8, offset=0)
        agent = await build_agent_runtime_snapshot(runtime)
        queue = await run_in_threadpool(ReplyRecoveryQueue)
        route_counts = {"supported": 0, "unsupported": 0, "unknown": 0}
        for route in DEFAULT_ROUTE_CAPABILITY_REGISTRY.snapshot():
            for capability in (route.get("capabilities") or {}).values():
                state = str((capability or {}).get("state") or "unknown")
                route_counts[state if state in route_counts else "unknown"] += 1
        bus = get_runtime_event_bus()
        return {
            "generated_at": datetime.now(tz=timezone.utc).isoformat(),
            "runtime_status": (
                "offline"
                if not agent["running"]
                else "degraded"
                if not agent["enabled"] or agent["stale_turns"] or agent["background_failures"]
                else "healthy"
            ),
            "active_turns": agent["active_turns"],
            "events_last_hour": len(bus),
            "p95_turn_ms": agent["turn_p95_ms"],
            "route_counts": route_counts,
            "recovery_counts": await run_in_threadpool(queue.status_counts),
            "latest_traces": [_trace_summary(trace) for trace in traces],
            "diagnostics": (
                [{"code": "runtime_background_failures", "title": "存在失败的后台任务", "level": "warn"}]
                if agent["background_failures"]
                else []
            ),
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

    @router.get("/plugin-update/status")
    async def plugin_update_status(_: AdminIdentity = Depends(require_admin)) -> dict[str, Any]:
        from ...core import plugin_update_manager

        return await plugin_update_manager.get_plugin_update_status(
            plugin_config=getattr(runtime, "plugin_config", None),
            refresh=False,
        )

    @router.post("/plugin-update/benchmark")
    async def plugin_update_benchmark(
        request: Request,
        admin: AdminIdentity = Depends(require_admin),
    ) -> dict[str, Any]:
        from ...core import plugin_update_manager

        result = await plugin_update_manager.benchmark_update_operation(
            plugin_config=getattr(runtime, "plugin_config", None),
        )
        operation = result.get("operation") if isinstance(result.get("operation"), dict) else {}
        webui_audit_log.record(
            action="plugin_update_benchmark",
            qq=admin.qq,
            device_id=admin.device_id,
            ip_hash=get_client_ip(request),
            detail={"operation_id": operation.get("operation_id"), "diagnostic_code": result.get("diagnostic_code"), "selected_source_id": operation.get("selected_source_id")},
            outcome="ok" if result.get("ok") else "error",
        )
        publish_runtime_event(
            "plugin_update.updated",
            payload={
                "operation_id": operation.get("operation_id"),
                "state": operation.get("state"),
                "diagnostic_code": operation.get("diagnostic_code"),
            },
        )
        return result

    @router.post("/plugin-update/check")
    async def plugin_update_check(
        request: Request,
        admin: AdminIdentity = Depends(require_admin),
    ) -> dict[str, Any]:
        from ...core import plugin_update_manager

        result = await plugin_update_manager.check_plugin_update(
            plugin_config=getattr(runtime, "plugin_config", None),
        )
        operation = result.get("operation") if isinstance(result.get("operation"), dict) else {}
        status = result.get("status") if isinstance(result.get("status"), dict) else {}
        webui_audit_log.record(
            action="plugin_update_check",
            qq=admin.qq,
            device_id=admin.device_id,
            ip_hash=get_client_ip(request),
            detail={"operation_id": operation.get("operation_id"), "diagnostic_code": result.get("diagnostic_code"), "selected_source_id": operation.get("selected_source_id"), "update_available": status.get("update_available")},
            outcome="ok" if result.get("ok") else "error",
        )
        publish_runtime_event(
            "plugin_update.updated",
            payload={
                "operation_id": operation.get("operation_id"),
                "state": operation.get("state"),
                "diagnostic_code": operation.get("diagnostic_code"),
            },
        )
        return result

    @router.post("/plugin-update/apply")
    async def plugin_update_apply(
        request: Request,
        body: dict[str, Any] = Body(default_factory=dict),
        admin: AdminIdentity = Depends(require_admin),
    ) -> dict[str, Any]:
        from ...core import plugin_update_manager

        if str(body.get("confirmation") or "") != "UPDATE":
            raise HTTPException(status_code=422, detail={"code": "plugin_update_confirmation_required", "message": "确认串必须精确等于 UPDATE。"})
        result = await plugin_update_manager.perform_plugin_update(plugin_config=getattr(runtime, "plugin_config", None))
        operation = result.get("operation") if isinstance(result.get("operation"), dict) else {}
        state = str(operation.get("state") or "failed")
        webui_audit_log.record(
            action="plugin_update_apply",
            qq=admin.qq,
            device_id=admin.device_id,
            ip_hash=get_client_ip(request),
            detail={"operation_id": operation.get("operation_id"), "diagnostic_code": operation.get("diagnostic_code"), "selected_source_id": operation.get("selected_source_id")},
            outcome="ok" if state == "succeeded" else "unknown" if state == "unknown" else "error",
        )
        publish_runtime_event(
            "plugin_update.updated",
            payload={
                "operation_id": operation.get("operation_id"),
                "state": state,
                "diagnostic_code": operation.get("diagnostic_code"),
            },
        )
        return result

    @router.get("/plugin-update/operations/{operation_id}")
    async def plugin_update_operation(
        operation_id: str,
        _: AdminIdentity = Depends(require_admin),
    ) -> dict[str, Any]:
        from ...core import plugin_update_manager

        operation = plugin_update_manager.get_update_operation(operation_id)
        if operation is None:
            raise HTTPException(status_code=404, detail={"code": "plugin_update_operation_not_found", "message": "未找到该更新操作。"})
        return operation

    @router.get("/plugin-update/history")
    async def plugin_update_history(
        limit: int = Query(default=30, ge=1, le=100),
        _: AdminIdentity = Depends(require_admin),
    ) -> dict[str, Any]:
        from ...core import plugin_update_manager

        result = await plugin_update_manager.get_plugin_update_history(
            plugin_config=getattr(runtime, "plugin_config", None),
            limit=limit,
            refresh=False,
        )
        operations = list(result.get("operations") or [])
        return {
            "items": operations,
            "total": len(operations),
            "commits": list(result.get("history") or []),
            "pending_commits": list(result.get("pending_history") or []),
            "source": result.get("source") if isinstance(result.get("source"), dict) else {},
            "diagnostic_code": "plugin_update_history_ready",
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
            auth_status=get_qzone_auth_status(bot_id, plugin_config=plugin_config),
            aggregate_status=get_qzone_capability_status(
                bot_id,
                enabled=enabled,
                plugin_config=plugin_config,
            ),
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
