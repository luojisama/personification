from __future__ import annotations

import asyncio
import hashlib
import json
import math
import time
from typing import Any

from ..agent.inner_state import get_personification_data_dir, load_inner_state
from ..core import config_registry, env_writer, reply_turn_trace, runtime_performance


_BOT_IDENTITY_CACHE: dict[str, tuple[float, str]] = {}
_BOT_IDENTITY_TTL_SECONDS = 60.0


def normalized_numeric_id(value: Any) -> str:
    text = str(value or "").strip()
    return text if text.isascii() and text.isdigit() else ""


def qq_avatar_url(value: Any) -> str | None:
    qq_id = normalized_numeric_id(value)
    return f"https://q1.qlogo.cn/g?b=qq&nk={qq_id}&s=640" if qq_id else None


def group_avatar_url(value: Any) -> str | None:
    group_id = normalized_numeric_id(value)
    return f"https://p.qlogo.cn/gh/{group_id}/{group_id}/640" if group_id else None


def _runtime_bots(runtime: Any) -> list[Any]:
    found: dict[int, Any] = {}
    for holder in (getattr(runtime, "runtime_bundle", None), runtime):
        getter = getattr(holder, "get_bots", None) if holder is not None else None
        if not callable(getter):
            continue
        try:
            bots = getter() or {}
        except Exception:
            continue
        values = bots.values() if isinstance(bots, dict) else bots
        for bot in values:
            if bot is not None:
                found[id(bot)] = bot
    return list(found.values())


async def _bot_nickname(bot: Any, bot_id: str) -> str:
    now = time.monotonic()
    cached = _BOT_IDENTITY_CACHE.get(bot_id)
    if cached is not None and now - cached[0] <= _BOT_IDENTITY_TTL_SECONDS:
        return cached[1]
    nickname = str(
        getattr(bot, "nickname", "")
        or getattr(bot, "name", "")
        or getattr(getattr(bot, "config", None), "nickname", "")
        or ""
    ).strip()
    method = getattr(bot, "get_login_info", None)
    call_api = getattr(bot, "call_api", None)
    try:
        if callable(method):
            raw = await asyncio.wait_for(method(), timeout=2.0)
        elif callable(call_api):
            raw = await asyncio.wait_for(call_api("get_login_info"), timeout=2.0)
        else:
            raw = None
        if isinstance(raw, dict):
            nickname = str(raw.get("nickname") or nickname).strip()
    except Exception:
        pass
    resolved = nickname or (f"Bot {bot_id}" if bot_id else "拟人插件")
    _BOT_IDENTITY_CACHE[bot_id] = (now, resolved[:160])
    return resolved[:160]


async def list_bot_identities(runtime: Any) -> list[dict[str, Any]]:
    bots = _runtime_bots(runtime)
    now = time.time()
    identities: list[dict[str, Any]] = []
    for index, bot in enumerate(bots):
        bot_id = normalized_numeric_id(getattr(bot, "self_id", ""))
        if not bot_id:
            continue
        identities.append(
            {
                "bot_id": bot_id,
                "nickname": await _bot_nickname(bot, bot_id),
                "avatar_url": qq_avatar_url(bot_id),
                "online": True,
                "is_default": index == 0,
                "last_seen_at": now,
            }
        )
    return identities


def _trace_elapsed_ms(trace: dict[str, Any]) -> int | None:
    stamps: list[float] = []
    for stage in trace.get("stages") or []:
        if not isinstance(stage, dict):
            continue
        try:
            stamp = float(stage.get("ts") or 0)
        except (TypeError, ValueError):
            stamp = 0.0
        if stamp > 0:
            stamps.append(stamp)
    if len(stamps) < 2:
        return None
    return max(0, int((max(stamps) - min(stamps)) * 1000))


def _percentile(values: list[int], percentile: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * percentile) - 1))
    return int(ordered[index])


