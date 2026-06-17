from __future__ import annotations

import asyncio
import random
import time
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from ..deps import AdminIdentity, require_admin

_INTERACTION_WAIT_SECONDS = 75


def _first_bot(runtime) -> Any | None:
    try:
        bots = runtime.get_bots() if callable(getattr(runtime, "get_bots", None)) else {}
    except Exception:
        return None
    return next(iter(bots.values()), None) if bots else None


# 这些 OneBot 动作都是"对外发消息"，交互测试需要全部捕获，
# 否则拟人化发送层若走 send_group_msg / 转发消息 / call_api 等非 bot.send 路径，
# 测试会误判为"未回复"，而真实聊天里 bot 其实已经发言。
_SEND_API_ACTIONS = frozenset(
    {
        "send_msg",
        "send_group_msg",
        "send_private_msg",
        "send_group_forward_msg",
        "send_private_forward_msg",
        "send_forward_msg",
    }
)


class _CapturingBot:
    """透传真实 bot（消息真的发到 QQ），同时捕获各种发送路径的内容用于回显。

    覆盖 send / send_*_msg / 转发 / call_api，确保无论回复链路用哪种发送方式
    （含拟人化发送层、多段、图片、表情、转发）都能被捕获并回显。
    """

    def __init__(self, real: Any, *, trace_id: str = "") -> None:
        self._real = real
        self.captured: list[str] = []
        self.self_id = getattr(real, "self_id", "")
        self.trace_id = trace_id

    def _record_attempt(self, detail: str) -> None:
        from ...core import reply_turn_trace

        reply_turn_trace.record_stage(
            trace_id=self.trace_id,
            key="send_attempt",
            label="发送回复",
            status="info",
            detail=str(detail)[:500],
        )

    def _record_outcome(self, *, ok: bool, detail: str) -> None:
        from ...core import reply_turn_trace

        if ok:
            reply_turn_trace.record_stage(
                trace_id=self.trace_id, key="send_success", label="发送成功",
                status="ok", detail=str(detail)[:500],
            )
        else:
            reply_turn_trace.record_stage(
                trace_id=self.trace_id, key="send_failed", label="发送失败",
                status="error", detail=str(detail)[:500],
                hint="检查协议端发送权限、好友/群关系、账号风控或消息格式",
            )

    def _capture(self, message: Any) -> None:
        try:
            text = str(message)
        except Exception:
            return
        if text:
            self.captured.append(text)

    async def send(self, event: Any, message: Any, **kwargs: Any) -> Any:
        self._record_attempt(message)
        try:
            result = await self._real.send(event, message, **kwargs)
        except Exception as exc:
            self._record_outcome(ok=False, detail=str(exc))
            raise
        self._capture(message)
        self._record_outcome(ok=True, detail=result)
        return result

    async def call_api(self, api: str, **data: Any) -> Any:
        action = str(api or "").strip()
        if action in _SEND_API_ACTIONS:
            self._record_attempt(data.get("message", data))
        try:
            result = await self._real.call_api(api, **data)
        except Exception as exc:
            if action in _SEND_API_ACTIONS:
                self._record_outcome(ok=False, detail=str(exc))
            raise
        if action in _SEND_API_ACTIONS:
            self._capture(data.get("message", ""))
            self._record_outcome(ok=True, detail=result)
        return result

    async def _send_via_action(self, action: str, message: Any, **data: Any) -> Any:
        self._record_attempt(message)
        try:
            result = await getattr(self._real, action)(message=message, **data)
        except Exception as exc:
            self._record_outcome(ok=False, detail=str(exc))
            raise
        self._capture(message)
        self._record_outcome(ok=True, detail=result)
        return result

    async def send_msg(self, *, message: Any = "", **data: Any) -> Any:
        return await self._send_via_action("send_msg", message, **data)

    async def send_group_msg(self, *, message: Any = "", **data: Any) -> Any:
        return await self._send_via_action("send_group_msg", message, **data)

    async def send_private_msg(self, *, message: Any = "", **data: Any) -> Any:
        return await self._send_via_action("send_private_msg", message, **data)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)


