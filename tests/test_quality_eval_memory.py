from __future__ import annotations

from types import SimpleNamespace

from scripts.quality_eval.memory_fixtures import apply_memory_event, build_memory_store


def _config(tmp_path):
    return SimpleNamespace(isolated_data_dir=str(tmp_path))


def _case(case_id: str, *, surface: str = "private", seed_memory=None):
    return {"id": case_id, "surface": surface, "seed_memory": list(seed_memory or [])}


def test_private_fixture_recall_stays_with_declared_owner(tmp_path):
    case = _case("memory-private", seed_memory=[{
        "owner": "alice", "fact": "喜欢乌龙茶", "trust": "private", "scope": "private",
    }])
    store = build_memory_store(case, _config(tmp_path))
    assert [item["summary"] for item in store.recall_memories(query="乌龙茶", user_id="alice", context_type="private", platform="onebot", bot_id="quality-bot")] == ["喜欢乌龙茶"]
    assert store.recall_memories(query="乌龙茶", user_id="bob", context_type="private", platform="onebot", bot_id="quality-bot") == []
    assert store.embedding_service.enabled is False


def test_group_recall_excludes_private_fixture_memory(tmp_path):
    case = _case("memory-group", surface="group", seed_memory=[{
        "owner": "alice", "fact": "只告诉过私聊的计划", "trust": "private", "scope": "private",
    }])
    store = build_memory_store(case, _config(tmp_path))
    assert store.recall_memories(query="计划", user_id="alice", group_id="g1", context_type="group", platform="onebot", bot_id="quality-bot") == []


def test_memory_update_uses_real_revision_cas_and_rejects_incomplete_scope(tmp_path):
    case = _case("memory-correction", seed_memory=[{
        "owner": "alice", "fact": "考试在周六", "trust": "confirmed", "scope": "private", "revision": 1,
    }])
    store = build_memory_store(case, _config(tmp_path))
    memory_id = store.recall_memories(
        query="考试", user_id="alice", context_type="private",
        platform="onebot", bot_id="quality-bot", limit=1,
    )[0]["memory_id"]
    event = {"kind": "memory_update", "update": {
        "memory_id": memory_id, "revision": 2, "owner": "alice", "fact": "考试改到周日",
        "trust": "confirmed", "scope": "private",
    }}
    assert apply_memory_event(store, event, case)["status"] == "applied"
    assert store.get_memory_item(memory_id)["summary"] == "考试改到周日"
    stale = {"kind": "memory_update", "update": {**event["update"], "revision": 1, "fact": "仍是周六"}}
    assert apply_memory_event(store, stale, case)["status"] == "applied"
    assert store.get_memory_item(memory_id)["summary"] == "考试改到周日"
    unsupported = apply_memory_event(store, {"kind": "memory_update", "update": {"memory_id": memory_id, "revision": 3, "fact": "未知 owner"}}, case)
    assert unsupported["status"] == "unsupported"
    assert store.get_memory_item(memory_id)["summary"] == "考试改到周日"
