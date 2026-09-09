from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from ._loader import load_personification_module


jobs = load_personification_module("plugin.personification.core.sticker_relabel_jobs")


class _Store:
    def __init__(self) -> None: self.value = {}
    def load_sync(self, _name): return self.value
    def mutate_sync(self, _name, mutator): self.value = mutator(self.value); return self.value


def test_visual_relabel_job_persists_pause_and_resume(monkeypatch) -> None:  # noqa: ANN001
    store = _Store()
    monkeypatch.setattr(jobs, "get_data_store", lambda: store)
    job = jobs.create_job(batch_size=999)
    assert job["batch_size"] == 20
    assert jobs.pause(job["job_id"])["status"] == "paused"
    assert jobs.resume(job["job_id"])["status"] == "queued"
    assert jobs.fail(job["job_id"], "vision_missing")["last_error"] == "vision_missing"
    assert jobs.get_job(job["job_id"])["status"] == "failed"


def test_running_pause_resume_keeps_pause_and_schedules_next(monkeypatch, tmp_path) -> None:  # noqa: ANN001
    store = _Store(); monkeypatch.setattr(jobs, "get_data_store", lambda: store)
    started, release = asyncio.Event(), asyncio.Event()
    impl = load_personification_module("plugin.personification.skills.skillpacks.sticker_labeler.scripts.impl")
    async def _fake_relabel(self, *_a, **_kw):  # noqa: ANN001
        started.set(); await release.wait(); return {"total": 1, "remaining": 1}
    monkeypatch.setattr(impl.StickerLabeler, "relabel", _fake_relabel)
    async def _run():
        job = jobs.create_job(batch_size=1)
        jobs.schedule_next(job_id=job["job_id"], sticker_dir=Path(tmp_path), runtime=SimpleNamespace(vision_caller=object()), logger=SimpleNamespace(), concurrency=1)
        await asyncio.wait_for(started.wait(), 1)
        assert jobs.pause(job["job_id"])["status"] == "paused"
        release.set(); await asyncio.sleep(0.05)
        assert jobs.get_job(job["job_id"])["status"] == "paused"
        jobs.resume(job["job_id"])
        jobs.schedule_next(job_id=job["job_id"], sticker_dir=Path(tmp_path), runtime=SimpleNamespace(vision_caller=object()), logger=SimpleNamespace(), concurrency=1)
        await asyncio.sleep(0.05)
        assert jobs.get_job(job["job_id"])["processed"] >= 1
    asyncio.run(_run())


def test_failed_batch_stops_automatic_retry(monkeypatch, tmp_path) -> None:  # noqa: ANN001
    store = _Store(); monkeypatch.setattr(jobs, "get_data_store", lambda: store)
    impl = load_personification_module("plugin.personification.skills.skillpacks.sticker_labeler.scripts.impl")
    calls = 0
    async def _failed_relabel(self, *_a, **_kw):  # noqa: ANN001
        nonlocal calls; calls += 1; return {"total": 1, "failed": 1, "remaining": 1}
    monkeypatch.setattr(impl.StickerLabeler, "relabel", _failed_relabel)
    async def _run():
        job = jobs.create_job(batch_size=1)
        jobs.schedule_next(job_id=job["job_id"], sticker_dir=Path(tmp_path), runtime=SimpleNamespace(vision_caller=object()), logger=SimpleNamespace(), concurrency=1)
        await asyncio.sleep(0.1)
        saved = jobs.get_job(job["job_id"])
        assert saved["status"] == "failed" and saved["failed"] == 1 and calls == 1
    asyncio.run(_run())


def test_two_persisted_jobs_drain_serially(monkeypatch, tmp_path) -> None:  # noqa: ANN001
    store = _Store(); monkeypatch.setattr(jobs, "get_data_store", lambda: store)
    impl = load_personification_module("plugin.personification.skills.skillpacks.sticker_labeler.scripts.impl")
    calls = 0
    async def _relabel(self, *_a, **_kw):  # noqa: ANN001
        nonlocal calls; calls += 1; await asyncio.sleep(0.02); return {"total": 1, "failed": 0, "remaining": 0}
    monkeypatch.setattr(impl.StickerLabeler, "relabel", _relabel)
    async def _run():
        first, second = jobs.create_job(batch_size=1), jobs.create_job(batch_size=1)
        common = {"sticker_dir": Path(tmp_path), "runtime": SimpleNamespace(vision_caller=object()), "logger": SimpleNamespace(), "concurrency": 1}
        jobs.schedule_next(job_id=first["job_id"], **common); jobs.schedule_next(job_id=second["job_id"], **common)
        await asyncio.sleep(0.15)
        assert calls == 2
        assert jobs.get_job(first["job_id"])["status"] == "completed"
        assert jobs.get_job(second["job_id"])["status"] == "completed"
    asyncio.run(_run())


def test_resume_pending_drains_every_persisted_job(monkeypatch, tmp_path) -> None:  # noqa: ANN001
    """A restart must not leave jobs after the first one queued forever."""
    store = _Store(); monkeypatch.setattr(jobs, "get_data_store", lambda: store)
    impl = load_personification_module("plugin.personification.skills.skillpacks.sticker_labeler.scripts.impl")
    calls = 0
    async def _relabel(self, *_a, **_kw):  # noqa: ANN001
        nonlocal calls; calls += 1; await asyncio.sleep(0.01); return {"total": 1, "failed": 0, "remaining": 0}
    monkeypatch.setattr(impl.StickerLabeler, "relabel", _relabel)
    async def _run():
        first, second = jobs.create_job(batch_size=1), jobs.create_job(batch_size=1)
        count = jobs.resume_pending(sticker_dir=Path(tmp_path), runtime=SimpleNamespace(vision_caller=object()), logger=SimpleNamespace(), concurrency=1)
        assert count == 2
        await asyncio.sleep(0.15)
        assert calls == 2
        assert jobs.get_job(first["job_id"])["status"] == "completed"
        assert jobs.get_job(second["job_id"])["status"] == "completed"
    asyncio.run(_run())