def _build_probe_event(bot: Any, *, group_id: str, user_id: str, text: str) -> Any:
    from nonebot.adapters.onebot.v11 import (
        GroupMessageEvent,
        Message,
        MessageSegment,
        PrivateMessageEvent,
    )
    from nonebot.adapters.onebot.v11.event import Sender

    self_id = int(getattr(bot, "self_id", 0) or 0)
    if group_id:
        # 群聊探测显式 @bot：与真实"直呼 bot"一致，确保被判为定向消息、
        # 立即处理（不进随机插话/批延迟），避免被语义帧判为"非对我说"而沉默。
        msg = MessageSegment.at(self_id) + MessageSegment.text(" " + text)
        raw_message = f"[CQ:at,qq={self_id}] {text}"
    else:
        msg = Message(text)
        raw_message = text
    common = dict(
        time=int(time.time()),
        self_id=self_id,
        post_type="message",
        message_id=random.randint(1, 2_000_000_000),
        user_id=int(user_id),
        message=msg,
        original_message=msg,
        raw_message=raw_message,
        font=0,
        sender=Sender(user_id=int(user_id), nickname="功能自检"),
        to_me=True,
    )
    if group_id:
        return GroupMessageEvent(
            message_type="group", sub_type="normal", group_id=int(group_id), anonymous=None, **common
        )
    return PrivateMessageEvent(message_type="private", sub_type="friend", **common)


def _stage(stages: list[dict[str, Any]], trace_id: str, key: str, label: str, status: str, detail: str = "", hint: str = "") -> None:
    from ...core import reply_turn_trace

    item = {"key": key, "label": label, "status": status, "detail": detail, "hint": hint}
    stages.append(item)
    reply_turn_trace.record_stage(
        trace_id=trace_id,
        key=key,
        label=label,
        status=status,
        detail=detail,
        hint=hint,
    )


