from __future__ import annotations

import asyncio
import json
import re
import time
from datetime import datetime
from typing import Any, Awaitable, Callable, Optional

from ..agent.inner_state import DEFAULT_STATE, load_inner_state
from ..core.data_store import get_data_store
from ..core.emotion_state import describe_user_emotion_memory, load_emotion_state
from ..core.image_input import summarize_images_with_vision
from ..core.qzone_service import _extract_qzone_comments
from ..core.time_ctx import inject_current_time_context
from .diary_flow import clean_generated_text


_STORE_NAME = "qzone_social_state"
_SCAN_LOCK = asyncio.Lock()


def _extract_system_prompt(prompt_data: Any) -> str:
    if isinstance(prompt_data, dict):
        value = prompt_data.get("system")
        if isinstance(value, str) and value.strip():
            return value.strip()
        return str(prompt_data)
    if isinstance(prompt_data, str):
        return prompt_data.strip()
    return ""


def _extract_json_object(raw: Any) -> dict[str, Any] | None:
    text = clean_generated_text(str(raw or "")).strip()
    if not text:
        return None
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text).rstrip("`").strip()
    try:
        parsed = json.loads(text)
    except Exception:
        match = re.search(r"\{[\s\S]*\}", text)
        if not match:
            return None
        try:
            parsed = json.loads(match.group(0))
        except Exception:
            return None
    return parsed if isinstance(parsed, dict) else None


