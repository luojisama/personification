import random
import re
import time
from typing import Any, Callable, Optional, Tuple

from ..core.message_relations import (
    build_event_relation_metadata,
    extract_mentioned_ids,
    extract_reply_message_id,
)
from ..core.message_provenance import (
    is_bot_self_message_event,
    is_human_chat_record,
    is_personification_reply_record,
)
from ..core.group_roles import extract_sender_role
from ..core.group_mute import is_group_muted
from ..core.shared_content import parse_onebot_share_card
from ..core.command_runtime_context import has_runtime_command_prefix
from ..core.target_inference import (
    MessageTargetDecision,
    TARGET_BOT,
    TARGET_OTHERS,
    TARGET_UNCLEAR,
    classify_unprompted_followup,
    infer_message_target,
)

try:
    from nonebot.adapters.onebot.v11 import Event
    from nonebot.typing import T_State
except Exception:  # pragma: no cover - fallback for lightweight unit-test stubs
    Event = Any
    T_State = dict[str, Any]


def _detect_solo_speaker_follow(
    recent_msgs: list[dict[str, Any]],
    *,
    current_user_id: str,
    current_text: str,
    now_ts: float | None = None,
) -> dict[str, Any]:
    if not recent_msgs:
        return {}
    current_user = str(current_user_id or "").strip()
    if not current_user:
        return {}
    current_time = float(now_ts or time.time())
    recent_window = [
        msg
        for msg in list(recent_msgs)[-8:]
        if isinstance(msg, dict) and current_time - float(msg.get("time", 0) or 0) <= 6 * 60
    ]
    if not recent_window:
        return {}
    recent_non_bot = [msg for msg in recent_window if is_human_chat_record(msg)]
    if len(recent_non_bot) < 3:
        return {}
    tail = recent_non_bot[-4:]
    same_user_msgs = [msg for msg in tail if str(msg.get("user_id", "") or "").strip() == current_user]
    if len(same_user_msgs) < 3:
        return {}
    if str(tail[-1].get("user_id", "") or "").strip() != current_user:
        return {}
    if any(is_personification_reply_record(msg) for msg in recent_window[-6:]):
        return {}

    topic_seed = str(same_user_msgs[-1].get("content", "") or "").strip()
    return {
        "user_id": current_user,
        "count": len(same_user_msgs),
        "topic": topic_seed[:80],
    }


