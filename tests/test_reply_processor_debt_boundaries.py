from __future__ import annotations

import asyncio
import sqlite3
from types import SimpleNamespace

import pytest
from nonebot.exception import FinishedException

from ._loader import load_personification_module
from .test_reply_batch_yaml_handoff import (
    _run_normal_selected_referent_replay,
    agent_synthesis,
)

processor = load_personification_module("plugin.personification.handlers.reply_pipeline.processor")
trace = load_personification_module("plugin.personification.core.reply_turn_trace")
llm_context = load_personification_module("plugin.personification.core.llm_context")


async def _accept_review(_messages, **_kwargs):
    return '{"action":"accept","persona_verdict":"consistent","flags":[]}'


@pytest.mark.parametrize("receipt", ["confirmed", "unknown"])
def test_trace_database_failure_does_not_retry_or_upgrade_receipt(monkeypatch, receipt):
    def unavailable():
        raise sqlite3.OperationalError("isolated trace database unavailable")

    monkeypatch.setattr(trace, "connect_sync", unavailable)
    _images, state, _messages = _run_normal_selected_referent_replay(
        monkeypatch,
        yaml_mode=False,
        agent_result=agent_synthesis.AgentResult(text="这张图挺有意思。", pending_actions=[]),
        review_call=_accept_review,
        send_behavior=receipt,
    )
    assert len(state["_test_replay_sent"]) == 1
    assert state["reply_delivery_started"] is True
    assert bool(state.get("reply_delivery_confirmed")) is (receipt == "confirmed")
    assert bool(state.get("reply_delivery_complete")) is (receipt == "confirmed")
    if receipt == "unknown":
        assert state["delivery_unknown"] is True
    assert not any("API 调用失败" in item for item in state["_test_replay_logs"])


def test_internal_trace_programming_error_stops_before_send(monkeypatch):
    def broken_record(**kwargs):
        if kwargs["key"] == "dialogue_provenance":
            raise TypeError("broken internal trace stage")
    monkeypatch.setattr(trace, "record_stage", broken_record)
    with pytest.raises(TypeError, match="broken internal trace stage"):
        _run_normal_selected_referent_replay(monkeypatch, yaml_mode=False)


def test_post_send_trace_programming_error_is_visible_without_resend(monkeypatch):
    def broken_record(**kwargs):
        if kwargs["key"] == "post_send_bookkeeping":
            raise TypeError("broken post-send trace stage")
    monkeypatch.setattr(trace, "record_stage", broken_record)
    monkeypatch.setattr(trace, "finish_trace", lambda **_kwargs: None)
    _images, state, _messages = _run_normal_selected_referent_replay(
        monkeypatch, yaml_mode=False,
        agent_result=agent_synthesis.AgentResult(text="这张图挺有意思。", pending_actions=[]),
        review_call=_accept_review,
    )
    assert len(state["_test_replay_sent"]) == 1
    assert state["reply_delivery_confirmed"] is True
    assert state["reply_delivery_complete"] is True
    assert any("API 调用失败" in item and "TypeError" in item for item in state["_test_replay_logs"])


def _deps():
    logger = SimpleNamespace(debug=lambda *_a: None, error=lambda *_a: None)
    return processor.ReplyProcessorDeps(
        session=SimpleNamespace(), persona=SimpleNamespace(), types=SimpleNamespace(),
        runtime=SimpleNamespace(
            user_policy_gate=None, logger=logger,
            plugin_config=SimpleNamespace(personification_turn_trace_enabled=True),
        ),
    )


def _stub_trace(monkeypatch):
    monkeypatch.setattr(trace, "start_trace", lambda **_kwargs: "isolated-turn")
    monkeypatch.setattr(trace, "record_stage", lambda **_kwargs: None)
    monkeypatch.setattr(trace, "finish_trace", lambda **_kwargs: None)
    monkeypatch.setattr(trace, "get_trace", lambda _id: {"outcome": "ok"})


@pytest.mark.parametrize("failure", [asyncio.CancelledError, FinishedException, TypeError])
def test_lifecycle_exception_restores_context_and_releases_commit(monkeypatch, failure):
    _stub_trace(monkeypatch)
    async def fail(_bot, _event, state, _deps):
        await processor.acquire_reply_commit(state)
        raise failure("isolated failure")
    monkeypatch.setattr(processor, "_process_response_logic_impl", fail)
    async def run():
        outer_llm = llm_context.set_llm_context(user_id="outer")
        outer_trace = trace.set_current_trace_id("outer-trace")
        lock = asyncio.Lock()
        state = {"reply_commit_lock": lock}
        try:
            with pytest.raises(failure):
                await processor.process_response_logic(SimpleNamespace(), SimpleNamespace(user_id="inner", group_id="isolated", get_plaintext=lambda: ""), state, _deps())
            assert llm_context.current_llm_context()["user_id"] == "outer"
            assert trace.current_trace_id() == "outer-trace"
            assert not lock.locked()
            assert not state.get("_reply_commit_lock_acquired")
        finally:
            llm_context.reset_llm_context(outer_llm)
            trace.reset_current_trace_id(outer_trace)
    asyncio.run(run())


def test_trace_setup_programming_failure_restores_llm_context(monkeypatch):
    _stub_trace(monkeypatch)
    def broken_start(**_kwargs):
        raise TypeError("invalid trace setup")
    monkeypatch.setattr(trace, "start_trace", broken_start)
    calls = []
    async def impl(*_args):
        calls.append("entered")
    monkeypatch.setattr(processor, "_process_response_logic_impl", impl)
    async def run():
        token = llm_context.set_llm_context(user_id="outer")
        try:
            with pytest.raises(TypeError, match="invalid trace setup"):
                await processor.process_response_logic(SimpleNamespace(), SimpleNamespace(group_id="isolated", user_id="inner", get_plaintext=lambda: ""), {}, _deps())
            assert llm_context.current_llm_context()["user_id"] == "outer"
            assert calls == []
        finally:
            llm_context.reset_llm_context(token)
    asyncio.run(run())


