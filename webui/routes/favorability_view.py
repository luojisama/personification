from __future__ import annotations

import math
from typing import Any


_EVENT_LABELS: dict[str, str] = {
    "group_good_atmosphere": "群聊氛围良好",
    "user_interesting_chat": "有趣互动",
    "user_reply_interaction": "回复互动",
    "user_perm_blacklist": "加入永久黑名单",
    "user_perm_blacklist_removed": "移出永久黑名单",
    "manual_adjust": "管理员手动调整",
    "daily_decay": "每日关系衰减",
    "user_behavior_observed": "模型观察用户表现",
    "baseline_migration": "默认基线迁移",
    "relationship_progress_meaningful": "有效关系进展",
    "relationship_progress_resonant": "深度关系共鸣",
    "relationship_progress_milestone": "重要关系里程碑",
}

_STATUS_LABELS: dict[str, str] = {
    "applied": "已生效",
    "capped": "已达每日上限",
    "clamped": "已触及分值边界",
    "disabled": "功能关闭",
    "invalid": "无效事件",
    "duplicate": "重复事件已忽略",
    "projected": "拟议变化（影子）",
    "skipped_low_confidence": "置信度不足，未应用",
    "failed": "观察失败",
}


def _favorability_service(runtime: Any) -> Any | None:
    bundle = getattr(runtime, "runtime_bundle", None)
    if bundle is None:
        return None
    return getattr(bundle, "favorability_service", None)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return number if math.isfinite(number) else default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _event_view(event: dict[str, Any]) -> dict[str, Any]:
    event_type = str(event.get("type", "") or "").strip()
    status = str(event.get("status", "") or "").strip()
    delta = round(_safe_float(event.get("delta", 0.0), 0.0), 2)
    requested_delta = round(_safe_float(event.get("requested_delta", delta), delta), 2)
    metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
    source = str(event.get("source", "") or metadata.get("source", "") or "legacy")[:64]
    mode = str(event.get("mode", "") or metadata.get("mode", "") or "legacy")[:32]
    scope = str(event.get("scope", "") or metadata.get("scope", "") or "global")[:32]
    behavior_tags = event.get("behavior_tags", metadata.get("behavior_tags", []))
    if not isinstance(behavior_tags, list):
        behavior_tags = []
    message_ids = event.get("message_ids", metadata.get("message_ids", []))
    if not isinstance(message_ids, list):
        message_ids = []
    return {
        "type": event_type,
        "label": _EVENT_LABELS.get(event_type, "其他好感事件"),
        "status": status,
        "status_label": _STATUS_LABELS.get(status, "未知状态"),
        "delta": delta,
        "requested_delta": requested_delta,
        "projected_delta": round(_safe_float(event.get("projected_delta", requested_delta), requested_delta), 2),
        "applied_delta": round(_safe_float(event.get("applied_delta", delta), delta), 2),
        "old": round(_safe_float(event.get("old", 0.0), 0.0), 2),
        "new": round(_safe_float(event.get("new", 0.0), 0.0), 2),
        "timestamp": _safe_int(event.get("timestamp", 0), 0),
        "date": str(event.get("date", "") or ""),
        "reason": str(event.get("reason", "") or ""),
        "actor": str(event.get("actor", "") or ""),
        "group_id": str(event.get("group_id", "") or ""),
        "capped": bool(event.get("capped", False)),
        "source": source,
        "source_label": {"observer": "模型观察", "turn_reply_interaction": "回复互动", "manual": "管理员", "legacy": "旧事件"}.get(source, source),
        "mode": mode,
        "scope": scope,
        "confidence": round(max(0.0, min(1.0, _safe_float(event.get("confidence", metadata.get("confidence", 0.0)), 0.0))), 3),
        "behavior_tags": [str(item or "")[:40] for item in behavior_tags[:3]],
        "evidence_summary": str(event.get("evidence_summary", metadata.get("evidence_summary", "")) or "")[:120],
        "trace_id": str(event.get("trace_id", metadata.get("trace_id", "")) or "")[:128],
        "message_ids": [str(item or "")[:128] for item in message_ids[:8]],
        "level_before": str(event.get("level_before", "") or "")[:32],
        "level_after": str(event.get("level_after", "") or "")[:32],
    }