async def personification_rule(
    event: Event,
    state: T_State,
    *,
    sign_in_available: bool,
    get_user_data: Callable[[str], dict],
    user_blacklist: dict[str, float],
    logger: Any,
    group_event_cls: type,
    private_event_cls: type,
    is_group_whitelisted: Callable[[str, list[str]], bool],
    plugin_whitelist: list[str],
    load_prompt: Callable[[str], Any],
    load_proactive_state: Callable[[], dict[str, dict[str, Any]]],
    is_rest_time: Callable[..., bool],
    probability: float,
    group_chat_follow_probability: float,
    looks_like_private_command: Callable[[str], bool],
    get_recent_group_msgs: Optional[Callable[[str, int], list[dict]]] = None,
    user_policy_gate: Any = None,
    favorability_service: Any = None,
    followup_call_ai_api: Any = None,
    unprompted_followup_enabled: bool = True,
    unprompted_followup_window_seconds: float = 120.0,
    unprompted_followup_confidence: float = 0.78,
    unprompted_followup_timeout_seconds: float = 6.0,
) -> bool:
    # plugin_invoker 代为执行其它插件命令时会用 handle_event 重新分发合成事件，
    # 这里短路，避免合成事件再次进入拟人回复流程造成递归。
    if getattr(event, "_personification_synthetic", False):
        return False
    # OneBot may echo this account's outbound messages as ordinary message
    # events.  Stop before policy/model work; other plugins on the same QQ
    # account are still recorded as plugin provenance by resolve_record_message.
    if is_bot_self_message_event(event):
        return False
    user_id = str(event.user_id)

    if user_policy_gate is not None:
        decision = await user_policy_gate.evaluate(
            event,
            bot_self_id=str(getattr(event, "self_id", "") or ""),
        )
        state["user_policy_decision"] = decision.to_dict()
        if not decision.allow_normal_processing:
            if isinstance(event, private_event_cls) and looks_like_private_command(
                event.get_plaintext()
            ):
                return False
            if isinstance(event, group_event_cls):
                group_id = str(event.group_id)
                if not is_group_whitelisted(group_id, plugin_whitelist):
                    return False
                if is_group_muted(group_id):
                    return False
            if decision.disposition == "direct_closure_candidate":
                decision = await user_policy_gate.claim_direct_closure(decision)
                state["user_policy_decision"] = decision.to_dict()
            return decision.disposition == "direct_closure"

    if sign_in_available:
        user_data = get_user_data(user_id)
        if user_data.get("is_perm_blacklisted", False):
            return False

    if user_id in user_blacklist:
        if time.time() < user_blacklist[user_id]:
            return False
        del user_blacklist[user_id]
        logger.info(f"用户 {user_id} 的拉黑时间已到，已自动恢复。")

    if isinstance(event, group_event_cls):
        group_id = str(event.group_id)
        if not is_group_whitelisted(group_id, plugin_whitelist):
            return False
        if is_group_muted(group_id):
            logger.debug(f"拟人插件：群 {group_id} 处于 bot 禁言期，本轮不进入回复流程。")
            return False
        state["attention_admitted"] = True

        idle_active_state: dict[str, Any] = {}
        try:
            proactive_state = load_proactive_state() or {}
            raw_active_state = proactive_state.get(f"group_idle_active_{group_id}", {})
            if isinstance(raw_active_state, dict):
                until = float(raw_active_state.get("until", 0) or 0)
                if until > time.time():
                    idle_active_state = {
                        "until": until,
                        "topic": str(raw_active_state.get("topic", "") or "").strip()[:30],
                    }
        except Exception as e:
            logger.warning(f"拟人插件: 读取群空闲活跃窗口失败: {e}")
        if idle_active_state:
            state["group_idle_active"] = idle_active_state
        else:
            state.pop("group_idle_active", None)

        group_chat_active_state: dict[str, Any] = {}
        try:
            proactive_state = load_proactive_state() or {}
            raw_chat_state = proactive_state.get(f"group_chat_active_{group_id}", {})
            if isinstance(raw_chat_state, dict):
                until = float(raw_chat_state.get("until", 0) or 0)
                if until > time.time():
                    group_chat_active_state = {
                        "until": until,
                        "topic": str(raw_chat_state.get("topic", "") or "").strip()[:30],
                        "last_user_id": str(raw_chat_state.get("last_user_id", "") or "").strip(),
                    }
        except Exception as e:
            logger.warning(f"拟人插件: 读取群聊活跃窗口失败: {e}")
        if group_chat_active_state:
            state["active_followup"] = group_chat_active_state
        else:
            state.pop("active_followup", None)

        is_name_mentioned = False
        try:
            prompt_data = load_prompt(group_id)
            if isinstance(prompt_data, dict):
                names = []
                if prompt_data.get("name"):
                    names.append(str(prompt_data["name"]))
                if isinstance(prompt_data.get("nick_name"), list):
                    names.extend([str(n) for n in prompt_data["nick_name"] if n])
                msg_text = event.get_plaintext()
                for name in names:
                    if name in msg_text:
                        is_name_mentioned = True
                        break
        except Exception as e:
            logger.warning(f"拟人插件: 检查名字提及失败: {e}")

        _, explicitly_at_bot = extract_mentioned_ids(
            getattr(event, "message", []) or [],
            bot_self_id=str(getattr(event, "self_id", "") or ""),
        )
        adapter_direct_without_reply = bool(event.to_me) and not extract_reply_message_id(event)
        if explicitly_at_bot or adapter_direct_without_reply or is_name_mentioned:
            state["is_random_chat"] = False
            state["message_target"] = TARGET_BOT
            state["message_target_reason"] = (
                "explicit_persona_mention" if explicitly_at_bot or adapter_direct_without_reply else "persona_name_mention"
            )
            return True

        plain_text = str(event.get_plaintext() or "").strip()
        if _looks_like_plugin_command_interaction(plain_text):
            state["is_random_chat"] = False
            state["message_target"] = TARGET_OTHERS
            return False
        msg_len = len(plain_text)
        adaptive_enabled = bool(
            favorability_service is not None
            and getattr(
                getattr(favorability_service, "plugin_config", None),
                "personification_favorability_frequency_adaptive_enabled",
                False,
            )
        )

        def _random_probability_with_relation(base: float) -> float:
            resolved = max(0.0, min(1.0, float(base)))
            bias = 0.0
            band = ""
            score = None
            if adaptive_enabled:
                try:
                    profile = favorability_service.get_effective_profile(user_id, group_id)
                    policy = profile.get("effective", {}).get("behavior_policy", {})
                    bias = max(-0.20, min(0.20, float(policy.get("random_reply_add", 0.0) or 0.0)))
                    band = str(policy.get("band", "") or "")
                    score = policy.get("score")
                except Exception:
                    bias = 0.0
            effective = max(0.0, min(1.0, resolved + bias))
            state["favorability_frequency"] = {
                "base_probability": round(resolved, 4),
                "favorability_score": score,
                "behavior_band": band,
                "favorability_bias": round(bias, 4),
                "effective_probability": round(effective, 4),
                "gate_result": "pending",
            }
            return effective
        if get_recent_group_msgs is not None:
            try:
                recent_msgs = get_recent_group_msgs(group_id, 20)
            except Exception:
                recent_msgs = []
            target_decision = infer_message_target(
                event,
                bot_self_id=str(getattr(event, "self_id", "") or ""),
                recent_group_msgs=recent_msgs,
            )
            if isinstance(target_decision, MessageTargetDecision):
                state.update(target_decision.trace_fields())
                message_target = target_decision.target
            else:
                message_target = str(target_decision or "")
                state["message_target"] = message_target
        else:
            recent_msgs = []
            message_target = state.get("message_target", "")

        if (
            message_target == TARGET_UNCLEAR
            and bool(unprompted_followup_enabled)
            and get_recent_group_msgs is not None
        ):
            assessment = await classify_unprompted_followup(
                event,
                bot_self_id=str(getattr(event, "self_id", "") or ""),
                recent_group_msgs=recent_msgs,
                call_ai_api=followup_call_ai_api,
                window_seconds=unprompted_followup_window_seconds,
                confidence_threshold=unprompted_followup_confidence,
                timeout_seconds=unprompted_followup_timeout_seconds,
            )
            state["unprompted_followup"] = {
                "target": assessment.target,
                "confidence": assessment.confidence,
                "diagnostic_code": assessment.diagnostic_code,
            }
            if assessment.should_promote:
                target_decision = MessageTargetDecision(
                    TARGET_BOT,
                    reason="llm_unprompted_followup",
                    participants=(user_id, str(getattr(event, "self_id", "") or "")),
                    confidence=assessment.confidence,
                )
                state.update(target_decision.trace_fields())
                message_target = target_decision.target

        # Inferred Bot targeting is an explicit conversational cue, not an
        # optional random participation candidate.  It must bypass any signed
        # favorability probability reduction just like @/name mentions.
        if message_target == TARGET_BOT:
            state["is_random_chat"] = False
            return True

        solo_speaker_follow: dict[str, Any] = {}
        if get_recent_group_msgs is not None:
            solo_speaker_follow = _detect_solo_speaker_follow(
                recent_msgs=recent_msgs,
                current_user_id=user_id,
                current_text=plain_text,
            )
        if solo_speaker_follow:
            state["solo_speaker_follow"] = solo_speaker_follow
        else:
            state.pop("solo_speaker_follow", None)

        if group_chat_active_state:
            last_user_id = str(group_chat_active_state.get("last_user_id", "") or "").strip()
            same_user = bool(last_user_id) and last_user_id == user_id
            current_prob = float(max(0.0, min(1.0, group_chat_follow_probability)))

            recent_bot_participated = any(
                is_personification_reply_record(
                    msg,
                    str(getattr(event, "self_id", "") or ""),
                )
                for msg in recent_msgs[-4:]
            )
            if message_target == TARGET_OTHERS:
                current_prob = 0.0
            elif message_target == TARGET_BOT:
                current_prob = max(current_prob, 0.95)
            elif same_user and recent_bot_participated:
                current_prob = max(current_prob, 0.84)
            else:
                current_prob *= 0.35

            if random.random() < min(1.0, current_prob):
                state["is_random_chat"] = False
                state["active_followup"] = group_chat_active_state
                return True

        if message_target == TARGET_OTHERS and not solo_speaker_follow:
            state["is_random_chat"] = False
            return False

        if solo_speaker_follow:
            current_prob = min(1.0, max(probability * 1.65, 0.78))
            if msg_len <= 4:
                current_prob = max(current_prob, 0.70)
            elif msg_len >= 18:
                current_prob = max(current_prob, 0.84)
            effective_prob = _random_probability_with_relation(current_prob)
            if random.random() < effective_prob:
                state["is_random_chat"] = True
                state.setdefault("favorability_frequency", {})["gate_result"] = "pass"
                return True

        is_unsuitable_time = not is_rest_time(allow_unsuitable_prob=0.0)
        current_prob = probability * (0.55 if is_unsuitable_time else 1.0)
        if message_target == TARGET_BOT:
            current_prob = max(current_prob, min(1.0, max(probability * 1.8, 0.60)))
        if msg_len <= 1:
            current_prob *= 0.5
        elif msg_len <= 4:
            current_prob *= 0.8
        elif msg_len >= 24:
            current_prob *= 1.2
        if idle_active_state:
            current_prob = min(1.0, max(current_prob, probability * 0.9))
        effective_prob = _random_probability_with_relation(min(1.0, current_prob))
        if random.random() < effective_prob:
            state["is_random_chat"] = True
            state.setdefault("favorability_frequency", {})["gate_result"] = "pass"
            return True
        state.setdefault("favorability_frequency", {})["gate_result"] = "fail"
        return False

    if isinstance(event, private_event_cls):
        if looks_like_private_command(event.get_plaintext()):
            return False
        state["attention_admitted"] = True
        state["message_target"] = TARGET_BOT
        return True

    return False


