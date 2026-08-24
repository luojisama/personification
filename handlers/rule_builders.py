from typing import Any, Awaitable, Callable, Dict

try:
    from nonebot.adapters import Event
    from nonebot.typing import T_State
except Exception:  # pragma: no cover - fallback for lightweight unit-test stubs
    Event = Any
    T_State = Dict[str, Any]


def build_personification_rule(
    *,
    personification_rule_core: Callable[..., Awaitable[bool]],
    sign_in_available: bool,
    get_user_data: Callable[[str], Dict[str, Any]],
    user_blacklist: Dict[str, float],
    logger: Any,
    group_event_cls: Any,
    private_event_cls: Any,
    is_group_whitelisted: Callable[[str, list[str]], bool],
    plugin_whitelist: list[str],
    load_prompt: Callable[[str], Any],
    load_proactive_state: Callable[[], Dict[str, Dict[str, Any]]],
    is_rest_time: Callable[..., bool],
    probability: float,
    group_chat_follow_probability: float,
    looks_like_private_command: Callable[[str], bool],
    get_recent_group_msgs: Callable[[str, int], list[dict]] | None = None,
    user_policy_gate: Any = None,
    favorability_service: Any = None,
    attention_service: Any = None,
) -> Callable[[Event, T_State], Awaitable[bool]]:
    async def _rule(event: Event, state: T_State) -> bool:
        legacy_should_reply = await personification_rule_core(
            event,
            state,
            sign_in_available=sign_in_available,
            get_user_data=get_user_data,
            user_blacklist=user_blacklist,
            logger=logger,
            group_event_cls=group_event_cls,
            private_event_cls=private_event_cls,
            is_group_whitelisted=is_group_whitelisted,
            plugin_whitelist=plugin_whitelist,
            load_prompt=load_prompt,
            load_proactive_state=load_proactive_state,
            is_rest_time=is_rest_time,
            probability=probability,
            group_chat_follow_probability=group_chat_follow_probability,
            looks_like_private_command=looks_like_private_command,
            get_recent_group_msgs=get_recent_group_msgs,
            user_policy_gate=user_policy_gate,
            favorability_service=favorability_service,
        )
        if attention_service is None or not bool(state.get("attention_admitted", False)):
            return legacy_should_reply
        is_group = isinstance(event, group_event_cls)
        target = str(state.get("message_target", "") or "").strip().lower()
        is_at_bot = bool(
            getattr(event, "to_me", False)
            or str(state.get("message_target_reason", "") or "")
            in {"explicit_persona_mention", "persona_name_mention"}
        )
        is_reply_to_bot = bool(
            getattr(event, "reply", None)
            and target == "bot"
        )
        is_continuation = bool(
            state.get("active_followup")
            or state.get("solo_speaker_follow")
            or state.get("group_idle_active")
        )
        group_id = str(getattr(event, "group_id", "") or "").strip()
        user_id = str(getattr(event, "user_id", "") or "").strip()
        bot_id = str(getattr(event, "self_id", "") or "").strip()
        session_key = f"{bot_id}:{group_id or f'private_{user_id}'}"
        recent_context: list[dict[str, Any]] = []
        if is_group and get_recent_group_msgs is not None:
            try:
                recent_context = list(get_recent_group_msgs(group_id, 8) or [])
            except Exception:
                recent_context = []
        evaluation = await attention_service.evaluate(
            session_key=session_key,
            user_text=str(event.get_plaintext() or ""),
            legacy_should_reply=bool(legacy_should_reply),
            is_private=not is_group,
            is_at_bot=is_at_bot,
            is_reply_to_bot=is_reply_to_bot,
            is_continuation=is_continuation,
            recent_context=recent_context,
        )
        state["attention_decision"] = evaluation.decision.to_dict()
        state["attention_metrics"] = evaluation.to_metrics()
        state["attention_wait_seconds"] = evaluation.decision.wait_seconds
        state["_attention_participation_service"] = attention_service
        if evaluation.mode.value == "on" and evaluation.actual_should_reply:
            state["is_random_chat"] = bool(
                is_group and not is_at_bot and not is_reply_to_bot and not is_continuation
            )
        return evaluation.actual_should_reply

    return _rule


def build_poke_rule(
    *,
    poke_rule_core: Callable[..., Awaitable[bool]],
    is_group_whitelisted: Callable[[str, list[str]], bool],
    plugin_whitelist: list[str],
    probability: float,
    user_policy_gate: Any = None,
) -> Callable[[Event], Awaitable[bool]]:
    async def _rule(event: Event) -> bool:
        return await poke_rule_core(
            event,
            is_group_whitelisted=is_group_whitelisted,
            plugin_whitelist=plugin_whitelist,
            probability=probability,
            user_policy_gate=user_policy_gate,
        )

    return _rule


def build_poke_notice_rule(
    *,
    poke_notice_rule_core: Callable[..., Awaitable[bool]],
    is_group_whitelisted: Callable[[str, list[str]], bool],
    plugin_whitelist: list[str],
    probability: float,
    logger: Any,
    user_policy_gate: Any = None,
) -> Callable[[Event], Awaitable[bool]]:
    async def _rule(event: Event) -> bool:
        return await poke_notice_rule_core(
            event,
            is_group_whitelisted=is_group_whitelisted,
            plugin_whitelist=plugin_whitelist,
            probability=probability,
            logger=logger,
            user_policy_gate=user_policy_gate,
        )

    return _rule
