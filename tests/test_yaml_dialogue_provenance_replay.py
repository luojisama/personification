from __future__ import annotations

import asyncio
from datetime import datetime
from types import SimpleNamespace

import pytest

from ._loader import load_personification_module


config_module = load_personification_module("plugin.personification.config")
reply_turn_trace = load_personification_module("plugin.personification.core.reply_turn_trace")
yaml_processor = load_personification_module("plugin.personification.handlers.yaml_pipeline.processor")
tool_registry_module = load_personification_module("plugin.personification.agent.tool_registry")
agent_synthesis_module = load_personification_module(
    "plugin.personification.agent.runtime.final_synthesis"
)


class _Bot:
    self_id = "persona-bot"

    def __init__(self) -> None:
        self.sent: list[object] = []

    async def send(self, _event, payload) -> dict[str, int]:  # noqa: ANN001
        self.sent.append(payload)
        return {"message_id": len(self.sent)}


class _Segments:
    @staticmethod
    def image(value: str) -> str:
        return value

    @staticmethod
    def poke(value: int) -> tuple[str, int]:
        return ("poke", value)


def _event(*, text: str, message_id: str, reply_to_msg_id: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        message=[],
        group_id=1,
        user_id="human-1",
        message_id=message_id,
        reply_to_message_id=reply_to_msg_id,
        reply=None,
        get_plaintext=lambda: text,
    )


def test_yaml_user_comment_on_persona_bot_quote_reviews_attribution_and_sends(monkeypatch) -> None:
    """A human quote of Bot content must pass review as a human-triggered reply."""
    async def _review(messages, **_kwargs):  # noqa: ANN001
        return (
            '{"action":"accept","persona_verdict":"consistent",'
            '"attribution_verdict":"safe_quote_or_rebuttal","text":"",'
            '"reason":"current human asks about the quoted Bot sticker",'
            '"flags":[],"segments":["这是我刚才用来表达无奈的表情。"],"self_claims":[]}'
        )

    event = _event(text="这个表情什么意思？", message_id="human-comment", reply_to_msg_id="bot-quoted")
    event.reply = SimpleNamespace(
        message_id="bot-quoted", user_id="persona-bot", sender=SimpleNamespace(user_id="persona-bot"),
        message=[SimpleNamespace(type="text", data={"text":"我刚发的说明"}), SimpleNamespace(type="image", data={"file":"opaque"})],
    )
    candidate = "这是我刚才用来表达无奈的表情。"
    bot, primary, reviews, stages = _run_yaml_turn(
        monkeypatch,
        history=[{"message_id":"bot-quoted","user_id":"persona-bot","source_kind":"bot_reply","confirmed":True,"text":"我刚发的说明"}],
        event=event, candidate=candidate, review_call=_review, final_gate_enabled=False,
    )
    # The trigger stays the actual human; quote provenance remains the Bot.
    primary_text = "\n".join(str(message.get("content", "")) for message in primary[0])
    review_text = "\n".join(str(message.get("content", "")) for message in reviews[0])
    assert event.user_id == "human-1"
    assert '"speaker_kind":"persona_bot"' in primary_text
    assert '"source_kind":"bot_reply"' in primary_text
    assert '"speaker_kind":"human"' in review_text
    assert '"current":true' in review_text
    assert event.get_plaintext() in review_text
    assert '"reply_ref":"message_1"' in review_text
    assert '"speaker_kind":"persona_bot"' in review_text
    assert candidate in review_text
    assert any(stage.get("key") == "yaml_dialogue_provenance" for stage in stages)
    # This is the actual dispatch boundary: a valid attribution + persona
    # verdict must reach outbound, while the quoted Bot is never its author.
    assert bot.sent == [candidate.rstrip("。")]


