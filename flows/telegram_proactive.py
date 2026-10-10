"""Telegram room transport for the existing idle-topic generation and review.

Only an observed, explicitly enabled group/topic can receive an autonomous
message. QQ registries, private friend discovery and QZone are never consulted.
"""
from __future__ import annotations

import asyncio
import json
import random
from typing import Any, Callable

from ..core.interaction_adapter import InteractionEnvelope
from ..core.llm_context import set_llm_context, reset_llm_context
from ..core.social_decision import SocialDecision, claim_social_decision, settle_social_decision
from ..core.telegram_actions import send_parts, split_text
from ..core.visible_output import guard_visible_text
from ..core import reply_turn_trace
from ..schedule import is_group_active_hour
from .proactive_flow import (
    GROUP_IDLE_TOPIC_PROMPT,
    _build_time_anchored_messages,
    _extract_system_prompt,
    _get_group_idle_time_flavor,
    _get_group_idle_today_count,
    _increment_group_idle_count,
    _is_group_truly_idle,
    _review_proactive_text,
    _strip_xml_message_content,
)


def _telegram(bot: Any) -> bool:
    return type(bot).__module__.startswith("nonebot.adapters.telegram") or getattr(bot, "platform", None) == "telegram"


def _history_version(rows: list[dict]) -> str:
    # An edited message also supersedes an unprompted draft.
    return json.dumps([(r.get("message_id"), r.get("content"), r.get("time"), r.get("timestamp")) for r in rows], ensure_ascii=False, sort_keys=True)


