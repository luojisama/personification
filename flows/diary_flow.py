from __future__ import annotations

import asyncio
import inspect
import json
import random
import re
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from ..agent.inner_state import load_inner_state, update_state_from_diary
from ..core.context_policy import strip_response_control_markers
from ..core.data_store import get_data_store
from ..core.emotion_state import describe_group_emotion_memory, load_emotion_state
from ..skills.skillpacks.image_gen.scripts.impl import generate_image as generate_codex_image


_FLOW_OUTPUT_GUARD = (
    "\n\n本轮你不在群聊中扮演角色，按这条用户消息里要求的纯文本/JSON 输出，"
    "不要使用 <status>/<think>/<action>/<output>/<message> 等思维链 XML 包装。"
)


def filter_sensitive_content(text: str) -> str:
    """Filter obviously unsafe fragments from sampled group history."""
    sensitive_patterns = [
        r"自杀",
        r"跳楼",
        r"毒品",
        r"开盒",
        r"爆照",
        r"约炮",
        r"政治敏感",
        r"血腥",
        r"色情",
    ]

    filtered_text = text
    for pattern in sensitive_patterns:
        filtered_text = re.sub(pattern, "**", filtered_text, flags=re.IGNORECASE)

    if len(filtered_text.strip()) < 2:
        return ""
    return filtered_text


def clean_generated_text(text: str) -> str:
    """Strip model-side thinking/status wrappers."""
    cleaned = re.sub(r"<status.*?>.*?</\s*status\s*>", "", text, flags=re.DOTALL | re.IGNORECASE)
    cleaned = re.sub(r"<think.*?>.*?</\s*think\s*>", "", cleaned, flags=re.DOTALL | re.IGNORECASE)
    cleaned = re.sub(r"</?\s*output.*?>", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"</?\s*message.*?>", "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip()


def _extract_json_object(raw: str) -> dict[str, Any] | None:
    text = str(raw or "").strip()
    if not text:
        return None
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text).rstrip("`").strip()
    try:
        payload = json.loads(text)
    except Exception:
        match = re.search(r"\{[\s\S]*\}", text)
        if not match:
            return None
        try:
            payload = json.loads(match.group(0))
        except Exception:
            return None
    return payload if isinstance(payload, dict) else None


def _trim_qzone_content(text: str, *, max_chars: int = 50) -> str:
    cleaned = clean_generated_text(text)
    cleaned = re.sub(r"^(POST|SKIP)\s*[|：:]\s*", "", cleaned, flags=re.IGNORECASE).strip()
    cleaned = re.sub(r"#[^#\s]{1,24}", "", cleaned).strip()
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if len(cleaned) <= max_chars:
        return cleaned
    candidate = cleaned[:max_chars]
    for index in range(len(candidate) - 1, max(0, len(candidate) - 24), -1):
        if candidate[index] in "。！？!?\n":
            return candidate[: index + 1].strip()
    return candidate.rstrip("，、,. ") + "..."


def _compact_qzone_history_content(content: str) -> str:
    cleaned = re.sub(r"\[IMAGE_B64\][A-Za-z0-9+/=\r\n]+\[/IMAGE_B64\]", "", str(content or ""))
    cleaned = _trim_qzone_content(cleaned, max_chars=80)
    return cleaned.strip()


def _load_recent_qzone_posts(limit: int = 8) -> list[str]:
    try:
        state = get_data_store().load_sync("qzone_post_state")
    except Exception:
        return []
    if not isinstance(state, dict):
        return []
    candidates: list[Any] = []
    recent = state.get("recent_contents")
    if isinstance(recent, list):
        candidates.extend(recent)
    if state.get("last_content"):
        candidates.append(state.get("last_content"))
    items: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        raw = item.get("content") if isinstance(item, dict) else item
        text = _compact_qzone_history_content(str(raw or ""))
        if not text or text in seen:
            continue
        seen.add(text)
        items.append(text)
    return items[-max(1, int(limit)) :]


def _format_recent_qzone_posts(posts: list[str]) -> str:
    if not posts:
        return "- 暂无"
    return "\n".join(f"- {item}" for item in posts[-8:])


