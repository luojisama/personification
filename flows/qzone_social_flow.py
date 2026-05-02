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
    state.setdefault("per_friend", {})
    state.setdefault("like_count", 0)
    state.setdefault("comment_count", 0)
    return state


def _prune_state_maps(state: dict[str, Any], *, max_items: int = 500) -> None:
    for key in ("seen", "reacted"):
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


def _collect_candidates(
    *,
    friend_profiles: dict[str, dict[str, Any]],
    proactive_state: dict[str, dict[str, Any]],
    persona_store: Any,
    persona_snippet_max_chars: int,
    target_user_id: str = "",
) -> list[dict[str, Any]]:
    if target_user_id:
        profile = friend_profiles.get(str(target_user_id))
        if not profile:
            return []
        snippet = _get_persona_snippet(persona_store, str(target_user_id), persona_snippet_max_chars)
        state = proactive_state.get(str(target_user_id), {})
        return [
            {
                "user_id": str(target_user_id),
                "nickname": str(profile.get("nickname", "") or target_user_id),
                "persona_snippet": snippet or "暂无画像",
                "last_interaction": float((state if isinstance(state, dict) else {}).get("last_interaction", 0) or 0),
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
            }
        )
    candidates.sort(key=lambda item: float(item.get("last_interaction", 0) or 0), reverse=True)
    return candidates[:12]


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
        "你正在阅读一位 QQ 好友刚发的空间动态。请判断是否要互动。\n"
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
            "last_error": "",
        }
        try:
            friend_profiles = await _get_friend_profiles(bot, logger)
            if not friend_profiles:
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
            )
            if not candidates:
                result["last_error"] = "no_candidates"
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
            _save_state(state, result)
            return result
        except Exception as exc:
            result["ok"] = False
            result["last_error"] = str(exc)
            _save_state(state, result)
            logger.warning(f"[qzone_social] scan failed: {exc}")
            return result
