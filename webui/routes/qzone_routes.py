"""WebUI QQ 空间路由：展示发空间月度额度/状态，并支持管理员手动发一条。

让 agent 自己按额度把控发不发，这里把额度状态可视化给管理员，并提供手动控制。
"""
from __future__ import annotations

import time
import uuid
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response

from ...core import webui_audit_log
from ..deps import AdminIdentity, get_client_ip, require_admin


def _get_bot(runtime, bot_id: str = "") -> Any | None:
    try:
        bots = runtime.get_bots() if callable(getattr(runtime, "get_bots", None)) else {}
    except Exception:
        return None
    expected = str(bot_id or "").strip()
    if expected:
        for key, bot in (bots or {}).items():
            if str(getattr(bot, "self_id", key) or "") == expected:
                return bot
        return None
    return next(iter((bots or {}).values()), None)


def _auth_owner(admin: AdminIdentity) -> str:
    return f"{admin.qq}:{admin.device_id}"


def _bundle_attr(runtime, name: str) -> Any:
    bundle = getattr(runtime, "runtime_bundle", None)
    return getattr(bundle, name, None) if bundle is not None else None


def _build_status(runtime) -> dict[str, Any]:
    from ...core.data_store import get_data_store
    from ...core.qzone_service import get_qzone_auth_status
    from ...core.sensitive_data import sanitize_object
    from ...core.time_ctx import get_configured_now
    from ...flows.qzone_social_flow import get_qzone_scan_status
    from ...jobs.periodic_jobs import build_qzone_quota

    cfg = getattr(runtime, "plugin_config", None)
    enabled = bool(getattr(cfg, "personification_qzone_enabled", False))
    proactive_enabled = bool(getattr(cfg, "personification_qzone_proactive_enabled", False))
    social_enabled = bool(getattr(cfg, "personification_qzone_social_enabled", False))
    inbound_enabled = bool(getattr(cfg, "personification_qzone_inbound_enabled", False))
    monthly_limit = int(getattr(cfg, "personification_qzone_monthly_limit", 30) or 30)
    min_interval_hours = float(getattr(cfg, "personification_qzone_min_interval_hours", 12.0) or 0)
    check_interval = int(getattr(cfg, "personification_qzone_check_interval", 60) or 60)
    quiet_start = int(getattr(cfg, "personification_qzone_quiet_hour_start", 0) or 0)
    quiet_end = int(getattr(cfg, "personification_qzone_quiet_hour_end", 7) or 7)

    state = get_data_store().load_sync("qzone_post_state")
    if not isinstance(state, dict):
        state = {}
    now = get_configured_now()
    quota = build_qzone_quota(
        state=state,
        now=now,
        monthly_limit=monthly_limit,
        min_interval_hours=min_interval_hours,
    )
    last_post_at = float(state.get("last_post_at", 0) or 0)
    next_eligible_at = float(quota.get("next_eligible_at", 0) or 0)
    now_ts = time.time()
    social_state = get_data_store().load_sync("qzone_social_state")
    if not isinstance(social_state, dict):
        social_state = {}
    scheduler = _bundle_attr(runtime, "scheduler")
    try:
        bot_items = list((runtime.get_bots() or {}).items()) if callable(getattr(runtime, "get_bots", None)) else []
    except Exception:
        bot_items = []

    def _job_status(job_id: str) -> dict[str, Any]:
        try:
            job = scheduler.get_job(job_id) if scheduler is not None and hasattr(scheduler, "get_job") else None
        except Exception:
            job = None
        return {
            "registered": job is not None,
            "next_run_at": job.next_run_time.timestamp() if job is not None and getattr(job, "next_run_time", None) else 0,
        }

    return {
        "enabled": enabled,
        "proactive_enabled": proactive_enabled,
        "social_enabled": social_enabled,
        "inbound_enabled": inbound_enabled,
        "publish_available": bool(_bundle_attr(runtime, "qzone_publish_available")),
        "bots": [{"bot_id": str(getattr(bot, "self_id", key) or key)} for key, bot in bot_items],
        "cookie_configured": bool(str(getattr(cfg, "personification_qzone_cookie", "") or "").strip()),
        "auth": sanitize_object(get_qzone_auth_status()),
        "scan": get_qzone_scan_status(),
        "social": {
            "last_scan_at": float(social_state.get("last_scan_at", 0) or 0),
            "last_result": social_state.get("last_result") if isinstance(social_state.get("last_result"), dict) else {},
            "last_error": str(social_state.get("last_error", "") or ""),
            "job": _job_status("personification_qzone_social_scan"),
        },
        "inbound": {
            "last_scan_at": float(social_state.get("last_inbound_scan_at", 0) or 0),
            "last_result": social_state.get("last_inbound_result") if isinstance(social_state.get("last_inbound_result"), dict) else {},
            "last_error": str(social_state.get("last_inbound_error", "") or ""),
            "job": _job_status("personification_qzone_inbound_poll"),
        },
        "quota": quota,
        "check_interval_minutes": check_interval,
        "quiet_hour_start": quiet_start,
        "quiet_hour_end": quiet_end,
        "last_post_at": last_post_at,
        "last_content": str(state.get("last_content", "") or ""),
        "next_eligible_at": next_eligible_at,
        "next_eligible_in_seconds": max(0, int(next_eligible_at - now_ts)) if next_eligible_at else 0,
        "recent_contents": [str(x) for x in list(state.get("recent_contents", []))][-12:],
        "server_now": int(now_ts),
    }


