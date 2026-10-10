from __future__ import annotations

from pathlib import Path
from contextlib import asynccontextmanager
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from ...core.paths import get_data_dir
from ...core import webui_audit_log
from ...core.quality_lab import QualityLab, QualityLabError
from ..deps import AdminIdentity, require_admin


def invalid_quality_request() -> HTTPException:
    return HTTPException(status_code=400, detail={"code": "quality_request_invalid", "message": "操作未完成，请检查已脱敏案例、所选模型、请求预算及当前作业状态。"})


def build_quality_router(*, runtime: Any, service: QualityLab | None = None) -> APIRouter:
    lab = service

    @asynccontextmanager
    async def lifespan(_app):
        try:
            yield
        finally:
            if lab is not None:
                await lab.close()

    router = APIRouter(prefix="/api/v2/quality", tags=["quality"], lifespan=lifespan)

    def get_lab() -> QualityLab:
        nonlocal lab
        if lab is None:
            lab = QualityLab(Path(get_data_dir(runtime.plugin_config)) / "quality_lab")
        return lab

    def checked(fn, *args):
        try:
            return fn(*args)
        except QualityLabError:
            raise invalid_quality_request() from None

    def audit(identity: AdminIdentity, action: str, item_id: str):
        webui_audit_log.record(action="quality." + action, qq=str(getattr(identity, "qq", "")), device_id=str(getattr(identity, "device_id", "")), target=item_id)

    @router.get("")
    async def index(_: AdminIdentity = Depends(require_admin)):
        return checked(get_lab().snapshot, runtime.plugin_config)

    @router.post("/cases")
    async def add_case(payload: dict[str, Any], _: AdminIdentity = Depends(require_admin)):
        result = checked(get_lab().add_case, payload)
        audit(_, "case.create", result["id"])
        return result

    @router.post("/preview")
    async def preview(payload: dict[str, Any], _: AdminIdentity = Depends(require_admin)):
        return checked(get_lab().preview, payload, runtime.plugin_config)

    @router.post("/jobs")
    async def start(payload: dict[str, Any], _: AdminIdentity = Depends(require_admin)):
        try:
            result = await get_lab().start(payload, runtime.plugin_config)
            audit(_, "job.start", result["id"])
            return result
        except QualityLabError:
            raise invalid_quality_request() from None

    @router.get("/jobs/{job_id}")
    async def job(job_id: str, _: AdminIdentity = Depends(require_admin)):
        return checked(get_lab().job, job_id)

    @router.post("/jobs/{job_id}/scores")
    async def score(job_id: str, payload: dict[str, Any], _: AdminIdentity = Depends(require_admin)):
        result = checked(get_lab().score, job_id, payload)
        audit(_, "job.score", job_id)
        return result

    @router.get("/jobs/{job_id}/report")
    async def report(job_id: str, _: AdminIdentity = Depends(require_admin)):
        return checked(get_lab().report, job_id)

    @router.post("/jobs/{job_id}/cancel")
    async def cancel(job_id: str, _: AdminIdentity = Depends(require_admin)):
        try:
            result = await get_lab().cancel(job_id)
            audit(_, "job.cancel", job_id)
            return result
        except QualityLabError:
            raise invalid_quality_request() from None

    return router
