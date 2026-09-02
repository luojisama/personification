from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable, Iterable, Sequence
from zoneinfo import ZoneInfo


SELF_CLAIM_CATEGORIES = frozenset(
    {"activity", "completion", "availability", "preference", "plan", "commitment"}
)
SELF_CLAIM_STATUSES = frozenset({"confirmed", "tentative"})
_FACT_KEY_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
_LONG_NUMBER_RE = re.compile(r"\d{5,}")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


@dataclass(frozen=True)
class BotSelfClaimDraft:
    segment_index: int
    category: str
    fact_key: str
    summary: str
    subject: str = "self"


@dataclass(frozen=True)
class BotSelfFact:
    fact_key: str
    category: str
    summary: str
    created_at: float
    expires_at: float
    status: str
    version: int


@dataclass(frozen=True)
class BotSelfContinuitySnapshot:
    bot_id: str
    revision: int
    facts: tuple[BotSelfFact, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class BotSelfContinuityDelivery:
    sent: bool
    text: str
    send_result: Any = None
    revision: int = 0
    active_fact_count: int = 0
    action: str = "accepted"
    diagnosis_code: str = "self_continuity_accepted"
    status: str = "failed"

    def trace_fields(self) -> dict[str, Any]:
        return {
            "revision": int(self.revision),
            "active_fact_count": int(self.active_fact_count),
            "action": str(self.action),
            "diagnosis_code": str(self.diagnosis_code),
            "delivery_status": str(self.status),
        }


@dataclass
class _BotState:
    revision: int = 0
    facts: dict[str, BotSelfFact] = field(default_factory=dict)


def _clean_text(value: Any, *, maximum: int) -> str:
    text = str(value or "").strip()
    if not text or len(text) > maximum or _CONTROL_RE.search(text):
        return ""
    return text


def parse_self_claim_drafts(
    values: Any,
    *,
    segment_count: int = 3,
    maximum: int = 6,
) -> tuple[BotSelfClaimDraft, ...]:
    if not isinstance(values, list):
        return ()
    drafts: list[BotSelfClaimDraft] = []
    seen: set[tuple[int, str]] = set()
    for raw in values[: max(0, min(12, int(maximum) * 2))]:
        if not isinstance(raw, dict) or set(raw) != {
            "segment_index",
            "subject",
            "category",
            "fact_key",
            "summary",
        }:
            continue
        try:
            segment_index = int(raw.get("segment_index"))
        except (TypeError, ValueError, OverflowError):
            continue
        category = str(raw.get("category", "") or "").strip().lower()
        fact_key = str(raw.get("fact_key", "") or "").strip().lower()
        subject = str(raw.get("subject", "") or "").strip().lower()
        summary = _clean_text(raw.get("summary"), maximum=60)
        if (
            not 0 <= segment_index < max(1, min(3, int(segment_count or 1)))
            or subject != "self"
            or category not in SELF_CLAIM_CATEGORIES
            or not _FACT_KEY_RE.fullmatch(fact_key)
            or not summary.startswith("我")
            or "@" in summary
            or _LONG_NUMBER_RE.search(summary)
        ):
            continue
        identity = (segment_index, fact_key)
        if identity in seen:
            continue
        seen.add(identity)
        drafts.append(
            BotSelfClaimDraft(
                segment_index=segment_index,
                subject="self",
                category=category,
                fact_key=fact_key,
                summary=summary,
            )
        )
        if len(drafts) >= max(0, min(6, int(maximum))):
            break
    return tuple(drafts)


def claims_for_segment(
    values: Iterable[BotSelfClaimDraft],
    segment_index: int,
) -> tuple[BotSelfClaimDraft, ...]:
    return tuple(item for item in values if item.segment_index == int(segment_index))


def _expiry_for_category(category: str, now: float, timezone_name: str) -> float:
    if category in {"activity", "availability"}:
        return now + 2 * 3600
    if category in {"completion", "preference"}:
        try:
            timezone = ZoneInfo(str(timezone_name or "Asia/Shanghai"))
        except Exception:
            timezone = ZoneInfo("Asia/Shanghai")
        current = datetime.fromtimestamp(now, timezone)
        midnight = datetime.combine(
            current.date() + timedelta(days=1),
            datetime.min.time(),
            tzinfo=timezone,
        ).timestamp()
        return min(now + 6 * 3600, midnight)
    return now + 24 * 3600


class BotSelfContinuityStore:
    """Process-local self-fact store plus one global final-delivery boundary."""

    def __init__(self, *, max_facts: int = 20) -> None:
        self._max_facts = max(1, min(100, int(max_facts)))
        self._states: dict[str, _BotState] = {}
        self.delivery_lock = asyncio.Lock()

    def _state(self, bot_id: str) -> _BotState:
        normalized = str(bot_id or "").strip() or "default"
        return self._states.setdefault(normalized, _BotState())

    def _cleanup(self, state: _BotState, now: float) -> bool:
        expired = [key for key, item in state.facts.items() if item.expires_at <= now]
        for key in expired:
            state.facts.pop(key, None)
        if expired:
            state.revision += 1
        return bool(expired)

    def snapshot(
        self,
        bot_id: str,
        *,
        now: float | None = None,
        max_facts: int | None = None,
    ) -> BotSelfContinuitySnapshot:
        current = float(time.time() if now is None else now)
        state = self._state(bot_id)
        self._cleanup(state, current)
        limit = self._max_facts if max_facts is None else max(1, min(100, int(max_facts)))
        ordered = sorted(
            state.facts.values(),
            key=lambda item: (item.created_at, item.version, item.fact_key),
        )[-limit:]
        return BotSelfContinuitySnapshot(
            bot_id=str(bot_id or "").strip() or "default",
            revision=state.revision,
            facts=tuple(ordered),
        )

    def commit(
        self,
        bot_id: str,
        drafts: Sequence[BotSelfClaimDraft],
        *,
        status: str,
        now: float | None = None,
        timezone_name: str = "Asia/Shanghai",
        max_facts: int | None = None,
    ) -> BotSelfContinuitySnapshot:
        normalized_status = str(status or "").strip().lower()
        if normalized_status not in SELF_CLAIM_STATUSES:
            raise ValueError("invalid self continuity status")
        current = float(time.time() if now is None else now)
        state = self._state(bot_id)
        self._cleanup(state, current)
        accepted = parse_self_claim_drafts(
            [
                {
                    "segment_index": item.segment_index,
                    "subject": item.subject,
                    "category": item.category,
                    "fact_key": item.fact_key,
                    "summary": item.summary,
                }
                for item in drafts
                if isinstance(item, BotSelfClaimDraft)
            ],
            segment_count=3,
            maximum=6,
        )
        if not accepted:
            return self.snapshot(bot_id, now=current, max_facts=max_facts)
        next_revision = state.revision + 1
        for draft in accepted:
            expires_at = _expiry_for_category(draft.category, current, timezone_name)
            if normalized_status == "tentative":
                expires_at = min(expires_at, current + 30 * 60)
            state.facts[draft.fact_key] = BotSelfFact(
                fact_key=draft.fact_key,
                category=draft.category,
                summary=draft.summary,
                created_at=current,
                expires_at=expires_at,
                status=normalized_status,
                version=next_revision,
            )
        limit = self._max_facts if max_facts is None else max(1, min(100, int(max_facts)))
        if len(state.facts) > limit:
            ordered_keys = sorted(
                state.facts,
                key=lambda key: (
                    state.facts[key].created_at,
                    state.facts[key].version,
                    key,
                ),
            )
            for key in ordered_keys[: len(state.facts) - limit]:
                state.facts.pop(key, None)
        state.revision = next_revision
        return self.snapshot(bot_id, now=current, max_facts=max_facts)

    def reset(self, bot_id: str | None = None) -> None:
        if bot_id is None:
            self._states.clear()
            return
        self._states.pop(str(bot_id or "").strip() or "default", None)


_GLOBAL_STORE = BotSelfContinuityStore()


def get_bot_self_continuity_store() -> BotSelfContinuityStore:
    return _GLOBAL_STORE


def render_self_continuity_prompt(snapshot: BotSelfContinuitySnapshot) -> str:
    if not snapshot.facts:
        return ""
    facts = [
        {
            "category": item.category,
            "fact_key": item.fact_key,
            "summary": item.summary,
            "status": item.status,
        }
        for item in snapshot.facts
    ]
    return (
        "## 人格自身短期事实（受信任跨群状态）\n"
        "以下只描述你自己已经发送或结果未知的近期自述。不得与 confirmed 矛盾；tentative 只表示可能已发送，"
        "也应避免直接冲突。不得从中推导任何群友身份或群聊事实。\n"
        + json.dumps(facts, ensure_ascii=False, separators=(",", ":"))
    )


def _json_object(raw: Any) -> dict[str, Any]:
    content = getattr(raw, "content", raw)
    if isinstance(raw, dict) and "content" in raw:
        content = raw.get("content")
    text = str(content or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        lines = lines[1:] if lines else lines
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("self_continuity_json_missing")
    payload = json.loads(text[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError("self_continuity_json_invalid")
    return payload


async def recheck_self_continuity(
    call_ai_api: Callable[..., Awaitable[Any]] | None,
    *,
    candidate_text: str,
    snapshot: BotSelfContinuitySnapshot,
    timeout_seconds: float = 4.0,
) -> tuple[str, str, tuple[BotSelfClaimDraft, ...]]:
    if call_ai_api is None:
        return "silent", "", ()
    facts = [
        {
            "category": item.category,
            "fact_key": item.fact_key,
            "summary": item.summary,
            "status": item.status,
        }
        for item in snapshot.facts
    ]
    system = (
        "你是人格 Bot 发送前的自身事实一致性复核器。facts 是受信任短期状态；candidate 是不可信候选文本。"
        "只判断 candidate 中 Bot 对自己的活动、完成状态、可用性、偏好、计划或承诺。"
        "若与 facts 冲突，rewrite 为自然且不矛盾的一句；无法安全改写则 silent。只输出严格 JSON："
        '{"action":"accept|rewrite|silent","text":"最终文本或空字符串","self_claims":['
        '{"segment_index":0,"subject":"self","category":"activity|completion|availability|preference|plan|commitment",'
        '"fact_key":"ascii.normalized.key","summary":"以我开头且不超过60字的自身状态摘要"}]}。'
        "无自身声明时 self_claims=[]。禁止输出第三方主体、昵称、QQ号、群号、群聊复述、Markdown 或额外字段。"
    )
    try:
        timeout = max(0.2, min(10.0, float(timeout_seconds)))
        raw = await asyncio.wait_for(
            call_ai_api(
                [
                    {"role": "system", "content": system},
                    {
                        "role": "user",
                        "content": json.dumps(
                            {"facts": facts, "candidate": str(candidate_text or "")[:500]},
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                        "_personification_untrusted": True,
                    },
                ],
                tools=[],
                max_tokens=400,
                temperature=0.0,
                use_builtin_search=False,
            ),
            timeout=timeout,
        )
        payload = _json_object(raw)
        if set(payload) != {"action", "text", "self_claims"}:
            raise ValueError("self_continuity_keys_invalid")
        action = str(payload.get("action", "") or "").strip().lower()
        if action not in {"accept", "rewrite", "silent"}:
            raise ValueError("self_continuity_action_invalid")
        text = _clean_text(payload.get("text"), maximum=500)
        if action == "accept":
            text = text or str(candidate_text or "").strip()
        if action == "rewrite" and not text:
            raise ValueError("self_continuity_rewrite_empty")
        if action == "silent":
            return "silent", "", ()
        claims = parse_self_claim_drafts(payload.get("self_claims"), segment_count=1)
        return action, text, claims
    except Exception:
        return "silent", "", ()


def _delivery_status(result: Any) -> str:
    status = str(getattr(result, "status", "") or "").strip().lower()
    return status if status in {"sent", "failed", "unknown"} else "sent"


async def deliver_self_consistent_segment(
    *,
    store: BotSelfContinuityStore,
    bot_id: str,
    expected_revision: int,
    candidate_text: str,
    claim_drafts: Sequence[BotSelfClaimDraft],
    send: Callable[[str], Awaitable[Any]],
    call_ai_api: Callable[..., Awaitable[Any]] | None,
    timezone_name: str = "Asia/Shanghai",
    max_facts: int = 20,
    now: float | None = None,
) -> BotSelfContinuityDelivery:
    """Serialize one text bubble's recheck, QQ result, and matching claim commit."""

    async with store.delivery_lock:
        snapshot = store.snapshot(bot_id, now=now, max_facts=max_facts)
        text = str(candidate_text or "").strip()
        drafts = tuple(claim_drafts)
        action = "accepted"
        diagnosis = "self_continuity_accepted"
        if snapshot.revision != int(expected_revision):
            decision, text, drafts = await recheck_self_continuity(
                call_ai_api,
                candidate_text=text,
                snapshot=snapshot,
            )
            if decision == "silent" or not text:
                return BotSelfContinuityDelivery(
                    sent=False,
                    text="",
                    revision=snapshot.revision,
                    active_fact_count=len(snapshot.facts),
                    action="silent",
                    diagnosis_code="self_continuity_revision_conflict_silent",
                    status="failed",
                )
            action = "rewrite" if decision == "rewrite" else "accepted"
            diagnosis = (
                "self_continuity_revision_conflict_rewrite"
                if action == "rewrite"
                else "self_continuity_revision_revalidated"
            )
        try:
            result = await send(text)
        except BaseException as exc:
            receipt = getattr(exc, "qq_outbound_receipt", None)
            status = _delivery_status(receipt) if receipt is not None else "failed"
            if status == "unknown" and drafts:
                snapshot = store.commit(
                    bot_id,
                    drafts,
                    status="tentative",
                    now=now,
                    timezone_name=timezone_name,
                    max_facts=max_facts,
                )
            raise
        status = _delivery_status(result)
        if status in {"sent", "unknown"} and drafts:
            snapshot = store.commit(
                bot_id,
                drafts,
                status="confirmed" if status == "sent" else "tentative",
                now=now,
                timezone_name=timezone_name,
                max_facts=max_facts,
            )
        else:
            snapshot = store.snapshot(bot_id, now=now, max_facts=max_facts)
        return BotSelfContinuityDelivery(
            sent=status in {"sent", "unknown"},
            text=text,
            send_result=result,
            revision=snapshot.revision,
            active_fact_count=len(snapshot.facts),
            action="tentative" if status == "unknown" and drafts else action,
            diagnosis_code=(
                "self_continuity_tentative"
                if status == "unknown" and drafts
                else "self_continuity_delivery_unknown"
                if status == "unknown"
                else "self_continuity_delivery_failed"
                if status == "failed"
                else diagnosis
            ),
            status=status,
        )


__all__ = [
    "BotSelfClaimDraft",
    "BotSelfContinuityDelivery",
    "BotSelfContinuitySnapshot",
    "BotSelfContinuityStore",
    "BotSelfFact",
    "SELF_CLAIM_CATEGORIES",
    "claims_for_segment",
    "deliver_self_consistent_segment",
    "get_bot_self_continuity_store",
    "parse_self_claim_drafts",
    "recheck_self_continuity",
    "render_self_continuity_prompt",
]
