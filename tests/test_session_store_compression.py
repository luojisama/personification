from __future__ import annotations

import asyncio
import sqlite3
from contextlib import contextmanager
from types import SimpleNamespace

from ._loader import load_personification_module


store = load_personification_module("plugin.personification.core.session_store")


def _wire_db(monkeypatch, tmp_path):
    path = tmp_path / "session.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE session_messages (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, role TEXT, content TEXT, is_summary INTEGER DEFAULT 0, timestamp REAL, metadata TEXT DEFAULT '{}')")
    @contextmanager
    def connect():
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()
    monkeypatch.setattr(store, "connect_sync", connect)
    return path


def test_snapshot_commit_preserves_append_and_replaces_old_summaries(monkeypatch, tmp_path) -> None:
    _wire_db(monkeypatch, tmp_path)
    for index in range(3):
        store.append_session_message("group_x", "user", f"old-{index}")
    snapshot = store._fetch_session_messages_sync("group_x")
    store.append_session_message("group_x", "user", "new-arrival")
    assert store._replace_history_with_summary_sync(
        "group_x", "summary", candidate_ids=[row["id"] for row in snapshot],
        cutoff_id=snapshot[-1]["id"], keep_boundary_timestamp=snapshot[-1]["timestamp"],
    )
    rows = store._fetch_session_messages_sync("group_x")
    assert [row["content"] for row in rows if not row["is_summary"]] == ["new-arrival"]
    assert len([row for row in rows if row["is_summary"]]) == 1


def test_snapshot_commit_after_clear_does_not_resurrect_history(monkeypatch, tmp_path) -> None:
    _wire_db(monkeypatch, tmp_path)
    store.append_session_message("group_x", "user", "old")
    snapshot = store._fetch_session_messages_sync("group_x")
    store.clear_session_history("group_x")
    assert not store._replace_history_with_summary_sync("group_x", "late", candidate_ids=[snapshot[0]["id"]], cutoff_id=snapshot[0]["id"], keep_boundary_timestamp=snapshot[0]["timestamp"])
    assert store._fetch_session_messages_sync("group_x") == []


def test_session_metadata_cannot_shadow_authoritative_message_envelope(monkeypatch, tmp_path) -> None:
    path = _wire_db(monkeypatch, tmp_path)
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            INSERT INTO session_messages(session_id, role, content, is_summary, timestamp, metadata)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "group_x",
                "user",
                '"real-content"',
                0,
                123.5,
                '{"id":999,"role":"assistant","content":"spoofed",'
                '"timestamp":999.0,"is_summary":true,"source_kind":"qq_user"}',
            ),
        )
        conn.commit()

    row = store._fetch_session_messages_sync("group_x")[0]

    assert row["id"] == 1
    assert row["role"] == "user"
    assert row["content"] == "real-content"
    assert row["timestamp"] == 123.5
    assert row["is_summary"] is False
    assert row["source_kind"] == "qq_user"


def test_append_session_message_drops_reserved_metadata_keys(monkeypatch, tmp_path) -> None:
    _wire_db(monkeypatch, tmp_path)

    store.append_session_message(
        "group_x",
        "user",
        "real-content",
        id=999,
        timestamp=999.0,
        is_summary=True,
        source_kind="qq_user",
    )
    row = store._fetch_session_messages_sync("group_x")[0]

    assert row["id"] == 1
    assert row["timestamp"] != 999.0
    assert row["is_summary"] is False
    assert row["source_kind"] == "qq_user"