async def record_msg_rule(_event: Event, *, user_policy_gate: Any = None) -> bool:
    if getattr(_event, "_personification_synthetic", False):
        return False
    if not str(getattr(_event, "group_id", "") or "").strip():
        return False
    if user_policy_gate is not None:
        decision = await user_policy_gate.evaluate(
            _event,
            bot_self_id=str(getattr(_event, "self_id", "") or ""),
        )
        return decision.allow_normal_processing
    return True


def _extract_share_card_token(seg_type: str, data: dict) -> str:
    """将 QQ 卡片的已验证元数据投影成短文本，不声称已经访问网页。"""

    shared = parse_onebot_share_card(
        {"type": seg_type, "data": dict(data or {})},
        segment_type=seg_type,
    )
    if not shared.available:
        return "[分享:内容不可用]"
    label = shared.title or shared.summary or shared.canonical_url
    label = re.sub(r"\s+", " ", str(label or "")).strip()[:80]
    platform = shared.platform if shared.platform != "unknown" else "分享"
    return f"[{platform}:{label}]" if label else f"[{platform}:仅元数据]"


def _extract_recordable_group_message(event: Any) -> tuple[str, int, str]:
    plain_text = str(event.get_plaintext() or "").strip()
    message = getattr(event, "message", None)
    if message is None:
        return plain_text, 0, ""

    text_parts: list[str] = []
    visual_parts: list[str] = []
    image_count = 0
    try:
        for seg in message:
            seg_type = str(getattr(seg, "type", "") or "").strip().lower()
            data = getattr(seg, "data", {}) or {}
            if seg_type == "text":
                text = str(data.get("text", "") or "")
                if text:
                    text_parts.append(text)
            elif seg_type == "face":
                from ..core.qq_expression_library import semantic_text_for_qq_expression_segment

                token = semantic_text_for_qq_expression_segment("face", data)
                text_parts.append(token)
                visual_parts.append(token)
            elif seg_type == "mface":
                from ..core.qq_expression_library import semantic_text_for_qq_expression_segment

                token = semantic_text_for_qq_expression_segment("mface", data, default_mface_kind="super")
                text_parts.append(token)
                visual_parts.append(token)
            elif seg_type == "image":
                image_count += 1
                token = "[图片]"
                text_parts.append(token)
                visual_parts.append(token)
            elif seg_type in ("json", "xml", "share"):
                token = _extract_share_card_token(seg_type, data)
                text_parts.append(token)
                visual_parts.append(token)
    except Exception:
        return plain_text, 0, ""

    content = "".join(text_parts).strip() or plain_text
    if not content and image_count > 0:
        content = "[发送了一张图片]" if image_count == 1 else f"[发送了{image_count}张图片]"
    visual_summary = " ".join(visual_parts[:6]).strip()
    return content, image_count, visual_summary