def _run_yaml_turn(
    monkeypatch,
    *,
    history: list[dict[str, object]],
    event: SimpleNamespace,
    candidate: str,
    review_call,
    final_gate_enabled: bool,
    parse_yaml_response=None,
    tts_service=None,
    configure=None,
    agent_tool_caller=None,
    tool_registry=None,
    **turn_kwargs,
) -> tuple[_Bot, list[list[dict[str, object]]], list[list[dict[str, object]]], list[dict[str, object]]]:  # noqa: ANN001
    primary_prompts: list[list[dict[str, object]]] = []
    review_prompts: list[list[dict[str, object]]] = []
    stages: list[dict[str, object]] = []

    async def _call_ai_api(messages):  # noqa: ANN001
        primary_prompts.append(messages)
        return candidate

    async def _review_call(messages, **kwargs):  # noqa: ANN001
        review_prompts.append(messages)
        return await review_call(messages, **kwargs)

    monkeypatch.setattr(reply_turn_trace, "record_stage", lambda **kwargs: stages.append(kwargs))
    monkeypatch.setattr(reply_turn_trace, "finish_trace", lambda **_kwargs: None)
    monkeypatch.setattr(yaml_processor, "build_group_context_window", lambda *_args, **_kwargs: history)
    monkeypatch.setattr(yaml_processor, "get_recent_group_msgs", lambda *_args, **_kwargs: history)
    monkeypatch.setattr(yaml_processor, "get_group_topic_summary", lambda *_args, **_kwargs: "")

    async def _empty_sticker_feedback() -> dict[str, object]:
        # This isolated YAML replay does not initialise the plugin DataStore.
        # Feedback is unrelated to the provenance gate under test.
        return {}

    monkeypatch.setattr(yaml_processor, "load_sticker_feedback", _empty_sticker_feedback)
    # Agent-path replays should not reach the optional QZone settings store.
    # QZone registration is outside this local video/response-review contract.
    monkeypatch.setattr(yaml_processor, "register_groupmate_qzone_agent_tools", lambda *_a, **_k: None)

    plugin_config = config_module.Config(
        personification_agent_enabled=False,
        personification_qq_expression_enabled=False,
        personification_schedule_global=False,
        personification_group_followup_referent_enabled=False,
        personification_final_dialogue_gate_enabled=final_gate_enabled,
    )
    if configure is not None:
        configure(plugin_config)
    logger = SimpleNamespace(
        debug=lambda *_args, **_kwargs: None,
        info=lambda *_args, **_kwargs: None,
        warning=lambda *_args, **_kwargs: None,
    )
    bot = _Bot()
    asyncio.run(
        yaml_processor.process_yaml_response_logic(
            bot,
            event,
            group_id="1",
            user_id="human-1",
            user_name="human",
            level_name="friend",
            prompt_config={"system": "persona", "input": "{history_last}"},
            chat_history=[],
            trigger_reason="",
            get_current_time=lambda: datetime(2026, 9, 5, 12, 0, 0),
            format_time_context=lambda _now: "noon",
            bot_statuses={},
            get_group_config=lambda _group_id: {"schedule_enabled": False},
            plugin_config=plugin_config,
            get_schedule_prompt_injection=lambda _prompt="": "",
            schedule_disabled_override_prompt=lambda: "",
            build_grounding_context=lambda *_args, **_kwargs: "",
            call_ai_api=_call_ai_api,
            review_call_ai_api=_review_call,
            parse_yaml_response=parse_yaml_response or (lambda text: {
                "status": "",
                "think": "",
                "action": "",
                "messages": [{"text": str(text), "sticker": ""}],
            }),
            message_segment_cls=_Segments,
            sanitize_history_text=str,
            private_session_prefix="private_",
            build_private_session_id=lambda user_id: f"private_{user_id}",
            build_group_session_id=str,
            append_session_message=lambda *_args, **_kwargs: None,
            record_group_msg=None,
            logger=logger,
            user_blacklist={},
            current_image_urls=[],
            disable_network_hooks=True,
            message_intent="banter",
            intent_ambiguity_level="low",
            raw_message_text=event.get_plaintext(),
            message_target="bot",
            recent_context_hint="",
            reply_required=True,
            tts_service=tts_service,
            agent_tool_caller=agent_tool_caller,
            tool_registry=tool_registry,
            **turn_kwargs,
        )
    )
    return bot, primary_prompts, review_prompts, stages