def test_compress_timeout_retries_without_deleting_raw(monkeypatch, tmp_path) -> None:
    _wire_db(monkeypatch, tmp_path)
    store._plugin_config = SimpleNamespace(personification_compress_threshold=99, personification_compress_keep_recent=0, personification_compress_timeout_seconds=.01)
    for index in range(2):
        store.append_session_message("group_x", "user", f"raw-{index}")
    class Caller:
        async def chat_with_tools(self, **_kwargs):
            await asyncio.sleep(1)
    scheduled = []
    store._plugin_config.personification_compress_threshold = 2
    monkeypatch.setattr(store, "_compress_tool_caller", Caller())
    monkeypatch.setattr(store, "_schedule_compress_retry", lambda key, attempt: scheduled.append((key, attempt)))
    asyncio.run(store._run_compress("group_x"))
    assert len(store._fetch_session_messages_sync("group_x")) == 2
    assert scheduled == [("group_x", 1)]


def test_retry_delays_stop_after_third_attempt(monkeypatch) -> None:
    waits: list[float] = []
    scheduled: list[str] = []
    async def sleep(delay: float) -> None:
        waits.append(delay)
    monkeypatch.setattr(store, "_compress_sleep", sleep)
    monkeypatch.setattr(store, "_schedule_compress", scheduled.append)
    async def run() -> None:
        for attempt in (1, 2, 3, 4):
            store._schedule_compress_retry("group_retry", attempt)
            await asyncio.sleep(0)
    asyncio.run(run())
    assert waits == [30.0, 120.0, 600.0]
    assert scheduled == ["group_retry", "group_retry", "group_retry"]


def test_new_append_restarts_exhausted_retry_cycle_and_clear_forgets_failures(monkeypatch, tmp_path) -> None:
    _wire_db(monkeypatch, tmp_path)
    store._plugin_config = SimpleNamespace(personification_compress_threshold=1)
    store._compress_failures.clear()
    store._compress_failures["group_retry"] = 3
    scheduled: list[str] = []
    monkeypatch.setattr(store, "_schedule_compress", scheduled.append)
    pending_retry = object()
    store._compress_retry_tasks["group_retry"] = pending_retry
    store.append_session_message("group_retry", "user", "new")
    # Count 3 means the final 600-second retry is still outstanding: a new
    # message must not reset it or create a duplicate generation timer.
    assert store._compress_failures["group_retry"] == 3
    assert scheduled == []
    store._compress_retry_tasks.pop("group_retry", None)
    store._compress_failures["group_retry"] = 4
    store.append_session_message("group_retry", "user", "next-cycle")
    assert "group_retry" not in store._compress_failures
    assert scheduled == ["group_retry"]
    store._compress_failures["group_retry"] = 2
    store.clear_session_history("group_retry")
    assert "group_retry" not in store._compress_failures


def test_keep_is_constrained_by_history_len_and_old_summaries_deduped(monkeypatch, tmp_path) -> None:
    _wire_db(monkeypatch, tmp_path)
    store._plugin_config = SimpleNamespace(personification_compress_threshold=99, personification_compress_keep_recent=99, personification_history_len=40, personification_compress_timeout_seconds=1)
    for index in range(45):
        store.append_session_message("group_x", "user", f"raw-{index}")
    store._plugin_config.personification_compress_threshold = 2
    class Caller:
        async def chat_with_tools(self, **_kwargs):
            return SimpleNamespace(content="summary")
    store._compress_tool_caller = Caller()
    asyncio.run(store._run_compress("group_x"))
    # threshold is deliberately raised during append; run now compacts to one
    # summary plus at most history_len - 1 raw row.
    rows = store._fetch_session_messages_sync("group_x")
    assert len(rows) <= 40
    assert sum(bool(row["is_summary"]) for row in rows) == 1
    # A second successful run never creates another active summary.
    asyncio.run(store._run_compress("group_x"))
    rows = store._fetch_session_messages_sync("group_x")
    assert sum(bool(row["is_summary"]) for row in rows) == 1


