from __future__ import annotations

from secrets import token_urlsafe
from typing import Any

from nonebot.adapters.onebot.v11 import MessageSegment

from ..core.qq_outbound import QQOutboundLedger, SendReceipt, build_outbound_context, parse_onebot_message_id
from ..core.qq_recall import QQRecallService
from ..core.visible_output import guard_visible_text
from ..core.expression_policy import expression_action_allowed
from ..core.expression_policy import expression_source_enabled
from ..core.expression_preparation import prepare_local_expression
from ..core.qq_expression_library import review_native_qq_expression
from ..core.qq_face_names import QQ_FACE_NAMES
from ..core.response_review import final_dialogue_gate


class ActionExecutor:
    def __init__(
        self,
        bot: Any,
        event: Any,
        config: Any,
        logger: Any,
        *,
        qq_outbound_ledger: Any | None = None,
        operation_id: str | None = None,
        user_target: str | None = None,
        qq_recall_service: QQRecallService | None = None,
        recall_cutoff: float | None = None,
        core_persona: str = "",
        expression_review_caller: Any = None,
        runtime: Any = None,
    ) -> None:
        self.bot = bot
        self.event = event
        self._config = config
        self.logger = logger
        self.qq_outbound_ledger = qq_outbound_ledger
        self.operation_id = str(operation_id or "").strip()
        self.user_target = str(user_target or "").strip()
        self.qq_recall_service = qq_recall_service
        if self.qq_recall_service is None and isinstance(qq_outbound_ledger, QQOutboundLedger):
            self.qq_recall_service = QQRecallService(
                qq_outbound_ledger,
                plugin_config=config,
                logger=logger,
            )
        self.recall_cutoff = float(recall_cutoff or 0.0)
        self.core_persona = str(core_persona or "").strip()
        self.expression_review_caller = expression_review_caller
        self.runtime = runtime
        self.pending_actions: list[dict[str, Any]] = []
        # Remote expression bytes are capability data, not model-supplied
        # action parameters.  A queued action only carries this opaque token;
        # the source and frozen bytes remain in this executor's private map.
        self._remote_expression_payloads: dict[str, dict[str, str]] = {}
        self.last_delivery_confirmed = False
        self.last_recall_result: Any = None
        self.receipts: list[SendReceipt] = []

    @property
    def config(self) -> Any:
        return getattr(self.runtime, "plugin_config", None) or self._config

    async def _send(self, message: Any, *, surface: str) -> None:
        if self.qq_outbound_ledger is None:
            result = await self.bot.send(self.event, message)
        else:
            context = build_outbound_context(
                bot=self.bot,
                event=self.event,
                surface=surface,
                operation_id=self.operation_id,
                user_target=self.user_target,
            )
            self.operation_id = context.operation_id
            receipt = await self.qq_outbound_ledger.dispatch(
                context,
                message,
                lambda: self.bot.send(self.event, message),
            )
            self.receipts.append(receipt)
            self.last_delivery_confirmed = receipt.status == "sent"
            return
        self.last_delivery_confirmed = parse_onebot_message_id(result) is not None

    def bind_pending_actions(self, actions: list[dict[str, Any]]) -> None:
        self.pending_actions = actions

    def queue_action(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
        item = {"type": str(action or "").strip(), "params": dict(params or {})}
        self.pending_actions.append(item)
        return item

    def queue_remote_expression(
        self,
        *,
        image_ref: str,
        source: str,
        text: str = "",
        summary: str = "",
        history_text: str = "",
    ) -> bool:
        normalized_source = str(source or "").strip().lower()
        if normalized_source not in {"qq_favorite", "qq_recommended"} or not str(image_ref or "").startswith("data:image/"):
            return False
        token = token_urlsafe(24)
        self._remote_expression_payloads[token] = {
            "image_ref": str(image_ref),
            "source": normalized_source,
        }
        self.queue_action(
            "send_qq_image_expression",
            {
                "expression_token": token,
                "text": str(text or ""),
                "summary": str(summary or ""),
                "history_text": str(history_text or ""),
            },
        )
        return True

    async def send_text(self, text: str) -> None:
        content = guard_visible_text(text, logger=self.logger, surface="agent_action_text", allow_direct_media=False)
        content = await self._review_visible_text(content)
        if content:
            await self._send(content, surface="agent_action_text")

    async def _review_visible_text(self, text: Any) -> str:
        """Fail closed before an Agent-visible bubble or caption is delivered."""
        candidate = str(text or "").strip()
        caller = self.expression_review_caller
        if not candidate or not self.core_persona or not callable(caller):
            return ""
        try:
            decision = await final_dialogue_gate(
                caller,
                candidate_text=candidate,
                raw_message_text=str(getattr(self.event, "get_plaintext", lambda: "")() or ""),
                core_persona=self.core_persona,
                response_deadline=self.recall_cutoff + 180 if self.recall_cutoff else None,
                timeout_seconds=8.0,
            )
        except Exception:
            return ""
        return str(getattr(decision, "text", "") or "").strip() if str(getattr(decision, "action", "") or "") == "accept" else ""

    async def send_image_b64(self, image_b64: str) -> None:
        payload = str(image_b64 or "").strip()
        if payload:
            await self._send(
                MessageSegment.image(f"base64://{payload}"),
                surface="agent_action_image",
            )

    async def execute(self, action: str, params: dict) -> str:
        self.last_delivery_confirmed = False
        if not expression_action_allowed(self.config, action, params):
            return "表情发送已被当前来源开关拦截"
        match action:
            case "recall_latest_qq_operation":
                if self.qq_recall_service is None:
                    return "撤回能力不可用"
                self.last_recall_result = await self.qq_recall_service.recall_latest(
                    bot=self.bot,
                    event=self.event,
                    requester_user_id=str(getattr(self.event, "user_id", "") or self.user_target),
                    actor_kind="user",
                    cutoff=self.recall_cutoff or None,
                    current_operation_id=self.operation_id,
                )
                return f"撤回结果：{self.last_recall_result.status}"
            case "send_sticker":
                image_ref = await prepare_local_expression(
                    path=str(params.get("path", "") or ""),
                    config=self.config,
                    core_persona=self.core_persona,
                    runtime=self.runtime,
                    context=str(getattr(self.event, "get_plaintext", lambda: "")() or ""),
                    group_id=getattr(self.event, "group_id", None),
                )
                if not image_ref:
                    return "本地表情人格语义审阅未通过"
                await self._send(
                    MessageSegment.image("base64://" + image_ref.split(",", 1)[1]),
                    surface="agent_action_sticker",
                )
                return "已发送表情包"
            case "send_qq_face":
                try:
                    face_id = int(params["face_id"])
                except (KeyError, TypeError, ValueError):
                    return "QQ 表情发送失败：缺少有效表情编号"
                face_name = QQ_FACE_NAMES.get(face_id)
                if not face_name:
                    return "QQ 表情发送失败：未知协议表情"
                if not await review_native_qq_expression(
                    face_id=face_id,
                    label=face_name,
                    core_persona=self.core_persona,
                    runtime=self.runtime,
                    context=str(getattr(self.event, "get_plaintext", lambda: "")() or ""),
                ):
                    return "QQ 表情人格语义审阅未通过"
                if not expression_source_enabled(self.config, "native"):
                    return "表情发送已被当前来源开关拦截"
                raw_caption = guard_visible_text(params.get("text", ""), logger=self.logger, surface="qq_face_caption", allow_direct_media=False)
                text = await self._review_visible_text(raw_caption)
                if raw_caption and not text:
                    return "QQ 表情文案人格审阅未通过"
                message = MessageSegment.face(face_id)
                if text:
                    message += text
                await self._send(message, surface="agent_action_qq_expression")
                return "已发送 QQ 表情"
            case "send_qq_image_expression":
                token = str(params.get("expression_token", "") or "").strip()
                payload = self._remote_expression_payloads.pop(token, None)
                if not isinstance(payload, dict):
                    return "QQ 表情发送失败：缺少受控图片凭据"
                source = str(payload.get("source", "") or "")
                image_ref = str(payload.get("image_ref", "") or "")
                # Reloaded policy must still permit the program-assigned
                # source when a tool call was queued before a hot change.
                if not expression_source_enabled(self.config, source) or not image_ref.startswith("data:image/"):
                    return "QQ 表情发送已被当前来源开关拦截"
                raw_caption = guard_visible_text(params.get("text", ""), logger=self.logger, surface="qq_image_caption", allow_direct_media=False)
                text = await self._review_visible_text(raw_caption)
                if raw_caption and not text:
                    return "QQ 图片表情文案人格审阅未通过"
                message = MessageSegment.image("base64://" + image_ref.split(",", 1)[1])
                if text:
                    message += text
                await self._send(message, surface="agent_action_qq_expression")
                return "已发送 QQ 图片表情"
            case "send_image_url":
                url = str(params.get("url", "") or "").strip()
                if not url:
                    return "图片发送失败：缺少图片 URL"
                raw_caption = guard_visible_text(params.get("text", ""), logger=self.logger, surface="image_caption", allow_direct_media=False)
                text = await self._review_visible_text(raw_caption)
                if raw_caption and not text:
                    return "图片文案人格审阅未通过"
                message = MessageSegment.image(url)
                if text:
                    message += text
                await self._send(message, surface="agent_action_image")
                return "已发送图片"
            case "send_qq_mface":
                data = params.get("data") if isinstance(params, dict) else {}
                if not isinstance(data, dict) or not data:
                    return "QQ mface 发送失败：缺少 mface 数据"
                raw_caption = guard_visible_text(params.get("text", ""), logger=self.logger, surface="mface_caption", allow_direct_media=False)
                text = await self._review_visible_text(raw_caption)
                if raw_caption and not text:
                    return "QQ mface 文案人格审阅未通过"
                message = MessageSegment("mface", data)
                if text:
                    message += text
                await self._send(message, surface="agent_action_qq_expression")
                return "已发送 QQ mface 表情"
            case "poke_user":
                await self._send(
                    MessageSegment("poke", {"qq": params["user_id"]}),
                    surface="agent_action_poke",
                )
                return "已戳"
            case _:
                self.logger.warning(f"[executor] unknown action: {action}")
                return f"未知 action: {action}"
