from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

import pytest

from ._loader import load_personification_module


reply_buffer = load_personification_module("plugin.personification.handlers.reply_buffer")
pipeline_context = load_personification_module(
    "plugin.personification.handlers.reply_pipeline.pipeline_context"
)
reply_recovery_queue = load_personification_module(
    "plugin.personification.core.reply_recovery_queue"
)
yaml_processor = load_personification_module(
    "plugin.personification.handlers.yaml_pipeline.processor"
)


def test_batch_trigger_does_not_let_human_reply_semantics_steal_latest_event() -> None:
    human_reply = object()
    latest = object()
    selected, trigger_type = reply_buffer._select_batch_trigger(
        [
            {
                "event": human_reply,
                "is_direct_mention": False,
                "is_reply_to_bot": False,
                "state": {"message_target": "TARGET_OTHERS"},
            },
            {
                "event": latest,
                "is_direct_mention": False,
                "is_reply_to_bot": False,
                "state": {"message_target": "TARGET_UNCLEAR"},
            },
        ]
    )
    assert selected["event"] is latest
    assert trigger_type == "latest"


def test_batch_trigger_prioritizes_direct_reply_and_high_confidence_followup() -> None:
    inferred = {
        "event": object(),
        "is_direct_mention": False,
        "is_reply_to_bot": False,
        "state": {"message_target": "TARGET_BOT", "message_target_confidence": 0.91},
    }
    explicit_reply = {
        "event": object(),
        "is_direct_mention": False,
        "is_reply_to_bot": True,
        "state": {},
    }
    direct_mention = {
        "event": object(),
        "is_direct_mention": True,
        "is_reply_to_bot": False,
        "state": {},
    }
    selected, trigger_type = reply_buffer._select_batch_trigger(
        [inferred, explicit_reply, direct_mention]
    )
    assert selected is direct_mention
    assert trigger_type == "direct_mention"

    selected, trigger_type = reply_buffer._select_batch_trigger([inferred, {"event": object(), "state": {}}])
    assert selected is inferred
    assert trigger_type == "high_confidence_target_bot"


@dataclass
class _TextSeg:
    data: dict[str, str]
    type: str = "text"


@dataclass
class _AtSeg:
    data: dict[str, str]
    type: str = "at"


@dataclass
class _FileSeg:
    data: dict[str, str]
    type: str = "file"


@dataclass
class _ImageSeg:
    data: dict[str, str]
    type: str = "image"


@dataclass
class _JsonSeg:
    data: dict[str, str]
    type: str = "json"


class _Message(list):
    pass


class _MessageSegment:
    @staticmethod
    def text(value: str) -> _TextSeg:
        return _TextSeg({"text": value})


class _Sender:
    card = ""
    nickname = "tester"


class _PrivateEvent:
    def __init__(self, message_id: int, text: str) -> None:
        self.message_id = message_id
        self.user_id = 123
        self.sender = _Sender()
        self.message = _Message([_TextSeg({"text": text})])


class _GroupEvent(_PrivateEvent):
    group_id = 456


class _MentionEvent(_GroupEvent):
    def __init__(self, message_id: int, text: str) -> None:
        super().__init__(message_id, text)
        self.message.insert(0, _AtSeg({"qq": "999"}))


class _Bot:
    self_id = "999"