def _looks_like_plugin_command_interaction(text: str) -> bool:
    """Structural command marker used only to label/skip command interactions."""
    return has_runtime_command_prefix(text)


def _render_plugin_command_interaction(text: str) -> str:
    command = re.sub(r"\s+", " ", str(text or "")).strip()[:180]
    return f"[用户调用其它插件/命令] {command}" if command else "[用户调用其它插件/命令]"


def resolve_record_message(
    event: Any,
    *,
    get_custom_title: Callable[[str], Optional[str]],
    record_group_msg: Callable[..., int],
    should_trigger_auto_analyze: Optional[Callable[[str, int], bool]] = None,
) -> Tuple[Optional[str], bool]:
    """记录群消息，返回 (group_id, should_auto_analyze)。"""
    if bool(getattr(event, "_personification_synthetic", False)):
        return None, False
    if bool(getattr(event, "_personification_muted_recorded", False)):
        return None, False

    raw_msg, image_count, visual_summary = _extract_recordable_group_message(event)
    if not raw_msg or len(raw_msg) >= 500:
        return None, False
    is_command_interaction = _looks_like_plugin_command_interaction(raw_msg)
    record_content = _render_plugin_command_interaction(raw_msg) if is_command_interaction else raw_msg

    group_id = str(event.group_id)
    user_id = str(event.user_id)
    self_id = str(getattr(event, "self_id", "") or "").strip()
    sender = getattr(event, "sender", None)
    nickname = (
        getattr(sender, "card", None)
        or getattr(sender, "nickname", None)
        or user_id
    )

    custom_title = get_custom_title(user_id)
    if custom_title:
        nickname = custom_title

    peer_source_kind = str(
        getattr(event, "_personification_peer_bot_source_kind", "") or ""
    ).strip().lower()
    is_peer_bot_message = peer_source_kind in {"peer_bot_candidate", "peer_bot_reply"}
    is_bot_message = (bool(self_id) and user_id == self_id) or is_peer_bot_message
    source_kind = (
        peer_source_kind
        if is_peer_bot_message
        else ("plugin" if is_bot_message else ("plugin_command" if is_command_interaction else "user"))
    )
    relation_metadata = build_event_relation_metadata(
        event,
        bot_self_id=self_id,
        source_kind=source_kind,
    )
    count = record_group_msg(
        group_id,
        nickname,
        record_content,
        is_bot=is_bot_message,
        user_id=user_id,
        sender_role=extract_sender_role(event),
        image_count=image_count,
        visual_summary=visual_summary,
        **relation_metadata,
    )
    if source_kind != "user":
        return group_id, False
    if should_trigger_auto_analyze is None:
        return group_id, count >= 200
    return group_id, bool(should_trigger_auto_analyze(group_id, count))