def test_history_target_triggers_before_larger_compress_threshold(monkeypatch, tmp_path) -> None:
    _wire_db(monkeypatch, tmp_path)
    store._plugin_config = SimpleNamespace(personification_compress_threshold=180, personification_compress_keep_recent=0, personification_history_len=40, personification_compress_timeout_seconds=1)
    scheduled: list[str] = []
    monkeypatch.setattr(store, "_schedule_compress", scheduled.append)
    for index in range(40):
        store.append_session_message("group_target", "user", f"raw-{index}")
    assert scheduled == ["group_target"]
    assert store._get_compress_trigger() == 40


def test_empty_and_exception_never_delete_snapshot(monkeypatch, tmp_path) -> None:
    _wire_db(monkeypatch, tmp_path)
    store._plugin_config = SimpleNamespace(personification_compress_threshold=99, personification_compress_keep_recent=0, personification_history_len=40, personification_compress_timeout_seconds=1)
    for index in range(2):
        store.append_session_message("group_x", "user", f"raw-{index}")
    scheduled: list[tuple[str, int]] = []
    store._plugin_config.personification_compress_threshold = 2
    monkeypatch.setattr(store, "_schedule_compress_retry", lambda *args: scheduled.append(args))
    class Empty:
        async def chat_with_tools(self, **_kwargs):
            return SimpleNamespace(content="")
    store._compress_tool_caller = Empty()
    asyncio.run(store._run_compress("group_x"))
    class Broken:
        async def chat_with_tools(self, **_kwargs):
            raise RuntimeError("broken")
    store._compress_tool_caller = Broken()
    asyncio.run(store._run_compress("group_x"))
    assert len(store._fetch_session_messages_sync("group_x")) == 2
    assert len(scheduled) == 2


def test_empty_summary_over_target_only_uses_retry_not_immediate_compress(monkeypatch, tmp_path) -> None:
    _wire_db(monkeypatch, tmp_path)
    store._plugin_config = SimpleNamespace(personification_compress_threshold=99, personification_compress_keep_recent=0, personification_history_len=40, personification_compress_timeout_seconds=1)
    for index in range(41):
        store.append_session_message("group_empty_target", "user", f"raw-{index}")
    store._plugin_config.personification_compress_threshold = 2
    retries: list[tuple[str, int]] = []
    immediate: list[str] = []
    monkeypatch.setattr(store, "_schedule_compress_retry", lambda key, attempt: retries.append((key, attempt)))
    monkeypatch.setattr(store, "_schedule_compress", immediate.append)
    class Empty:
        async def chat_with_tools(self, **_kwargs):
            return SimpleNamespace(content="")
    store._compress_tool_caller = Empty()
    async def run() -> None:
        task = asyncio.create_task(store._run_compress("group_empty_target"))
        store._compress_tasks["group_empty_target"] = task
        await task
    asyncio.run(run())
    assert retries == [("group_empty_target", 1)]
    assert immediate == []


def test_successful_compaction_with_concurrent_append_over_target_schedules_next_pass(monkeypatch, tmp_path) -> None:
    _wire_db(monkeypatch, tmp_path)
    store._plugin_config = SimpleNamespace(personification_compress_threshold=99, personification_compress_keep_recent=39, personification_history_len=40, personification_compress_timeout_seconds=1)
    for index in range(41):
        store.append_session_message("group_success_target", "user", f"raw-{index}")
    started, release = asyncio.Event(), asyncio.Event()
    class Caller:
        async def chat_with_tools(self, **_kwargs):
            started.set(); await release.wait(); return SimpleNamespace(content="summary")
    store._compress_tool_caller = Caller()
    store._plugin_config.personification_compress_threshold = 2
    scheduled: list[str] = []
    monkeypatch.setattr(store, "_schedule_compress", scheduled.append)
    async def run() -> None:
        task = asyncio.create_task(store._run_compress("group_success_target"))
        store._compress_tasks["group_success_target"] = task
        await started.wait()
        store._plugin_config.personification_compress_threshold = 99
        store._plugin_config.personification_history_len = 99
        store.append_session_message("group_success_target", "user", "new-1")
        store.append_session_message("group_success_target", "user", "new-2")
        store._plugin_config.personification_history_len = 40
        release.set(); await task
    asyncio.run(run())
    assert scheduled == ["group_success_target"]


