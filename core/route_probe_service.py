"""Durable, privacy-safe lifecycle for model-route capability probes.

This module intentionally owns only operation metadata.  Provider payloads,
URLs, credentials, media bytes and visible answers never cross this boundary.
The actual probe callable is injected by the WebUI or scheduled job, which
keeps all probes on the selected route and makes it impossible for this layer
to send a QQ message.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable
from .paths import get_data_dir

_TERMINAL = {"succeeded", "failed", "cancelled", "interrupted", "inconclusive", "skipped"}
_SAFE_STATES = _TERMINAL | {"queued", "running", "cancel_requested"}
_MAX_PAGE_SIZE = 100
_CODE_PREFIXES = ("probe_", "function_call_", "native_search_", "reasoning_", "image_input_", "audio_input_", "video_input_", "media_", "builtin_")
_SAFE_CODES = {
    "gemini_response_no_candidates", "gemini_response_no_text", "gemini_response_safety_blocked",
    "gemini_response_json_invalid", "media_response_json_invalid",
    "video_input_builtin_content_mismatch", "audio_input_builtin_content_mismatch",
    "media_inline_budget_exceeded",
    "probe_auth_rejected", "probe_rate_limited", "probe_server_error", "probe_request_rejected",
    "probe_queued", "probe_running", "probe_cancel_requested", "probe_cancelled",
    "probe_process_interrupted", "probe_internal_failed", "probe_lease_unavailable",
    "probe_timeout", "probe_network_error", "probe_route_caller_unavailable",
    "probe_caller_build_failed", "probe_unavailable", "builtin_sample_integrity_failed",
    "builtin_sample_not_found", "media_probe_primary_route_unavailable", "media_probe_upload_required",
    "function_call_noop_structured_tool_call", "function_call_probe_inconclusive",
    "native_search_readonly_visible_answer", "native_search_probe_inconclusive",
    "reasoning_minimal_visible_answer", "reasoning_probe_inconclusive", "probe_visual_succeeded", "probe_visual_inconclusive",
    "audio_input_builtin_content_verified", "video_input_builtin_content_verified",
    "audio_input_custom_media_transport_verified", "video_input_custom_media_transport_verified",
    "audio_input_custom_media_transport_rejected", "video_input_custom_media_transport_rejected",
    "audio_input_probe_inconclusive", "video_input_probe_inconclusive",
}
_SAFE_STAGES = {"", "tool_call", "tool_continuation", "reasoning", "provider", "transport", "deadline", "media", "visual", "search"}
_FAILED_CODES = {"probe_auth_rejected","probe_rate_limited","probe_server_error","probe_request_rejected",
                 "probe_timeout","probe_network_error","probe_internal_failed","probe_caller_build_failed"}


def _safe_code(value: Any, fallback: str = "probe_unknown") -> str:
    text = "".join(c if c.isalnum() or c in "_.:-" else "_" for c in str(value or "").lower())
    text = text.strip("_.:-")[:96]
    return text if text in _SAFE_CODES else fallback


def _safe_stage(value: Any) -> str:
    value = str(value or "").strip().lower()
    return value if value in _SAFE_STAGES else ""


@dataclass(frozen=True)
class ProbeResult:
    capability_state: str = "unknown"
    verification_state: str = "inconclusive"
    detail_code: str = "probe_inconclusive"
    transport_verified: bool = False
    content_verified: bool = False
    stage: str = ""
    http_status: int | None = None
    input_count: int = 0


class RouteProbeStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS route_probe_operations (
              operation_id TEXT PRIMARY KEY, route_fingerprint TEXT NOT NULL,
              capability TEXT NOT NULL, source TEXT NOT NULL, probe_version TEXT NOT NULL,
              status TEXT NOT NULL, detail_code TEXT NOT NULL, capability_state TEXT NOT NULL,
              verification_state TEXT NOT NULL, transport_verified INTEGER NOT NULL DEFAULT 0,
              content_verified INTEGER NOT NULL DEFAULT 0, queued_at REAL NOT NULL,
              started_at REAL, finished_at REAL, updated_at REAL NOT NULL,
              day_key TEXT NOT NULL DEFAULT '', duration_ms INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_route_probe_operations_list ON route_probe_operations(route_fingerprint, status, queued_at DESC);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_route_probe_daily ON route_probe_operations(route_fingerprint, capability, day_key, probe_version, source)
              WHERE source='daily';
            CREATE TABLE IF NOT EXISTS route_probe_facts (
              route_fingerprint TEXT NOT NULL, capability TEXT NOT NULL,
              last_verified_json TEXT NOT NULL DEFAULT '', latest_attempt_json TEXT NOT NULL DEFAULT '',
              PRIMARY KEY(route_fingerprint, capability)
            );
            CREATE TABLE IF NOT EXISTS route_probe_leases (
              lease_name TEXT PRIMARY KEY, holder TEXT NOT NULL, expires_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS route_probe_scheduler_watermarks (
              day_key TEXT PRIMARY KEY, status TEXT NOT NULL, claimed_at REAL NOT NULL
            );
            """)
            # Additive migration for installations created before stage-level
            # diagnostic evidence existed. Values are codes/counts only.
            existing = {str(row[1]) for row in db.execute("PRAGMA table_info(route_probe_operations)")}
            for column, ddl in (
                ("stage", "TEXT NOT NULL DEFAULT ''"),
                ("http_status", "INTEGER"),
                ("input_count", "INTEGER NOT NULL DEFAULT 0"),
            ):
                if column not in existing:
                    db.execute(f"ALTER TABLE route_probe_operations ADD COLUMN {column} {ddl}")

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    @staticmethod
    def _dto(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        value = dict(row)
        result = {key: value[key] for key in (
            "operation_id", "route_fingerprint", "capability", "source", "probe_version", "status",
            "detail_code", "capability_state", "verification_state", "transport_verified", "content_verified",
            "queued_at", "started_at", "finished_at", "updated_at", "duration_ms",
            "stage", "http_status", "input_count",
        ) if key in value}
        for key in ("transport_verified", "content_verified"):
            if key in result:
                result[key] = bool(result[key])
        return result

    def recover_interrupted(self) -> int:
        now = time.time()
        with self._lock, self._connect() as db:
            active = db.execute("SELECT 1 FROM route_probe_leases WHERE lease_name='global' AND expires_at>?", (now,)).fetchone()
            if active is not None:
                return 0
            cursor = db.execute("UPDATE route_probe_operations SET status='interrupted', detail_code='probe_process_interrupted', finished_at=?, updated_at=? WHERE status IN ('queued','running','cancel_requested')", (now, now))
            return int(cursor.rowcount)

    def acquire_global_lease(self, holder: str, *, seconds: float = 600.0) -> bool:
        now=time.time(); expires=now+max(1.0,seconds)
        with self._lock, self._connect() as db:
            db.execute("INSERT INTO route_probe_leases(lease_name,holder,expires_at) VALUES('global',?,?) ON CONFLICT(lease_name) DO UPDATE SET holder=excluded.holder,expires_at=excluded.expires_at WHERE route_probe_leases.expires_at<? OR route_probe_leases.holder=excluded.holder", (holder,expires,now))
            row=db.execute("SELECT holder FROM route_probe_leases WHERE lease_name='global'",()).fetchone()
            return bool(row and row[0] == holder)

    def renew_global_lease(self, holder: str, *, seconds: float = 600.0) -> bool:
        now=time.time()
        with self._lock, self._connect() as db:
            return bool(db.execute("UPDATE route_probe_leases SET expires_at=? WHERE lease_name='global' AND holder=? AND expires_at>?", (now+max(1.0,seconds),holder,now)).rowcount)

    def release_global_lease(self, holder: str) -> None:
        with self._lock, self._connect() as db:
            db.execute("DELETE FROM route_probe_leases WHERE lease_name='global' AND holder=?", (holder,))

    def claim_due_day(self, day_key: str, *, status: str = "catchup") -> bool:
        """Atomic once-only watermark; historical days are never replayed."""
        with self._lock, self._connect() as db:
            return bool(db.execute("INSERT OR IGNORE INTO route_probe_scheduler_watermarks(day_key,status,claimed_at) VALUES(?,?,?)", (str(day_key)[:16],str(status)[:24],time.time())).rowcount)

    def create(self, *, route_fingerprint: str, capability: str, source: str = "manual", probe_version: str = "v1", day_key: str = "") -> dict[str, Any]:
        now = time.time(); operation_id = f"rp-{uuid.uuid4().hex}"
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if source == "daily":
                prior = db.execute("SELECT * FROM route_probe_operations WHERE route_fingerprint=? AND capability=? AND day_key=? AND probe_version=? AND source='daily'", (route_fingerprint, capability, day_key, probe_version)).fetchone()
                if prior is not None:
                    return self._dto(prior)
            # A manual click and a daily job must join the same active work;
            # duplicate provider calls are neither useful nor safe.
            active = db.execute(
                "SELECT * FROM route_probe_operations WHERE route_fingerprint=? AND capability=? AND probe_version=? AND status IN ('queued','running','cancel_requested') ORDER BY queued_at ASC LIMIT 1",
                (route_fingerprint[:96], capability[:48], probe_version[:32]),
            ).fetchone()
            if active is not None:
                return self._dto(active)
            db.execute("INSERT INTO route_probe_operations(operation_id,route_fingerprint,capability,source,probe_version,status,detail_code,capability_state,verification_state,queued_at,updated_at,day_key) VALUES(?,?,?,?,?,'queued','probe_queued','unknown','not_run',?,?,?)", (operation_id, route_fingerprint[:96], capability[:48], source[:24], probe_version[:32], now, now, day_key[:16]))
            row = db.execute("SELECT * FROM route_probe_operations WHERE operation_id=?", (operation_id,)).fetchone()
            return self._dto(row)

    def get(self, operation_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as db:
            row = db.execute("SELECT * FROM route_probe_operations WHERE operation_id=?", (str(operation_id)[:96],)).fetchone()
            return self._dto(row) if row else None

    def list(self, *, route_fingerprint: str = "", status: str = "", page: int = 1, page_size: int = 20) -> dict[str, Any]:
        page=max(1,int(page)); page_size=min(_MAX_PAGE_SIZE,max(1,int(page_size))); clauses=[]; args=[]
        if route_fingerprint: clauses.append("route_fingerprint=?"); args.append(route_fingerprint[:96])
        if status in _SAFE_STATES: clauses.append("status=?"); args.append(status)
        where=(" WHERE "+" AND ".join(clauses)) if clauses else ""
        with self._lock, self._connect() as db:
            total=int(db.execute("SELECT count(*) FROM route_probe_operations"+where,args).fetchone()[0])
            rows=db.execute("SELECT * FROM route_probe_operations"+where+" ORDER BY queued_at DESC LIMIT ? OFFSET ?",(*args,page_size,(page-1)*page_size)).fetchall()
        return {"items":[self._dto(row) for row in rows],"page":page,"page_size":page_size,"total":total,"total_pages":max(1,(total+page_size-1)//page_size)}

    def cancel(self, operation_id: str) -> dict[str, Any] | None:
        now=time.time()
        with self._lock, self._connect() as db:
            db.execute("UPDATE route_probe_operations SET status='cancel_requested', detail_code='probe_cancel_requested', updated_at=? WHERE operation_id=? AND status IN ('queued','running')", (now,str(operation_id)[:96]))
        return self.get(operation_id)

    def begin(self, operation_id: str) -> bool:
        now=time.time()
        with self._lock, self._connect() as db:
            return bool(db.execute("UPDATE route_probe_operations SET status='running', started_at=?, updated_at=? WHERE operation_id=? AND status='queued'", (now,now,operation_id)).rowcount)

    def finish(self, operation_id: str, result: ProbeResult | None = None, *, failure_code: str = "") -> dict[str, Any] | None:
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row=db.execute("SELECT * FROM route_probe_operations WHERE operation_id=?", (operation_id,)).fetchone()
            current=self._dto(row) if row else None
            if not current: return None
            now=time.time(); cancelled=current["status"] == "cancel_requested"; result=result or ProbeResult(detail_code=failure_code or "probe_internal_failed")
            if cancelled: status, code = "cancelled", "probe_cancelled"
            elif failure_code: status, code = "failed", _safe_code(failure_code)
            elif result.detail_code in _FAILED_CODES: status, code = "failed", _safe_code(result.detail_code)
            elif result.verification_state == "verified": status, code = "succeeded", _safe_code(result.detail_code)
            else: status, code = "inconclusive", _safe_code(result.detail_code)
            started=float(current.get("started_at") or current["queued_at"]); duration=max(0,int((now-started)*1000))
            updated = db.execute("UPDATE route_probe_operations SET status=?,detail_code=?,capability_state=?,verification_state=?,transport_verified=?,content_verified=?,finished_at=?,updated_at=?,duration_ms=?,stage=?,http_status=?,input_count=? WHERE operation_id=? AND status NOT IN ('succeeded','failed','cancelled','interrupted','inconclusive','skipped')", (status,code,result.capability_state,result.verification_state,int(result.transport_verified),int(result.content_verified),now,now,duration,_safe_stage(result.stage),int(result.http_status) if isinstance(result.http_status, int) else None,max(0, min(64, int(result.input_count or 0))),operation_id)).rowcount
            row=db.execute("SELECT * FROM route_probe_operations WHERE operation_id=?",(operation_id,)).fetchone(); dto=self._dto(row)
            if not updated:
                return dto
            latest=json.dumps(dto,ensure_ascii=True,separators=(",",":"))
            verified=latest if result.verification_state == "verified" and status == "succeeded" else None
            db.execute("INSERT INTO route_probe_facts(route_fingerprint,capability,last_verified_json,latest_attempt_json) VALUES(?,?,?,?) ON CONFLICT(route_fingerprint,capability) DO UPDATE SET latest_attempt_json=excluded.latest_attempt_json,last_verified_json=CASE WHEN excluded.last_verified_json<>'' THEN excluded.last_verified_json ELSE route_probe_facts.last_verified_json END", (dto["route_fingerprint"],dto["capability"],verified or "",latest))
        return dto

    def facts(self, route_fingerprint: str, capability: str) -> dict[str, Any]:
        with self._lock, self._connect() as db:
            row=db.execute("SELECT last_verified_json,latest_attempt_json FROM route_probe_facts WHERE route_fingerprint=? AND capability=?",(route_fingerprint,capability)).fetchone()
            latest = db.execute("SELECT * FROM route_probe_operations WHERE route_fingerprint=? AND capability=? ORDER BY queued_at DESC LIMIT 1", (route_fingerprint, capability)).fetchone()
        # Refresh/restart must show queued/running/interrupted work even before
        # a final result has been written into the durable capability facts.
        return {"last_verified":self._dto(json.loads(row[0])) if row and row[0] else None,
                "latest_attempt":self._dto(latest) if latest else self._dto(json.loads(row[1])) if row and row[1] else None}

    def all_facts(self) -> list[tuple[str, str, dict[str, Any], dict[str, Any]]]:
        with self._lock, self._connect() as db:
            rows = db.execute("SELECT route_fingerprint,capability,last_verified_json,latest_attempt_json FROM route_probe_facts").fetchall()
        return [(str(row[0]), str(row[1]), self._dto(json.loads(row[2])) if row[2] else {}, self._dto(json.loads(row[3])) if row[3] else {}) for row in rows]

    def prune(self, *, retention_seconds: float = 90 * 24 * 60 * 60) -> int:
        with self._lock, self._connect() as db:
            return int(db.execute("DELETE FROM route_probe_operations WHERE finished_at IS NOT NULL AND finished_at<?", (time.time() - max(0.0, retention_seconds),)).rowcount)


class RouteProbeService:
    def __init__(self, store: RouteProbeStore, *, logger: Any = None, route_timeout_seconds: float = 480.0, retention_days: float = 90.0) -> None:
        self.store=store; self._logger=logger; self._route_timeout_seconds=max(1.0,min(480.0,float(route_timeout_seconds))); self._retention_days=max(1.0,min(3650.0,float(retention_days or 90.0))); self._tasks: dict[str,asyncio.Task[Any]]={}; self._lock=asyncio.Lock(); self._run_lock=asyncio.Lock(); self._closed=False

    def prune_history(self) -> int:
        return self.store.prune(retention_seconds=self._retention_days * 24 * 60 * 60)

    def _log(self, phase: str, operation_id: str, code: str = "") -> None:
        from .runtime_events import publish_runtime_event
        publish_runtime_event("route_probe.updated", payload={
            "operation_id": str(operation_id)[:48],
            "status": phase if phase in _SAFE_STATES else "inconclusive",
            "detail_code": _safe_code(code),
        })
        # Never pass exception objects, route URLs, or runner output to logs.
        logger = self._logger
        if logger is not None:
            try:
                logger.info("[route_probe] phase={} operation_id={} code={}", phase, str(operation_id)[:48], _safe_code(code, ""))
            except Exception:
                pass

    async def queue(self, *, route_fingerprint: str, capability: str, runner: Callable[[], Awaitable[ProbeResult]], source: str="manual", probe_version: str="v1", day_key: str="", on_done: Callable[[], None] | None = None) -> dict[str,Any]:
        async with self._lock:
            if self._closed:
                operation = self.store.create(route_fingerprint=route_fingerprint, capability=capability, source=source, probe_version=probe_version, day_key=day_key)
                if operation["status"] == "queued":
                    self.store.cancel(operation["operation_id"])
                    self.store.finish(operation["operation_id"])
                if on_done is not None: on_done()
                return self.store.get(operation["operation_id"]) or operation
            operation=self.store.create(route_fingerprint=route_fingerprint,capability=capability,source=source,probe_version=probe_version,day_key=day_key)
            if operation["status"] == "queued" and operation["operation_id"] not in self._tasks:
                self._log("queued", operation["operation_id"], "probe_queued")
                self._tasks[operation["operation_id"]]=asyncio.create_task(self._run(operation["operation_id"],runner))
                def done(task: asyncio.Task[Any]) -> None:
                    # A task cancelled before its first instruction cannot run
                    # _run's finally block. Persist and clean that case here.
                    operation_id = operation["operation_id"]
                    if task.cancelled():
                        self.store.cancel(operation_id)
                        self.store.finish(operation_id)
                    elif task.exception() is not None:
                        self.store.finish(operation_id, failure_code="probe_internal_failed")
                    self._tasks.pop(operation_id, None)
                    if on_done is not None:
                        on_done()
                self._tasks[operation["operation_id"]].add_done_callback(done)
            elif on_done is not None:
                # Joined requests may own a temporary upload too. That upload
                # is unused by the existing runner and can be released now.
                on_done()
            return operation

    async def _run(self, operation_id: str, runner: Callable[[], Awaitable[ProbeResult]]) -> None:
        acquired = False
        lease_holder = f"{operation_id}:{uuid.uuid4().hex}"
        try:
            async with self._run_lock:  # one provider probe at a time across manual and daily callers
                for _ in range(600):
                    if self.store.acquire_global_lease(lease_holder):
                        acquired = True; break
                    await asyncio.sleep(1)
                if not acquired:
                    self.store.finish(operation_id, ProbeResult(detail_code="probe_lease_unavailable")); return
                if not self.store.begin(operation_id): return
                self._log("running", operation_id, "probe_running")
                lost_lease = asyncio.Event()
                async def heartbeat() -> None:
                    try:
                        while True:
                            await asyncio.sleep(30)
                            current = self.store.get(operation_id)
                            if current is None or current.get("status") == "cancel_requested" or not self.store.renew_global_lease(lease_holder):
                                lost_lease.set()
                                return
                    except asyncio.CancelledError:
                        raise
                heartbeat_task = asyncio.create_task(heartbeat())
                provider_task = lost_task = None
                try:
                    provider_task = asyncio.create_task(runner())
                    lost_task = asyncio.create_task(lost_lease.wait())
                    done, _ = await asyncio.wait({provider_task, lost_task}, timeout=self._route_timeout_seconds, return_when=asyncio.FIRST_COMPLETED)
                    if provider_task not in done:
                        provider_task.cancel(); lost_task.cancel(); await asyncio.gather(provider_task, lost_task, return_exceptions=True)
                        if lost_lease.is_set():
                            self.store.cancel(operation_id); self.store.finish(operation_id)
                        else:
                            self.store.finish(operation_id, ProbeResult(detail_code="probe_timeout"))
                        return
                    lost_task.cancel(); await asyncio.gather(lost_task, return_exceptions=True)
                    result = provider_task.result()
                except asyncio.CancelledError:
                    self.store.cancel(operation_id); self.store.finish(operation_id); raise
                except asyncio.TimeoutError:
                    self.store.finish(operation_id, ProbeResult(detail_code="probe_timeout"))
                except Exception:
                    self.store.finish(operation_id, failure_code="probe_internal_failed")
                    self._log("failed", operation_id, "probe_internal_failed")
                else:
                    finished = self.store.finish(operation_id,result)
                    self._log(str((finished or {}).get("status") or "inconclusive"), operation_id, str((finished or {}).get("detail_code") or ""))
                finally:
                    pending = [task for task in (provider_task, lost_task) if task is not None]
                    for task in pending:
                        if not task.done(): task.cancel()
                    if pending: await asyncio.gather(*pending, return_exceptions=True)
                    heartbeat_task.cancel()
                    await asyncio.gather(heartbeat_task, return_exceptions=True)
        except asyncio.CancelledError:
            self.store.cancel(operation_id); self.store.finish(operation_id)
            raise
        finally:
            if acquired: self.store.release_global_lease(lease_holder)
            self._tasks.pop(operation_id,None)

    async def cancel(self, operation_id: str) -> dict[str,Any] | None:
        operation=self.store.cancel(operation_id); task=self._tasks.get(operation_id)
        if task: task.cancel()
        return operation

    async def wait(self, operation_id: str) -> dict[str, Any] | None:
        task = self._tasks.get(operation_id)
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        return self.store.get(operation_id)

    async def shutdown(self) -> None:
        self._closed = True
        tasks = tuple(self._tasks.values())
        for task in tasks: task.cancel()
        if tasks: await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()


_SERVICES: dict[str, RouteProbeService] = {}

def get_route_probe_service(runtime: Any) -> RouteProbeService:
    """The one process-local service for WebUI, daily jobs and uploads."""
    data_dir = get_data_dir(getattr(runtime, "plugin_config", None)).resolve()
    key = str(data_dir)
    service = _SERVICES.get(key)
    if service is None:
        config = getattr(runtime, "plugin_config", None)
        service = RouteProbeService(RouteProbeStore(data_dir / "route_probe_operations.sqlite3"), logger=getattr(runtime, "logger", None), route_timeout_seconds=getattr(config, "personification_route_probe_route_budget_seconds", 480.0), retention_days=getattr(config, "personification_route_probe_retention_days", 90))
        service.store.recover_interrupted()
        _SERVICES[key] = service
    return service

def restore_route_probe_facts(registry: Any, service: RouteProbeService) -> int:
    """Restore only confirmed facts; inconclusive/auth failures stay latest-attempt UI data."""
    restored = 0
    bindings = {str(item.get("route_fingerprint") or ""): str(item.get("route_name") or "") for item in registry.snapshot()}
    for fingerprint, capability, verified, _latest in service.store.all_facts():
        if not verified or str(verified.get("verification_state") or "") != "verified":
            continue
        name = bindings.get(fingerprint, "")
        key = registry.route_key(name) if name else None
        if key is None:
            continue
        try:
            registry.record(
                key, capability, state=str(verified.get("capability_state") or "unknown"), source="probe",
                detail_code=str(verified.get("detail_code") or "probe_unknown"),
                checked_at=float(verified.get("finished_at") or verified.get("updated_at") or 0) or None,
                verification_state="verified",
            )
            restored += 1
        except Exception:
            continue
    return restored
