from __future__ import annotations

import asyncio
import logging
import sqlite3
from pathlib import Path

import pytest

from ._loader import load_personification_module

traces = load_personification_module("plugin.personification.core.reply_turn_trace")
runtime_events = load_personification_module("plugin.personification.core.runtime_events")


@pytest.fixture
def trace_db(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "trace.db"

    def connect():
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        return conn

    with connect() as conn:
        conn.execute("""CREATE TABLE reply_turn_traces (
            trace_id TEXT PRIMARY KEY, ts REAL, session_type TEXT,
            group_id TEXT, user_id TEXT, stages TEXT, outcome TEXT,
            diagnosis_code TEXT, detail TEXT
        )""")
    monkeypatch.setattr(traces, "connect_sync", connect)
    monkeypatch.setattr(traces, "_TRACE_FAILURE_LOG_TIMES", {})
    bus = runtime_events.RuntimeEventBus()
    monkeypatch.setattr(runtime_events, "_DEFAULT_RUNTIME_EVENT_BUS", bus)
    return connect, bus


def _write(operation: str):
    if operation == "start_trace":
        return traces.start_trace(trace_id="private-trace", group_id="private-group")
    if operation == "record_stage":
        return traces.record_stage(trace_id="private-trace", key="send", detail="private-message")
    return traces.finish_trace(
        trace_id="private-trace", outcome="delivery_unknown",
        detail={"outbound_delivery": "unknown"},
    )


@pytest.mark.parametrize("operation", ["start_trace", "record_stage", "finish_trace"])
def test_missing_sqlite_table_is_best_effort_and_diagnostic(trace_db, operation, caplog):
    connect, bus = trace_db
    with connect() as conn:
        conn.execute("DROP TABLE reply_turn_traces")
    with caplog.at_level(logging.WARNING, logger=traces.__name__):
        result = _write(operation)
        _write(operation)
    assert result == ("private-trace" if operation == "start_trace" else None)
    assert len(caplog.records) == 1
    assert f"operation={operation}" in caplog.text
    assert "error_type=OperationalError" in caplog.text
    assert "private-" not in caplog.text
    assert "reply_turn_traces" not in caplog.text
    assert bus.latest_id == 2
    if operation == "finish_trace":
        assert bus.replay().events[-1].payload["outcome"] == "delivery_unknown"


@pytest.mark.parametrize("operation", ["start_trace", "record_stage", "finish_trace"])
def test_filesystem_failure_does_not_interrupt_trace_api(trace_db, monkeypatch, operation, caplog):
    def unavailable():
        raise PermissionError("private-path private-message")

    monkeypatch.setattr(traces, "connect_sync", unavailable)
    with caplog.at_level(logging.WARNING, logger=traces.__name__):
        _write(operation)
    assert "error_type=PermissionError" in caplog.text
    assert "private-" not in caplog.text


@pytest.mark.parametrize("operation", ["start_trace", "record_stage", "finish_trace"])
@pytest.mark.parametrize("error_type", [RuntimeError, asyncio.CancelledError])
def test_programming_errors_and_cancellation_propagate(trace_db, monkeypatch, operation, error_type):
    def failed():
        raise error_type("unexpected internal failure")

    monkeypatch.setattr(traces, "connect_sync", failed)
    with pytest.raises(error_type):
        _write(operation)
    assert trace_db[1].latest_id == 0


def test_event_publisher_errors_are_not_silently_swallowed(trace_db, monkeypatch):
    def invalid_event(*args, **kwargs):
        raise runtime_events.RuntimeEventError("invalid_event_payload", "bad internal payload")

    monkeypatch.setattr(traces, "publish_runtime_event", invalid_event)
    with pytest.raises(runtime_events.RuntimeEventError):
        _write("start_trace")
    assert traces.get_trace("private-trace") is not None


def test_trace_round_trip_preserves_unknown_and_confirmed_delivery(trace_db):
    traces.start_trace(trace_id="turn", detail={"source": "unit"})
    traces.record_stage(trace_id="turn", key="send", elapsed_ms=12, detail="safe summary")
    traces.finish_trace(trace_id="turn", outcome="delivery_unknown", detail={"outbound_delivery": "unknown"})
    row = traces.get_trace("turn")
    assert row["outcome"] == "delivery_unknown"
    assert row["detail"]["outbound_delivery"] == "unknown"
    assert row["stages"][0]["elapsed_ms"] == 12
    traces.finish_trace(trace_id="turn", outcome="replied", detail={"outbound_delivery": "confirmed"})
    row = traces.get_trace("turn")
    assert row["outcome"] == "replied"
    assert row["detail"]["outbound_delivery"] == "confirmed"
    assert row["detail"]["source"] == "unit"


def test_corrupt_stored_json_recovers_without_masking_row_shape_errors(trace_db):
    connect, _ = trace_db
    traces.start_trace(trace_id="turn")
    with connect() as conn:
        conn.execute("UPDATE reply_turn_traces SET stages='[broken', detail='{broken'")
    traces.record_stage(trace_id="turn", key="recover")
    traces.finish_trace(trace_id="turn", outcome="no_reply")
    row = traces.get_trace("turn")
    assert row["stages"][0]["key"] == "recover"
    assert row["outcome"] == "no_reply"
    with pytest.raises(KeyError):
        traces._row_to_dict({})