def _normalize_similarity_text(text: str) -> str:
    cleaned = _compact_qzone_history_content(text)
    cleaned = re.sub(r"[\s，。！？!?、,.；;：:~…·'\"“”‘’（）()【】\[\]#]+", "", cleaned)
    return cleaned.lower()


def _char_bigrams(text: str) -> set[str]:
    value = _normalize_similarity_text(text)
    if len(value) < 2:
        return {value} if value else set()
    return {value[index : index + 2] for index in range(len(value) - 1)}


def _longest_common_substring_len(left: str, right: str) -> int:
    a = _normalize_similarity_text(left)
    b = _normalize_similarity_text(right)
    if not a or not b:
        return 0
    prev = [0] * (len(b) + 1)
    best = 0
    for ca in a:
        curr = [0]
        for index, cb in enumerate(b, start=1):
            value = prev[index - 1] + 1 if ca == cb else 0
            curr.append(value)
            if value > best:
                best = value
        prev = curr
    return best


def _qzone_post_similarity(left: str, right: str) -> float:
    a = _char_bigrams(left)
    b = _char_bigrams(right)
    if not a or not b:
        return 0.0
    return (2.0 * len(a & b)) / (len(a) + len(b))


def _is_too_similar_to_recent_qzone_post(content: str, recent_posts: list[str]) -> bool:
    text = _normalize_similarity_text(content)
    if len(text) < 6:
        return False
    for recent in recent_posts or []:
        other = _normalize_similarity_text(recent)
        if not other:
            continue
        if text == other or text in other or other in text:
            return True
        longest = _longest_common_substring_len(text, other)
        if longest >= 8:
            return True
        if longest >= 5 and _qzone_post_similarity(text, other) >= 0.42:
            return True
    return False


async def _maybe_generate_qzone_image_marker(
    *,
    tool_caller: Any,
    image_prompt: str,
    logger: Any,
) -> str:
    prompt = str(image_prompt or "").strip()
    if not prompt or tool_caller is None:
        return ""
    try:
        result = await generate_codex_image(
            prompt,
            tool_caller=tool_caller,
            size="1024x1024",
            image_model="gpt-image-2",
        )
    except Exception as exc:
        logger.warning(f"[qzone] Codex 配图生成失败: {exc}")
        return ""
    if not isinstance(result, dict):
        return ""
    b64 = str(result.get("b64_json", "") or "").strip()
    if not b64:
        error = str(result.get("error", "") or "").strip()
        if error:
            logger.warning(f"[qzone] Codex 配图生成失败: {error}")
        return ""
    return f"\n[IMAGE_B64]{b64}[/IMAGE_B64]"


async def _build_qzone_post_with_optional_image(
    *,
    content: str,
    image_prompt: str,
    tool_caller: Any,
    logger: Any,
    recent_posts: Optional[list[str]] = None,
) -> str:
    text = _trim_qzone_content(content)
    if not text:
        return ""
    if _is_too_similar_to_recent_qzone_post(text, recent_posts or []):
        logger.info(f"[qzone] skip generated post because it repeats recent content: {text}")
        return ""
    image_marker = await _maybe_generate_qzone_image_marker(
        tool_caller=tool_caller,
        image_prompt=image_prompt,
        logger=logger,
    )
    return f"{text}{image_marker}"


async def get_recent_chat_context(bot: Any, logger: Any) -> str:
    """Sample recent group messages as diary context."""
    try:
        group_list = await bot.get_group_list()
        if not group_list:
            return ""

        selected_groups = random.sample(group_list, min(2, len(group_list)))
        context_parts = []
        for group in selected_groups:
            group_id = group["group_id"]
            group_name = group.get("group_name", str(group_id))

            try:
                messages = await bot.get_group_msg_history(group_id=group_id, count=50)
            except Exception as e:
                logger.warning(f"[diary] get group history failed: {group_id}: {e}")
                continue

            if not messages or "messages" not in messages:
                continue

            lines = []
            for msg in messages["messages"]:
                sender_name = msg.get("sender", {}).get("nickname", "未知")
                raw_msg = msg.get("message", "")
                content = ""

                if isinstance(raw_msg, list):
                    text_parts = []
                    for seg in raw_msg:
                        if isinstance(seg, dict) and seg.get("type") == "text":
                            text_parts.append(str((seg.get("data") or {}).get("text", "")))
                    content = "".join(text_parts)
                elif isinstance(raw_msg, str):
                    content = re.sub(r"\[CQ:[^\]]+\]", "", raw_msg)

                safe_content = filter_sensitive_content(content)
                if safe_content.strip():
                    lines.append(f"{sender_name}: {safe_content.strip()}")

            if lines:
                context_parts.append(f"群聊 {group_name} 的最近聊天：\n" + "\n".join(lines))

        return "\n\n".join(context_parts)
    except Exception as e:
        logger.error(f"[diary] get recent chat context failed: {e}")
        return ""


