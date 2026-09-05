from __future__ import annotations

import asyncio
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from ._loader import load_personification_module


processor = load_personification_module("plugin.personification.handlers.reply_pipeline.processor")
config_module = load_personification_module("plugin.personification.config")
turn_media = load_personification_module("plugin.personification.core.turn_media")
yaml_processor = load_personification_module("plugin.personification.handlers.yaml_pipeline.processor")
planner = load_personification_module("plugin.personification.agent.runtime.planner")


class _Text:
    type = "text"
    def __init__(self, text: str) -> None:
        self.data = {"text": text}


class _At:
    type = "at"
    def __init__(self, qq: str) -> None:
        self.data = {"qq": qq}


class _GroupEvent:
    group_id = "g"
    user_id = "trigger"
    message_id = "trigger-msg"
    sender = SimpleNamespace(nickname="触发者", card="", role="member")
    def __init__(self) -> None:
        self.message = [_At("bot"), _Text("触发")]
    def get_plaintext(self) -> str:
        return "触发"


class _Mention:
    type = "at"

    def __init__(self, qq: str) -> None:
        self.data = {"qq": qq}


def test_normal_outer_prepares_batch_record_without_fake_trigger_user() -> None:
    events = [
        {"message_id": "m1", "user_id": "u1", "sender_name": "甲", "text": "第一句"},
        {"message_id": "m2", "user_id": "u2", "sender_name": "乙", "text": "第二句", "is_direct_mention": True},
    ]
    content, speaker, metadata = processor.prepare_incoming_history_record(
        is_private_session=False, batched_events=events, fallback_content="trigger-only",
        fallback_speaker="触发者", image_urls=[], image_detail="auto",
        trigger_user_id="trigger", trigger_message_id="trigger-msg", trigger_group_id="g",
    )
    assert metadata["source_kind"] == "user_batch"
    assert speaker == "多人群聊批次"
    assert "user_id" not in metadata
    assert metadata["trigger_user_id"] == "trigger"
    assert metadata["trigger_message_id"] == "trigger-msg"
    assert "不可信群聊数据" in str(content)
    assert "甲|uid=u1" in str(content)
    assert "乙|uid=u2" in str(content)


def test_agent_preparation_shares_group_envelope_and_keeps_single_private_shape() -> None:
    events = [
        {"message_id": "m1", "user_id": "u1", "sender_name": "甲", "text": "第一", "reply_to_user_id": "old"},
        {"message_id": "m2", "user_id": "u2", "sender_name": "乙", "text": "第二"},
    ]
    kwargs = dict(is_private_session=False, batched_events=events, fallback_content="trigger", fallback_speaker="触发", image_urls=[], image_detail="auto", trigger_user_id="u2", trigger_message_id="m2", trigger_group_id="g")
    normal, _, _ = processor.prepare_incoming_history_record(**kwargs)
    agent = processor.prepare_agent_incoming_content(**kwargs)
    assert str(agent) == str(normal)
    assert str(agent).find("甲|uid=u1|回复用户=old") < str(agent).find("乙|uid=u2")
    private = processor.prepare_agent_incoming_content(**{**kwargs, "is_private_session": True, "batched_events": events[:1]})
    assert private == "trigger"


