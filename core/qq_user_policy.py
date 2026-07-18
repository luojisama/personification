from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from dataclasses import dataclass, replace
from typing import Any

from .policy_classifier import PolicyEventInput
from .user_policy import PolicyAssessment, PolicyAuthorization, UserPolicyService


QQ_POLICY_ALLOW = "allow"
QQ_POLICY_SILENT = "silent"
QQ_POLICY_DIRECT_CLOSURE = "direct_closure"
QQ_POLICY_DIRECT_CLOSURE_CANDIDATE = "direct_closure_candidate"


class QQPolicyBlockedDuringTurn(RuntimeError):
    pass


@dataclass(frozen=True)
class QQPolicyDecision:
    user_id: str
    event_key: str
    surface: str
    channel_key: str
    direct: bool
    disposition: str
    assessment: PolicyAssessment
    authorization: PolicyAuthorization

    @property
    def allow_normal_processing(self) -> bool:
        return self.disposition == QQ_POLICY_ALLOW and not self.authorization.blocked

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "event_key": self.event_key,
            "surface": self.surface,
            "channel_key": self.channel_key,
            "direct": self.direct,
            "disposition": self.disposition,
            "verdict": self.assessment.verdict,
            "category": self.assessment.category,
            "intent": self.assessment.intent,
            "severity": self.assessment.severity,
            "confidence": self.assessment.confidence,
            "reason_code": self.assessment.reason_code,
            "blocked": self.authorization.blocked,
            "tier": self.authorization.tier,
        }


def _event_user_id(event: Any) -> str:
    return str(getattr(event, "user_id", "") or "").strip()


def _event_surface(event: Any) -> tuple[str, str]:
    group_id = str(getattr(event, "group_id", "") or "").strip()
    if group_id:
        return "qq_group", f"qq_group:{group_id}"
    user_id = _event_user_id(event)
    return "qq_private", f"qq_private:{user_id}"


def _event_is_direct(event: Any, bot_self_id: str) -> bool:
    if not str(getattr(event, "group_id", "") or "").strip():
        return True
    if bool(getattr(event, "to_me", False)):
        return True
    self_id = str(bot_self_id or getattr(event, "self_id", "") or "").strip()
    if not self_id:
        return False
    try:
        for segment in getattr(event, "message", []) or []:
            if str(getattr(segment, "type", "") or "").strip().lower() != "at":
                continue
            target = str((getattr(segment, "data", {}) or {}).get("qq", "") or "").strip()
            if target == self_id:
                return True
    except Exception:
        pass
    reply = getattr(event, "reply", None)
    sender = getattr(reply, "sender", None) if reply is not None else None
    if isinstance(reply, dict):
        sender = reply.get("sender")
    if isinstance(sender, dict):
        reply_user_id = str(sender.get("user_id", "") or "").strip()
    else:
        reply_user_id = str(getattr(sender, "user_id", "") or "").strip()
    return bool(reply_user_id and reply_user_id == self_id)


def _event_text(event: Any) -> str:
    try:
        return str(event.get_plaintext() or "").strip()[:1200]
    except Exception:
        return ""


def _event_media_summary(event: Any) -> str:
    kinds: list[str] = []
    try:
        for segment in getattr(event, "message", []) or []:
            kind = str(getattr(segment, "type", "") or "").strip().lower()
            if kind in {"image", "face", "mface", "video", "record", "json", "xml"}:
                kinds.append(kind)
    except Exception:
        return ""
    return ",".join(kinds[:12])


def _blocked_authorization(tier: str = "policy_unavailable") -> PolicyAuthorization:
    return PolicyAuthorization(
        blocked=True,
        tier=tier,
        allow_reply=False,
        allow_visible_reaction=False,
        allow_agent_action=False,
        allow_proactive=False,
        allow_qzone=False,
        allow_profile_write=False,
        allow_history_write=False,
        allow_memory_write=False,
        allow_relation_write=False,
        allow_context_read=False,
    )


