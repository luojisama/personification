from __future__ import annotations

import asyncio
import re
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from .sensitive_data import sanitize_text


_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_GIT_TIMEOUT_SECONDS = 60.0
_DEFAULT_PROBE_TIMEOUT_SECONDS = 8.0
_PROBE_CACHE_SECONDS = 60.0
_LOG_FORMAT = "%H%x1f%h%x1f%ct%x1f%an%x1f%s"
_UPDATE_LOCK = asyncio.Lock()
_BENCHMARK_CACHE: dict[tuple[str, str, str], dict[str, Any]] = {}
_OPERATIONS: dict[str, dict[str, Any]] = {}
_OPERATION_ORDER: deque[str] = deque(maxlen=100)


def default_plugin_root() -> Path:
    return _PLUGIN_ROOT


def _as_root(path: str | Path | None) -> Path:
    return Path(path or _PLUGIN_ROOT).resolve()


def _short_hash(value: str) -> str:
    raw = str(value or "").strip()
    return raw[:7] if raw else ""


def _redact_remote_url(url: str) -> str:
    raw = str(url or "").strip()
    if not raw:
        return ""
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return re.sub(r"^(https?://)([^/@]+@)", r"\1***@", raw)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return re.sub(r"^(https?://)([^/@]+@)", r"\1***@", raw)
    host = parsed.hostname
    try:
        if parsed.port:
            host = f"{host}:{parsed.port}"
    except ValueError:
        pass
    # Query/fragment frequently contain deploy tokens. They are never needed by
    # the WebUI diagnostics or command summary.
    return urlunsplit((parsed.scheme, host, parsed.path, "", ""))


def _sanitize_git_output(value: str) -> str:
    text = str(value or "")
    text = re.sub(r"https?://[^\s'\"]+", lambda match: _redact_remote_url(match.group(0)), text)
    return sanitize_text(text, limit=400)


def _public_git_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    result = dict(snapshot)
    source = dict(result.get("source") or {})
    source.pop("normalized_remote_url", None)
    result["source"] = source
    for key in ("dirty_preview", "plugin_root", "repo_root"):
        result.pop(key, None)
    return result


def normalize_git_remote_url(url: str) -> str:
    raw = str(url or "").strip()
    match = re.match(r"^git@([^:]+):(.+?)(?:\.git)?$", raw)
    if match:
        host = match.group(1)
        path = match.group(2).removesuffix(".git")
        return f"https://{host}/{path}.git"
    return raw


def _mirror_prefixes(plugin_config: Any) -> list[str]:
    plural = getattr(plugin_config, "personification_git_mirror_prefixes", None) or []
    if isinstance(plural, str):
        plural = [item.strip() for item in plural.split(",")]
    singular = str(getattr(plugin_config, "personification_git_mirror_prefix", "") or "").strip()
    values: list[str] = []
    seen: set[str] = set()
    for item in [*list(plural or []), singular]:
        value = str(item or "").strip().rstrip("/")
        if value and value not in seen:
            seen.add(value)
            values.append(value)
    return values


def _probe_timeout(plugin_config: Any) -> float:
    try:
        value = float(getattr(plugin_config, "personification_git_probe_timeout_seconds", _DEFAULT_PROBE_TIMEOUT_SECONDS) or _DEFAULT_PROBE_TIMEOUT_SECONDS)
    except (TypeError, ValueError):
        value = _DEFAULT_PROBE_TIMEOUT_SECONDS
    return max(2.0, min(value, 30.0))


def _looks_like_network_failure(stderr: str) -> bool:
    text = str(stderr or "").lower()
    return any(
        keyword in text
        for keyword in (
            "gnutls_handshake", "ssl_read", "ssl_connect", "tls", "http2", "framing layer", "curl 16",
            "flush packet", "could not resolve host", "couldn't connect", "connection refused", "connection reset",
            "connection timed out", "operation timed out", "timed out", "timeout", "unable to access", "failed to connect",
            "early eof", "rpc failed", "git 命令超时", "超时", "网络", "无法访问",
        )
    )


