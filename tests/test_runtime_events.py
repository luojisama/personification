from __future__ import annotations

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from ..core import runtime_events
from ..core.runtime_events import (
    RUNTIME_EVENT_CAPACITY,
    RUNTIME_EVENT_HEARTBEAT_SECONDS,
    RuntimeEventBus,
    RuntimeEventError,
    format_sse_heartbeat,
    parse_last_event_id,
)


def _sse_data(frame: str) -> dict[str, object]:
    line = next(item for item in frame.splitlines() if item.startswith("data: "))
    return json.loads(line.removeprefix("data: "))


def test_runtime_event_defaults_are_fixed() -> None:
    bus = RuntimeEventBus()

    assert bus.capacity == RUNTIME_EVENT_CAPACITY == 1000
    assert bus.heartbeat_seconds == RUNTIME_EVENT_HEARTBEAT_SECONDS == 15.0


def test_shared_bus_helpers_publish_without_exposing_mutable_state() -> None:
    shared = runtime_events.get_runtime_event_bus()
    before = shared.latest_id
    event = runtime_events.publish_runtime_event(
        "turn.stage",
        payload={"stage": "tools", "prompt": "secret"},
        trace_id="trace-shared",
    )
    assert event.id == before + 1
    assert event.payload == {"stage": "tools", "prompt": "***"}
    event.payload["stage"] = "tampered"
    replay = shared.replay(before)
    assert replay.events[-1].payload["stage"] == "tools"


def test_publish_assigns_monotonic_ids_and_bounded_timestamps() -> None:
    timestamps = iter([100.25, 101.5])
    bus = RuntimeEventBus(time_source=lambda: next(timestamps))

    first = bus.publish("turn.started", payload={"stage": "buffer"})
    second = bus.publish("turn.finished", payload={"status": "ok"}, trace_id="trace-1")

    assert (first.id, second.id) == (1, 2)
    assert (first.ts, second.ts) == (100.25, 101.5)
    assert second.to_dict()["trace_id"] == "trace-1"


def test_publish_redacts_secrets_and_non_auditable_fields() -> None:
    original = {
        "status": "ok",
        "api_key": "sk-super-secret-value",
        "prompt": "hidden system prompt",
        "systemPrompt": "hidden camel-case prompt",
        "prompt_text": "hidden prompt text",
        "tool_args": {"query": "private query"},
        "toolArguments": {"query": "private camel-case query"},
        "tool_result": "full untrusted result",
        "incoming_text": "raw user message",
        "response": "raw provider response",
        "nested": {
            "cookie": "uin=123; p_skey=secret-cookie",
            "summary": "safe summary",
        },
        "url": "https://example.test/data?token=secret-query&item=1",
    }
    bus = RuntimeEventBus()

    event = bus.publish("log.appended", payload=original)
    original["status"] = "mutated"
    rendered = json.dumps(event.to_dict(), ensure_ascii=False)

    assert event.payload["status"] == "ok"
    assert event.payload["prompt"] == "***"
    assert event.payload["systemPrompt"] == "***"
    assert event.payload["prompt_text"] == "***"
    assert event.payload["tool_args"] == "***"
    assert event.payload["toolArguments"] == "***"
    assert event.payload["tool_result"] == "***"
    assert event.payload["incoming_text"] == "***"
    assert event.payload["response"] == "***"
    assert event.payload["nested"]["summary"] == "safe summary"
    assert "super-secret" not in rendered
    assert "hidden system prompt" not in rendered
    assert "hidden camel-case prompt" not in rendered
    assert "hidden prompt text" not in rendered
    assert "private query" not in rendered
    assert "private camel-case query" not in rendered
    assert "raw user message" not in rendered
    assert "raw provider response" not in rendered
    assert "secret-cookie" not in rendered
    assert "secret-query" not in rendered


def test_topic_is_safe_for_the_sse_event_line() -> None:
    bus = RuntimeEventBus()

    event = bus.publish("turn.started\nevent: injected", payload={})

    assert event.topic == "turn.started_event:_injected"