def _normalize_limit(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except Exception:
        return 0


def _limit_reached(limit: Any, count: Any) -> bool:
    normalized_limit = _normalize_limit(limit)
    if normalized_limit <= 0:
        return False
    try:
        current = int(count or 0)
    except Exception:
        current = 0
    return current >= normalized_limit


def _trim_comment(text: Any, *, max_chars: int = 36) -> str:
    cleaned = clean_generated_text(str(text or ""))
    cleaned = re.sub(r"</?[^>]+>", "", cleaned)
    cleaned = cleaned.strip().strip('"').strip("'").strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[:max_chars].rstrip("，、,.。!！?？ ")


def _format_ts(ts: float) -> str:
    if not ts:
        return "未记录"
    try:
        return datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return "未知"


def _get_persona_snippet(persona_store: Any, user_id: str, max_chars: int) -> str:
    if persona_store is None:
        return ""
    getter = getattr(persona_store, "get_persona_snippet", None)
    if not callable(getter):
        return ""
    try:
        return str(getter(str(user_id), max_chars=max(1, int(max_chars))) or "").strip()
    except Exception:
        return ""


async def _get_friend_profiles(bot: Any, logger: Any) -> dict[str, dict[str, Any]]:
    try:
        friends = await bot.get_friend_list()
    except Exception as exc:
        logger.warning(f"[qzone_social] get_friend_list failed: {exc}")
        return {}
    profiles: dict[str, dict[str, Any]] = {}
    if not isinstance(friends, list):
        return profiles
    for item in friends:
        if not isinstance(item, dict) or item.get("user_id") is None:
            continue
        user_id = str(item.get("user_id"))
        profiles[user_id] = {
            "nickname": str(
                item.get("remark")
                or item.get("nickname")
                or item.get("user_remark")
                or user_id
            )
        }
    return profiles


def _normalize_state(now: datetime) -> dict[str, Any]:
    today = now.strftime("%Y-%m-%d")
    store = get_data_store()
    state = store.load_sync(_STORE_NAME)
    if not isinstance(state, dict):
        state = {}
    if state.get("date") != today:
        state["date"] = today
        state["like_count"] = 0
        state["comment_count"] = 0
        state["per_friend"] = {}
    state.setdefault("seen", {})
    state.setdefault("reacted", {})
    state.setdefault("comment_replies", {})
    state.setdefault("comment_actions", {})
    state.setdefault("profile_records", {})
    state.setdefault("per_friend", {})
    state.setdefault("like_count", 0)
    state.setdefault("comment_count", 0)
    return state


def _prune_state_maps(state: dict[str, Any], *, max_items: int = 2000) -> None:
    for key in ("seen", "reacted", "comment_replies", "comment_actions", "profile_records"):
        value = state.get(key)
        if not isinstance(value, dict) or len(value) <= max_items:
            continue
        items = sorted(
            value.items(),
            key=lambda item: float((item[1] if isinstance(item[1], dict) else {}).get("at", 0) or 0),
            reverse=True,
        )
        state[key] = dict(items[:max_items])


def _save_state(state: dict[str, Any], result: dict[str, Any]) -> None:
    state["last_scan_at"] = time.time()
    state["last_result"] = dict(result)
    if result.get("last_error"):
        state["last_error"] = str(result.get("last_error") or "")[:240]
    else:
        state["last_error"] = ""
    _prune_state_maps(state)
    get_data_store().save_sync(_STORE_NAME, state)


def _save_inbound_state(state: dict[str, Any], result: dict[str, Any]) -> None:
    state["last_inbound_scan_at"] = time.time()
    state["last_inbound_result"] = dict(result)
    if result.get("last_error"):
        state["last_inbound_error"] = str(result.get("last_error") or "")[:240]
    else:
        state["last_inbound_error"] = ""
    _prune_state_maps(state)
    get_data_store().save_sync(_STORE_NAME, state)


def _daily_friend_state(state: dict[str, Any], user_id: str, today: str) -> dict[str, Any]:
    per_friend = state.setdefault("per_friend", {})
    if not isinstance(per_friend, dict):
        per_friend = {}
        state["per_friend"] = per_friend
    item = per_friend.get(user_id)
    if not isinstance(item, dict) or item.get("date") != today:
        item = {"date": today, "like_count": 0, "comment_count": 0, "action_count": 0}
        per_friend[user_id] = item
    return item


def _mark_seen(state: dict[str, Any], feed_key: str) -> None:
    seen = state.setdefault("seen", {})
    if isinstance(seen, dict):
        seen[feed_key] = {"at": time.time()}


def _mark_reacted(state: dict[str, Any], feed_key: str, *, action: str, comment: str = "") -> None:
    reacted = state.setdefault("reacted", {})
    if isinstance(reacted, dict):
        reacted[feed_key] = {"at": time.time(), "action": action, "comment": comment}


def _feed_already_reacted(state: dict[str, Any], feed_key: str) -> bool:
    reacted = state.get("reacted")
    return isinstance(reacted, dict) and feed_key in reacted


def _comment_already_replied(state: dict[str, Any], comment_key: str) -> bool:
    replies = state.get("comment_replies")
    return isinstance(replies, dict) and comment_key in replies


def _mark_comment_replied(state: dict[str, Any], comment_key: str, *, reply: str) -> None:
    replies = state.setdefault("comment_replies", {})
    if isinstance(replies, dict):
        replies[comment_key] = {"at": time.time(), "reply": reply}


def _comment_already_processed(state: dict[str, Any], comment_key: str) -> bool:
    actions = state.get("comment_actions")
    if isinstance(actions, dict) and comment_key in actions:
        return True
    return _comment_already_replied(state, comment_key)


def _mark_comment_processed(
    state: dict[str, Any],
    comment_key: str,
    *,
    action: str,
    reply: str = "",
    reason: str = "",
) -> None:
    actions = state.setdefault("comment_actions", {})
    if isinstance(actions, dict):
        actions[comment_key] = {
            "at": time.time(),
            "action": str(action or "ignore"),
            "reply": str(reply or "")[:80],
            "reason": str(reason or "")[:120],
        }
    if action == "reply" and reply:
        _mark_comment_replied(state, comment_key, reply=reply)


def _profile_evidence_already_recorded(state: dict[str, Any], evidence_key: str) -> bool:
    records = state.get("profile_records")
    return isinstance(records, dict) and evidence_key in records


def _mark_profile_evidence_recorded(state: dict[str, Any], evidence_key: str) -> None:
    records = state.setdefault("profile_records", {})
    if isinstance(records, dict):
        records[evidence_key] = {"at": time.time()}


async def _record_qzone_profile_evidence(
    *,
    persona_store: Any,
    user_id: str,
    evidence_key: str,
    kind: str,
    content: str,
    image_summary: str = "",
    state: dict[str, Any],
    result: dict[str, Any],
    logger: Any,
) -> None:
    if persona_store is None or not user_id or not evidence_key:
        return
    if _profile_evidence_already_recorded(state, evidence_key):
        return
    text = str(content or "").strip()
    visual = str(image_summary or "").strip()
    if not text and not visual:
        return
    lines = [f"[QQ空间{kind}]"]
    if text:
        lines.append(text[:240])
    if visual:
        lines.append(f"图片摘要：{visual[:180]}")
    record_message = getattr(persona_store, "record_message", None)
    if not callable(record_message):
        return
    try:
        await record_message(str(user_id), "\n".join(lines))
    except Exception as exc:
        logger.warning(f"[qzone_social] record qzone profile evidence failed for {user_id}: {exc}")
        return
    _mark_profile_evidence_recorded(state, evidence_key)
    result["profile_records"] = int(result.get("profile_records", 0) or 0) + 1


def _collect_candidates(
    *,
    friend_profiles: dict[str, dict[str, Any]],
    proactive_state: dict[str, dict[str, Any]],
    persona_store: Any,
    persona_snippet_max_chars: int,
    target_user_id: str = "",
    allow_open_user: bool = False,
) -> list[dict[str, Any]]:
    if target_user_id:
        profile = friend_profiles.get(str(target_user_id))
        if not profile and not allow_open_user:
            return []
        snippet = _get_persona_snippet(persona_store, str(target_user_id), persona_snippet_max_chars)
        state = proactive_state.get(str(target_user_id), {})
        return [
            {
                "user_id": str(target_user_id),
                "nickname": str((profile or {}).get("nickname", "") or target_user_id),
                "persona_snippet": snippet or "暂无画像",
                "last_interaction": float((state if isinstance(state, dict) else {}).get("last_interaction", 0) or 0),
                "is_friend": bool(profile),
            }
        ]

    candidates: list[dict[str, Any]] = []
    for user_id, user_state in proactive_state.items():
        uid = str(user_id)
        if uid.startswith("group_") or uid not in friend_profiles:
            continue
        if not isinstance(user_state, dict):
            continue
        last_interaction = float(user_state.get("last_interaction", 0) or 0)
        if last_interaction <= 0:
            continue
        profile = friend_profiles[uid]
        snippet = _get_persona_snippet(persona_store, uid, persona_snippet_max_chars)
        candidates.append(
            {
                "user_id": uid,
                "nickname": str(profile.get("nickname", "") or uid),
                "persona_snippet": snippet,
                "last_interaction": last_interaction,
                "is_friend": True,
            }
        )
    candidates.sort(key=lambda item: float(item.get("last_interaction", 0) or 0), reverse=True)
    if candidates:
        return candidates[:12]

    # Fresh deployments may have friend access and Qzone permission before private
    # proactive_state has any last_interaction records. Still keep the hard bot-friend
    # boundary; unreadable spaces are filtered by fetch_user_feeds permissions.
    fallback: list[dict[str, Any]] = []
    for uid, profile in friend_profiles.items():
        user_id = str(uid)
        if not user_id or user_id.startswith("group_"):
            continue
        snippet = _get_persona_snippet(persona_store, user_id, persona_snippet_max_chars)
        fallback.append(
            {
                "user_id": user_id,
                "nickname": str(profile.get("nickname", "") or user_id),
                "persona_snippet": snippet,
                "last_interaction": 0.0,
                "is_friend": True,
            }
        )
    fallback.sort(key=lambda item: item["user_id"])
    return fallback[:12]


async def _summarize_images(
    *,
    vision_caller: Any,
    images: list[str],
    logger: Any,
) -> str:
    if not vision_caller or not images:
        return ""
    return await summarize_images_with_vision(
        vision_caller=vision_caller,
        image_urls=images[:3],
        sticker_like=False,
        sticker_prompt="请用一句话描述这张 QQ 空间动态配图的主体和情绪氛围。",
        person_prompt="请用一句话描述这张 QQ 空间动态配图的主体、场景和情绪氛围。不要编造图中不存在的文字或事件。",
        cache_namespace="qzone_social_v1",
        logger=logger,
    )


async def _decide_feed_action(
    *,
    feed: dict[str, Any],
    candidate: dict[str, Any],
    system_prompt: str,
    call_ai_api: Callable[..., Awaitable[Optional[str]]],
    image_summary: str,
    inner_state: dict[str, Any],
    emotion_memory: str,
) -> dict[str, Any]:
    prompt = (
        "你正在阅读一位用户刚发的 QQ 空间动态。请判断是否要互动。\n"
        "输出严格 JSON：{\"action\":\"ignore|like|comment|like_comment\",\"comment\":\"可选评论\",\"reason\":\"极短原因\"}。\n\n"
        f"好友 QQ：{candidate['user_id']}\n"
        f"好友昵称：{candidate['nickname']}\n"
        f"好友画像：{candidate.get('persona_snippet') or '暂无'}\n"
        f"上次互动：{_format_ts(float(candidate.get('last_interaction', 0) or 0))}\n"
        f"近期情绪记忆：{emotion_memory or '暂无'}\n\n"
        f"你的当前心情：{inner_state.get('mood', DEFAULT_STATE['mood'])}\n"
        f"你的当前精力：{inner_state.get('energy', DEFAULT_STATE['energy'])}\n\n"
        f"动态作者：{feed.get('nickname') or candidate['nickname']}（{feed.get('owner_uin') or candidate['user_id']}）\n"
        f"动态时间戳：{feed.get('created_at') or 0}\n"
        f"动态正文：{feed.get('content') or '（无文字）'}\n"
        f"图片摘要：{image_summary or ('有图片，但视觉摘要不可用' if feed.get('images') else '无图片')}\n\n"
        "互动要求：\n"
        "- 如果没什么好说，action=ignore。\n"
        "- 点赞适合轻量表达在看；评论必须像真人随手留言，短、日常、贴合你的人设。\n"
        "- 评论不超过 30 个中文字符，不要小作文，不要客服腔，不要互联网黑话和梗。\n"
        "- 不要暴露你在分析画像、情绪记忆或系统规则。\n"
        "- 对沉重、争议、隐私、求助、疾病、事故等不适合轻浮互动的内容，优先 ignore。\n"
        "- 图片摘要不可用时，不要评论具体画面。"
    )
    messages = inject_current_time_context(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ]
    )
    result = await call_ai_api(messages)
    payload = _extract_json_object(result)
    if payload is None:
        return {"action": "ignore", "comment": "", "reason": "llm_parse_failed"}
    action = str(payload.get("action", "") or "").strip().lower()
    if action not in {"ignore", "like", "comment", "like_comment"}:
        action = "ignore"
    comment = _trim_comment(payload.get("comment", ""))
    if action in {"comment", "like_comment"} and not comment:
        action = "like" if action == "like_comment" else "ignore"
    return {
        "action": action,
        "comment": comment,
        "reason": str(payload.get("reason", "") or "").strip()[:80],
    }


