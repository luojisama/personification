from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from ._loader import load_personification_module
from .test_api_embedding_memory import _store

text_index = load_personification_module("plugin.personification.core.text_memory_index")
query_module = load_personification_module("plugin.personification.core.memory_query")
store_module = load_personification_module("plugin.personification.core.memory_store")
llm_context = load_personification_module("plugin.personification.core.llm_context")


def test_algorithm_never_calls_hash_or_remote(tmp_path, monkeypatch):
    store = _store(tmp_path)
    store.plugin_config.personification_memory_retrieval_mode = "algorithm_llm"
    def forbidden(*args, **kwargs):
        raise AssertionError("algorithm path invoked model/hash")
    monkeypatch.setattr(store_module, "embed_text", forbidden)
    store.embedding_service.embed_query = forbidden
    store.write_memory_item({"memory_id": "sleep", "summary": "晚上翻来覆去一直睡不着", "aliases": ["失眠", "作息"],
        "memory_type": "semantic", "user_id": "u1", "permission_type": "private_fact"})
    assert asyncio.run(store.arecall_memories(query="作息", user_id="u1", context_type="private"))
    assert not asyncio.run(store.arecall_memories(query="作息", user_id="u2", context_type="private"))


@pytest.mark.parametrize("query", ["睡眠", "作息", "培训", "活动", "约定", "旅行", "兴趣", "音乐", "周末", "假期"])
def test_chinese_phrase_inside_sentence(query):
    assert query in text_index.tokens("之前我们讨论过" + query + "的安排")
    assert "好世" not in text_index.tokens("你好，世界")


def test_safe_match_and_rebuild_resume(tmp_path):
    store = _store(tmp_path)
    store.plugin_config.personification_memory_retrieval_mode = "algorithm_llm"
    for i in range(5):
        store.write_memory_item({"memory_id": str(i), "summary": "假期旅行", "memory_type": "semantic"})
    with store_module._connect(tmp_path / "memory_palace" / "memory_palace.db") as conn:
        conn.execute("DELETE FROM memory_text_versions")
        conn.commit()
    assert store.rebuild_text_index_batch(2) == 2
    assert store.rebuild_text_index_batch(2) == 2
    assert store.rebuild_text_index_batch(2) == 1
    assert store.rebuild_text_index_batch(2) == 0
    store.recall_memories(query='" OR NOT * : NEAR DROP TABLE', limit=2)
    assert store.text_index_status()["total"] == 5


def test_query_planner_cannot_create_scope_or_event():
    class Caller:
        async def chat_with_tools(self, **kwargs):
            return SimpleNamespace(content='{"queries":["睡眠","作息"],"bot_id":"other","event_ids":["bad","known"]}')
    result = asyncio.run(query_module.plan_memory_query("最近如何", [{"state_id": "known"}], Caller(), timeout=1))
    assert result.queries == ["睡眠", "作息"]
    assert result.event_ids == ["known"]
    assert not hasattr(result, "bot_id")


def test_fts_filters_identity_before_bounded_candidates(tmp_path):
    store = _store(tmp_path)
    store.plugin_config.personification_memory_retrieval_mode = "algorithm_llm"
    store.write_memory_item({"memory_id":"wanted", "summary":"假期旅行", "platform":"onebot", "bot_id":"wanted-bot", "user_id":"u", "permission_type":"private_fact"})
    for index in range(90):
        store.write_memory_item({"memory_id":f"noise-{index}", "summary":"假期旅行", "platform":"onebot", "bot_id":"other-bot", "user_id":"u", "permission_type":"private_fact"})
    found = store.recall_memories(query="假期", user_id="u", platform="onebot", bot_id="wanted-bot", limit=1)
    assert [row["memory_id"] for row in found] == ["wanted"]


def test_query_planner_rejects_reversed_time_range():
    class Caller:
        async def chat_with_tools(self, **kwargs):
            return SimpleNamespace(content='{"queries":["安排"],"after":200,"before":100}')
    result = asyncio.run(query_module.plan_memory_query("原问题", [], Caller(), timeout=1))
    assert result.status == "invalid_time_range"
    assert result.queries == ["原问题"]


def test_algorithm_rebuild_never_creates_legacy_vectors(tmp_path, monkeypatch):
    store = _store(tmp_path)
    store.plugin_config.personification_memory_retrieval_mode = "algorithm_llm"
    store.write_memory_item({"memory_id":"raw", "summary":"假期"})
    monkeypatch.setattr(store_module, "embed_text", lambda *_: pytest.fail("unexpected legacy vector"))
    assert store.rebuild_vector_index()["retrieval_mode"] == "algorithm_llm"
    assert asyncio.run(store.arebuild_vector_index())["retrieval_mode"] == "algorithm_llm"
    assert store.get_vector_index_status()["chunk_count"] == 0