def test_published_event_cannot_mutate_the_ring_copy() -> None:
    bus = RuntimeEventBus()
    published = bus.publish("turn.stage", payload={"detail": {"status": "ok"}})

    published.payload["detail"]["status"] = "mutated"

    assert bus.replay(0).events[0].payload == {"detail": {"status": "ok"}}


def test_ring_keeps_only_the_latest_one_thousand_events() -> None:
    bus = RuntimeEventBus()
    for index in range(RUNTIME_EVENT_CAPACITY + 5):
        bus.publish("turn.stage", payload={"index": index})

    assert len(bus) == 1000
    assert bus.oldest_id == 6
    assert bus.latest_id == 1005
    replay = bus.replay(5)
    assert replay.resync_required is False
    assert replay.events[0].id == 6
    assert replay.events[-1].id == 1005


def test_replay_requires_resync_for_evicted_or_future_cursor() -> None:
    bus = RuntimeEventBus(capacity=3)
    for index in range(5):
        bus.publish("turn.stage", payload={"index": index})

    evicted = bus.replay(1)
    future = bus.replay(99)

    assert evicted.resync_required is True
    assert evicted.events == ()
    assert (evicted.oldest_id, evicted.latest_id) == (3, 5)
    assert future.resync_required is True


def test_publish_is_thread_safe_and_ids_remain_unique() -> None:
    bus = RuntimeEventBus()

    with ThreadPoolExecutor(max_workers=8) as pool:
        events = list(pool.map(lambda index: bus.publish("turn.stage", payload={"index": index}), range(200)))

    ids = sorted(event.id for event in events)
    assert ids == list(range(1, 201))
    assert bus.latest_id == 200


def test_last_event_id_validation_has_stable_code() -> None:
    assert parse_last_event_id("42") == 42
    with pytest.raises(RuntimeEventError) as caught:
        parse_last_event_id("4 OR 1=1")

    assert caught.value.code == "invalid_last_event_id"


def test_stream_replays_after_last_event_id() -> None:
    async def scenario() -> None:
        bus = RuntimeEventBus()
        bus.publish("turn.started", payload={"number": 1})
        bus.publish("turn.stage", payload={"number": 2})
        stream = bus.stream(last_event_id="1", heartbeat_seconds=0.1)
        try:
            frame = await asyncio.wait_for(stream.__anext__(), timeout=1)
        finally:
            await stream.aclose()

        assert frame.startswith("id: 2\nevent: turn.stage\n")
        assert _sse_data(frame)["payload"] == {"number": 2}

    asyncio.run(scenario())


def test_new_stream_starts_after_current_tail() -> None:
    async def scenario() -> None:
        bus = RuntimeEventBus()
        bus.publish("turn.started", payload={"number": 1})
        stream = bus.stream(heartbeat_seconds=0.5)
        pending = asyncio.create_task(stream.__anext__())
        await asyncio.sleep(0)
        bus.publish("turn.finished", payload={"number": 2})
        try:
            frame = await asyncio.wait_for(pending, timeout=1)
        finally:
            await stream.aclose()

        assert frame.startswith("id: 2\nevent: turn.finished\n")

    asyncio.run(scenario())


def test_stream_emits_resync_required_when_ring_has_a_gap() -> None:
    async def scenario() -> None:
        bus = RuntimeEventBus(capacity=2)
        for index in range(3):
            bus.publish("turn.stage", payload={"index": index})
        stream = bus.stream(last_event_id=0, heartbeat_seconds=0.1)
        try:
            frame = await asyncio.wait_for(stream.__anext__(), timeout=1)
        finally:
            await stream.aclose()

        assert "event: resync_required" in frame
        assert _sse_data(frame) == {
            "requested_last_event_id": 0,
            "oldest_id": 2,
            "latest_id": 3,
        }

    asyncio.run(scenario())


def test_stream_emits_heartbeat_after_fifteen_second_interval_contract() -> None:
    async def scenario() -> None:
        bus = RuntimeEventBus()
        stream = bus.stream(heartbeat_seconds=0.01)
        try:
            frame = await asyncio.wait_for(stream.__anext__(), timeout=1)
        finally:
            await stream.aclose()

        assert frame == format_sse_heartbeat() == ": heartbeat\n\n"

    asyncio.run(scenario())
