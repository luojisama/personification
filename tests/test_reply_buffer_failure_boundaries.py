from __future__ import annotations

import asyncio
import logging
import sqlite3
import time

import pytest

from ._loader import load_personification_module
from .test_reply_buffer import _Bot, _GroupEvent, _Logger, _Message, _MessageSegment, _PrivateEvent

buffer = load_personification_module("plugin.personification.handlers.reply_buffer")
fence = load_personification_module("plugin.personification.core.generation_fence")
recovery = load_personification_module("plugin.personification.core.reply_recovery_queue")


@pytest.mark.parametrize("state", [
    {"reply_delivery_unknown": True}, {"delivery_unknown": True},
    {"reply_delivery_started": True}, {"reply_delivery_confirmed": True},
    {"reply_delivery_complete": True}, {"_external_action_started": True},
    {"_generation_invalidated": True},
])
def test_random_preemption_uses_complete_generation_barrier(state):
    entry = {"processing": True, "current_is_random_chat": True, "active_state": state}
    assert not buffer._should_preempt_current_batch(entry, immediate_flush=True)
    buffer._invalidate_random_preemption(entry)
    assert "generation_cancel_reason" not in state


def test_fence_programming_error_does_not_enable_fallback(monkeypatch):
    def invalid(_state):
        raise RuntimeError("broken fence")

    monkeypatch.setattr(buffer, "safe_to_supersede", invalid)
    entry = {"processing": True, "current_is_random_chat": True, "active_state": {}}
    with pytest.raises(RuntimeError):
        buffer._should_preempt_current_batch(entry, immediate_flush=True)
    with pytest.raises(RuntimeError):
        buffer._invalidate_random_preemption(entry)
    assert not entry["active_state"]


def test_detached_failure_is_diagnosed_and_task_is_released(caplog):
    async def run():
        async def failed():
            raise RuntimeError("private-message token=private-token")

        task = asyncio.create_task(failed())
        await asyncio.gather(task, return_exceptions=True)
        buffer._detached_generation_tasks.add(task)
        buffer._consume_detached_generation(task)
        assert task not in buffer._detached_generation_tasks

    with caplog.at_level(logging.WARNING, logger=buffer.__name__):
        asyncio.run(run())
    assert "error_type=RuntimeError" in caplog.text
    assert "private-" not in caplog.text