@pytest.mark.parametrize(
    "review_result",
    ["timeout", "invalid-json", "explicit-no-reply"],
    ids=["timeout", "invalid-json", "explicit-no-reply"],
)
def test_yaml_bot_history_attribution_review_fails_closed_without_sending(
    monkeypatch,
    review_result: str,
) -> None:  # noqa: ANN001
    """A required reply never restores a candidate after provenance review fails."""

    async def _review(_messages, **_kwargs):  # noqa: ANN001
        if review_result == "timeout":
            raise asyncio.TimeoutError("review timeout")
        if review_result == "explicit-no-reply":
            return '{"action":"no_reply","text":"","reason":"candidate is not grounded","flags":[]}'
        return "this is not review json"

    bot, primary_prompts, review_prompts, stages = _run_yaml_turn(
        monkeypatch,
        history=[
            {
                "message_id": "bot-previous",
                "user_id": "persona-bot",
                "source_kind": "bot_reply",
                "confirmed": True,
                "text": "我刚才说要带火把。",
            }
        ],
        event=_event(text="嗯", message_id="human-current"),
        candidate="对，我刚才就是让你带火把。",
        review_call=_review,
        # The mixed Bot/human projection, rather than a global always-on gate,
        # must force the actual final dialogue gate.
        final_gate_enabled=False,
    )

    assert primary_prompts
    prompt = "\n".join(str(message.get("content", "")) for message in primary_prompts[0])
    assert "## 有序对话归属投影" in prompt
    assert '"source_kind":"bot_reply"' in prompt
    assert '"speaker_kind":"persona_bot"' in prompt
    assert review_prompts and "有序归属投影" in str(review_prompts[0])
    assert any(stage.get("key") == "yaml_dialogue_provenance" for stage in stages)
    assert any(stage.get("key") == "yaml_no_reply" for stage in stages)
    assert bot.sent == []


def test_yaml_rewrite_uses_reviewed_text_not_mismatched_segments(monkeypatch) -> None:  # noqa: ANN001
    async def _review(_messages, **_kwargs):  # noqa: ANN001
        if "独立复核改写是否符合受信任核心人格" in str(_messages):
            return '{"action":"accept","persona_verdict":"consistent","flags":[]}'
        return (
            '{"action":"rewrite","persona_verdict":"rewrite","text":"那就两组，洞里拐弯容易漏。",'
            '"reason":"answer current human",'
            '"flags":[],"segments":["你刚才让大家带火把。"]}'
        )

    bot, _primary_prompts, review_prompts, stages = _run_yaml_turn(
        monkeypatch,
        history=[
            {
                "message_id": "human-previous",
                "user_id": "human-2",
                "source_kind": "user",
                "confirmed": True,
                "text": "带火把吧。",
            }
        ],
        event=_event(text="那带几组？", message_id="human-current", reply_to_msg_id="human-previous"),
        candidate="嗯。",
        review_call=_review,
        final_gate_enabled=True,
    )

    assert review_prompts
    assert any(stage.get("key") == "yaml_dialogue_provenance" for stage in stages)
    assert bot.sent == ["那就两组，洞里拐弯容易漏"]
    assert all("你刚才让大家带火把" not in str(payload) for payload in bot.sent)


def test_yaml_real_user_quote_can_pass_attribution_review_and_send(monkeypatch) -> None:  # noqa: ANN001
    async def _review(_messages, **_kwargs):  # noqa: ANN001
        return (
            '{"action":"accept","persona_verdict":"consistent","text":"","reason":"real user quote",'
            '"flags":[],"segments":["那就两组，洞里拐弯容易漏。"],'
            '"attribution_verdict":"safe_quote_or_rebuttal","self_claims":[]}'
        )

    bot, primary_prompts, review_prompts, stages = _run_yaml_turn(
        monkeypatch,
        history=[
            {
                "message_id": "human-previous",
                "user_id": "human-2",
                "source_kind": "user",
                "confirmed": True,
                "text": "带火把吧。",
            }
        ],
        event=_event(text="那带几组？", message_id="human-current", reply_to_msg_id="human-previous"),
        candidate="那就两组，洞里拐弯容易漏。",
        review_call=_review,
        final_gate_enabled=True,
    )

    assert primary_prompts and review_prompts
    review_text = "\n".join(str(message.get("content", "")) for message in review_prompts[0])
    assert '"reply_ref":"message_1"' in review_text
    assert '"speaker_kind":"human"' in review_text
    assert any(stage.get("key") == "yaml_dialogue_provenance" for stage in stages)
    # The configured terminal-punctuation policy removes the final full stop
    # at dispatch; the accepted human-quote reply still reaches outbound send.
    assert bot.sent == ["那就两组，洞里拐弯容易漏"]


