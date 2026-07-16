from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request

from ...core import webui_audit_log
from ...core.mcp_management import (
    get_mcp_manager,
    mcp_registry_sources,
    resolve_registry_source,
)
from ...core.operation_diagnostics import diagnostic, detail, step
from ..deps import AdminIdentity, get_client_ip, require_admin


def _failure(
    exc: Exception,
    title: str,
    *,
    validation: bool = False,
    message: str = "",
) -> HTTPException:
    status = 400 if validation or isinstance(exc, ValueError) else 404 if isinstance(exc, KeyError) else 500
    safe_message = message or (
        "请求字段不符合 MCP 操作要求。"
        if status == 400
        else "找不到指定的 MCP 安装或工具。"
        if status == 404
        else "MCP 操作未完成；第三方进程、Registry 或 runtime 返回异常。"
    )
    return HTTPException(
        status_code=status,
        detail=diagnostic(
            ok=False,
            code="mcp_request_invalid" if status == 400 else "mcp_installation_not_found" if status == 404 else "mcp_operation_failed",
            phase="validation" if status == 400 else "lookup" if status == 404 else "runtime",
            title=title,
            message=safe_message,
            details=(detail("失败类型", type(exc).__name__, "error"),),
            steps=(step("mcp_operation", title, "error", "操作未完成。"),),
            suggestion="检查输入和当前安装状态后重试。" if status < 500 else "查看脱敏日志；若预检已执行，先刷新安装列表确认是否已落库。",
            retryable=status != 404,
        ),
    )


def _strict_bool(body: dict[str, Any], key: str, *, title: str, default: Any = None) -> bool:
    value = body.get(key, default)
    if type(value) is not bool:
        raise _failure(
            ValueError(key),
            title,
            validation=True,
            message=f"{key} 必须是 JSON boolean。",
        )
    return value