def test_existing_intent_plan_reuses_queries_without_extra_api():
    planner = load_personification_module("plugin.personification.agent.runtime.planner")
    plan = planner.parse_turn_plan_payload({"reply_action":"reply", "memory_queries":["去年旅行","最近假期"]})
    class Caller:
        async def chat_with_tools(self, **kwargs):
            pytest.fail("planned queries must not add a planning API")
    result = asyncio.run(query_module.plan_memory_query("早上好", [], Caller(), timeout=1, turn_plan=plan))
    assert result.status == "reused"
    assert result.queries == ["去年旅行", "最近假期"]


def test_query_planner_scopes_optional_purpose_without_replacing_outer_context():
    seen = []

    class Caller:
        async def chat_with_tools(self, **_kwargs):
            seen.append(dict(llm_context.current_llm_context()))
            llm_context.remember_successful_route("sticky-route")
            return SimpleNamespace(content='{"queries":["睡眠"]}')

    async def scenario():
        outer = llm_context.set_llm_context(
            group_id="g", user_id="u", platform="onebot", bot_id="bot",
            purpose="reply", retry_policy=llm_context.LLM_RETRY_POLICY_SINGLE_ATTEMPT,
            deadline_monotonic=123.0,
        )
        wire = llm_context.set_wire_retry_disabled(usage_route_id="route", usage_provider="gemini")
        try:
            result = await query_module.plan_memory_query("睡眠", [], Caller(), timeout=1)
            assert result.status == "planned"
            restored = llm_context.current_llm_context()
            assert restored["purpose"] == "reply"
            assert restored["usage_route_id"] == "route"
            assert llm_context.successful_route_key() == "sticky-route"
        finally:
            llm_context.reset_llm_context(wire)
            llm_context.reset_llm_context(outer)

    asyncio.run(scenario())
    assert seen and seen[0]["purpose"] == "memory_query_plan"
    assert {key: seen[0][key] for key in ("group_id", "user_id", "platform", "bot_id", "retry_policy", "deadline_monotonic", "usage_route_id", "usage_provider")} == {
        "group_id": "g", "user_id": "u", "platform": "onebot", "bot_id": "bot",
        "retry_policy": llm_context.LLM_RETRY_POLICY_SINGLE_ATTEMPT,
        "deadline_monotonic": 123.0, "usage_route_id": "route", "usage_provider": "gemini",
    }


def test_query_planner_timeout_and_external_cancellation_keep_existing_outcomes():
    seen = []
    cancelled = []

    class Caller:
        async def chat_with_tools(self, **_kwargs):
            seen.append(dict(llm_context.current_llm_context()))
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.append(True)
                raise

    async def scenario():
        outer = llm_context.set_llm_context(purpose="reply", group_id="g", user_id="u")
        try:
            timed_out = await query_module.plan_memory_query("当前问题", [], Caller(), timeout=0.01)
            assert timed_out.status == "timeout"
            assert llm_context.current_llm_context()["purpose"] == "reply"

            task = asyncio.create_task(query_module.plan_memory_query("当前问题", [], Caller(), timeout=30))
            while len(seen) < 2:
                await asyncio.sleep(0)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert llm_context.current_llm_context()["purpose"] == "reply"
        finally:
            llm_context.reset_llm_context(outer)

    asyncio.run(scenario())
    assert cancelled == [True, True]
    assert [context["purpose"] for context in seen] == ["memory_query_plan", "memory_query_plan"]


def test_disabled_hybrid_api_never_generates_hash_vectors(tmp_path, monkeypatch):
    store = _store(tmp_path)
    store.embedding_service = SimpleNamespace(enabled=False, misconfigured=False,
        provider=SimpleNamespace(model_id="disabled"))
    def forbidden(*args, **kwargs):
        raise AssertionError("disabled hybrid path invoked hash")
    monkeypatch.setattr(store_module, "embed_text", forbidden)
    store.write_memory_item({"memory_id": "disabled-api", "summary": "下周假期旅行", "user_id": "u1", "permission_type": "private_fact"})
    assert asyncio.run(store.arecall_memories(query="假期", user_id="u1", context_type="private"))
    assert store.rebuild_vector_index()["status"] == "embedding_unconfigured"
    with store_module._connect(tmp_path / "memory_palace" / "memory_palace.db") as conn:
        assert conn.execute("SELECT COUNT(*) FROM memory_embeddings").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM memory_embedding_queue").fetchone()[0] == 1