async def _decide_bot_comment_reply(
    *,
    feed: dict[str, Any],
    comment: dict[str, Any],
    commenter_profile: dict[str, Any],
    system_prompt: str,
    call_ai_api: Callable[..., Awaitable[Optional[str]]],
    inner_state: dict[str, Any],
    emotion_memory: str,
) -> dict[str, Any]:
    prompt = (
        "有人在你的 QQ 空间动态下留言。把它当作对你的私聊式互动来理解，判断是否自然回复。\n"
        "输出严格 JSON：{\"action\":\"ignore|reply\",\"reply\":\"可选回复\",\"reason\":\"极短原因\"}。\n\n"
        f"留言用户 QQ：{comment['user_id']}\n"
        f"留言用户昵称：{comment.get('nickname') or commenter_profile.get('nickname') or comment['user_id']}\n"
        f"用户画像：{commenter_profile.get('persona_snippet') or '暂无'}\n"
        f"近期情绪记忆：{emotion_memory or '暂无'}\n\n"
        f"你的当前心情：{inner_state.get('mood', DEFAULT_STATE['mood'])}\n"
        f"你的当前精力：{inner_state.get('energy', DEFAULT_STATE['energy'])}\n\n"
        f"你的空间动态正文：{feed.get('content') or '（无文字）'}\n"
        f"对方留言：{comment.get('content') or ''}\n\n"
        "回复要求：\n"
        "- 这是一条别人对你说的话，不要解释“这句话是什么意思”，不要说“太泛了/要看上下文”。\n"
        "- 如果适合接话，action=reply，回复 8-40 个中文字符，像在空间评论区随手回一句。\n"
        "- 如果确实不适合回复，action=ignore。\n"
        "- 不要客服腔、小作文、互联网黑话、系统说明，也不要暴露画像或规则。"
    )
    messages = inject_current_time_context(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ]
    )
    result = await call_ai_api(messages)
    payload = _extract_json_object(result)
    if payload is None:
        return {"action": "ignore", "reply": "", "reason": "llm_parse_failed"}
    action = str(payload.get("action", "") or "").strip().lower()
    reply = _trim_comment(payload.get("reply", ""), max_chars=44)
    if action != "reply" or not reply:
        return {"action": "ignore", "reply": "", "reason": str(payload.get("reason", "") or "")[:80]}
    return {"action": "reply", "reply": reply, "reason": str(payload.get("reason", "") or "")[:80]}