def _recent_trace_summary(trace: dict[str, Any], *, now: float, timeout: float) -> dict[str, Any]:
    process = reply_turn_trace.build_process_view(trace, logs=[])
    inspection = process.get("agent_inspection") if isinstance(process, dict) else {}
    tools = inspection.get("tools") if isinstance(inspection, dict) else []
    detail = trace.get("detail") if isinstance(trace.get("detail"), dict) else {}
    updated_at = float(trace.get("ts") or 0)
    outcome = str(trace.get("outcome") or "")
    age = max(0.0, now - updated_at)
    state = "finished" if outcome else "running" if age <= timeout + 15 else "stale"
    stages = trace.get("stages") if isinstance(trace.get("stages"), list) else []
    last_stage = stages[-1] if stages and isinstance(stages[-1], dict) else {}
    return {
        "trace_id": str(trace.get("trace_id") or ""),
        "state": state,
        "outcome": outcome or "unknown",
        "updated_at": updated_at,
        "age_seconds": round(age, 1),
        "stage": str(last_stage.get("label") or last_stage.get("key") or ""),
        "stage_status": str(last_stage.get("status") or ""),
        "elapsed_ms": _trace_elapsed_ms(trace),
        "model": str(detail.get("model") or detail.get("route_model") or "")[:160],
        "tool_count": len(tools or []),
        "session_type": str(trace.get("session_type") or ""),
        "group_id": str(trace.get("group_id") or ""),
        "diagnosis_code": str(trace.get("diagnosis_code") or ""),
    }


async def build_agent_runtime_snapshot(runtime: Any, bot_id: str = "") -> dict[str, Any]:
    now = time.time()
    config = getattr(runtime, "plugin_config", None)
    timeout = max(10.0, float(getattr(config, "personification_response_timeout", 120) or 120))
    traces = await asyncio.to_thread(reply_turn_trace.query_recent, limit=80)
    performance = runtime_performance.snapshot()
    identities = await list_bot_identities(runtime)
    selected_id = normalized_numeric_id(bot_id)
    selected = next((item for item in identities if item["bot_id"] == selected_id), None)
    if selected is None:
        selected = next((item for item in identities if item["is_default"]), None)
    if selected is None:
        selected = {
            "bot_id": selected_id,
            "nickname": "拟人插件",
            "avatar_url": qq_avatar_url(selected_id),
            "online": False,
            "is_default": True,
            "last_seen_at": None,
        }

    recent = [_recent_trace_summary(trace, now=now, timeout=timeout) for trace in traces]
    active_rows = [item for item in recent if item["state"] == "running"]
    stale_rows = [item for item in recent if item["state"] == "stale"]
    elapsed = [int(item["elapsed_ms"]) for item in recent if isinstance(item.get("elapsed_ms"), int)]
    reply = performance.get("reply") if isinstance(performance.get("reply"), dict) else {}
    event_loop = performance.get("event_loop") if isinstance(performance.get("event_loop"), dict) else {}
    process = performance.get("process") if isinstance(performance.get("process"), dict) else {}
    tasks = performance.get("tasks") if isinstance(performance.get("tasks"), dict) else {}
    caches = performance.get("caches") if isinstance(performance.get("caches"), list) else []
    sending_turns = 0
    for trace in traces:
        stages = trace.get("stages") if isinstance(trace.get("stages"), list) else []
        last = stages[-1] if stages and isinstance(stages[-1], dict) else {}
        key = str(last.get("key") or "").casefold()
        if not trace.get("outcome") and any(token in key for token in ("send", "outbound", "delivery")):
            sending_turns += 1
    try:
        inner = await load_inner_state(get_personification_data_dir(config))
    except Exception:
        inner = {}
    try:
        from ..skills.skillpacks.tool_caller.scripts.impl import provider_streaming_snapshot

        provider_streaming = provider_streaming_snapshot(
            configured_mode=getattr(config, "personification_provider_streaming_mode", "off"),
            api_type=getattr(config, "personification_api_type", "openai"),
        )
    except Exception:
        provider_streaming = {
            "mode": str(getattr(config, "personification_provider_streaming_mode", "off") or "off"),
            "active_calls": 0,
            "route_supported": False,
            "fallback_count": 0,
            "first_chunk_ms": None,
            "total_ms": None,
            "chunk_count": 0,
        }
    last_active_at = max((float(item.get("updated_at") or 0) for item in recent), default=0.0) or None
    return {
        "bot": selected,
        "connected_bots": identities,
        "enabled": bool(getattr(config, "personification_agent_enabled", True)),
        "running": bool(identities),
        "last_active_at": last_active_at,
        "waiting_turns": max(0, int(reply.get("waiting", 0) or 0)),
        "admission_waiting_turns": max(0, int(reply.get("admission_waiting_turns", reply.get("waiting", 0)) or 0)),
        "buffered_sessions": max(0, int(reply.get("buffered_sessions", 0) or 0)),
        "buffered_messages": max(0, int(reply.get("buffered_messages", 0) or 0)),
        "processing_buffer_sessions": max(0, int(reply.get("processing_buffer_sessions", 0) or 0)),
        "oldest_buffer_age_ms": max(0, int(reply.get("oldest_buffer_age_ms", 0) or 0)),
        "next_buffer_fire_ms": max(0, int(reply.get("next_buffer_fire_ms", 0) or 0)),
        "active_turns": max(len(active_rows), int(reply.get("active", 0) or 0)),
        "sending_turns": sending_turns,
        "gated_turns": max(0, int(reply.get("session_gates", 0) or 0)),
        "cancelled_turns": sum(1 for item in recent if item.get("outcome") == "cancelled"),
        "stale_turns": len(stale_rows),
        "event_loop_p50_ms": event_loop.get("p50_ms"),
        "event_loop_p95_ms": event_loop.get("p95_ms"),
        "turn_p50_ms": _percentile(elapsed, 0.50),
        "turn_p95_ms": _percentile(elapsed, 0.95),
        "rss_bytes": process.get("rss_bytes"),
        "peak_rss_bytes": process.get("peak_rss_bytes"),
        "background_tasks": int(tasks.get("total", 0) or 0),
        "background_failures": int(tasks.get("failed_total", 0) or 0),
        "cache_entries": sum(int(item.get("entries", 0) or 0) for item in caches if isinstance(item, dict)),
        "provider_streaming": provider_streaming,
        "inner_state": {
            "mood": str(inner.get("mood") or ""),
            "energy": str(inner.get("energy") or ""),
            "pending_count": len(inner.get("pending_thoughts") or []),
            "updated_at": str(inner.get("updated_at") or ""),
        },
        "recent_traces": recent[:30],
        "generated_at": now,
    }


