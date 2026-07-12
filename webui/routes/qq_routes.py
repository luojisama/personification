"""WebUI QQ 账号管理：改资料/头像、群管理（退群/处理邀请）、好友管理（删/处理请求）。

均为管理员操作；写操作记审计。底层调用 OneBot v11 + NapCat 扩展 API，
不支持的 API 捕获异常返回友好错误（不同协议端能力不同）。
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from nonebot.exception import ActionFailed, ApiNotAvailable, NetworkError

from ...core import webui_audit_log
from ...core.group_directory import discover_group_union
from ...core.operation_diagnostics import OperationDetail, detail, diagnostic, step
from ..deps import AdminIdentity, require_admin


def _target_bot_id(bot: Any, fallback: Any = None) -> str:
    return str(getattr(bot, "self_id", "") or fallback or "当前连接")


def _target_details(bot_id: Any, api: str) -> tuple[OperationDetail, ...]:
    return (
        detail("目标 Bot", str(bot_id or "未指定"), "info"),
        detail("OneBot API", api or "未进入 API 调用", "info"),
    )


def _raise_selection_error(
    *,
    status_code: int,
    code: str,
    title: str,
    message: str,
    bot_id: Any,
    api: str,
    operation_id: str = "",
) -> None:
    raise HTTPException(
        status_code=status_code,
        detail=diagnostic(
            ok=False,
            code=code,
            phase="bot_selection",
            title=title,
            message=message,
            details=_target_details(bot_id, api),
            steps=(
                step("bot_selection", "确认目标 Bot", "error", message),
                step("adapter_call", "调用 OneBot API", "skipped", "尚未向协议端发起调用。"),
            ),
            suggestion="检查目标 Bot 的连接状态和 Bot ID 后再试。",
            retryable=code == "qq_bot_disconnected",
            operation_id=operation_id,
        ),
    )


def _bots(runtime, *, api: str = "", operation_id: str = "") -> dict[str, Any]:
    try:
        bots = runtime.get_bots() if callable(getattr(runtime, "get_bots", None)) else {}
    except Exception:
        bots = {}
    result = {str(getattr(bot, "self_id", "") or key): bot for key, bot in bots.items()} if isinstance(bots, dict) else {}
    if not result:
        _raise_selection_error(
            status_code=503,
            code="qq_bot_disconnected",
            title="没有可用的 QQ Bot 连接",
            message="当前没有已连接的 Bot，操作未进入 OneBot API 调用阶段。",
            bot_id="未连接",
            api=api,
            operation_id=operation_id,
        )
    return result


def _bot(
    runtime,
    bot_id: Any = None,
    *,
    explicit: bool = False,
    api: str = "",
    operation_id: str = "",
) -> Any:
    selected = str(bot_id or "").strip()
    if explicit and not selected:
        _raise_selection_error(
            status_code=400,
            code="qq_invalid_input",
            title="缺少目标 Bot",
            message="该操作必须显式提供 bot_id。",
            bot_id="未指定",
            api=api,
            operation_id=operation_id,
        )
    bots = _bots(runtime, api=api, operation_id=operation_id)
    if not selected:
        return next(iter(bots.values()))
    if selected not in bots:
        _raise_selection_error(
            status_code=503,
            code="qq_bot_disconnected",
            title="目标 QQ Bot 未连接",
            message="指定的 Bot 不在当前连接列表中，操作未进入 OneBot API 调用阶段。",
            bot_id=selected,
            api=api,
            operation_id=operation_id,
        )
    return bots[selected]


async def _call(
    bot: Any,
    api: str,
    *,
    operation_id: str = "",
    side_effect: bool = False,
    target_bot_id: str = "",
    **kwargs: Any,
) -> Any:
    try:
        return await bot.call_api(api, **kwargs)
    except Exception as exc:
        outcome_unknown = False
        if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
            status_code = 504
            code = "qq_operation_timeout"
            title = "QQ 操作等待超时"
            message = "在等待时间内没有收到协议端的明确结果。"
            suggestion = "先在 QQ 中核对实际状态；确认未生效后再决定是否重试。"
            retryable = not side_effect
            outcome_unknown = side_effect
        elif isinstance(exc, PermissionError):
            status_code = 403
            code = "qq_permission_denied"
            title = "QQ 操作权限不足"
            message = "目标账号或协议端没有完成该操作所需的权限。"
            suggestion = "检查 Bot 的群角色、好友关系和协议端权限配置。"
            retryable = False
        elif isinstance(exc, (FileNotFoundError, LookupError)):
            status_code = 404
            code = "qq_target_not_found"
            title = "QQ 操作目标不存在"
            message = "协议调用依赖的目标、请求或资源不存在。"
            suggestion = "刷新 QQ 数据并确认目标仍然存在后再试。"
            retryable = False
        elif isinstance(exc, ValueError):
            status_code = 400
            code = "qq_invalid_input"
            title = "QQ 操作输入无效"
            message = "提交给协议端的参数不符合当前 API 要求。"
            suggestion = "检查目标 ID、请求类型和输入格式。"
            retryable = False
        elif isinstance(exc, ApiNotAvailable):
            status_code = 501
            code = "qq_adapter_unsupported"
            title = "协议端不支持该 QQ 操作"
            message = "当前 Adapter 没有提供所需的 OneBot API。"
            suggestion = "确认协议端版本与扩展 API 支持情况；必要时改用支持该能力的 Adapter。"
            retryable = False
        elif isinstance(exc, (NetworkError, ConnectionError)):
            status_code = 503
            code = "qq_bot_disconnected"
            title = "QQ Bot 连接已中断"
            message = "调用 OneBot API 时连接不可用，未收到明确结果。"
            suggestion = "先恢复 Bot 连接并核对 QQ 实际状态；写操作不要直接重复提交。"
            retryable = not side_effect
            outcome_unknown = side_effect
        elif isinstance(exc, ActionFailed):
            status_code = 502
            code = "qq_adapter_rejected"
            title = "协议端拒绝了 QQ 操作"
            message = "OneBot API 已返回明确失败，本次操作未成功。"
            suggestion = "检查目标状态、账号权限和 Adapter 能力后再试。"
            retryable = False
        else:
            status_code = 500
            code = "qq_internal_error"
            title = "QQ 操作内部异常"
            message = "调用流程发生内部异常，未能确认操作完成。"
            suggestion = "查看服务端脱敏日志定位问题；写操作应先核对 QQ 实际状态。"
            retryable = not side_effect
            outcome_unknown = side_effect
        raise HTTPException(
            status_code=status_code,
            detail=diagnostic(
                ok=False,
                code=code,
                phase="adapter_call",
                title=title,
                message=message,
                details=_target_details(target_bot_id or _target_bot_id(bot), api),
                steps=(
                    step("bot_selection", "确认目标 Bot", "ok", "已选择目标 Bot。"),
                    step(
                        "adapter_call",
                        "调用 OneBot API",
                        "unknown" if outcome_unknown else "error",
                        message,
                    ),
                ),
                suggestion=suggestion,
                retryable=retryable,
                outcome_unknown=outcome_unknown,
                operation_id=operation_id,
            ),
        ) from exc


def _success(
    *,
    code: str,
    title: str,
    message: str,
    bot_id: str,
    api: str,
    operation_id: str,
    extra_details: tuple[OperationDetail, ...] = (),
) -> dict[str, Any]:
    result = diagnostic(
        ok=True,
        code=code,
        phase="operation_complete",
        title=title,
        message=message,
        details=(*_target_details(bot_id, api), *extra_details),
        steps=(
            step("bot_selection", "确认目标 Bot", "ok", "目标 Bot 已连接并通过操作前检查。"),
            step("adapter_call", "调用 OneBot API", "ok", "协议端已明确返回成功。"),
        ),
        suggestion="无需重复提交该操作。",
        operation_id=operation_id,
    )
    result["success"] = True
    return result


def _require_confirm(body: dict, *, expected: str, label: str) -> None:
    confirmed = str(body.get("confirm", "") or "").strip()
    if confirmed != str(expected):
        raise HTTPException(status_code=400, detail=f"危险操作需要 confirm={label}")


def build_qq_router(*, runtime) -> APIRouter:
    router = APIRouter(prefix="/api/qq", tags=["qq"])

    @router.get("/info")
    async def info(_: AdminIdentity = Depends(require_admin)) -> dict:
        bots = _bots(runtime, api="get_login_info")
        bot = next(iter(bots.values()))
        data = await _call(bot, "get_login_info")
        return {
            "user_id": str((data or {}).get("user_id", "") or getattr(bot, "self_id", "")),
            "nickname": str((data or {}).get("nickname", "") or ""),
            "bots": [{"bot_id": bot_id} for bot_id in bots],
        }

    @router.post("/nickname")
    async def set_nickname(body: dict = Body(default_factory=dict), admin: AdminIdentity = Depends(require_admin)) -> dict:
        nickname = str(body.get("nickname", "") or "").strip()
        if not nickname:
            raise HTTPException(status_code=400, detail="昵称不能为空")
        operation_id = uuid.uuid4().hex
        api = "set_qq_profile"
        bot = _bot(runtime, api=api, operation_id=operation_id)
        bot_id = _target_bot_id(bot)
        await _call(
            bot,
            api,
            operation_id=operation_id,
            side_effect=True,
            target_bot_id=bot_id,
            nickname=nickname,
        )
        webui_audit_log.record(action="qq_set_nickname", qq=admin.qq, device_id=admin.device_id, target=nickname, outcome="ok")
        return _success(
            code="qq_nickname_updated",
            title="QQ 昵称已修改",
            message="协议端已明确确认昵称更新成功。",
            bot_id=bot_id,
            api=api,
            operation_id=operation_id,
            extra_details=(detail("新昵称", nickname, "ok"),),
        )

    @router.post("/signature")
    async def set_signature(body: dict = Body(default_factory=dict), admin: AdminIdentity = Depends(require_admin)) -> dict:
        sign = str(body.get("signature", "") or "").strip()
        operation_id = uuid.uuid4().hex
        api = "set_self_longnick"
        bot = _bot(runtime, body.get("bot_id"), explicit=True, api=api, operation_id=operation_id)
        bot_id = _target_bot_id(bot, body.get("bot_id"))
        await _call(
            bot,
            api,
            operation_id=operation_id,
            side_effect=True,
            target_bot_id=bot_id,
            longNick=sign,
        )
        webui_audit_log.record(action="qq_set_signature", qq=admin.qq, device_id=admin.device_id, target=str(getattr(bot, "self_id", "")), outcome="ok")
        return _success(
            code="qq_signature_updated",
            title="QQ 签名已修改",
            message="协议端已明确确认个性签名更新成功。",
            bot_id=bot_id,
            api=api,
            operation_id=operation_id,
            extra_details=(detail("签名长度", len(sign), "ok"),),
        )

    @router.post("/avatar")
    async def set_avatar(body: dict = Body(default_factory=dict), admin: AdminIdentity = Depends(require_admin)) -> dict:
        file = str(body.get("file", "") or "").strip()  # url 或 base64://...
        if not file:
            raise HTTPException(status_code=400, detail="未提供头像（url 或 base64://）")
        operation_id = uuid.uuid4().hex
        api = "set_qq_avatar"
        bot = _bot(runtime, body.get("bot_id"), explicit=True, api=api, operation_id=operation_id)
        bot_id = _target_bot_id(bot, body.get("bot_id"))
        await _call(
            bot,
            api,
            operation_id=operation_id,
            side_effect=True,
            target_bot_id=bot_id,
            file=file,
        )
        webui_audit_log.record(action="qq_set_avatar", qq=admin.qq, device_id=admin.device_id, target=str(getattr(bot, "self_id", "")), outcome="ok")
        source_type = "base64" if file.startswith("base64://") else "remote_url" if file.startswith(("http://", "https://")) else "file_reference"
        return _success(
            code="qq_avatar_updated",
            title="QQ 头像已修改",
            message="协议端已明确确认头像更新成功。",
            bot_id=bot_id,
            api=api,
            operation_id=operation_id,
            extra_details=(detail("头像来源类型", source_type, "ok"),),
        )

    @router.get("/groups")
    async def groups(_: AdminIdentity = Depends(require_admin)) -> dict:
        data = await discover_group_union(runtime)
        items = [
            {
                "group_id": str(g.get("group_id", "")),
                "group_name": str(g.get("group_name", "")),
                "member_count": int(g.get("member_count", 0) or 0),
                "max_member_count": int(g.get("max_member_count", 0) or 0),
                "bot_self_ids": list(g.get("bot_self_ids", [])),
                "sources": list(g.get("sources", [])),
            }
            for g in data if isinstance(g, dict)
        ]
        items.sort(key=lambda x: x["member_count"], reverse=True)
        return {"groups": items}

    @router.post("/groups/{group_id}/leave")
    async def leave_group(group_id: str, body: dict = Body(default_factory=dict), admin: AdminIdentity = Depends(require_admin)) -> dict:
        _require_confirm(body, expected=str(group_id), label=str(group_id))
        raw_dismiss = body.get("is_dismiss", False)
        if not isinstance(raw_dismiss, bool):
            raise HTTPException(status_code=400, detail="is_dismiss 必须是 JSON boolean")
        is_dismiss = raw_dismiss
        operation_id = uuid.uuid4().hex
        api = "set_group_leave"
        bot = _bot(runtime, body.get("bot_id"), explicit=True, api=api, operation_id=operation_id)
        bot_id = str(getattr(bot, "self_id", "") or body.get("bot_id"))
        memberships = next((g.get("bot_self_ids", []) for g in await discover_group_union(runtime, probe_limit=0) if str(g.get("group_id")) == str(group_id)), [])
        if bot_id not in memberships:
            raise HTTPException(status_code=409, detail="目标 Bot 不在该群的已确认 membership 中")
        if is_dismiss:
            expected = f"DISMISS:{bot_id}:{group_id}"
            if body.get("dismiss_confirm") != expected:
                raise HTTPException(status_code=400, detail=f"解散群需要 dismiss_confirm={expected}")
        await _call(
            bot,
            api,
            operation_id=operation_id,
            side_effect=True,
            target_bot_id=bot_id,
            group_id=int(group_id),
            is_dismiss=is_dismiss,
        )
        webui_audit_log.record(action="qq_leave_group", qq=admin.qq, device_id=admin.device_id, target=str(group_id),
                                detail={"bot_id": bot_id, "is_dismiss": is_dismiss}, outcome="ok")
        return _success(
            code="qq_group_dismissed" if is_dismiss else "qq_group_left",
            title="QQ群已解散" if is_dismiss else "已退出 QQ 群",
            message="协议端已明确确认群操作成功。",
            bot_id=bot_id,
            api=api,
            operation_id=operation_id,
            extra_details=(
                detail("目标群", str(group_id), "ok"),
                detail("操作类型", "dismiss" if is_dismiss else "leave", "ok"),
            ),
        )

    @router.post("/group-requests/handle")
    async def handle_group_request(body: dict = Body(default_factory=dict), admin: AdminIdentity = Depends(require_admin)) -> dict:
        flag = str(body.get("flag", "") or "")
        sub_type = str(body.get("sub_type", "invite") or "invite")
        approve = bool(body.get("approve", True))
        if not flag:
            raise HTTPException(status_code=400, detail="缺少 flag")
        operation_id = uuid.uuid4().hex
        api = "set_group_add_request"
        bot = _bot(runtime, api=api, operation_id=operation_id)
        bot_id = _target_bot_id(bot)
        await _call(
            bot,
            api,
            operation_id=operation_id,
            side_effect=True,
            target_bot_id=bot_id,
            flag=flag,
            sub_type=sub_type,
            approve=approve,
            reason=str(body.get("reason", "") or ""),
        )
        webui_audit_log.record(action="qq_handle_group_request", qq=admin.qq, device_id=admin.device_id,
                               detail={"approve": approve}, outcome="ok")
        return _success(
            code="qq_group_request_handled",
            title="QQ群请求已处理",
            message="协议端已明确确认群请求处理成功。",
            bot_id=bot_id,
            api=api,
            operation_id=operation_id,
            extra_details=(
                detail("请求类型", sub_type, "info"),
                detail("处理结果", "approved" if approve else "rejected", "ok"),
            ),
        )

    @router.get("/friends")
    async def friends(_: AdminIdentity = Depends(require_admin)) -> dict:
        bot = _bot(runtime, api="get_friend_list")
        data = await _call(bot, "get_friend_list")
        items = [
            {
                "user_id": str(f.get("user_id", "")),
                "nickname": str(f.get("nickname", "")),
                "remark": str(f.get("remark", "") or ""),
            }
            for f in (data or []) if isinstance(f, dict)
        ]
        return {"friends": items, "count": len(items)}

    @router.delete("/friends/{user_id}")
    async def delete_friend(user_id: str, body: dict = Body(default_factory=dict), admin: AdminIdentity = Depends(require_admin)) -> dict:
        _require_confirm(body, expected=str(user_id), label=str(user_id))
        operation_id = uuid.uuid4().hex
        api = "delete_friend"
        bot = _bot(runtime, api=api, operation_id=operation_id)
        bot_id = _target_bot_id(bot)
        await _call(
            bot,
            api,
            operation_id=operation_id,
            side_effect=True,
            target_bot_id=bot_id,
            user_id=int(user_id),
        )
        webui_audit_log.record(action="qq_delete_friend", qq=admin.qq, device_id=admin.device_id, target=str(user_id), outcome="ok")
        return _success(
            code="qq_friend_deleted",
            title="QQ 好友已删除",
            message="协议端已明确确认好友删除成功。",
            bot_id=bot_id,
            api=api,
            operation_id=operation_id,
            extra_details=(detail("目标 QQ", str(user_id), "ok"),),
        )

    @router.post("/friend-requests/handle")
    async def handle_friend_request(body: dict = Body(default_factory=dict), admin: AdminIdentity = Depends(require_admin)) -> dict:
        flag = str(body.get("flag", "") or "")
        approve = bool(body.get("approve", True))
        if not flag:
            raise HTTPException(status_code=400, detail="缺少 flag")
        operation_id = uuid.uuid4().hex
        api = "set_friend_add_request"
        bot = _bot(runtime, api=api, operation_id=operation_id)
        bot_id = _target_bot_id(bot)
        await _call(
            bot,
            api,
            operation_id=operation_id,
            side_effect=True,
            target_bot_id=bot_id,
            flag=flag,
            approve=approve,
            remark=str(body.get("remark", "") or ""),
        )
        webui_audit_log.record(action="qq_handle_friend_request", qq=admin.qq, device_id=admin.device_id,
                               detail={"approve": approve}, outcome="ok")
        return _success(
            code="qq_friend_request_handled",
            title="QQ 好友请求已处理",
            message="协议端已明确确认好友请求处理成功。",
            bot_id=bot_id,
            api=api,
            operation_id=operation_id,
            extra_details=(detail("处理结果", "approved" if approve else "rejected", "ok"),),
        )

    return router
