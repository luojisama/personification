"""Trusted request scope for memory tools.

Tool arguments are model-produced data.  They may narrow a search query, but
they must never choose the user, group, platform, or visibility context used
to read memories.  This module turns the request context installed by the
reply pipeline into a small fail-closed capability object shared by legacy and
skillpack memory tools.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .llm_context import current_llm_context


@dataclass(frozen=True)
class MemoryRequestScope:
    """The only identity/surface inputs permitted for an agent memory read."""

    platform: str
    bot_id: str
    group_id: str
    user_id: str
    context_type: str
    memory_enabled: bool
    palace_enabled: bool

    @property
    def enabled(self) -> bool:
        return self.memory_enabled and self.palace_enabled

    @property
    def identifiable(self) -> bool:
        return bool(self.group_id or self.user_id)

    @property
    def can_recall(self) -> bool:
        return self.enabled and self.identifiable

    @classmethod
    def from_runtime(cls, *, plugin_config: Any, memory_store: Any) -> "MemoryRequestScope":
        ctx = current_llm_context()
        group_id = str(ctx.get("group_id", "") or "").strip()
        user_id = str(ctx.get("user_id", "") or "").strip()
        context_type = "group" if group_id else "private"
        try:
            palace_enabled = bool(memory_store.palace_enabled())
        except Exception:
            palace_enabled = False
        return cls(
            platform=str(ctx.get("platform", "") or "").strip(),
            bot_id=str(ctx.get("bot_id", "") or "").strip(),
            group_id=group_id,
            user_id=user_id,
            context_type=context_type,
            memory_enabled=bool(getattr(plugin_config, "personification_memory_enabled", True)),
            palace_enabled=palace_enabled,
        )

    def actor_recall_kwargs(self) -> dict[str, str]:
        """Return the current actor's private-or-group constrained inputs."""
        return {
            "group_id": self.group_id,
            "user_id": self.user_id,
            "context_type": self.context_type,
            "platform": self.platform,
            "bot_id": self.bot_id,
        }

    def group_recall_kwargs(self) -> dict[str, str]:
        """Recall public/current-group memory without limiting to one member."""
        return {
            "group_id": self.group_id,
            "user_id": "",
            "context_type": "group",
            "platform": self.platform,
            "bot_id": self.bot_id,
        }


def memory_tools_enabled(*, plugin_config: Any, memory_store: Any) -> bool:
    """Dynamic execution gate; configuration changes after queueing take effect."""
    return MemoryRequestScope.from_runtime(
        plugin_config=plugin_config, memory_store=memory_store
    ).enabled


__all__ = ["MemoryRequestScope", "memory_tools_enabled"]