def _finish_payload(
    *,
    trace_id: str,
    stages: list[dict[str, Any]],
    replied: bool,
    target: str,
    reply: str = "",
    detail: str,
    diagnosis_code: str,
    duration_ms: int = 0,
    target_detail: dict[str, str] | None = None,
) -> dict:
    from ...core import reply_turn_trace

    if replied:
        outcome = "ok"
    elif diagnosis_code in {"no_reply", "model_empty", "stale_reply", "capture_timeout"}:
        outcome = "no_reply"
    else:
        outcome = "failed"
    reply_turn_trace.finish_trace(
        trace_id=trace_id,
        outcome=outcome,
        diagnosis_code=diagnosis_code,
        detail={"detail": detail, "target": target, **(target_detail or {})},
    )
    return {
        "replied": replied,
        "duration_ms": duration_ms,
        "target": target,
        "reply": reply[:2000],
        "detail": detail,
        "trace_id": trace_id,
        "diagnosis_code": diagnosis_code,
        "stages": stages,
        "target_detail": target_detail or {},
        "last_trace": reply_turn_trace.get_trace(trace_id),
    }


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
        from ...core import reply_turn_trace

        cfg = getattr(runtime, "plugin_config", None)
        target = str(body.get("target", "") or "").strip()  # "group" | "private"
        group_id = str(getattr(cfg, "personification_webui_test_group_id", "") or "").strip()
        user_id = str(getattr(cfg, "personification_webui_test_user_id", "") or "").strip()
        text = str(body.get("text", "") or "").strip() or "（功能自检）你好呀，简单回复一句就行"
        target_label = "group" if target == "group" else "private"
        stages: list[dict[str, Any]] = []

        if target == "group":
            if not group_id:
                raise HTTPException(status_code=400, detail="未配置测试群（personification_webui_test_group_id）")
            probe_user = user_id or str(admin.qq)
            target_group, target_user = group_id, probe_user
        else:
            if not user_id:
                raise HTTPException(status_code=400, detail="未配置测试私聊用户（personification_webui_test_user_id）")
            target_group, target_user = "", user_id

        trace_id = reply_turn_trace.start_trace(
            session_type=target_label,
            group_id=target_group,
            user_id=target_user,
            detail={"source": "webui_interaction_test", "text": text[:200]},
        )
        target_detail = {"group_id": target_group, "user_id": target_user}
        _stage(stages, trace_id, "runtime_ready", "运行时", "ok" if cfg is not None else "error",
               "运行时配置已就绪" if cfg is not None else "运行时配置缺失")
        if cfg is None:
            return _finish_payload(
                trace_id=trace_id,
                stages=stages,
                replied=False,
                target=target_label,
                detail="运行时未就绪，无法构造交互测试。",
                diagnosis_code="runtime_unavailable",
                target_detail=target_detail,
            )

        bot = _first_bot(runtime)
        if bot is None:
            _stage(stages, trace_id, "bot_connected", "Bot 连接", "error", "Bot 未连接", "检查 OneBot 连接、反向 WebSocket、协议端状态")
            return _finish_payload(
                trace_id=trace_id,
                stages=stages,
                replied=False,
                target=target_label,
                detail="Bot 未连接。",
                diagnosis_code="bot_not_connected",
                target_detail=target_detail,
            )
        _stage(stages, trace_id, "bot_connected", "Bot 连接", "ok",
               f"self_id={getattr(bot, 'self_id', '') or 'unknown'}")

        if target_group:
            try:
                from ...utils import is_group_whitelisted

                enabled = is_group_whitelisted(str(target_group), list(getattr(cfg, "personification_whitelist", []) or []))
            except Exception as exc:
                enabled = False
                _stage(stages, trace_id, "group_whitelist", "群白名单", "warn", f"检查失败：{exc}",
                       "检查 data_store / 群配置是否可读")
            else:
                _stage(stages, trace_id, "group_whitelist", "群白名单", "ok" if enabled else "error",
                       "目标群已启用拟人回复" if enabled else "目标群未启用拟人回复",
                       "" if enabled else "在 WebUI「群开关」启用测试群，或配置 personification_whitelist")
                if not enabled:
                    return _finish_payload(
                        trace_id=trace_id,
                        stages=stages,
                        replied=False,
                        target=target_label,
                        detail="目标群未启用拟人回复，群聊测试不会进入回复链路。",
                        diagnosis_code="not_whitelisted",
                        target_detail=target_detail,
                    )
            try:
                from ...core.group_mute import refresh_bot_group_mute_state

                muted = await refresh_bot_group_mute_state(bot, str(target_group), logger=getattr(runtime, "logger", None))
            except Exception as exc:
                muted = False
                _stage(stages, trace_id, "group_mute", "Bot 禁言", "warn", f"禁言状态查询失败：{exc}",
                       "协议端不支持 get_group_member_info 时会回退缓存")
            else:
                _stage(stages, trace_id, "group_mute", "Bot 禁言", "error" if muted else "ok",
                       "Bot 当前在目标群被禁言" if muted else "未检测到 Bot 被禁言")
                if muted:
                    return _finish_payload(
                        trace_id=trace_id,
                        stages=stages,
                        replied=False,
                        target=target_label,
                        detail="Bot 在测试群被禁言，无法发送回复。",
                        diagnosis_code="bot_muted",
                        target_detail=target_detail,
                    )
        else:
            call_api = getattr(bot, "call_api", None)
            if callable(call_api):
                try:
                    friends = await call_api("get_friend_list")
                    friend_ids = {str(item.get("user_id", "")) for item in list(friends or []) if isinstance(item, dict)}
                    if friend_ids:
                        ok = str(target_user) in friend_ids
                        _stage(stages, trace_id, "friend_relation", "好友关系", "ok" if ok else "warn",
                               "目标用户在好友列表中" if ok else "目标用户不在 get_friend_list 返回中",
                               "" if ok else "若协议端好友列表不完整可忽略；否则检查是否已添加好友")
                    else:
                        _stage(stages, trace_id, "friend_relation", "好友关系", "info", "协议端返回空好友列表")
                except Exception as exc:
                    _stage(stages, trace_id, "friend_relation", "好友关系", "warn", f"好友列表查询失败：{exc}",
                           "协议端不支持 get_friend_list 时可忽略；若私聊失败则检查好友关系")

        try:
            from nonebot.message import handle_event

            event = _build_probe_event(bot, group_id=target_group, user_id=target_user, text=text)
        except Exception as exc:
            _stage(stages, trace_id, "event_build", "构造事件", "error", str(exc),
                   "检查测试群号/QQ 是否为纯数字，以及 OneBot v11 依赖是否正常")
            return _finish_payload(
                trace_id=trace_id,
                stages=stages,
                replied=False,
                target=target_label,
                detail=f"构造测试事件失败：{exc}",
                diagnosis_code="event_build_failed",
                target_detail=target_detail,
            )
        _stage(stages, trace_id, "event_build", "构造事件", "ok",
               f"message_type={getattr(event, 'message_type', '')}, to_me={getattr(event, 'to_me', None)}")

        proxy = _CapturingBot(bot, trace_id=trace_id)
        started = time.monotonic()
        token = reply_turn_trace.set_current_trace_id(trace_id)
        try:
            _stage(stages, trace_id, "dispatch_start", "分发事件", "info", "开始调用 nonebot.message.handle_event")
            await asyncio.wait_for(handle_event(proxy, event), timeout=_INTERACTION_WAIT_SECONDS)
        except asyncio.TimeoutError:
            _stage(stages, trace_id, "dispatch_timeout", "分发事件", "warn",
                   f"handle_event 超过 {_INTERACTION_WAIT_SECONDS}s 未返回",
                   "通常是模型调用或回复链路较慢，继续等待 send 捕获")
        except Exception as exc:
            _stage(stages, trace_id, "dispatch_failed", "分发事件", "error", str(exc)[:500],
                   "查看插件日志中同 trace_id 的异常")
            reply_turn_trace.reset_current_trace_id(token)
            return _finish_payload(
                trace_id=trace_id,
                stages=stages,
                replied=False,
                target=target_label,
                detail=f"分发事件失败：{exc}",
                diagnosis_code="internal_exception",
                duration_ms=int((time.monotonic() - started) * 1000),
                target_detail=target_detail,
            )
        finally:
            try:
                reply_turn_trace.reset_current_trace_id(token)
            except Exception:
                pass

        # 回复经缓冲/模型，可能在 handle_event 返回后才产生，轮询等待
        deadline = started + _INTERACTION_WAIT_SECONDS
        while not proxy.captured and time.monotonic() < deadline:
            await asyncio.sleep(0.5)
        ms = int((time.monotonic() - started) * 1000)
        replied = bool(proxy.captured)
        if replied:
            _stage(stages, trace_id, "capture_reply", "捕获回复", "ok", f"捕获 {len(proxy.captured)} 条 send")
            return _finish_payload(
                trace_id=trace_id,
                stages=stages,
                replied=True,
                target=target_label,
                reply="\n".join(proxy.captured),
                detail=f"已在{'测试群 ' + target_group if target_group else '私聊 ' + target_user}收到 bot 回复",
                diagnosis_code="ok",
                duration_ms=ms,
                target_detail=target_detail,
            )
        last_trace = reply_turn_trace.get_trace(trace_id) or {}
        diagnosis = str(last_trace.get("diagnosis_code") or "") or "capture_timeout"
        if diagnosis == "ok":
            diagnosis = "capture_timeout"
        _stage(stages, trace_id, "capture_reply", "捕获回复", "error",
               "未捕获到 bot.send",
               "查看上方阶段和插件日志；常见原因是 NO_REPLY、规则未进入、模型超时或发送失败")
        return _finish_payload(
            trace_id=trace_id,
            stages=stages,
            replied=False,
            target=target_label,
            detail="未捕获到回复：已完成分层诊断，请查看 stages / last_trace / 插件日志。",
            diagnosis_code=diagnosis,
            duration_ms=ms,
            target_detail=target_detail,
        )

    return router