class _Logger:
    def __init__(self) -> None:
        self.errors: list[str] = []

    def debug(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def info(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def warning(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def exception(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def error(self, message: str, *_args: Any, **_kwargs: Any) -> None:
        self.errors.append(str(message))


def test_stable_item_key_is_deduplicable_and_never_contains_body() -> None:
    first = _GroupEvent(9, "绝不能泄漏到状态正文")
    assert reply_buffer._stable_item_key("group", first, 1.0) == "id:9"
    first.message_id = ""
    first.time = 123.0
    same = reply_buffer._stable_item_key("group", first, 1.0)
    assert same == reply_buffer._stable_item_key("group", first, 1.0)
    assert "正文" not in same
    other_user = _GroupEvent(0, "绝不能泄漏到状态正文")
    other_user.user_id = 999
    assert same != reply_buffer._stable_item_key("group", other_user, 1.0)
    assert same == reply_buffer._stable_item_key("group", first, 2.0)
    first.time = 124.0
    assert same != reply_buffer._stable_item_key("group", first, 2.0)
    long = _GroupEvent(0, "x" * 120 + "甲"); long.time = 1
    other = _GroupEvent(0, "x" * 120 + "乙"); other.time = 1
    assert reply_buffer._stable_item_key("g", long, 0) != reply_buffer._stable_item_key("g", other, 0)
    first_media = _GroupEvent(0, ""); first_media.time = 1; first_media.message = _Message([_FileSeg({"file": "a", "name": "a.png"})])
    same_media = _GroupEvent(0, ""); same_media.time = 1; same_media.message = _Message([_FileSeg({"name": "a.png", "file": "a"})])
    other_media = _GroupEvent(0, ""); other_media.time = 1; other_media.message = _Message([_FileSeg({"file": "b", "name": "b.png"})])
    assert reply_buffer._stable_item_key("g", first_media, 0) == reply_buffer._stable_item_key("g", same_media, 0)
    assert reply_buffer._stable_item_key("g", first_media, 0) != reply_buffer._stable_item_key("g", other_media, 0)


def test_active_random_preemption_requires_no_delivery_but_must_reply_never_preempts() -> None:
    entry = {"processing": True, "current_is_random_chat": True, "active_state": {}}
    assert reply_buffer._should_preempt_current_batch(entry, immediate_flush=True)
    for field in ("reply_delivery_started", "reply_delivery_confirmed", "reply_delivery_complete", "delivery_unknown"):
        entry["active_state"] = {field: True}
        assert not reply_buffer._should_preempt_current_batch(entry, immediate_flush=True)
    entry["current_is_random_chat"] = False
    entry["active_state"] = {}
    assert not reply_buffer._should_preempt_current_batch(entry, immediate_flush=True)


def test_fifo_chunking_runs_two_real_timer_generations_without_loss() -> None:
    async def run() -> None:
        key, bot, seen = "456:123", _Bot(), []
        entry = reply_buffer._new_entry(0.0)
        base = time.monotonic()
        entry["items"] = [{"event": _GroupEvent(100 + index, str(index)), "state": {}, "received_at": base + index * .0001, "dedupe_key": f"id:{100 + index}"} for index in range(10)]
        buffer = {key: entry}
        async def process(_bot: Any, _event: Any, state: dict[str, Any]) -> None:
            seen.append([int(row["message_id"]) - 100 for row in state["batched_events"]])
        common = dict(msg_buffer=buffer, process_response_logic=process, message_event_cls=_GroupEvent, message_cls=_Message, message_segment_cls=_MessageSegment, logger=_Logger(), delay=0, response_timeout_seconds=30)
        await reply_buffer.run_buffer_timer(key, bot, **common)
        await asyncio.sleep(0.05)
        assert seen == [list(range(8)), [8, 9]]
    asyncio.run(run())


def test_gate_wait_reorder_keeps_overflow_fifo_without_duplicates() -> None:
    async def run() -> None:
        key, bot, seen, tasks = "999:456", _Bot(), [], []
        entry = reply_buffer._new_entry(time.monotonic())
        base = time.monotonic()
        entry["items"] = [
            {
                "event": _GroupEvent(index, str(index)),
                "state": {"is_random_chat": True},
                "received_at": base + index * 0.0001,
                "dedupe_key": f"id:{index}",
            }
            for index in range(1, 11)
        ]
        buffer = {key: entry}
        entered, release = asyncio.Event(), asyncio.Event()

        class Gate:
            calls = 0

            async def allows_current(self, _event: Any) -> bool:
                self.calls += 1
                if self.calls == 1:
                    entered.set()
                    await release.wait()
                return True

        gate = Gate()

        async def process(_bot: Any, _event: Any, state: dict[str, Any]) -> None:
            seen.append([int(row["message_id"]) for row in state["batched_events"]])

        common = {
            "msg_buffer": buffer,
            "process_response_logic": process,
            "message_event_cls": _GroupEvent,
            "message_cls": _Message,
            "message_segment_cls": _MessageSegment,
            "logger": _Logger(),
            "response_timeout_seconds": 30,
        }
        old = asyncio.create_task(
            reply_buffer.run_buffer_timer(
                key,
                bot,
                user_policy_gate=gate,
                **common,
                delay=0,
            )
        )
        entry["timer_task"] = old
        await entered.wait()

        def start_timer(timer_key: str, timer_bot: Any, wait: float):
            task = asyncio.create_task(
                reply_buffer.run_buffer_timer(timer_key, timer_bot, **common, delay=wait)
            )
            tasks.append(task)
            return task

        await reply_buffer.handle_reply_event(
            bot,
            _GroupEvent(11, "11"),
            {"is_random_chat": True},
            poke_event_cls=type("P", (), {}),
            message_event_cls=_PrivateEvent,
            group_message_event_cls=_GroupEvent,
            process_response_logic=process,
            msg_buffer=buffer,
            start_buffer_timer=start_timer,
            logger=_Logger(),
            batch_base_wait_seconds=0,
            batch_min_wait_seconds=0,
            batch_max_wait_seconds=0,
        )
        release.set()
        await asyncio.gather(old, *tasks, return_exceptions=True)
        await asyncio.sleep(0.05)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        assert seen == [list(range(1, 9)), [9, 10, 11]]
        assert sorted(value for batch in seen for value in batch) == list(range(1, 12))
        assert key not in buffer

    asyncio.run(run())


def test_rejected_first_chunk_recovers_eight_and_keeps_overflow_generation(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        key, base, seen, recovered = "456:123", time.monotonic(), [], []
        entry = reply_buffer._new_entry(0.0)
        entry["items"] = [{"event": _GroupEvent(100 + index, str(index)), "state": {}, "received_at": base + index * .0001, "dedupe_key": f"id:{100 + index}"} for index in range(10)]
        buffer = {key: entry}
        class Gate:
            calls: list[int] = []
            async def allows_current(self, event: Any) -> bool:
                self.calls.append(int(event.message_id)); return int(event.message_id) >= 108
        gate = Gate()
        monkeypatch.setattr(reply_buffer, "_record_recovery_failure", lambda **kw: recovered.append(int(kw["event"].message_id)) or 1)
        async def process(_bot: Any, _event: Any, state: dict[str, Any]) -> None:
            seen.append([int(row["message_id"]) for row in state["batched_events"]])
        common = dict(msg_buffer=buffer, process_response_logic=process, message_event_cls=_GroupEvent, message_cls=_Message, message_segment_cls=_MessageSegment, logger=_Logger(), delay=0, response_timeout_seconds=30, user_policy_gate=gate)
        await reply_buffer.run_buffer_timer(key, _Bot(), **common)
        await asyncio.sleep(.05)
        assert seen == [[108, 109]]
        assert recovered == list(range(100, 108))
        assert gate.calls == list(range(100, 110))
    asyncio.run(run())


def test_timing_resolver_is_called_only_when_new_message_is_enqueued() -> None:
    async def run() -> None:
        calls: list[int] = []
        class Timing:
            base_wait_seconds = 0.01
            min_wait_seconds = 0.01
            max_wait_seconds = 0.02
            legacy_reply_backoff_seconds = None
        def resolver() -> Timing:
            calls.append(1)
            return Timing()
        await reply_buffer.handle_reply_event(_Bot(), _GroupEvent(77, "x"), {"is_random_chat": True}, poke_event_cls=type("P", (), {}), message_event_cls=_PrivateEvent, group_message_event_cls=_GroupEvent, process_response_logic=lambda *_: None, msg_buffer={}, start_buffer_timer=lambda *_: None, logger=_Logger(), timing_resolver=resolver)
        assert calls == [1]
    asyncio.run(run())


def test_timing_resolver_replaces_deadline_only_after_a_new_message() -> None:
    async def run() -> None:
        class Timing:
            def __init__(self, base: float) -> None:
                self.base_wait_seconds = base
                self.min_wait_seconds = base
                self.max_wait_seconds = base
                self.legacy_reply_backoff_seconds = None

        timings = iter([Timing(0.5), Timing(0.01)])
        calls: list[int] = []
        scheduled: list[float] = []

        def resolver() -> Timing:
            calls.append(1)
            return next(timings)

        def start(_key: str, _bot: Any, delay: float) -> None:
            scheduled.append(delay)

        common = dict(
            poke_event_cls=type("P", (), {}), message_event_cls=_PrivateEvent,
            group_message_event_cls=_GroupEvent, process_response_logic=lambda *_: None,
            msg_buffer={}, start_buffer_timer=start, logger=_Logger(),
            timing_resolver=resolver,
        )
        await reply_buffer.handle_reply_event(_Bot(), _GroupEvent(71, "first"), {"is_random_chat": True}, **common)
        await reply_buffer.handle_reply_event(_Bot(), _GroupEvent(72, "new message"), {"is_random_chat": True}, **common)
        assert calls == [1, 1]
        assert scheduled[0] >= 0.45
        assert scheduled[-1] <= 0.02

    asyncio.run(run())


def test_recovery_projection_stores_each_inbound_message_without_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded: list[dict[str, Any]] = []

    class _Queue:
        def record_failure(self, **kwargs: Any) -> None:
            recorded.append(dict(kwargs))

    monkeypatch.setattr(reply_recovery_queue, "ReplyRecoveryQueue", _Queue)
    state = {
        "reply_trace_id": "trace-safe",
        "provider_route_fingerprint": "route-safe",
        "batched_events": [
            {
                "message_id": "11",
                "user_id": "123",
                "group_id": "456",
                "text": "第一条",
                "media": [],
            },
            {
                "message_id": "12",
                "user_id": "124",
                "group_id": "456",
                "text": "第二条",
                "media": [],
            },
        ],
        "generated_reply": "绝不能进入恢复队列",
    }

    count = reply_buffer._record_recovery_failure(
        bot=_Bot(),
        event=_GroupEvent(12, "第二条"),
        state=state,
        failure_stage="reply_timeout",
        failure_class="generation_failed_before_send",
    )

    assert count == 2
    assert [item["original_message_id"] for item in recorded] == ["11", "12"]
    assert all(item["conversation_kind"] == "group" for item in recorded)
    assert all(item["conversation_id"] == "456" for item in recorded)
    assert all(item["route_fingerprint"] == "route-safe" for item in recorded)
    assert all(item["trace_id"] == "trace-safe" for item in recorded)
    assert all("generated_reply" not in item for item in recorded)


def test_buffer_snapshot_counts_active_once_and_is_data_free() -> None:
    now = time.monotonic()
    snapshot = reply_buffer.buffer_runtime_snapshot({
        "private-do-not-leak": {
            "items": [{"text": "正文不能泄漏"}],
            "pending_items": [{"text": "下一批正文"}],
            "queued_items": [{"text": "镜像不能重复"}],
            "active_items": [{"text": "活动正文"}],
            "processing": True,
            "processing_started_at": now - 0.02,
            "next_fire_at": now + 1,
        }
    })
    assert snapshot["buffered_sessions"] == 1
    assert snapshot["buffered_messages"] == 3
    assert snapshot["processing_buffer_sessions"] == 1
    assert snapshot["oldest_buffer_age_ms"] >= 1
    assert snapshot["next_buffer_fire_ms"] >= 0
    assert "正文" not in repr(snapshot)


def test_buffer_snapshot_ignores_processing_stale_fire_for_future_waiter() -> None:
    now = time.monotonic()
    snapshot = reply_buffer.buffer_runtime_snapshot({
        "active": {"processing": True, "active_items": [{"dedupe_key": "a", "received_at": now - 1}], "next_fire_at": now - 10},
        "waiting": {"items": [{"dedupe_key": "b", "received_at": now}], "next_fire_at": now + .2},
    })
    assert 0 < snapshot["next_buffer_fire_ms"] <= 250


def test_policy_revocation_records_complete_inbound_batch(monkeypatch: pytest.MonkeyPatch) -> None:
    recorded: list[dict[str, str]] = []

    def record(**kwargs: Any) -> int:
        recorded.append({"stage": kwargs["failure_stage"], "kind": kwargs["failure_class"]})
        return 1

    monkeypatch.setattr(reply_buffer, "_record_recovery_failure", record)

    class Gate:
        async def allows_current(self, _event: Any) -> bool:
            return False

    async def run() -> None:
        await reply_buffer.handle_reply_event(
            _Bot(), _GroupEvent(51, "被撤销"), {"batched_events": [{"message_id": "51", "text": "被撤销"}]},
            poke_event_cls=type("P", (), {}), message_event_cls=_PrivateEvent,
            group_message_event_cls=_GroupEvent, process_response_logic=lambda *_: None,
            msg_buffer={}, start_buffer_timer=lambda *_: None, logger=_Logger(), user_policy_gate=Gate(),
        )

    asyncio.run(run())
    assert recorded == [{"stage": "permission_revoked", "kind": "delivery_unknown"}]


def test_policy_gate_exception_fails_closed_and_recovers(monkeypatch: pytest.MonkeyPatch) -> None:
    recorded: list[dict[str, str]] = []
    monkeypatch.setattr(reply_buffer, "_record_recovery_failure", lambda **kw: recorded.append({"stage": kw["failure_stage"], "kind": kw["failure_class"]}) or 1)
    class Gate:
        async def allows_current(self, _event: Any) -> bool:
            raise RuntimeError("gate unavailable")
    async def run() -> None:
        await reply_buffer.handle_reply_event(
            _Bot(), _GroupEvent(53, "gate error"), {"batched_events": [{"message_id": "53", "text": "gate error"}]},
            poke_event_cls=type("P", (), {}), message_event_cls=_PrivateEvent,
            group_message_event_cls=_GroupEvent, process_response_logic=lambda *_: None,
            msg_buffer={}, start_buffer_timer=lambda *_: None, logger=_Logger(), user_policy_gate=Gate(),
        )
    asyncio.run(run())
    assert recorded == [{"stage": "permission_revoked", "kind": "delivery_unknown"}]


def test_enqueue_policy_rejection_recovers_media_only_event(monkeypatch: pytest.MonkeyPatch) -> None:
    recorded: list[dict[str, Any]] = []

    class Queue:
        def record_failure(self, **kwargs: Any) -> None:
            recorded.append(kwargs)

    class Gate:
        async def allows_current(self, _event: Any) -> bool:
            return False

    monkeypatch.setattr(reply_recovery_queue, "ReplyRecoveryQueue", Queue)
    event = _GroupEvent(701, "")
    event.message = _Message([_ImageSeg({"file": "opaque-image-token"})])
    processed: list[int] = []

    async def process(_bot: Any, current: Any, _state: dict[str, Any]) -> None:
        processed.append(int(current.message_id))

    async def run() -> None:
        await reply_buffer.handle_reply_event(
            _Bot(),
            event,
            {},
            poke_event_cls=type("PokeEvent", (), {}),
            message_event_cls=_PrivateEvent,
            group_message_event_cls=_GroupEvent,
            process_response_logic=process,
            msg_buffer={},
            start_buffer_timer=lambda *_args: None,
            logger=_Logger(),
            user_policy_gate=Gate(),
        )

    asyncio.run(run())
    assert processed == []
    assert len(recorded) == 1
    assert recorded[0]["original_message_id"] == "701"
    assert recorded[0]["failure_stage"] == "permission_revoked"
    assert recorded[0]["failure_class"] == "delivery_unknown"
    assert recorded[0]["media_refs"] and recorded[0]["media_refs"][0]["kind"] == "image"


def test_timer_policy_rejection_recovers_enqueued_media_only_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded: list[dict[str, Any]] = []

    class Queue:
        def record_failure(self, **kwargs: Any) -> None:
            recorded.append(kwargs)

    class Gate:
        allowed = True

        async def allows_current(self, _event: Any) -> bool:
            return self.allowed

    monkeypatch.setattr(reply_recovery_queue, "ReplyRecoveryQueue", Queue)
    event = _GroupEvent(702, "")
    event.message = _Message([_ImageSeg({"file": "opaque-image-token"})])
    gate = Gate()
    processed: list[int] = []
    msg_buffer: dict[str, dict[str, Any]] = {}

    async def process(_bot: Any, current: Any, _state: dict[str, Any]) -> None:
        processed.append(int(current.message_id))

    async def run() -> None:
        await reply_buffer.handle_reply_event(
            _Bot(),
            event,
            {"is_random_chat": True},
            poke_event_cls=type("PokeEvent", (), {}),
            message_event_cls=_PrivateEvent,
            group_message_event_cls=_GroupEvent,
            process_response_logic=process,
            msg_buffer=msg_buffer,
            start_buffer_timer=lambda *_args: None,
            logger=_Logger(),
            user_policy_gate=gate,
        )
        gate.allowed = False
        await reply_buffer.run_buffer_timer(
            "999:456",
            _Bot(),
            msg_buffer=msg_buffer,
            process_response_logic=process,
            message_event_cls=_PrivateEvent,
            message_cls=_Message,
            message_segment_cls=_MessageSegment,
            logger=_Logger(),
            user_policy_gate=gate,
        )

    asyncio.run(run())
    assert processed == []
    assert msg_buffer == {}
    assert len(recorded) == 1
    assert recorded[0]["original_message_id"] == "702"
    assert recorded[0]["failure_stage"] == "permission_revoked"
    assert recorded[0]["failure_class"] == "delivery_unknown"
    assert recorded[0]["media_refs"] and recorded[0]["media_refs"][0]["kind"] == "image"


def test_policy_rejection_is_quarantined_and_never_auto_claimed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    queue = reply_recovery_queue.ReplyRecoveryQueue(tmp_path / "policy-rejected.sqlite3")
    monkeypatch.setattr(reply_recovery_queue, "ReplyRecoveryQueue", lambda: queue)

    class Gate:
        async def allows_current(self, _event: Any) -> bool:
            return False

    event = _GroupEvent(703, "")
    event.message = _Message([_ImageSeg({"file": "opaque-image-token"})])
    processed: list[int] = []

    async def process(_bot: Any, current: Any, _state: dict[str, Any]) -> None:
        processed.append(int(current.message_id))

    async def run() -> None:
        await reply_buffer.handle_reply_event(
            _Bot(),
            event,
            {},
            poke_event_cls=type("PokeEvent", (), {}),
            message_event_cls=_PrivateEvent,
            group_message_event_cls=_GroupEvent,
            process_response_logic=process,
            msg_buffer={},
            start_buffer_timer=lambda *_args: None,
            logger=_Logger(),
            user_policy_gate=Gate(),
        )

    asyncio.run(run())
    item = queue.get(1)
    assert processed == []
    assert item is not None
    assert item.status == "quarantined"
    assert item.failure_class == "delivery_unknown"
    assert item.failure_stage == "permission_revoked"
    assert item.media_refs and item.media_refs[0]["kind"] == "image"
    assert queue.claim_next_batch(worker_id="policy-worker") is None


def test_recovery_synthetic_id_includes_stable_media_and_event_time(monkeypatch: pytest.MonkeyPatch) -> None:
    recorded: list[dict[str, Any]] = []
    class Queue:
        def record_failure(self, **kwargs: Any) -> None:
            recorded.append(kwargs)
    monkeypatch.setattr(reply_recovery_queue, "ReplyRecoveryQueue", Queue)
    base = {"user_id": "u", "group_id": "g", "event_time": "42", "text": "", "media": [{"kind": "image", "data": {"id": "a"}}]}
    reply_buffer._record_recovery_failure(bot=_Bot(), event=_GroupEvent(0, ""), state={"batched_events": [base]}, failure_stage="x", failure_class="generation_failed_before_send")
    same = {**base, "media": [{"data": {"id": "a"}, "kind": "image"}]}
    other = {**base, "media": [{"kind": "image", "data": {"id": "b"}}]}
    reply_buffer._record_recovery_failure(bot=_Bot(), event=_GroupEvent(0, ""), state={"batched_events": [same]}, failure_stage="x", failure_class="generation_failed_before_send")
    reply_buffer._record_recovery_failure(bot=_Bot(), event=_GroupEvent(0, ""), state={"batched_events": [other]}, failure_stage="x", failure_class="generation_failed_before_send")
    assert recorded[0]["original_message_id"] == recorded[1]["original_message_id"]
    assert recorded[0]["original_message_id"] != recorded[2]["original_message_id"]


def test_admission_timeout_records_safe_recovery(monkeypatch: pytest.MonkeyPatch) -> None:
    recorded: list[dict[str, str]] = []
    monkeypatch.setattr(reply_buffer, "_record_recovery_failure", lambda **kw: recorded.append({"stage": kw["failure_stage"], "kind": kw["failure_class"]}) or 1)
    reply_buffer._record_reply_admission_timeout(
        bot=_Bot(), event=_GroupEvent(52, "排队"), state={"batched_events": [{"message_id": "52", "text": "排队"}]},
        session_key="secret-session", wait_ms=1, mode="buffered",
    )
    assert recorded == [{"stage": "reply_admission_timeout", "kind": "generation_failed_before_send"}]


def test_buffer_diagnostics_are_aggregate_and_do_not_emit_identifiers(monkeypatch: pytest.MonkeyPatch) -> None:
    entry = reply_buffer._new_entry(1.0)
    reply_buffer._note_buffer_diagnostic(entry, "enqueue", count=3)
    reply_buffer._note_buffer_diagnostic(entry, "failure_send", count=1)
    payload = repr(reply_buffer._take_buffer_diagnostics(entry, generation=7))
    assert "'code': 'enqueue'" in payload and "'generation': 7" in payload
    assert "QQ" not in payload and "secret" not in payload and "正文" not in payload


def test_share_card_context_is_structured_and_untrusted() -> None:
    event = _GroupEvent(99, "看看这个")
    event.message.append(
        _JsonSeg(
            {
                "data": '{"prompt":"[链接] 示例","meta":{"news":{"jumpUrl":"https://example.com/a","desc":"摘要"}}}'
            }
        )
    )
    shared, forward = reply_buffer._extract_shared_content_context(event)
    assert forward is None
    assert len(shared) == 1
    assert shared[0]["canonical_url"] == "https://example.com/a"
    assert shared[0]["trust"] == "untrusted_data_only"
    assert shared[0]["evidence_state"] == "metadata_only"


class _PolicyGate:
    def __init__(self, allowed: bool) -> None:
        self.allowed = allowed
        self.calls: list[int] = []

    async def allows_current(self, event: Any) -> bool:
        self.calls.append(int(event.message_id))
        return self.allowed


def test_private_direct_message_does_not_cancel_nonrandom_generation() -> None:
    asyncio.run(_run_private_direct_message_does_not_cancel_nonrandom_generation())


async def _run_private_direct_message_does_not_cancel_nonrandom_generation() -> None:
    msg_buffer: dict[str, dict[str, Any]] = {}
    tasks: list[asyncio.Task[Any]] = []
    first_started = asyncio.Event()
    first_cancelled = asyncio.Event()
    release_first = asyncio.Event()
    second_processed = asyncio.Event()
    processed_ids: list[int] = []

    async def process_response_logic(_bot: Any, event: Any, _state: dict[str, Any]) -> None:
        processed_ids.append(int(event.message_id))
        if int(event.message_id) == 1:
            first_started.set()
            try:
                await release_first.wait()
            except asyncio.CancelledError:
                first_cancelled.set()
                raise
        if int(event.message_id) == 2:
            second_processed.set()

    def start_buffer_timer(key: str, bot: Any, wait_seconds: float) -> asyncio.Task[Any]:
        task = asyncio.create_task(
            reply_buffer.run_buffer_timer(
                key,
                bot,
                msg_buffer=msg_buffer,
                process_response_logic=process_response_logic,
                message_event_cls=_PrivateEvent,
                message_cls=_Message,
                message_segment_cls=_MessageSegment,
                logger=_Logger(),
                delay=wait_seconds,
                response_timeout_seconds=30,
                batch_base_wait_seconds=0.03,
                batch_min_wait_seconds=0.01,
                batch_max_wait_seconds=0.06,
            )
        )
        tasks.append(task)
        return task

    try:
        await reply_buffer.handle_reply_event(
            _Bot(),
            _PrivateEvent(1, "first"),
            {},
            poke_event_cls=type("PokeEvent", (), {}),
            message_event_cls=_PrivateEvent,
            group_message_event_cls=_GroupEvent,
            process_response_logic=process_response_logic,
            msg_buffer=msg_buffer,
            start_buffer_timer=start_buffer_timer,
            logger=_Logger(),
            batch_base_wait_seconds=0.03,
            batch_min_wait_seconds=0.01,
            batch_max_wait_seconds=0.06,
        )
        await asyncio.wait_for(first_started.wait(), timeout=1)

        await reply_buffer.handle_reply_event(
            _Bot(),
            _PrivateEvent(2, "second"),
            {},
            poke_event_cls=type("PokeEvent", (), {}),
            message_event_cls=_PrivateEvent,
            group_message_event_cls=_GroupEvent,
            process_response_logic=process_response_logic,
            msg_buffer=msg_buffer,
            start_buffer_timer=start_buffer_timer,
            logger=_Logger(),
            batch_base_wait_seconds=0.03,
            batch_min_wait_seconds=0.01,
            batch_max_wait_seconds=0.06,
        )

        await asyncio.sleep(0.02)
        assert not first_cancelled.is_set()
        assert not second_processed.is_set()
        release_first.set()
        await asyncio.wait_for(second_processed.wait(), timeout=1)

        assert processed_ids == [1, 2]
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def test_direct_turns_run_concurrently_and_commit_without_interleaving() -> None:
    asyncio.run(_run_direct_turns_run_concurrently_and_commit_without_interleaving())


async def _run_direct_turns_run_concurrently_and_commit_without_interleaving() -> None:
    controller = reply_buffer.ReplyConcurrencyController(session_limit=3, global_limit=3)
    msg_buffer: dict[str, dict[str, Any]] = {}
    release_generation = asyncio.Event()
    all_started = asyncio.Event()
    active = 0
    max_active = 0
    commit_order: list[int] = []

    async def process_response_logic(_bot: Any, event: Any, state: dict[str, Any]) -> None:
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        if active == 3:
            all_started.set()
        await release_generation.wait()
        active -= 1
        lock = state["reply_commit_lock"]
        async with lock:
            commit_order.append(int(event.message_id))
            await asyncio.sleep(0.01)

    async def dispatch(message_id: int) -> None:
        await reply_buffer.handle_reply_event(
            _Bot(),
            _PrivateEvent(message_id, f"message-{message_id}"),
            {},
            poke_event_cls=type("PokeEvent", (), {}),
            message_event_cls=_PrivateEvent,
            group_message_event_cls=_GroupEvent,
            process_response_logic=process_response_logic,
            msg_buffer=msg_buffer,
            start_buffer_timer=lambda *_args: None,
            logger=_Logger(),
            concurrency_controller=controller,
            response_timeout_seconds=30,
        )

    tasks = [asyncio.create_task(dispatch(index)) for index in range(1, 4)]
    await asyncio.wait_for(all_started.wait(), timeout=1)
    release_generation.set()
    await asyncio.gather(*tasks)

    assert max_active == 3
    assert sorted(commit_order) == [1, 2, 3]


def test_direct_turn_concurrency_limit_queues_without_dropping() -> None:
    asyncio.run(_run_direct_turn_concurrency_limit_queues_without_dropping())


async def _run_direct_turn_concurrency_limit_queues_without_dropping() -> None:
    controller = reply_buffer.ReplyConcurrencyController(session_limit=2, global_limit=2)
    active = 0
    max_active = 0
    processed: list[int] = []

    async def process_response_logic(_bot: Any, event: Any, _state: dict[str, Any]) -> None:
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.02)
        processed.append(int(event.message_id))
        active -= 1

    async def dispatch(message_id: int) -> None:
        await reply_buffer.handle_reply_event(
            _Bot(),
            _PrivateEvent(message_id, f"message-{message_id}"),
            {},
            poke_event_cls=type("PokeEvent", (), {}),
            message_event_cls=_PrivateEvent,
            group_message_event_cls=_GroupEvent,
            process_response_logic=process_response_logic,
            msg_buffer={},
            start_buffer_timer=lambda *_args: None,
            logger=_Logger(),
            concurrency_controller=controller,
            response_timeout_seconds=30,
        )

    await asyncio.gather(*(dispatch(index) for index in range(1, 6)))

    assert max_active == 2
    assert sorted(processed) == [1, 2, 3, 4, 5]


def test_session_key_isolated_by_bot_id() -> None:
    event = _GroupEvent(1, "hello")

    first = reply_buffer._session_key(
        event,
        group_message_event_cls=_GroupEvent,
        bot_self_id="10001",
    )
    second = reply_buffer._session_key(
        event,
        group_message_event_cls=_GroupEvent,
        bot_self_id="10002",
    )

    assert first == "10001:456"
    assert second == "10002:456"
    assert first != second


def test_batched_failure_stays_silent_without_replaying_delivery() -> None:
    async def run() -> None:
        msg_buffer: dict[str, dict[str, Any]] = {}
        logger = _Logger()
        calls = 0

        async def process_response_logic(_bot: Any, _event: Any, state: dict[str, Any]) -> None:
            nonlocal calls
            calls += 1
            state["reply_delivery_started"] = True
            raise RuntimeError("https://private.example/?api_key=top-secret")

        event = _GroupEvent(1, "hello")
        await reply_buffer.handle_reply_event(
            _Bot(),
            event,
            {},
            poke_event_cls=type("PokeEvent", (), {}),
            message_event_cls=_PrivateEvent,
            group_message_event_cls=_GroupEvent,
            process_response_logic=process_response_logic,
            msg_buffer=msg_buffer,
            start_buffer_timer=lambda *_args: None,
            logger=logger,
        )
        key = reply_buffer._session_key(event, group_message_event_cls=_GroupEvent, bot_self_id="999")
        await reply_buffer.run_buffer_timer(
            key,
            _Bot(),
            msg_buffer=msg_buffer,
            process_response_logic=process_response_logic,
            message_event_cls=_PrivateEvent,
            message_cls=_Message,
            message_segment_cls=_MessageSegment,
            logger=logger,
            delay=0,
            response_timeout_seconds=30,
        )

        assert calls == 1
        assert logger.errors
        assert "delivery_state=dispatching" in logger.errors[-1]
        assert "top-secret" not in logger.errors[-1]
        assert "private.example" not in logger.errors[-1]

    asyncio.run(run())


def test_group_mentions_enter_buffers_instead_of_direct_turns() -> None:
    async def run() -> None:
        controller = reply_buffer.ReplyConcurrencyController(session_limit=3, global_limit=3)
        started: list[int] = []

        async def process_response_logic(_bot: Any, event: Any, _state: dict[str, Any]) -> None:
            started.append(int(event.message_id))

        async def dispatch(message_id: int) -> None:
            await reply_buffer.handle_reply_event(
                _Bot(),
                _MentionEvent(message_id, f"mention-{message_id}"),
                {},
                poke_event_cls=type("PokeEvent", (), {}),
                message_event_cls=_PrivateEvent,
                group_message_event_cls=_GroupEvent,
                process_response_logic=process_response_logic,
                msg_buffer={},
                start_buffer_timer=lambda *_args: None,
                logger=_Logger(),
                concurrency_controller=controller,
                response_timeout_seconds=30,
            )

        await asyncio.wait_for(asyncio.gather(*(dispatch(index) for index in range(1, 4))), timeout=1)
        # Each isolated first group turn was scheduled into its own supplied
        # buffer, not executed by the private direct lane.
        assert started == []

    asyncio.run(run())


def test_private_and_group_mention_are_marked_reply_required() -> None:
    async def run() -> None:
        controller = reply_buffer.ReplyConcurrencyController(session_limit=2, global_limit=2)
        captured: list[dict[str, Any]] = []

        async def process_response_logic(_bot: Any, _event: Any, state: dict[str, Any]) -> None:
            captured.append(dict(state))

        msg_buffer: dict[str, dict[str, Any]] = {}
        for event in (_PrivateEvent(1, "private"), _MentionEvent(2, "mention")):
            await reply_buffer.handle_reply_event(
                _Bot(),
                event,
                {},
                poke_event_cls=type("PokeEvent", (), {}),
                message_event_cls=_PrivateEvent,
                group_message_event_cls=_GroupEvent,
                process_response_logic=process_response_logic,
                msg_buffer=msg_buffer,
                start_buffer_timer=lambda *_args: None,
                logger=_Logger(),
                concurrency_controller=controller,
                response_timeout_seconds=30,
            )

        assert len(captured) == 1
        assert all(state["reply_required"] is True for state in captured)
        assert all(float(state["response_deadline"]) > 0 for state in captured)
        assert any(entry.get("items") for entry in msg_buffer.values())

    asyncio.run(run())


def test_private_file_video_is_carried_into_same_sender_followup() -> None:
    async def run() -> None:
        reply_buffer._clear_recent_media_for_test()
        controller = reply_buffer.ReplyConcurrencyController(session_limit=2, global_limit=2)
        states: list[dict[str, Any]] = []

        async def process_response_logic(_bot: Any, _event: Any, state: dict[str, Any]) -> None:
            states.append(dict(state))

        file_event = _PrivateEvent(1, "")
        file_event.message = _Message(
            [_FileSeg({"file": "opaque-video-token", "name": "gameplay.mp4"})]
        )
        prompt_event = _PrivateEvent(2, "概括刚才的视频")
        msg_buffer: dict[str, dict[str, Any]] = {}
        common = {
            "poke_event_cls": type("PokeEvent", (), {}),
            "message_event_cls": _PrivateEvent,
            "group_message_event_cls": _GroupEvent,
            "process_response_logic": process_response_logic,
            "msg_buffer": msg_buffer,
            "start_buffer_timer": lambda *_args: None,
            "logger": _Logger(),
            "concurrency_controller": controller,
            "response_timeout_seconds": 30,
        }
        try:
            await reply_buffer.handle_reply_event(_Bot(), file_event, {}, **common)
            await reply_buffer.handle_reply_event(_Bot(), prompt_event, {}, **common)
        finally:
            reply_buffer._clear_recent_media_for_test()

        assert len(states) == 2
        first_media = states[0]["turn_media_context"]
        followup_media = states[1]["turn_media_context"]
        assert len(first_media) == 1
        assert len(followup_media) == 1
        assert states[1]["batch_event_count"] == 2
        assert followup_media[0]["kind"] == "video"
        assert followup_media[0]["origin"] == "batch"
        assert followup_media[0]["owner_user_id"] == "123"
        assert followup_media[0]["message_id"] == "1"
        assert followup_media[0]["file_id"] == "opaque-video-token"
        assert reply_buffer._recent_media_for_followup(
            session_key="999:private_123",
            user_id="123",
            now=time.monotonic(),
        ) == []

    asyncio.run(run())


def test_recent_file_media_is_not_shared_with_another_group_sender() -> None:
    reply_buffer._clear_recent_media_for_test()
    now = time.monotonic()
    refs = [
        {
            "media_id": "media-owner-a",
            "ref": "opaque-video-token",
            "origin": "current",
            "owner_user_id": "owner-a",
            "message_id": "file-message",
            "kind": "video",
            "file_id": "opaque-video-token",
        }
    ]
    try:
        reply_buffer._remember_recent_media(
            session_key="bot:group-1",
            user_id="owner-a",
            values=refs,
            now=now,
        )
        assert reply_buffer._recent_media_for_followup(
            session_key="bot:group-1",
            user_id="owner-b",
            now=now + 1,
        ) == []
    finally:
        reply_buffer._clear_recent_media_for_test()


def test_unresolved_quoted_media_marks_completion_evidence_unavailable() -> None:
    async def run() -> None:
        reply_buffer._clear_recent_media_for_test()
        controller = reply_buffer.ReplyConcurrencyController(session_limit=2, global_limit=2)
        captured: dict[str, Any] = {}

        async def process_response_logic(_bot: Any, _event: Any, state: dict[str, Any]) -> None:
            captured.update(state)

        event = _PrivateEvent(9, "概括引用的视频")
        event.reply = type(
            "Reply",
            (),
            {
                "message_id": "404",
                "sender": type("Sender", (), {"user_id": "123"})(),
                "message": [],
            },
        )()
        try:
            await reply_buffer.handle_reply_event(
                _Bot(),
                event,
                {},
                poke_event_cls=type("PokeEvent", (), {}),
                message_event_cls=_PrivateEvent,
                group_message_event_cls=_GroupEvent,
                process_response_logic=process_response_logic,
                msg_buffer={},
                start_buffer_timer=lambda *_args: None,
                logger=_Logger(),
                concurrency_controller=controller,
                response_timeout_seconds=30,
            )
        finally:
            reply_buffer._clear_recent_media_for_test()

        assert captured["turn_media_context"] == []
        assert captured["media_reference_unavailable"] is True

    asyncio.run(run())


def test_random_bot_target_is_not_upgraded_to_required_reply() -> None:
    async def run() -> None:
        msg_buffer: dict[str, dict[str, Any]] = {}
        state = {"is_random_chat": True, "message_target": "bot"}
        await reply_buffer.handle_reply_event(
            _Bot(),
            _GroupEvent(1, "random"),
            state,
            poke_event_cls=type("PokeEvent", (), {}),
            message_event_cls=_PrivateEvent,
            group_message_event_cls=_GroupEvent,
            process_response_logic=lambda *_args: None,
            msg_buffer=msg_buffer,
            start_buffer_timer=lambda *_args: None,
            logger=_Logger(),
            concurrency_controller=reply_buffer.ReplyConcurrencyController(),
        )

        assert state["reply_required"] is False
        assert msg_buffer

    asyncio.run(run())


def test_reply_to_same_account_external_plugin_is_not_persona_direct_turn() -> None:
    event = _GroupEvent(1, "plugin followup")
    event.reply = type(
        "Reply",
        (),
        {"sender": type("Sender", (), {"user_id": "999"})()},
    )()

    assert reply_buffer._is_reply_to_bot(
        event,
        "999",
        message_target="external_plugin",
    ) is False


def test_blocked_event_never_enters_direct_or_buffer_processing() -> None:
    async def run() -> None:
        processed: list[int] = []
        msg_buffer: dict[str, dict[str, Any]] = {}
        gate = _PolicyGate(False)

        async def process(_bot: Any, event: Any, _state: dict[str, Any]) -> None:
            processed.append(int(event.message_id))

        await reply_buffer.handle_reply_event(
            _Bot(),
            _PrivateEvent(1, "blocked"),
            {},
            poke_event_cls=type("PokeEvent", (), {}),
            message_event_cls=_PrivateEvent,
            group_message_event_cls=_GroupEvent,
            process_response_logic=process,
            msg_buffer=msg_buffer,
            start_buffer_timer=lambda *_args: None,
            logger=_Logger(),
            user_policy_gate=gate,
        )

        assert processed == []
        assert msg_buffer == {}
        assert gate.calls == [1]

    asyncio.run(run())


def test_buffer_dequeue_drops_user_blocked_after_enqueue() -> None:
    async def run() -> None:
        processed: list[int] = []
        msg_buffer: dict[str, dict[str, Any]] = {}
        gate = _PolicyGate(True)
        event = _GroupEvent(1, "later blocked")

        async def process(_bot: Any, current: Any, _state: dict[str, Any]) -> None:
            processed.append(int(current.message_id))

        await reply_buffer.handle_reply_event(
            _Bot(),
            event,
            {"is_random_chat": True},
            poke_event_cls=type("PokeEvent", (), {}),
            message_event_cls=_PrivateEvent,
            group_message_event_cls=_GroupEvent,
            process_response_logic=process,
            msg_buffer=msg_buffer,
            start_buffer_timer=lambda *_args: None,
            logger=_Logger(),
            user_policy_gate=gate,
        )
        gate.allowed = False
        key = reply_buffer._session_key(event, group_message_event_cls=_GroupEvent, bot_self_id="999")

        await reply_buffer.run_buffer_timer(
            key,
            _Bot(),
            msg_buffer=msg_buffer,
            process_response_logic=process,
            message_event_cls=_PrivateEvent,
            message_cls=_Message,
            message_segment_cls=_MessageSegment,
            logger=_Logger(),
            user_policy_gate=gate,
        )

        assert processed == []
        assert msg_buffer == {}

    asyncio.run(run())



def test_direct_turn_cancels_active_random_turn_only() -> None:
    async def run() -> None:
        timing = {
            "batch_base_wait_seconds": 0.03,
            "batch_min_wait_seconds": 0.01,
            "batch_max_wait_seconds": 0.06,
        }
        controller = reply_buffer.ReplyConcurrencyController(session_limit=3, global_limit=3)
        msg_buffer: dict[str, dict[str, Any]] = {}
        timer_tasks: list[asyncio.Task[Any]] = []
        random_started = asyncio.Event()
        random_cancelled = asyncio.Event()
        second_batch_seen = asyncio.Event()
        direct_finished = asyncio.Event()
        generations: dict[int, int] = {}

        async def process_response_logic(_bot: Any, event: Any, _state: dict[str, Any]) -> None:
            first_run = int(event.message_id) not in generations
            generations[int(event.message_id)] = int(_state["turn_generation_id"])
            if int(event.message_id) == 1 and first_run:
                random_started.set()
                try:
                    await asyncio.sleep(10)
                except asyncio.CancelledError:
                    random_cancelled.set()
                    raise
            elif int(event.message_id) == 3:
                assert [item["message_id"] for item in _state["batched_events"]] == ["1", "2", "3"]
                direct_finished.set()
                second_batch_seen.set()

        def start_buffer_timer(key: str, bot: Any, wait_seconds: float) -> asyncio.Task[Any]:
            task = asyncio.create_task(
                reply_buffer.run_buffer_timer(
                    key,
                    bot,
                    msg_buffer=msg_buffer,
                    process_response_logic=process_response_logic,
                    message_event_cls=_PrivateEvent,
                    message_cls=_Message,
                    message_segment_cls=_MessageSegment,
                    logger=_Logger(),
                    delay=wait_seconds,
                    response_timeout_seconds=30,
                    concurrency_controller=controller,
                    **timing,
                )
            )
            timer_tasks.append(task)
            return task

        await reply_buffer.handle_reply_event(
            _Bot(),
            _GroupEvent(1, "random"),
            {"is_random_chat": True},
            poke_event_cls=type("PokeEvent", (), {}),
            message_event_cls=_PrivateEvent,
            group_message_event_cls=_GroupEvent,
            process_response_logic=process_response_logic,
            msg_buffer=msg_buffer,
            start_buffer_timer=start_buffer_timer,
            logger=_Logger(),
            concurrency_controller=controller,
            response_timeout_seconds=30,
            **timing,
        )
        await asyncio.wait_for(random_started.wait(), timeout=1)

        await reply_buffer.handle_reply_event(
            _Bot(),
            _GroupEvent(2, "pending-random"),
            {"is_random_chat": True},
            poke_event_cls=type("PokeEvent", (), {}),
            message_event_cls=_PrivateEvent,
            group_message_event_cls=_GroupEvent,
            process_response_logic=process_response_logic,
            msg_buffer=msg_buffer,
            start_buffer_timer=start_buffer_timer,
            logger=_Logger(),
            concurrency_controller=controller,
            response_timeout_seconds=30,
            **timing,
        )

        await reply_buffer.handle_reply_event(
            _Bot(),
            _MentionEvent(3, "direct"),
            {},
            poke_event_cls=type("PokeEvent", (), {}),
            message_event_cls=_PrivateEvent,
            group_message_event_cls=_GroupEvent,
            process_response_logic=process_response_logic,
            msg_buffer=msg_buffer,
            start_buffer_timer=start_buffer_timer,
            logger=_Logger(),
            concurrency_controller=controller,
            response_timeout_seconds=30,
            **timing,
        )

        await asyncio.wait_for(random_cancelled.wait(), timeout=1)
        await asyncio.wait_for(direct_finished.wait(), timeout=1)
        await asyncio.wait_for(second_batch_seen.wait(), timeout=1)
        await asyncio.gather(*timer_tasks, return_exceptions=True)
        assert generations[1] != generations[3]

    asyncio.run(run())


def test_waiting_group_batch_merges_direct_cue_and_fires_immediately_in_order() -> None:
    async def run() -> None:
        controller = reply_buffer.ReplyConcurrencyController(session_limit=2, global_limit=2)
        msg_buffer: dict[str, dict[str, Any]] = {}
        processed = asyncio.Event()
        observed: list[list[str]] = []
        tasks: list[asyncio.Task[Any]] = []

        async def process(_bot: Any, _event: Any, state: dict[str, Any]) -> None:
            observed.append([item["message_id"] for item in state["batched_events"]])
            processed.set()

        def start(key: str, bot: Any, wait: float) -> asyncio.Task[Any]:
            task = asyncio.create_task(reply_buffer.run_buffer_timer(
                key, bot, msg_buffer=msg_buffer, process_response_logic=process,
                message_event_cls=_PrivateEvent, message_cls=_Message,
                message_segment_cls=_MessageSegment, logger=_Logger(), delay=wait,
                concurrency_controller=controller,
                batch_base_wait_seconds=1, batch_min_wait_seconds=0.01,
                batch_max_wait_seconds=1,
            ))
            tasks.append(task)
            return task

        common = dict(
            poke_event_cls=type("PokeEvent", (), {}), message_event_cls=_PrivateEvent,
            group_message_event_cls=_GroupEvent, process_response_logic=process,
            msg_buffer=msg_buffer, start_buffer_timer=start, logger=_Logger(),
            concurrency_controller=controller, batch_base_wait_seconds=1,
            batch_min_wait_seconds=0.01, batch_max_wait_seconds=1,
        )
        await reply_buffer.handle_reply_event(_Bot(), _GroupEvent(1, "queued"), {"is_random_chat": True}, **common)
        await reply_buffer.handle_reply_event(_Bot(), _MentionEvent(2, "direct"), {}, **common)
        await asyncio.wait_for(processed.wait(), timeout=1)
        await asyncio.gather(*tasks, return_exceptions=True)
        assert observed == [["1", "2"]]

    asyncio.run(run())


@pytest.mark.parametrize(
    "delivery_flag",
    ["reply_delivery_started", "reply_delivery_confirmed", "reply_delivery_complete", "delivery_unknown"],
)
def test_direct_cue_after_delivery_state_waits_for_next_generation(delivery_flag: str) -> None:
    async def run() -> None:
        controller = reply_buffer.ReplyConcurrencyController(session_limit=2, global_limit=2)
        msg_buffer: dict[str, dict[str, Any]] = {}
        started = asyncio.Event()
        release = asyncio.Event()
        second_done = asyncio.Event()
        cancelled = False
        batches: list[list[str]] = []
        tasks: list[asyncio.Task[Any]] = []
        active_state: dict[str, Any] = {}

        async def process(_bot: Any, event: Any, state: dict[str, Any]) -> None:
            nonlocal cancelled
            batches.append([item["message_id"] for item in state["batched_events"]])
            if int(event.message_id) == 1:
                active_state.update(state)
                state[delivery_flag] = True
                started.set()
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    cancelled = True
                    raise
            else:
                second_done.set()

        def start(key: str, bot: Any, wait: float) -> asyncio.Task[Any]:
            task = asyncio.create_task(reply_buffer.run_buffer_timer(
                key, bot, msg_buffer=msg_buffer, process_response_logic=process,
                message_event_cls=_PrivateEvent, message_cls=_Message,
                message_segment_cls=_MessageSegment, logger=_Logger(), delay=wait,
                concurrency_controller=controller, batch_base_wait_seconds=.01,
                batch_min_wait_seconds=.01, batch_max_wait_seconds=.05,
            ))
            tasks.append(task)
            return task

        common = dict(
            poke_event_cls=type("PokeEvent", (), {}), message_event_cls=_PrivateEvent,
            group_message_event_cls=_GroupEvent, process_response_logic=process,
            msg_buffer=msg_buffer, start_buffer_timer=start, logger=_Logger(),
            concurrency_controller=controller, batch_base_wait_seconds=.01,
            batch_min_wait_seconds=.01, batch_max_wait_seconds=.05,
        )
        first_event = _GroupEvent(1, "random")
        key = reply_buffer._session_key(
            first_event,
            group_message_event_cls=_GroupEvent,
            bot_self_id="999",
        )
        await reply_buffer.handle_reply_event(
            _Bot(),
            first_event,
            {"is_random_chat": True},
            **common,
        )
        await asyncio.wait_for(started.wait(), timeout=1)
        await reply_buffer.handle_reply_event(_Bot(), _MentionEvent(2, "direct"), {}, **common)
        assert not cancelled
        assert msg_buffer[key]["pending_items"]
        assert pipeline_context.stale_reply_abort_reason(active_state) == ""
        assert not yaml_processor._batch_ref_has_newer_messages(active_state["batch_runtime_ref"])
        release.set()
        await asyncio.wait_for(second_done.wait(), timeout=1)
        await asyncio.gather(*tasks, return_exceptions=True)
        assert batches == [["1"], ["2"]]
        assert not cancelled

    asyncio.run(run())


def test_active_must_reply_turn_is_not_cancelled_and_direct_queues_next_generation() -> None:
    async def run() -> None:
        controller = reply_buffer.ReplyConcurrencyController(session_limit=2, global_limit=2)
        event = _GroupEvent(1, "must reply")
        key = reply_buffer._session_key(event, group_message_event_cls=_GroupEvent, bot_self_id="999")
        msg_buffer: dict[str, dict[str, Any]] = {
            key: reply_buffer._new_entry(0.0),
        }
        msg_buffer[key]["items"] = [{"event": event, "state": {}, "received_at": time.monotonic(), "dedupe_key": "id:1"}]
        started = asyncio.Event()
        release = asyncio.Event()
        second_done = asyncio.Event()
        cancelled = False
        tasks: list[asyncio.Task[Any]] = []
        batches: list[list[str]] = []
        active_state: dict[str, Any] = {}

        async def process(_bot: Any, selected: Any, state: dict[str, Any]) -> None:
            nonlocal cancelled
            batches.append([item["message_id"] for item in state["batched_events"]])
            if int(selected.message_id) == 1:
                active_state.update(state)
                started.set()
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    cancelled = True
                    raise
            else:
                second_done.set()

        def start(timer_key: str, bot: Any, wait: float) -> asyncio.Task[Any]:
            task = asyncio.create_task(reply_buffer.run_buffer_timer(
                timer_key, bot, msg_buffer=msg_buffer, process_response_logic=process,
                message_event_cls=_PrivateEvent, message_cls=_Message,
                message_segment_cls=_MessageSegment, logger=_Logger(), delay=wait,
                concurrency_controller=controller, batch_base_wait_seconds=.01,
                batch_min_wait_seconds=.01, batch_max_wait_seconds=.05,
            ))
            tasks.append(task)
            return task

        active = start(key, _Bot(), 0.0)
        await asyncio.wait_for(started.wait(), timeout=1)
        await reply_buffer.handle_reply_event(
            _Bot(), _MentionEvent(2, "new direct"), {},
            poke_event_cls=type("PokeEvent", (), {}), message_event_cls=_PrivateEvent,
            group_message_event_cls=_GroupEvent, process_response_logic=process,
            msg_buffer=msg_buffer, start_buffer_timer=start, logger=_Logger(),
            concurrency_controller=controller, batch_base_wait_seconds=.01,
            batch_min_wait_seconds=.01, batch_max_wait_seconds=.05,
        )
        assert not cancelled
        assert msg_buffer[key]["pending_items"]
        assert pipeline_context.stale_reply_abort_reason(active_state) == ""
        assert not yaml_processor._batch_ref_has_newer_messages(active_state["batch_runtime_ref"])
        release.set()
        await asyncio.wait_for(second_done.wait(), timeout=1)
        await asyncio.gather(active, *tasks, return_exceptions=True)
        assert batches == [["1"], ["2"]]
        assert not cancelled

    asyncio.run(run())


def test_session_queue_does_not_consume_global_slots() -> None:
    async def run() -> None:
        controller = reply_buffer.ReplyConcurrencyController(session_limit=1, global_limit=1)
        first_started = asyncio.Event()
        release_first = asyncio.Event()
        order: list[str] = []

        async def occupy_first() -> None:
            async with controller.direct_turn("bot:group-a"):
                order.append("a1")
                first_started.set()
                await release_first.wait()

        async def queued_same_session() -> None:
            async with controller.direct_turn("bot:group-a"):
                order.append("a2")

        async def other_session() -> None:
            async with controller.direct_turn("bot:group-b"):
                order.append("b1")

        first = asyncio.create_task(occupy_first())
        await first_started.wait()
        queued = asyncio.create_task(queued_same_session())
        other = asyncio.create_task(other_session())
        await asyncio.sleep(0)
        release_first.set()
        await asyncio.gather(first, queued, other)

        assert order == ["a1", "b1", "a2"]

    asyncio.run(run())


def test_session_gates_are_reclaimed_after_large_session_churn() -> None:
    async def run() -> None:
        controller = reply_buffer.ReplyConcurrencyController(session_limit=3, global_limit=12)
        for index in range(10_000):
            async with controller.direct_turn(f"bot:private_{index}"):
                pass
        assert controller.snapshot() == {"active": 0, "waiting": 0, "session_gates": 0}

    asyncio.run(run())


def test_reply_admission_timeout_and_cancellation_release_all_gates() -> None:
    async def run() -> None:
        controller = reply_buffer.ReplyConcurrencyController(session_limit=1, global_limit=1)
        occupied = asyncio.Event()
        release = asyncio.Event()

        async def holder() -> None:
            async with controller.direct_turn("bot:group-a"):
                occupied.set()
                await release.wait()

        first = asyncio.create_task(holder())
        await occupied.wait()
        with pytest.raises(reply_buffer.ReplyAdmissionTimeout):
            async with controller.direct_turn(
                "bot:group-b",
                deadline=time.monotonic() + 0.02,
            ):
                raise AssertionError("timed out turn must not be admitted")

        async def queued() -> None:
            async with controller.direct_turn(
                "bot:group-c",
                deadline=time.monotonic() + 1.0,
            ):
                pass

        pending = asyncio.create_task(queued())
        await asyncio.sleep(0)
        assert controller.snapshot()["waiting"] == 1
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        release.set()
        await first
        assert controller.snapshot() == {"active": 0, "waiting": 0, "session_gates": 0}

    asyncio.run(run())


def test_processing_timeout_is_not_reclassified_as_admission_timeout() -> None:
    async def run() -> None:
        controller = reply_buffer.ReplyConcurrencyController()
        with pytest.raises(asyncio.TimeoutError):
            async with controller.direct_turn("bot:group-a"):
                raise asyncio.TimeoutError
        assert controller.snapshot() == {"active": 0, "waiting": 0, "session_gates": 0}

    asyncio.run(run())


def test_compute_batch_fire_at_single_message_uses_thirty_second_baseline() -> None:
    wait = reply_buffer._schedule_debounce_wait(
        first_at=1000.0,
        last_at=1000.0,
        last_reply_at=0.0,
        base_wait_seconds=30.0,
        min_wait_seconds=10.0,
        max_wait_seconds=60.0,
        legacy_reply_backoff_seconds=None,
        immediate=False,
        now=1000.0,
    )
    assert wait == pytest.approx(30.0)


def test_compute_batch_fire_at_new_message_recomputes_from_last_item() -> None:
    wait = reply_buffer._schedule_debounce_wait(
        first_at=1000.0,
        last_at=1002.0,
        last_reply_at=0.0,
        base_wait_seconds=30.0,
        min_wait_seconds=10.0,
        max_wait_seconds=60.0,
        legacy_reply_backoff_seconds=None,
        immediate=False,
        now=1002.0,
    )
    assert wait == pytest.approx(30.0)


def test_compute_batch_fire_at_clamps_requested_wait_to_minimum() -> None:
    wait = reply_buffer._schedule_debounce_wait(
        first_at=1000.0,
        last_at=1001.0,
        last_reply_at=0.0,
        base_wait_seconds=3.0,
        min_wait_seconds=10.0,
        max_wait_seconds=60.0,
        legacy_reply_backoff_seconds=None,
        immediate=False,
        now=1001.0,
    )
    assert wait == pytest.approx(10.0)


def test_compute_batch_fire_at_capped_by_max_wait() -> None:
    wait = reply_buffer._schedule_debounce_wait(
        first_at=1000.0,
        last_at=1059.0,
        last_reply_at=0.0,
        base_wait_seconds=30.0,
        min_wait_seconds=10.0,
        max_wait_seconds=60.0,
        legacy_reply_backoff_seconds=None,
        immediate=False,
        now=1059.0,
    )
    assert wait == pytest.approx(1.0)


def test_compute_batch_fire_at_immediate_always_zero() -> None:
    wait = reply_buffer._schedule_debounce_wait(
        first_at=1000.0,
        last_at=1000.0,
        last_reply_at=0.0,
        base_wait_seconds=30.0,
        min_wait_seconds=10.0,
        max_wait_seconds=60.0,
        legacy_reply_backoff_seconds=None,
        immediate=True,
        now=1000.0,
    )
    assert wait == 0.0


def test_await_private_direct_backoff_skips_without_recent_reply() -> None:
    async def run() -> None:
        key = "bot:private_987"
        start = time.monotonic()
        await reply_buffer._await_private_direct_backoff(
            key,
            debounce_seconds=0.1,
            max_wait_seconds=1.0,
            backoff_seconds=0.2,
        )
        elapsed = time.monotonic() - start
        assert elapsed < 0.08

    asyncio.run(run())


def test_await_private_direct_backoff_waits_in_burst_after_reply() -> None:
    async def run() -> None:
        key = "bot:private_987"
        reply_buffer._note_session_reply(key)
        start = time.monotonic()
        await reply_buffer._await_private_direct_backoff(
            key,
            debounce_seconds=0.1,
            max_wait_seconds=1.0,
            backoff_seconds=0.3,
        )
        elapsed = time.monotonic() - start
        # 退避下限是「上次回复 + backoff=0.3s」：必须等到，且不得退化成等满 max_wait
        assert elapsed >= 0.28
        assert elapsed < 0.8
        reply_buffer._PRIVATE_DIRECT_STATE.pop(key, None)
        reply_buffer._SESSION_LAST_REPLY_AT.pop(key, None)

    asyncio.run(run())


def test_session_last_reply_at_persists_and_prunes_stale() -> None:
    reply_buffer._note_session_reply("bot:private_555")
    assert reply_buffer._session_last_reply_at("bot:private_555") > 0
    reply_buffer._SESSION_LAST_REPLY_AT["bot:stale"] = time.monotonic() - 10_000
    reply_buffer._note_session_reply("bot:private_556")
    assert "bot:stale" not in reply_buffer._SESSION_LAST_REPLY_AT
    reply_buffer._SESSION_LAST_REPLY_AT.pop("bot:private_555", None)
    reply_buffer._SESSION_LAST_REPLY_AT.pop("bot:private_556", None)


def test_reply_concurrency_limits_hold_under_fifty_turn_burst() -> None:
    async def run() -> None:
        controller = reply_buffer.ReplyConcurrencyController(session_limit=3, global_limit=12)
        global_active = 0
        global_peak = 0
        session_active: dict[str, int] = {}
        session_peak: dict[str, int] = {}
        guard = asyncio.Lock()

        async def worker(index: int) -> None:
            nonlocal global_active, global_peak
            key = f"bot:group-{index % 10}"
            async with controller.direct_turn(key, deadline=time.monotonic() + 2.0):
                async with guard:
                    global_active += 1
                    global_peak = max(global_peak, global_active)
                    session_active[key] = session_active.get(key, 0) + 1
                    session_peak[key] = max(session_peak.get(key, 0), session_active[key])
                await asyncio.sleep(0.002)
                async with guard:
                    global_active -= 1
                    session_active[key] -= 1

        await asyncio.gather(*(worker(index) for index in range(50)))
        assert global_peak <= 12
        assert max(session_peak.values()) <= 3
        assert controller.snapshot() == {"active": 0, "waiting": 0, "session_gates": 0}

    asyncio.run(run())


def test_external_buffer_cleanup_releases_retained_session_gate() -> None:
    context_cleanup = load_personification_module("plugin.personification.core.context_cleanup")
    controller = reply_buffer.ReplyConcurrencyController()
    controller.retain_buffer_session("bot:group-1")
    entry: dict[str, Any] = {
        "timer_task": None,
        "_release_concurrency_gate": lambda: controller.release_buffer_session("bot:group-1"),
    }
    msg_buffer = {"bot:group-1": entry}
    assert controller.snapshot()["session_gates"] == 1
    assert context_cleanup.clear_message_buffer(msg_buffer, "group-1") == 1
    assert controller.snapshot() == {"active": 0, "waiting": 0, "session_gates": 0}


def test_buffer_entry_retains_gate_until_dequeue_finishes() -> None:
    async def run() -> None:
        controller = reply_buffer.ReplyConcurrencyController()
        msg_buffer: dict[str, dict[str, Any]] = {}
        event = _GroupEvent(1, "buffered")

        async def process(_bot: Any, _event: Any, _state: dict[str, Any]) -> None:
            await asyncio.sleep(0)

        await reply_buffer.handle_reply_event(
            _Bot(),
            event,
            {"is_random_chat": True},
            poke_event_cls=type("PokeEvent", (), {}),
            message_event_cls=_PrivateEvent,
            group_message_event_cls=_GroupEvent,
            process_response_logic=process,
            msg_buffer=msg_buffer,
            start_buffer_timer=lambda *_args: None,
            logger=_Logger(),
            concurrency_controller=controller,
        )
        assert controller.snapshot()["session_gates"] == 1
        key = reply_buffer._session_key(event, group_message_event_cls=_GroupEvent, bot_self_id="999")
        await reply_buffer.run_buffer_timer(
            key,
            _Bot(),
            msg_buffer=msg_buffer,
            process_response_logic=process,
            message_event_cls=_PrivateEvent,
            message_cls=_Message,
            message_segment_cls=_MessageSegment,
            logger=_Logger(),
            concurrency_controller=controller,
        )
        assert msg_buffer == {}
        assert controller.snapshot() == {"active": 0, "waiting": 0, "session_gates": 0}

    asyncio.run(run())