def test_normal_processor_reprojects_selected_referent_into_yaml_provider_request(monkeypatch) -> None:  # noqa: ANN001
    """A text-only follow-up can activate exactly its selected historical image.

    This exercises the normal processor from an initially image-free event,
    through referent selection and direct-mode recomputation, into the real
    YAML provider call.  An unselected historical image must not leak there.
    """
    selected_image = "data:image/png;base64,c2VsZWN0ZWQtcmVmZXJlbnQ="
    unselected_image = "data:image/png;base64,dW5zZWxlY3RlZC1oaXN0b3J5"
    selected = turn_media.TurnMediaRef(
        media_id="selected", ref=selected_image, origin="history",
        owner_user_id="image-owner", message_id="image-message", kind="image",
        file_id="selected-file", content_hash="selected-hash", reference_role="selected_referent",
    )
    selected_same_transport = turn_media.TurnMediaRef(
        media_id="selected-second-owner", ref=selected_image, origin="history",
        owner_user_id="second-owner", message_id="second-message", kind="image",
        file_id="second-file", content_hash="selected-hash", reference_role="selected_referent",
    )
    unselected = turn_media.TurnMediaRef(
        media_id="background", ref=unselected_image, origin="history",
        owner_user_id="other-owner", message_id="other-message", kind="image",
        file_id="background-file", content_hash="background-hash", reference_role="background",
    )

    class _Resolver:
        async def resolve(self, **_kwargs):  # noqa: ANN003
            return SimpleNamespace(
                active_media=(selected, selected_same_transport),
                media_manifest=(unselected, selected, selected_same_transport),
                diagnostic_code="followup_referent_resolved", addressing_target="bot",
                semantic_referent="selected", confidence=1.0, candidates=(selected,),
                context_fields=lambda: {"selected_media_id": "selected"},
            )

    model_messages: list[dict] = []
    stored_messages: list[dict] = []
    logger = SimpleNamespace(debug=lambda *_a, **_k: None, info=lambda *_a, **_k: None,
                             warning=lambda *_a, **_k: None, error=lambda *_a, **_k: None)

    async def _call_ai_api(messages):  # noqa: ANN001
        model_messages.extend(messages)
        return "[NO_REPLY]"

    async def _run_yaml(bot, event, group_id, user_id, user_name, level_name, prompt, history, **kwargs):  # noqa: ANN001
        trigger_reason = kwargs.pop("trigger_reason", "")
        kwargs.pop("disable_network_hooks", None)
        await yaml_processor.process_yaml_response_logic(
            bot, event,
            group_id=group_id, user_id=user_id, user_name=user_name, level_name=level_name,
            prompt_config=prompt, chat_history=history, trigger_reason=trigger_reason,
            get_current_time=lambda: datetime(2026, 9, 5, 12, 0, 0),
            format_time_context=lambda _now: "noon", bot_statuses={},
            get_group_config=lambda _group_id: {"schedule_enabled": False},
            plugin_config=plugin_config, get_schedule_prompt_injection=lambda _prompt="": "",
            schedule_disabled_override_prompt=lambda: "", build_grounding_context=lambda *_a, **_k: "",
            call_ai_api=_call_ai_api,
            parse_yaml_response=lambda _text: {"status": "", "think": "", "action": "", "messages": []},
            message_segment_cls=SimpleNamespace, sanitize_history_text=str,
            private_session_prefix="private_", build_private_session_id=lambda value: f"private_{value}",
            build_group_session_id=str, append_session_message=lambda *_a, **_k: None,
            record_group_msg=None, logger=logger, user_blacklist={}, disable_network_hooks=True,
            **kwargs,
        )

    semantic_frame = planner.turn_plan_to_semantic_frame(planner.TurnPlan(
        reply_action="reply", speech_act="answer", research_need="none", output_mode="text",
        tool_intent=[], ambiguity_level="low", message_target="bot",
    ))
    intent_decision = SimpleNamespace(ambiguity_level="low", recommend_silence=False)
    prepared_semantics = SimpleNamespace(
        recent_bot_replies=[], data_dir=None, inner_state={}, emotion_state={}, semantic_frame=semantic_frame,
        intent_decision=intent_decision, message_intent="chat", arbitration="reply", emotion_block="",
    )
    plugin_config = config_module.Config(
        personification_agent_enabled=False, personification_schedule_global=False,
        personification_qq_expression_enabled=False, personification_image_input_mode="direct",
    )
    monkeypatch.setattr(processor, "refresh_bot_group_mute_state", lambda *_a, **_k: _false())
    monkeypatch.setattr(processor, "extract_forward_message_content", lambda *_a, **_k: _empty())
    monkeypatch.setattr(processor, "review_pending_sticker_reaction", lambda *_a, **_k: _none())
    monkeypatch.setattr(processor, "get_group_followup_referent_resolver", lambda: _Resolver())
    monkeypatch.setattr(processor, "get_recent_group_msgs", lambda *_a, **_k: [])
    monkeypatch.setattr(processor, "build_group_context_window", lambda *_a, **_k: [])
    monkeypatch.setattr(processor, "build_group_conversation_context", lambda **_k: SimpleNamespace(
        relationship_hint="", plugin_episode=None, topic_state=None, peer_bot_episodes=(),
    ))
    monkeypatch.setattr(processor, "render_group_conversation_context", lambda *_a, **_k: "")
    monkeypatch.setattr(processor, "render_topic_state_trace_detail", lambda *_a, **_k: "")
    monkeypatch.setattr(processor, "render_plugin_episode_trace_detail", lambda *_a, **_k: "")
    monkeypatch.setattr(processor, "media_summary_timeout_seconds", lambda *_a, **_k: 0.0)
    monkeypatch.setattr(processor, "prepare_reply_semantics", lambda **_k: _prepared(prepared_semantics))
    monkeypatch.setattr(processor, "prepare_meme_turn_context", lambda **_k: {})
    monkeypatch.setattr(processor, "format_meme_turn_prompt", lambda _value: "")
    monkeypatch.setattr(yaml_processor, "get_recent_group_msgs", lambda *_a, **_k: [])
    monkeypatch.setattr(yaml_processor, "get_group_topic_summary", lambda *_a, **_k: "")

    session = processor.SessionDeps(
        private_session_prefix="private_", looks_like_private_command=lambda _text: False,
        ensure_session_history=lambda *_a, **_k: None, build_private_session_id=lambda value: f"private_{value}",
        build_group_session_id=str, sanitize_session_messages=lambda messages: messages,
        get_session_messages=lambda _session_id: list(stored_messages),
        append_session_message=lambda _session_id, role, content, **meta: stored_messages.append({"role": role, "content": content, **meta}),
        sanitize_history_text=str, build_private_anti_loop_hint=lambda _messages: "",
    )
    persona = processor.PersonaDeps(
        load_prompt=lambda _group_id: {"system": "persona", "input": "{history_last}"}, sign_in_available=False,
        get_user_data=lambda _user_id: {}, get_level_name=lambda _score: "friend", update_user_data=lambda *_a, **_k: None,
        get_group_config=lambda _group_id: {}, get_group_style=lambda _group_id: "", favorability_attitudes={},
        get_custom_title=lambda _user_id: "", default_bot_nickname="bot",
    )
    runtime = processor.RuntimeDeps(
        is_msg_processed=lambda _message_id: False, logger=logger, superusers=set(),
        get_configured_api_providers=lambda: [{"name": "test"}], should_avoid_interrupting=lambda *_a: False,
        module_instance_id=1, process_yaml_response_logic=_run_yaml, plugin_config=plugin_config,
        get_current_time=lambda: datetime(2026, 9, 5, 12, 0, 0), format_time_context=lambda _now: "noon",
        schedule_disabled_override_prompt=lambda: "", get_schedule_prompt_injection=lambda: "",
        build_grounding_context=lambda _query: "", update_private_interaction_time=lambda _user_id: None,
        call_ai_api=_call_ai_api, save_plugin_runtime_config=None, user_blacklist={}, record_group_msg=lambda *_a, **_k: None,
        split_text_into_segments=lambda value: [value], message_segment_cls=SimpleNamespace,
        get_sticker_files=lambda: [], get_http_client=lambda: object(), get_whitelisted_groups=lambda: [],
    )
    types = processor.TypeDeps(
        poke_event_cls=type("Poke", (), {}), message_event_cls=_GroupEvent, group_message_event_cls=_GroupEvent,
        private_message_event_cls=type("Private", (), {}), message_cls=list,
    )
    event = _GroupEvent()
    event.message = [_Mention("bot"), _Text("这张图怎么样？")]
    event.message_id = "follow-up"
    event.user_id = "asker"
    event.sender = SimpleNamespace(nickname="提问者", card="", role="member")
    state = {"response_deadline": 10_000_000_000.0, "turn_media_context": []}
    asyncio.run(processor._process_response_logic_impl(
        SimpleNamespace(self_id="bot"), event,
        state,
        processor.ReplyProcessorDeps(session=session, persona=persona, runtime=runtime, types=types),
    ))
    image_parts = [
        part for message in model_messages
        for part in (message.get("content") if isinstance(message.get("content"), list) else [])
        if isinstance(part, dict) and part.get("type") == "image_url"
    ]
    assert [part["image_url"]["url"] for part in image_parts] == [selected_image]
    # Provider transport de-duplicates, while the per-occurrence manifest
    # still keeps both owners for provenance and per-media summary binding.
    assert [item["owner_user_id"] for item in state["turn_media_context"]] == [
        "image-owner", "second-owner",
    ]


