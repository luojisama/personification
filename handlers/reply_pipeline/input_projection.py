"""Pure projections of an incoming turn for reply history and media gates."""

from __future__ import annotations

from typing import Any, Dict, List

from ...core.history_projection import build_group_batch_history
from ...core.message_parts import build_user_message_content


def prepare_incoming_history_record(
    *,
    is_private_session: bool,
    batched_events: list[dict[str, Any]],
    fallback_content: Any,
    fallback_speaker: str,
    image_urls: list[str],
    image_detail: str,
    trigger_user_id: str,
    trigger_message_id: str,
    trigger_group_id: str,
    message_target: Any = "",
) -> tuple[Any, str, dict[str, Any]]:
    """Build one recoverable history row without inventing a batch speaker."""
    if is_private_session or len(batched_events) <= 1:
        return fallback_content, fallback_speaker, {}
    envelope, metadata = build_group_batch_history(batched_events)
    content = build_user_message_content(
        text=envelope, image_urls=image_urls, image_detail=image_detail,
    )
    metadata = dict(metadata)
    speaker = str(metadata.pop("speaker", "多人群聊批次") or "多人群聊批次")
    # Trigger identity is metadata, never a fictitious user_id for the batch.
    metadata.update({
        "source_kind": "user_batch",
        "trigger_user_id": str(trigger_user_id or ""),
        "trigger_message_id": str(trigger_message_id or ""),
        "trigger_group_id": str(trigger_group_id or ""),
        "message_target": message_target or "",
    })
    metadata.pop("user_id", None)
    return content, speaker, metadata


def prepare_agent_incoming_content(**kwargs: Any) -> Any:
    """Build the Agent-visible current user record through the same envelope."""
    content, _, _ = prepare_incoming_history_record(**kwargs)
    return content


def _build_image_only_context_message(
    *, sender_name: str, is_private_context: bool, is_active_followup: bool,
    followup_topic: str, is_solo_speaker_follow: bool, solo_follow_topic: str,
    is_random_chat: bool,
) -> str:
    if is_private_context:
        return (
            "[对方发送了一张图片。若没有直接看到图片或可见摘要，不要假装看懂；"
            "先结合最近对话短句回应，必要时请对方补一句]"
        )
    if is_active_followup:
        return (
            f"[对方正在顺着你刚才的话题继续聊，并发来了一条图片/表情消息。"
            f"刚才的话题：{followup_topic or '上一轮对话'}。"
            "若没有清楚的视觉摘要，不要评价图片内容；只有能从前文确定是在接话时才短句回应，否则保持安静]"
        )
    if is_solo_speaker_follow:
        return (
            f"[群里 {sender_name} 已经连续说了一阵，并发来了一条图片/表情消息。"
            f"当前延续的话题：{solo_follow_topic or '刚才这串内容'}。"
            "若没有清楚的视觉摘要，不要假装看懂图片；只有能从前文确定是在接话时才短句回应，否则保持安静]"
        )
    if is_random_chat:
        return (
            f"[群里 {sender_name} 发了一条图片/表情消息，你只是路过看到。"
            "没人 cue 你且没有明确文字意图时保持安静，不要评论图片或表情内容]"
        )
    return (
        "[对方发送了一张图片，是在对你说话。"
        "如果看不清内容，先接文字或最近上下文；信息不足时给一句保守短反应或保持安静，不要追问图里是什么]"
    )


def _batch_media_owner_matches_selected_user(
    batched_events: List[Dict[str, Any]], selected_user_id: str,
) -> bool:
    media_owners = {
        str(media.get("owner_user_id", "") or "").strip()
        for item in batched_events
        if isinstance(item, dict)
        for media in list(item.get("media") or [])
        if isinstance(media, dict) and str(media.get("owner_user_id", "") or "").strip()
    }
    return not media_owners or media_owners == {str(selected_user_id or "").strip()}


def _has_turn_media_input(image_urls: List[str], turn_media_context: List[Any]) -> bool:
    """Return whether media can drive Agent processing before lazy resolution."""
    if image_urls:
        return True
    return any(
        str(item.get("kind", "") if isinstance(item, dict) else getattr(item, "kind", ""))
        .strip().lower() in {"video", "audio"}
        for item in list(turn_media_context or [])
    )
