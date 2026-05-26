from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from ._loader import load_personification_module


@pytest.fixture
def _store(tmp_path: Path, monkeypatch):
    data_store = load_personification_module("plugin.personification.core.data_store")
    paths = load_personification_module("plugin.personification.core.paths")
    monkeypatch.setattr(paths, "get_data_dir", lambda _cfg=None: tmp_path)
    cfg = SimpleNamespace(
        personification_data_dir=str(tmp_path),
        personification_memory_enabled=True,
        personification_memory_palace_enabled=True,
        personification_memory_recall_top_k=8,
    )
    data_store.init_data_store(cfg)
    memory_store_mod = load_personification_module("plugin.personification.core.memory_store")
    store = memory_store_mod.MemoryStore(plugin_config=cfg, logger=SimpleNamespace(warning=lambda *_a, **_k: None))
    store.initialize()
    return store


def test_group_recall_filters_private_and_sensitive_memories(_store) -> None:
    _store.write_memory_item(
        {
            "memory_id": "public",
            "memory_type": "fact",
            "summary": "公开偏好：喜欢塔防游戏",
            "group_id": "g1",
            "permission_type": "public_preference",
            "confidence": 0.9,
        }
    )
    _store.write_memory_item(
        {
            "memory_id": "private",
            "memory_type": "fact",
            "summary": "私人事实：真实住址在测试路",
            "group_id": "g1",
            "permission_type": "private_fact",
            "confidence": 0.9,
        }
    )
    _store.write_memory_item(
        {
            "memory_id": "sensitive",
            "memory_type": "fact",
            "summary": "敏感事实：测试病史",
            "group_id": "g1",
            "permission_type": "sensitive_memory",
            "confidence": 0.9,
        }
    )

    results = _store.recall_memories(query="测试 偏好 住址 病史 塔防", group_id="g1", context_type="group")
    summaries = [item["summary"] for item in results]

    assert any("塔防" in summary for summary in summaries)
    assert all("住址" not in summary for summary in summaries)
    assert all("病史" not in summary for summary in summaries)


def test_group_recall_redacts_conflict_memory(_store) -> None:
    _store.write_memory_item(
        {
            "memory_id": "conflict",
            "memory_type": "conflict_memory",
            "summary": "冲突避雷：不要复述具体争吵细节",
            "group_id": "g1",
            "permission_type": "conflict_memory",
            "confidence": 0.9,
        }
    )

    results = _store.recall_memories(query="争吵 避雷", group_id="g1", context_type="group")

    assert results
    assert results[0]["permission_type"] == "conflict_memory"
    assert "中性克制" in results[0]["summary"]
    assert "不要复述具体争吵细节" not in results[0]["summary"]


def test_private_recall_can_return_private_fact(_store) -> None:
    _store.write_memory_item(
        {
            "memory_id": "private",
            "memory_type": "fact",
            "summary": "私人事实：喜欢深夜整理代码",
            "user_id": "u1",
            "permission_type": "private_fact",
            "confidence": 0.9,
        }
    )

    results = _store.recall_memories(query="深夜 整理代码", user_id="u1", context_type="private")

    assert any("深夜整理代码" in item["summary"] for item in results)
