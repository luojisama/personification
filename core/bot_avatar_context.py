from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class BotAvatarInsightContext:
    bot_self_id: str = ""
    asset_kind: str = "unknown"
    neutral_summary: str = ""
    acg_candidates: list[str] = field(default_factory=list)
    confidence: float = 0.0
    has_insight: bool = False


def get_bot_avatar_insight_context(
    profile_service: Any,
    bot_self_id: str | int,
) -> BotAvatarInsightContext:
    uid = str(bot_self_id or "").strip()
    if not uid:
        return BotAvatarInsightContext()
    if profile_service is None:
        return BotAvatarInsightContext(bot_self_id=uid)
    try:
        from .user_avatar_insight import get_user_avatar_state

        state = get_user_avatar_state(profile_service, uid)
        insight = state.get("insight", {}) if isinstance(state, dict) else {}
        if not insight:
            return BotAvatarInsightContext(bot_self_id=uid)

        summary = str(insight.get("neutral_summary", "") or "").strip()
        asset_kind = str(insight.get("asset_kind", "unknown") or "unknown").strip().lower()
        candidates = [
            str(item).strip()
            for item in (insight.get("acg_candidates") or [])
            if str(item).strip()
        ]
        confidence = float(insight.get("confidence", 0.0) or 0.0)
        has_insight = bool(summary and summary != "头像内容无法可靠归类" and asset_kind != "real_person")

        return BotAvatarInsightContext(
            bot_self_id=uid,
            asset_kind=asset_kind,
            neutral_summary=summary,
            acg_candidates=candidates,
            confidence=confidence,
            has_insight=has_insight,
        )
    except Exception:
        return BotAvatarInsightContext(bot_self_id=uid)


def render_bot_avatar_vision_prompt(
    context: BotAvatarInsightContext | None,
) -> str:
    if context is None or not context.has_insight or not context.neutral_summary:
        return ""

    candidates_text = (
        f"（可能对应的角色或原型参考：{'、'.join(context.acg_candidates[:4])}）"
        if context.acg_candidates
        else ""
    )

    return (
        "【自身形象与二创自知】\n"
        f"- 你当前在聊天中的外貌/头像形象特征为：{context.neutral_summary} {candidates_text}\n"
        "- 当用户发送图片并询问“这是不是你”、“这人是谁”，或发送带有你上述形象特征的同人图、二创插画、表情包时：\n"
        "  请结合画面的视觉特征与你的角色人设进行判断。若画面符合你的角色形象，应自然认出那是关于你的二创/同人内容，"
        "并以你的人设性格（如害羞、傲娇、困惑或吐槽）做出符合身份的自然反应，不要作为无关陌生人机械否认。"
    )


__all__ = [
    "BotAvatarInsightContext",
    "get_bot_avatar_insight_context",
    "render_bot_avatar_vision_prompt",
]
