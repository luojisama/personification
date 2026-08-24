from __future__ import annotations

import hashlib
import inspect
import ipaddress
import secrets
from typing import Any

from fastapi import APIRouter, Body, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import Response

from ...core import notify, webui_audit_log, webui_auth_store
from ...core.admin_step_up import (
    DEFAULT_ADMIN_STEP_UP_SERVICE,
    STEP_UP_ACTIONS,
    StepUpError,
)
from ...core.backup_artifact_store import DEFAULT_BACKUP_ARTIFACT_STORE
from ...core.whole_plugin_backup import (
    DryRunPlan,
    SECRET_PACKAGE,
    STATE_PACKAGE,
    WholePluginBackupError,
    WholePluginBackupService,
)
from ..deps import (
    AdminIdentity,
    get_client_ip,
    get_cookie_name,
    get_csrf_header_name,
    get_user_agent,
    require_admin,
)


_PLANS: dict[str, DryRunPlan] = {}
_BACKUP_SERVICE = WholePluginBackupService()


async def _maybe_await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


def _safe_error(exc: Exception, *, status_code: int = 400) -> HTTPException:
    if isinstance(exc, (WholePluginBackupError, StepUpError)):
        return HTTPException(status_code=status_code, detail=exc.to_dict())
    return HTTPException(
        status_code=500,
        detail={
            "ok": False,
            "code": "whole_backup_operation_failed",
            "message": "完整备份操作失败，未暴露底层异常内容。",
        },
    )


def _is_https_or_loopback(request: Request) -> bool:
    forwarded = str(request.headers.get("x-forwarded-proto") or "").split(",", 1)[0].strip().lower()
    if request.url.scheme == "https" or forwarded == "https":
        return True
    try:
        return ipaddress.ip_address(get_client_ip(request)).is_loopback
    except ValueError:
        return False


def _require_secret_transport(request: Request) -> None:
    if not _is_https_or_loopback(request):
        raise HTTPException(
            status_code=403,
            detail={
                "ok": False,
                "code": "secret_transport_https_required",
                "message": "远程秘密包操作必须使用 HTTPS；仅本机 loopback 允许开发例外。",
            },
        )


def _require_download_csrf(request: Request) -> None:
    token = request.cookies.get(get_cookie_name(), "")
    record = webui_auth_store.lookup_device(token, ua=get_user_agent(request)) or {}
    expected = str(record.get("csrf_token") or "")
    provided = str(request.headers.get(get_csrf_header_name()) or "")
    if not expected or not provided or not secrets.compare_digest(expected, provided):
        raise HTTPException(status_code=403, detail={"code": "download_csrf_invalid", "message": "下载 CSRF 校验失败。"})


def _audit(action: str, admin: AdminIdentity, request: Request, target: str, outcome: str) -> None:
    try:
        webui_audit_log.record(
            action=action,
            qq=admin.qq,
            ip_hash=hashlib.sha256(get_client_ip(request).encode("utf-8")).hexdigest()[:16],
            device_id=admin.device_id,
            target=str(target or "")[:160],
            outcome=outcome,
        )
    except Exception:
        pass


def _backend(runtime: Any) -> Any:
    backend = getattr(runtime, "whole_plugin_restore_backend", None)
    if backend is None:
        raise HTTPException(
            status_code=503,
            detail={
                "ok": False,
                "code": "whole_backup_restore_backend_unavailable",
                "message": "当前运行时没有挂载完整恢复后端；未执行任何数据写入。",
            },
        )
    return backend


def _artifact(artifact_id: str, admin: AdminIdentity):
    artifact = DEFAULT_BACKUP_ARTIFACT_STORE.get(
        artifact_id,
        owner_qq=admin.qq,
        owner_device_id=admin.device_id,
    )
    if artifact is None:
        raise HTTPException(status_code=404, detail={"code": "backup_artifact_not_found", "message": "备份包不存在、已过期或不属于当前设备。"})
    return artifact


def _consume_step_up(token: str, *, admin: AdminIdentity, request: Request, action: str) -> None:
    DEFAULT_ADMIN_STEP_UP_SERVICE.consume(
        token,
        admin_qq=admin.qq,
        device_id=admin.device_id,
        ip=get_client_ip(request),
        action=action,
    )