class QQUserPolicyGate:
    def __init__(
        self,
        service: UserPolicyService,
        *,
        logger: Any = None,
        legacy_block_checker: Any = None,
        cache_ttl_seconds: float = 60.0,
        cache_max_size: int = 512,
    ) -> None:
        self.service = service
        self.logger = logger
        self.legacy_block_checker = legacy_block_checker
        self.cache_ttl_seconds = max(1.0, float(cache_ttl_seconds))
        self.cache_max_size = max(16, int(cache_max_size))
        self._cache: OrderedDict[str, tuple[float, QQPolicyDecision]] = OrderedDict()
        self._inflight: dict[str, asyncio.Task[QQPolicyDecision]] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def _event_key(event: Any, *, bot_self_id: str, surface: str) -> str:
        message_id = str(getattr(event, "message_id", "") or "").strip()
        user_id = _event_user_id(event) or "unknown"
        identity = message_id or f"object-{id(event)}"
        return f"qq:{bot_self_id or 'unknown'}:{surface}:{user_id}:{identity}"

    async def current_authorization(self, user_id: str) -> PolicyAuthorization:
        try:
            if self.legacy_block_checker is not None:
                legacy_blocked = await asyncio.to_thread(
                    self.legacy_block_checker,
                    str(user_id or "").strip(),
                )
                if legacy_blocked:
                    return _blocked_authorization("legacy_permanent")
            return await asyncio.to_thread(self.service.authorize, str(user_id or "").strip())
        except Exception as exc:
            if self.logger is not None:
                self.logger.warning(
                    f"[user_policy] authorization failed closed: {type(exc).__name__}"
                )
            return _blocked_authorization()

    async def allows_current(self, event: Any) -> bool:
        user_id = _event_user_id(event)
        if not user_id:
            return False
        authorization = await self.current_authorization(user_id)
        return not authorization.blocked

    async def ensure_current(self, event: Any) -> None:
        if not await self.allows_current(event):
            raise QQPolicyBlockedDuringTurn("QQ user policy blocked during turn")

    async def claim_direct_closure(self, decision: QQPolicyDecision) -> QQPolicyDecision:
        if decision.disposition != QQ_POLICY_DIRECT_CLOSURE_CANDIDATE:
            return decision
        try:
            claimed = await asyncio.to_thread(
                self.service.claim_direct_closure,
                user_id=decision.user_id,
                channel_key=decision.channel_key,
                event_key=decision.event_key,
            )
        except Exception as exc:
            if self.logger is not None:
                self.logger.warning(
                    f"[user_policy] direct closure claim failed closed: {type(exc).__name__}"
                )
            claimed = False
        return replace(
            decision,
            disposition=QQ_POLICY_DIRECT_CLOSURE if claimed else QQ_POLICY_SILENT,
        )

    async def evaluate(self, event: Any, *, bot_self_id: str = "") -> QQPolicyDecision:
        user_id = _event_user_id(event)
        surface, channel_key = _event_surface(event)
        resolved_bot_id = str(bot_self_id or getattr(event, "self_id", "") or "").strip()
        key = self._event_key(event, bot_self_id=resolved_bot_id, surface=surface)
        now = time.monotonic()
        async with self._lock:
            cached = self._cache.get(key)
            if cached is not None and now - cached[0] <= self.cache_ttl_seconds:
                self._cache.move_to_end(key)
                return cached[1]
            if cached is not None:
                self._cache.pop(key, None)
            task = self._inflight.get(key)
            if task is None:
                task = asyncio.create_task(
                    self._evaluate_uncached(
                        event,
                        user_id=user_id,
                        bot_self_id=resolved_bot_id,
                        event_key=key,
                        surface=surface,
                        channel_key=channel_key,
                    )
                )
                self._inflight[key] = task
                task.add_done_callback(
                    lambda completed, cache_key=key: self._schedule_task_finalization(
                        cache_key, completed
                    )
                )
        decision = await asyncio.shield(task)
        return decision

    def _schedule_task_finalization(
        self,
        key: str,
        task: asyncio.Task[QQPolicyDecision],
    ) -> None:
        try:
            asyncio.get_running_loop().create_task(self._finalize_task(key, task))
        except RuntimeError:
            return

    async def _finalize_task(
        self,
        key: str,
        task: asyncio.Task[QQPolicyDecision],
    ) -> None:
        async with self._lock:
            if self._inflight.get(key) is task:
                self._inflight.pop(key, None)
            if task.cancelled():
                return
            try:
                decision = task.result()
            except Exception:
                return
            self._cache[key] = (time.monotonic(), decision)
            self._cache.move_to_end(key)
            while len(self._cache) > self.cache_max_size:
                self._cache.popitem(last=False)

    async def _evaluate_uncached(
        self,
        event: Any,
        *,
        user_id: str,
        bot_self_id: str,
        event_key: str,
        surface: str,
        channel_key: str,
    ) -> QQPolicyDecision:
        direct = _event_is_direct(event, bot_self_id)
        authorization = await self.current_authorization(user_id)
        if authorization.blocked or not user_id:
            return QQPolicyDecision(
                user_id=user_id,
                event_key=event_key,
                surface=surface,
                channel_key=channel_key,
                direct=direct,
                disposition=QQ_POLICY_SILENT,
                assessment=PolicyAssessment(reason_code="active_blacklist", confirmed=False),
                authorization=authorization,
            )

        if bot_self_id and user_id == bot_self_id:
            return QQPolicyDecision(
                user_id=user_id,
                event_key=event_key,
                surface=surface,
                channel_key=channel_key,
                direct=direct,
                disposition=QQ_POLICY_ALLOW,
                assessment=PolicyAssessment(
                    verdict="allow",
                    category="none",
                    intent="ordinary",
                    severity="none",
                    confidence=1.0,
                    reason_code="bot_self_message",
                    confirmed=True,
                ),
                authorization=authorization,
            )

        text = _event_text(event)
        if not text:
            return QQPolicyDecision(
                user_id=user_id,
                event_key=event_key,
                surface=surface,
                channel_key=channel_key,
                direct=direct,
                disposition=QQ_POLICY_ALLOW,
                assessment=PolicyAssessment(
                    verdict="allow",
                    category="none",
                    intent="ordinary",
                    severity="none",
                    confidence=1.0,
                    reason_code="non_text_event",
                    confirmed=True,
                ),
                authorization=authorization,
            )

        classifier = self.service.classifier
        if classifier is None:
            assessment = PolicyAssessment(reason_code="classifier_unavailable", confirmed=False)
        else:
            try:
                assessment = await classifier.classify(
                    PolicyEventInput(
                        user_id=user_id,
                        event_id=event_key,
                        surface=surface,
                        text=text,
                        is_direct=direct,
                        media_summary=_event_media_summary(event),
                    )
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if self.logger is not None:
                    self.logger.warning(
                        f"[user_policy] QQ classifier failed closed: {type(exc).__name__}"
                    )
                assessment = PolicyAssessment(reason_code="classifier_unavailable", confirmed=False)

        try:
            result = await asyncio.to_thread(
                self.service.apply_assessment,
                user_id=user_id,
                idempotency_key=event_key,
                surface=surface,
                assessment=assessment,
                content=text,
                metadata={
                    "bot_id": bot_self_id,
                    "group_id": str(getattr(event, "group_id", "") or ""),
                    "message_id": str(getattr(event, "message_id", "") or ""),
                    "is_direct": direct,
                    "protocol": "onebot_v11",
                },
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if self.logger is not None:
                self.logger.warning(
                    f"[user_policy] QQ policy persistence failed closed: {type(exc).__name__}"
                )
            return QQPolicyDecision(
                user_id=user_id,
                event_key=event_key,
                surface=surface,
                channel_key=channel_key,
                direct=direct,
                disposition=QQ_POLICY_SILENT,
                assessment=PolicyAssessment(
                    reason_code="policy_store_unavailable",
                    confirmed=False,
                ),
                authorization=_blocked_authorization(),
            )
        assessment = result.assessment
        authorization = await self.current_authorization(user_id)
        if authorization.blocked:
            disposition = QQ_POLICY_SILENT
        elif assessment.should_quarantine:
            disposition = QQ_POLICY_DIRECT_CLOSURE_CANDIDATE if direct else QQ_POLICY_SILENT
        else:
            disposition = QQ_POLICY_ALLOW
        return QQPolicyDecision(
            user_id=user_id,
            event_key=event_key,
            surface=surface,
            channel_key=channel_key,
            direct=direct,
            disposition=disposition,
            assessment=assessment,
            authorization=authorization,
        )


__all__ = [
    "QQ_POLICY_ALLOW",
    "QQ_POLICY_DIRECT_CLOSURE",
    "QQ_POLICY_DIRECT_CLOSURE_CANDIDATE",
    "QQ_POLICY_SILENT",
    "QQPolicyBlockedDuringTurn",
    "QQPolicyDecision",
    "QQUserPolicyGate",
]