def build_telegram_idle_checker(
    *, plugin_config: Any, get_bots: Callable, load_prompt: Callable,
    call_ai_api: Callable, get_now: Callable, load_proactive_state: Callable,
    save_proactive_state: Callable, logger: Any, review_call_ai_api: Callable | None = None,
    connections: Any = None, get_recent_group_msgs: Callable | None = None,
    record_group_msg: Callable | None = None, append_session_message: Callable | None = None,
) -> Callable:
    """Same dependency names as FlowSetupDeps; no startup/network side effects."""
    if connections is None:
        from ..core.telegram_connections import get_connections
        connections = get_connections(plugin_config)
    if get_recent_group_msgs is None:
        from ..utils import get_recent_group_msgs
    if record_group_msg is None:
        from ..utils import record_group_msg
    if append_session_message is None:
        from ..core.session_store import append_session_message
    lock = asyncio.Lock()

    def enabled() -> bool:
        return bool(getattr(plugin_config, "personification_global_enabled", True)) and bool(getattr(plugin_config, "personification_proactive_enabled", True)) and bool(getattr(plugin_config, "personification_group_idle_enabled", False))

    async def check() -> int:
        if lock.locked() or not enabled():
            return 0
        if not is_group_active_hour(int(getattr(plugin_config, "personification_group_quiet_hour_start", 0)), int(getattr(plugin_config, "personification_group_quiet_hour_end", 7))):
            return 0
        count = 0
        async with lock:
            for bot in (get_bots() or {}).values():
                if not _telegram(bot):
                    continue
                bot_id = str(bot.self_id)
                settings = connections.settings_for(bot_id)
                if settings.get("scope") != "all":
                    continue
                for room in connections.rooms(bot_id):
                    if room.get("proactive_enabled") is not True:
                        continue
                    # TG groups/supergroups use negative IDs; do not turn a
                    # private chat into an opt-in autonomous friend workflow.
                    chat, topic = str(room.get("chat_id", "")), str(room.get("topic_id", ""))
                    try:
                        if int(chat) >= 0:
                            continue
                    except ValueError:
                        continue
                    canonical = connections.canonical_chat(bot_id, chat)
                    envelope = InteractionEnvelope("telegram", bot_id, "group", canonical, "", "", topic_id=topic)
                    scope = envelope.conversation_namespace
                    if room.get("scope_key") != scope:
                        continue
                    now = get_now()
                    now_ts, today = now.timestamp(), now.strftime("%Y-%m-%d")
                    idle_minutes = max(10, int(getattr(plugin_config, "personification_group_idle_minutes", 60)))
                    daily_limit = max(1, int(getattr(plugin_config, "personification_group_idle_daily_limit", 3)))
                    state = load_proactive_state() or {}
                    recent = get_recent_group_msgs(scope, limit=10, bot_id=envelope.bot_namespace, platform="telegram")
                    if not recent or not any(not row.get("is_bot") and row.get("source_kind") == "user" and row.get("message_id") for row in recent):
                        continue
                    idle, elapsed = _is_group_truly_idle(scope, get_recent_group_msgs=lambda _scope, limit: recent, idle_minutes=idle_minutes, now_ts=now_ts)
                    idle_state = state.get("group_idle_" + scope) or {}
                    last_attempt = float(idle_state.get("last_attempt_at", idle_state.get("last_sent_at", 0)) or 0)
                    if not idle or now_ts - last_attempt < idle_minutes * 60 or _get_group_idle_today_count(scope, state, today) >= daily_limit:
                        continue
                    probability = max(0.0, min(1.0, float(getattr(plugin_config, "personification_proactive_probability", 0.15))))
                    if random.random() >= probability:
                        continue
                    persona = _extract_system_prompt(settings.get("persona") or load_prompt(scope))
                    if not persona:
                        continue
                    context = "\n".join(f"{r.get('nickname', '')}: {r.get('content', '')}" for r in recent)[-6000:]
                    prompt = GROUP_IDLE_TOPIC_PROMPT.format(
                        system_prompt=persona, mood="未读取跨平台状态", energy="未读取跨平台状态",
                        group_emotion_memory="仅依据下方当前话题记录", datetime_info=now.isoformat(),
                        group_id=scope, group_name=str(room.get("title") or chat), group_style="自然群聊",
                        idle_minutes=elapsed, context_hint=context, time_flavor=_get_group_idle_time_flavor(now.hour),
                        relationship_policy="不编造未发生的共同经历、身体状态或群友反应", superuser_context="",
                    )
                    token = set_llm_context(platform="telegram", bot_id=envelope.bot_namespace, group_id=scope, purpose="proactive_group_idle")
                    trace_id = reply_turn_trace.start_trace(session_type="group", group_id=scope, detail={"platform": "telegram", "bot_id": bot_id, "trigger": "proactive_group_idle"})
                    trace_token = reply_turn_trace.set_current_trace_id(trace_id)
                    outcome = "no_reply"
                    try:
                        raw = await call_ai_api(_build_time_anchored_messages(system_prompt=persona, user_prompt=prompt, now=now))
                        candidate = _strip_xml_message_content(str(raw or ""))
                        if not candidate or candidate.startswith("["):
                            continue
                        candidate = await _review_proactive_text(caller=review_call_ai_api or call_ai_api, text=candidate, core_persona=persona, context=context, is_private=False)
                        candidate = guard_visible_text(candidate, logger=logger, surface="telegram_proactive", allow_direct_media=False)
                        if not candidate:
                            continue
                        # Recheck mutable authorization and context after model
                        # latency, before creating any irreversible send attempt.
                        current = next((r for r in connections.rooms(bot_id) if str(r.get("chat_id")) == chat and str(r.get("topic_id", "")) == topic), {})
                        latest = get_recent_group_msgs(scope, limit=10, bot_id=envelope.bot_namespace, platform="telegram")
                        if not enabled() or current.get("proactive_enabled") is not True or connections.settings_for(bot_id).get("scope") != "all" or _history_version(recent) != _history_version(latest) or bot not in (get_bots() or {}).values():
                            outcome = "superseded"
                            continue
                        decision = SocialDecision("join", target_id=scope, content=candidate, motivation="group_idle_topic", source_event_ids=tuple(str(r["message_id"]) for r in recent if not r.get("is_bot") and r.get("source_kind") == "user" and r.get("message_id")))
                        if not claim_social_decision(decision, channel="telegram_proactive", scope=envelope.bot_namespace, now=now_ts, target_cooldown_seconds=idle_minutes * 60):
                            outcome = "duplicate_or_unknown"
                            continue
                        # Reload shared state immediately before mutation; do
                        # not overwrite another room's update during LLM awaits.
                        state = load_proactive_state() or {}
                        state.setdefault("group_idle_" + scope, {})["last_attempt_at"] = now_ts
                        save_proactive_state(state)
                        outcome = "unknown"
                        operation = lambda: send_parts(bot, chat, candidate, topic_id=topic)
                        guarded = getattr(connections, "send_guarded", None)
                        result = await guarded(bot, operation) if callable(guarded) else await operation()
                        settlement = "unknown" if result.partial else result.state
                        settle_social_decision(decision, status=settlement, scope=envelope.bot_namespace)
                        outcome = settlement
                        if result.state == "sent":
                            count += 1
                            if callable(getattr(connections, "observed", None)):
                                connections.observed(bot_id, delivery="verified")
                        # A partial send is not a full success; only each actual
                        # confirmed segment becomes conversational history.
                        for mid, text in zip(result.message_ids, split_text(candidate)):
                            message_id = f"{scope}:m{len(mid)}:{mid}"
                            metadata = {"platform": "telegram", "bot_id": envelope.bot_namespace, "message_id": message_id, "source_kind": "bot_reply", "reply_delivery_status": "sent", "delivery_status": "sent", "user_id": envelope.bot_namespace}
                            record_group_msg(scope, str(getattr(bot, "username", "") or "bot"), text, is_bot=True, **metadata)
                            append_session_message("group_" + scope, "assistant", text, **metadata)
                        if result.message_ids:
                            state = load_proactive_state() or {}
                            _increment_group_idle_count(scope, state, today, now_ts)
                            save_proactive_state(state)
                    except Exception:
                        outcome = "sent_history_unavailable" if outcome == "sent" else "unknown" if outcome == "unknown" else "failed"
                        logger.warning("[telegram_proactive] 执行失败；未自动重发")
                    finally:
                        reply_turn_trace.finish_trace(trace_id=trace_id, outcome=outcome)
                        reply_turn_trace.reset_current_trace_id(trace_token)
                        reset_llm_context(token)
        return count

    return check


def register_telegram_proactive_job(*, scheduler: Any, **dependencies: Any) -> Callable:
    """Install alongside existing idle jobs; a stable ID prevents duplicates."""
    checker = build_telegram_idle_checker(**dependencies)
    config = dependencies["plugin_config"]
    scheduler.add_job(checker, "interval", minutes=max(5, int(getattr(config, "personification_group_idle_check_interval", 15))), id="personification_telegram_group_idle", replace_existing=True, max_instances=1, coalesce=True, misfire_grace_time=60)
    return checker