async def _generate_once(
    system_prompt: Any,
    user_prompt: str,
    *,
    call_ai_api: Callable[..., Awaitable[Optional[str]]],
    use_builtin_search: bool = False,
) -> str:
    if isinstance(system_prompt, str):
        system_text = system_prompt + _FLOW_OUTPUT_GUARD
    elif isinstance(system_prompt, dict):
        copied = dict(system_prompt)
        sys_text = str(copied.get("system", "") or "")
        copied["system"] = sys_text + _FLOW_OUTPUT_GUARD
        system_text = copied
    else:
        system_text = system_prompt
    messages = [
        {"role": "system", "content": system_text},
        {"role": "user", "content": user_prompt},
    ]
    supports_builtin_search = True
    try:
        signature = inspect.signature(call_ai_api)
        supports_builtin_search = (
            "use_builtin_search" in signature.parameters
            or any(param.kind == inspect.Parameter.VAR_KEYWORD for param in signature.parameters.values())
        )
    except (TypeError, ValueError):
        supports_builtin_search = True
    if supports_builtin_search:
        result = await call_ai_api(messages, use_builtin_search=use_builtin_search)
    else:
        result = await call_ai_api(messages)
    if not result:
        return ""
    # 兜底：若 LLM 仍输出思维链 XML，剥除标签再走 clean_generated_text
    stripped = strip_response_control_markers(result)
    return clean_generated_text(stripped or result)


def _schedule_diary_state_update(
    *,
    diary_text: str,
    tool_caller: Any,
    data_dir: Optional[Path],
    logger: Any,
) -> None:
    if not diary_text or tool_caller is None or data_dir is None:
        return
    asyncio.create_task(
        update_state_from_diary(
            diary_text,
            Path(data_dir),
            tool_caller,
            logger,
        )
    )


