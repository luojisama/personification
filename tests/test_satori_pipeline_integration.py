"""Satori reaches the ordinary reply processor without a second fake core."""
from __future__ import annotations

import asyncio
import base64
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import nonebot.adapters
import pytest
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageEvent, MessageSegment, PrivateMessageEvent

from ._loader import load_personification_module


_satori_root = os.environ.get("PERSONIFICATION_SATORI_ADAPTERS", "").strip()
if _satori_root:
    satori_adapters = Path(_satori_root)
    if satori_adapters.is_dir() and str(satori_adapters) not in nonebot.adapters.__path__:
        nonebot.adapters.__path__.append(str(satori_adapters))

PrivateMessageCreatedEvent = pytest.importorskip("nonebot.adapters.satori.event").PrivateMessageCreatedEvent  # noqa: E402


bridge = load_personification_module("plugin.personification.handlers.satori_reply_bridge")
processor = load_personification_module("plugin.personification.handlers.reply_pipeline.processor")
config_module = load_personification_module("plugin.personification.config")
planner = load_personification_module("plugin.personification.agent.runtime.planner")
pipeline_sticker = load_personification_module("plugin.personification.handlers.reply_pipeline.pipeline_sticker")
reply_buffer = load_personification_module("plugin.personification.handlers.reply_buffer")


def _event(*, sender: str = "member-A"):
    return PrivateMessageCreatedEvent.model_validate({
        "type": "message-created", "timestamp": int(datetime.now(timezone.utc).timestamp() * 1000),
        "login": {"sn": 1, "status": 1, "adapter": "test", "platform": "discord", "user": {"id": "bot-A"}},
        "channel": {"id": "dm-A", "type": 1}, "user": {"id": sender},
        "message": {"id": "incoming-1", "content": "看看这两张<img src=\"https://media.example.test/a.png\"/><img src=\"https://media.example.test/a.png\"/>"},
    })


def _deps(monkeypatch, observed: list[list[dict]]):  # noqa: ANN001
    provider_inputs: list[dict] = []
    extracted_images: list[str] = []
    dispatch_errors: list[str] = []
    stored_messages: list[dict] = []
    config = config_module.Config(
        personification_agent_enabled=False,
        personification_schedule_global=False,
        personification_qq_expression_enabled=False,
        personification_final_dialogue_gate_enabled=False,
        personification_image_input_mode="direct",
        personification_humanize_typing_enabled=False,
    )
    logs: list[str] = []
    logger = SimpleNamespace(debug=lambda *a, **_k: logs.append(" ".join(map(str, a))), info=lambda *a, **_k: logs.append(" ".join(map(str, a))),
                             warning=lambda *a, **_k: logs.append(" ".join(map(str, a))), error=lambda *a, **_k: logs.append(" ".join(map(str, a))))
    semantic = planner.turn_plan_to_semantic_frame(planner.TurnPlan(
        reply_action="reply", speech_act="answer", research_need="none", output_mode="text",
        tool_intent=[], ambiguity_level="low", message_target="bot",
    ))
    prepared = SimpleNamespace(recent_bot_replies=[], data_dir=None, inner_state={}, emotion_state={},
        semantic_frame=semantic, intent_decision=SimpleNamespace(ambiguity_level="low", recommend_silence=False),
        message_intent="chat", arbitration="reply", emotion_block="")

    async def provider(messages):  # noqa: ANN001
        observed.append(messages)
        # The first call is the normal reply Provider; the second is the
        # independent shared final-review call made by this real pipeline.
        if len(observed) > 1:
            return '{"action":"accept","text":"看到了，挺可爱的。","reason":"ok","segments":["看到了，挺可爱的。"],"attribution_verdict":"current_human","persona_verdict":"consistent","self_claims":[]}'
        return "看到了，挺可爱的。"

    async def false(): return False
    async def empty(): return ""
    async def none(): return None
    monkeypatch.setattr(processor, "refresh_bot_group_mute_state", lambda *_a, **_k: false())
    monkeypatch.setattr(processor, "review_pending_sticker_reaction", lambda *_a, **_k: none())
    monkeypatch.setattr(processor, "get_recent_group_msgs", lambda *_a, **_k: [])
    monkeypatch.setattr(processor, "build_group_context_window", lambda *_a, **_k: [])
    monkeypatch.setattr(processor, "prepare_reply_semantics", lambda **_k: _await_value(prepared))
    monkeypatch.setattr(processor, "media_summary_timeout_seconds", lambda *_a, **_k: 0.0)
    monkeypatch.setattr(processor, "prepare_meme_turn_context", lambda **_k: {})
    monkeypatch.setattr(processor, "format_meme_turn_prompt", lambda _value: "")
    async def inject_downloaded_image(segment, *, image_urls, transport_aliases, **_kwargs):  # noqa: ANN001
        # Downloading is the sole external boundary.  Keep the normal
        # processor's current-image loop, media projection and Provider wire
        # formatting intact, but make its validated bytes deterministic.
        assert str(segment.data.get("url") or "").startswith("https://media.example.test/")
        source = str(segment.data["url"])
        extracted_images.append(source)
        transport = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9JpZ4AAAAASUVORK5CYII="
        transport_aliases[source] = transport
        image_urls.append(transport)
    monkeypatch.setattr(processor, "_extract_images_from_segment", inject_downloaded_image)
    original_build_message = processor.build_user_message_content
    def observe_wire(**kwargs):  # noqa: ANN003
        provider_inputs.append(dict(kwargs))
        return original_build_message(**kwargs)
    monkeypatch.setattr(processor, "build_user_message_content", observe_wire)
    original_dispatch = processor._dispatch_reply_part
    async def observe_dispatch(**kwargs):  # noqa: ANN003
        try:
            return await original_dispatch(**kwargs)
        except Exception as exc:
            dispatch_errors.append(repr(exc))
            raise
    monkeypatch.setattr(processor, "_dispatch_reply_part", observe_dispatch)
    def append_message(_session_id, role, content, **metadata):  # noqa: ANN001
        stored_messages.append({"role": role, "content": content, **metadata})
    session = processor.SessionDeps("private_", lambda _t: False, lambda *_a, **_k: None,
        lambda value: f"private_{value}", str, lambda m: m, lambda _s: list(stored_messages), append_message, str, lambda _m: "")
    # Text prompt exercises the ordinary Provider branch.  YAML itself has
    # dedicated pipeline coverage and would not prove this bridge route.
    persona = processor.PersonaDeps(lambda _g: "真寻", False,
        lambda _u: {}, lambda _v: "friend", lambda *_a, **_k: None, lambda _g: {}, lambda _g: "", {}, lambda _u: "", "bot")
    runtime = processor.RuntimeDeps(
        lambda _m: False, logger, set(), lambda: [{"name": "fake"}], lambda *_a: False, 1,
        lambda *_a, **_k: None, config, lambda: datetime.now(), lambda _t: "now", lambda: "", lambda: "",
        lambda _q: "", lambda _u: None, provider, None, {}, lambda *_a, **_k: None, lambda text: [text],
        MessageSegment, lambda: [], lambda: object(), lambda: [],
    )
    types = processor.TypeDeps(type("PokeEvent", (), {}), MessageEvent, GroupMessageEvent, PrivateMessageEvent, Message)
    return processor.ReplyProcessorDeps(session, persona, runtime, types), config, logger, provider_inputs, extracted_images, logs, dispatch_errors


