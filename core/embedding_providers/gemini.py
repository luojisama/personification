from __future__ import annotations

import os
from typing import Any

from .base import EmbeddingProvider


class GeminiEmbeddingProvider(EmbeddingProvider):
    """Scoped Gemini REST embedding provider; no process-global SDK config."""

    def __init__(self, plugin_config: Any | None = None) -> None:
        self.plugin_config = plugin_config
        self._model_id = (
            str(getattr(plugin_config, "personification_embedding_model", "") or "").strip()
            or "gemini-embedding-001"
        )
        self._api_key = (
            str(getattr(plugin_config, "personification_embedding_api_key", "") or "").strip()
            or os.getenv("GOOGLE_API_KEY", "")
            or os.getenv("GEMINI_API_KEY", "")
        )
        self._output_dimensionality = int(
            getattr(plugin_config, "personification_embedding_dimensions", 0) or 0
        )
        self._base_url = (
            str(getattr(plugin_config, "personification_embedding_api_url", "") or "").strip().rstrip("/")
            or "https://generativelanguage.googleapis.com/v1beta"
        )
        self._timeout_seconds = max(1.0, min(120.0, float(
            getattr(plugin_config, "personification_embedding_timeout_seconds", 20.0) or 20.0
        )))

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def dim(self) -> int:
        # Gemini's default dimension may vary by model/version.  Persist the
        # returned dimension rather than claiming this is the request dimension.
        # No dimension is promised until the API returns one.  The service
        # records the observed dimension into its index version.
        return self._output_dimensionality

    async def embed_batch(self, texts: list[str], *, task: str = "document") -> list[list[float]]:
        if not self._api_key:
            raise RuntimeError("Gemini embedding api key is empty")
        try:
            import httpx
        except Exception as exc:
            raise RuntimeError(f"httpx package unavailable: {exc}") from exc
        task_type = "RETRIEVAL_QUERY" if task == "query" else "RETRIEVAL_DOCUMENT"
        url = f"{self._base_url}/models/{self._model_id}:embedContent"
        vectors: list[list[float]] = []
        # Short-lived client prevents stale API keys/endpoints surviving a
        # runtime config reload.  Error bodies are deliberately never logged.
        async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
            for text in [str(value or "") for value in texts]:
                body: dict[str, Any] = {
                    "model": f"models/{self._model_id}",
                    "content": {"parts": [{"text": text}]},
                    "taskType": task_type,
                }
                if self._output_dimensionality > 0:
                    body["outputDimensionality"] = self._output_dimensionality
                try:
                    response = await client.post(url, headers={"x-goog-api-key": self._api_key}, json=body)
                    response.raise_for_status()
                    payload = response.json()
                except Exception as exc:
                    raise RuntimeError(f"Gemini embedding API request failed ({type(exc).__name__})") from exc
                values = ((payload.get("embedding") or {}).get("values") if isinstance(payload, dict) else None)
                if not isinstance(values, list):
                    raise RuntimeError("Gemini embedding API response has no vector")
                vectors.append(list(values))
        return vectors


__all__ = ["GeminiEmbeddingProvider"]