async def sticker_chat_rule(
    event: Event,
    *,
    is_group_whitelisted: Callable[[str, list[str]], bool],
    plugin_whitelist: list[str],
    probability: float,
    user_policy_gate: Any = None,
) -> bool:
    if getattr(event, "_personification_synthetic", False):
        return False
    if user_policy_gate is not None:
        decision = await user_policy_gate.evaluate(
            event,
            bot_self_id=str(getattr(event, "self_id", "") or ""),
        )
        if not decision.allow_normal_processing:
            return False
    if event.to_me:
        return False
    group_id = str(event.group_id)
    if not is_group_whitelisted(group_id, plugin_whitelist):
        return False
    return random.random() < probability


async def poke_rule(
    event: Event,
    *,
    is_group_whitelisted: Callable[[str, list[str]], bool],
    plugin_whitelist: list[str],
    probability: float,
    user_policy_gate: Any = None,
) -> bool:
    if getattr(event, "_personification_synthetic", False):
        return False
    if user_policy_gate is not None and not await user_policy_gate.allows_current(event):
        return False
    target_id = getattr(event, "target_id", None)
    self_id = getattr(event, "self_id", None)
    group_id = getattr(event, "group_id", None)
    if target_id is None or self_id is None or group_id is None:
        return False
    if target_id != self_id:
        return False
    group_id = str(group_id)
    if not is_group_whitelisted(group_id, plugin_whitelist):
        return False
    if is_group_muted(group_id):
        return False
    return random.random() < probability


async def poke_notice_rule(
    event: Event,
    *,
    is_group_whitelisted: Callable[[str, list[str]], bool],
    plugin_whitelist: list[str],
    probability: float,
    logger: Any,
    user_policy_gate: Any = None,
) -> bool:
    notice_type = str(getattr(event, "notice_type", "") or "").strip().lower()
    sub_type = str(getattr(event, "sub_type", "") or "").strip().lower()
    if notice_type != "notify" or sub_type != "poke":
        return False
    if user_policy_gate is not None and not await user_policy_gate.allows_current(event):
        return False

    target_id = getattr(event, "target_id", None)
    self_id = getattr(event, "self_id", None)
    group_id = getattr(event, "group_id", None)
    if target_id is None or self_id is None or group_id is None:
        logger.debug("收到 notify 事件，但缺少 poke 所需字段，已忽略。")
        return False

    logger.info(f"收到戳一戳事件: target_id={target_id}, self_id={self_id}")
    if target_id != self_id:
        return False
    group_id = str(group_id)
    if not is_group_whitelisted(group_id, plugin_whitelist):
        logger.info(f"群 {group_id} 不在白名单 {plugin_whitelist} 或动态白名单中")
        return False
    if is_group_muted(group_id):
        logger.debug(f"拟人插件：群 {group_id} 处于 bot 禁言期，忽略戳一戳响应。")
        return False
    res = random.random() < probability
    logger.info(f"戳一戳响应判定: 概率={probability}, 结果={res}")
    return res