def _apply_action_limits(
    *,
    decision: dict[str, Any],
    state: dict[str, Any],
    friend_state: dict[str, Any],
    like_limit: int,
    comment_limit: int,
    per_friend_limit: int,
) -> dict[str, Any]:
    action = str(decision.get("action", "") or "ignore")
    if action == "ignore":
        return decision
    if _limit_reached(per_friend_limit, friend_state.get("action_count", 0)):
        return {"action": "ignore", "comment": "", "reason": "per_friend_limit_reached"}
    want_like = action in {"like", "like_comment"}
    want_comment = action in {"comment", "like_comment"}
    if want_like and _limit_reached(like_limit, state.get("like_count", 0)):
        want_like = False
    if want_comment and _limit_reached(comment_limit, state.get("comment_count", 0)):
        want_comment = False
    if want_like and want_comment:
        action = "like_comment"
    elif want_like:
        action = "like"
    elif want_comment:
        action = "comment"
    else:
        action = "ignore"
    updated = dict(decision)
    updated["action"] = action
    if action == "ignore":
        updated["comment"] = ""
        updated["reason"] = "limit_reached"
    return updated


async def _scan_bot_space_comments(
    *,
    bot: Any,
    qzone_social_service: Any,
    friend_profiles: dict[str, dict[str, Any]],
    proactive_state: dict[str, dict[str, Any]],
    persona_store: Any,
    persona_snippet_max_chars: int,
    system_prompt: str,
    call_ai_api: Callable[..., Awaitable[Optional[str]]],
    inner_state: dict[str, Any],
    emotion_state: dict[str, Any],
    max_feeds: int,
    max_comments_per_feed: int = 20,
    count_checked_feeds: bool = False,
    state: dict[str, Any],
    result: dict[str, Any],
    logger: Any,
) -> None:
    bot_id = str(getattr(bot, "self_id", "") or "")
    if not bot_id:
        return
    ok, msg, feeds = await qzone_social_service.fetch_user_feeds(
        target_uin=bot_id,
        bot_id=bot_id,
        count=max(1, min(100, int(max_feeds or 20))),
        include_comments=True,
        comment_count=max_comments_per_feed,
    )
    if not ok:
        result["failed"] = int(result.get("failed", 0) or 0) + 1
        result["last_error"] = msg
        logger.warning(f"[qzone_social] fetch bot feeds failed: {msg}")
        return

    checked_feeds = feeds[: max(1, min(100, int(max_feeds or 20)))]
    if count_checked_feeds:
        result["scanned_users"] = int(result.get("scanned_users", 0) or 0) + 1
        result["feeds_seen"] = int(result.get("feeds_seen", 0) or 0) + len(checked_feeds)

    for feed in checked_feeds:
        feed_key = str(feed.get("feed_key", "") or "")
        comments = _extract_qzone_comments(feed.get("raw") if isinstance(feed.get("raw"), dict) else {})
        for comment in comments[: max(1, min(100, int(max_comments_per_feed or 20)))]:
            commenter_id = str(comment.get("user_id", "") or "")
            if not commenter_id or commenter_id == bot_id:
                continue
            # 自动回复自己的空间留言仍限制为 bot 好友；开放用户只允许通过测试命令主动探测。
            if commenter_id not in friend_profiles:
                continue
            comment_key = f"{feed_key}:{comment.get('comment_key') or commenter_id}"
            if _comment_already_processed(state, comment_key):
                continue
            result["inbound_comments"] = int(result.get("inbound_comments", 0) or 0) + 1
            await _record_qzone_profile_evidence(
                persona_store=persona_store,
                user_id=commenter_id,
                evidence_key=f"comment:{comment_key}",
                kind="留言",
                content=str(comment.get("content", "") or ""),
                state=state,
                result=result,
                logger=logger,
            )
            profile = friend_profiles.get(commenter_id, {})
            persona_snippet = _get_persona_snippet(persona_store, commenter_id, persona_snippet_max_chars)
            commenter_profile = {
                "nickname": str(profile.get("nickname", "") or comment.get("nickname") or commenter_id),
                "persona_snippet": persona_snippet,
                "last_interaction": float(
                    (proactive_state.get(commenter_id, {}) if isinstance(proactive_state.get(commenter_id), dict) else {})
                    .get("last_interaction", 0)
                    or 0
                ),
            }
            emotion_memory = describe_user_emotion_memory(emotion_state or {}, commenter_id)
            decision = await _decide_bot_comment_reply(
                feed=feed,
                comment=comment,
                commenter_profile=commenter_profile,
                system_prompt=system_prompt,
                call_ai_api=call_ai_api,
                inner_state=inner_state,
                emotion_memory=emotion_memory,
            )
            if str(decision.get("action", "") or "") != "reply":
                _mark_comment_processed(
                    state,
                    comment_key,
                    action="ignore",
                    reason=str(decision.get("reason", "") or ""),
                )
                continue
            reply = str(decision.get("reply", "") or "").strip()
            if not reply:
                _mark_comment_processed(
                    state,
                    comment_key,
                    action="ignore",
                    reason=str(decision.get("reason", "") or "empty_reply"),
                )
                continue
            ok_reply, reply_msg = await qzone_social_service.comment_feed(
                feed=feed,
                bot_id=bot_id,
                content=reply,
            )
            if ok_reply:
                result["replied"] = int(result.get("replied", 0) or 0) + 1
                _mark_comment_processed(state, comment_key, action="reply", reply=reply)
            else:
                result["failed"] = int(result.get("failed", 0) or 0) + 1
                result["last_error"] = reply_msg
                logger.warning(f"[qzone_social] reply comment failed for {comment_key}: {reply_msg}")


