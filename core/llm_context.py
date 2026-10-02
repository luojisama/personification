from __future__ import annotations

import contextvars
from typing import Any


_LLM_CONTEXT: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "personification_llm_context", default=None
)

LLM_RETRY_POLICY_SINGLE_ATTEMPT = "single_attempt"
LLM_MAX_WIRE_ATTEMPTS = 4
_WIRE_RETRY_DISABLED = "wire_retry_disabled"
_WIRE_BUDGET = "wire_budget"


def set_llm_context(
    *,
    group_id: str = "",
    user_id: str = "",
    platform: str = "",
    bot_id: str = "",
    purpose: str = "",
    retry_policy: str = "",
    deadline_monotonic: float | None = None,
) -> contextvars.Token:
    """在进入 LLM 调用栈前调用；返回 token 供 reset。"""
    value = {
        "group_id": str(group_id or ""),
        "user_id": str(user_id or ""),
        "platform": str(platform or ""),
        "bot_id": str(bot_id or ""),
        "purpose": str(purpose or ""),
        # The mutable object is intentional: ``asyncio.wait_for`` creates a
        # child task with a copied ContextVar mapping.  A small per-turn state
        # object remains shared with that child, without making route affinity
        # global on a RoutedToolCaller instance.
        "turn_state": {"successful_route_key": ""},
    }
    if retry_policy:
        value["retry_policy"] = str(retry_policy)
    if deadline_monotonic is not None:
        value["deadline_monotonic"] = float(deadline_monotonic)
    return _LLM_CONTEXT.set(value)


def reset_llm_context(token: contextvars.Token) -> None:
    try:
        _LLM_CONTEXT.reset(token)
    except Exception:
        pass


def current_llm_context() -> dict[str, Any]:
    value = _LLM_CONTEXT.get()
    return value if isinstance(value, dict) else {}


def set_llm_output_limit(max_output_tokens: int) -> contextvars.Token:
    """Bind a route's output ceiling to this request, without mutating callers."""
    value = dict(current_llm_context())
    value["max_output_tokens"] = max(1, int(max_output_tokens))
    return _LLM_CONTEXT.set(value)


def current_llm_output_limit() -> int:
    return max(1, int(current_llm_context().get("max_output_tokens", 32_768)))


def use_single_attempt_retry_policy() -> bool:
    return str(current_llm_context().get("retry_policy", "") or "") == LLM_RETRY_POLICY_SINGLE_ATTEMPT


def set_wire_retry_disabled(*, usage_route_id: str = "", usage_provider: str = "") -> contextvars.Token:
    """Disable SDK transport retries for exactly one outer wire attempt.

    This deliberately differs from ``single_attempt``: the latter also owns
    probe/QZone request-shape compatibility rules. Explicit shape negotiation
    can reserve another request from the shared outer budget.
    """
    value = dict(current_llm_context())
    value[_WIRE_RETRY_DISABLED] = True
    value["usage_route_id"] = usage_route_id
    value["usage_provider"] = usage_provider
    return _LLM_CONTEXT.set(value)


def set_wire_budget(max_attempts: int) -> contextvars.Token:
    value = dict(current_llm_context())
    value[_WIRE_BUDGET] = {"remaining": max(1, int(max_attempts))}
    return _LLM_CONTEXT.set(value)


def wire_budget_remaining() -> int | None:
    budget = current_llm_context().get(_WIRE_BUDGET)
    return int(budget["remaining"]) if isinstance(budget, dict) else None


def reserve_wire_attempt() -> None:
    """Reserve one actual provider generation request before dispatch."""
    budget = current_llm_context().get(_WIRE_BUDGET)
    if not isinstance(budget, dict):
        return
    if budget["remaining"] <= 0:
        error = RuntimeError("provider wire attempt budget exhausted")
        error.code = "provider_request_budget_exhausted"
        error.retryable = False
        raise error
    budget["remaining"] -= 1


def set_llm_purpose(purpose: str) -> contextvars.Token:
    """Temporarily label one nested LLM operation without replacing its scope.

    Nested optional stages still belong to the enclosing reply for identity,
    deadline, retry, wire, and successful-route-affinity purposes.  Copying
    the mapping (rather than calling :func:`set_llm_context`) deliberately
    keeps the shared ``turn_state`` object intact.
    """
    value = dict(current_llm_context())
    value["purpose"] = str(purpose or "")
    return _LLM_CONTEXT.set(value)


def set_llm_retry_policy(retry_policy: str) -> contextvars.Token:
    """Temporarily override retry policy without replacing the current scope."""
    value = dict(current_llm_context())
    value["retry_policy"] = str(retry_policy or "")
    return _LLM_CONTEXT.set(value)


def use_single_wire_attempt_policy() -> bool:
    context = current_llm_context()
    return bool(context.get(_WIRE_RETRY_DISABLED)) or use_single_attempt_retry_policy()


def remaining_llm_deadline_seconds() -> float | None:
    """Return this turn's remaining monotonic budget, when one is active."""
    deadline = current_llm_context().get("deadline_monotonic")
    if deadline is None:
        return None
    try:
        import time

        return float(deadline) - time.monotonic()
    except (TypeError, ValueError):
        return 0.0


def successful_route_key() -> str:
    state = current_llm_context().get("turn_state")
    if not isinstance(state, dict):
        return ""
    return str(state.get("successful_route_key", "") or "").strip()


def remember_successful_route(route_key: str) -> None:
    """Bind only the current ContextVar scope, never a shared caller object."""
    normalized = str(route_key or "").strip()
    if not normalized:
        return
    state = current_llm_context().get("turn_state")
    if not isinstance(state, dict):
        return
    state["successful_route_key"] = normalized


__all__ = [
    "LLM_RETRY_POLICY_SINGLE_ATTEMPT",
    "LLM_MAX_WIRE_ATTEMPTS",
    "set_llm_context",
    "reset_llm_context",
    "current_llm_context",
    "use_single_attempt_retry_policy",
    "set_wire_retry_disabled",
    "set_wire_budget",
    "wire_budget_remaining",
    "reserve_wire_attempt",
    "set_llm_purpose",
    "set_llm_retry_policy",
    "use_single_wire_attempt_policy",
    "remaining_llm_deadline_seconds",
    "successful_route_key",
    "remember_successful_route",
]
