from __future__ import annotations

import asyncio
import sys
import types
from types import SimpleNamespace

from ._loader import load_personification_module

hash_bow = load_personification_module("plugin.personification.core.embedding_providers.hash_bow")
openai = load_personification_module("plugin.personification.core.embedding_providers.openai")
gemini = load_personification_module("plugin.personification.core.embedding_providers.gemini")


def test_hash_bow_provider_matches_legacy_dim() -> None:
    provider = hash_bow.HashBowEmbeddingProvider()
    vectors = asyncio.run(provider.embed_batch(["hello world"]))

    assert provider.model_id == "hash-bow-v2-stable"
    assert provider.dim == 64
    assert len(vectors) == 1
    assert len(vectors[0]) == 64


def test_real_provider_placeholders_keep_target_model_metadata() -> None:
    openai_provider = openai.OpenAIEmbeddingProvider()
    gemini_provider = gemini.GeminiEmbeddingProvider()

    assert openai_provider.model_id == "text-embedding-3-small"
    assert openai_provider.dim == 1536
    assert gemini_provider.model_id == "gemini-embedding-001"
    # The provider does not claim a dimension until the remote API returns it.
    assert gemini_provider.dim == 0


def test_gemini_provider_uses_scoped_rest_task_types(monkeypatch) -> None:
    calls = []

    class Response:
        def raise_for_status(self):
            return None
        def json(self):
            return {"embedding": {"values": [1.0, 2.0]}}

    class Client:
        def __init__(self, **kwargs):
            assert kwargs["timeout"] == 20.0
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return None
        async def post(self, url, *, headers, json):
            calls.append((url, headers, json))
            return Response()

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", Client)
    provider = gemini.GeminiEmbeddingProvider(SimpleNamespace(
        personification_embedding_api_key="not-printed", personification_embedding_model="gemini-embedding-001",
        personification_embedding_api_url="https://example.invalid/v1beta", personification_embedding_dimensions=0,
    ))
    vectors = asyncio.run(provider.embed_batch(["doc", "query"], task="query"))
    assert vectors == [[1.0, 2.0], [1.0, 2.0]]
    assert all(call[0] == "https://example.invalid/v1beta/models/gemini-embedding-001:embedContent" for call in calls)
    assert all(call[1] == {"x-goog-api-key": "not-printed"} for call in calls)
    assert all(call[2]["taskType"] == "RETRIEVAL_QUERY" for call in calls)


def test_openai_provider_validates_indexes_and_closes_client(monkeypatch) -> None:
    closed = []
    class Client:
        def __init__(self, **kwargs):
            assert kwargs["timeout"] == 20.0 and kwargs["max_retries"] == 0
            self.embeddings = types.SimpleNamespace(create=self.create)
        async def __aenter__(self): return self
        async def __aexit__(self, *args): closed.append(True)
        async def create(self, **kwargs):
            return types.SimpleNamespace(data=[types.SimpleNamespace(index=1, embedding=[2.0]), types.SimpleNamespace(index=0, embedding=[1.0])])
    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(AsyncOpenAI=Client))
    provider = openai.OpenAIEmbeddingProvider(SimpleNamespace(personification_embedding_api_key="x"))
    assert asyncio.run(provider.embed_batch(["a", "b"])) == [[1.0], [2.0]]
    assert closed
