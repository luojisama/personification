from __future__ import annotations

import asyncio
from typing import Any


_LOCK_KEY = "reply_commit_lock"
_ACQUIRED_KEY = "_reply_commit_lock_acquired"


async def acquire_reply_commit(state: dict[str, Any]) -> None:
    """Acquire the per-session delivery gate once for the current turn."""
    if bool(state.get(_ACQUIRED_KEY)):
        return
    lock = state.get(_LOCK_KEY)
    if not isinstance(lock, asyncio.Lock):
        return
    await lock.acquire()
    state[_ACQUIRED_KEY] = True


def release_reply_commit(state: dict[str, Any]) -> None:
    if not bool(state.pop(_ACQUIRED_KEY, False)):
        return
    lock = state.get(_LOCK_KEY)
    if isinstance(lock, asyncio.Lock) and lock.locked():
        lock.release()


async def execute_pending_actions(
    executor: Any,
    actions: list[dict[str, Any]],
) -> list[str]:
    """Execute staged Agent actions and return their visible history projections."""
    if executor is None or not actions:
        return []

    from ..core.qq_expression_tools import qq_action_history_text

    history_parts: list[str] = []
    for action in actions:
        await executor.execute(action["type"], action["params"])
        history_text = qq_action_history_text(action)
        if history_text:
            history_parts.append(history_text)
    actions.clear()
    return history_parts


__all__ = [
    "acquire_reply_commit",
    "execute_pending_actions",
    "release_reply_commit",
]