def serialize_favorability(
    runtime: Any,
    key: str,
    *,
    scope: str,
    include_events: bool = True,
    group_id: str = "",
) -> dict[str, Any]:
    service = _favorability_service(runtime)
    profile_key = str(key or "").strip()
    if not profile_key:
        return {
            "available": False,
            "enabled": False,
            "exists": False,
            "key": "",
            "scope": scope,
            "reason": "empty_key",
        }
    if service is None or not hasattr(service, "peek_user_data"):
        return {
            "available": False,
            "enabled": False,
            "exists": False,
            "key": profile_key,
            "scope": scope,
            "reason": "favorability_service_missing",
        }
    try:
        enabled = bool(service.enabled)
    except Exception:
        enabled = False
    try:
        stored_profile = service.peek_user_data(profile_key)
    except Exception as exc:
        return {
            "available": False,
            "enabled": enabled,
            "exists": False,
            "key": profile_key,
            "scope": scope,
            "reason": str(exc),
        }
    exists = isinstance(stored_profile, dict)
    profile = dict(stored_profile) if exists else {}
    if exists:
        default_score = 0.0
    else:
        try:
            default_score = _safe_float(service.default_score(profile_key), 0.0)
        except Exception:
            default_score = 0.0
    score = round(_safe_float(profile.get("favorability", default_score), default_score), 2)
    try:
        level = str(service.get_level_name(score) or "")
    except Exception:
        level = ""
    events_raw = profile.get("favorability_events")
    events = [_event_view(e) for e in events_raw if isinstance(e, dict)] if isinstance(events_raw, list) else []
    shadow_raw = profile.get("favorability_shadow_events")
    if isinstance(shadow_raw, list):
        events.extend(_event_view(e) for e in shadow_raw if isinstance(e, dict))
    events.sort(key=lambda item: (_safe_int(item.get("timestamp", 0), 0), str(item.get("trace_id", ""))))
    latest_event = events[-1] if events else None
    try:
        behavior_policy = service.behavior_policy_for_score(score)
    except Exception:
        behavior_policy = {"band": "", "score": score}
    try:
        configured_bands = getattr(service.plugin_config, "personification_favorability_behavior_bands", {})
        from ...core.favorability import normalize_favorability_behavior_bands
        behavior_bands = normalize_favorability_behavior_bands(configured_bands)
    except Exception:
        behavior_bands = {}
    effective_payload: dict[str, Any] | None = None
    if scope in {"user", "global", "group_user"} and hasattr(service, "get_effective_profile"):
        try:
            effective_payload = service.get_effective_profile(profile_key, group_id)
        except Exception:
            effective_payload = None
    observer = {}
    try:
        observer = service.observer_status()
    except Exception:
        observer = {}
    try:
        today = str(service.current_date() or "")
    except Exception:
        today = ""
    positive_date = str(profile.get("daily_positive_date", "") or "")
    negative_date = str(profile.get("daily_negative_date", "") or "")
    group_daily_date = str(profile.get("last_update", "") or "")
    interesting_date = str(profile.get("last_interesting_date", "") or "")
    growth_model = str(
        getattr(service.plugin_config, "personification_favorability_growth_model", "quality_daily_v2")
        or "quality_daily_v2"
    ).strip().lower()
    daily_growth_cap = max(
        0.0,
        _safe_float(
            getattr(
                service.plugin_config,
                (
                    "personification_favorability_group_daily_growth_cap"
                    if scope == "group"
                    else "personification_favorability_user_daily_growth_cap"
                ),
                0.23,
            ),
            0.23,
        ),
    )
    today_positive = round(
        _safe_float(profile.get("daily_positive_count", 0.0), 0.0)
        if positive_date == today
        else 0.0,
        2,
    )
    remaining_today = round(max(0.0, daily_growth_cap - today_positive), 2)
    estimated_active_days_to_70 = (
        0
        if score >= 70
        else math.ceil((70.0 - score) / daily_growth_cap)
        if daily_growth_cap > 0
        else None
    )
    return {
        "available": True,
        "enabled": enabled,
        "exists": exists,
        "key": profile_key,
        "scope": scope,
        "scope_used": str((effective_payload or {}).get("effective", {}).get("scope_used", scope)),
        "fallback_used": bool((effective_payload or {}).get("effective", {}).get("fallback_used", False)),
        "score": score,
        "score_min": -100,
        "score_max": 100,
        "level": level,
        "growth_model": growth_model,
        "today_positive": today_positive,
        "daily_growth_cap": round(daily_growth_cap, 2),
        "remaining_today": remaining_today,
        "last_progress_quality": str(profile.get("last_progress_quality", "none") or "none"),
        "estimated_active_days_to_70": estimated_active_days_to_70,
        "is_perm_blacklisted": bool(profile.get("is_perm_blacklisted", False)),
        "blacklist_count": _safe_int(profile.get("blacklist_count", 0), 0),
        "daily_positive_count": today_positive,
        "daily_negative_count": round(
            _safe_float(profile.get("daily_negative_count", 0.0), 0.0) if negative_date == today else 0.0,
            2,
        ),
        "daily_net_count": round(
            (_safe_float(profile.get("daily_positive_count", 0.0), 0.0) if positive_date == today else 0.0)
            - (_safe_float(profile.get("daily_negative_count", 0.0), 0.0) if negative_date == today else 0.0),
            2,
        ),
        "daily_fav_count": round(
            _safe_float(profile.get("daily_fav_count", 0.0), 0.0) if group_daily_date == today else 0.0,
            2,
        ),
        "daily_interesting_count": round(
            _safe_float(profile.get("daily_interesting_count", 0.0), 0.0)
            if interesting_date == today
            else 0.0,
            2,
        ),
        "daily_positive_date": positive_date,
        "daily_negative_date": negative_date,
        "today": today,
        "last_event_at": _safe_int(profile.get("last_favorability_event_at", 0), 0),
        "last_event_date": str(profile.get("last_favorability_event_date", "") or ""),
        "last_relationship_activity_at": _safe_int(profile.get("last_relationship_activity_at", 0), 0),
        "revision": _safe_int(profile.get("revision", 0), 0),
        "updated_at": _safe_int(profile.get("updated_at", 0), 0),
        "source": str(profile.get("source", "") or ("personification" if exists else "virtual_default")),
        "behavior_policy": behavior_policy,
        "behavior_bands": behavior_bands,
        "observer": observer,
        "global": (effective_payload or {}).get("global"),
        "group": (effective_payload or {}).get("group"),
        "effective": (effective_payload or {}).get("effective"),
        "latest_event": latest_event,
        "latest_change": latest_event,
        "events": list(reversed(events[-12:])) if include_events else [],
    }
