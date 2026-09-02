from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request

from ...core import webui_audit_log
from ...core.qzone_agent_interaction import (
    get_group_qzone_agent_settings,
    set_group_qzone_agent_settings,
)
from ...core.qzone_social_operations import QzoneSocialOperationCoordinator
from ..deps import AdminIdentity, get_client_ip, require_admin


def _bot_id(runtime: Any) -> str:
    try:
        bots = runtime.get_bots()
    except Exception:
        bots = {}
    return str(next(iter(bots), "") or "") if isinstance(bots, dict) else ""


def build_qzone_agent_group_router(*, runtime: Any) -> APIRouter:
    router = APIRouter()

    @router.get("/{group_id}/qzone-agent")
    async def get_qzone_agent_state(
        group_id: str,
        _: AdminIdentity = Depends(require_admin),
    ) -> dict[str, Any]:
        settings = get_group_qzone_agent_settings(group_id)
        bot_id = _bot_id(runtime)
        operations = {"period_day": "", "count": 0, "operations": []}
        if bot_id:
            try:
                operations = QzoneSocialOperationCoordinator(
                    timezone_name=str(
                        getattr(runtime.plugin_config, "personification_timezone", "Asia/Shanghai")
                        or "Asia/Shanghai"
                    )
                ).snapshot(bot_id=bot_id, group_id=group_id)
            except Exception:
                pass
        return {
            "group_id": str(group_id),
            "global_enabled": bool(
                getattr(runtime.plugin_config, "personification_agent_qzone_interaction_enabled", False)
            ),
            "qzone_enabled": bool(getattr(runtime.plugin_config, "personification_qzone_enabled", False)),
            "settings": settings,
            "quota": {
                "used_today": int(operations.get("count", 0) or 0),
                "group_daily_limit": settings["group_daily_limit"],
                "target_daily_limit": settings["target_daily_limit"],
            },
            "recent_operations": list(operations.get("operations", []) or []),
        }

    @router.put("/{group_id}/qzone-agent")
    async def update_qzone_agent_state(
        group_id: str,
        request: Request,
        payload: dict[str, Any] = Body(default_factory=dict),
        admin: AdminIdentity = Depends(require_admin),
    ) -> dict[str, Any]:
        allowed = {
            "enabled",
            "group_daily_limit",
            "target_daily_limit",
            "target_cooldown_seconds",
        }
        unknown = sorted(set(payload) - allowed)
        if unknown:
            raise HTTPException(status_code=400, detail={"code": "qzone_agent_unknown_fields", "fields": unknown})
        global_group_limit = max(
            0,
            int(getattr(runtime.plugin_config, "personification_agent_qzone_group_daily_limit", 3) or 0),
        )
        global_target_limit = max(
            0,
            int(getattr(runtime.plugin_config, "personification_agent_qzone_target_daily_limit", 1) or 0),
        )
        global_cooldown = max(
            0.0,
            float(
                getattr(runtime.plugin_config, "personification_agent_qzone_target_cooldown_seconds", 1800.0)
                or 0.0
            ),
        )
        if int(payload.get("group_daily_limit", global_group_limit)) > global_group_limit:
            raise HTTPException(status_code=400, detail={"code": "qzone_agent_group_limit_exceeds_global"})
        if int(payload.get("target_daily_limit", global_target_limit)) > global_target_limit:
            raise HTTPException(status_code=400, detail={"code": "qzone_agent_target_limit_exceeds_global"})
        if float(payload.get("target_cooldown_seconds", global_cooldown)) < global_cooldown:
            raise HTTPException(status_code=400, detail={"code": "qzone_agent_cooldown_below_global"})
        try:
            settings = set_group_qzone_agent_settings(group_id, payload)
        except (TypeError, ValueError, OverflowError) as exc:
            raise HTTPException(status_code=400, detail={"code": "qzone_agent_settings_invalid"}) from exc
        try:
            webui_audit_log.record(
                action="group_qzone_agent_update",
                qq=admin.qq,
                device_id=admin.device_id,
                target=str(group_id),
                ip_hash=get_client_ip(request),
                detail={
                    "enabled": bool(settings.get("enabled", False)),
                    "group_daily_limit": int(settings.get("group_daily_limit", 0) or 0),
                    "target_daily_limit": int(settings.get("target_daily_limit", 0) or 0),
                    "target_cooldown_seconds": float(
                        settings.get("target_cooldown_seconds", 0.0) or 0.0
                    ),
                },
            )
            audit_ok = True
        except Exception as exc:
            audit_ok = False
            logger = getattr(runtime, "logger", None)
            if logger is not None:
                logger.warning(
                    "[qzone agent operation] audit_failed "
                    f"exception={type(exc).__name__}"
                )
        return {
            "ok": True,
            "group_id": str(group_id),
            "settings": settings,
            "partial": not audit_ok,
            "outcome_unknown": False,
            "warnings": [] if audit_ok else ["配置已保存，但审计记录写入失败。"],
        }

    return router


__all__ = ["build_qzone_agent_group_router"]
