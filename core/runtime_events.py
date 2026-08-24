from __future__ import annotations

import asyncio
import copy
import json
import math
import re
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable, Mapping

from .sensitive_data import sanitize_object, sanitize_text


RUNTIME_EVENT_CAPACITY = 1000
RUNTIME_EVENT_HEARTBEAT_SECONDS = 15.0

_TOPIC_UNSAFE_RE = re.compile(r"[^A-Za-z0-9_.:-]+")
_KEY_SEPARATOR_RE = re.compile(r"[^a-z0-9]+")
_FORBIDDEN_EVENT_KEYS = {
    "args",
    "arguments",
    "chain_of_thought",
    "completion",
    "content",
    "final_reply",
    "hidden_reasoning",
    "incoming_message",
    "incoming_text",
    "kwargs",
    "messages",
    "model_input",
    "model_messages",
    "model_output",
    "outgoing_message",
    "outgoing_text",
    "parameters",
    "prompt",
    "provider_body",
    "provider_request",
    "provider_response",
    "raw",
    "raw_arguments",
    "raw_completion",
    "raw_media",
    "raw_request",
    "raw_response",
    "raw_result",
    "reasoning_content",
    "request",
    "request_body",
    "response",
    "response_body",
    "system_prompt",
    "thought",
    "thoughts",
    "tool_args",
    "tool_arguments",
    "tool_output",
    "tool_outputs",
    "tool_result",
    "tool_results",
    "user_message",
}
_FORBIDDEN_EVENT_KEYS_COMPACT = {key.replace("_", "") for key in _FORBIDDEN_EVENT_KEYS}


class RuntimeEventError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = str(code)


def _normalized_key(value: Any) -> str:
    return _KEY_SEPARATOR_RE.sub("_", str(value or "").strip().lower()).strip("_")


def _is_forbidden_event_key(value: Any) -> bool:
    normalized = _normalized_key(value)
    compact = normalized.replace("_", "")
    return (
        normalized in _FORBIDDEN_EVENT_KEYS
        or compact in _FORBIDDEN_EVENT_KEYS_COMPACT
        or normalized.startswith("raw_")
        or normalized.endswith("_raw")
        or "prompt" in normalized
    )


def _remove_forbidden_event_fields(value: Any, *, depth: int = 0) -> Any:
    if depth > 5:
        return "<nested>"
    if isinstance(value, Mapping):
        output: dict[str, Any] = {}
        for key, item in list(value.items())[:80]:
            key_text = str(key or "")[:120]
            output[key_text] = (
                "***"
                if _is_forbidden_event_key(key_text)
                else _remove_forbidden_event_fields(item, depth=depth + 1)
            )
        return output
    if isinstance(value, (list, tuple, set)):
        return [
            _remove_forbidden_event_fields(item, depth=depth + 1)
            for item in list(value)[:80]
        ]
    return value


