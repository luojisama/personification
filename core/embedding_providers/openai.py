from __future__ import annotations

import os
from typing import Any

from .base import EmbeddingProvider


class OpenAIEmbeddingProvider(EmbeddingProvider):
    """OpenAI-compatible embedding provider."""

    def __init__(self, plugin_config: Any | None = None) -> None:
        self.plugin_config = plugin_config
        self._model_id = (
            str(getattr(plugin_config, "personification_embedding_model", "") or "").strip()
            or "text-embedding-3-small"
        )
        self._api_key = (
            str(getattr(plugin_config, "personification_embedding_api_key", "") or "").strip()
            or os.getenv("OPENAI_API_KEY", "")
        )
        self._base_url = str(getattr(plugin_config, "personification_embedding_api_url", "") or "").strip()
        self._timeout_seconds = max(1.0, min(120.0, float(
            getattr(plugin_config, "personification_embedding_timeout_seconds", 20.0) or 20.0
        )))

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def dim(self) -> int:
        model = self._model_id.lower()
        if "large" in model:
            return 3072
        if "small" in model or "ada" in model:
            return 1536
        return 0

    async def embed_batch(self, texts: list[str], *, task: str = "document") -> list[list[float]]:
        if not self._api_key:
            raise RuntimeError("OpenAI embedding api key is empty")
        try:
            from openai import AsyncOpenAI
        except Exception as exc:
            raise RuntimeError(f"openai package unavailable: {exc}") from exc
        kwargs: dict[str, Any] = {"api_key": self._api_key}
        if self._base_url:
            kwargs["base_url"] = self._base_url
        # Explicit short-lived client avoids leaked connections/stale secrets;
        # retries are owned by the plugin route, not silently multiplied here.
        async with AsyncOpenAI(**kwargs, timeout=self._timeout_seconds, max_retries=0) as client:
            response = await client.embeddings.create(
                model=self._model_id,
                input=[str(text or "") for text in texts],
            )
        # The OpenAI-compatible embeddings API has no retrieval task parameter.
        # Keep the argument in the common contract so query/document intent is
        # preserved for providers (notably Gemini) that do support it.
        indices = [int(getattr(item, "index", -1)) for item in response.data]
        expected = set(range(len(texts)))
        if len(indices) != len(texts) or set(indices) != expected:
            raise RuntimeError("OpenAI embedding response indexes are missing or duplicated")
        indexed = sorted(response.data, key=lambda item: int(getattr(item, "index", -1)))
        return [list(item.embedding or []) for item in indexed]


__all__ = ["OpenAIEmbeddingProvider"]
