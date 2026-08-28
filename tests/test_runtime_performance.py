from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from ._loader import load_personification_module
from .test_webui_smoke import _build_client, _login_as_admin, _runtime_context  # noqa: F401


metrics = load_personification_module("plugin.personification.core.metrics")
runtime_performance = load_personification_module("plugin.personification.core.runtime_performance")
task_supervisor_module = load_personification_module(
    "plugin.personification.core.runtime_task_supervisor"
)
v2_services = load_personification_module("plugin.personification.webui.v2_services")


@pytest.fixture(autouse=True)
def _reset_runtime_metrics():
    metrics.reset_metrics()
    runtime_performance.reset_for_testing()
    yield
    metrics.reset_metrics()
    runtime_performance.reset_for_testing()


def test_metrics_are_bounded_and_keep_recent_percentiles() -> None:
    for value in range(200):
        metrics.record_timing("reply", float(value), session=str(value))
    for value in range(200):
        metrics.record_counter("tool", tool=str(value))
    snapshot = metrics.snapshot_metrics()
    assert snapshot["series"]["used"] <= 256
    assert snapshot["series"]["overflow_total"] > 0

    metrics.reset_metrics()
    for value in range(200):
        metrics.record_timing("reply", float(value))
    timing = metrics.snapshot_metrics()["timings"][0]
    assert timing["count"] == 200
    assert timing["recent_count"] == 128
    assert timing["recent_max_ms"] == 199.0
    assert timing["p50_ms"] == 135.0
    assert timing["p95_ms"] == 193.0


def test_task_supervisor_deduplicates_reports_failure_and_shuts_down() -> None:
    async def _run() -> None:
        supervisor = task_supervisor_module.RuntimeTaskSupervisor()
        supervisor.configure(logger=SimpleNamespace(warning=lambda *_a, **_k: None))
        gate = asyncio.Event()
        calls = 0

        async def _waiter() -> None:
            nonlocal calls
            calls += 1
            await gate.wait()

        first = supervisor.start("waiter", _waiter)
        second = supervisor.start("waiter", _waiter)
        assert first is second
        await asyncio.sleep(0)
        assert calls == 1
        gate.set()
        await first
        await asyncio.sleep(0)
        assert supervisor.snapshot()["supervised"][0]["state"] == "completed"

        async def _boom() -> None:
            raise RuntimeError("secret failure details")

        await asyncio.gather(supervisor.start("boom", _boom), return_exceptions=True)
        await asyncio.sleep(0)
        failure = next(
            item for item in supervisor.snapshot()["supervised"] if item["name"] == "boom"
        )
        assert failure["state"] == "failed"
        assert failure["error_code"] == "runtime_task_failed"
        assert "secret failure details" not in json.dumps(failure)

        async def _forever() -> None:
            await asyncio.Event().wait()

        supervisor.start("forever", _forever)
        await asyncio.sleep(0)
        await supervisor.shutdown(timeout=0.5)
        assert next(
            item for item in supervisor.snapshot()["supervised"] if item["name"] == "forever"
        )["state"] == "cancelled"

    asyncio.run(_run())


def test_event_loop_sampler_and_runtime_snapshot_are_bounded() -> None:
    async def _sample() -> None:
        task = asyncio.create_task(runtime_performance.sample_event_loop_lag(interval=0.01))
        await asyncio.sleep(0.15)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(_sample())
    runtime_performance.register_reply_reporter(
        lambda: {"active": 2, "waiting": 3, "session_gates": 4, "secret": "ignored"}
    )
    runtime_performance.register_cache_reporter(
        "test_cache", lambda: {"entries": 5, "limit": 10, "evictions": 1, "path": "ignored"}
    )
    snapshot = runtime_performance.snapshot()
    assert snapshot["schema_version"] == 1
    assert snapshot["event_loop"]["samples"] > 0
    assert snapshot["reply"] == {
        "active": 2,
        "waiting": 3,
        "session_gates": 4,
        "admission_waiting_turns": 3,
        "buffered_sessions": 0,
        "buffered_messages": 0,
        "processing_buffer_sessions": 0,
        "oldest_buffer_age_ms": 0,
        "next_buffer_fire_ms": 0,
    }
    assert next(item for item in snapshot["caches"] if item["name"] == "test_cache") == {
        "name": "test_cache",
        "entries": 5,
        "limit": 10,
        "evictions": 1,
    }
    rendered = json.dumps(snapshot, ensure_ascii=False).lower()
    assert "cookie" not in rendered
    assert "ignored" not in rendered


def test_performance_runtime_route_requires_admin(_runtime_context) -> None:
    client = _build_client(_runtime_context)
    assert client.get("/personification/api/performance/runtime").status_code in {401, 403}
    _login_as_admin(client, _runtime_context)
    response = client.get("/personification/api/performance/runtime")
    assert response.status_code == 200
    assert response.json()["schema_version"] == 1


def test_agent_snapshot_maps_buffer_and_admission_fields_without_reporter_leaks(monkeypatch) -> None:
    monkeypatch.setattr(v2_services.runtime_performance, "snapshot", lambda: {"reply": {
        "waiting": 7, "buffered_sessions": 2, "buffered_messages": 5,
        "processing_buffer_sessions": 1, "oldest_buffer_age_ms": 99,
        "next_buffer_fire_ms": 123, "body": "聊天正文", "qq": "123456",
        "path": r"C:\secret\x", "secret": "token",
    }, "event_loop": {}, "process": {}, "tasks": {}, "caches": []})
    monkeypatch.setattr(v2_services.reply_turn_trace, "query_recent", lambda **_kwargs: [])
    monkeypatch.setattr(v2_services, "list_bot_identities", lambda *_args, **_kwargs: asyncio.sleep(0, result=[]))
    monkeypatch.setattr(v2_services, "load_inner_state", lambda *_args, **_kwargs: asyncio.sleep(0, result={}))
    runtime = SimpleNamespace(plugin_config=SimpleNamespace(personification_response_timeout=120, personification_agent_enabled=True))
    snapshot = asyncio.run(v2_services.build_agent_runtime_snapshot(runtime))
    assert snapshot["waiting_turns"] == 7
    assert snapshot["admission_waiting_turns"] == 7
    assert {key: snapshot[key] for key in ("buffered_sessions", "buffered_messages", "processing_buffer_sessions", "oldest_buffer_age_ms", "next_buffer_fire_ms")} == {"buffered_sessions": 2, "buffered_messages": 5, "processing_buffer_sessions": 1, "oldest_buffer_age_ms": 99, "next_buffer_fire_ms": 123}
    rendered = repr(snapshot)
    for forbidden in ("聊天正文", "123456", r"C:\secret", "token"):
        assert forbidden not in rendered