def redact_runtime_payload(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return a bounded payload safe for an administrator event stream."""

    if payload is None:
        return {}
    if not isinstance(payload, Mapping):
        raise RuntimeEventError("invalid_event_payload", "event payload must be an object")
    sanitized = sanitize_object(_remove_forbidden_event_fields(payload))
    return sanitized if isinstance(sanitized, dict) else {}


def _normalize_topic(topic: Any) -> str:
    normalized = _TOPIC_UNSAFE_RE.sub("_", str(topic or "").strip())[:128].strip("_")
    if not normalized:
        raise RuntimeEventError("invalid_event_topic", "event topic must not be empty")
    return normalized


def parse_last_event_id(value: Any) -> int:
    if value is None or value == "":
        return 0
    if isinstance(value, bool):
        raise RuntimeEventError("invalid_last_event_id", "Last-Event-ID must be a non-negative integer")
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeEventError(
            "invalid_last_event_id",
            "Last-Event-ID must be a non-negative integer",
        ) from exc
    if normalized < 0:
        raise RuntimeEventError(
            "invalid_last_event_id",
            "Last-Event-ID must be a non-negative integer",
        )
    return normalized


@dataclass(frozen=True)
class RuntimeEvent:
    id: int
    ts: float
    topic: str
    payload: dict[str, Any]
    trace_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        output: dict[str, Any] = {
            "id": self.id,
            "ts": self.ts,
            "topic": self.topic,
            "payload": copy.deepcopy(self.payload),
        }
        if self.trace_id:
            output["trace_id"] = self.trace_id
        return output


@dataclass(frozen=True)
class EventReplay:
    events: tuple[RuntimeEvent, ...]
    resync_required: bool
    requested_last_event_id: int
    oldest_id: int
    latest_id: int


@dataclass(frozen=True)
class _Subscriber:
    loop: asyncio.AbstractEventLoop
    signal: asyncio.Event


def _copy_event(event: RuntimeEvent) -> RuntimeEvent:
    return RuntimeEvent(
        id=event.id,
        ts=event.ts,
        topic=event.topic,
        trace_id=event.trace_id,
        payload=copy.deepcopy(event.payload),
    )


def format_sse_event(event: RuntimeEvent) -> str:
    data = json.dumps(event.to_dict(), ensure_ascii=False, separators=(",", ":"))
    return f"id: {event.id}\nevent: {event.topic}\ndata: {data}\n\n"


def format_resync_required(replay: EventReplay) -> str:
    payload = {
        "requested_last_event_id": replay.requested_last_event_id,
        "oldest_id": replay.oldest_id,
        "latest_id": replay.latest_id,
    }
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    id_line = f"id: {replay.latest_id}\n" if replay.latest_id > 0 else ""
    return f"{id_line}event: resync_required\ndata: {data}\n\n"


def format_sse_heartbeat() -> str:
    return ": heartbeat\n\n"


class RuntimeEventBus:
    """Thread-safe in-memory runtime event bus with asynchronous SSE replay."""

    def __init__(
        self,
        *,
        capacity: int = RUNTIME_EVENT_CAPACITY,
        heartbeat_seconds: float = RUNTIME_EVENT_HEARTBEAT_SECONDS,
        time_source: Callable[[], float] = time.time,
    ) -> None:
        if isinstance(capacity, bool):
            raise RuntimeEventError("invalid_event_capacity", "event capacity must be an integer")
        try:
            normalized_capacity = int(capacity)
        except (TypeError, ValueError) as exc:
            raise RuntimeEventError(
                "invalid_event_capacity",
                "event capacity must be an integer",
            ) from exc
        if normalized_capacity < 1 or normalized_capacity > RUNTIME_EVENT_CAPACITY:
            raise RuntimeEventError(
                "invalid_event_capacity",
                f"event capacity must be between 1 and {RUNTIME_EVENT_CAPACITY}",
            )
        try:
            normalized_heartbeat = float(heartbeat_seconds)
        except (TypeError, ValueError) as exc:
            raise RuntimeEventError(
                "invalid_heartbeat_seconds",
                "heartbeat interval must be positive",
            ) from exc
        if normalized_heartbeat <= 0:
            raise RuntimeEventError(
                "invalid_heartbeat_seconds",
                "heartbeat interval must be positive",
            )
        if not callable(time_source):
            raise RuntimeEventError("invalid_time_source", "time source must be callable")

        self._capacity = normalized_capacity
        self._heartbeat_seconds = normalized_heartbeat
        self._time_source = time_source
        self._events: deque[RuntimeEvent] = deque(maxlen=normalized_capacity)
        self._latest_id = 0
        self._lock = threading.RLock()
        self._subscribers: dict[int, _Subscriber] = {}
        self._next_subscriber_id = 1

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def heartbeat_seconds(self) -> float:
        return self._heartbeat_seconds

    @property
    def latest_id(self) -> int:
        with self._lock:
            return self._latest_id

    @property
    def oldest_id(self) -> int:
        with self._lock:
            return self._events[0].id if self._events else 0

    def __len__(self) -> int:
        with self._lock:
            return len(self._events)

    def publish(
        self,
        topic: Any,
        *,
        payload: Mapping[str, Any] | None = None,
        trace_id: Any = "",
    ) -> RuntimeEvent:
        safe_topic = _normalize_topic(topic)
        safe_payload = redact_runtime_payload(payload)
        safe_trace_id = sanitize_text(trace_id, limit=128).strip()[:128]
        try:
            timestamp = float(self._time_source())
        except (TypeError, ValueError) as exc:
            raise RuntimeEventError("invalid_event_timestamp", "time source returned an invalid value") from exc
        if not math.isfinite(timestamp):
            raise RuntimeEventError("invalid_event_timestamp", "time source returned an invalid value")

        with self._lock:
            self._latest_id += 1
            stored = RuntimeEvent(
                id=self._latest_id,
                ts=timestamp,
                topic=safe_topic,
                trace_id=safe_trace_id,
                payload=safe_payload,
            )
            self._events.append(stored)
            subscribers = tuple(self._subscribers.items())

        stale_subscribers: list[int] = []
        for subscriber_id, subscriber in subscribers:
            try:
                subscriber.loop.call_soon_threadsafe(subscriber.signal.set)
            except RuntimeError:
                stale_subscribers.append(subscriber_id)
        if stale_subscribers:
            with self._lock:
                for subscriber_id in stale_subscribers:
                    self._subscribers.pop(subscriber_id, None)
        return _copy_event(stored)

    def replay(self, last_event_id: Any = 0) -> EventReplay:
        requested = parse_last_event_id(last_event_id)
        with self._lock:
            oldest = self._events[0].id if self._events else 0
            latest = self._latest_id
            stale = requested > latest or bool(self._events and requested < oldest - 1)
            selected = () if stale else tuple(
                _copy_event(event) for event in self._events if event.id > requested
            )
        return EventReplay(
            events=selected,
            resync_required=stale,
            requested_last_event_id=requested,
            oldest_id=oldest,
            latest_id=latest,
        )

    def _subscribe(self, loop: asyncio.AbstractEventLoop) -> tuple[int, asyncio.Event]:
        signal = asyncio.Event()
        with self._lock:
            subscriber_id = self._next_subscriber_id
            self._next_subscriber_id += 1
            self._subscribers[subscriber_id] = _Subscriber(loop=loop, signal=signal)
        return subscriber_id, signal

    def _unsubscribe(self, subscriber_id: int) -> None:
        with self._lock:
            self._subscribers.pop(subscriber_id, None)

    async def stream(
        self,
        *,
        last_event_id: Any = None,
        heartbeat_seconds: float | None = None,
    ) -> AsyncIterator[str]:
        """Yield encoded SSE frames.

        A new client without ``Last-Event-ID`` starts after the current tail;
        REST remains the source of the initial snapshot. Reconnecting clients
        replay retained events, or receive ``resync_required`` when a gap can no
        longer be served from the ring.
        """

        if heartbeat_seconds is None:
            heartbeat = self._heartbeat_seconds
        else:
            try:
                heartbeat = float(heartbeat_seconds)
            except (TypeError, ValueError) as exc:
                raise RuntimeEventError(
                    "invalid_heartbeat_seconds",
                    "heartbeat interval must be positive",
                ) from exc
            if heartbeat <= 0:
                raise RuntimeEventError(
                    "invalid_heartbeat_seconds",
                    "heartbeat interval must be positive",
                )

        cursor = self.latest_id if last_event_id is None or last_event_id == "" else parse_last_event_id(last_event_id)
        loop = asyncio.get_running_loop()
        subscriber_id, signal = self._subscribe(loop)
        try:
            while True:
                replay = self.replay(cursor)
                if replay.resync_required:
                    yield format_resync_required(replay)
                    cursor = replay.latest_id
                    continue
                if replay.events:
                    for event in replay.events:
                        yield format_sse_event(event)
                        cursor = event.id
                    continue

                signal.clear()
                replay = self.replay(cursor)
                if replay.resync_required or replay.events:
                    continue
                try:
                    await asyncio.wait_for(signal.wait(), timeout=heartbeat)
                except asyncio.TimeoutError:
                    yield format_sse_heartbeat()
        finally:
            self._unsubscribe(subscriber_id)


_DEFAULT_RUNTIME_EVENT_BUS = RuntimeEventBus()


def get_runtime_event_bus() -> RuntimeEventBus:
    return _DEFAULT_RUNTIME_EVENT_BUS


def publish_runtime_event(
    topic: Any,
    *,
    payload: Mapping[str, Any] | None = None,
    trace_id: Any = "",
) -> RuntimeEvent:
    return _DEFAULT_RUNTIME_EVENT_BUS.publish(
        topic,
        payload=payload,
        trace_id=trace_id,
    )


__all__ = [
    "EventReplay",
    "RUNTIME_EVENT_CAPACITY",
    "RUNTIME_EVENT_HEARTBEAT_SECONDS",
    "RuntimeEvent",
    "RuntimeEventBus",
    "RuntimeEventError",
    "format_resync_required",
    "format_sse_event",
    "format_sse_heartbeat",
    "get_runtime_event_bus",
    "parse_last_event_id",
    "publish_runtime_event",
    "redact_runtime_payload",
]
