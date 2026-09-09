from __future__ import annotations

import asyncio
from types import SimpleNamespace

from ._loader import load_personification_module


memory_store = load_personification_module("plugin.personification.core.memory_store")
embedding_service = load_personification_module("plugin.personification.core.embedding_service")


class _RemoteOnlyFake:
    """No local model: deterministic stand-in for the remote API contract."""

    enabled = True

    class provider:
        model_id = "remote-test-embedding"

    version_prefix = "api:fake:remote-test-embedding:etest"

    async def embed_documents(self, texts):
        assert texts
        return embedding_service.EmbeddingBatch([[1.0, 0.0] for _ in texts], "api:fake:remote-test-embedding:etest:d2", 2)

    async def embed_query(self, text):
        assert text
        return embedding_service.EmbeddingBatch([[1.0, 0.0]], "api:fake:remote-test-embedding:etest:d2", 2)


class _FailingRemote(_RemoteOnlyFake):
    async def embed_query(self, text):
        raise embedding_service.EmbeddingServiceError("upstream unavailable")


def _store(tmp_path):
    data_store = load_personification_module("plugin.personification.core.data_store")
    cfg = SimpleNamespace(
        personification_memory_retrieval_mode="hybrid_api",
        personification_data_dir=str(tmp_path),
        personification_memory_enabled=True,
        personification_memory_palace_enabled=True,
        personification_memory_rag_enabled=True,
        personification_memory_vector_backend="sqlite_exact",
        personification_memory_recall_top_k=12,
        personification_memory_search_scan_limit=300,
        personification_real_embedding_enabled=True,
        personification_embedding_provider="openai",
    )
    data_store.init_data_store(cfg)
    store = memory_store.MemoryStore(cfg)
    store.initialize()
    store.embedding_service = _RemoteOnlyFake()
    return store


def test_real_api_embeddings_are_queued_then_indexed_without_hash_substitution(tmp_path) -> None:
    store = _store(tmp_path)
    store.write_memory_item({"memory_id": "remote-1", "memory_type": "semantic", "summary": "用户计划下周旅行", "user_id": "u1", "permission_type": "private_fact"})

    with memory_store._connect(tmp_path / "memory_palace" / "memory_palace.db") as conn:
        queued = conn.execute("SELECT status FROM memory_embedding_queue WHERE memory_id='remote-1'").fetchone()
        old = conn.execute("SELECT model_version FROM memory_embeddings WHERE memory_id='remote-1'").fetchone()
    assert queued["status"] == "pending"
    assert old is None

    result = asyncio.run(store.aindex_pending_embeddings())
    assert result == {"status": "ok", "indexed": 1, "failed": 0, "pending": 0}
    with memory_store._connect(tmp_path / "memory_palace" / "memory_palace.db") as conn:
        row = conn.execute("SELECT model_version, embedding FROM memory_vector_chunks WHERE memory_id='remote-1'").fetchone()
        queued = conn.execute("SELECT 1 FROM memory_embedding_queue WHERE memory_id='remote-1'").fetchone()
    assert row["model_version"] == "api:fake:remote-test-embedding:etest:d2"
    assert queued is None


def test_async_recall_uses_remote_query_vector_and_keeps_private_scope(tmp_path) -> None:
    store = _store(tmp_path)
    store.write_memory_item({"memory_id": "private-remote", "memory_type": "semantic", "summary": "私人旅行安排", "user_id": "u1", "permission_type": "private_fact"})
    asyncio.run(store.aindex_pending_embeddings())

    found = asyncio.run(store.arecall_memories(query="旅行", user_id="u1", context_type="private", limit=12))
    hidden = asyncio.run(store.arecall_memories(query="旅行", user_id="u2", context_type="private", limit=12))
    assert any(item["memory_id"] == "private-remote" and "vector" in item["search_source"] for item in found)
    assert not any(item["memory_id"] == "private-remote" for item in hidden)


def test_api_query_failure_never_labels_legacy_hash_as_semantic_recall(tmp_path) -> None:
    store = _store(tmp_path)
    store.embedding_service = _FailingRemote()
    store.write_memory_item({"memory_id": "api-failure", "memory_type": "semantic", "summary": "旧索引中的旅行安排", "user_id": "u1", "permission_type": "private_fact"})
    found = asyncio.run(store.arecall_memories(query="旅行", user_id="u1", context_type="private", limit=12))
    assert found
    assert all("embedding" not in str(item.get("search_source", "")) and "vector" not in str(item.get("search_source", "")) for item in found)
    assert store.embedding_status()["error_code"] == "api_failed"


def test_api_result_for_old_revision_does_not_overwrite_newer_memory(tmp_path) -> None:
    store = _store(tmp_path)
    original = {"memory_id": "race", "memory_type": "semantic", "summary": "旧计划", "user_id": "u1", "permission_type": "private_fact"}
    store.write_memory_item(original)
    snapshot = store.get_memory_item("race")
    store.write_memory_item({**snapshot, "summary": "新计划", "revision": int(snapshot["revision"]) + 1})
    batch = embedding_service.EmbeddingBatch([[1.0, 0.0]], "api:fake:remote-test-embedding:etest:d2", 2)
    assert store._persist_api_embeddings("race", snapshot, [("race:0", "旧计划")], batch) is False  # noqa: SLF001
    with memory_store._connect(tmp_path / "memory_palace" / "memory_palace.db") as conn:
        assert conn.execute("SELECT COUNT(1) AS cnt FROM memory_vector_chunks WHERE memory_id='race'").fetchone()["cnt"] == 0


def test_normal_recall_prefers_recent_window_but_deep_can_use_archive(tmp_path) -> None:
    store = _store(tmp_path)
    # Turn off the fake API to isolate the configured retrieval policy.
    store.plugin_config.personification_real_embedding_enabled = False
    now = memory_store.now_ts()
    store.write_memory_item({"memory_id": "old", "memory_type": "semantic", "summary": "旅行安排", "user_id": "u1", "permission_type": "private_fact", "time_created": now - 40 * 86400, "salience": 1.0})
    store.write_memory_item({"memory_id": "new", "memory_type": "semantic", "summary": "旅行安排", "user_id": "u1", "permission_type": "private_fact", "time_created": now - 86400, "salience": 0.1})
    auto = store.recall_memories(query="旅行安排", user_id="u1", context_type="private", limit=1)
    deep = store.recall_memories(query="旅行安排", user_id="u1", context_type="private", limit=8, mode="deep")
    assert auto[0]["memory_id"] == "new"
    assert {item["memory_id"] for item in deep} >= {"old", "new"}