def test_yaml_accept_projects_multi_messages_to_reviewed_text_once(monkeypatch) -> None:  # noqa: ANN001
    """Structured sticker records survive, but only canonical reviewed text is sent."""

    async def _review(_messages, **_kwargs):  # noqa: ANN001
        return (
            '{"action":"accept","persona_verdict":"consistent","text":"","reason":"safe",'
            '"flags":[],"segments":["第一句","第二句"]}'
        )

    def _parse(_text):  # noqa: ANN001
        return {
            "status": "",
            "think": "",
            "action": "",
            "messages": [
                {"text": "第一句", "sticker": ""},
                {"text": "第二句", "sticker": "approved-sticker"},
            ],
        }

    async def _no_sleep(*_args, **_kwargs):  # noqa: ANN002, ANN003
        return None

    monkeypatch.setattr(yaml_processor.asyncio, "sleep", _no_sleep)
    bot, _primary_prompts, _review_prompts, _stages = _run_yaml_turn(
        monkeypatch,
        history=[],
        event=_event(text="继续", message_id="human-current"),
        candidate="unused",
        review_call=_review,
        final_gate_enabled=True,
        parse_yaml_response=_parse,
    )

    # Before projection the accepted review segments would have been replayed
    # once for each YAML message.  The second record still exists for its
    # sticker authorization, but it cannot carry stale text.
    assert bot.sent == ["第一句", "第二句"]
    assert " ".join(bot.sent) == "第一句 第二句"


def test_yaml_final_local_sticker_uses_narrow_runtime(monkeypatch, tmp_path) -> None:
    from PIL import Image
    image = tmp_path / "ok.png"; Image.new("RGB", (4, 4), "pink").save(image, "PNG")
    seen = []
    async def allow(**kwargs):
        seen.append(kwargs["runtime"])
        return "data:image/png;base64,QUJD"
    async def choose(*_a, **_k): return image
    monkeypatch.setattr(yaml_processor, "choose_sticker_for_context", choose)
    monkeypatch.setattr(yaml_processor, "prepare_local_expression", allow)
    async def review(*_a, **_k): return '{"action":"accept","persona_verdict":"consistent","flags":[]}'
    bot, _primary, reviews, _stages = _run_yaml_turn(
        monkeypatch, history=[], event=_event(text="hi", message_id="m"), candidate="reply", review_call=review,
        final_gate_enabled=False,
        parse_yaml_response=lambda _text: {"status":"","think":"","action":"","messages":[{"text":"reply","sticker":"ok"}]},
        configure=lambda cfg: (setattr(cfg, "personification_sticker_path", str(tmp_path)), setattr(cfg, "personification_sticker_probability", 1.0)),
    )
    assert seen and seen[0].plugin_config.personification_sticker_path == str(tmp_path)
    assert reviews and bot.sent


def test_yaml_final_local_sticker_visual_rejection_sends_no_image(monkeypatch, tmp_path) -> None:
    from PIL import Image

    image = tmp_path / "rejected.png"
    Image.new("RGB", (4, 4), "pink").save(image, "PNG")

    async def reject(**_kwargs):
        # A visual/persona review rejection is fail-closed for the image only;
        # the independently reviewed text reply may still be delivered.
        return None

    async def choose(*_args, **_kwargs):
        return image

    monkeypatch.setattr(yaml_processor, "choose_sticker_for_context", choose)
    monkeypatch.setattr(yaml_processor, "prepare_local_expression", reject)

    async def review(*_args, **_kwargs):
        return '{"action":"accept","persona_verdict":"consistent","flags":[]}'

    bot, _primary, reviews, _stages = _run_yaml_turn(
        monkeypatch,
        history=[],
        event=_event(text="hi", message_id="rejected-local-expression"),
        candidate="text reply",
        review_call=review,
        final_gate_enabled=False,
        parse_yaml_response=lambda _text: {
            "status": "",
            "think": "",
            "action": "",
            "messages": [{"text": "text reply", "sticker": "rejected"}],
        },
        configure=lambda cfg: (
            setattr(cfg, "personification_sticker_path", str(tmp_path)),
            setattr(cfg, "personification_sticker_probability", 1.0),
        ),
    )

    assert reviews
    assert bot.sent == ["text reply"]
    assert all("base64://" not in str(payload) for payload in bot.sent)


