"""Isolated full-pipeline quality-evaluation adapter.

This is development-only glue: it deliberately calls the public normal reply
processor and lets a dict persona traverse its normal-to-YAML handoff.  It
does not load environment configuration, start the plugin, or contact OneBot.
"""
from __future__ import annotations

import asyncio
import importlib
import json
import sys
import types
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any


def _bootstrap() -> None:
    root = Path(__file__).resolve().parents[2]
    paths = {"plugin": root.parent, "plugin.personification": root}
    for folder in ("core", "agent", "agent/runtime", "handlers", "handlers/reply_pipeline", "handlers/yaml_pipeline", "skills", "skills/skillpacks", "skills/skillpacks/tool_caller", "skills/skillpacks/tool_caller/scripts"):
        paths[f"plugin.personification.{folder.replace('/', '.')}"] = root / folder
    for name, path in paths.items():
        module = sys.modules.get(name)
        if module is None:
            module = types.ModuleType(name)
            module.__path__ = [str(path)]
            sys.modules[name] = module


class _Log:
    def __init__(self) -> None:
        self.failed = False
    def debug(self, *_a: Any, **_k: Any) -> None: pass
    def info(self, *_a: Any, **_k: Any) -> None: pass
    def warning(self, *_a: Any, **_k: Any) -> None: pass
    def error(self, *_a: Any, **_k: Any) -> None:
        self.failed = True


class _Segment:
    def __init__(self, kind: str, data: dict[str, Any]) -> None:
        self.type, self.data = kind, data

    @staticmethod
    def image(value: str) -> str:
        return value

    @staticmethod
    def poke(value: int) -> tuple[str, int]:
        return ("poke", value)


class _MessageEvent:
    def __init__(self, *, group_id: str, user_id: str, message_id: str, sender: str, text: str, mention: bool, bot_id: str, platform: str) -> None:
        self.group_id, self.user_id, self.message_id = group_id, user_id, message_id
        # The real recall path scopes records by the incoming event identity,
        # not the capture bot alone.  Fixtures must model both fields so a
        # seed cannot silently miss (or widen) its platform/bot boundary.
        self.self_id, self.platform = bot_id, platform
        self.sender = SimpleNamespace(nickname=sender, card="", role="member")
        self.message = ([ _Segment("at", {"qq": bot_id}) ] if mention else []) + [_Segment("text", {"text": text})]
        self.reply = None
        self.reply_to_message_id = ""

    def get_plaintext(self) -> str:
        return "".join(str(item.data.get("text", "")) for item in self.message if item.type == "text")


class _GroupEvent(_MessageEvent):
    pass


class _PrivateEvent(_MessageEvent):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(group_id="", **kwargs)


class _CaptureBot:
    def __init__(self, receipt: str) -> None:
        self.self_id = "quality-eval-bot"
        self.receipt = receipt
        self.sent: list[Any] = []

    async def send(self, _event: Any, payload: Any) -> Any:
        self.sent.append(payload)
        if self.receipt == "failed":
            raise RuntimeError("quality_eval_synthetic_send_failed")
        if self.receipt == "unknown":
            return None
        return {"message_id": f"quality-{len(self.sent)}"}


def _event_from(case: dict[str, Any], current: dict[str, Any], bot_id: str, index: int) -> Any:
    if str(current.get("kind", "message")) not in {"message", "mention"}:
        raise ValueError("unsupported_fixture:trigger_kind")
    common = dict(user_id=str(current.get("user_id", current.get("sender", "user"))), message_id=str(current.get("message_id", f"eval-{case.get('id', 'case')}-{index}")), sender=str(current.get("sender", "user")), text=str(current.get("text", "")), mention=str(current.get("kind")) == "mention", bot_id=bot_id, platform="onebot")
    if str(case.get("surface", "group")) == "private":
        return _PrivateEvent(**common)
    return _GroupEvent(group_id=str(current.get("group_id", "quality-group")), **common)


async def _caller_text(caller: Any, messages: list[dict[str, Any]]) -> str:
    response = await caller.chat_with_tools(messages, [], False)
    return str(getattr(response, "content", "") or "")


