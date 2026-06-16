from __future__ import annotations

import asyncio
import random
import time
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from ..deps import AdminIdentity, require_admin

_INTERACTION_WAIT_SECONDS = 45


def _first_bot(runtime) -> Any | None:
    try:
        bots = runtime.get_bots() if callable(getattr(runtime, "get_bots", None)) else {}
    except Exception:
        return None
    return next(iter(bots.values()), None) if bots else None


class _CapturingBot:
    """透传真实 bot（消息真的发到 QQ），同时捕获 send 的内容用于回显。"""

    def __init__(self, real: Any) -> None:
        self._real = real
        self.captured: list[str] = []
        self.self_id = getattr(real, "self_id", "")

    async def send(self, event: Any, message: Any, **kwargs: Any) -> Any:
        try:
            self.captured.append(str(message))
        except Exception:
            pass
        return await self._real.send(event, message, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)


def _build_probe_event(bot: Any, *, group_id: str, user_id: str, text: str) -> Any:
    from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, PrivateMessageEvent
    from nonebot.adapters.onebot.v11.event import Sender

    msg = Message(text)
    common = dict(
        time=int(time.time()),
        self_id=int(getattr(bot, "self_id", 0) or 0),
        post_type="message",
        message_id=random.randint(1, 2_000_000_000),
        user_id=int(user_id),
        message=msg,
        original_message=msg,
        raw_message=text,
        font=0,
        sender=Sender(user_id=int(user_id), nickname="功能自检"),
        to_me=True,
    )
    if group_id:
        return GroupMessageEvent(
            message_type="group", sub_type="normal", group_id=int(group_id), anonymous=None, **common
        )
    return PrivateMessageEvent(message_type="private", sub_type="friend", **common)


def build_health_router(*, runtime) -> APIRouter:
    router = APIRouter(prefix="/api/health", tags=["health"])

    @router.get("/check")
    async def check(
        only: str = Query(default=""),
        refresh: bool = Query(default=False),
        _: AdminIdentity = Depends(require_admin),
    ) -> dict:
        from ...core.diagnostics import get_cached_diagnostics, run_diagnostics

        # 默认返回缓存（秒开）；only=单项 或 refresh=true 时才真实重跑
        if not only and not refresh:
            cached = get_cached_diagnostics()
            if cached is not None:
                return {**cached, "cached": True}
        result = await run_diagnostics(
            plugin_config=getattr(runtime, "plugin_config", None),
            bundle=getattr(runtime, "runtime_bundle", None),
            superusers=getattr(runtime, "superusers", set()),
            get_bots=getattr(runtime, "get_bots", None),
            logger=getattr(runtime, "logger", None),
            only=only.strip(),
        )
        return {**result, "cached": False}

    @router.post("/interaction-test")
    async def interaction_test(
        body: dict = Body(default_factory=dict),
        admin: AdminIdentity = Depends(require_admin),
    ) -> dict:
        """向配置的测试群/私聊真实注入一条消息，走完整回复链路并回显 bot 实际回复。"""
        cfg = getattr(runtime, "plugin_config", None)
        target = str(body.get("target", "") or "").strip()  # "group" | "private"
        group_id = str(getattr(cfg, "personification_webui_test_group_id", "") or "").strip()
        user_id = str(getattr(cfg, "personification_webui_test_user_id", "") or "").strip()
        text = str(body.get("text", "") or "").strip() or "（功能自检）你好呀，简单回复一句就行"

        if target == "group":
            if not group_id:
                raise HTTPException(status_code=400, detail="未配置测试群（personification_webui_test_group_id）")
            probe_user = user_id or str(admin.qq)
            target_group, target_user = group_id, probe_user
        else:
            if not user_id:
                raise HTTPException(status_code=400, detail="未配置测试私聊用户（personification_webui_test_user_id）")
            target_group, target_user = "", user_id

        bot = _first_bot(runtime)
        if bot is None:
            raise HTTPException(status_code=503, detail="Bot 未连接")

        try:
            from nonebot.message import handle_event

            event = _build_probe_event(bot, group_id=target_group, user_id=target_user, text=text)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"构造测试事件失败：{exc}") from exc

        proxy = _CapturingBot(bot)
        started = time.monotonic()
        try:
            await asyncio.wait_for(handle_event(proxy, event), timeout=_INTERACTION_WAIT_SECONDS)
        except asyncio.TimeoutError:
            pass
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"分发事件失败：{exc}") from exc

        # 回复经缓冲/模型，可能在 handle_event 返回后才产生，轮询等待
        deadline = started + _INTERACTION_WAIT_SECONDS
        while not proxy.captured and time.monotonic() < deadline:
            await asyncio.sleep(0.5)
        ms = int((time.monotonic() - started) * 1000)
        replied = bool(proxy.captured)
        return {
            "replied": replied,
            "duration_ms": ms,
            "target": "group" if target_group else "private",
            "reply": "\n".join(proxy.captured)[:2000],
            "detail": (
                f"已在{'测试群 ' + target_group if target_group else '私聊 ' + target_user}收到 bot 回复"
                if replied else
                "未捕获到回复：可能被 NO_REPLY/白名单/概率拦截，或模型超时。详见日志。"
            ),
        }

    return router
