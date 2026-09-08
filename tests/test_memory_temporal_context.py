from __future__ import annotations

import asyncio
import json
import sqlite3
from contextlib import contextmanager
from types import SimpleNamespace

from ._loader import load_personification_module

context = load_personification_module("plugin.personification.core.memory_context")
temporal = load_personification_module("plugin.personification.core.temporal_memory")
session = load_personification_module("plugin.personification.core.session_store")
gate = load_personification_module("plugin.personification.core.memory_recall_gate")


def wire(monkeypatch, tmp_path):
    pending = load_personification_module("plugin.personification.flows.social_intelligence.pending_topics")
    monkeypatch.setattr(pending, "list_pending_topics", lambda: [])
    path = tmp_path / "isolated.db"
    @contextmanager
    def connect():
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()
    monkeypatch.setattr(temporal, "connect_sync", connect)
    monkeypatch.setattr(session, "connect_sync", connect)
    with connect() as conn:
        conn.execute("CREATE TABLE session_messages (id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT, is_summary INTEGER DEFAULT 0, timestamp REAL, metadata TEXT DEFAULT '{}')")
    return connect


def test_history_dates_and_summary_survive_both_projections():
    messages = [{"id": 1, "role": "user", "content": "今天下午培训结束", "timestamp": 1788753600},
                {"id": 2, "role": "system", "content": "已确认假期", "is_summary": True, "timestamp": 1788753601}]
    normal = context.project_history(messages)
    yaml = context.render_history(messages)
    assert normal[1]["role"] == "user"
    assert "history_summary" in yaml and "已确认假期" in yaml
    assert "2026-09-07" in normal[0]["content"]
    moved = [{**m, "timestamp": m["timestamp"] + 86400 * 7} for m in messages]
    assert context.render_history(moved) != yaml
    assert context.project_history(normal) == normal
    assert messages[1]["role"] == "system"


def test_state_correction_versions_and_scope_isolation(monkeypatch, tmp_path):
    connect = wire(monkeypatch, tmp_path)
    scope = dict(platform="onebot", bot_id="bot", user_id="alice", group_id="")
    msgs = [{"id": 1, "role": "user", "content": "明天培训", "timestamp": 100},
            {"id": 2, "role": "user", "content": "取消了", "timestamp": 200}]
    temporal._commit(scope, [dict(statement="明天培训", status="planned", source_ids=[1])], [], msgs)
    old = temporal.load_current_states(scope)
    temporal._commit(scope, [dict(state_id=old[0]["state_id"], statement="培训取消", status="cancelled", source_ids=[2])], old, msgs)
    new = temporal.load_current_states(scope)
    assert len(new) == 1 and new[0]["status"] == "cancelled" and new[0]["revision"] == 2
    assert temporal.load_current_states({**scope, "bot_id": "other"}) == []
    assert temporal.load_current_states({**scope, "user_id": "bob"}) == []
    with connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM memory_current_states").fetchone()[0] == 2
    # Stale simultaneous turn cannot undo the correction.
    temporal._commit(scope, [dict(state_id=old[0]["state_id"], statement="仍培训", status="planned", source_ids=[1])], old, msgs)
    assert temporal.load_current_states(scope) == new


def test_unbound_or_assistant_only_confirmation_rejected(monkeypatch, tmp_path):
    wire(monkeypatch, tmp_path)
    scope = dict(platform="onebot", bot_id="bot", user_id="alice", group_id="")
    msgs = [{"id": 1, "role": "assistant", "content": "你已到家", "timestamp": 100}]
    temporal._commit(scope, [dict(statement="到家", status="confirmed", source_ids=[1]),
                              dict(statement="到家", status="confirmed", source_ids=[999])], [], msgs)
    assert temporal.load_current_states(scope) == []


def test_private_gate_requires_matching_owner():
    record = dict(permission_type="private_fact", user_id="alice")
    assert gate._hard_filter(record, now=0, private_owner_id="alice")[0]
    assert not gate._hard_filter(record, now=0, private_owner_id="bob")[0]
    assert not gate._hard_filter(record, now=0)[0]


def test_archive_raw_evidence_retrievable_after_compaction(monkeypatch, tmp_path):
    connect = wire(monkeypatch, tmp_path)
    with connect() as conn:
        for n in range(1, 5):
            conn.execute("INSERT INTO session_messages VALUES (?,?,?,?,?,?,?)",
                         (n, "private_a", "user", json.dumps(f"event-{n}"), 0, 100+n, "{}"))
        conn.commit()
    assert session._replace_history_with_summary_sync("private_a", "summary", candidate_ids=[1,2,3], cutoff_id=3, keep_boundary_timestamp=104)
    restored = session.get_recent_session_candidates("private_a", limit=10, days=0)
    assert [x["content"] for x in restored if not x["is_summary"]] == ["event-1", "event-2", "event-3", "event-4"]
    assert session.get_recent_session_candidates("private_b", limit=10, days=0) == []
    session.clear_session_history("private_a")
    assert session.get_recent_session_candidates("private_a", limit=10, days=0) == []