async def scan_qzone_social_feeds(
    *,
    bot: Any,
    plugin_config: Any,
    qzone_social_service: Any,
    load_prompt: Callable[[], Any],
    call_ai_api: Callable[..., Awaitable[Optional[str]]],
    load_proactive_state: Callable[[], dict[str, dict[str, Any]]],
    get_now: Callable[[], datetime],
    logger: Any,
    persona_store: Any = None,
    vision_caller: Any = None,
    agent_data_dir: Any = None,
    target_user_id: str = "",
    allow_open_user: bool = False,
) -> dict[str, Any]:
    if _SCAN_LOCK.locked():
        return {"ok": False, "skipped": True, "last_error": "qzone_social_scan_already_running"}
    async with _SCAN_LOCK:
        now = get_now()
        today = now.strftime("%Y-%m-%d")
        state = _normalize_state(now)
        result: dict[str, Any] = {
            "ok": True,
            "target_user_id": str(target_user_id or ""),
            "scanned_users": 0,
            "feeds_seen": 0,
            "liked": 0,
            "commented": 0,
            "ignored": 0,
            "failed": 0,
            "inbound_comments": 0,
            "replied": 0,
            "profile_records": 0,
            "last_error": "",
        }
        try:
            friend_profiles = await _get_friend_profiles(bot, logger)
            if not friend_profiles and not (target_user_id and allow_open_user):
                result["ok"] = False
                result["last_error"] = "no_friend_profiles"
                _save_state(state, result)
                return result

            proactive_state = load_proactive_state() or {}
            persona_snippet_max_chars = int(
                getattr(plugin_config, "personification_persona_snippet_max_chars", 150)
            )
            candidates = _collect_candidates(
                friend_profiles=friend_profiles,
                proactive_state=proactive_state,
                persona_store=persona_store,
                persona_snippet_max_chars=persona_snippet_max_chars,
                target_user_id=str(target_user_id or ""),
                allow_open_user=allow_open_user,
            )
            if not candidates:
                result["ok"] = False
                result["skipped"] = True
                result["last_error"] = "target_not_bot_friend" if target_user_id else "no_candidates"
                _save_state(state, result)
                return result

            inner_state = dict(DEFAULT_STATE)
            if agent_data_dir is not None:
                try:
                    inner_state.update(await load_inner_state(agent_data_dir))
                except Exception as exc:
                    logger.warning(f"[qzone_social] load inner_state failed: {exc}")
            emotion_state = {}
            if agent_data_dir is not None:
                try:
                    emotion_state = await load_emotion_state(agent_data_dir)
                except Exception as exc:
                    logger.warning(f"[qzone_social] load emotion_state failed: {exc}")

            system_prompt = _extract_system_prompt(load_prompt())
            max_feeds = max(1, int(getattr(plugin_config, "personification_qzone_social_max_feeds_per_scan", 10)))
            like_limit = _normalize_limit(getattr(plugin_config, "personification_qzone_social_like_limit", 0))
            comment_limit = _normalize_limit(getattr(plugin_config, "personification_qzone_social_comment_limit", 0))
            per_friend_limit = _normalize_limit(
                getattr(plugin_config, "personification_qzone_social_per_friend_limit", 0)
            )

            processed_feeds = 0
            for candidate in candidates:
                if processed_feeds >= max_feeds:
                    break
                ok, msg, feeds = await qzone_social_service.fetch_user_feeds(
                    target_uin=str(candidate["user_id"]),
                    bot_id=str(getattr(bot, "self_id", "") or ""),
                    count=max_feeds,
                    include_comments=False,
                )
                result["scanned_users"] += 1
                if not ok:
                    result["failed"] += 1
                    result["last_error"] = msg
                    logger.warning(f"[qzone_social] fetch feeds failed for {candidate['user_id']}: {msg}")
                    continue
                for feed in feeds:
                    if processed_feeds >= max_feeds:
                        break
                    feed_key = str(feed.get("feed_key", "") or "")
                    if not feed_key or _feed_already_reacted(state, feed_key):
                        continue
                    processed_feeds += 1
                    result["feeds_seen"] += 1
                    _mark_seen(state, feed_key)
                    image_summary = await _summarize_images(
                        vision_caller=vision_caller,
                        images=list(feed.get("images") or []),
                        logger=logger,
                    )
                    await _record_qzone_profile_evidence(
                        persona_store=persona_store,
                        user_id=str(candidate["user_id"]),
                        evidence_key=f"feed:{feed_key}",
                        kind="动态",
                        content=str(feed.get("content", "") or ""),
                        image_summary=image_summary,
                        state=state,
                        result=result,
                        logger=logger,
                    )
                    emotion_memory = describe_user_emotion_memory(emotion_state or {}, str(candidate["user_id"]))
                    decision = await _decide_feed_action(
                        feed=feed,
                        candidate=candidate,
                        system_prompt=system_prompt,
                        call_ai_api=call_ai_api,
                        image_summary=image_summary,
                        inner_state=inner_state,
                        emotion_memory=emotion_memory,
                    )
                    friend_state = _daily_friend_state(state, str(candidate["user_id"]), today)
                    decision = _apply_action_limits(
                        decision=decision,
                        state=state,
                        friend_state=friend_state,
                        like_limit=like_limit,
                        comment_limit=comment_limit,
                        per_friend_limit=per_friend_limit,
                    )
                    action = str(decision.get("action", "") or "ignore")
                    if action == "ignore":
                        result["ignored"] += 1
                        continue

                    acted = False
                    comment_text = str(decision.get("comment", "") or "")
                    if action in {"like", "like_comment"}:
                        like_ok, like_msg = await qzone_social_service.like_feed(
                            feed=feed,
                            bot_id=str(getattr(bot, "self_id", "") or ""),
                        )
                        if like_ok:
                            acted = True
                            result["liked"] += 1
                            state["like_count"] = int(state.get("like_count", 0) or 0) + 1
                            friend_state["like_count"] = int(friend_state.get("like_count", 0) or 0) + 1
                        else:
                            result["failed"] += 1
                            result["last_error"] = like_msg
                            logger.warning(f"[qzone_social] like failed for {feed_key}: {like_msg}")
                    if action in {"comment", "like_comment"} and comment_text:
                        comment_ok, comment_msg = await qzone_social_service.comment_feed(
                            feed=feed,
                            bot_id=str(getattr(bot, "self_id", "") or ""),
                            content=comment_text,
                        )
                        if comment_ok:
                            acted = True
                            result["commented"] += 1
                            state["comment_count"] = int(state.get("comment_count", 0) or 0) + 1
                            friend_state["comment_count"] = int(friend_state.get("comment_count", 0) or 0) + 1
                        else:
                            result["failed"] += 1
                            result["last_error"] = comment_msg
                            logger.warning(f"[qzone_social] comment failed for {feed_key}: {comment_msg}")
                    if acted:
                        friend_state["action_count"] = int(friend_state.get("action_count", 0) or 0) + 1
                        _mark_reacted(state, feed_key, action=action, comment=comment_text)
                    else:
                        result["ignored"] += 1
            if not target_user_id and friend_profiles:
                await _scan_bot_space_comments(
                    bot=bot,
                    qzone_social_service=qzone_social_service,
                    friend_profiles=friend_profiles,
                    proactive_state=proactive_state,
                    persona_store=persona_store,
                    persona_snippet_max_chars=persona_snippet_max_chars,
                    system_prompt=system_prompt,
                    call_ai_api=call_ai_api,
                    inner_state=inner_state,
                    emotion_state=emotion_state,
                    max_feeds=max_feeds,
                    state=state,
                    result=result,
                    logger=logger,
                )
            _save_state(state, result)
            return result
        except Exception as exc:
            result["ok"] = False
            result["last_error"] = str(exc)
            _save_state(state, result)
            logger.warning(f"[qzone_social] scan failed: {exc}")
            return result


