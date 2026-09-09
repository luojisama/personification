from __future__ import annotations

"""Durable, deliberately small visual-relabel job controller."""

import asyncio
import time
import uuid
from pathlib import Path
from typing import Any

from .data_store import get_data_store

_NAMESPACE = "sticker_visual_relabel_jobs_v1"
_TASKS: dict[str, asyncio.Task[None]] = {}
_ACTIVE_JOB_ID = ""
_WAITING: dict[str, dict[str, Any]] = {}


def _load() -> dict[str, Any]:
    value = get_data_store().load_sync(_NAMESPACE)
    return value if isinstance(value, dict) else {"jobs": {}}


def _mutate(change):  # noqa: ANN001
    return get_data_store().mutate_sync(_NAMESPACE, change)


def get_job(job_id: str) -> dict[str, Any] | None:
    raw = (_load().get("jobs") or {}).get(str(job_id))
    return dict(raw) if isinstance(raw, dict) else None


def create_job(*, batch_size: int) -> dict[str, Any]:
    job = {"job_id": uuid.uuid4().hex, "status": "queued", "batch_size": max(1, min(int(batch_size), 20)), "processed": 0, "failed": 0, "remaining": None, "created_at": time.time(), "updated_at": time.time(), "last_error": ""}
    def _change(current):
        payload = current if isinstance(current, dict) else {}
        jobs = payload.setdefault("jobs", {})
        jobs[job["job_id"]] = job
        return payload
    _mutate(_change)
    return job


def pause(job_id: str) -> dict[str, Any] | None:
    def _change(current):
        jobs = current.setdefault("jobs", {}) if isinstance(current, dict) else {}
        job = jobs.get(job_id)
        if isinstance(job, dict) and job.get("status") in {"queued", "running"}:
            job["status"] = "paused"; job["updated_at"] = time.time()
        return current
    _mutate(_change); return get_job(job_id)


def resume(job_id: str) -> dict[str, Any] | None:
    def _change(current):
        jobs = current.setdefault("jobs", {}) if isinstance(current, dict) else {}
        job = jobs.get(job_id)
        if isinstance(job, dict) and job.get("status") in {"paused", "queued", "failed"}:
            job["status"] = "queued"; job["updated_at"] = time.time()
        return current
    _mutate(_change); return get_job(job_id)


def fail(job_id: str, reason: str) -> dict[str, Any] | None:
    def _change(current):
        job = (current.setdefault("jobs", {}) if isinstance(current, dict) else {}).get(job_id)
        if isinstance(job, dict): job.update(status="failed", last_error=str(reason)[:80], updated_at=time.time())
        return current
    _mutate(_change); return get_job(job_id)


async def run_next(*, job_id: str, sticker_dir: Path, runtime: Any, logger: Any, concurrency: int) -> None:
    job = get_job(job_id)
    if not job or job.get("status") not in {"queued", "running"}:
        return
    def _running(current):
        current["jobs"][job_id].update(status="running", updated_at=time.time()); return current
    _mutate(_running)
    try:
        from ..skills.skillpacks.sticker_labeler.scripts.impl import StickerLabeler
        labeler = StickerLabeler(sticker_dir, logger=logger, concurrency=concurrency)
        result = await labeler.relabel(getattr(runtime, "vision_caller", None), only_needs_visual_relabel=True, max_items=int(job["batch_size"]))
        def _done(current):
            row = current["jobs"][job_id]
            row["processed"] = int(row.get("processed", 0)) + int(result.get("total", 0))
            row["failed"] = int(row.get("failed", 0)) + int(result.get("failed", 0))
            row["remaining"] = int(result.get("remaining", 0)); row["updated_at"] = time.time()
            # A concurrent pause is authoritative; never overwrite it.
            if row.get("status") == "running":
                # Failed source files stay marked for visual review. Retrying
                # automatically would call the API forever on the same input.
                if int(result.get("failed", 0)) > 0:
                    row["status"] = "failed"; row["last_error"] = "batch_item_failed"
                else:
                    row["status"] = "completed" if not row["remaining"] else "queued"
            return current
        _mutate(_done)
        # A completed bounded batch schedules exactly one following batch. A
        # pause racing this completion wins because it changes the persisted
        # status before this fresh read.
        if (get_job(job_id) or {}).get("status") == "queued":
            _schedule_after_current(job_id, sticker_dir, runtime, logger, concurrency)
    except Exception as exc:
        def _failed(current):
            row = current["jobs"][job_id]; row.update(status="failed", last_error=type(exc).__name__, updated_at=time.time()); return current
        _mutate(_failed)


def schedule_next(**kwargs: Any) -> None:
    global _ACTIVE_JOB_ID
    job_id = str(kwargs["job_id"])
    # relabel() discovers its own candidate set, so concurrent jobs could
    # select the same file. Serialize all visual jobs, not merely same job id.
    if _ACTIVE_JOB_ID and _ACTIVE_JOB_ID != job_id:
        _WAITING[job_id] = dict(kwargs)
        return
    existing = _TASKS.get(job_id)
    if existing is not None and not existing.done():
        # This can occur when an administrator explicitly resumes a failed
        # batch in the tiny interval after run_next persisted ``failed`` but
        # before its task's release callback ran.  It is an explicit retry,
        # not an automatic one: wait for that task to release the global slot
        # and then schedule exactly one new attempt.
        if (get_job(job_id) or {}).get("status") == "queued":
            existing.add_done_callback(lambda _task: schedule_next(**kwargs))
        return
    _ACTIVE_JOB_ID = job_id
    task = asyncio.create_task(run_next(**kwargs)); _TASKS[job_id] = task
    def _release(_task: asyncio.Task[None]) -> None:
        global _ACTIVE_JOB_ID
        if _ACTIVE_JOB_ID == job_id: _ACTIVE_JOB_ID = ""
        # Drain one persisted-eligible waiting job in insertion order. Failed
        # jobs are never implicitly inserted here, so a failure still needs an
        # administrator's explicit resume.
        for waiting_id, waiting in tuple(_WAITING.items()):
            _WAITING.pop(waiting_id, None)
            if (get_job(waiting_id) or {}).get("status") == "queued":
                schedule_next(**waiting)
                break
    task.add_done_callback(_release)


def _schedule_after_current(job_id: str, sticker_dir: Path, runtime: Any, logger: Any, concurrency: int) -> None:
    current = asyncio.current_task()
    if current is None:
        schedule_next(job_id=job_id, sticker_dir=sticker_dir, runtime=runtime, logger=logger, concurrency=concurrency); return
    current.add_done_callback(lambda _task: schedule_next(job_id=job_id, sticker_dir=sticker_dir, runtime=runtime, logger=logger, concurrency=concurrency))


def resume_pending(*, sticker_dir: Path, runtime: Any, logger: Any, concurrency: int) -> int:
    jobs = _load().get("jobs", {})
    selected = [str(job_id) for job_id, job in (jobs.items() if isinstance(jobs, dict) else []) if isinstance(job, dict) and job.get("status") in {"queued", "running"}]
    for job_id in selected:
        if (get_job(job_id) or {}).get("status") == "running": resume(job_id)
        schedule_next(job_id=job_id, sticker_dir=sticker_dir, runtime=runtime, logger=logger, concurrency=concurrency)
    return len(selected)