def test_cleanup_failure_still_releases_lock_and_context(monkeypatch):
    _stub_trace(monkeypatch)
    async def impl(_bot, _event, state, _deps):
        await processor.acquire_reply_commit(state)
    monkeypatch.setattr(processor, "_process_response_logic_impl", impl)
    def broken_cleanup(_state):
        raise TypeError("invalid media cleanup")
    monkeypatch.setattr(processor, "cleanup_turn_media_lease", broken_cleanup)
    async def run():
        token = llm_context.set_llm_context(user_id="outer")
        lock = asyncio.Lock()
        try:
            with pytest.raises(TypeError, match="invalid media cleanup"):
                await processor.process_response_logic(SimpleNamespace(), SimpleNamespace(group_id="isolated", get_plaintext=lambda: ""), {"reply_commit_lock": lock}, _deps())
            assert not lock.locked()
            assert llm_context.current_llm_context()["user_id"] == "outer"
        finally:
            llm_context.reset_llm_context(token)
    asyncio.run(run())


@pytest.mark.parametrize("confirmed", [False, True])
def test_tts_finished_exception_does_not_fall_back_to_text(monkeypatch, confirmed):
    sends = []
    class TTS:
        async def decide_tts_delivery(self, **_kwargs):
            return SimpleNamespace(action="voice", style_hint="")
        async def send_tts(self, **kwargs):
            sends.append(kwargs["bot"])
            kwargs["on_delivery_started"]()
            if confirmed:
                kwargs["on_delivery_confirmed"]()
            raise FinishedException()

    original_runtime = processor.RuntimeDeps
    def runtime_with_tts(**kwargs):
        object.__setattr__(kwargs["plugin_config"], "personification_tts_enabled", True)
        kwargs["tts_service"] = TTS()
        return original_runtime(**kwargs)
    monkeypatch.setattr(processor, "RuntimeDeps", runtime_with_tts)
    monkeypatch.setattr(trace, "record_stage", lambda **_kwargs: None)
    monkeypatch.setattr(trace, "finish_trace", lambda **_kwargs: None)
    with pytest.raises(FinishedException):
        _run_normal_selected_referent_replay(
            monkeypatch, yaml_mode=False,
            agent_result=agent_synthesis.AgentResult(text="这张图挺有意思。", pending_actions=[]),
            review_call=_accept_review,
        )
    assert len(sends) == 1
    assert sends[0].sent == []  # No text replay after the voice control flow ends.


@pytest.mark.parametrize("failure", [FinishedException, asyncio.CancelledError])
def test_control_flow_owns_failure_when_trace_and_cleanup_break(monkeypatch, caplog, failure):
    _stub_trace(monkeypatch)
    def broken_finish(**_kwargs):
        raise TypeError("secret diagnostic text")
    def broken_cleanup(_state):
        raise TypeError("secret cleanup text")
    monkeypatch.setattr(trace, "finish_trace", broken_finish)
    monkeypatch.setattr(processor, "cleanup_turn_media_lease", broken_cleanup)
    expected = failure("owning control flow")
    async def impl(_bot, _event, state, _deps):
        await processor.acquire_reply_commit(state)
        raise expected
    monkeypatch.setattr(processor, "_process_response_logic_impl", impl)
    async def run():
        outer_llm = llm_context.set_llm_context(user_id="outer")
        outer_trace = trace.set_current_trace_id("outer-trace")
        lock = asyncio.Lock()
        try:
            with pytest.raises(failure) as caught:
                await processor.process_response_logic(SimpleNamespace(), SimpleNamespace(group_id="isolated", get_plaintext=lambda: ""), {"reply_commit_lock": lock}, _deps())
            assert caught.value is expected
            assert not lock.locked()
            assert llm_context.current_llm_context()["user_id"] == "outer"
            assert trace.current_trace_id() == "outer-trace"
        finally:
            llm_context.reset_llm_context(outer_llm)
            trace.reset_current_trace_id(outer_trace)
    asyncio.run(run())
    assert "error_type=TypeError" in caplog.text
    assert "secret" not in caplog.text


@pytest.mark.parametrize("confirmed,complete,outcome,code", [
    (True, True, "ok", "post_send_internal_exception"),
    (True, False, "partial", "partial_internal_exception"),
])
def test_outer_diagnostic_preserves_real_delivery_when_stage_keeps_failing(monkeypatch, caplog, confirmed, complete, outcome, code):
    _stub_trace(monkeypatch)
    finishes = []
    monkeypatch.setattr(trace, "finish_trace", lambda **kwargs: finishes.append(kwargs))
    expected = TypeError("owning original error")
    async def impl(_bot, _event, state, _deps):
        state.update(reply_delivery_started=True, reply_delivery_confirmed=confirmed, reply_delivery_complete=complete)
        def broken_stage(**_kwargs):
            raise ValueError("secret secondary error")
        monkeypatch.setattr(trace, "record_stage", broken_stage)
        raise expected
    monkeypatch.setattr(processor, "_process_response_logic_impl", impl)
    with pytest.raises(TypeError) as caught:
        asyncio.run(processor.process_response_logic(SimpleNamespace(), SimpleNamespace(group_id="isolated", get_plaintext=lambda: ""), {}, _deps()))
    assert caught.value is expected
    assert finishes[-1]["outcome"] == outcome
    assert finishes[-1]["diagnosis_code"] == code
    assert "error_type=ValueError" in caplog.text
    assert "secret" not in caplog.text
