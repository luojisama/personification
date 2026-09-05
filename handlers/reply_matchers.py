import asyncio
import time
from typing import Any, Callable, Dict

from nonebot import on_message, on_notice
from nonebot.rule import Rule

from ..core.group_mute import update_group_mute_from_notice
from ..core.message_provenance import is_bot_self_message_event
from ..core.reply_buffer_timing import resolve_reply_buffer_timing
from ..core.runtime_performance import register_reply_reporter
from .reply_buffer import ReplyConcurrencyController, buffer_runtime_snapshot

try:
    from nonebot.typing import T_State
    from nonebot.adapters.onebot.v11 import Bot, Event
except Exception:  # pragma: no cover - fallback for lightweight unit-test stubs
    Bot = Any
    Event = Any
    T_State = Dict[str, Any]


_RULE_EVAL_CACHE: Dict[str, dict[str, Any]] = {}
_RULE_EVAL_CACHE_TTL_SECONDS = 30.0
_RULE_EVAL_CACHE_MAX_SIZE = 128


def _build_rule_cache_key(event: Event) -> str:
    message_id = str(getattr(event, "message_id", "") or "").strip()
    if message_id:
        bot_self_id = str(getattr(event, "self_id", "") or "").strip()
        group_id = str(getattr(event, "group_id", "") or "").strip()
        user_id = str(getattr(event, "user_id", "") or "").strip()
        return f"{event.__class__.__name__}:{bot_self_id}:{group_id}:{user_id}:{message_id}"

    user_id = str(getattr(event, "user_id", "") or "").strip()
    group_id = str(getattr(event, "group_id", "") or "").strip()
    notice_type = str(getattr(event, "notice_type", "") or "").strip()
    return f"{event.__class__.__name__}:{group_id}:{user_id}:{notice_type}:{id(event)}"


def _prune_rule_eval_cache(now_ts: float) -> None:
    expired_keys = [
        key
        for key, cached in _RULE_EVAL_CACHE.items()
        if now_ts - float(cached.get("saved_at", 0) or 0) > _RULE_EVAL_CACHE_TTL_SECONDS
    ]
    for key in expired_keys:
        _RULE_EVAL_CACHE.pop(key, None)

    if len(_RULE_EVAL_CACHE) <= _RULE_EVAL_CACHE_MAX_SIZE:
        return

    overflow = sorted(
        _RULE_EVAL_CACHE.items(),
        key=lambda item: float(item[1].get("saved_at", 0) or 0),
    )[: len(_RULE_EVAL_CACHE) - _RULE_EVAL_CACHE_MAX_SIZE]
    for key, _ in overflow:
        _RULE_EVAL_CACHE.pop(key, None)


async def _evaluate_personification_rule(
    *,
    personification_rule: Callable[[Event, T_State], Any],
    event: Event,
    state: T_State,
    peer_bot_coordinator: Any = None,
) -> dict[str, Any]:
    # plugin_invoker 用 handle_event 重新分发的合成事件与原事件共享 message_id，
    # 会命中下方的规则结果缓存而绕过 personification_rule 顶部的合成事件短路，
    # 因此这里必须在查缓存之前先短路，避免合成事件再次进入回复流程造成递归。
    if getattr(event, "_personification_synthetic", False):
        state["is_random_chat"] = False
        return {"matched": False, "is_random_chat": False}
    if is_bot_self_message_event(event):
        state["is_random_chat"] = False
        return {"matched": False, "is_random_chat": False}
    if peer_bot_coordinator is not None:
        try:
            peer_bot_coordinator.classify_event(event)
        except Exception:
            pass
    cache_key = _build_rule_cache_key(event)
    now_ts = time.time()
    cached = _RULE_EVAL_CACHE.get(cache_key)
    if isinstance(cached, dict):
        saved_at = float(cached.get("saved_at", 0) or 0)
        if now_ts - saved_at <= _RULE_EVAL_CACHE_TTL_SECONDS:
            cached_state = cached.get("state")
            if isinstance(cached_state, dict):
                state.update(cached_state)
            return {
                "matched": bool(cached.get("matched")),
                "is_random_chat": bool(cached.get("is_random_chat")),
            }

    matched = bool(await personification_rule(event, state))
    result = {
        "matched": matched,
        "is_random_chat": bool(state.get("is_random_chat", False)),
    }
    _RULE_EVAL_CACHE[cache_key] = {
        "saved_at": now_ts,
        "matched": result["matched"],
        "is_random_chat": result["is_random_chat"],
        "state": dict(state),
    }
    _prune_rule_eval_cache(now_ts)
    return result