def build_qzone_router(*, runtime) -> APIRouter:
    router = APIRouter(prefix="/api/qzone", tags=["qzone"])

    @router.get("/status")
    async def status(_: AdminIdentity = Depends(require_admin)) -> dict:
        return _build_status(runtime)

    @router.post("/post-now")
    async def post_now(
        request: Request,
        body: dict = Body(default_factory=dict),
        admin: AdminIdentity = Depends(require_admin),
    ) -> dict:
        """管理员手动强制发一条说说（绕过额度/间隔/agent 决策，但仍计入月度额度）。"""
        from ...core.time_ctx import get_configured_now
        from ...core.operation_diagnostics import detail, diagnostic, normalize_diagnostic, step
        from ...core.qzone_service import get_qzone_auth_status
        from ...core.sensitive_data import sanitize_text
        from ...jobs.periodic_jobs import build_qzone_quota, coordinated_qzone_publish

        logger = getattr(runtime, "logger", None)
        generate = _bundle_attr(runtime, "qzone_generate_post")
        publish = _bundle_attr(runtime, "publish_qzone_shuo")
        update_cookie = _bundle_attr(runtime, "update_qzone_cookie")
        if generate is None or publish is None:
            raise HTTPException(status_code=503, detail="发空间能力未就绪（qzone 未启用或运行时未初始化）")

        bot = _get_bot(runtime, str(body.get("bot_id") or ""))
        if bot is None:
            raise HTTPException(status_code=503, detail="Bot 未连接")

        operation_id = str(body.get("operation_id") or uuid.uuid4().hex)[:96]
        warnings: list[str] = []
        if callable(update_cookie):
            try:
                refresh_ok, refresh_message = await update_cookie(bot)
                if not refresh_ok:
                    warnings.append(f"LLOneBot Cookie 刷新未成功，已继续尝试使用现有凭证：{sanitize_text(refresh_message, limit=180)}")
            except Exception as exc:
                if logger is not None:
                    logger.warning(f"[webui] 手动发说说刷新 Cookie 失败：{exc}")
                warnings.append(f"LLOneBot Cookie 刷新异常，已继续尝试使用现有凭证：{type(exc).__name__}")

        try:
            detailed_generate = getattr(generate, "detailed", None)
            if callable(detailed_generate):
                generation = await detailed_generate(bot)
                content = str(generation.get("content") or "") if isinstance(generation, dict) else ""
                generation_diag = normalize_diagnostic(
                    generation.get("diagnostic") if isinstance(generation, dict) else None,
                    ok=bool(content),
                )
            else:
                content = await generate(bot)
                generation_diag = diagnostic(
                    ok=bool(content),
                    code="qzone_draft_ready" if content else "legacy_generator_empty",
                    phase="draft_generation",
                    title="说说草稿已生成" if content else "旧版生成器没有返回草稿",
                    message="草稿已生成。" if content else "当前运行时未提供详细生成报告，且返回正文为空。",
                    suggestion="重启插件加载最新运行时后再试。" if not content else "",
                    retryable=not bool(content),
                )
        except Exception as exc:
            result = diagnostic(
                ok=False,
                code="qzone_generation_exception",
                phase="draft_generation",
                title="说说生成流程异常中断",
                message="生成函数抛出异常，尚未进入 QZone 发布阶段。",
                details=(detail("异常类型", type(exc).__name__, "error"),),
                warnings=warnings,
                suggestion="根据异常类型检查生成链路和 Provider 状态后重试。",
                retryable=True,
                operation_id=operation_id,
            )
            webui_audit_log.record(
                action="qzone_post_now",
                qq=admin.qq,
                device_id=admin.device_id,
                target=str(getattr(bot, "self_id", "") or ""),
                ip_hash=get_client_ip(request),
                detail={"operation_id": operation_id, "code": result["code"], "phase": result["phase"]},
                outcome="failed",
            )
            return result
        if not content:
            generation_diag["operation_id"] = operation_id
            generation_diag["warnings"] = list(generation_diag.get("warnings") or []) + warnings
            webui_audit_log.record(
                action="qzone_post_now",
                qq=admin.qq,
                device_id=admin.device_id,
                target=str(getattr(bot, "self_id", "") or ""),
                ip_hash=get_client_ip(request),
                detail={"operation_id": operation_id, "code": generation_diag.get("code"), "phase": generation_diag.get("phase")},
                outcome="failed",
            )
            return generation_diag

        cfg = getattr(runtime, "plugin_config", None)
        published = await coordinated_qzone_publish(
            operation_id=operation_id,
            content=content,
            now=get_configured_now(),
            monthly_limit=int(getattr(cfg, "personification_qzone_monthly_limit", 30) or 30),
            min_interval_hours=float(getattr(cfg, "personification_qzone_min_interval_hours", 12.0) or 0),
            kind="post",
            publish=lambda: publish(content, getattr(bot, "self_id", "")),
            force=True,
        )
        if not published.get("success"):
            publish_status = str(published.get("status") or "failed")
            auth_status = get_qzone_auth_status()
            outcome_unknown = publish_status in {"outcome_unknown", "unknown"}
            if outcome_unknown:
                code = "qzone_publish_outcome_unknown"
                title = "QZone 发布结果未知"
                message = "发布请求可能已经到达腾讯，但本次没有得到明确成功或失败结果。"
                suggestion = "先打开 QQ 空间检查是否已经发布，禁止直接再次点击发布，以免产生重复说说。"
                retryable = False
            elif publish_status in {"reserved", "duplicate_reserved"}:
                code = "qzone_publish_in_progress"
                title = "相同发布请求仍在处理中"
                message = "该 Operation ID 已有一个未完成的发布请求，当前没有再次向 QZone 外发。"
                suggestion = "等待原请求完成并刷新状态，不要创建新的重复请求。"
                retryable = False
            elif auth_status.get("status") == "login_required":
                code = "qzone_login_required"
                title = "QZone 登录凭证已失效"
                message = "腾讯返回了登录页、验证码或无效认证状态，本次草稿没有发布。"
                suggestion = "先在上方“QZone 认证恢复”中扫码登录，确认认证健康后再重新发布。"
                retryable = False
            else:
                code = "qzone_publish_rejected"
                title = "QZone 明确拒绝了发布"
                message = sanitize_text(published.get("message") or "发布层返回失败状态", limit=260)
                suggestion = "根据发布层返回和认证状态修复问题；只有明确失败时才可以重新发布。"
                retryable = True
            steps = list(generation_diag.get("steps") or [])
            steps.append(step("publish", "提交到 QZone", "unknown" if outcome_unknown else "error", message).to_dict())
            result = diagnostic(
                ok=False,
                code=code,
                phase="qzone_publish",
                title=title,
                message=message,
                details=(
                    detail("候选正文", content[:200], "ok"),
                    detail("协调状态", publish_status, "error" if not outcome_unknown else "warn"),
                ),
                steps=tuple(
                    step(
                        str(item.get("key") or "step"),
                        str(item.get("label") or "步骤"),
                        str(item.get("status") or "unknown"),
                        str(item.get("message") or ""),
                        details=tuple(
                            detail(str(child.get("label") or "详情"), child.get("value"), str(child.get("status") or "info"))
                            for child in item.get("details") or []
                            if isinstance(child, dict)
                        ),
                    )
                    for item in steps
                    if isinstance(item, dict)
                ),
                warnings=list(generation_diag.get("warnings") or []) + warnings,
                suggestion=suggestion,
                retryable=retryable,
                outcome_unknown=outcome_unknown,
                operation_id=operation_id,
            )
            result["content"] = content[:2000]
            webui_audit_log.record(
                action="qzone_post_now",
                qq=admin.qq,
                device_id=admin.device_id,
                target=str(getattr(bot, "self_id", "") or ""),
                ip_hash=get_client_ip(request),
                detail={"operation_id": operation_id, "code": code, "status": publish_status},
                outcome="unknown" if outcome_unknown else "failed",
            )
            return result

        state = published.get("state") or {}
        mark_published = getattr(generate, "mark_published", None)
        if callable(mark_published):
            mark_published(content)
        quota = build_qzone_quota(
            state=state,
            now=get_configured_now(),
            monthly_limit=int(getattr(cfg, "personification_qzone_monthly_limit", 30) or 30),
            min_interval_hours=float(getattr(cfg, "personification_qzone_min_interval_hours", 12.0) or 0),
        )
        if logger is not None:
            logger.info(f"[webui] 管理员 {admin.qq} 手动发布了一条空间说说。")
        webui_audit_log.record(
            action="qzone_post_now",
            qq=admin.qq,
            device_id=admin.device_id,
            target=str(getattr(bot, "self_id", "") or ""),
            ip_hash=get_client_ip(request),
            detail={"operation_id": operation_id, "duplicate": bool(published.get("duplicate"))},
            outcome="ok",
        )
        generation_steps = list(generation_diag.get("steps") or [])
        generation_steps.append({"key": "publish", "label": "提交到 QZone", "status": "ok", "message": "腾讯已明确返回发布成功。", "details": []})
        result = diagnostic(
            ok=True,
            code="qzone_post_published",
            phase="publish_complete",
            title="说说已经发布",
            message="草稿通过全部检查，腾讯已明确确认发布成功。",
            details=(detail("正文", content[:200], "ok"), detail("本月已用额度", quota.get("used", 0), "info")),
            steps=tuple(
                step(str(item.get("key") or "step"), str(item.get("label") or "步骤"), str(item.get("status") or "unknown"), str(item.get("message") or ""))
                for item in generation_steps
                if isinstance(item, dict)
            ),
            warnings=list(generation_diag.get("warnings") or []) + warnings,
            suggestion="无需再次点击发布。",
            operation_id=operation_id,
        )
        result.update({
            "content": content[:2000],
            "quota": quota,
            "duplicate": bool(published.get("duplicate")),
        })
        return result

    @router.post("/refresh-cookie")
    async def refresh_cookie(
        request: Request,
        body: dict = Body(default_factory=dict),
        admin: AdminIdentity = Depends(require_admin),
    ) -> dict:
        update_cookie = _bundle_attr(runtime, "update_qzone_cookie")
        bot = _get_bot(runtime, str(body.get("bot_id") or ""))
        if not callable(update_cookie) or bot is None:
            raise HTTPException(status_code=503, detail="Cookie 刷新能力未就绪或 Bot 未连接")
        try:
            ok, message = await update_cookie(bot, force=True)
        except Exception as exc:
            ok, message = False, str(exc)
        webui_audit_log.record(
            action="qzone_cookie_refresh",
            qq=admin.qq,
            device_id=admin.device_id,
            target=str(getattr(bot, "self_id", "") or ""),
            ip_hash=get_client_ip(request),
            detail={"ok": bool(ok), "status": "refreshed" if ok else "failed"},
            outcome="ok" if ok else "failed",
        )
        from ...core.sensitive_data import sanitize_text

        return {"ok": bool(ok), "status": "refreshed" if ok else "failed", "message": "ok" if ok else sanitize_text(message, limit=240)}

    @router.post("/auth/login/start")
    async def start_login(
        request: Request,
        body: dict = Body(default_factory=dict),
        admin: AdminIdentity = Depends(require_admin),
    ) -> dict:
        from ...core.qzone_auth import qzone_login_manager
        from ...core.qzone_service import install_qzone_cookie

        bot = _get_bot(runtime, str(body.get("bot_id") or ""))
        if bot is None:
            raise HTTPException(status_code=503, detail="目标 Bot 未连接")
        bot_id = str(getattr(bot, "self_id", "") or "")

        async def _install(cookie: str, expected_bot_id: str, source: str) -> tuple[bool, str]:
            return await install_qzone_cookie(
                cookie=cookie,
                expected_bot_id=expected_bot_id,
                plugin_config=getattr(runtime, "plugin_config", None),
                logger=getattr(runtime, "logger", None),
                source=source,
            )

        try:
            result = await qzone_login_manager.start(
                bot_id=bot_id,
                owner_key=_auth_owner(admin),
                install_cookie=_install,
            )
        except RuntimeError as exc:
            webui_audit_log.record(
                action="qzone_login_start",
                qq=admin.qq,
                device_id=admin.device_id,
                target=bot_id,
                ip_hash=get_client_ip(request),
                detail={"status": "rate_limited_or_busy"},
                outcome="failed",
            )
            raise HTTPException(status_code=429, detail=str(exc)) from exc
        except Exception as exc:
            logger = getattr(runtime, "logger", None)
            if logger is not None:
                logger.warning(f"[qzone-auth] 生成登录二维码失败：{type(exc).__name__}")
            webui_audit_log.record(
                action="qzone_login_start",
                qq=admin.qq,
                device_id=admin.device_id,
                target=bot_id,
                ip_hash=get_client_ip(request),
                detail={"status": "upstream_failed", "error_type": type(exc).__name__},
                outcome="failed",
            )
            raise HTTPException(status_code=502, detail="无法生成腾讯登录二维码，请稍后重试") from exc
        webui_audit_log.record(
            action="qzone_login_start",
            qq=admin.qq,
            device_id=admin.device_id,
            target=bot_id,
            ip_hash=get_client_ip(request),
            detail={"status": result.get("status")},
            outcome="ok",
        )
        return result

    @router.get("/auth/login/{session_id}/status")
    async def login_status(
        session_id: str,
        admin: AdminIdentity = Depends(require_admin),
    ) -> dict:
        from ...core.qzone_auth import qzone_login_manager

        try:
            return qzone_login_manager.status(session_id, owner_key=_auth_owner(admin))
        except LookupError as exc:
            raise HTTPException(status_code=404, detail="登录会话不存在或已过期") from exc

    @router.get("/auth/login/{session_id}/qrcode")
    async def login_qrcode(
        session_id: str,
        admin: AdminIdentity = Depends(require_admin),
    ) -> Response:
        from ...core.qzone_auth import qzone_login_manager

        try:
            image = qzone_login_manager.qrcode(session_id, owner_key=_auth_owner(admin))
        except LookupError as exc:
            raise HTTPException(status_code=404, detail="登录二维码不存在或已失效") from exc
        return Response(
            content=image,
            media_type="image/png",
            headers={"Cache-Control": "no-store, private", "Pragma": "no-cache"},
        )

    @router.post("/auth/login/{session_id}/cancel")
    async def cancel_login(
        session_id: str,
        request: Request,
        admin: AdminIdentity = Depends(require_admin),
    ) -> dict:
        from ...core.qzone_auth import qzone_login_manager

        try:
            result = await qzone_login_manager.cancel(session_id, owner_key=_auth_owner(admin))
        except LookupError as exc:
            raise HTTPException(status_code=404, detail="登录会话不存在或已过期") from exc
        webui_audit_log.record(
            action="qzone_login_cancel",
            qq=admin.qq,
            device_id=admin.device_id,
            target=str(result.get("bot_id") or ""),
            ip_hash=get_client_ip(request),
            detail={"status": "cancelled"},
            outcome="ok",
        )
        return result

    @router.post("/auth/cookie")
    async def import_cookie(
        request: Request,
        body: dict = Body(default_factory=dict),
        admin: AdminIdentity = Depends(require_admin),
    ) -> dict:
        from ...core.qzone_service import install_qzone_cookie

        cookie = str(body.get("cookie") or "").strip()
        if not cookie or len(cookie) > 16_384:
            raise HTTPException(status_code=400, detail="Cookie 为空或超过 16 KiB")
        bot = _get_bot(runtime, str(body.get("bot_id") or ""))
        if bot is None:
            raise HTTPException(status_code=503, detail="目标 Bot 未连接")
        bot_id = str(getattr(bot, "self_id", "") or "")
        ok, reason = await install_qzone_cookie(
            cookie=cookie,
            expected_bot_id=bot_id,
            plugin_config=getattr(runtime, "plugin_config", None),
            logger=getattr(runtime, "logger", None),
            source="manual",
        )
        messages = {
            "missing_p_skey": "Cookie 缺少 p_skey",
            "missing_uin": "Cookie 缺少有效 uin",
            "account_mismatch": "Cookie QQ 与当前 Bot QQ 不一致",
            "auth_blocked": "Cookie 已失效或仍被腾讯认证拦截",
            "probe_failed": "暂时无法验证 Cookie，请稍后重试",
        }
        message = "QZone Cookie 已验证并安装" if ok else messages.get(reason, "Cookie 验证失败")
        webui_audit_log.record(
            action="qzone_cookie_import",
            qq=admin.qq,
            device_id=admin.device_id,
            target=bot_id,
            ip_hash=get_client_ip(request),
            detail={"source": "manual", "status": "installed" if ok else str(reason)[:32]},
            outcome="ok" if ok else "failed",
        )
        return {"ok": bool(ok), "status": "installed" if ok else "failed", "message": message}

    @router.post("/scan-now")
    async def scan_now(
        request: Request,
        body: dict = Body(default_factory=dict),
        admin: AdminIdentity = Depends(require_admin),
    ) -> dict:
        kind = str(body.get("kind") or "inbound").strip().lower()
        if kind not in {"social", "inbound"}:
            raise HTTPException(status_code=400, detail="kind 只能是 social 或 inbound")
        runner = _bundle_attr(runtime, "qzone_social_scan" if kind == "social" else "qzone_inbound_poll")
        if not callable(runner):
            raise HTTPException(status_code=503, detail="对应空间扫描任务未初始化")
        result = await runner(force=True)
        webui_audit_log.record(
            action="qzone_scan_now",
            qq=admin.qq,
            device_id=admin.device_id,
            target=kind,
            ip_hash=get_client_ip(request),
            detail={"status": result.get("status") or ("success" if result.get("ok") else "failed"), "skipped": bool(result.get("skipped"))},
            outcome="ok" if result.get("ok") else "failed",
        )
        return result

    return router