async def scan_qzone_inbound_messages(
    *,
    bot: Any,
    plugin_config: Any,
    qzone_social_service: Any,
    load_prompt: Callable[[], Any],
    call_ai_api: Callable[..., Awaitable[Optional[str]]],
    load_proactive_state: Callable[[], dict[str, dict[str, Any]]],
    get_now: Callable[[], datetime],
    logger: Any,
    persona_store: Any = None,
    agent_data_dir: Any = None,
) -> dict[str, Any]:
    """Poll comments under the bot's own Qzone feeds and let the LLM decide replies."""
    if _SCAN_LOCK.locked():
        return {"ok": False, "skipped": True, "last_error": "qzone_scan_already_running"}
    async with _SCAN_LOCK:
        state = _normalize_state(get_now())
        result: dict[str, Any] = {
            "ok": True,
            "target_user_id": str(getattr(bot, "self_id", "") or ""),
            "scanned_users": 0,
            "feeds_seen": 0,
            "liked": 0,
            "commented": 0,
            "ignored": 0,
            "failed": 0,
            "inbound_comments": 0,
            "replied": 0,
            "profile_records": 0,
            "last_error": "",
        }
        try:
            friend_profiles = await _get_friend_profiles(bot, logger)
            if not friend_profiles:
                result["ok"] = False
                result["skipped"] = True
                result["last_error"] = "no_friend_profiles"
                _save_inbound_state(state, result)
                return result

            proactive_state = load_proactive_state() or {}
            persona_snippet_max_chars = int(
                getattr(plugin_config, "personification_persona_snippet_max_chars", 150)
            )
            inner_state = dict(DEFAULT_STATE)
            emotion_state = {}
            if agent_data_dir is not None:
                try:
                    inner_state.update(await load_inner_state(agent_data_dir))
                except Exception as exc:
                    logger.warning(f"[qzone_inbound] load inner_state failed: {exc}")
                try:
                    emotion_state = await load_emotion_state(agent_data_dir)
                except Exception as exc:
                    logger.warning(f"[qzone_inbound] load emotion_state failed: {exc}")

            max_feeds = max(
                1,
                int(getattr(plugin_config, "personification_qzone_inbound_max_feeds_per_scan", 20) or 20),
            )
            max_comments_per_feed = max(
                1,
                int(getattr(plugin_config, "personification_qzone_inbound_max_comments_per_feed", 20) or 20),
            )
            await _scan_bot_space_comments(
                bot=bot,
                qzone_social_service=qzone_social_service,
                friend_profiles=friend_profiles,
                proactive_state=proactive_state,
                persona_store=persona_store,
                persona_snippet_max_chars=persona_snippet_max_chars,
                system_prompt=_extract_system_prompt(load_prompt()),
                call_ai_api=call_ai_api,
                inner_state=inner_state,
                emotion_state=emotion_state,
                max_feeds=max_feeds,
                max_comments_per_feed=max_comments_per_feed,
                count_checked_feeds=True,
                state=state,
                result=result,
                logger=logger,
            )
            result["feeds_seen"] = max(0, int(result.get("feeds_seen", 0) or 0))
            _save_inbound_state(state, result)
            return result
        except Exception as exc:
            result["ok"] = False
            result["last_error"] = str(exc)
            _save_inbound_state(state, result)
            logger.warning(f"[qzone_inbound] poll failed: {exc}")
            return result