async def run_full_path_case(case: dict[str, Any], *, caller: Any, isolated_dir: str, behavior_config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Run one capture-only normal turn, optionally through real YAML routing.

    ``caller`` is injected by the owner and is expected to already enforce the
    global evaluation budget.  Unsupported fixture forms fail explicitly rather
    than silently falling back to an Agent-only fragment.
    """
    _bootstrap()
    if not hasattr(caller, "chat_with_tools"):
        raise TypeError("caller must provide chat_with_tools")
    events = [dict(item) for item in list(case.get("events") or []) if isinstance(item, dict)]
    if not events:
        raise ValueError("unsupported_fixture:no_events")
    seed = case.get("seed") if isinstance(case.get("seed"), dict) else {}
    if seed.get("memory") or seed.get("seed_memory") or case.get("coverage_requires"):
        raise ValueError("blocked_fixture:memory_or_coverage_requires")
    if any(str(item.get("kind", "")) in {"tool_result", "send_receipt", "memory_update"} for item in events):
        raise ValueError("unsupported_fixture:external_or_state_event")
    if case.get("tools") or case.get("media"):
        raise ValueError("unsupported_fixture:tools_or_media")
    # ``pipeline`` is the versioned corpus field.  ``mode`` remains only for
    # older fixtures during migration.
    mode = str(case.get("pipeline", case.get("mode", "normal")) or "normal")
    if mode not in {"normal", "yaml"}:
        raise ValueError("unsupported_fixture:mode")
    root = Path(isolated_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    processor = importlib.import_module("plugin.personification.handlers.reply_pipeline.processor")
    yaml_mod = importlib.import_module("plugin.personification.handlers.yaml_pipeline.processor")
    config_mod = importlib.import_module("plugin.personification.config")
    registry_mod = importlib.import_module("plugin.personification.agent.tool_registry")
    yaml_parse_mod = importlib.import_module("plugin.personification.flows.yaml_parser")

    persona_value = case.get("persona") or case.get("trusted_persona")
    if isinstance(persona_value, dict):
        prompt = dict(persona_value)
    elif mode == "yaml":
        # This is only the adapter's fallback template.  Production YAML
        # deliberately flattens rendered history into its input field, and a
        # template containing any history placeholder opts out of automatic
        # history insertion.  Include both retained slices so multi-turn
        # fixtures do not accidentally hide confirmed replies.  User-supplied
        # persona dicts remain untouched.
        prompt = {"system": str(persona_value or ""), "input": "{history_new}\n{history_last}"}
    else:
        prompt = str(persona_value or "")
    if not prompt:
        raise ValueError("unsupported_fixture:missing_persona")
    # The injected BudgetedCaller counts chat_with_tools only.  Keep discovery
    # and provider-native search off so no delegated/provider path can bypass
    # that single accounting boundary during an evaluation run.
    values = dict(behavior_config or {})
    values.update(personification_data_dir=str(root), personification_qq_expression_enabled=False, personification_tts_enabled=False, personification_schedule_global=False, personification_tool_disclosure_mode="off", personification_model_builtin_search_enabled=False, personification_builtin_search=False)
    config = config_mod.Config(**values)
    session_data: dict[str, list[dict[str, Any]]] = {}
    confirmed_history: list[dict[str, Any]] = []
    group_window = list((case.get("seed") or {}).get("group_window", [])) if isinstance(case.get("seed"), dict) else []
    # Production records confirmed bot replies through ``record_group_msg``.
    # Keep those records pending until the current human event is appended
    # below, so the next turn sees chronological user -> bot ordering without
    # exposing an unconfirmed outgoing reply to the same turn.
    captured_group_replies: list[dict[str, Any]] = []
    receipt = str(case.get("synthetic_receipt", "confirmed") or "confirmed")
    if receipt not in {"confirmed", "unknown", "failed"}:
        raise ValueError("unsupported_fixture:receipt")
    bot = _CaptureBot(receipt)
    logger = _Log()
    data_store = importlib.import_module("plugin.personification.core.data_store")
    data_store.init_data_store(config, logger=logger)
    memory_store = None
    if case.get("seed_memory"):
        from scripts.quality_eval.memory_fixtures import build_memory_store
        memory_store = build_memory_store(case, {"isolated_data_dir": str(root)}, logger)
        if memory_store.quality_fixture_unsupported:
            raise ValueError("blocked_fixture:memory_seed_incomplete")

    def append(session_id: str, role: str, content: str, **meta: Any) -> None:
        record = {"role": role, "content": content, **meta}
        session_data.setdefault(session_id, []).append(record)
        if role == "assistant":
            confirmed_history.append(record)

    def record_group_msg(
        group_id: str,
        sender_name: str,
        content: str,
        **meta: Any,
    ) -> None:
        captured_group_replies.append({
            "group_id": str(group_id or ""),
            "message_id": str(meta.get("message_id", "") or ""),
            "user_id": str(meta.get("user_id", "") or ""),
            "sender_name": str(sender_name or ""),
            "text": str(content or ""),
            "source_kind": str(meta.get("source_kind", "bot_reply") or "bot_reply"),
            "is_bot": bool(meta.get("is_bot", True)),
            "reply_to_msg_id": str(meta.get("reply_to_msg_id", "") or ""),
            "reply_to_user_id": str(meta.get("reply_to_user_id", "") or ""),
            "mentioned_ids": list(meta.get("mentioned_ids", []) or []),
        })

    async def primary(messages: list[dict[str, Any]], **_kwargs: Any) -> str:
        return await _caller_text(caller, messages)

    yaml_processor = yaml_mod.build_yaml_response_processor(
        get_current_time=lambda: datetime(2026, 1, 1, 12, 0, 0), format_time_context=lambda _now: "2026-01-01 12:00",
        bot_statuses={}, get_group_config=lambda _gid: {"schedule_enabled": False}, plugin_config=config,
        get_schedule_prompt_injection=lambda: "", schedule_disabled_override_prompt=lambda: "", build_grounding_context=lambda _q: "",
        call_ai_api=primary, lite_call_ai_api=primary, review_call_ai_api=primary, parse_yaml_response=yaml_parse_mod.parse_yaml_response,
        message_segment_cls=_Segment, sanitize_history_text=str, private_session_prefix="private_",
        build_private_session_id=lambda uid: f"private_{uid}", build_group_session_id=str, append_session_message=append,
        record_group_msg=record_group_msg, logger=logger, user_blacklist={}, tool_registry=registry_mod.ToolRegistry(),
        agent_tool_caller=caller, lite_tool_caller=caller, vision_caller=None, tts_service=None,
        memory_curator=None, knowledge_store=None, inner_state_updater=None, favorability_service=None,
        user_policy_gate=None, qq_outbound_ledger=None,
    )
    session = processor.SessionDeps(private_session_prefix="private_", looks_like_private_command=lambda _t: False,
        ensure_session_history=lambda *_a, **_k: None, build_private_session_id=lambda uid: f"private_{uid}", build_group_session_id=str,
        sanitize_session_messages=lambda values: values, get_session_messages=lambda sid: list(session_data.get(sid, [])),
        append_session_message=append, sanitize_history_text=str, build_private_anti_loop_hint=lambda _m: "")
    persona = processor.PersonaDeps(load_prompt=lambda _gid: prompt, sign_in_available=False, get_user_data=lambda _uid: {},
        get_level_name=lambda _score: "friend", update_user_data=lambda *_a, **_k: None, get_group_config=lambda _gid: {},
        get_group_style=lambda _gid: "", favorability_attitudes={}, get_custom_title=lambda _uid: "", default_bot_nickname="quality")
    runtime = processor.RuntimeDeps(is_msg_processed=lambda _mid: False, logger=logger, superusers=set(),
        get_configured_api_providers=lambda: [{"name": "quality-injected"}], should_avoid_interrupting=lambda *_a: False,
        module_instance_id=1, process_yaml_response_logic=yaml_processor, plugin_config=config,
        get_current_time=lambda: datetime(2026, 1, 1, 12, 0, 0), format_time_context=lambda _now: "2026-01-01 12:00",
        schedule_disabled_override_prompt=lambda: "", get_schedule_prompt_injection=lambda: "", build_grounding_context=lambda _q: "",
        update_private_interaction_time=lambda _uid: None, call_ai_api=primary, lite_call_ai_api=primary, review_call_ai_api=primary,
        save_plugin_runtime_config=None, user_blacklist={}, record_group_msg=record_group_msg,
        split_text_into_segments=lambda text: [text], message_segment_cls=_Segment, get_sticker_files=lambda: [],
        get_http_client=lambda: _NoNetworkClient(), get_whitelisted_groups=lambda: [], agent_tool_caller=caller,
        lite_tool_caller=caller, tool_registry=registry_mod.ToolRegistry(), memory_store=memory_store)
    type_deps = processor.TypeDeps(poke_event_cls=type("Poke", (), {}), message_event_cls=_MessageEvent,
        group_message_event_cls=_GroupEvent, private_message_event_cls=_PrivateEvent, message_cls=list)
    state: dict[str, Any] = {}
    original_yaml_topic, original_yaml_recent = yaml_mod.get_group_topic_summary, yaml_mod.get_recent_group_msgs
    original_qzone_register = yaml_mod.register_groupmate_qzone_agent_tools
    original_sticker_feedback = yaml_mod.load_sticker_feedback
    tool_registrars = ("register_qq_recall_tool", "register_current_user_avatar_tool", "register_peer_bot_tools", "register_current_group_context_tool", "register_send_qq_expression_tools", "register_group_user_avatar_pair_insight_tool", "register_group_member_avatar_insight_tool", "register_moderation_for_turn")
    original_registrars = {name: getattr(yaml_mod, name) for name in tool_registrars}
    yaml_mod.get_group_topic_summary = lambda *_a, **_k: ""
    yaml_mod.get_recent_group_msgs = lambda *_a, **_k: list(group_window)
    # QZone registration reads its own persistent settings and may expose write
    # tools.  It is deliberately unavailable in a capture-only quality turn.
    yaml_mod.register_groupmate_qzone_agent_tools = lambda *_a, **_k: None
    # Do not change tool-selection semantics with a fake business result: block
    # registration of every external/actionable tool in this fixture profile.
    # The empty base registry therefore makes any attempted external tool call
    # fail closed rather than reaching QQ, profile, peer, or network services.
    for name in tool_registrars:
        setattr(yaml_mod, name, lambda *_a, **_k: None)
    async def _empty_sticker_feedback() -> dict[str, Any]:
        return {}
    yaml_mod.load_sticker_feedback = _empty_sticker_feedback
    if str(case.get("surface", "group")) != "private":
        # The production context builder consumes this injected *data source*, not
        # a pre-rendered context hint.  It is reset immediately after the turn.
        original_window, original_recent = processor.build_group_context_window, processor.get_recent_group_msgs
        processor.build_group_context_window = lambda *_a, **_k: list(group_window)
        processor.get_recent_group_msgs = lambda *_a, **_k: list(group_window)
    else:
        original_window = original_recent = None
    try:
        turns: list[dict[str, Any]] = []
        for index, event_data in enumerate(events):
            event = _event_from(case, event_data, bot.self_id, index)
            captured_group_replies.clear()
            before = len(bot.sent)
            state = {"response_deadline": asyncio.get_running_loop().time() + float(config.personification_response_timeout), "disable_network_hooks": True, "is_random_chat": bool(case.get("is_random_chat", False)), "batched_events": [], "turn_media_context": []}
            await processor.process_response_logic(bot, event, state, processor.ReplyProcessorDeps(session=session, persona=persona, runtime=runtime, types=type_deps))
            new_payloads = bot.sent[before:]
            turn_reply = "".join(str(value or "") for value in new_payloads)
            turns.append({"mode": "yaml" if isinstance(prompt, dict) else "normal", "input": event.get_plaintext(), "reply": turn_reply, "sent": len(new_payloads), "reply_delivery_confirmed": bool(state.get("reply_delivery_confirmed", False)), "delivery_unknown": bool(state.get("delivery_unknown", False))})
            if logger.failed or (new_payloads and receipt != "confirmed"):
                break
            if str(case.get("surface", "group")) != "private":
                group_window.append({"message_id": str(getattr(event, "message_id", "")), "user_id": str(getattr(event, "user_id", "")), "sender_name": str(getattr(getattr(event, "sender", None), "nickname", "")), "text": event.get_plaintext(), "source_kind": "user"})
                # Only production's confirmed-delivery code calls
                # ``record_group_msg``.  Preserve that distinction here rather
                # than reconstructing a bot reply from ``bot.sent``.
                group_window.extend(captured_group_replies)
    finally:
        if original_window is not None:
            processor.build_group_context_window, processor.get_recent_group_msgs = original_window, original_recent
        yaml_mod.get_group_topic_summary, yaml_mod.get_recent_group_msgs = original_yaml_topic, original_yaml_recent
        yaml_mod.register_groupmate_qzone_agent_tools = original_qzone_register
        for name, value in original_registrars.items():
            setattr(yaml_mod, name, value)
        yaml_mod.load_sticker_feedback = original_sticker_feedback
    reply = ""
    for payload in bot.sent:
        if isinstance(payload, str):
            reply += payload
        elif isinstance(payload, list):
            reply += "".join(str(getattr(part, "data", {}).get("text", "")) for part in payload)
        else:
            reply += str(payload or "")
    delivery = "capture_confirmed" if receipt == "confirmed" and bot.sent else "unknown" if receipt == "unknown" and bot.sent else "failed" if receipt == "failed" and bot.sent else "not_sent"
    return {"reply": reply, "status": "failed" if logger.failed else "completed" if delivery == "capture_confirmed" else delivery if delivery in {"unknown", "failed"} else "no_reply", "turns": turns,
        "trace": str(state.get("reply_trace_id", "") or ""), "coverage": "normal_public_wrapper+yaml_public_wrapper+multi_turn" if isinstance(prompt, dict) else "normal_public_wrapper+multi_turn",
        "synthetic_receipts": [receipt] * len(bot.sent), "confirmed_history": len(confirmed_history), "send_attempt_count": len(bot.sent), "delivery": delivery, "generated": bool(bot.sent)}


class _NoNetworkClient:
    async def __aenter__(self) -> "_NoNetworkClient": return self
    async def __aexit__(self, *_a: Any) -> None: return None