async def _false() -> bool:
    return False


async def _empty() -> str:
    return ""


async def _none() -> None:
    return None


async def _prepared(value):  # noqa: ANN001
    return value


def test_process_response_logic_starts_trace_before_flushing_buffer_diagnostics(monkeypatch) -> None:
    events: list[tuple[str, dict]] = []
    class Trace:
        started = False
        @staticmethod
        def current_trace_id(): return ""
        @staticmethod
        def start_trace(**kwargs): Trace.started = True; events.append(("start", kwargs)); return "trace-fixed"
        @staticmethod
        def set_current_trace_id(value): events.append(("set", {"trace_id": value})); return object()
        @staticmethod
        def record_stage(**kwargs): assert Trace.started; events.append((kwargs["key"], kwargs))
        @staticmethod
        def get_trace(_trace_id): return {"outcome": "ok"}
        @staticmethod
        def reset_current_trace_id(_token): events.append(("reset", {}))
        @staticmethod
        def finish_trace(**_kwargs): return None
    monkeypatch.setitem(sys.modules, "plugin.personification.core.reply_turn_trace", Trace)
    core = sys.modules.get("plugin.personification.core")
    if core is not None: monkeypatch.setattr(core, "reply_turn_trace", Trace, raising=False)
    async def noop(*_args, **_kwargs): return None
    monkeypatch.setattr(processor, "_process_response_logic_impl", noop)
    runtime = SimpleNamespace(user_policy_gate=None, plugin_config=SimpleNamespace(personification_turn_trace_enabled=True), logger=SimpleNamespace())
    deps = processor.ReplyProcessorDeps(session=SimpleNamespace(), persona=SimpleNamespace(), runtime=runtime, types=SimpleNamespace())
    event = _GroupEvent(); event.group_id = "456"; event.user_id = "123"; event.message_id = "789"
    state = {"buffer_trace_diagnostics": [{"code": "dequeue", "count": 2, "generation": 3, "wait_ms": 4}], "sensitive": "正文 session QQ 456 789 D:\\secret token"}
    asyncio.run(processor.process_response_logic(SimpleNamespace(), event, state, deps))
    names = [name for name, _ in events]
    assert names.index("start") < names.index("buffer_diagnostic") < names.index("ingress")
    buffer = next(value for name, value in events if name == "buffer_diagnostic")
    assert buffer["trace_id"] == "trace-fixed" and "code=dequeue count=2 generation=3 wait_ms=4" == buffer["detail"]
    assert "buffer_trace_diagnostics" not in state
    assert all(marker not in repr(buffer) for marker in ("正文", "session", "456", "789", "D:\\secret", "token"))


