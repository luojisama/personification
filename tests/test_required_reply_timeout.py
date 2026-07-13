from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from ._loader import load_personification_module


processor = load_personification_module("plugin.personification.handlers.reply_pipeline.processor")
reply_buffer = load_personification_module("plugin.personification.handlers.reply_buffer")
reply_turn_trace = load_personification_module("plugin.personification.core.reply_turn_trace")


def test_cancelled_reply_processor_does_not_finish_as_no_reply(monkeypatch) -> None:  # noqa: ANN001
    finished: list[dict[str, object]] = []

    async def _cancelled_impl(*_args, **_kwargs):  # noqa: ANN001
        raise asyncio.CancelledError

    monkeypatch.setattr(processor, "_process_response_logic_impl", _cancelled_impl)
    monkeypatch.setattr(reply_turn_trace, "current_trace_id", lambda: "")
    monkeypatch.setattr(reply_turn_trace, "start_trace", lambda **_kwargs: "trace-required")
    monkeypatch.setattr(reply_turn_trace, "set_current_trace_id", lambda _trace_id: object())
    monkeypatch.setattr(reply_turn_trace, "reset_current_trace_id", lambda _token: None)
    monkeypatch.setattr(reply_turn_trace, "record_stage", lambda **_kwargs: None)
    monkeypatch.setattr(reply_turn_trace, "get_trace", lambda _trace_id: {})
    monkeypatch.setattr(reply_turn_trace, "finish_trace", lambda **kwargs: finished.append(kwargs))

    state: dict[str, object] = {}
    deps = SimpleNamespace(runtime=SimpleNamespace(plugin_config=SimpleNamespace(personification_turn_trace_enabled=True)))
    event = SimpleNamespace(user_id=1, message_id=2)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(processor.process_response_logic(SimpleNamespace(), event, state, deps))

    assert state["reply_trace_id"] == "trace-required"
    assert finished == []


def test_required_image_timeout_sends_fallback_and_finishes_degraded(monkeypatch) -> None:  # noqa: ANN001
    stages: list[dict[str, object]] = []
    finished: list[dict[str, object]] = []
    monkeypatch.setattr(reply_turn_trace, "record_stage", lambda **kwargs: stages.append(kwargs))
    monkeypatch.setattr(reply_turn_trace, "finish_trace", lambda **kwargs: finished.append(kwargs))

    class _Bot:
        def __init__(self) -> None:
            self.sent: list[str] = []

        async def send(self, _event, message):  # noqa: ANN001
            self.sent.append(str(message))

    event = SimpleNamespace(
        message=[SimpleNamespace(type="text", data={"text": "你翻译一下"}), SimpleNamespace(type="image", data={})]
    )
    bot = _Bot()

    asyncio.run(
        reply_buffer._handle_reply_timeout(
            bot=bot,
            event=event,
            state={"reply_required": True, "reply_trace_id": "trace-required"},
            session_key="bot:private_1",
            timeout_seconds=180.0,
            logger=SimpleNamespace(warning=lambda *_args, **_kwargs: None),
            commit_lock=asyncio.Lock(),
        )
    )

    assert bot.sent == ["这张图我刚刚没读出来，重发一下试试。"]
    assert stages[-1]["trace_id"] == "trace-required"
    assert stages[-1]["key"] == "reply_timeout"
    assert "fallback_sent=true" in str(stages[-1]["detail"])
    assert finished[-1]["outcome"] == "degraded"
    assert finished[-1]["diagnosis_code"] == "reply_timeout"