def _failure_code(stderr: str, *, timed_out: bool = False) -> str:
    text = str(stderr or "").lower()
    if timed_out or "timed out" in text or "timeout" in text or "超时" in text:
        return "git_source_timeout"
    if "could not resolve host" in text or "name or service not known" in text or "dns" in text:
        return "git_source_dns_failed"
    if any(value in text for value in ("tls", "ssl", "certificate", "gnutls")):
        return "git_source_tls_failed"
    if any(value in text for value in ("repository not found", "authentication failed", "permission denied", "403", "401")):
        return "git_source_permission_failed"
    if any(value in text for value in ("not valid", "expected flush", "invalid", "html")):
        return "git_source_invalid_response"
    if _looks_like_network_failure(text):
        return "git_source_network_failed"
    return "git_source_git_failed"


async def _run_git_command(
    args: list[str],
    *,
    cwd: str,
    extra_config: list[str] | None = None,
    timeout: float = _GIT_TIMEOUT_SECONDS,
) -> tuple[int, str, str]:
    command: list[str] = []
    for item in extra_config or []:
        command.extend(["-c", item])
    command.extend(args)
    try:
        process = await asyncio.create_subprocess_exec(
            "git",
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=max(1.0, float(timeout or 1.0)))
    except asyncio.TimeoutError:
        return -1, "", f"git 命令超时（{int(timeout)}s）"
    except FileNotFoundError:
        return -1, "", "找不到 git 命令，请确认已安装 git"
    except Exception as exc:
        return -1, "", str(exc)
    return (
        int(process.returncode or 0),
        stdout.decode("utf-8", errors="replace").strip(),
        stderr.decode("utf-8", errors="replace").strip(),
    )


def _source_rewrite(source: dict[str, Any]) -> list[str]:
    if source.get("kind") != "mirror" or source.get("applicable") is False:
        return []
    base_url = str(source.get("base_url") or "").rstrip("/")
    return [f"url.{base_url}/https://github.com/.insteadOf=https://github.com/"]


def _source_candidates(plugin_config: Any, repo_url: str) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    try:
        mirror_applicable = (urlsplit(repo_url).hostname or "").lower() == "github.com"
    except ValueError:
        mirror_applicable = False
    for index, mirror in enumerate(_mirror_prefixes(plugin_config), start=1):
        candidates.append(
            {
                "source_id": f"mirror_{index}",
                "kind": "mirror",
                "display_name": f"镜像 {index}",
                "base_url": _redact_remote_url(mirror),
                "repo_url": repo_url,
                "order": index - 1,
                "applicable": mirror_applicable,
            }
        )
    candidates.append(
        {
            "source_id": "official",
            "kind": "official",
            "display_name": "官方源",
            "base_url": _redact_remote_url(repo_url),
            "repo_url": repo_url,
            "order": len(candidates),
            "applicable": True,
        }
    )
    return candidates