def config_revision(plugin_config: Any) -> str:
    values: dict[str, Any] = {}
    for entry in config_registry.get_config_entries("global"):
        values[entry.field_name] = getattr(plugin_config, entry.field_name, None)
    encoded = json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:24]


async def apply_config_patch(
    runtime: Any,
    *,
    revision: str,
    values: dict[str, Any],
) -> dict[str, Any]:
    from .routes.config_routes import _MASKED_CONFIG_VALUE, _reload_runtime_step, _restore_masked_config_secrets

    lock = getattr(runtime, "_personification_v2_config_lock", None)
    if not isinstance(lock, asyncio.Lock):
        lock = asyncio.Lock()
        setattr(runtime, "_personification_v2_config_lock", lock)
    async with lock:
        current_revision = config_revision(runtime.plugin_config)
        if str(revision or "") != current_revision:
            raise RuntimeError("config_revision_conflict")
        entries = {entry.field_name: entry for entry in config_registry.get_config_entries("global")}
        unknown = sorted(set(values) - set(entries))
        if unknown:
            raise KeyError(",".join(unknown))
        normalized: dict[str, Any] = {}
        for field_name, raw in values.items():
            entry = entries[field_name]
            try:
                value = entry.normalize_value(raw)
                value = _restore_masked_config_secrets(field_name, value, runtime.plugin_config)
            except ValueError as exc:
                raise ValueError(field_name) from exc
            if entry.secret and raw == _MASKED_CONFIG_VALUE:
                value = getattr(runtime.plugin_config, field_name, None)
            normalized[field_name] = value
        if not normalized:
            return {
                "revision": current_revision,
                "updated_keys": [],
                "hot_reloaded_keys": [],
                "restart_required_keys": [],
                "warnings": [],
            }
        result = await asyncio.to_thread(env_writer.write_many, normalized, runtime.plugin_config)
        if result.get("errors") or not result.get("env_json_path"):
            raise OSError("config_batch_persist_failed")
        previous = {name: getattr(runtime.plugin_config, name, None) for name in normalized}
        try:
            for name, value in normalized.items():
                setattr(runtime.plugin_config, name, value)
        except Exception:
            for name, value in previous.items():
                try:
                    setattr(runtime.plugin_config, name, value)
                except Exception:
                    pass
            raise
        hot_keys = sorted(name for name in normalized if entries[name].hot_reloadable)
        restart_keys = sorted(name for name in normalized if not entries[name].hot_reloadable)
        warnings: list[dict[str, str]] = []
        if hot_keys:
            _step, reload_error = await _reload_runtime_step(runtime, enabled=True)
            if reload_error:
                warnings.append(
                    {
                        "code": "config_runtime_reload_partial",
                        "message": "配置已原子持久化，但运行时重载不完整。",
                    }
                )
        return {
            "revision": config_revision(runtime.plugin_config),
            "updated_keys": sorted(normalized),
            "hot_reloaded_keys": hot_keys,
            "restart_required_keys": restart_keys,
            "warnings": warnings,
        }


__all__ = [
    "apply_config_patch",
    "build_agent_runtime_snapshot",
    "config_revision",
    "group_avatar_url",
    "list_bot_identities",
    "normalized_numeric_id",
    "qq_avatar_url",
]
