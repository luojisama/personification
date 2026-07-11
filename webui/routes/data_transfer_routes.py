from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from ...core import webui_audit_log
from ...core.data_transfer.service import DataTransferError, DataTransferService
from ..deps import AdminIdentity, require_admin
from ..schemas import DataExportRequest, DataImportApplyRequest, DataImportPlanRequest


def _service(runtime) -> DataTransferService:
    cfg = getattr(runtime, "plugin_config", None)
    from ...core.paths import get_data_dir
    from ...core.db import resolve_db_path
    data_dir = get_data_dir(cfg)
    bundle = getattr(runtime, "runtime_bundle", None)
    return DataTransferService(
        data_dir=data_dir / "data_transfer",
        db_path=resolve_db_path(cfg),
        memory_store=getattr(bundle, "memory_store", None),
    )


def _audit(action: str, admin: AdminIdentity, target: str, detail: dict, outcome: str = "ok") -> None:
    webui_audit_log.record(action=action, qq=admin.qq, device_id=admin.device_id, target=target, detail=detail, outcome=outcome)


def _connected_bot_ids(runtime) -> set[str]:
    try:
        bots = runtime.get_bots() if callable(getattr(runtime, "get_bots", None)) else {}
    except Exception:
        bots = {}
    values = bots.values() if isinstance(bots, dict) else []
    return {str(getattr(bot, "self_id", "") or "").strip() for bot in values if str(getattr(bot, "self_id", "") or "").strip()}


def _require_connected_bot(runtime, bot_id: str) -> None:
    if str(bot_id or "").strip() not in _connected_bot_ids(runtime):
        raise HTTPException(status_code=400, detail="目标 Bot 当前未连接，无法确认数据作用域")


async def _require_group_membership(runtime, bot_id: str, group_id: str) -> None:
    _require_connected_bot(runtime, bot_id)
    bots = runtime.get_bots() if callable(getattr(runtime, "get_bots", None)) else {}
    bot = next((value for value in bots.values() if str(getattr(value, "self_id", "")) == str(bot_id)), None)
    if bot is None or not callable(getattr(bot, "get_group_list", None)):
        raise HTTPException(status_code=400, detail="无法检查目标 Bot 的群成员关系")
    try:
        groups = await bot.get_group_list()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="目标群成员关系检查失败") from exc
    if not any(str(item.get("group_id", "")) == str(group_id) for item in (groups or []) if isinstance(item, dict)):
        raise HTTPException(status_code=400, detail="目标 Bot 不在指定群中")


def build_data_transfer_router(*, runtime) -> APIRouter:
    router = APIRouter(prefix="/api/data-transfer", tags=["data-transfer"])

    @router.post("/exports/create")
    async def create_export(body: DataExportRequest, admin: AdminIdentity = Depends(require_admin)) -> dict:
        await _require_group_membership(runtime, body.bot_id, body.group_id)
        try:
            result = await asyncio.to_thread(_service(runtime).create_export, bot_id=body.bot_id, group_id=body.group_id, datasets=body.datasets)
        except DataTransferError as exc:
            _audit("data_export_create", admin, body.group_id, {}, "denied")
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        _audit("data_export_create", admin, body.group_id, {"task_id": result["task_id"], "datasets": body.datasets or []})
        return result

    @router.get("/exports/{task_id}/status")
    async def export_status(task_id: str, _: AdminIdentity = Depends(require_admin)) -> dict:
        try:
            return _service(runtime).task_status(task_id)
        except DataTransferError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get("/exports/{task_id}/download")
    async def download_export(task_id: str, admin: AdminIdentity = Depends(require_admin)):
        try:
            path = _service(runtime).export_path(task_id)
        except DataTransferError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        _audit("data_export_download", admin, task_id, {})
        return FileResponse(path, media_type="application/zip", filename=path.name)

    @router.post("/imports/upload")
    async def upload_import(file: UploadFile = File(...), admin: AdminIdentity = Depends(require_admin)) -> dict:
        try:
            result = await asyncio.to_thread(_service(runtime).store_upload, file.file, filename=file.filename or "package.zip")
        except DataTransferError as exc:
            _audit("data_import_upload", admin, "", {}, "denied")
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        _audit("data_import_upload", admin, result["task_id"], {"size": result["metadata"].get("size", 0)})
        return result

    @router.get("/imports/{task_id}/inspect")
    async def inspect_import(task_id: str, _: AdminIdentity = Depends(require_admin)) -> dict:
        try:
            return await asyncio.to_thread(_service(runtime).inspect, task_id)
        except (DataTransferError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/imports/{task_id}/dry-run")
    async def dry_run(task_id: str, body: DataImportPlanRequest, admin: AdminIdentity = Depends(require_admin)) -> dict:
        await _require_group_membership(runtime, body.target_bot_id, body.target_group_id)
        try:
            result = await asyncio.to_thread(_service(runtime).dry_run, task_id, target_bot_id=body.target_bot_id, target_group_id=body.target_group_id, mode=body.mode, allow_same_identity=body.allow_same_identity)
        except DataTransferError as exc:
            _audit("data_import_dry_run", admin, task_id, {"mode": body.mode}, "denied")
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        _audit("data_import_dry_run", admin, task_id, {"mode": body.mode})
        return result

    @router.post("/imports/{task_id}/apply")
    async def apply_import(task_id: str, body: DataImportApplyRequest, admin: AdminIdentity = Depends(require_admin)) -> dict:
        await _require_group_membership(runtime, body.target_bot_id, body.target_group_id)
        try:
            result = await asyncio.to_thread(_service(runtime).apply, task_id, target_bot_id=body.target_bot_id, target_group_id=body.target_group_id, mode=body.mode, allow_same_identity=body.allow_same_identity, plan_token=body.plan_token)
        except DataTransferError as exc:
            _audit("data_import_apply", admin, task_id, {"mode": body.mode}, "denied")
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        _audit("data_import_apply", admin, task_id, {"mode": body.mode, "journal_id": result["journal_id"]})
        return result

    @router.post("/targets/check")
    async def check_target(body: DataImportPlanRequest, _: AdminIdentity = Depends(require_admin)) -> dict:
        target = _service(runtime).validate_target_scope(target_bot_id=body.target_bot_id, target_group_id=body.target_group_id)
        await _require_group_membership(runtime, target["bot_id"], target["group_id"])
        return {"valid": True, "target": target}

    @router.post("/imports/{journal_id}/rollback")
    async def rollback_import(journal_id: str, admin: AdminIdentity = Depends(require_admin)) -> dict:
        try:
            result = await asyncio.to_thread(_service(runtime).rollback, journal_id)
        except DataTransferError as exc:
            _audit("data_import_rollback", admin, journal_id, {}, "denied")
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        _audit("data_import_rollback", admin, journal_id, {"idempotent": result["idempotent"]})
        return result

    return router