def build_mcp_router(*, runtime: Any) -> APIRouter:
    router = APIRouter(prefix="/api/mcp", tags=["mcp"])

    def manager():
        return get_mcp_manager(runtime)

    @router.get("/sources")
    async def sources(_: AdminIdentity = Depends(require_admin)) -> dict:
        return {"sources": mcp_registry_sources(runtime.plugin_config)}

    @router.get("/search")
    async def search(
        source_id: str = "official",
        q: str = "",
        cursor: str = "",
        limit: int = Query(20, ge=1, le=50),
        _: AdminIdentity = Depends(require_admin),
    ) -> dict:
        try:
            source = resolve_registry_source(runtime.plugin_config, source_id)
            return await manager().registry_client.search(source, q, limit=limit, cursor=cursor)
        except Exception as exc:
            raise _failure(exc, "MCP Registry 搜索失败") from exc

    @router.get("/detail")
    async def registry_detail(
        name: str,
        source_id: str = "official",
        fresh: bool = False,
        _: AdminIdentity = Depends(require_admin),
    ) -> dict:
        try:
            source = resolve_registry_source(runtime.plugin_config, source_id)
            result = await manager().registry_client.detail(source, name, fresh=fresh)
            result.pop("raw", None)
            return result
        except Exception as exc:
            raise _failure(exc, "MCP Server 详情读取失败") from exc

    @router.get("/installations")
    async def installations(_: AdminIdentity = Depends(require_admin)) -> dict:
        current_manager = manager()
        await current_manager.refresh_process_states()
        items = current_manager.list_public()
        return {"installations": items, "total": len(items)}

    @router.post("/install")
    async def install(
        request: Request,
        body: dict = Body(default_factory=dict),
        admin: AdminIdentity = Depends(require_admin),
    ) -> dict:
        if body.get("confirm_execution") is not True:
            raise _failure(
                ValueError("confirm_execution"),
                "MCP 安装未确认",
                validation=True,
                message="必须以 JSON boolean true 确认下载并执行第三方 package。",
            )
        fresh_fetch = _strict_bool(body, "fresh_fetch", title="MCP 安装请求无效", default=True)
        source_id = str(body.get("source_id") or "official")
        server_name = str(body.get("server_name") or "")
        try:
            source = resolve_registry_source(runtime.plugin_config, source_id)
            server_detail = await manager().registry_client.detail(source, server_name, fresh=fresh_fetch)
            item = await manager().install(
                source=source,
                detail=server_detail,
                package_index=int(body.get("package_index") or 0),
                package_digest=str(body.get("package_digest") or ""),
                inputs=body.get("inputs") if isinstance(body.get("inputs"), dict) else {},
                prefix=str(body.get("name_prefix") or ""),
                creator=admin.qq,
            )
        except Exception as exc:
            webui_audit_log.record(
                action="mcp_install",
                qq=admin.qq,
                device_id=admin.device_id,
                target=server_name,
                ip_hash=get_client_ip(request),
                detail={"source_id": source_id, "error_type": type(exc).__name__},
                outcome="error",
            )
            raise _failure(exc, "MCP Server 安装失败") from exc
        enabled = sum(1 for tool in item.get("tools", []) if tool.get("enabled"))
        disabled = len(item.get("tools", [])) - enabled
        webui_audit_log.record(
            action="mcp_install",
            qq=admin.qq,
            device_id=admin.device_id,
            target=item["installation_id"],
            ip_hash=get_client_ip(request),
            detail={"source_id": source_id, "server_name": server_name, "enabled_tools": enabled, "disabled_tools": disabled},
        )
        report = diagnostic(
            ok=True,
            code="mcp_installed",
            phase="ready",
            title="MCP Server 已安装",
            message="package 已按精确版本预检；所有工具默认关闭，需管理员逐项确认风险后启用。",
            details=(detail("已启用工具", enabled, "ok"), detail("待人工确认工具", disabled, "warn" if disabled else "ok")),
            steps=(
                step("resolve", "读取权威 metadata", "ok", "已从配置的 Registry 重新读取详情。"),
                step("preflight", "启动并读取 tools/list", "ok", "第三方 package 已完成显式确认后的预检。"),
                step("persist", "保存安装与 Secret 引用", "ok", "Secret 未写入数据库、审计或响应。"),
                step("activate", "等待逐工具授权", "skipped", "未信任 publisher annotation；当前没有自动注册工具。"),
            ),
            warnings=("Registry 仅验证发布者命名空间，不代表 package 经过安全审计。",),
            operation_id=item["installation_id"],
        )
        return {"installation": item, "diagnostic": report}

    @router.post("/installations/{installation_id}/toggle")
    async def toggle_installation(
        installation_id: str,
        body: dict = Body(default_factory=dict),
        admin: AdminIdentity = Depends(require_admin),
    ) -> dict:
        enabled = _strict_bool(body, "enabled", title="MCP Server 状态请求无效")
        try:
            item = await manager().toggle_installation(installation_id, enabled)
        except Exception as exc:
            current = manager().public_installation(installation_id)
            if current is not None and current.get("desired_enabled") is enabled:
                webui_audit_log.record(
                    action="mcp_toggle",
                    qq=admin.qq,
                    device_id=admin.device_id,
                    target=installation_id,
                    detail={"enabled": enabled, "runtime_error_type": type(exc).__name__},
                    outcome="partial",
                )
                report = diagnostic(
                    ok=False,
                    code="mcp_server_start_partial" if enabled else "mcp_server_stop_partial",
                    phase="activation" if enabled else "shutdown",
                    title="MCP Server 状态已保存，但 runtime 未完成",
                    message="desired 状态已持久化，process 未达到请求状态。",
                    details=(detail("失败类型", type(exc).__name__, "error"), detail("desired_enabled", enabled, "ok")),
                    steps=(
                        step("validate", "校验请求", "ok"),
                        step("persist", "持久化 desired 状态", "ok"),
                        step("runtime", "同步 catalog 与 process", "error", "runtime 未完成请求状态。"),
                    ),
                    suggestion="保留当前 desired 状态；修复 package 或环境后执行 MCP reload。",
                    retryable=True,
                    partial=True,
                    operation_id=installation_id,
                )
                raise HTTPException(status_code=500, detail=report) from exc
            raise _failure(exc, "MCP Server 状态切换失败") from exc
        webui_audit_log.record(action="mcp_toggle", qq=admin.qq, device_id=admin.device_id, target=installation_id, detail={"enabled": enabled})
        report = diagnostic(
            ok=True,
            code="mcp_server_toggled",
            phase="runtime",
            title="MCP Server 状态已更新",
            message="Server 已按 desired 状态完成 catalog 同步与 process 调整。",
            details=(
                detail("desired_enabled", item.get("desired_enabled"), "ok"),
                detail("process_state", item.get("process_state"), "ok"),
                detail("保留授权工具", item.get("authorized_count", 0), "info"),
            ),
            steps=(
                step("persist", "持久化 desired 状态", "ok"),
                step("catalog", "同步 tool catalog", "ok" if enabled else "skipped"),
                step("runtime", "调整 process 与注册工具", "ok"),
            ),
            operation_id=installation_id,
        )
        return {"installation": item, "diagnostic": report}

    @router.post("/installations/{installation_id}/tools/{remote_name:path}/toggle")
    async def toggle_tool(
        installation_id: str,
        remote_name: str,
        body: dict = Body(default_factory=dict),
        admin: AdminIdentity = Depends(require_admin),
    ) -> dict:
        enabled = _strict_bool(body, "enabled", title="MCP 工具状态请求无效")
        confirm_side_effect = _strict_bool(
            body,
            "confirm_side_effect",
            title="MCP 工具状态请求无效",
            default=False,
        )
        current = manager().public_installation(installation_id)
        if current is None:
            raise _failure(KeyError(installation_id), "找不到 MCP 安装")
        policy = next((tool for tool in current.get("tools", []) if tool.get("remote_name") == remote_name), None)
        if policy is None:
            raise _failure(KeyError(remote_name), "找不到 MCP 工具")
        if enabled and not confirm_side_effect:
            raise _failure(
                ValueError("confirm_side_effect"),
                "MCP 工具启用未确认",
                validation=True,
                message="MCP 工具需要以 JSON boolean true 显式确认风险后才能启用。",
            )
        try:
            item = await manager().toggle_tool(
                installation_id,
                remote_name,
                enabled,
                approve_side_effect=confirm_side_effect,
            )
        except Exception as exc:
            current = manager().public_installation(installation_id)
            current_policy = next(
                (tool for tool in (current or {}).get("tools", []) if tool.get("remote_name") == remote_name),
                None,
            )
            if current_policy is not None and current_policy.get("authorized") is enabled:
                webui_audit_log.record(
                    action="mcp_tool_toggle",
                    qq=admin.qq,
                    device_id=admin.device_id,
                    target=installation_id,
                    detail={
                        "remote_name": remote_name,
                        "enabled": enabled,
                        "confirmed_side_effect": confirm_side_effect,
                        "runtime_error_type": type(exc).__name__,
                    },
                    outcome="partial",
                )
                report = diagnostic(
                    ok=False,
                    code="mcp_tool_toggle_partial",
                    phase="activation",
                    title="MCP 工具授权已保存，但 runtime 未完成",
                    message="工具授权状态已持久化，process 或注册状态未完成同步。",
                    details=(detail("失败类型", type(exc).__name__, "error"), detail("authorized", enabled, "ok")),
                    steps=(
                        step("persist", "持久化工具授权", "ok"),
                        step("catalog", "同步 tool catalog", "error"),
                        step("runtime", "更新工具注册", "error"),
                    ),
                    suggestion="保留当前授权；修复 Server 后执行 MCP reload。",
                    retryable=True,
                    partial=True,
                    operation_id=installation_id,
                )
                raise HTTPException(status_code=500, detail=report) from exc
            raise _failure(exc, "MCP 工具状态切换失败") from exc
        webui_audit_log.record(action="mcp_tool_toggle", qq=admin.qq, device_id=admin.device_id, target=installation_id, detail={"remote_name": remote_name, "enabled": enabled, "confirmed_side_effect": confirm_side_effect})
        report = diagnostic(
            ok=True,
            code="mcp_tool_toggled",
            phase="runtime",
            title="MCP 工具授权已更新",
            message="授权、catalog 与当前注册状态已完成同步。",
            details=(detail("remote_name", remote_name, "info"), detail("authorized", enabled, "ok")),
            steps=(
                step("persist", "持久化工具授权", "ok"),
                step("catalog", "重新读取 tool catalog", "ok" if item.get("desired_enabled") else "skipped"),
                step("runtime", "更新工具注册", "ok"),
            ),
            operation_id=installation_id,
        )
        return {"installation": item, "diagnostic": report}

    @router.delete("/installations/{installation_id}")
    async def delete_installation(
        installation_id: str,
        body: dict = Body(default_factory=dict),
        admin: AdminIdentity = Depends(require_admin),
    ) -> dict:
        if body.get("confirm") != "delete":
            raise _failure(ValueError("删除 MCP 安装需要 confirm=delete"), "MCP 删除未确认", validation=True)
        try:
            await manager().delete(installation_id)
        except Exception as exc:
            raise _failure(exc, "MCP Server 删除失败") from exc
        webui_audit_log.record(action="mcp_delete", qq=admin.qq, device_id=admin.device_id, target=installation_id)
        return {"success": True, "installation_id": installation_id}

    @router.post("/reload")
    async def reload_mcp(admin: AdminIdentity = Depends(require_admin)) -> dict:
        try:
            result = await manager().reload()
        except Exception as exc:
            raise _failure(exc, "MCP reload 失败") from exc
        webui_audit_log.record(action="mcp_reload", qq=admin.qq, device_id=admin.device_id, detail=result, outcome="partial" if result["failed"] else "ok")
        failed = int(result.get("failed") or 0)
        report = diagnostic(
            ok=failed == 0,
            code="mcp_reload_complete" if failed == 0 else "mcp_reload_partial",
            phase="runtime_reload",
            title="MCP reload 已完成" if failed == 0 else "MCP reload 部分完成",
            message="已逐个同步启用安装的 catalog 与 process 状态。",
            details=(
                detail("running", result.get("running", 0), "ok"),
                detail("ready", result.get("ready", 0), "info"),
                detail("failed", failed, "error" if failed else "ok"),
                detail("catalog_added", result.get("catalog_added", 0), "info"),
                detail("catalog_updated", result.get("catalog_updated", 0), "info"),
                detail("catalog_removed", result.get("catalog_removed", 0), "info"),
            ),
            steps=(
                step("stop", "停止旧 managed process", "ok"),
                step("catalog", "重新同步 tool catalog", "warn" if failed else "ok"),
                step("restore", "恢复授权工具注册", "warn" if failed else "ok"),
            ),
            suggestion="检查失败安装的 last_error 后再次 reload。" if failed else "",
            retryable=failed > 0,
            partial=failed > 0,
        )
        return {**result, "diagnostic": report}

    return router
