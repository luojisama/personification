"""WebUI QQ 账号管理：改资料/头像、群管理（退群/处理邀请）、好友管理（删/处理请求）。

均为管理员操作；写操作记审计。底层调用 OneBot v11 + NapCat 扩展 API，
不支持的 API 捕获异常返回友好错误（不同协议端能力不同）。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from ...core import webui_audit_log
from ...core.group_directory import discover_group_union
from ..deps import AdminIdentity, require_admin


def _bots(runtime) -> dict[str, Any]:
    try:
        bots = runtime.get_bots() if callable(getattr(runtime, "get_bots", None)) else {}
    except Exception:
        bots = {}
    result = {str(getattr(bot, "self_id", "") or key): bot for key, bot in bots.items()} if isinstance(bots, dict) else {}
    if not result:
        raise HTTPException(status_code=503, detail="Bot 未连接")
    return result


def _bot(runtime, bot_id: Any = None, *, explicit: bool = False) -> Any:
    bots = _bots(runtime)
    selected = str(bot_id or "").strip()
    if explicit and not selected:
        raise HTTPException(status_code=400, detail="缺少 bot_id")
    if not selected:
        return next(iter(bots.values()))
    if selected not in bots:
        raise HTTPException(status_code=404, detail="目标 Bot 未连接")
    return bots[selected]


async def _call(bot: Any, api: str, **kwargs: Any) -> Any:
    try:
        return await bot.call_api(api, **kwargs)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"协议端不支持或调用失败（{api}）：{str(exc)[:160]}") from exc


def _require_confirm(body: dict, *, expected: str, label: str) -> None:
    confirmed = str(body.get("confirm", "") or "").strip()
    if confirmed != str(expected):
        raise HTTPException(status_code=400, detail=f"危险操作需要 confirm={label}")


def build_qq_router(*, runtime) -> APIRouter:
    router = APIRouter(prefix="/api/qq", tags=["qq"])

    @router.get("/info")
    async def info(_: AdminIdentity = Depends(require_admin)) -> dict:
        bots = _bots(runtime)
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
        bot = _bot(runtime)
        await _call(bot, "set_qq_profile", nickname=nickname)
        webui_audit_log.record(action="qq_set_nickname", qq=admin.qq, device_id=admin.device_id, target=nickname, outcome="ok")
        return {"success": True}

    @router.post("/signature")
    async def set_signature(body: dict = Body(default_factory=dict), admin: AdminIdentity = Depends(require_admin)) -> dict:
        sign = str(body.get("signature", "") or "").strip()
        bot = _bot(runtime, body.get("bot_id"), explicit=True)
        await _call(bot, "set_self_longnick", longNick=sign)
        webui_audit_log.record(action="qq_set_signature", qq=admin.qq, device_id=admin.device_id, target=str(getattr(bot, "self_id", "")), outcome="ok")
        return {"success": True}

    @router.post("/avatar")
    async def set_avatar(body: dict = Body(default_factory=dict), admin: AdminIdentity = Depends(require_admin)) -> dict:
        file = str(body.get("file", "") or "").strip()  # url 或 base64://...
        if not file:
            raise HTTPException(status_code=400, detail="未提供头像（url 或 base64://）")
        bot = _bot(runtime, body.get("bot_id"), explicit=True)
        await _call(bot, "set_qq_avatar", file=file)
        webui_audit_log.record(action="qq_set_avatar", qq=admin.qq, device_id=admin.device_id, target=str(getattr(bot, "self_id", "")), outcome="ok")
        return {"success": True}

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
        bot = _bot(runtime, body.get("bot_id"), explicit=True)
        bot_id = str(getattr(bot, "self_id", "") or body.get("bot_id"))
        memberships = next((g.get("bot_self_ids", []) for g in await discover_group_union(runtime, probe_limit=0) if str(g.get("group_id")) == str(group_id)), [])
        if bot_id not in memberships:
            raise HTTPException(status_code=409, detail="目标 Bot 不在该群的已确认 membership 中")
        if is_dismiss:
            expected = f"DISMISS:{bot_id}:{group_id}"
            if body.get("dismiss_confirm") != expected:
                raise HTTPException(status_code=400, detail=f"解散群需要 dismiss_confirm={expected}")
        await _call(bot, "set_group_leave", group_id=int(group_id), is_dismiss=is_dismiss)
        webui_audit_log.record(action="qq_leave_group", qq=admin.qq, device_id=admin.device_id, target=str(group_id),
                                detail={"bot_id": bot_id, "is_dismiss": is_dismiss}, outcome="ok")
        return {"success": True}

    @router.post("/group-requests/handle")
    async def handle_group_request(body: dict = Body(default_factory=dict), admin: AdminIdentity = Depends(require_admin)) -> dict:
        flag = str(body.get("flag", "") or "")
        sub_type = str(body.get("sub_type", "invite") or "invite")
        approve = bool(body.get("approve", True))
        if not flag:
            raise HTTPException(status_code=400, detail="缺少 flag")
        bot = _bot(runtime)
        await _call(bot, "set_group_add_request", flag=flag, sub_type=sub_type, approve=approve,
                    reason=str(body.get("reason", "") or ""))
        webui_audit_log.record(action="qq_handle_group_request", qq=admin.qq, device_id=admin.device_id,
                               detail={"approve": approve}, outcome="ok")
        return {"success": True}

    @router.get("/friends")
    async def friends(_: AdminIdentity = Depends(require_admin)) -> dict:
        bot = _bot(runtime)
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
        bot = _bot(runtime)
        await _call(bot, "delete_friend", user_id=int(user_id))
        webui_audit_log.record(action="qq_delete_friend", qq=admin.qq, device_id=admin.device_id, target=str(user_id), outcome="ok")
        return {"success": True}

    @router.post("/friend-requests/handle")
    async def handle_friend_request(body: dict = Body(default_factory=dict), admin: AdminIdentity = Depends(require_admin)) -> dict:
        flag = str(body.get("flag", "") or "")
        approve = bool(body.get("approve", True))
        if not flag:
            raise HTTPException(status_code=400, detail="缺少 flag")
        bot = _bot(runtime)
        await _call(bot, "set_friend_add_request", flag=flag, approve=approve, remark=str(body.get("remark", "") or ""))
        webui_audit_log.record(action="qq_handle_friend_request", qq=admin.qq, device_id=admin.device_id,
                               detail={"approve": approve}, outcome="ok")
        return {"success": True}

    return router