def test_yaml_agent_executor_receives_narrow_runtime(monkeypatch) -> None:
    captured = []
    tool_caller = object()

    async def fake_agent(**kwargs):
        captured.append(kwargs["executor"])
        return agent_synthesis_module.AgentResult(
            text="agent reply",
            pending_actions=[],
        )

    monkeypatch.setattr(yaml_processor, "run_agent", fake_agent)
    monkeypatch.setattr(yaml_processor, "register_groupmate_qzone_agent_tools", lambda *_a, **_k: None)
    async def review(*_a, **_k): return '{"action":"accept","persona_verdict":"consistent","flags":[]}'
    bot, _primary, reviews, _stages = _run_yaml_turn(
        monkeypatch, history=[], event=_event(text="hi", message_id="agent"), candidate="fallback", review_call=review,
        final_gate_enabled=False, agent_tool_caller=tool_caller, tool_registry=tool_registry_module.ToolRegistry(),
        configure=lambda cfg: setattr(cfg, "personification_agent_enabled", True),
    )
    assert captured
    runtime = captured[0].runtime
    assert runtime.plugin_config.personification_agent_enabled is True
    assert runtime.agent_tool_caller is tool_caller
    assert runtime.response_review_call_ai_api is not None
    assert reviews and "agent reply" in str(reviews[0])
    assert bot.sent == ["agent reply"]


def test_yaml_canonical_projection_keeps_authorized_structured_records() -> None:
    projected = yaml_processor._project_parsed_messages_to_canonical_text(
        {
            "messages": [
                {"text": "旧文本", "sticker": ""},
                {
                    "text": "[IMAGE_B64]cGF5bG9hZA==[/IMAGE_B64]",
                    "sticker": "approved-sticker",
                    "media_authorized": True,
                },
            ]
        },
        "已审文本[IMAGE_B64]cGF5bG9hZA==[/IMAGE_B64]",
    )

    assert projected["messages"] == [
        {"text": "已审文本[IMAGE_B64]cGF5bG9hZA==[/IMAGE_B64]", "sticker": ""},
        {"text": "", "sticker": "approved-sticker", "media_authorized": True},
    ]


def test_yaml_tts_uses_the_same_reviewed_canonical_text(monkeypatch) -> None:  # noqa: ANN001
    class _VoiceService:
        def __init__(self) -> None:
            self.texts: list[str] = []

        async def decide_tts_delivery(self, **_kwargs):  # noqa: ANN003, ANN202
            return SimpleNamespace(action="voice", style_hint="")

        async def send_tts(self, **kwargs):  # noqa: ANN003, ANN202
            self.texts.append(kwargs["text"])
            kwargs["on_delivery_started"]()
            kwargs["on_delivery_confirmed"]()
            return True

    async def _review(_messages, **_kwargs):  # noqa: ANN001
        return (
            '{"action":"accept","persona_verdict":"consistent","text":"","reason":"safe",'
            '"flags":[],"segments":["第一句","第二句"]}'
        )

    voice = _VoiceService()
    bot, _primary_prompts, _review_prompts, _stages = _run_yaml_turn(
        monkeypatch,
        history=[],
        event=_event(text="继续", message_id="human-current"),
        candidate="unused",
        review_call=_review,
        final_gate_enabled=True,
        parse_yaml_response=lambda _text: {
            "status": "",
            "think": "",
            "action": "",
            "messages": [
                {"text": "第一句", "sticker": ""},
                {"text": "第二句", "sticker": ""},
            ],
        },
        tts_service=voice,
    )

    assert voice.texts == ["第一句 第二句"]
    assert bot.sent == []