async def _await_value(value):
    return value


def test_satori_private_text_and_multimedia_reach_real_processor_provider_and_send(monkeypatch):  # noqa: ANN001
    observed: list[list[dict]] = []
    deps, config, logger, wire_inputs, extracted_images, logs, dispatch_errors = _deps(monkeypatch, observed)
    sent: list[str] = []

    class Bot:
        self_id = "bot-A"
        features = ["message.create", "media"]
        async def send(self, _event, content):
            sent.append(content)
            return [{"id": "out-1"}]

    async def rule(_event, _state): return True
    states: list[dict] = []
    async def real_processor(bot, event, state, deps):  # noqa: ANN001
        await processor.process_response_logic(bot, event, state, deps)
        states.append(dict(state))
    async def immediate_timer(*args, **kwargs):  # noqa: ANN002, ANN003
        kwargs["delay"] = 0.0
        await reply_buffer.run_buffer_timer(*args, **kwargs)
    async def run():
        await bridge.dispatch_satori_reply(
            Bot(), _event(), {}, personification_rule=rule, process_response_logic=real_processor,
            reply_processor_deps=deps, msg_buffer={}, message_cls=Message, message_segment_cls=MessageSegment,
            message_event_cls=MessageEvent, group_message_event_cls=GroupMessageEvent,
            private_message_event_cls=PrivateMessageEvent, poke_event_cls=deps.types.poke_event_cls, logger=logger,
            plugin_config=config, run_buffer_timer=immediate_timer,
        )
        await asyncio.sleep(0.05)
    asyncio.run(run())
    assert len(observed) >= 2, (len(observed), logs, states)
    image_parts = [
        part
        for request in observed
        for message in request
        for part in (message.get("content") if isinstance(message.get("content"), list) else [])
        if isinstance(part, dict) and part.get("type") == "image_url"
    ]
    assert len(image_parts) == 1, (extracted_images, wire_inputs, [
        (message.get("role"), type(message.get("content")).__name__, repr(message.get("content"))[:300])
        for request in observed for message in request
    ])
    assert sent and "看到了" in sent[-1] and "[CQ:" not in sent[-1], (states, logs, dispatch_errors)


def test_satori_own_echo_never_enters_provider(monkeypatch):  # noqa: ANN001
    observed: list[list[dict]] = []
    deps, config, logger, _wire_inputs, _extracted_images, _logs, _dispatch_errors = _deps(monkeypatch, observed)
    class Bot:
        self_id = "bot-A"
        features = ["message.create"]
        async def send(self, *_args): return [{"id": "out"}]
    async def rule(_event, _state): return True
    asyncio.run(bridge.dispatch_satori_reply(
        Bot(), _event(sender="bot-A"), {}, personification_rule=rule, process_response_logic=processor.process_response_logic,
        reply_processor_deps=deps, msg_buffer={}, message_cls=Message, message_segment_cls=MessageSegment,
        message_event_cls=MessageEvent, group_message_event_cls=GroupMessageEvent,
        private_message_event_cls=PrivateMessageEvent, poke_event_cls=deps.types.poke_event_cls, logger=logger, plugin_config=config,
    ))
    assert observed == []


def test_new_modules_import_in_fresh_python_processes():
    import subprocess
    modules = [
        "plugin.personification.core.interaction_adapter",
        "plugin.personification.handlers.satori_reply_bridge",
        "plugin.personification.core.moderation",
        "plugin.personification.core.route_probe_service",
        "plugin.personification.core.route_probe_runtime",
        "plugin.personification.jobs.route_probe_schedule",
    ]
    for module in modules:
        workspace = Path(__file__).parents[1]
        script = (
            "import runpy; "
            "loader=runpy.run_path('tests/_loader.py')['load_personification_module']; "
            f"loader('{module}')"
        )
        completed = subprocess.run([sys.executable, "-c", script], cwd=workspace, env=os.environ.copy(), capture_output=True, text=True, timeout=20)
        assert completed.returncode == 0, completed.stderr
