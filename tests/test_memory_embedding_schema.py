from __future__ import annotations

from ._loader import load_personification_module

memory_store = load_personification_module("plugin.personification.core.memory_store")


def test_pack_float16_vector_returns_two_bytes_per_value() -> None:
    payload = memory_store._pack_float16_vector([0.0, 1.0, -1.0])

    assert isinstance(payload, bytes)
    assert len(payload) == 6


def test_fuse_recall_candidates_uses_rrf_and_merges_reasons() -> None:
    candidate_a_fts = memory_store.MemorySearchCandidate(
        memory_id="m-a",
        payload={"memory_id": "m-a", "summary": "A"},
        base_score=0.5,
        match_reasons=["全文命中"],
        source="fts",
    )
    candidate_a_embedding = memory_store.MemorySearchCandidate(
        memory_id="m-a",
        payload={"memory_id": "m-a", "summary": "A"},
        base_score=0.45,
        match_reasons=["语义相近"],
        source="embedding",
    )
    candidate_b = memory_store.MemorySearchCandidate(
        memory_id="m-b",
        payload={"memory_id": "m-b", "summary": "B"},
        base_score=0.51,
        match_reasons=["实体命中"],
        source="entity",
    )

    fused = memory_store._fuse_recall_candidates(
        [[candidate_b], [candidate_a_fts], [candidate_a_embedding]],
        limit=2,
        rrf_k=1,
    )

    assert [item.memory_id for item in fused] == ["m-a", "m-b"]
    assert fused[0].source == "fts+embedding"
    assert "全文命中" in fused[0].match_reasons
    assert "语义相近" in fused[0].match_reasons
    assert any(reason.startswith("RRF混合召回") for reason in fused[0].match_reasons)
