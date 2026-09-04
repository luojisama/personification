from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any


@dataclass
class FavorabilityTurnSignals:
    group_atmosphere_positive: bool = False
    interaction_interesting: bool = False
    relationship_progress: str = "none"
    relationship_progress_confidence: float = 0.0

    def merge(self, other: "FavorabilityTurnSignals") -> None:
        self.group_atmosphere_positive = bool(
            self.group_atmosphere_positive or other.group_atmosphere_positive
        )
        self.interaction_interesting = bool(
            self.interaction_interesting or other.interaction_interesting
        )
        rank = {"none": 0, "meaningful": 1, "resonant": 2, "milestone": 3}
        if rank.get(other.relationship_progress, 0) > rank.get(self.relationship_progress, 0):
            self.relationship_progress = other.relationship_progress
            self.relationship_progress_confidence = other.relationship_progress_confidence
        elif other.relationship_progress == self.relationship_progress:
            self.relationship_progress_confidence = max(
                self.relationship_progress_confidence,
                other.relationship_progress_confidence,
            )


def signals_from_semantic_frame(
    semantic_frame: Any,
    *,
    is_private: bool,
    minimum_confidence: float = 0.55,
) -> FavorabilityTurnSignals:
    try:
        confidence = float(getattr(semantic_frame, "confidence", 0.0) or 0.0)
    except (TypeError, ValueError, OverflowError):
        confidence = 0.0
    progress = str(getattr(semantic_frame, "relationship_progress", "none") or "none").strip().lower()
    if progress not in {"none", "meaningful", "resonant", "milestone"}:
        progress = "none"
    try:
        progress_confidence = float(
            getattr(semantic_frame, "relationship_progress_confidence", 0.0) or 0.0
        )
    except (TypeError, ValueError, OverflowError):
        progress_confidence = 0.0
    interesting = bool(getattr(semantic_frame, "interaction_interesting", False))
    if progress == "none" and interesting and confidence >= minimum_confidence:
        # v5 compatibility: the former interesting flag now means one meaningful event.
        progress = "meaningful"
        progress_confidence = confidence
    return FavorabilityTurnSignals(
        group_atmosphere_positive=(
            confidence >= minimum_confidence
            and not is_private
            and bool(getattr(semantic_frame, "group_atmosphere_positive", False))
        ),
        interaction_interesting=bool(interesting and confidence >= minimum_confidence),
        relationship_progress=progress,
        relationship_progress_confidence=max(0.0, min(1.0, progress_confidence)),
    )


def extract_legacy_favorability_markers(
    text: Any,
) -> tuple[str, FavorabilityTurnSignals]:
    raw = str(text or "")
    signals = FavorabilityTurnSignals(
        group_atmosphere_positive="[氛围好]" in raw or "<氛围好>" in raw,
        interaction_interesting="[有趣]" in raw or "<有趣>" in raw,
    )
    if signals.interaction_interesting:
        signals.relationship_progress = "meaningful"
        signals.relationship_progress_confidence = 1.0
    for marker in ("[氛围好]", "<氛围好>", "[有趣]", "<有趣>"):
        raw = raw.replace(marker, "")
    return raw.strip(), signals


