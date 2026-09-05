from __future__ import annotations

import asyncio
import re
import time
import uuid
from itertools import islice
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from starlette.concurrency import run_in_threadpool

from ...core.operation_diagnostics import detail as operation_detail
from ...core.operation_diagnostics import diagnostic, exception_diagnostic, step
from ...core import webui_audit_log
from ...core.sensitive_data import sanitize_object, sanitize_text
from ..deps import AdminIdentity, get_client_ip, require_admin


_KNOWLEDGE_BUILD_OPERATIONS: dict[int, dict[str, Any]] = {}
_KNOWLEDGE_BUILD_LOCKS: dict[int, asyncio.Lock] = {}
_KNOWLEDGE_SECTIONS = (
    "triggers",
    "features",
    "config_schema",
    "dependencies",
    "entrypoints",
    "implementation_map",
    "data_access",
)
_SAFE_SECTION_KEYS = (
    "key",
    "name",
    "title",
    "summary",
    "description",
    "type",
    "default",
    "required",
    "location",
    "feature_key",
    "keywords",
    "detail",
    "config_items",
    "files",
    "symbols",
    "module",
    "callable",
    "source",
    "target",
    "purpose",
    "access_kind",
    "command",
    "aliases",
)

_WINDOWS_ABSOLUTE_PATH_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9_])(?:[A-Za-z]:[\\/]|\\\\)[^\s\"'<>|]*"
)
_POSIX_LOCAL_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9_:])/(?:home|Users|root|etc|var|opt|srv|tmp)(?:/[^\s\"'<>]*)?"
)


_DIAGNOSTIC_FIELDS = (
    "ok",
    "code",
    "phase",
    "title",
    "message",
    "details",
    "steps",
    "warnings",
    "suggestion",
    "retryable",
    "partial",
    "outcome_unknown",
    "operation_id",
    "trace_id",
)


def _attach_diagnostic(payload: dict[str, Any], report: dict[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    result["diagnostic"] = report
    for field in _DIAGNOSTIC_FIELDS:
        result.setdefault(field, report[field])
    return result


def _log_read_exception(runtime: Any, exc: BaseException, report: dict[str, Any]) -> None:
    logger = getattr(runtime, "logger", None)
    if logger is None:
        return
    try:
        logger.warning(
            f"[webui] plugin knowledge read failed: code={report.get('code', '')} "
            f"exception={type(exc).__name__} trace={report.get('trace_id', '')}"
        )
    except Exception:
        pass


def _read_failure(
    runtime: Any,
    exc: BaseException,
    *,
    code: str,
    phase: str,
    title: str,
    message: str,
    step_key: str,
    step_label: str,
) -> HTTPException:
    report = exception_diagnostic(
        exc,
        phase=phase,
        title=title,
        message=message,
        suggestion="请根据 Trace ID 查看脱敏日志；确认知识库文件状态后重试。",
        retryable=True,
    )
    report["code"] = code
    report["steps"] = [
        step(step_key, step_label, "error", "读取异常中断，未向客户端返回 exception text。").to_dict()
    ]
    _log_read_exception(runtime, exc, report)
    return HTTPException(status_code=500, detail=report)


def _store_unavailable_report(*, operation: str) -> dict[str, Any]:
    return diagnostic(
        ok=False,
        code="plugin_knowledge_store_unavailable",
        phase="store_lookup",
        title="插件知识库未就绪",
        message="当前 runtime 尚未提供 knowledge_store。",
        steps=(step("resolve_store", f"准备{operation}", "error", "knowledge_store 不可用。"),),
        suggestion="等待插件启动完成，或检查 runtime 初始化状态后重试。",
        retryable=True,
    )


def _knowledge_store(runtime) -> Any | None:
    """从 runtime_bundle 嵌套路径取 PluginKnowledgeStore 实例。"""
    bundle = getattr(runtime, "runtime_bundle", None)
    if bundle is None:
        return None
    deps = getattr(bundle, "reply_processor_deps", None)
    if deps is None:
        return None
    inner = getattr(deps, "runtime", None)
    if inner is None:
        return None
    return getattr(inner, "knowledge_store", None)


def _knowledge_runtime(runtime: Any) -> Any | None:
    bundle = getattr(runtime, "runtime_bundle", None)
    deps = getattr(bundle, "reply_processor_deps", None) if bundle is not None else None
    return getattr(deps, "runtime", None) if deps is not None else None


def _knowledge_build_lock(runtime: Any) -> asyncio.Lock:
    key = id(runtime)
    lock = _KNOWLEDGE_BUILD_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _KNOWLEDGE_BUILD_LOCKS[key] = lock
    return lock


def _safe_text(value: Any, limit: int = 2_000) -> str:
    bounded = max(0, int(limit))
    text = sanitize_text(value, limit=max(bounded, 16)).strip()
    text = _WINDOWS_ABSOLUTE_PATH_RE.sub("<redacted-path>", text)
    text = _POSIX_LOCAL_PATH_RE.sub("<redacted-path>", text)
    return text[:bounded]


def _safe_scalar_text(value: Any, limit: int = 2_000) -> str:
    if not isinstance(value, (str, int, float, bool)):
        return ""
    return _safe_text(value, limit)


def _safe_relative_text(value: Any, limit: int = 500) -> str:
    text = _safe_text(value, limit)
    if not text:
        return ""
    path = Path(text)
    if path.is_absolute() or path.drive or ".." in path.parts:
        return ""
    return text


def _safe_section_value(key: str, value: Any) -> Any:
    if key in {"files", "location", "source"}:
        if isinstance(value, list):
            return [item for item in (_safe_relative_text(part, 240) for part in value[:16]) if item]
        return _safe_relative_text(value)
    if isinstance(value, list):
        return [item for item in (_safe_scalar_text(part, 240) for part in value[:16]) if item]
    if isinstance(value, dict):
        return ""
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return value
    return _safe_text(value)


def _safe_section_item(value: Any, *, key: str = "") -> dict[str, Any]:
    if not isinstance(value, dict):
        return {
            "key": _safe_text(key, 160),
            "value": _safe_scalar_text(value),
        }
    result: dict[str, Any] = {}
    remaining_chars = 2_500
    if key:
        result["key"] = _safe_text(key, 160)
        remaining_chars -= len(result["key"])
    for field in _SAFE_SECTION_KEYS:
        if remaining_chars <= 0:
            break
        if field not in value:
            continue
        item = _safe_section_value(field, value.get(field))
        if isinstance(item, str):
            item = item[:remaining_chars]
            remaining_chars -= len(item)
        elif isinstance(item, list):
            bounded: list[str] = []
            for part in item:
                if remaining_chars <= 0:
                    break
                text = _safe_scalar_text(part, min(240, remaining_chars))
                if text:
                    bounded.append(text)
                    remaining_chars -= len(text)
            item = bounded
        if item not in ("", [], None):
            result[field] = item
    name = str(result.get("name") or result.get("key") or "")
    projected = sanitize_object({name: "visible"}) if name else {}
    if isinstance(projected, dict) and projected.get(name) == "***":
        result.pop("default", None)
    return result


def _raw_section(entry: dict[str, Any], section: str) -> Any:
    raw = entry.get(section)
    if section == "triggers" and raw in (None, {}, []):
        raw = entry.get("commands")
    return raw


def _section_count(entry: dict[str, Any], section: str) -> int:
    raw = _raw_section(entry, section)
    return len(raw) if isinstance(raw, (dict, list)) else 0


def _section_page(
    entry: dict[str, Any],
    section: str,
    *,
    offset: int,
    page_size: int,
) -> tuple[list[dict[str, Any]], int]:
    raw = _raw_section(entry, section)
    if isinstance(raw, dict):
        items = [
            _safe_section_item(value, key=str(key))
            for key, value in islice(raw.items(), offset, offset + page_size)
        ]
        return items, len(raw)
    if isinstance(raw, list):
        return [_safe_section_item(value) for value in raw[offset : offset + page_size]], len(raw)
    return [], 0


def _entry_summary(
    plugin_name: str,
    entry: dict[str, Any],
    meta: dict[str, Any],
    assets: dict[str, Any],
) -> dict[str, Any]:
    sections = {name: _section_count(entry, name) for name in _KNOWLEDGE_SECTIONS}
    return {
        "plugin_name": _safe_text(plugin_name, 200),
        "display_name": _safe_scalar_text(entry.get("display_name") or meta.get("display_name"), 200),
        "summary": _safe_scalar_text(entry.get("summary") or meta.get("summary"), 2_000),
        "architecture_summary": _safe_scalar_text(entry.get("architecture_summary"), 2_000),
        "category": _safe_scalar_text(meta.get("category") or entry.get("category"), 80),
        "keywords": [
            item
            for item in (
                _safe_scalar_text(value, 120)
                for value in list(entry.get("keywords") or meta.get("keywords") or [])[:24]
            )
            if item
        ],
        "updated_at": _safe_scalar_text(entry.get("updated_at") or meta.get("updated_at"), 80),
        "source_coverage": _source_coverage_payload(meta=meta, entry=entry),
        "sections": sections,
        "snapshot_status": {
            kind: {
                "status": _safe_text((assets.get(kind) or {}).get("status"), 32) or "missing",
                "size_bytes": _as_int((assets.get(kind) or {}).get("size_bytes")),
            }
            for kind in ("runtime", "source")
        },
    }


async def _inspect_plugin_assets(store: Any, plugin_name: str) -> dict[str, Any]:
    inspector = getattr(store, "inspect_plugin_assets", None)
    if callable(inspector):
        return await inspector(plugin_name)
    entry = await run_in_threadpool(store.load_plugin_entry_sync, plugin_name)
    return {
        "entry": {"status": "ready" if isinstance(entry, dict) else "missing", "data": entry, "size_bytes": 0},
        "runtime": {"status": "missing", "data": None, "size_bytes": 0},
        "source": {"status": "missing", "data": None, "size_bytes": 0},
        "meta": {},
    }


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value or default)
    except Exception:
        return int(default)


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value if value not in (None, "") else default)
    except Exception:
        return float(default)


def _source_coverage_payload(
    *,
    meta: dict[str, Any] | None = None,
    entry: dict[str, Any] | None = None,
    source_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for candidate in (source_snapshot, entry, meta):
        if not isinstance(candidate, dict):
            continue
        coverage = candidate.get("source_coverage", {})
        if isinstance(coverage, dict):
            merged.update(coverage)
        for key in (
            "analysis_scope",
            "analysis_strategy",
            "analysis_mode",
            "source_file_count",
            "source_chunk_count",
            "source_chars",
            "module_bundle_count",
            "source_complete",
            "source_truncated",
        ):
            if key in candidate and candidate.get(key) not in (None, ""):
                merged[key] = candidate.get(key)

    source_file_count = _as_int(merged.get("source_file_count"))
    source_chunk_count = _as_int(merged.get("source_chunk_count"))
    analyzed_chunk_count = _as_int(merged.get("unique_analyzed_chunk_count", merged.get("analyzed_chunk_count", source_chunk_count)))
    full_input = bool(
        merged.get("full_input", False)
        or (
            source_chunk_count > 0
            and analyzed_chunk_count >= source_chunk_count
            and not bool(merged.get("source_truncated", False))
        )
    )
    return {
        "analysis_scope": _safe_scalar_text(merged.get("analysis_scope"), 120) or "full_readable_python_source",
        "analysis_strategy": _safe_scalar_text(merged.get("analysis_strategy"), 120),
        "analysis_mode": _safe_scalar_text(merged.get("analysis_mode"), 120),
        "full_input": full_input,
        "source_complete": bool(merged.get("source_complete", True)),
        "source_truncated": bool(merged.get("source_truncated", False)),
        "source_file_count": source_file_count,
        "source_chunk_count": source_chunk_count,
        "source_chars": _as_int(merged.get("source_chars")),
        "analysis_unit_count": _as_int(merged.get("analysis_unit_count")),
        "analyzed_chunk_count": _as_int(merged.get("analyzed_chunk_count", analyzed_chunk_count)),
        "unique_analyzed_chunk_count": analyzed_chunk_count,
        "duplicate_analyzed_chunk_count": _as_int(merged.get("duplicate_analyzed_chunk_count")),
        "module_bundle_count": _as_int(merged.get("module_bundle_count")),
        "coverage_percent": _as_float(merged.get("coverage_percent"), 100.0 if full_input and source_chunk_count else 0.0),
        "note": _safe_scalar_text(
            merged.get("note", "")
            or "插件知识库读取完整可读 Python 源码；大型插件按模块或 chunk 分批分析，不做抽样。",
            500,
        ),
    }


def list_plugin_knowledge_items(runtime: Any) -> list[dict[str, Any]]:
    """Read the normalized plugin-knowledge catalog without WebUI response wrapping."""

    store = _knowledge_store(runtime)
    if store is None:
        return []
    inspect_index = getattr(store, "inspect_index_sync", None)
    if callable(inspect_index):
        inspected = inspect_index()
        status = str(inspected.get("status") or "unavailable")
        if status == "missing":
            return []
        if status != "ready":
            raise RuntimeError(f"plugin_knowledge_index_{status}")
        index = inspected.get("data")
    else:
        index = store.load_index_sync()
    if not isinstance(index, dict):
        raise RuntimeError("plugin_knowledge_index_corrupt")
    plugins_map = index.get("plugins", {}) if isinstance(index, dict) else {}
    items: list[dict[str, Any]] = []
    for name, meta in list(plugins_map.items())[:500]:
        if not isinstance(meta, dict):
            continue
        coverage = _source_coverage_payload(meta=meta)
        items.append(
            {
                "plugin_name": _safe_scalar_text(name, 200),
                "display_name": _safe_scalar_text(meta.get("display_name"), 200),
                "summary": _safe_scalar_text(meta.get("summary"), 200),
                "keywords": [
                    item
                    for item in (
                        _safe_scalar_text(value, 120)
                        for value in list(meta.get("keywords", []) or [])[:8]
                    )
                    if item
                ],
                "category": _safe_scalar_text(meta.get("category"), 80),
                "has_runtime_data": bool(meta.get("has_runtime_data", False)),
                "has_source_data": bool(meta.get("has_source_data", False)),
                "source_file_count": _as_int(meta.get("source_file_count")),
                "source_chunk_count": _as_int(meta.get("source_chunk_count")),
                "source_chars": _as_int(meta.get("source_chars")),
                "analysis_strategy": _safe_scalar_text(meta.get("analysis_strategy"), 120),
                "analysis_mode": _safe_scalar_text(meta.get("analysis_mode"), 120),
                "analysis_scope": _safe_scalar_text(
                    meta.get("analysis_scope") or coverage.get("analysis_scope"), 120
                ),
                "source_complete": bool(meta.get("source_complete", coverage.get("source_complete", True))),
                "source_truncated": bool(meta.get("source_truncated", coverage.get("source_truncated", False))),
                "source_coverage": coverage,
                "updated_at": _safe_scalar_text(meta.get("updated_at"), 80),
            }
        )
    items.sort(key=lambda item: (item["category"] or "~", item["plugin_name"]))
    return items


def plugin_knowledge_available(runtime: Any) -> bool:
    return _knowledge_store(runtime) is not None


def _entry_status_exception(plugin_name: str, entry_status: str) -> HTTPException:
    normalized = str(entry_status or "unavailable").strip().lower()
    status_code = 404 if normalized == "missing" else 413 if normalized == "too_large" else 503
    code = {
        "missing": "plugin_knowledge_not_found",
        "corrupt": "plugin_knowledge_entry_corrupt",
        "too_large": "plugin_knowledge_entry_too_large",
        "unavailable": "plugin_knowledge_entry_unavailable",
    }.get(normalized, "plugin_knowledge_entry_unavailable")
    return HTTPException(
        status_code=status_code,
        detail=diagnostic(
            ok=False,
            code=code,
            phase="entry_lookup",
            title="插件知识详情不可用",
            message="知识条目缺失、损坏、超过安全读取上限或当前无法读取。",
            details=(operation_detail("Plugin", _safe_text(plugin_name, 200)),),
            steps=(step("read_entry", "读取插件知识详情", "error", f"读取状态：{normalized}。"),),
            suggestion="查看知识构建状态，必要时执行一次确认重建。",
            retryable=False,
        ),
    )


def _knowledge_task_handles(runtime: Any) -> tuple[Any, Any, Any]:
    bundle = getattr(runtime, "runtime_bundle", None)
    getter = getattr(bundle, "get_knowledge_build_task", None) if bundle is not None else None
    setter = getattr(bundle, "set_knowledge_build_task", None) if bundle is not None else None
    inner = _knowledge_runtime(runtime)
    caller = None
    if inner is not None:
        caller = getattr(inner, "agent_tool_caller", None) or getattr(inner, "lite_tool_caller", None)
    return getter, setter, caller


async def _knowledge_status_payload(runtime: Any) -> dict[str, Any]:
    store = _knowledge_store(runtime)
    configured_enabled = bool(
        getattr(
            getattr(runtime, "plugin_config", None),
            "personification_plugin_knowledge_build_enabled",
            False,
        )
    )
    if store is None:
        return {
            "available": False,
            "automatic_build_enabled": configured_enabled,
            "state": "unavailable",
            "counts": {"loaded": 0, "indexed": 0, "missing": 0, "pending": 0, "failed": 0, "degraded": 0, "success": 0},
            "current": {},
            "plugins": [],
            "operation": None,
            "diagnostic_code": "plugin_knowledge_store_unavailable",
        }
    from ...core.knowledge_builder import inspect_plugin_knowledge_health

    inspect_index = getattr(store, "inspect_index", None)
    inspect_build_state = getattr(store, "inspect_build_state", None)
    index_result, build_state_result, health = await asyncio.gather(
        inspect_index() if callable(inspect_index) else store.load_index(),
        inspect_build_state() if callable(inspect_build_state) else store.load_build_state(),
        inspect_plugin_knowledge_health(store),
    )
    if isinstance(index_result, dict) and "status" in index_result:
        index_status = str(index_result.get("status") or "unavailable")
        index = index_result.get("data") if index_status == "ready" else {"plugins": {}}
    else:
        index_status = "ready"
        index = index_result
    if isinstance(build_state_result, dict) and "status" in build_state_result:
        build_state_status = str(build_state_result.get("status") or "unavailable")
        build_state = build_state_result.get("data") if build_state_status == "ready" else {"plugins": {}}
    else:
        build_state_status = "ready"
        build_state = build_state_result
    index_plugins = index.get("plugins", {}) if isinstance(index, dict) else {}
    state_plugins = build_state.get("plugins", {}) if isinstance(build_state, dict) else {}
    current = build_state.get("current", {}) if isinstance(build_state, dict) else {}
    if not isinstance(index_plugins, dict):
        index_plugins = {}
    if not isinstance(state_plugins, dict):
        state_plugins = {}
    if not isinstance(current, dict):
        current = {}
    counts = {"pending": 0, "failed": 0, "degraded": 0, "success": 0}
    plugins: list[dict[str, Any]] = []
    for name, meta in sorted(state_plugins.items(), key=lambda item: str(item[0]))[:200]:
        if not isinstance(meta, dict):
            continue
        state = str(meta.get("status") or "pending").lower()
        if state in counts:
            counts[state] += 1
        plugins.append(
            {
                "plugin_name": _safe_text(name, 200),
                "state": state if state in counts else "pending",
                "phase": _safe_scalar_text(meta.get("phase"), 80),
                "updated_at": _safe_scalar_text(meta.get("updated_at"), 80),
                "error_type": _safe_scalar_text(meta.get("error_type"), 120),
                "diagnostic_code": "plugin_knowledge_build_item_failed" if state == "failed" else f"plugin_knowledge_build_item_{state}",
            }
        )
    getter, _setter, _caller = _knowledge_task_handles(runtime)
    task = getter() if callable(getter) else None
    task_running = bool(task is not None and not task.done())
    task_cancelled = bool(task is not None and task.done() and task.cancelled())
    task_failed = False
    if task is not None and task.done() and not task_cancelled:
        try:
            task_failed = task.exception() is not None
        except (asyncio.CancelledError, RuntimeError):
            task_failed = True
    current_plugin = _safe_text(current.get("plugin_name"), 200)
    stale_pending = bool(current_plugin and not task_running)
    operation = _KNOWLEDGE_BUILD_OPERATIONS.get(id(runtime))
    if operation is not None:
        if task_running:
            operation["state"] = "running"
        elif operation.get("state") in {"queued", "running", "cancelling"}:
            operation["state"] = (
                "cancelled"
                if operation.get("cancel_requested") or task_cancelled
                else "failed"
                if task_failed or stale_pending or counts["failed"] or counts["pending"]
                else "succeeded"
            )
            operation["diagnostic_code"] = f"plugin_knowledge_build_{operation['state']}"
            operation["finished_at"] = operation.get("finished_at") or time.time()
    state = "running" if task_running else "stale_pending" if stale_pending or counts["pending"] else (
        "degraded" if counts["failed"] or counts["degraded"] else "ready" if index_plugins else "empty"
    )
    return {
        "available": True,
        "automatic_build_enabled": configured_enabled,
        "state": state,
        "counts": {
            "loaded": _as_int(health.get("loaded_plugin_count")),
            "indexed": len(index_plugins),
            "missing": len(list(health.get("missing_plugins") or [])),
            **counts,
            "source_snapshot_missing": sum(
                1 for meta in index_plugins.values() if isinstance(meta, dict) and not bool(meta.get("has_source_data"))
            ),
        },
        "current": {
            "plugin_name": current_plugin,
            "phase": _safe_scalar_text(current.get("phase"), 80),
            "updated_at": _safe_scalar_text(current.get("updated_at"), 80),
            "stale": stale_pending,
        },
        "plugins": plugins,
        "operation": dict(operation) if isinstance(operation, dict) else None,
        "storage_status": {"index": index_status, "build_state": build_state_status},
        "diagnostic_code": (
            f"plugin_knowledge_index_{index_status}"
            if index_status not in {"ready", "missing"}
            else f"plugin_knowledge_build_state_{build_state_status}"
            if build_state_status not in {"ready", "missing"}
            else
            "plugin_knowledge_build_running"
            if task_running
            else "plugin_knowledge_stale_pending"
            if state == "stale_pending"
            else "plugin_knowledge_automatic_build_disabled"
            if not configured_enabled
            else "plugin_knowledge_status_ready"
        ),
    }


def build_plugin_knowledge_router(*, runtime) -> APIRouter:
    router = APIRouter(prefix="/api/plugin-knowledge", tags=["plugin_knowledge"])

    @router.get("/list")
    async def list_all(_: AdminIdentity = Depends(require_admin)) -> dict:
        store = _knowledge_store(runtime)
        if store is None:
            return _attach_diagnostic(
                {"plugins": [], "total": 0, "available": False},
                _store_unavailable_report(operation="插件知识索引"),
            )
        try:
            items = await run_in_threadpool(list_plugin_knowledge_items, runtime)
        except Exception as exc:
            storage_code = str(exc)
            raise _read_failure(
                runtime,
                exc,
                code=(
                    storage_code
                    if storage_code in {
                        "plugin_knowledge_index_corrupt",
                        "plugin_knowledge_index_too_large",
                        "plugin_knowledge_index_unavailable",
                    }
                    else "plugin_knowledge_index_read_failed"
                ),
                phase="index_read",
                title="插件知识索引读取失败",
                message="服务器无法安全读取插件知识索引。",
                step_key="read_index",
                step_label="读取插件知识索引",
            ) from exc
        report = diagnostic(
            ok=True,
            code="plugin_knowledge_list_loaded",
            phase="read_complete",
            title="插件知识索引已读取",
            message=f"已读取 {len(items)} 条插件知识索引。",
            details=(operation_detail("插件数量", len(items), "ok"),),
            steps=(
                step("resolve_store", "准备插件知识索引", "ok", "knowledge_store 已就绪。"),
                step("read_index", "读取插件知识索引", "ok", "索引已读取并规范化。"),
            ),
        )
        return _attach_diagnostic(
            {
                "plugins": items[:200],
                "total": len(items),
                "truncated": len(items) > 200,
                "available": True,
            },
            report,
        )

    @router.get("/detail/{plugin_name}")
    async def detail(plugin_name: str, _: AdminIdentity = Depends(require_admin)) -> dict:
        store = _knowledge_store(runtime)
        if store is None:
            raise HTTPException(status_code=503, detail=_store_unavailable_report(operation="插件知识详情"))
        try:
            assets = await _inspect_plugin_assets(store, plugin_name)
        except Exception as exc:
            raise _read_failure(
                runtime,
                exc,
                code="plugin_knowledge_entry_read_failed",
                phase="entry_read",
                title="插件知识详情读取失败",
                message="服务器无法安全读取插件知识详情。",
                step_key="read_entry",
                step_label="读取插件知识详情",
            ) from exc
        entry_result = assets.get("entry") if isinstance(assets.get("entry"), dict) else {}
        entry_status = str(entry_result.get("status") or "unavailable")
        entry = entry_result.get("data") if entry_status == "ready" else None
        if not isinstance(entry, dict):
            raise _entry_status_exception(plugin_name, entry_status)
        meta = assets.get("meta") if isinstance(assets.get("meta"), dict) else {}
        summary = _entry_summary(plugin_name, entry, meta, assets)
        snapshot_status = summary["snapshot_status"]
        snapshot_incomplete = any(
            str(item.get("status") or "missing") != "ready"
            for item in snapshot_status.values()
            if isinstance(item, dict)
        )
        report = diagnostic(
            ok=True,
            code="plugin_knowledge_detail_partial" if snapshot_incomplete else "plugin_knowledge_detail_loaded",
            phase="read_complete",
            title="插件知识摘要已读取",
            message=(
                "知识摘要可用；部分构建快照缺失或不可用。原始快照不会发送到浏览器。"
                if snapshot_incomplete
                else "知识摘要和分区计数已读取；原始快照不会发送到浏览器。"
            ),
            details=(operation_detail("Plugin", plugin_name),),
            steps=(step("read_entry", "读取插件知识摘要", "ok", "已完成白名单投影。"),),
            warnings=("部分构建快照不可用，请查看构建状态。",) if snapshot_incomplete else (),
            suggestion="执行一次确认重建可补齐缺失或陈旧快照。" if snapshot_incomplete else "",
            partial=snapshot_incomplete,
        )
        return _attach_diagnostic(
            {
                "plugin_name": _safe_text(plugin_name, 200),
                "entry": summary,
                "source_coverage": summary["source_coverage"],
                "sections": summary["sections"],
                "snapshot_status": snapshot_status,
            },
            report,
        )

    @router.get("/detail/{plugin_name}/sections/{section}")
    async def detail_section(
        plugin_name: str,
        section: str,
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1, le=100),
        _: AdminIdentity = Depends(require_admin),
    ) -> dict[str, Any]:
        if section not in _KNOWLEDGE_SECTIONS:
            raise HTTPException(
                status_code=404,
                detail={"code": "plugin_knowledge_section_not_found", "message": "未找到该知识分区。"},
            )
        store = _knowledge_store(runtime)
        if store is None:
            raise HTTPException(status_code=503, detail=_store_unavailable_report(operation="插件知识分区"))
        assets = await _inspect_plugin_assets(store, plugin_name)
        entry_result = assets.get("entry") if isinstance(assets.get("entry"), dict) else {}
        entry_status = str(entry_result.get("status") or "unavailable")
        entry = entry_result.get("data") if entry_status == "ready" else None
        if not isinstance(entry, dict):
            raise _entry_status_exception(plugin_name, entry_status)
        offset = (page - 1) * page_size
        items, total = await run_in_threadpool(
            _section_page,
            entry,
            section,
            offset=offset,
            page_size=page_size,
        )
        return {
            "plugin_name": _safe_text(plugin_name, 200),
            "section": section,
            "items": items,
            "page": page,
            "page_size": page_size,
            "total": total,
            "has_more": offset + len(items) < total,
            "truncated": total > page_size,
            "diagnostic_code": "plugin_knowledge_section_loaded",
        }

    @router.get("/status")
    async def build_status(_: AdminIdentity = Depends(require_admin)) -> dict[str, Any]:
        return await _knowledge_status_payload(runtime)

    @router.post("/builds")
    async def start_one_shot_build(
        request: Request,
        body: dict[str, Any] = Body(default_factory=dict),
        admin: AdminIdentity = Depends(require_admin),
    ) -> dict[str, Any]:
        if str(body.get("mode") or "") != "one_shot" or body.get("confirmed") is not True:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "plugin_knowledge_build_confirmation_missing",
                    "message": "必须明确确认单次重建、源码读取和当前模型调用范围。",
                },
            )
        store = _knowledge_store(runtime)
        getter, setter, caller = _knowledge_task_handles(runtime)
        if store is None or not callable(getter) or not callable(setter) or caller is None:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "plugin_knowledge_build_runtime_unavailable",
                    "message": "知识存储、任务所有权或当前模型尚未就绪。",
                },
            )
        async with _knowledge_build_lock(runtime):
            current_task = getter()
            current_operation = _KNOWLEDGE_BUILD_OPERATIONS.get(id(runtime))
            if current_task is not None and not current_task.done():
                if current_operation is None:
                    current_operation = {
                        "id": uuid.uuid4().hex,
                        "mode": "one_shot",
                        "state": "running",
                        "started_at": time.time(),
                        "finished_at": 0.0,
                        "cancel_requested": False,
                        "diagnostic_code": "plugin_knowledge_build_already_running",
                    }
                    _KNOWLEDGE_BUILD_OPERATIONS[id(runtime)] = current_operation
                return {
                    "operation": dict(current_operation),
                    "status": await _knowledge_status_payload(runtime),
                    "diagnostic_code": "plugin_knowledge_build_already_running",
                }
            from ...core.knowledge_builder import maybe_start_plugin_knowledge_builder

            result = await maybe_start_plugin_knowledge_builder(
                plugin_config=getattr(runtime, "plugin_config", None),
                tool_caller=caller,
                knowledge_store=store,
                logger=getattr(runtime, "logger", None),
                get_knowledge_build_task=getter,
                set_knowledge_build_task=setter,
                trigger="webui_one_shot",
                force=True,
                allow_one_shot_disabled=True,
            )
            operation = {
                "id": uuid.uuid4().hex,
                "mode": "one_shot",
                "state": "queued" if result.get("started") else "failed",
                "started_at": time.time(),
                "finished_at": 0.0,
                "cancel_requested": False,
                "diagnostic_code": (
                    "plugin_knowledge_build_started"
                    if result.get("started")
                    else f"plugin_knowledge_build_{_safe_text(result.get('result'), 64) or 'not_started'}"
                ),
            }
            _KNOWLEDGE_BUILD_OPERATIONS[id(runtime)] = operation
        webui_audit_log.record(
            action="plugin_knowledge_one_shot_build",
            qq=admin.qq,
            device_id=admin.device_id,
            ip_hash=get_client_ip(request),
            detail={"operation_id": operation["id"], "mode": "one_shot"},
            outcome="ok" if result.get("started") else "failed",
        )
        return {
            "operation": dict(operation),
            "status": await _knowledge_status_payload(runtime),
            "diagnostic_code": operation["diagnostic_code"],
        }

    @router.get("/builds/{build_id}")
    async def get_one_shot_build(
        build_id: str,
        _: AdminIdentity = Depends(require_admin),
    ) -> dict[str, Any]:
        operation = _KNOWLEDGE_BUILD_OPERATIONS.get(id(runtime))
        if not isinstance(operation, dict) or str(operation.get("id") or "") != build_id:
            raise HTTPException(
                status_code=404,
                detail={"code": "plugin_knowledge_build_not_found", "message": "构建任务不存在或服务已重启。"},
            )
        status = await _knowledge_status_payload(runtime)
        return {
            "operation": dict(_KNOWLEDGE_BUILD_OPERATIONS.get(id(runtime)) or operation),
            "status": status,
            "diagnostic_code": str(status.get("diagnostic_code") or "plugin_knowledge_build_status_ready"),
        }

    @router.delete("/builds/{build_id}")
    async def cancel_one_shot_build(
        build_id: str,
        request: Request,
        admin: AdminIdentity = Depends(require_admin),
    ) -> dict[str, Any]:
        operation = _KNOWLEDGE_BUILD_OPERATIONS.get(id(runtime))
        if not isinstance(operation, dict) or str(operation.get("id") or "") != build_id:
            raise HTTPException(
                status_code=404,
                detail={"code": "plugin_knowledge_build_not_found", "message": "构建任务不存在或服务已重启。"},
            )
        store = _knowledge_store(runtime)
        getter, setter, _caller = _knowledge_task_handles(runtime)
        async with _knowledge_build_lock(runtime):
            operation.update(
                {
                    "state": "cancelling",
                    "cancel_requested": True,
                    "diagnostic_code": "plugin_knowledge_build_cancelling",
                }
            )
            from ...core.knowledge_builder import stop_plugin_knowledge_builder

            cancelled = await stop_plugin_knowledge_builder(
                logger=getattr(runtime, "logger", None),
                knowledge_store=store,
                get_knowledge_build_task=getter,
                set_knowledge_build_task=setter,
                enabled=bool(
                    getattr(
                        getattr(runtime, "plugin_config", None),
                        "personification_plugin_knowledge_build_enabled",
                        False,
                    )
                ),
                trigger="webui_one_shot_cancel",
                result="cancelled" if getter and getter() is not None else "idle",
                reasons=["admin_cancelled"],
            )
            operation.update(
                {
                    "state": "cancelled",
                    "finished_at": time.time(),
                    "diagnostic_code": (
                        "plugin_knowledge_build_cancelled" if cancelled else "plugin_knowledge_build_already_idle"
                    ),
                }
            )
        webui_audit_log.record(
            action="plugin_knowledge_one_shot_cancel",
            qq=admin.qq,
            device_id=admin.device_id,
            ip_hash=get_client_ip(request),
            detail={"operation_id": build_id, "cancelled": cancelled},
            outcome="ok",
        )
        return {
            "operation": dict(operation),
            "status": await _knowledge_status_payload(runtime),
            "diagnostic_code": operation["diagnostic_code"],
        }

    @router.get("/search")
    async def search(
        q: str = Query("", min_length=0, max_length=120),
        top_k: int = Query(10, ge=1, le=50),
        _: AdminIdentity = Depends(require_admin),
    ) -> dict:
        store = _knowledge_store(runtime)
        if store is None:
            return _attach_diagnostic(
                {"results": [], "items": [], "query": q, "available": False},
                _store_unavailable_report(operation="插件知识搜索"),
            )
        query = q.strip()
        if not query:
            report = diagnostic(
                ok=True,
                code="plugin_knowledge_search_skipped",
                phase="query_validation",
                title="插件知识搜索未执行",
                message="搜索词为空，已返回空结果。",
                steps=(step("validate_query", "校验搜索词", "skipped", "没有可执行的搜索词。"),),
            )
            return _attach_diagnostic(
                {"results": [], "items": [], "query": q, "available": True},
                report,
            )
        try:
            names = await run_in_threadpool(store.search_plugins, query, top_k=top_k)
            results = [
                item
                for item in (_safe_scalar_text(name, 200) for name in list(names)[:top_k])
                if item
            ]
            catalog = await run_in_threadpool(list_plugin_knowledge_items, runtime)
            by_name = {str(item.get("plugin_name") or ""): item for item in catalog}
            items = [by_name[name] for name in results if name in by_name]
        except Exception as exc:
            storage_code = str(exc)
            raise _read_failure(
                runtime,
                exc,
                code=(
                    storage_code
                    if storage_code in {
                        "plugin_knowledge_index_corrupt",
                        "plugin_knowledge_index_too_large",
                        "plugin_knowledge_index_unavailable",
                    }
                    else "plugin_knowledge_search_failed"
                ),
                phase="index_search",
                title="插件知识搜索失败",
                message="服务器无法安全搜索插件知识索引。",
                step_key="search_index",
                step_label="搜索插件知识索引",
            ) from exc
        report = diagnostic(
            ok=True,
            code="plugin_knowledge_search_complete",
            phase="read_complete",
            title="插件知识搜索完成",
            message=f"已找到 {len(results)} 条匹配结果。",
            details=(operation_detail("结果数量", len(results), "ok"),),
            steps=(
                step("validate_query", "校验搜索词", "ok", "搜索词有效。"),
                step("search_index", "搜索插件知识索引", "ok", "索引搜索已完成。"),
            ),
        )
        return _attach_diagnostic(
            {"results": results, "items": items, "query": q, "available": True},
            report,
        )

    return router
