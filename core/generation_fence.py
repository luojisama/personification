"""Host-owned generation lifetime, independent of provider cancellation."""
from __future__ import annotations

import asyncio
import contextvars
import copy
from types import SimpleNamespace
from typing import Any

_ACTIVE: contextvars.ContextVar[dict | None] = contextvars.ContextVar("reply_generation", default=None)


class SupersededGeneration(asyncio.CancelledError):
    """A newer user supplement owns this reply."""


def capture_config_snapshot(config: Any) -> Any:
    if config is None:
        return None
    values = {name: copy.deepcopy(getattr(config, name)) for name in dir(config)
              if name.startswith("personification_")}
    return SimpleNamespace(**values)


def bind_generation(state: dict, config: Any = None) -> contextvars.Token:
    if config is not None and "_route_config_snapshot" not in state:
        state["_route_config_snapshot"] = capture_config_snapshot(config)
    return _ACTIVE.set(state)


def reset_generation(token: contextvars.Token) -> None:
    _ACTIVE.reset(token)


def active_config(config: Any) -> Any:
    state = _ACTIVE.get()
    return state.get("_route_config_snapshot", config) if isinstance(state, dict) else config


def generation_route_cache() -> dict | None:
    state = _ACTIVE.get()
    return state.setdefault("_generation_route_callers", {}) if isinstance(state, dict) else None


def generation_is_current(state: dict | None = None) -> bool:
    state = state if state is not None else _ACTIVE.get()
    if not isinstance(state, dict):
        return True
    if state.get("_generation_invalidated"):
        return False
    ref = state.get("batch_runtime_ref")
    if not isinstance(ref, dict) or not isinstance(ref.get("entry"), dict):
        return True
    generation, entry = int(ref.get("generation", 0) or 0), ref["entry"]
    return (generation == int(entry.get("current_generation", 0) or 0)
            and int(entry.get("superseded_generation", 0) or 0) < generation)


def assert_current_generation(state: dict | None = None) -> None:
    if not generation_is_current(state) or (state is not None and not generation_is_current()):
        raise SupersededGeneration("supplement_superseded")


def safe_to_supersede(state: dict) -> bool:
    return generation_is_current(state) and not any(state.get(key) for key in (
        "_external_action_started", "reply_delivery_started", "reply_delivery_confirmed",
        "reply_delivery_complete", "delivery_unknown", "reply_delivery_unknown"))


def invalidate_generation(state: dict, reason: str = "supplement") -> bool:
    """Caller owns the session commit lock; there is no await in this check/write."""
    if not safe_to_supersede(state):
        return False
    state["_generation_invalidated"] = True
    state["generation_cancel_reason"] = str(reason)[:64]
    ref = state.get("batch_runtime_ref") or {}
    entry = ref.get("entry")
    if isinstance(entry, dict):
        entry["superseded_generation"] = max(int(entry.get("superseded_generation", 0)), int(ref.get("generation", 0)))
    return True


def mark_external_action_started(state: dict | None = None) -> None:
    state = state if state is not None else _ACTIVE.get()
    assert_current_generation(state)
    if isinstance(state, dict):
        # Keep this barrier even after a result: rerunning a completed write is
        # just as unsafe as rerunning an unknown in-flight write.
        state["_external_action_started"] = True
