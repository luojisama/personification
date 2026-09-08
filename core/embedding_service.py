"""API-only embedding orchestration for memory indexing and retrieval.

This module intentionally has no local ML fallback.  Hash-BOW remains a legacy
index format owned by :mod:`embedding_index`; it is never presented as an API
semantic embedding when real embedding is configured.
"""
from __future__ import annotations

import math
import hashlib
from dataclasses import dataclass
from typing import Any

from .embedding_providers import build_embedding_provider


class EmbeddingServiceError(RuntimeError):
    pass


@dataclass(frozen=True)
class EmbeddingBatch:
    vectors: list[list[float]]
    model_version: str
    dimension: int


class EmbeddingService:
    def __init__(self, plugin_config: Any | None = None) -> None:
        self.plugin_config = plugin_config
        self.provider = build_embedding_provider(plugin_config)

    @property
    def enabled(self) -> bool:
        return bool(getattr(self.plugin_config, "personification_real_embedding_enabled", False)) and not self.misconfigured

    @property
    def misconfigured(self) -> bool:
        requested = str(getattr(self.plugin_config, "personification_embedding_provider", "hash_bow") or "hash_bow").strip().lower().replace("-", "_")
        return bool(getattr(self.plugin_config, "personification_real_embedding_enabled", False)) and requested not in {"openai", "gemini"}

    @property
    def version_prefix(self) -> str:
        """Non-secret endpoint identity; API vectors never cross this boundary."""
        endpoint = str(getattr(self.provider, "_base_url", "") or "").strip().rstrip("/")
        endpoint_id = hashlib.blake2b(endpoint.encode("utf-8"), digest_size=8, person=b"pers-embed-v1").hexdigest()
        provider_name = self.provider.__class__.__name__.replace("EmbeddingProvider", "").lower()
        return f"api:{provider_name}:{self.provider.model_id}:e{endpoint_id}"

    async def embed_documents(self, texts: list[str]) -> EmbeddingBatch:
        return await self._embed(texts, task="document")

    async def embed_query(self, text: str) -> EmbeddingBatch:
        return await self._embed([text], task="query")

    async def _embed(self, texts: list[str], *, task: str) -> EmbeddingBatch:
        if self.misconfigured:
            raise EmbeddingServiceError("real embedding is enabled but provider is not an API provider")
        if not self.enabled:
            raise EmbeddingServiceError("API embedding is not enabled")
        prepared = [str(text or "") for text in texts]
        if not prepared:
            return EmbeddingBatch([], "", 0)
        try:
            vectors = await self.provider.embed_batch(prepared, task=task)
        except Exception as exc:
            raise EmbeddingServiceError(f"embedding API {task} request failed: {exc}") from exc
        if len(vectors) != len(prepared):
            raise EmbeddingServiceError("embedding API returned a different number of vectors")
        dim = 0
        cleaned: list[list[float]] = []
        for vector in vectors:
            if not isinstance(vector, list) or not vector:
                raise EmbeddingServiceError("embedding API returned an empty vector")
            try:
                item = [float(value) for value in vector]
            except (TypeError, ValueError) as exc:
                raise EmbeddingServiceError("embedding API returned a non-numeric vector") from exc
            if not all(math.isfinite(value) for value in item):
                raise EmbeddingServiceError("embedding API returned a non-finite vector")
            if not any(value != 0.0 for value in item):
                raise EmbeddingServiceError("embedding API returned a zero vector")
            if dim and len(item) != dim:
                raise EmbeddingServiceError("embedding API returned inconsistent vector dimensions")
            dim = len(item)
            cleaned.append(item)
        requested_dim = int(getattr(self.plugin_config, "personification_embedding_dimensions", 0) or 0)
        if requested_dim > 0 and dim != requested_dim:
            raise EmbeddingServiceError("embedding API ignored requested vector dimension")
        # The observed dimension is part of the version key, preventing vectors
        # from different output dimensions from ever being compared.
        return EmbeddingBatch(cleaned, f"{self.version_prefix}:d{dim}", dim)


__all__ = ["EmbeddingBatch", "EmbeddingService", "EmbeddingServiceError"]
