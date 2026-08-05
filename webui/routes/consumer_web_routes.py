from __future__ import annotations

import base64
import json
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, Response

from ...core import webui_audit_log
from ...core.gemini_web_service import get_gemini_web_service
from ..deps import AdminIdentity, get_client_ip, require_admin


_KNOWN_CODES = {
    "gemini_web_disabled",
    "gemini_web_risk_ack_required",
    "gemini_web_login_required",
    "gemini_web_manual_verification_required",
    "gemini_web_network_risk_detected",
    "gemini_web_network_risk_cooldown",
    "gemini_web_busy",
    "gemini_web_dom_changed",
    "gemini_web_upload_rejected",
    "gemini_web_generation_timeout",
    "gemini_web_output_empty",
    "gemini_web_process_failed",
    "gemini_web_context_idle_evicted",
    "gemini_web_media_too_large",
    "gemini_web_request_invalid",
    "gemini_web_local_rate_limited",
}


def _private(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store, private"
    response.headers["Pragma"] = "no-cache"


def _owner(admin: AdminIdentity) -> str:
    return f"{admin.qq}:{admin.device_id}:gemini_web"


def _safe_error(exc: Exception, title: str) -> HTTPException:
    raw = str(exc or "").strip()
    code = raw if raw in _KNOWN_CODES else "gemini_web_operation_failed"
    status = (
        409
        if code in {"gemini_web_busy", "gemini_web_network_risk_cooldown", "gemini_web_local_rate_limited"}
        else 400
        if code in {"gemini_web_risk_ack_required", "gemini_web_request_invalid"}
        else 503
    )
    messages = {
        "gemini_web_risk_ack_required": "启用或操作Gemini Web 前必须先确认第三方上传与消费者网页自动化风险。",
        "gemini_web_disabled": "Gemini Web 当前未启用，请先保存启用与风险确认配置。",
        "gemini_web_busy": "Gemini Web 当前已有分析任务，请等待本次任务结束。",
        "gemini_web_network_risk_detected": "页面提示网络或账号安全风险，自动操作已立即停止并进入冷却。",
        "gemini_web_network_risk_cooldown": "网络风险冷却尚未结束；请等待冷却或改用正式 API/分镜。",
        "gemini_web_local_rate_limited": "Gemini Web 本地调用频率已达到安全上限，本次将改用其他媒体路径。",
        "gemini_web_login_required": "Gemini登录态不可用，请由管理员打开人工登录。",
        "gemini_web_manual_verification_required": "官方页面要求人工验证，自动操作已停止。",
        "gemini_web_dom_changed": "Gemini页面结构与当前适配器不匹配，已停止自动操作。",
    }
    return HTTPException(
        status_code=status,
        detail={
            "ok": False,
            "code": code,
            "title": title,
            "message": messages.get(code, "Gemini Web 操作未完成；请查看脱敏诊断后改用人工接管或其他媒体路径。"),
            "error_type": type(exc).__name__,
        },
    )


def build_consumer_web_router(*, runtime: Any) -> APIRouter:
    router = APIRouter(prefix="/api/media/web/gemini", tags=["consumer-media-web"])
    service = get_gemini_web_service(runtime)

    @router.get("/status")
    async def status(
        response: Response,
        refresh: bool = Query(default=False),
        _: AdminIdentity = Depends(require_admin),
    ) -> dict[str, Any]:
        _private(response)
        return await service.status(runtime.plugin_config, refresh=refresh)

    @router.post("/probe")
    async def probe(
        request: Request,
        response: Response,
        admin: AdminIdentity = Depends(require_admin),
    ) -> dict[str, Any]:
        _private(response)
        try:
            result = await service.probe(runtime.plugin_config)
        except Exception as exc:
            raise _safe_error(exc, "Gemini页面兼容性检查未完成") from exc
        webui_audit_log.record(
            action="gemini_web_probe",
            qq=admin.qq,
            device_id=admin.device_id,
            target="gemini_web_v1",
            ip_hash=get_client_ip(request),
            detail={"state": str(result.get("state") or "unknown")},
            outcome="success" if result.get("state") == "ready" else "partial",
        )
        return result

    @router.post("/auth/start")
    async def auth_start(
        request: Request,
        response: Response,
        admin: AdminIdentity = Depends(require_admin),
    ) -> dict[str, Any]:
        _private(response)
        try:
            result = await service.auth_start(runtime.plugin_config, _owner(admin))
        except Exception as exc:
            raise _safe_error(exc, "Gemini人工登录会话创建失败") from exc
        webui_audit_log.record(
            action="gemini_web_auth_start",
            qq=admin.qq,
            device_id=admin.device_id,
            target="gemini_web",
            ip_hash=get_client_ip(request),
            detail={"page_contract_version": "gemini_web_v1"},
        )
        return result

    @router.get("/auth/{session_id}")
    async def auth_status(
        session_id: str,
        response: Response,
        admin: AdminIdentity = Depends(require_admin),
    ) -> dict[str, Any]:
        _private(response)
        try:
            return await service.auth_status(runtime.plugin_config, session_id, _owner(admin))
        except Exception as exc:
            raise _safe_error(exc, "Gemini人工登录状态读取失败") from exc

    @router.get("/auth/{session_id}/frame")
    async def auth_frame(
        session_id: str,
        revision: int = Query(default=0, ge=0, le=2_147_483_647),
        admin: AdminIdentity = Depends(require_admin),
    ) -> Response:
        try:
            result = await service.auth_frame(
                runtime.plugin_config,
                session_id,
                _owner(admin),
                after_revision=revision,
            )
            frame_revision = max(0, int(result.get("interactive_frame_revision") or 0))
            headers = {
                "Cache-Control": "no-store, private",
                "Pragma": "no-cache",
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": "default-src 'none'",
                "X-Interactive-Revision": str(frame_revision),
                "X-Interactive-Stale": "1" if result.get("stale") else "0",
            }
            if not bool(result.get("changed", bool(result.get("data_base64")))):
                return Response(status_code=204, headers=headers)
            image = base64.b64decode(str(result.get("data_base64") or ""), validate=True)
            mime_type = str(result.get("mime_type") or "")
            if mime_type not in {"image/jpeg", "image/png"} or not image or len(image) > 2 * 1024 * 1024:
                raise ValueError("gemini_web_process_failed")
            return Response(content=image, media_type=mime_type, headers=headers)
        except Exception as exc:
            raise _safe_error(exc, "Gemini人工接管画面读取失败") from exc

    @router.post("/auth/{session_id}/input")
    async def auth_input(
        session_id: str,
        response: Response,
        body: dict[str, Any] = Body(default_factory=dict),
        admin: AdminIdentity = Depends(require_admin),
    ) -> dict[str, Any]:
        _private(response)
        action = body.get("action")
        if not isinstance(action, dict) or len(
            json.dumps(action, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        ) > 16 * 1024:
            raise _safe_error(ValueError("gemini_web_request_invalid"), "Gemini人工接管操作无效")
        try:
            return await service.auth_input(
                runtime.plugin_config,
                session_id,
                _owner(admin),
                action,
            )
        except Exception as exc:
            raise _safe_error(exc, "Gemini人工接管操作未完成") from exc

    @router.post("/auth/{session_id}/finish")
    async def auth_finish(
        session_id: str,
        request: Request,
        response: Response,
        admin: AdminIdentity = Depends(require_admin),
    ) -> dict[str, Any]:
        _private(response)
        try:
            result = await service.auth_finish(runtime.plugin_config, session_id, _owner(admin))
        except Exception as exc:
            raise _safe_error(exc, "Gemini登录状态确认失败") from exc
        webui_audit_log.record(
            action="gemini_web_auth_finish",
            qq=admin.qq,
            device_id=admin.device_id,
            target="gemini_web",
            ip_hash=get_client_ip(request),
            detail={"status": str(result.get("status") or "unknown")},
            outcome="success" if result.get("status") == "success" else "partial",
        )
        return result

    @router.post("/auth/{session_id}/cancel")
    async def auth_cancel(
        session_id: str,
        response: Response,
        admin: AdminIdentity = Depends(require_admin),
    ) -> dict[str, Any]:
        _private(response)
        try:
            return await service.auth_cancel(runtime.plugin_config, session_id, _owner(admin))
        except Exception as exc:
            raise _safe_error(exc, "Gemini人工登录会话取消失败") from exc

    @router.post("/logout")
    async def logout(
        request: Request,
        response: Response,
        body: dict[str, Any] = Body(default_factory=dict),
        admin: AdminIdentity = Depends(require_admin),
    ) -> dict[str, Any]:
        _private(response)
        if str(body.get("confirm") or "") != "确认注销GeminiWeb":
            raise _safe_error(ValueError("gemini_web_request_invalid"), "请输入精确确认文本：确认注销GeminiWeb")
        try:
            result = await service.logout(runtime.plugin_config)
        except Exception as exc:
            raise _safe_error(exc, "Gemini本地登录 profile 注销失败") from exc
        webui_audit_log.record(
            action="gemini_web_logout",
            qq=admin.qq,
            device_id=admin.device_id,
            target="gemini_web",
            ip_hash=get_client_ip(request),
            detail={"profile_deleted": True},
        )
        return result

    return router


__all__ = ["build_consumer_web_router"]