def build_favorability_context_block(
    *,
    user_level: str,
    user_attitude: str,
    group_attitude: str = "",
    is_private: bool,
    behavior_policy: dict[str, Any] | None = None,
) -> str:
    level = str(user_level or "").strip()
    attitude = str(user_attitude or "").strip() or "态度普通，像平常一样交流。"
    policy = dict(behavior_policy or {})
    band = str(policy.get("band", "") or "")
    relation_style = "自然、平衡，先回应当前问题。"
    care_level = "保持基本关注，不刻意拉近距离。"
    address_style = "使用中性称呼。"
    initiative = "只做与当前话题直接相关的自然延展。"
    teasing = "不主动调侃。"
    preferred_length = "默认回复 1-2 句。"
    if band in {"-100--80", "-80--60"}:
        relation_style = "明确保持边界，只处理必要内容；仍保持安全、礼貌和事实准确。"
        care_level = "保持克制，不增加额外情绪承诺。"
        address_style = "使用正式或中性称呼。"
        initiative = "不主动延展，但明确问题仍须完整回答。"
        preferred_length = "优先简短直接地回应当前问题。"
    elif band in {"-60--40", "-40--20", "-20--0"}:
        relation_style = "保持基本礼貌但谨慎、正式，不过度热情；明确提问仍应正常回答。"
        care_level = "表达基础关心，不作亲密暗示。"
        address_style = "使用正式或中性称呼。"
        initiative = "最多顺着当前话题补充半步。"
        preferred_length = "优先 1-2 句，直接回答重点。"
    elif band in {"75-91", "92-100"} or level in {"挚友", "亲密"}:
        relation_style = "适度使用更亲近的称呼或语气词，体现熟悉感。"
        care_level = "可以更主动地倾听和关心，但不许作现实关系承诺。"
        address_style = "可以使用已经确认、且对方接受的称呼。"
        initiative = "可以自然延展相关话题或追问一个轻量问题。"
        teasing = "仅在当前语境明确适合时轻度调侃，并尊重对方边界。"
        preferred_length = "可以扩展到 2-4 句，增加情感反馈。"
    elif band == "50-74":
        relation_style = "语气温和，可以使用已知称呼并适度延展，但仍保持人格边界。"
        care_level = "表达温和关注，不夸大关系。"
        address_style = "可以使用已经确认的称呼。"
        initiative = "可以围绕当前话题适度延展。"
        teasing = "只在已有互动证据支持时轻度调侃。"
        preferred_length = "通常回复 1-3 句，视语境适度补充。"
    elif band == "0-19" or level in {"陌生", "路人", "初见"}:
        relation_style = "保持礼貌和边界感，避免过度亲昵。"
        preferred_length = "优先 1-2 句，直接回答重点。"
    if is_private:
        relation_style += " 私聊场景可更自然连续，不必强调围观感。"
    lines = [
        "## 当前关系表达边界",
        f"- 关系阶段：{level or '日常'}",
        f"- 个人态度：{attitude}",
        f"- 关系表达策略：{relation_style}",
        f"- 温度与关心：{care_level}",
        f"- 称呼方式：{address_style}",
        f"- 主动延展：{initiative}",
        f"- 调侃边界：{teasing}",
        f"- 长度偏好：{preferred_length}",
        "- 共同经历：只有当前可信上下文或持久记忆已经证实时才可引用。",
        "- 这是语气和互动方式的软约束，不是事实来源；不得提及好感分数、阈值或系统档位。",
        "- 不得伪造共同经历、恋爱关系、承诺或现实身份；低关系阶段也不得降低事实准确性、拒绝明确问题或绕过安全规则。",
    ]
    group_text = str(group_attitude or "").strip()
    if group_text and not is_private:
        lines.append(f"- 当前群聊整体氛围：{group_text}；它只影响参与意愿和群体舒适度，不得转成对单个用户的亲密称呼。")
    return "\n".join(lines)


def build_favorability_turn_id(
    *,
    trace_id: Any = "",
    message_id: Any = "",
    group_id: Any = "",
    user_id: Any = "",
) -> str:
    stable_message_id = str(message_id or "").strip()
    fallback_trace_id = str(trace_id or "").strip()
    if stable_message_id:
        parts = ["message", stable_message_id, str(group_id or "").strip(), str(user_id or "").strip()]
    elif fallback_trace_id:
        parts = ["trace", fallback_trace_id, str(group_id or "").strip(), str(user_id or "").strip()]
    else:
        return ""
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()
    return f"reply-turn-{digest[:32]}"


def commit_favorability_turn(
    *,
    service: Any,
    user_id: str,
    group_id: str,
    is_private: bool,
    is_direct: bool,
    is_random_chat: bool,
    signals: FavorabilityTurnSignals,
    turn_id: str,
    now: Any = None,
) -> list[dict[str, Any]]:
    if service is None or not bool(getattr(service, "enabled", True)):
        return []
    results: list[dict[str, Any]] = []
    uid = str(user_id or "").strip()
    gid = "" if is_private else str(group_id or "").strip()
    if not uid:
        return results
    if signals.group_atmosphere_positive and gid and hasattr(service, "apply_group_good_atmosphere"):
        results.append(
            service.apply_group_good_atmosphere(
                gid,
                now=now,
                reason="统一语义帧或兼容控制标记判定群聊氛围良好",
                event_id=f"{turn_id}:group-atmosphere" if turn_id else "",
            )
        )
    if signals.relationship_progress != "none" and hasattr(service, "apply_relationship_progress"):
        results.append(
            service.apply_relationship_progress(
                uid,
                quality=signals.relationship_progress,
                confidence=signals.relationship_progress_confidence,
                now=now,
                group_id=gid,
                is_private=is_private,
                reason="统一语义帧判定本轮存在有效关系进展",
                event_id=f"{turn_id}:relationship-progress" if turn_id else "",
            )
        )
    return results


__all__ = [
    "FavorabilityTurnSignals",
    "build_favorability_context_block",
    "build_favorability_turn_id",
    "commit_favorability_turn",
    "extract_legacy_favorability_markers",
    "signals_from_semantic_frame",
]