async def generate_ai_diary(
    bot: Any,
    *,
    load_prompt: Callable[[], Any],
    call_ai_api: Callable[..., Awaitable[Optional[str]]],
    logger: Any,
    tool_caller: Any = None,
    data_dir: Optional[Path] = None,
) -> str:
    """Generate a short Qzone post from recent chat context."""
    system_prompt = load_prompt()
    chat_context = await get_recent_chat_context(bot, logger)
    recent_posts = _load_recent_qzone_posts()
    emotion_hint = ""
    if data_dir is not None:
        try:
            emotion_state = await load_emotion_state(Path(data_dir))
            group_hints = [
                describe_group_emotion_memory(emotion_state, str(group_id))
                for group_id in list((emotion_state or {}).get("per_group", {}).keys())[:3]
            ]
            group_hints = [hint for hint in group_hints if hint]
            if group_hints:
                emotion_hint = "最近群情绪记忆：\n" + "\n".join(f"- {hint}" for hint in group_hints)
        except Exception as e:
            logger.debug(f"[diary] load emotion_state failed: {e}")

    base_requirements = (
        "请写一条自然、像真人随手发的 QQ 空间说说，不要写周记小作文。\n"
        "输出严格 JSON：{\"content\":\"正文\",\"image_prompt\":\"可选英文配图提示词\"}。\n"
        "1. 正文 12-50 个中文字符，像随手发的一句日常碎碎念。\n"
        "2. 只抓一个很小的生活瞬间或念头，不要总结聊天、日报、作文或公告。\n"
        "3. 允许跳跃：可以半句话突然转到另一个画面，或在一个观察后接一句不相关的吐槽；不必上下文连贯。\n"
        "4. 标点可以省略，可用空格、句号、问号代替逗号；可以是反问、牢骚、发现、未说完的半句；不必有结论。\n"
        "5. 语气贴合角色，但不要互联网黑话、热梗、夸张营业感、AI 客服腔、对仗工整的总结句。\n"
        "6. 不要列条目、不要标题、不要 hashtag、不要说自己是 AI。\n"
        "7. 必须避开最近说说已经反复出现的具体意象、食物、动作和句式；如果最近写过，就换一个完全不同的触发点。\n"
        "8. 可以从最近群聊、今天的游戏/动漫/轻新闻里抓一个很小的细节当触发点，但写成自己的即时反应，不要新闻播报。\n"
        "9. image_prompt 只有在适合配一张日常氛围图时填写英文画面描述；不适合就留空。"
    )
    recent_block = "最近已经发过的说说，禁止复读这些内容或近似句式：\n" + _format_recent_qzone_posts(recent_posts)

    if chat_context:
        rich_prompt = (
            "请结合下面这些最近聊天内容，写一条带一点生活感的 QQ 空间说说。\n"
            "不要逐条复述聊天记录，而是把它们消化成自己的感受、吐槽或碎碎念。\n\n"
            f"{chat_context}\n\n"
            f"{emotion_hint}\n\n"
            f"{recent_block}\n\n"
            f"{base_requirements}"
        )
        raw_rich_result = await _generate_once(
            system_prompt,
            rich_prompt,
            call_ai_api=call_ai_api,
            use_builtin_search=True,
        )
        payload = _extract_json_object(raw_rich_result)
        rich_result = ""
        if payload:
            rich_result = await _build_qzone_post_with_optional_image(
                content=str(payload.get("content", "") or ""),
                image_prompt=str(payload.get("image_prompt", "") or ""),
                tool_caller=tool_caller,
                logger=logger,
                recent_posts=recent_posts,
            )
        elif raw_rich_result:
            rich_result = _trim_qzone_content(raw_rich_result)
            if _is_too_similar_to_recent_qzone_post(rich_result, recent_posts):
                logger.info(f"[qzone] skip rich raw post because it repeats recent content: {rich_result}")
                rich_result = ""
        if rich_result:
            _schedule_diary_state_update(
                diary_text=rich_result,
                tool_caller=tool_caller,
                data_dir=data_dir,
                logger=logger,
            )
            return rich_result

        logger.warning("[diary] rich prompt generation failed, fallback to basic prompt")

    basic_prompt = (
        "请直接写一条自然的短说说，像是角色自己随手发的碎碎念。\n"
        "如果没有群聊素材，可以借助模型内置搜索/常识，从今天的游戏、动漫、轻新闻里挑一个小触发点。\n\n"
        f"{recent_block}\n\n"
        f"{base_requirements}"
    )
    raw_result = await _generate_once(
        system_prompt,
        basic_prompt,
        call_ai_api=call_ai_api,
        use_builtin_search=True,
    )
    payload = _extract_json_object(raw_result)
    if payload:
        result = await _build_qzone_post_with_optional_image(
            content=str(payload.get("content", "") or ""),
            image_prompt=str(payload.get("image_prompt", "") or ""),
            tool_caller=tool_caller,
            logger=logger,
            recent_posts=recent_posts,
        )
    else:
        result = _trim_qzone_content(raw_result)
        if _is_too_similar_to_recent_qzone_post(result, recent_posts):
            logger.info(f"[qzone] skip basic raw post because it repeats recent content: {result}")
            result = ""
    if result:
        _schedule_diary_state_update(
            diary_text=result,
            tool_caller=tool_caller,
            data_dir=data_dir,
            logger=logger,
        )
    return result


