from __future__ import annotations

import asyncio
import hashlib
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
from ...core.route_capabilities import DEFAULT_ROUTE_CAPABILITY_REGISTRY
from ...core.route_capabilities import CapabilityObservation, RouteKey
from ...core.qzone_capability_matrix import DEFAULT_QZONE_CAPABILITY_MATRIX
from ...core.runtime_events import get_runtime_event_bus
from ...core.runtime_events import publish_runtime_event
from ...core.visible_output import guard_visible_text
from ..deps import AdminIdentity, require_admin
from ..v2_services import (
    apply_config_patch,
    build_agent_runtime_snapshot,
    config_revision,
    group_avatar_url,
    list_bot_identities,
    qq_avatar_url,
)
from .metrics_routes import build_metrics_summary


_ROUTE_PROBE_TASKS: dict[str, dict[str, Any]] = {}
_STICKER_INDEX_TASKS: dict[str, dict[str, Any]] = {}
_FUNCTIONAL_TEST_RUNS: dict[str, dict[str, Any]] = {}

_FUNCTIONAL_TEST_CATALOG: tuple[dict[str, str], ...] = (
    {"id": "core", "label": "核心运行", "category": "核心", "risk": "local_read"},
    {"id": "model", "label": "主模型", "category": "模型", "risk": "external_read"},
    {"id": "submodels", "label": "子模型", "category": "子模型", "risk": "external_read"},
    {"id": "vision", "label": "图片理解", "category": "视觉", "risk": "external_read"},
    {"id": "video", "label": "视频理解", "category": "视频理解", "risk": "external_read"},
    {"id": "storage", "label": "存储", "category": "存储", "risk": "local_read"},
    {"id": "memory", "label": "记忆", "category": "记忆", "risk": "local_read"},
    {"id": "personas", "label": "画像", "category": "画像", "risk": "local_read"},
    {"id": "groups", "label": "群聊", "category": "群聊", "risk": "local_read"},
    {"id": "stickers", "label": "表情包", "category": "表情包", "risk": "local_read"},
    {"id": "tts", "label": "TTS", "category": "TTS", "risk": "external_read"},
    {"id": "qzone", "label": "QQ 空间", "category": "QQ 空间", "risk": "external_write"},
    {"id": "web_search", "label": "联网搜索", "category": "联网搜索", "risk": "external_read"},
    {"id": "skills", "label": "Skill", "category": "Skill", "risk": "local_read"},
    {"id": "proactive", "label": "主动社交", "category": "主动社交", "risk": "external_write"},
    {"id": "persona", "label": "人设", "category": "人设", "risk": "local_read"},
    {"id": "protocol", "label": "协议端", "category": "协议端", "risk": "external_read"},
    {"id": "webui_security", "label": "WebUI 安全", "category": "WebUI 安全", "risk": "local_read"},
)


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
            }
        )
    rows.sort(key=lambda item: (str(item["group"]), str(item["display_name"]), str(item["field_name"])))
    return rows


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


def _functional_test_definition(test_id: str) -> dict[str, str] | None:
    normalized = str(test_id or "").strip()
    return next((dict(item) for item in _FUNCTIONAL_TEST_CATALOG if item["id"] == normalized), None)


def _functional_test_view(run: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(run.get("id") or ""),
        "test_id": str(run.get("test_id") or ""),
        "label": str(run.get("label") or ""),
        "risk": str(run.get("risk") or "local_read"),
        "state": str(run.get("state") or "prepared"),
        "target_summary": str(run.get("target_summary") or "") or None,
        "route_fingerprint": str(run.get("route_fingerprint") or "") or None,
        "trace_id": str(run.get("trace_id") or "") or None,
        "diagnostic_code": str(run.get("diagnostic_code") or "test_prepared"),
        "created_at": _iso(run.get("created_at")),
        "finished_at": _iso(run.get("finished_at")),
        "duration_ms": run.get("duration_ms") if isinstance(run.get("duration_ms"), int) else None,
        "result_summary": run.get("result_summary") if isinstance(run.get("result_summary"), dict) else {},
    }


async def _execute_functional_test(runtime: Any, operation_id: str) -> None:
    from ...core.diagnostics import run_diagnostics

    run = _FUNCTIONAL_TEST_RUNS.get(operation_id)
    if run is None:
        return
    started = time.monotonic()
    run.update({"state": "running", "diagnostic_code": "test_running"})
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
        run.update(
            {
                "state": "succeeded" if ok else "failed",
                "diagnostic_code": (
                    "functional_test_warning" if status == "warn" else "functional_test_succeeded"
                ) if ok else "functional_test_failed",
                "result_summary": {
                    "overall": status,
                    "check_count": len(checks),
                    "failed_count": sum(
                        1
                        for item in checks
                        if isinstance(item, dict) and str(item.get("status") or "") not in {"ok", "healthy", "passed", "success"}
                    ),
                },
            }
        )
    except Exception as exc:
        run.update(
            {
                "state": "failed",
                "diagnostic_code": f"functional_test_exception:{type(exc).__name__}",
                "result_summary": {},
            }
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
            "risk": risk,
            "state": "prepared" if risk == "local_read" else "awaiting_confirmation",
            "target_summary": str(body.get("target_summary") or "")[:240],
            "route_fingerprint": str(body.get("route_fingerprint") or "")[:128],
            "trace_id": "",
            "diagnostic_code": "test_prepared" if risk == "local_read" else "test_confirmation_required",
            "created_at": time.time(),
            "finished_at": 0.0,
            "result_summary": {},
        }
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
            run.update(
                {
                    "state": "failed",
                    "finished_at": time.time(),
                    "diagnostic_code": "external_write_dedicated_canary_required",
                    "result_summary": {"message": "请在对应 QQ/QZone 专用页面完成带目标字段的单次 canary。"},
                }
            )
            return _functional_test_view(run)
        run.update({"state": "prepared", "diagnostic_code": "test_confirmed"})
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
        membership_state: str = Query(default="", pattern="^(|confirmed|configured|unconfirmed)$"),
        include_unconfirmed: bool = Query(default=False),
        enabled: str = Query(default="", pattern="^(|true|false|1|0|enabled|disabled)$"),
        bot_id: str = Query(default="", max_length=64),
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
        payload = build_page(
            rows[params.offset : params.offset + params.page_size],
            total=len(rows),
            params=params,
        ).to_dict()
        all_rows = await run_in_threadpool(_config_rows, runtime, search="", group="")
        counts: dict[str, int] = {}
        modified_counts: dict[str, int] = {}
        for item in all_rows:
            category = str(item.get("group") or "其他")
            counts[category] = counts.get(category, 0) + 1
            if item.get("modified"):
                modified_counts[category] = modified_counts.get(category, 0) + 1
        payload.update(
            {
                "revision": config_revision(runtime.plugin_config),
                "groups": sorted(counts),
                "group_counts": counts,
                "modified_counts": modified_counts,
            }
        )
        return payload

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