def split_text_into_segments(text: str) -> list[str]:
    # 只按 LLM 显式输出的段落分隔（连续 2+ 换行/空行）切分。
    # 单个句号、问号、感叹号不再触发切分，避免一句完整回复被发成多条消息。
    # 单换行（软分行）也不切，由下游 split_segment_if_long 按字数处理。
    parts = re.split(r"\n\s*\n+", text)
    segments = [p.strip() for p in parts if p.strip()]
    return segments


_PAIR_OPEN_CLOSE: dict[str, str] = {
    "《": "》",
    "“": "”",
    "‘": "’",
    "「": "」",
    "『": "』",
    "（": "）",
    "(": ")",
    "【": "】",
    "[": "]",
    "{": "}",
    "〈": "〉",
}
_OPEN_CHARS = set(_PAIR_OPEN_CLOSE.keys())
_CLOSE_CHARS = set(_PAIR_OPEN_CLOSE.values())
_CLOSE_TO_OPEN = {v: k for k, v in _PAIR_OPEN_CLOSE.items()}


def _split_with_pair_protection(text: str, delimiter_regex: str) -> list[str]:
    """在保护成对标点（如《书名！》、“引号！？”）不被切开的前提下按标点切分并保留标点。"""
    if not text:
        return []
    delims_pattern = re.compile(delimiter_regex)
    clauses: list[str] = []
    stack: list[str] = []
    current_clause: list[str] = []
    i = 0
    chars = list(text)
    n = len(chars)

    while i < n:
        ch = chars[i]
        if ch in _OPEN_CHARS:
            stack.append(ch)
            current_clause.append(ch)
            i += 1
            continue
        elif ch in _CLOSE_CHARS:
            if stack and stack[-1] == _CLOSE_TO_OPEN.get(ch):
                stack.pop()
            current_clause.append(ch)
            i += 1
            continue

        if not stack:
            match = delims_pattern.match(text, i)
            if match:
                delim_str = match.group(0)
                current_clause.append(delim_str)
                clause_text = "".join(current_clause).strip()
                if clause_text:
                    clauses.append(clause_text)
                current_clause = []
                i += len(delim_str)
                continue

        current_clause.append(ch)
        i += 1

    if current_clause:
        clause_text = "".join(current_clause).strip()
        if clause_text:
            clauses.append(clause_text)
    return clauses


def split_segment_if_long(segment: str, max_chars: int) -> list[str]:
    text = str(segment or "").strip()
    if max_chars <= 0 or len(text) <= max_chars:
        return [text] if text else []

    # 1. 优先按主要断句标点（句号、感叹号、问号、省略号、换行）切分子句并保留成对符号完整性
    major_delims = r"[。！？!?…~～\n]+"
    clauses = _split_with_pair_protection(text, major_delims)

    # 2. 对仍然超过 max_chars 的较长子句，按次级标点（逗号、顿号、分号）进一步切分
    fine_clauses: list[str] = []
    minor_delims = r"[，、；,;]+"
    for clause in clauses:
        if len(clause) <= max_chars:
            fine_clauses.append(clause)
        else:
            sub_clauses = _split_with_pair_protection(clause, minor_delims)
            fine_clauses.extend(sub_clauses or [clause])

    # 3. 贪心合并短子句，组装成不超过 max_chars 的自然消息
    result: list[str] = []
    buffer = ""

    for item in fine_clauses:
        if not item:
            continue
        if not buffer:
            buffer = item
        elif len(buffer) + len(item) <= max_chars:
            buffer += item
        else:
            result.append(buffer)
            buffer = item

    if buffer:
        result.append(buffer)

    # 4. 保底：对极少数无任何标点的超长纯文本块做拆分
    final: list[str] = []
    for chunk in result:
        while len(chunk) > max_chars:
            final.append(chunk[:max_chars])
            chunk = chunk[max_chars:]
        if chunk:
            final.append(chunk)

    return [s.strip() for s in final if s.strip()] or ([text] if text else [])