def test_shared_recall_uses_context_and_transport_identity(monkeypatch, tmp_path):
    wire(monkeypatch, tmp_path)
    captured = {}
    async def recall(**kwargs):
        captured.update(kwargs)
        return []
    runtime = SimpleNamespace(plugin_config=SimpleNamespace(), memory_store=SimpleNamespace(arecall_memories=recall))
    messages = [{"role": "user", "id": 1, "content": "明天开始假期", "timestamp": 1788753600},
                {"role": "user", "id": 2, "content": "早上好", "timestamp": 1788840000}]
    result = asyncio.run(context.prepare_memory_context(runtime=runtime, event=SimpleNamespace(user_id="alice"),
                        bot=SimpleNamespace(self_id="bot"), messages=messages))
    assert captured["bot_id"] == "bot" and captured["user_id"] == "alice"
    assert "假期" in captured["query"] and "早上好" in captured["query"]
    assert result.status == "no_hit"


def test_archive_keyword_query_searches_beyond_recent_window_and_scope(monkeypatch, tmp_path):
    connect = wire(monkeypatch, tmp_path)
    with connect() as conn:
        for n in range(1, 600):
            text = "rare_event" if n == 1 else "ordinary"
            conn.execute("INSERT INTO session_messages VALUES (?,?,?,?,?,?,?)",
                (n, "private_a", "user", json.dumps(text), 0, n,
                 json.dumps({"platform": "onebot", "bot_id": "b1"})))
        conn.execute("INSERT INTO session_messages VALUES (?,?,?,?,?,?,?)",
                (601, "private_a", "user", json.dumps("rare_event other bot"), 0, 601,
                 json.dumps({"platform": "onebot", "bot_id": "b2"})))
        conn.commit()
    result = session.get_recent_session_candidates("private_a", limit=12, days=0,
                platform="onebot", bot_id="b1", query_terms=("rare_event",))
    assert [row["id"] for row in result] == [1]
    assert session.get_recent_session_candidates("private_a", limit=12, days=0,
                platform="onebot", bot_id="b3", query_terms=("rare_event",)) == []


def test_state_correction_supersedes_and_preserves_real_simulation_boundary(monkeypatch, tmp_path):
    wire(monkeypatch, tmp_path)
    scope = dict(platform="onebot", bot_id="b", user_id="u", group_id="")
    messages = [{"id": 1, "role": "user", "content": "明天出行", "timestamp": 1},
                {"id": 2, "role": "user", "content": "取消", "timestamp": 2}]
    temporal._commit(scope, [dict(statement="出行计划", status="planned", source_ids=[1])], [], messages)
    prior = temporal.load_current_states(scope)
    temporal._commit(scope, [dict(statement="计划取消", status="cancelled", source_ids=[2], supersedes=[prior[0]["state_id"]])], prior, messages)
    active = temporal.load_current_states(scope)
    assert len(active) == 1 and active[0]["status"] == "cancelled"
    temporal._commit(scope, [dict(state_id=active[0]["state_id"], statement="模拟取消", layer="simulated", status="cancelled", source_ids=[2])], active, messages)
    assert temporal.load_current_states(scope) == active


def test_state_api_cancels_only_bound_pending_topic(monkeypatch, tmp_path):
    connect = wire(monkeypatch, tmp_path)
    pending = load_personification_module("plugin.personification.flows.social_intelligence.pending_topics")
    scope = dict(platform="onebot", bot_id="b", user_id="u", group_id="")
    monkeypatch.setattr(pending, "list_pending_topics", lambda: [{**scope, "topic_id": "bound"}, {**scope, "bot_id": "other", "topic_id": "foreign"}])
    cancelled = []
    monkeypatch.setattr(pending, "mark_skipped", cancelled.append)
    async def call(**kwargs):
        assert "cancel_pending" in kwargs["messages"][0]["content"]
        assert "foreign" not in kwargs["messages"][1]["content"]
        return SimpleNamespace(content=json.dumps({"updates": [], "cancel_pending": [
            {"topic_id": "bound", "source_ids": [1]}, {"topic_id": "foreign", "source_ids": [1]}]}))
    with connect() as conn:
        conn.execute("INSERT INTO session_messages VALUES (1,'private_u','user','cancel',0,1,'{}')")
        conn.commit()
    asyncio.run(temporal.update_current_states(scope=scope, messages=[{"id": 1, "role": "user", "content": "取消", "timestamp": 1}],
                caller=SimpleNamespace(chat_with_tools=call), timezone="Asia/Shanghai", timeout=1))
    assert cancelled == ["bound"]