def test_cancelled_owner_releases_admission_and_late_child_cannot_send():
    async def run():
        controller = buffer.ReplyConcurrencyController(session_limit=1, global_limit=1)
        entered, cancelled, finish_child = asyncio.Event(), asyncio.Event(), asyncio.Event()
        sent = []
        state = {}

        async def provider(_bot, _event, _state):
            entered.set()
            try:
                await finish_child.wait()
            except asyncio.CancelledError:
                cancelled.set()
                await finish_child.wait()
            fence.mark_external_action_started(_state)
            sent.append("reply")

        async def owner():
            async with controller.direct_turn("private-session"):
                await buffer._run_generation_call(provider, None, None, state, timeout=10)

        task = asyncio.create_task(owner())
        await asyncio.wait_for(entered.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.wait_for(cancelled.wait(), 1)
        assert state["_generation_invalidated"] is True
        assert controller._session_gates == {}
        assert controller._global_semaphore._value == 1
        late_tasks = list(buffer._detached_generation_tasks)
        assert late_tasks
        finish_child.set()
        await asyncio.gather(*late_tasks, return_exceptions=True)
        await asyncio.sleep(0)
        assert not sent
        assert not buffer._detached_generation_tasks

    asyncio.run(run())


@pytest.mark.parametrize("error_type", [sqlite3.OperationalError, PermissionError])
def test_recovery_storage_failure_preserves_unknown_and_releases_buffer(monkeypatch, caplog, error_type):
    def unavailable(*args, **kwargs):
        raise error_type("private-path private-message")

    monkeypatch.setattr(recovery, "ReplyRecoveryQueue", unavailable)
    monkeypatch.setattr(buffer, "_RECOVERY_FAILURE_LAST_LOG", None)

    async def run():
        event = _GroupEvent(1, "private-message")
        entry = buffer._new_entry(0)
        entry["items"] = [{"event": event, "state": {}, "received_at": time.monotonic(), "dedupe_key": "id:1"}]
        msg_buffer = {"session": entry}
        controller = buffer.ReplyConcurrencyController(session_limit=1, global_limit=1)
        calls, states = [], []

        async def process(_bot, _event, state):
            calls.append(1)
            state["reply_delivery_started"] = True
            state["reply_delivery_unknown"] = True
            states.append(state)
            raise RuntimeError("send outcome unavailable")

        await buffer.run_buffer_timer(
            "session", _Bot(), msg_buffer=msg_buffer, process_response_logic=process,
            message_event_cls=_GroupEvent, message_cls=_Message,
            message_segment_cls=_MessageSegment, logger=_Logger(), delay=0,
            concurrency_controller=controller,
        )
        assert calls == [1]
        assert states[0]["reply_delivery_unknown"] is True
        assert not msg_buffer
        assert not entry["processing"]
        assert entry["active_task"] is None
        assert controller._session_gates == {}
        assert controller._global_semaphore._value == 1

    with caplog.at_level(logging.WARNING, logger=buffer.__name__):
        asyncio.run(run())
    assert f"error_type={error_type.__name__}" in caplog.text
    assert "private-" not in caplog.text


def test_recovery_programming_error_is_visible(monkeypatch):
    def broken(*args, **kwargs):
        raise ValueError("invalid internal recovery data")

    monkeypatch.setattr(recovery, "ReplyRecoveryQueue", broken)
    with pytest.raises(ValueError):
        buffer._record_recovery_failure(
            bot=_Bot(), event=_GroupEvent(1, "message"), state={},
            failure_stage="unit", failure_class="delivery_unknown",
        )


def test_direct_recovery_programming_error_releases_owner_without_replay(monkeypatch):
    def broken(*args, **kwargs):
        raise ValueError("invalid internal recovery data")

    monkeypatch.setattr(recovery, "ReplyRecoveryQueue", broken)

    async def run():
        controller = buffer.ReplyConcurrencyController(session_limit=1, global_limit=1)
        msg_buffer, calls, states = {}, [], []

        async def process(_bot, _event, state):
            calls.append(1)
            states.append(state)
            state["reply_delivery_started"] = True
            state["reply_delivery_unknown"] = True
            raise RuntimeError("send outcome unavailable")

        with pytest.raises(ValueError):
            await buffer.handle_reply_event(
                _Bot(), _PrivateEvent(1, "message"), {},
                poke_event_cls=type("Poke", (), {}), message_event_cls=_PrivateEvent,
                group_message_event_cls=_GroupEvent, process_response_logic=process,
                msg_buffer=msg_buffer, start_buffer_timer=lambda *args: None,
                logger=_Logger(), concurrency_controller=controller,
                batch_base_wait_seconds=.001, batch_min_wait_seconds=.001,
                batch_max_wait_seconds=.005, supplement_settings={"enabled": True},
            )
        assert calls == [1]
        assert states[0]["reply_delivery_unknown"] is True
        assert not msg_buffer
        assert controller._session_gates == {}
        assert controller._global_semaphore._value == 1

    asyncio.run(run())


@pytest.mark.parametrize("pending", [False, True])
def test_buffer_owner_cancellation_remains_cancelled_and_does_not_replay(pending):
    async def run():
        controller = buffer.ReplyConcurrencyController(session_limit=1, global_limit=1)
        entered = asyncio.Event()
        entry = buffer._new_entry(0)
        first = {"event": _GroupEvent(1, "first"), "state": {}, "received_at": time.monotonic(), "dedupe_key": "id:1"}
        followup = {"event": _GroupEvent(2, "next"), "state": {}, "received_at": time.monotonic(), "dedupe_key": "id:2"}
        entry["items"] = [first]
        msg_buffer, calls = {"session": entry}, []

        async def process(_bot, _event, _state):
            calls.append(1)
            entered.set()
            await asyncio.Event().wait()

        task = asyncio.create_task(buffer.run_buffer_timer(
            "session", _Bot(), msg_buffer=msg_buffer, process_response_logic=process,
            message_event_cls=_GroupEvent, message_cls=_Message,
            message_segment_cls=_MessageSegment, logger=_Logger(), delay=0,
            concurrency_controller=controller,
        ))
        await asyncio.wait_for(entered.wait(), 1)
        if pending:
            entry["pending_items"] = [followup]
            entry["pending_ready"] = True
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0)
        assert task.cancelled()
        assert calls == [1]
        assert not entry["processing"]
        assert entry["active_task"] is None
        assert entry["active_items"] == []
        assert controller._session_gates == {}
        assert controller._global_semaphore._value == 1
        if pending:
            assert entry["pending_items"] == [followup]
            assert entry["timer_task"] is None

    asyncio.run(run())


@pytest.mark.parametrize("failure_site", ["serialize", "controller_begin", "deadline"])
@pytest.mark.parametrize("pending", [False, True])
def test_buffer_setup_error_releases_owner_and_preserves_followups(monkeypatch, failure_site, pending):
    def broken(*args, **kwargs):
        raise TypeError("invalid internal setup")

    if failure_site == "serialize":
        monkeypatch.setattr(buffer, "_serialize_batched_event", broken)
    elif failure_site == "controller_begin":
        monkeypatch.setattr(buffer.SupplementController, "begin", broken)
    else:
        monkeypatch.setattr(buffer, "attach_turn_deadline", broken)

    async def run():
        controller = buffer.ReplyConcurrencyController(session_limit=1, global_limit=1)
        entry = buffer._new_entry(0)
        entry["items"] = [{"event": _GroupEvent(1, "first"), "state": {}, "received_at": time.monotonic(), "dedupe_key": "id:1"}]
        followup = {"event": _GroupEvent(2, "next"), "state": {}, "received_at": time.monotonic(), "dedupe_key": "id:2"}
        if pending:
            entry["pending_items"] = [followup]
        msg_buffer, calls = {"session": entry}, []

        async def process(*args):
            calls.append(1)

        with pytest.raises(TypeError):
            await buffer.run_buffer_timer(
                "session", _Bot(), msg_buffer=msg_buffer, process_response_logic=process,
                message_event_cls=_GroupEvent, message_cls=_Message,
                message_segment_cls=_MessageSegment, logger=_Logger(), delay=0,
                concurrency_controller=controller,
            )
        assert not calls
        assert not entry["processing"]
        assert entry["active_task"] is None
        assert not entry["active_items"]
        assert controller._session_gates == {}
        assert controller._global_semaphore._value == 1
        if pending:
            assert entry["items"] == [followup]
            assert entry["queued_items"] == [followup]
            assert entry["timer_task"] is None
        else:
            assert not msg_buffer

    asyncio.run(run())