def register_reply_matchers(
    *,
    personification_rule: Callable[[Event, T_State], Any],
    poke_notice_rule: Callable[[Event], Any],
    handle_reply_event: Callable[..., Any],
    process_response_logic: Callable[[Bot, Event, T_State], Any],
    msg_buffer: Dict[str, Dict[str, Any]],
    run_buffer_timer: Callable[..., Any],
    poke_event_cls: Any,
    message_event_cls: Any,
    group_message_event_cls: Any,
    message_cls: Any,
    message_segment_cls: Any,
    logger: Any,
    plugin_config: Any,
    finished_exception_cls: Any = None,
    user_policy_gate: Any = None,
    peer_bot_coordinator: Any = None,
) -> Dict[str, Any]:
    response_timeout_seconds = min(
        600.0,
        max(
            30.0,
            float(getattr(plugin_config, "personification_response_timeout", 180) or 180),
        ),
    )
    batch_timing = resolve_reply_buffer_timing(plugin_config)
    concurrency_controller = ReplyConcurrencyController(
        session_limit=int(getattr(plugin_config, "personification_reply_session_concurrency", 3) or 3),
        global_limit=int(getattr(plugin_config, "personification_reply_global_concurrency", 12) or 12),
    )
    def _reply_runtime_reporter() -> dict[str, int]:
        value = concurrency_controller.snapshot()
        value.update(buffer_runtime_snapshot(msg_buffer))
        value["admission_waiting_turns"] = value["waiting"]
        return value
    register_reply_reporter(_reply_runtime_reporter)

    async def _direct_reply_rule(event: Event, state: T_State) -> bool:
        result = await _evaluate_personification_rule(
            personification_rule=personification_rule,
            event=event,
            state=state,
            peer_bot_coordinator=peer_bot_coordinator,
        )
        return bool(result.get("matched")) and not bool(result.get("is_random_chat"))

    async def _random_reply_rule(event: Event, state: T_State) -> bool:
        result = await _evaluate_personification_rule(
            personification_rule=personification_rule,
            event=event,
            state=state,
            peer_bot_coordinator=peer_bot_coordinator,
        )
        return bool(result.get("matched")) and bool(result.get("is_random_chat"))

    direct_reply_matcher = on_message(rule=Rule(_direct_reply_rule), priority=100, block=True)
    random_reply_matcher = on_message(rule=Rule(_random_reply_rule), priority=100, block=False)
    poke_notice_matcher = on_notice(rule=Rule(poke_notice_rule), priority=10, block=False)

    async def _group_mute_notice_rule(event: Event) -> bool:
        return str(getattr(event, "notice_type", "") or "").strip() == "group_ban"

    group_mute_notice_matcher = on_notice(rule=Rule(_group_mute_notice_rule), priority=9, block=False)

    @group_mute_notice_matcher.handle()
    async def _handle_group_mute_notice(bot: Bot, event: Event):
        update_group_mute_from_notice(
            event,
            bot_self_id=str(getattr(bot, "self_id", "") or ""),
            logger=logger,
        )

    async def _buffer_timer(key: str, bot: Bot, wait_seconds: float):
        await run_buffer_timer(
            key,
            bot,
            msg_buffer=msg_buffer,
            process_response_logic=process_response_logic,
            message_event_cls=message_event_cls,
            message_cls=message_cls,
            message_segment_cls=message_segment_cls,
            logger=logger,
            finished_exception_cls=finished_exception_cls,
            delay=wait_seconds,
            response_timeout_seconds=response_timeout_seconds,
            batch_base_wait_seconds=batch_timing.base_wait_seconds,
            batch_min_wait_seconds=batch_timing.min_wait_seconds,
            batch_max_wait_seconds=batch_timing.max_wait_seconds,
            legacy_reply_backoff_seconds=batch_timing.legacy_reply_backoff_seconds,
            concurrency_controller=concurrency_controller,
            user_policy_gate=user_policy_gate,
            timing_resolver=lambda: resolve_reply_buffer_timing(plugin_config),
        )

    @direct_reply_matcher.handle()
    @random_reply_matcher.handle()
    @poke_notice_matcher.handle()
    async def _handle_reply(bot: Bot, event: Event, state: T_State):
        await handle_reply_event(
            bot,
            event,
            state,
            poke_event_cls=poke_event_cls,
            message_event_cls=message_event_cls,
            group_message_event_cls=group_message_event_cls,
            process_response_logic=process_response_logic,
            msg_buffer=msg_buffer,
            start_buffer_timer=lambda key, _bot, wait_seconds: asyncio.create_task(
                _buffer_timer(key, _bot, wait_seconds)
            ),
            logger=logger,
            concurrency_controller=concurrency_controller,
            response_timeout_seconds=response_timeout_seconds,
            batch_base_wait_seconds=batch_timing.base_wait_seconds,
            batch_min_wait_seconds=batch_timing.min_wait_seconds,
            batch_max_wait_seconds=batch_timing.max_wait_seconds,
            legacy_reply_backoff_seconds=batch_timing.legacy_reply_backoff_seconds,
            finished_exception_cls=finished_exception_cls,
            user_policy_gate=user_policy_gate,
            timing_resolver=lambda: resolve_reply_buffer_timing(plugin_config),
        )

    return {
        "reply_matcher": direct_reply_matcher,
        "random_reply_matcher": random_reply_matcher,
        "poke_notice_matcher": poke_notice_matcher,
        "group_mute_notice_matcher": group_mute_notice_matcher,
        "handle_reply": _handle_reply,
    }