def build_whole_backup_router(*, runtime: Any) -> APIRouter:
    router = APIRouter(prefix="/api/v2", tags=["whole-backup-v2"])

    @router.post("/step-up/start")
    async def step_up_start(
        request: Request,
        body: dict[str, Any] = Body(default_factory=dict),
        admin: AdminIdentity = Depends(require_admin),
    ) -> dict[str, Any]:
        action = str(body.get("action") or "")
        if action in {"export_secret", "import_secret"}:
            _require_secret_transport(request)
        try:
            challenge = DEFAULT_ADMIN_STEP_UP_SERVICE.start(
                admin_qq=admin.qq,
                device_id=admin.device_id,
                ip=get_client_ip(request),
                action=action,
            )
            message = (
                "【拟人插件敏感操作二次验证】\n"
                f"操作：{action}\n验证码：{challenge.code}\n"
                "验证码仅对当前管理员、设备、IP 和操作类型有效，5 分钟后失效。"
            )
            if not await notify.send_to_admin(runtime.get_bots, admin.qq, message):
                raise HTTPException(status_code=502, detail={"code": "step_up_delivery_failed", "message": "无法向当前管理员 QQ 发送二次验证码。"})
        except HTTPException:
            raise
        except Exception as exc:
            raise _safe_error(exc) from exc
        _audit("step_up_start", admin, request, action, "ok")
        return {"ok": True, "code": "step_up_code_sent", **challenge.to_public_dict()}

    @router.post("/step-up/verify")
    async def step_up_verify(
        request: Request,
        body: dict[str, Any] = Body(default_factory=dict),
        admin: AdminIdentity = Depends(require_admin),
    ) -> dict[str, Any]:
        action = str(body.get("action") or "")
        if action in {"export_secret", "import_secret"}:
            _require_secret_transport(request)
        try:
            token = DEFAULT_ADMIN_STEP_UP_SERVICE.verify(
                challenge_id=body.get("challenge_id"),
                code=body.get("code"),
                admin_qq=admin.qq,
                device_id=admin.device_id,
                ip=get_client_ip(request),
                action=action,
            )
        except Exception as exc:
            _audit("step_up_verify", admin, request, action, "failed")
            raise _safe_error(exc, status_code=403) from exc
        _audit("step_up_verify", admin, request, action, "ok")
        return {"ok": True, "code": "step_up_verified", "action": action, "token": token, "expires_in": 300}

    @router.post("/backups/export/state")
    async def export_state(
        request: Request,
        body: dict[str, Any] = Body(default_factory=dict),
        admin: AdminIdentity = Depends(require_admin),
    ) -> dict[str, Any]:
        exporter = getattr(runtime, "whole_plugin_export_datasets", None)
        if not callable(exporter):
            raise HTTPException(status_code=503, detail={"code": "whole_backup_state_exporter_unavailable", "message": "当前运行时没有挂载完整状态投影器。"})
        try:
            datasets = await _maybe_await(exporter(body))
            package = _BACKUP_SERVICE.create_state_package(
                source_bot_id=str(body.get("source_bot_id") or "default"),
                datasets=datasets,
            )
            artifact = DEFAULT_BACKUP_ARTIFACT_STORE.put(
                package,
                owner_qq=admin.qq,
                owner_device_id=admin.device_id,
                package_type=STATE_PACKAGE,
                file_name="personification-state.zip",
            )
        except Exception as exc:
            raise _safe_error(exc) from exc
        _audit("whole_backup_state_export", admin, request, artifact.artifact_id, "ok")
        return {"ok": True, "code": "state_backup_ready", **artifact.to_public_dict()}

    @router.post("/backups/export/secret")
    async def export_secret(
        request: Request,
        body: dict[str, Any] = Body(default_factory=dict),
        admin: AdminIdentity = Depends(require_admin),
    ) -> dict[str, Any]:
        _require_secret_transport(request)
        try:
            _consume_step_up(str(body.get("step_up_token") or ""), admin=admin, request=request, action="export_secret")
            exporter = getattr(runtime, "whole_plugin_export_secrets", None)
            if not callable(exporter):
                raise HTTPException(status_code=503, detail={"code": "whole_backup_secret_exporter_unavailable", "message": "当前运行时没有挂载可移植秘密投影器。"})
            secrets_payload = await _maybe_await(exporter(body))
            package = _BACKUP_SERVICE.create_secret_package(
                source_bot_id=str(body.get("source_bot_id") or "default"),
                secrets=secrets_payload,
                passphrase=str(body.get("passphrase") or ""),
            )
            artifact = DEFAULT_BACKUP_ARTIFACT_STORE.put(
                package,
                owner_qq=admin.qq,
                owner_device_id=admin.device_id,
                package_type=SECRET_PACKAGE,
                file_name="personification-secrets.zip",
            )
        except HTTPException:
            raise
        except Exception as exc:
            raise _safe_error(exc) from exc
        _audit("whole_backup_secret_export", admin, request, artifact.artifact_id, "ok")
        return {"ok": True, "code": "secret_backup_ready", **artifact.to_public_dict()}

    @router.post("/backups/inspect")
    async def inspect_upload(
        request: Request,
        expected_type: str = "",
        file: UploadFile = File(...),
        admin: AdminIdentity = Depends(require_admin),
    ) -> dict[str, Any]:
        if expected_type == SECRET_PACKAGE:
            _require_secret_transport(request)
        payload = await file.read(_BACKUP_SERVICE.limits.max_archive_bytes + 1)
        try:
            inspection = _BACKUP_SERVICE.inspect(payload, expected_type=expected_type or None)
            artifact = DEFAULT_BACKUP_ARTIFACT_STORE.put(
                bytes(payload),
                owner_qq=admin.qq,
                owner_device_id=admin.device_id,
                package_type=inspection.package_type,
                file_name=file.filename or "personification-import.zip",
            )
        except Exception as exc:
            raise _safe_error(exc) from exc
        _audit("whole_backup_inspect", admin, request, artifact.artifact_id, "ok")
        return {**inspection.to_dict(), **artifact.to_public_dict()}

    @router.post("/backups/{artifact_id}/dry-run")
    async def dry_run(
        artifact_id: str,
        request: Request,
        body: dict[str, Any] = Body(default_factory=dict),
        admin: AdminIdentity = Depends(require_admin),
    ) -> dict[str, Any]:
        artifact = _artifact(artifact_id, admin)
        try:
            if artifact.package_type == SECRET_PACKAGE:
                _require_secret_transport(request)
                _consume_step_up(str(body.get("step_up_token") or ""), admin=admin, request=request, action="import_secret")
            plan = _BACKUP_SERVICE.dry_run(
                artifact.payload,
                backend=_backend(runtime),
                passphrase=str(body.get("passphrase") or "") if artifact.package_type == SECRET_PACKAGE else None,
            )
            _PLANS[plan.plan_id] = plan
        except HTTPException:
            raise
        except Exception as exc:
            raise _safe_error(exc) from exc
        _audit("whole_backup_dry_run", admin, request, artifact_id, "ok")
        return {"ok": True, "code": "whole_backup_dry_run_ready", **plan.to_dict()}

    @router.post("/backups/{artifact_id}/apply")
    async def apply_restore(
        artifact_id: str,
        request: Request,
        body: dict[str, Any] = Body(default_factory=dict),
        admin: AdminIdentity = Depends(require_admin),
    ) -> dict[str, Any]:
        artifact = _artifact(artifact_id, admin)
        if artifact.package_type == SECRET_PACKAGE:
            _require_secret_transport(request)
        try:
            _consume_step_up(str(body.get("step_up_token") or ""), admin=admin, request=request, action="apply_full_restore")
            plan = _PLANS.get(str(body.get("plan_id") or ""))
            if plan is None:
                raise WholePluginBackupError("restore_plan_not_found", "找不到本进程中未过期的恢复计划")
            result = _BACKUP_SERVICE.apply(
                artifact.payload,
                backend=_backend(runtime),
                plan=plan,
                passphrase=str(body.get("passphrase") or "") if artifact.package_type == SECRET_PACKAGE else None,
            )
        except HTTPException:
            raise
        except Exception as exc:
            _audit("whole_backup_apply", admin, request, artifact_id, "failed")
            raise _safe_error(exc) from exc
        _audit("whole_backup_apply", admin, request, result.journal_id, "ok")
        return {"ok": True, "code": "whole_backup_apply_complete", **result.to_dict()}

    @router.post("/backups/rollback/{journal_id}")
    async def rollback_restore(
        journal_id: str,
        request: Request,
        body: dict[str, Any] = Body(default_factory=dict),
        admin: AdminIdentity = Depends(require_admin),
    ) -> dict[str, Any]:
        try:
            _consume_step_up(str(body.get("step_up_token") or ""), admin=admin, request=request, action="apply_full_restore")
            result = _BACKUP_SERVICE.rollback(journal_id, backend=_backend(runtime))
        except HTTPException:
            raise
        except Exception as exc:
            raise _safe_error(exc) from exc
        _audit("whole_backup_rollback", admin, request, journal_id, "ok")
        return {"ok": True, "code": "whole_backup_rollback_complete", **result.to_dict()}

    @router.get("/backups/download/{artifact_id}")
    async def download(
        artifact_id: str,
        request: Request,
        admin: AdminIdentity = Depends(require_admin),
    ) -> Response:
        _require_download_csrf(request)
        artifact = _artifact(artifact_id, admin)
        if artifact.package_type == SECRET_PACKAGE:
            _require_secret_transport(request)
        _audit("whole_backup_download", admin, request, artifact_id, "ok")
        return Response(
            artifact.payload,
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="{artifact.file_name}"',
                "Cache-Control": "no-store, max-age=0",
            },
        )

    return router


__all__ = ["build_whole_backup_router"]
