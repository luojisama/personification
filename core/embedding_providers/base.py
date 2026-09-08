from __future__ import annotations

from abc import ABC, abstractmethod


class EmbeddingProvider(ABC):
    @property
    @abstractmethod
    def model_id(self) -> str:
        raise NotImplementedError

    @property
    @abstractmethod
    def dim(self) -> int:
        raise NotImplementedError

    @abstractmethod
    async def embed_batch(self, texts: list[str], *, task: str = "document") -> list[list[float]]:
        """Embed text remotely for either stored documents or a retrieval query.

        ``task`` is deliberately part of the provider contract.  Providers that
        do not expose task types may ignore it, but callers must never silently
        use a chat completion model as an embedding endpoint.
        """
        raise NotImplementedError


__all__ = ["EmbeddingProvider"]