def test_compaction_always_absorbs_legacy_summary_even_when_it_looks_recent(monkeypatch, tmp_path) -> None:
    _wire_db(monkeypatch, tmp_path)
    store._plugin_config = SimpleNamespace(personification_compress_threshold=99, personification_compress_keep_recent=1, personification_history_len=4, personification_compress_timeout_seconds=1)
    for index in range(4):
        store.append_session_message("group_x", "user", f"raw-{index}")
    with store.connect_sync() as conn:
        conn.execute("INSERT INTO session_messages(session_id, role, content, is_summary, timestamp, metadata) VALUES (?, ?, ?, 1, ?, ?)", ("group_x", "system", '"old summary"', 9999999999.0, '{"covered_count": 7, "compacted_through_id": 2}'))
        conn.commit()
    class Caller:
        async def chat_with_tools(self, **_kwargs):
            return SimpleNamespace(content="new summary")
    monkeypatch.setattr(store, "_compress_tool_caller", Caller())
    store._plugin_config.personification_compress_threshold = 2
    asyncio.run(store._run_compress("group_x"))
    rows = store._fetch_session_messages_sync("group_x")
    summaries = [row for row in rows if row["is_summary"]]
    raw = [row for row in rows if not row["is_summary"]]
    assert len(summaries) == 1
    assert [row["content"] for row in raw] == ["raw-3"]
    import json
    with store.connect_sync() as conn:
        raw_metadata = conn.execute("SELECT metadata FROM session_messages WHERE session_id=? AND is_summary=1", ("group_x",)).fetchone()["metadata"]
    metadata = json.loads(raw_metadata)
    assert metadata["covered_count"] == 10
    assert metadata["compacted_through_id"] >= 3
    assert [row["content"] for row in rows] == [summaries[0]["content"], "raw-3"]


def test_async_compress_append_and_clear_do_not_reorder_or_resurrect(monkeypatch, tmp_path) -> None:
    _wire_db(monkeypatch, tmp_path)
    store._plugin_config = SimpleNamespace(personification_compress_threshold=99, personification_compress_keep_recent=0, personification_history_len=4, personification_compress_timeout_seconds=1)
    async def append_case() -> None:
        started, release = asyncio.Event(), asyncio.Event()
        class Caller:
            async def chat_with_tools(self, **_kwargs):
                started.set(); await release.wait(); return SimpleNamespace(content="summary")
        monkeypatch.setattr(store, "_compress_tool_caller", Caller())
        store.append_session_message("group_append", "user", "old-1"); store.append_session_message("group_append", "user", "old-2")
        store._plugin_config.personification_compress_threshold = 2
        task = asyncio.create_task(store._run_compress("group_append")); await started.wait()
        store._plugin_config.personification_compress_threshold = 99
        store.append_session_message("group_append", "user", "new")
        release.set(); await task
        rows = store._fetch_session_messages_sync("group_append")
        assert rows[0]["is_summary"] and rows[1]["content"] == "new"
    async def clear_case() -> None:
        started, release = asyncio.Event(), asyncio.Event()
        class Caller:
            async def chat_with_tools(self, **_kwargs):
                started.set(); await release.wait(); return SimpleNamespace(content="summary")
        monkeypatch.setattr(store, "_compress_tool_caller", Caller())
        store.append_session_message("group_clear", "user", "old-1"); store.append_session_message("group_clear", "user", "old-2")
        store._plugin_config.personification_compress_threshold = 2
        task = asyncio.create_task(store._run_compress("group_clear")); await started.wait()
        store.clear_session_history("group_clear"); release.set(); await task
        assert store._fetch_session_messages_sync("group_clear") == []
    asyncio.run(append_case()); asyncio.run(clear_case())