async def maybe_generate_proactive_qzone_post(
    bot: Any,
    *,
    load_prompt: Callable[[], Any],
    call_ai_api: Callable[..., Awaitable[Optional[str]]],
    logger: Any,
    data_dir: Optional[Path] = None,
    tool_caller: Any = None,
) -> str:
    """根据近期聊天与内心状态决定是否主动发一条更日常的空间动态。"""
    system_prompt = load_prompt()
    chat_context = await get_recent_chat_context(bot, logger)
    if not chat_context:
        chat_context = "最近群聊可用文本很少；可以把今天的游戏、动漫、轻新闻或自己的状态当成触发点。"
    recent_posts = _load_recent_qzone_posts()

    inner_state = {}
    if data_dir is not None:
        try:
            inner_state = await load_inner_state(Path(data_dir))
        except Exception as e:
            logger.warning(f"[qzone] load inner_state failed: {e}")
    emotion_hint = ""
    if data_dir is not None:
        try:
            emotion_state = await load_emotion_state(Path(data_dir))
            group_hints = [
                describe_group_emotion_memory(emotion_state, str(group_id))
                for group_id in list((emotion_state or {}).get("per_group", {}).keys())[:3]
            ]
            group_hints = [hint for hint in group_hints if hint]
            if group_hints:
                emotion_hint = "\n".join(f"- {hint}" for hint in group_hints)
        except Exception as e:
            logger.debug(f"[qzone] load emotion_state failed: {e}")

    mood = str((inner_state or {}).get("mood", "平静") or "平静")
    energy = str((inner_state or {}).get("energy", "正常") or "正常")
    pending = (inner_state or {}).get("pending_thoughts", [])
    pending_lines = []
    if isinstance(pending, list):
        for item in pending[-4:]:
            if not isinstance(item, dict):
                continue
            thought = str(item.get("thought", "") or "").strip()
            if thought:
                pending_lines.append(f"- {thought}")
    pending_block = "\n".join(pending_lines) if pending_lines else "- 无明显挂念"

    decision_prompt = (
        "你现在在考虑要不要发一条 QQ 空间说说。\n"
        "请基于最近聊天内容、当前心情和挂念，判断你此刻是不是真的有想发动态的冲动。\n"
        "输出严格 JSON：{\"action\":\"skip|post\",\"content\":\"正文\",\"image_prompt\":\"可选英文配图提示词\",\"reason\":\"极短原因\"}。\n\n"
        f"当前心情：{mood}\n"
        f"当前精力：{energy}\n"
        f"最近挂念：\n{pending_block}\n\n"
        f"近期群情绪记忆：\n{emotion_hint or '- 暂无明显群情绪记忆'}\n\n"
        f"最近聊天片段：\n{chat_context}\n\n"
        "最近已经发过的说说，禁止复读这些内容或近似句式：\n"
        f"{_format_recent_qzone_posts(recent_posts)}\n\n"
        "要求：\n"
        "1. 如果没有明确想说的话，action=skip。\n"
        "2. 如果想发，action=post，并给出 content。\n"
        "3. 正文 12-50 个中文字符，像真人随手发的一句话，别写长篇大段。\n"
        "4. 只写一个小瞬间、小吐槽或突然想到的念头，不要列表、标题、hashtag 或总结腔。\n"
        "5. 不要为了发而发，不要重复最近已经说过很多遍的话题，不要互联网黑话和热梗。\n"
        "6. 可以从今天的游戏、动漫、轻新闻里抓一个细节，但必须写成自己的日常反应，不要像新闻标题。\n"
        "7. 如果适合配图，image_prompt 写英文画面描述，要求贴合人设和正文氛围；不适合就留空。"
    )
    result = await _generate_once(
        system_prompt,
        decision_prompt,
        call_ai_api=call_ai_api,
        use_builtin_search=True,
    )
    if not result:
        return ""
    payload = _extract_json_object(result)
    if payload:
        if str(payload.get("action", "") or "").strip().lower() != "post":
            return ""
        return await _build_qzone_post_with_optional_image(
            content=str(payload.get("content", "") or ""),
            image_prompt=str(payload.get("image_prompt", "") or ""),
            tool_caller=tool_caller,
            logger=logger,
            recent_posts=recent_posts,
        )
    if result.startswith("POST|"):
        text = _trim_qzone_content(result.split("|", 1)[1])
        if _is_too_similar_to_recent_qzone_post(text, recent_posts):
            logger.info(f"[qzone] skip POST raw post because it repeats recent content: {text}")
            return ""
        return text
    return ""