def _valid_ls_remote(output: str) -> bool:
    for line in str(output or "").splitlines():
        parts = line.split()
        if len(parts) >= 2 and re.fullmatch(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", parts[0]) and parts[1] == "HEAD":
            return True
    return False


async def _probe_source(source: dict[str, Any], *, cwd: str, timeout: float) -> dict[str, Any]:
    if source.get("applicable") is False:
        checked_at = time.time()
        return {
            "source_id": source["source_id"],
            "kind": source["kind"],
            "display_name": source["display_name"],
            "base_url": source["base_url"],
            "state": "inapplicable",
            "latency_ms": None,
            "rank": None,
            "checked_at": checked_at,
            "expires_at": checked_at + _PROBE_CACHE_SECONDS,
            "diagnostic_code": "git_source_mirror_inapplicable",
            "_order": source["order"],
            "_repo_url": source["repo_url"],
        }
    started = time.monotonic()
    rc, out, err = await _run_git_command(
        ["ls-remote", str(source.get("repo_url") or ""), "HEAD"],
        cwd=cwd,
        extra_config=_source_rewrite(source),
        timeout=timeout,
    )
    latency_ms = max(0, int((time.monotonic() - started) * 1000))
    checked_at = time.time()
    if rc == 0 and _valid_ls_remote(out):
        state = "succeeded"
        code = "git_source_probe_succeeded"
    elif rc == -1 and "超时" in err:
        state = "timeout"
        code = "git_source_timeout"
    else:
        state = "failed"
        code = "git_source_invalid_response" if rc == 0 else _failure_code(err)
    return {
        "source_id": source["source_id"],
        "kind": source["kind"],
        "display_name": source["display_name"],
        "base_url": source["base_url"],
        "state": state,
        "latency_ms": latency_ms if state == "succeeded" else None,
        "rank": None,
        "checked_at": checked_at,
        "expires_at": checked_at + _PROBE_CACHE_SECONDS,
        "diagnostic_code": code,
        "_order": source["order"],
        "_repo_url": source["repo_url"],
    }


async def _origin_https_url(cwd: str, remote_name: str = "origin") -> str:
    rc, out, _err = await _run_git_command(["remote", "get-url", remote_name or "origin"], cwd=cwd)
    return normalize_git_remote_url(out) if rc == 0 else ""


async def _resolve_git_context(plugin_root: Path) -> tuple[bool, dict[str, Any]]:
    rc, out, err = await _run_git_command(["rev-parse", "--show-toplevel"], cwd=str(plugin_root))
    if rc != 0:
        return False, {
            "available": False,
            "source_type": "unknown",
            "update_supported": False,
            "plugin_root": str(plugin_root),
            "message": f"当前安装目录不是 Git 仓库：{_sanitize_git_output(err or out or '无法解析仓库根目录')}",
            "diagnostic_code": "plugin_update_git_repository_unavailable",
        }
    repo_root = Path(out).resolve()
    try:
        plugin_subdir = str(plugin_root.relative_to(repo_root)).replace("\\", "/") or "."
    except Exception:
        plugin_subdir = ""
    return True, {
        "available": True,
        "source_type": "git",
        "update_supported": True,
        "plugin_root": str(plugin_root),
        "repo_root": str(repo_root),
        "plugin_subdir": plugin_subdir,
    }


async def _local_snapshot(plugin_root: Path, *, history_limit: int = 12) -> dict[str, Any]:
    ok, context = await _resolve_git_context(plugin_root)
    if not ok:
        return context
    cwd = str(context["repo_root"])
    rc_branch, branch, _ = await _run_git_command(["rev-parse", "--abbrev-ref", "HEAD"], cwd=cwd)
    branch = branch if rc_branch == 0 else ""
    rc_upstream, upstream, upstream_err = await _run_git_command(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"], cwd=cwd)
    upstream = upstream if rc_upstream == 0 else ""
    remote_name = upstream.split("/", 1)[0] if "/" in upstream else "origin"
    remote_url = await _origin_https_url(cwd, remote_name)
    rc_local, local_hash, local_err = await _run_git_command(["rev-parse", "HEAD"], cwd=cwd)
    rc_dirty, dirty_out, _ = await _run_git_command(["status", "--porcelain"], cwd=cwd)
    dirty_lines = [line for line in str(dirty_out or "").splitlines() if line.strip()] if rc_dirty == 0 else []
    history = await _git_log(cwd, "", limit=history_limit)
    return {
        **context,
        "source": {
            "type": "git",
            "remote_name": remote_name,
            "remote_url": _redact_remote_url(remote_url),
            "normalized_remote_url": remote_url,
            "branch": branch,
            "upstream": upstream,
        },
        "local": {"hash": local_hash if rc_local == 0 else "", "short_hash": _short_hash(local_hash), "branch": branch, "error": local_err if rc_local else ""},
        "dirty": bool(dirty_lines),
        "dirty_count": len(dirty_lines),
        "dirty_preview": dirty_lines[:12],
        "history": history,
        "upstream_error": upstream_err if not upstream else "",
    }


async def benchmark_update_sources(
    *,
    plugin_root: str | Path | None = None,
    plugin_config: Any = None,
    allow_cached: bool = False,
) -> dict[str, Any]:
    snapshot = await _local_snapshot(_as_root(plugin_root), history_limit=1)
    if not snapshot.get("available"):
        return {"ok": False, "probes": [], "diagnostic_code": snapshot.get("diagnostic_code", "plugin_update_unavailable"), "status": _public_git_snapshot(snapshot)}
    source = snapshot.get("source") or {}
    repo_url = str(source.get("normalized_remote_url") or "")
    if not repo_url.startswith(("http://", "https://")):
        return {"ok": False, "probes": [], "diagnostic_code": "git_remote_probe_inapplicable", "status": _public_git_snapshot(snapshot)}
    cache_key = (repo_url, str(source.get("remote_name") or "origin"), str(source.get("branch") or ""))
    cached = _BENCHMARK_CACHE.get(cache_key)
    now = time.time()
    if allow_cached and cached and float(cached.get("expires_at", 0) or 0) > now:
        return {**cached, "cache_hit": True}
    candidates = _source_candidates(plugin_config, repo_url)
    results = await asyncio.gather(*(_probe_source(item, cwd=str(snapshot["repo_root"]), timeout=_probe_timeout(plugin_config)) for item in candidates))
    succeeded = sorted((item for item in results if item["state"] == "succeeded"), key=lambda item: (int(item["latency_ms"] or 0), int(item["_order"])))
    for rank, item in enumerate(succeeded, start=1):
        item["rank"] = rank
    public = [{key: value for key, value in item.items() if not key.startswith("_")} for item in results]
    ranked_ids = [item["source_id"] for item in succeeded]
    result = {
        "ok": bool(succeeded),
        "probes": public,
        "ranked_source_ids": ranked_ids,
        "selected_source_id": ranked_ids[0] if ranked_ids else None,
        "checked_at": now,
        "expires_at": now + _PROBE_CACHE_SECONDS,
        "diagnostic_code": "git_source_benchmark_ready" if succeeded else "git_source_benchmark_all_failed",
        "cache_hit": False,
        "repository_key": {"remote_url": _redact_remote_url(repo_url), "remote_name": cache_key[1], "branch": cache_key[2]},
    }
    _BENCHMARK_CACHE[cache_key] = result
    return result


def _candidate_by_id(plugin_config: Any, repo_url: str, source_id: str) -> dict[str, Any] | None:
    return next((item for item in _source_candidates(plugin_config, repo_url) if item["source_id"] == source_id), None)


async def _fetch_ranked(
    *,
    snapshot: dict[str, Any],
    benchmark: dict[str, Any],
    plugin_config: Any,
) -> dict[str, Any]:
    source = snapshot.get("source") or {}
    repo_url = str(source.get("normalized_remote_url") or "")
    remote_name = str(source.get("remote_name") or "origin")
    attempts: list[dict[str, Any]] = []
    for source_id in benchmark.get("ranked_source_ids") or []:
        candidate = _candidate_by_id(plugin_config, repo_url, str(source_id))
        if candidate is None:
            continue
        started = time.monotonic()
        rc, out, err = await _run_git_command(
            ["fetch", "--prune", remote_name],
            cwd=str(snapshot.get("repo_root") or ""),
            extra_config=_source_rewrite(candidate),
            timeout=_GIT_TIMEOUT_SECONDS,
        )
        attempt = {
            "source_id": source_id,
            "state": "succeeded" if rc == 0 else "failed",
            "latency_ms": max(0, int((time.monotonic() - started) * 1000)),
            "diagnostic_code": "git_fetch_succeeded" if rc == 0 else _failure_code(err, timed_out=rc == -1),
            "output_summary": _sanitize_git_output(out or err or ""),
        }
        attempts.append(attempt)
        if rc == 0:
            return {"ok": True, "selected_source_id": source_id, "attempts": attempts, "output": out, "diagnostic_code": "git_fetch_succeeded"}
        if not _looks_like_network_failure(err):
            return {"ok": False, "selected_source_id": source_id, "attempts": attempts, "error": err or out, "diagnostic_code": attempt["diagnostic_code"], "deterministic": True}
    return {"ok": False, "selected_source_id": None, "attempts": attempts, "error": "所有测速成功源均无法完成 fetch。", "diagnostic_code": "git_fetch_all_sources_failed", "deterministic": False}


async def _remote_snapshot(snapshot: dict[str, Any], *, history_limit: int) -> dict[str, Any]:
    cwd = str(snapshot.get("repo_root") or "")
    upstream = str((snapshot.get("source") or {}).get("upstream") or "")
    local_hash = str((snapshot.get("local") or {}).get("hash") or "")
    if not upstream:
        return {"remote": {"hash": "", "short_hash": "", "upstream": "", "error": snapshot.get("upstream_error") or "当前分支未配置 upstream"}, "ahead": 0, "behind": 0, "pending_history": [], "update_available": False}
    rc_remote, remote_hash, remote_err = await _run_git_command(["rev-parse", "@{u}"], cwd=cwd)
    if rc_remote != 0:
        remote_hash = ""
    rc_ab, raw_counts, _ = await _run_git_command(["rev-list", "--left-right", "--count", "HEAD...@{u}"], cwd=cwd)
    ahead, behind = _parse_ahead_behind(raw_counts) if rc_ab == 0 else (0, 0)
    pending = await _git_log(cwd, "HEAD..@{u}", limit=history_limit)
    return {
        "remote": {"hash": remote_hash, "short_hash": _short_hash(remote_hash), "upstream": upstream, "error": remote_err if rc_remote else ""},
        "ahead": ahead,
        "behind": behind,
        "pending_history": pending,
        "update_available": bool(remote_hash and local_hash != remote_hash and behind > 0),
    }


async def get_plugin_update_status(
    *,
    plugin_root: str | Path | None = None,
    plugin_config: Any = None,
    refresh: bool = False,
    history_limit: int = 12,
) -> dict[str, Any]:
    snapshot = await _local_snapshot(_as_root(plugin_root), history_limit=history_limit)
    if not snapshot.get("available"):
        snapshot["checked_at"] = time.time()
        return snapshot
    benchmark: dict[str, Any] = {"ok": False, "probes": [], "ranked_source_ids": [], "selected_source_id": None, "diagnostic_code": "git_source_benchmark_not_run"}
    fetch: dict[str, Any] = {"attempted": False, "ok": True, "selected_source_id": None, "attempts": [], "diagnostic_code": "git_fetch_not_run"}
    if refresh and not snapshot.get("dirty"):
        benchmark = await benchmark_update_sources(plugin_root=plugin_root, plugin_config=plugin_config, allow_cached=False)
        fetch = {"attempted": True, **await _fetch_ranked(snapshot=snapshot, benchmark=benchmark, plugin_config=plugin_config)} if benchmark.get("ok") else {"attempted": True, "ok": False, "selected_source_id": None, "attempts": [], "diagnostic_code": "git_source_benchmark_all_failed", "error": "没有可用 Git 更新源。"}
    remote = await _remote_snapshot(snapshot, history_limit=history_limit)
    message = "已是最新版本"
    if snapshot.get("dirty"):
        message = "本地有未提交或未跟踪内容，自动更新已锁定"
    elif refresh and not fetch.get("ok"):
        message = f"远端检查失败：{_sanitize_git_output(fetch.get('error') or fetch.get('diagnostic_code'))}"
    elif not (snapshot.get("source") or {}).get("upstream"):
        message = "当前分支未配置 upstream"
    elif remote.get("update_available"):
        message = f"发现 {remote.get('behind') or len(remote.get('pending_history') or []) or 1} 个待更新提交"
    elif int(remote.get("ahead") or 0) > 0:
        message = f"本地领先 upstream {remote.get('ahead')} 个提交"
    public_snapshot = _public_git_snapshot(snapshot)
    return {
        **public_snapshot,
        **remote,
        "checked_at": time.time(),
        "benchmark": benchmark,
        "probes": benchmark.get("probes") or [],
        "selected_source_id": fetch.get("selected_source_id") or benchmark.get("selected_source_id"),
        "fetch": fetch,
        "message": message,
        "diagnostic_code": "plugin_update_status_ready" if not refresh or fetch.get("ok") else str(fetch.get("diagnostic_code") or "plugin_update_check_failed"),
    }


async def get_plugin_update_history(
    *,
    plugin_root: str | Path | None = None,
    plugin_config: Any = None,
    limit: int = 30,
    refresh: bool = False,
) -> dict[str, Any]:
    status = await get_plugin_update_status(plugin_root=plugin_root, plugin_config=plugin_config, refresh=refresh, history_limit=limit)
    return {
        "available": bool(status.get("available")),
        "source_type": status.get("source_type", "unknown"),
        "source": status.get("source", {}),
        "history": status.get("history", []),
        "pending_history": status.get("pending_history", []),
        "probes": status.get("probes", []),
        "fetch": status.get("fetch", {}),
        "operations": list_update_operations(limit=limit),
        "message": status.get("message", ""),
    }


def _start_operation(state: str, local_commit: str = "") -> dict[str, Any]:
    operation_id = uuid.uuid4().hex
    operation = {
        "operation_id": operation_id,
        "state": state,
        "local_commit": local_commit,
        "remote_commit": None,
        "dirty": False,
        "probes": [],
        "selected_source_id": None,
        "attempts": [],
        "diagnostic_code": "plugin_update_operation_started",
        "started_at": time.time(),
        "finished_at": None,
    }
    _OPERATIONS[operation_id] = operation
    _OPERATION_ORDER.appendleft(operation_id)
    return operation


def get_update_operation(operation_id: str) -> dict[str, Any] | None:
    operation = _OPERATIONS.get(str(operation_id or ""))
    return dict(operation) if operation else None


def list_update_operations(*, limit: int = 30) -> list[dict[str, Any]]:
    return [dict(_OPERATIONS[key]) for key in list(_OPERATION_ORDER)[: max(1, min(int(limit), 100))] if key in _OPERATIONS]


async def benchmark_update_operation(
    *,
    plugin_root: str | Path | None = None,
    plugin_config: Any = None,
) -> dict[str, Any]:
    """Run and persist a read-only five-source benchmark operation."""
    if _UPDATE_LOCK.locked():
        return {"ok": False, "diagnostic_code": "plugin_update_busy", "error": "另一个命令或 WebUI 更新操作正在进行。"}
    async with _UPDATE_LOCK:
        snapshot = await _local_snapshot(_as_root(plugin_root), history_limit=12)
        operation = _start_operation("probing", str((snapshot.get("local") or {}).get("hash") or ""))
        operation["dirty"] = bool(snapshot.get("dirty"))
        if not snapshot.get("update_supported"):
            operation.update({"state": "failed", "diagnostic_code": "plugin_update_unsupported", "finished_at": time.time()})
            return {"ok": False, "operation": dict(operation), "status": _public_git_snapshot(snapshot), "diagnostic_code": operation["diagnostic_code"]}
        benchmark = await benchmark_update_sources(plugin_root=plugin_root, plugin_config=plugin_config, allow_cached=False)
        operation.update(
            {
                "state": "ready" if benchmark.get("ok") else "failed",
                "probes": list(benchmark.get("probes") or []),
                "selected_source_id": benchmark.get("selected_source_id"),
                "diagnostic_code": benchmark.get("diagnostic_code"),
                "finished_at": time.time(),
            }
        )
        status = await get_plugin_update_status(plugin_root=plugin_root, plugin_config=plugin_config, refresh=False)
        return {"ok": bool(benchmark.get("ok")), "operation": dict(operation), "status": status, "diagnostic_code": operation["diagnostic_code"]}


async def check_plugin_update(
    *,
    plugin_root: str | Path | None = None,
    plugin_config: Any = None,
) -> dict[str, Any]:
    """Benchmark, fetch with ranked fallback, and persist a read-only check."""
    if _UPDATE_LOCK.locked():
        return {"ok": False, "diagnostic_code": "plugin_update_busy", "error": "另一个命令或 WebUI 更新操作正在进行。"}
    async with _UPDATE_LOCK:
        initial = await _local_snapshot(_as_root(plugin_root), history_limit=12)
        operation = _start_operation("probing", str((initial.get("local") or {}).get("hash") or ""))
        operation["dirty"] = bool(initial.get("dirty"))
        status = await get_plugin_update_status(plugin_root=plugin_root, plugin_config=plugin_config, refresh=True)
        benchmark = status.get("benchmark") if isinstance(status.get("benchmark"), dict) else {}
        fetch = status.get("fetch") if isinstance(status.get("fetch"), dict) else {}
        operation.update(
            {
                "state": "ready" if fetch.get("ok") and status.get("update_supported") else "failed",
                "remote_commit": str((status.get("remote") or {}).get("hash") or "") or None,
                "probes": list(benchmark.get("probes") or status.get("probes") or []),
                "selected_source_id": fetch.get("selected_source_id") or benchmark.get("selected_source_id"),
                "attempts": list(fetch.get("attempts") or []),
                "diagnostic_code": status.get("diagnostic_code") or "plugin_update_check_failed",
                "finished_at": time.time(),
            }
        )
        if initial.get("dirty"):
            operation.update({"state": "failed", "diagnostic_code": "plugin_update_dirty"})
        return {
            "ok": operation["state"] == "ready",
            "operation": dict(operation),
            "status": status,
            "diagnostic_code": operation["diagnostic_code"],
            "error": "" if operation["state"] == "ready" else status.get("message") or operation["diagnostic_code"],
        }


async def perform_plugin_update(
    *,
    plugin_root: str | Path | None = None,
    plugin_config: Any = None,
) -> dict[str, Any]:
    if _UPDATE_LOCK.locked():
        return {"ok": False, "updated": False, "error": "另一个命令或 WebUI 更新操作正在进行。", "diagnostic_code": "plugin_update_busy"}
    async with _UPDATE_LOCK:
        snapshot = await _local_snapshot(_as_root(plugin_root), history_limit=20)
        operation = _start_operation("probing", str((snapshot.get("local") or {}).get("hash") or ""))
        operation["dirty"] = bool(snapshot.get("dirty"))
        if not snapshot.get("update_supported"):
            operation.update({"state": "failed", "diagnostic_code": "plugin_update_unsupported", "finished_at": time.time()})
            return {"ok": False, "updated": False, "status": _public_git_snapshot(snapshot), "operation": dict(operation), "error": snapshot.get("message") or "当前安装源不支持自动更新"}
        if snapshot.get("dirty"):
            operation.update({"state": "failed", "diagnostic_code": "plugin_update_dirty", "finished_at": time.time()})
            return {"ok": False, "updated": False, "status": _public_git_snapshot(snapshot), "operation": dict(operation), "error": "本地有未提交或未跟踪内容，已拒绝自动更新。"}
        benchmark = await benchmark_update_sources(plugin_root=plugin_root, plugin_config=plugin_config, allow_cached=True)
        operation["probes"] = list(benchmark.get("probes") or [])
        operation["selected_source_id"] = benchmark.get("selected_source_id")
        if not benchmark.get("ok"):
            operation.update({"state": "failed", "diagnostic_code": "git_source_benchmark_all_failed", "finished_at": time.time()})
            return {"ok": False, "updated": False, "status": _public_git_snapshot(snapshot), "operation": dict(operation), "error": "四个镜像和官方源均未通过 Git 探测。"}
        operation["state"] = "fetching"
        fetch = await _fetch_ranked(snapshot=snapshot, benchmark=benchmark, plugin_config=plugin_config)
        operation["attempts"] = list(fetch.get("attempts") or [])
        operation["selected_source_id"] = fetch.get("selected_source_id")
        if not fetch.get("ok"):
            operation.update({"state": "failed", "diagnostic_code": fetch.get("diagnostic_code"), "finished_at": time.time()})
            return {"ok": False, "updated": False, "status": _public_git_snapshot(snapshot), "operation": dict(operation), "fetch": fetch, "error": _sanitize_git_output(fetch.get("error") or "fetch 失败")}
        remote = await _remote_snapshot(snapshot, history_limit=20)
        operation["remote_commit"] = str((remote.get("remote") or {}).get("hash") or "") or None
        if int(remote.get("ahead") or 0) > 0 and int(remote.get("behind") or 0) > 0:
            operation.update({"state": "failed", "diagnostic_code": "plugin_update_diverged", "finished_at": time.time()})
            return {"ok": False, "updated": False, "status": _public_git_snapshot({**snapshot, **remote}), "operation": dict(operation), "error": "本地与 upstream 已分叉，自动更新不会选择镜像掩盖分叉。"}
        if not remote.get("update_available"):
            operation.update({"state": "succeeded", "diagnostic_code": "plugin_update_already_current", "finished_at": time.time()})
            return {"ok": True, "updated": False, "status": _public_git_snapshot({**snapshot, **remote}), "operation": dict(operation), "message": "已是最新版本"}
        operation["state"] = "applying"
        cwd = str(snapshot.get("repo_root") or "")
        rc_merge, merge_out, merge_err = await _run_git_command(["merge", "--ff-only", "@{u}"], cwd=cwd)
        if rc_merge != 0:
            operation.update({"state": "failed", "diagnostic_code": "plugin_update_fast_forward_failed", "finished_at": time.time()})
            return {"ok": False, "updated": False, "status": _public_git_snapshot({**snapshot, **remote}), "operation": dict(operation), "error": _sanitize_git_output(merge_err or merge_out or "本地 fast-forward 失败")}
        after = await _local_snapshot(_as_root(plugin_root), history_limit=20)
        final_hash = str((after.get("local") or {}).get("hash") or "")
        expected_hash = str((remote.get("remote") or {}).get("hash") or "")
        if not expected_hash or final_hash != expected_hash or after.get("dirty"):
            operation.update({"state": "unknown", "diagnostic_code": "plugin_update_verification_unknown", "finished_at": time.time(), "final_commit": final_hash})
            return {"ok": False, "updated": False, "status": _public_git_snapshot(after), "operation": dict(operation), "error": "本地合并已执行，但最终 HEAD 或工作区状态未通过确认。"}
        operation.update({"state": "succeeded", "diagnostic_code": "plugin_update_applied", "finished_at": time.time(), "final_commit": final_hash})
        return {"ok": True, "updated": True, "before": _public_git_snapshot({**snapshot, **remote}), "status": _public_git_snapshot(after), "operation": dict(operation), "message": _sanitize_git_output(merge_out or "已完成本地 fast-forward；请重启 Bot 载入新版本。"), "fetch": fetch}


async def _run_git_with_mirror_fallback(
    args: list[str],
    *,
    cwd: str,
    plugin_config: Any = None,
    remote_name: str = "origin",
) -> tuple[int, str, str, str, list[dict[str, Any]]]:
    """Compatibility helper backed by the same ranked Git benchmark service."""
    repo_url = await _origin_https_url(cwd, remote_name)
    if not repo_url:
        rc, out, err = await _run_git_command(args, cwd=cwd)
        return rc, out, err, "", []
    candidates = _source_candidates(plugin_config, repo_url)
    probes = await asyncio.gather(*(_probe_source(item, cwd=cwd, timeout=_probe_timeout(plugin_config)) for item in candidates))
    successes = sorted((item for item in probes if item["state"] == "succeeded"), key=lambda item: (int(item["latency_ms"] or 0), int(item["_order"])))
    public = [{key: value for key, value in item.items() if not key.startswith("_")} for item in probes]
    for item in successes:
        candidate = _candidate_by_id(plugin_config, repo_url, str(item["source_id"]))
        if candidate is None:
            continue
        rc, out, err = await _run_git_command(args, cwd=cwd, extra_config=_source_rewrite(candidate))
        if rc == 0 or not _looks_like_network_failure(err):
            return rc, out, err, str(candidate.get("base_url") or "") if candidate.get("kind") == "mirror" else "", public
    return -1, "", "所有 Git 更新源均不可用", "", public


def _parse_commit_log(raw: str) -> list[dict[str, Any]]:
    commits: list[dict[str, Any]] = []
    for line in str(raw or "").splitlines():
        parts = line.split("\x1f", 4)
        if len(parts) != 5:
            continue
        full_hash, short_hash, timestamp, author, subject = parts
        try:
            ts = int(timestamp)
        except Exception:
            ts = 0
        commits.append({"hash": full_hash, "short_hash": short_hash, "timestamp": ts, "author": sanitize_text(author, limit=160), "subject": sanitize_text(subject, limit=400)})
    return commits


async def _git_log(cwd: str, rev_range: str, *, limit: int) -> list[dict[str, Any]]:
    args = ["log", f"--max-count={max(1, min(int(limit or 20), 100))}", f"--pretty=format:{_LOG_FORMAT}"]
    if rev_range:
        args.append(rev_range)
    rc, out, _ = await _run_git_command(args, cwd=cwd)
    return _parse_commit_log(out) if rc == 0 else []


def _parse_ahead_behind(value: str) -> tuple[int, int]:
    parts = str(value or "").split()
    if len(parts) < 2:
        return 0, 0
    try:
        return int(parts[0]), int(parts[1])
    except Exception:
        return 0, 0


__all__ = [
    "benchmark_update_operation",
    "benchmark_update_sources",
    "check_plugin_update",
    "default_plugin_root",
    "get_plugin_update_history",
    "get_plugin_update_status",
    "get_update_operation",
    "list_update_operations",
    "normalize_git_remote_url",
    "perform_plugin_update",
]
