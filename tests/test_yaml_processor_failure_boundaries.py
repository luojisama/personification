from __future__ import annotations

import asyncio
import sqlite3
from types import SimpleNamespace

import pytest
from nonebot.exception import FinishedException

from .test_yaml_dialogue_provenance_replay import (
    _event,
    _run_yaml_turn,
    reply_turn_trace,
    yaml_processor,
)


async def _accept_review(_messages, **_kwargs):
    return '{"action":"accept","persona_verdict":"consistent","flags":[]}'


def _run_with_real_trace(monkeypatch, *, configure_trace=None, **turn_kwargs):
    """Reuse the isolated outbound fixture, replacing its trace stubs at entry."""
    process = yaml_processor.process_yaml_response_logic
    record_stage = reply_turn_trace.record_stage
    finish_trace = reply_turn_trace.finish_trace
    captured = {"bot": None, "state": {}, "history": []}

    async def _process(bot, event, **kwargs):
        captured["bot"] = bot
        kwargs["reply_commit_state"] = captured["state"]
        kwargs["append_session_message"] = lambda *a, **k: captured["history"].append((a, k))
        monkeypatch.setattr(reply_turn_trace, "record_stage", record_stage)
        monkeypatch.setattr(reply_turn_trace, "finish_trace", finish_trace)
        if configure_trace is not None:
            configure_trace()
        token = reply_turn_trace.set_current_trace_id("yaml-isolated-failure-test")
        try:
            await process(bot, event, **kwargs)
        finally:
            reply_turn_trace.reset_current_trace_id(token)

    monkeypatch.setattr(yaml_processor, "process_yaml_response_logic", _process)

    def _run():
        return _run_yaml_turn(
            monkeypatch,
            history=[],
            event=_event(text="你好", message_id="yaml-failure-test"),
            candidate="你好呀",
            review_call=_accept_review,
            final_gate_enabled=False,
            **turn_kwargs,
        )

    return _run, captured


@pytest.mark.parametrize("error_type", [sqlite3.OperationalError, OSError])
def test_yaml_trace_storage_outage_preserves_single_confirmed_delivery(monkeypatch, error_type):
    def _unavailable():
        raise error_type("isolated trace store unavailable")

    monkeypatch.setattr(reply_turn_trace, "connect_sync", _unavailable)
    monkeypatch.setattr(reply_turn_trace, "publish_runtime_event", lambda *a, **k: None)
    run, captured = _run_with_real_trace(monkeypatch)
    run()

    assert captured["bot"].sent == ["你好呀"]
    assert captured["state"]["reply_delivery_confirmed"] is True
    assert captured["state"]["reply_delivery_complete"] is True
    assert len(captured["history"]) == 1
    assert captured["history"][0][0][2] == "你好呀"


@pytest.mark.parametrize("fault_stage, expected_sends", [("incoming_message", 0), ("delivery_complete", 1)])
def test_yaml_trace_programming_error_is_visible_without_replaying_send(
    monkeypatch, fault_stage, expected_sends
):
    def _configure():
        def _record(**kwargs):
            if kwargs["key"] == fault_stage:
                raise TypeError("trace programming defect")

        monkeypatch.setattr(reply_turn_trace, "record_stage", _record)
        monkeypatch.setattr(reply_turn_trace, "finish_trace", lambda **k: None)

    run, captured = _run_with_real_trace(monkeypatch, configure_trace=_configure)
    with pytest.raises(TypeError, match="trace programming defect"):
        run()

    assert len(captured["bot"].sent) == expected_sends
    if expected_sends:
        assert captured["state"]["reply_delivery_confirmed"] is True
        assert len(captured["history"]) == 1


def test_yaml_segment_programming_error_does_not_bypass_raw_command_guard(monkeypatch):
    def _broken_split(_text):
        raise TypeError("segment programming defect")

    monkeypatch.setattr(yaml_processor, "split_text_into_segments", _broken_split)
    run, captured = _run_with_real_trace(
        monkeypatch,
        configure_trace=lambda: monkeypatch.setattr(reply_turn_trace, "record_stage", lambda **k: None),
    )
    with pytest.raises(TypeError, match="segment programming defect"):
        run()
    assert captured["bot"].sent == []


def test_yaml_finish_programming_error_is_visible_after_confirmed_delivery(monkeypatch):
    def _configure():
        def _finish(**_kwargs):
            raise TypeError("finish programming defect")

        monkeypatch.setattr(reply_turn_trace, "record_stage", lambda **k: None)
        monkeypatch.setattr(reply_turn_trace, "finish_trace", _finish)

    run, captured = _run_with_real_trace(monkeypatch, configure_trace=_configure)
    with pytest.raises(TypeError, match="finish programming defect"):
        run()

    assert captured["bot"].sent == ["你好呀"]
    assert captured["state"]["reply_delivery_confirmed"] is True
    assert len(captured["history"]) == 1


def test_yaml_addressing_programming_error_does_not_send_unaddressed_reply(monkeypatch):
    def _broken_addressing(**_kwargs):
        raise TypeError("addressing programming defect")

    monkeypatch.setattr(yaml_processor._humanize, "prepend_addressing_segments", _broken_addressing)
    run, captured = _run_with_real_trace(
        monkeypatch,
        configure_trace=lambda: monkeypatch.setattr(reply_turn_trace, "record_stage", lambda **k: None),
    )
    with pytest.raises(TypeError, match="addressing programming defect"):
        run()
    assert captured["bot"].sent == []


@pytest.mark.parametrize("control_exception", [FinishedException, asyncio.CancelledError])
@pytest.mark.parametrize("boundary", ["decision", "send", "confirmed_send"])
def test_yaml_tts_control_flow_does_not_fall_back_to_text(monkeypatch, control_exception, boundary):
    class _VoiceService:
        async def decide_tts_delivery(self, **_kwargs):
            if boundary == "decision":
                raise control_exception()
            return SimpleNamespace(action="voice", style_hint="")

        async def send_tts(self, **kwargs):
            kwargs["on_delivery_started"]()
            if boundary == "confirmed_send":
                kwargs["on_delivery_confirmed"]()
            raise control_exception()

    def _configure():
        monkeypatch.setattr(reply_turn_trace, "record_stage", lambda **k: None)
        monkeypatch.setattr(reply_turn_trace, "finish_trace", lambda **k: None)

    run, captured = _run_with_real_trace(
        monkeypatch, configure_trace=_configure, tts_service=_VoiceService()
    )
    with pytest.raises(control_exception):
        run()

    assert captured["bot"].sent == []
    assert captured["history"] == []
    assert bool(captured["state"].get("reply_delivery_confirmed")) is (boundary == "confirmed_send")