def test_buffer_failure_trace_uses_existing_trace_id_only(monkeypatch) -> None:
    stages = []
    trace = SimpleNamespace(record_stage=lambda **kwargs: stages.append(kwargs))
    monkeypatch.setitem(sys.modules, "plugin.personification.core.reply_turn_trace", trace)
    core = sys.modules.get("plugin.personification.core")
    if core is not None: monkeypatch.setattr(core, "reply_turn_trace", trace, raising=False)
    buffer = load_personification_module("plugin.personification.handlers.reply_buffer")
    buffer._record_buffer_failure_trace({"reply_trace_id": "trace-fixed"}, "processing_failure", count=2, generation=3, wait_ms=4)
    buffer._record_buffer_failure_trace({}, "processing_failure", count=2, generation=3, wait_ms=4)
    assert len(stages) == 1 and stages[0]["trace_id"] == "trace-fixed"


def test_normal_and_yaml_block_markers_are_silent_without_fixed_refusal() -> None:
    root = Path(__file__).resolve().parents[1]
    normal = (root / "handlers" / "reply_pipeline" / "processor.py").read_text(encoding="utf-8")
    yaml = (root / "handlers" / "yaml_pipeline" / "processor.py").read_text(encoding="utf-8")

    assert 'reply_content = "这个我不能接。"' not in normal
    assert 'reply_content = "这个我不能接。"' not in yaml
    assert "当前静默结束本轮" in normal
    assert "当前静默结束本轮" in yaml


def test_normal_and_yaml_share_self_continuity_snapshot_gate_and_delivery() -> None:
    root = Path(__file__).resolve().parents[1]
    normal = (root / "handlers" / "reply_pipeline" / "processor.py").read_text(encoding="utf-8")
    yaml = (root / "handlers" / "yaml_pipeline" / "processor.py").read_text(encoding="utf-8")

    for source in (normal, yaml):
        assert "get_bot_self_continuity_store" in source
        assert "render_self_continuity_prompt" in source
        assert "self_continuity_snapshot=" in source
        assert "deliver_self_consistent_segment(" in source
        assert "claims_for_segment(" in source
