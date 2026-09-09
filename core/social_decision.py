"""Typed, fail-closed decisions for every proactive social surface.

The model supplies social intent; this module only validates its bounded data
and keeps a small durable operation record.  In particular an ``unknown``
attempt is a terminal claim for its motivation and must never be retried with
different wording.
"""
from __future__ import annotations

import hashlib
import json
import math
import time
from dataclasses import dataclass
from typing import Any

_NAMESPACE = "social_decision_operations_v1"
_ACTIONS = frozenset({"respond", "join", "contact", "qzone_interact", "defer", "silent"})
_EXPRESSIONS = frozenset({"text", "emoji", "qq_face", "sticker"})


@dataclass(frozen=True)
class SocialDecision:
    action: str
    target_id: str = ""
    content: str = ""
    motivation: str = ""
    source_event_ids: tuple[str, ...] = ()
    expression: str = "text"
    next_consider_at: float = 0.0

    @property
    def should_send(self) -> bool:
        return self.action in {"respond", "join", "contact", "qzone_interact"} and bool(self.content)

    @property
    def operation_key(self) -> str:
        # Content, action and motivation are model wording and therefore must
        # not make a new operation.  Only trusted target/event identity does.
        source = ",".join(sorted(set(self.source_event_ids)))
        raw = f"{self.target_id}\0{source}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def parse_social_decision(
    raw: Any, *, max_content_chars: int = 240, allowed_source_event_ids: set[str] | None = None
) -> SocialDecision | None:
    """Parse only a JSON object with a real JSON boolean ``send``.

    Invalid JSON, string booleans, absent motivation for an outbound action and
    invalid IDs are all rejected.  This deliberately has no permissive default.
    """
    try:
        payload = json.loads(str(raw or "").strip())
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    send = payload.get("send")
    if type(send) is not bool:
        return None
    action = str(payload.get("action", "silent") or "silent").strip().lower()
    if action not in _ACTIONS:
        return None
    target_id = str(payload.get("target_id", "") or "").strip()[:160]
    content = str(payload.get("content", "") or "").strip()[:max(1, int(max_content_chars))]
    motivation = str(payload.get("motivation", "") or "").strip()[:240]
    expression = str(payload.get("expression", "text") or "text").strip().lower()
    if expression not in _EXPRESSIONS:
        return None
    source = payload.get("source_event_ids", [])
    if not isinstance(source, list) or any(not isinstance(item, (str, int)) for item in source):
        return None
    source_ids = tuple(str(item).strip()[:160] for item in source if str(item).strip())[:8]
    next_at = payload.get("next_consider_at", 0)
    if isinstance(next_at, bool):
        return None
    try:
        next_consider_at = float(next_at or 0)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(next_consider_at):
        return None
    next_consider_at = max(0.0, next_consider_at)
    outbound = action in {"respond", "join", "contact", "qzone_interact"}
    if send != outbound or (outbound and (not content or not motivation)):
        return None
    if not outbound and content:
        return None
    if allowed_source_event_ids is not None and not set(source_ids).issubset(allowed_source_event_ids):
        return None
    return SocialDecision(action, target_id, content, motivation, source_ids, expression, next_consider_at)


def _scoped_key(decision: SocialDecision, scope: str) -> str:
    return f"{str(scope or '').strip()[:160]}:{decision.operation_key}"


def scheduled_event_id(*, scenario: str, period: str, target_id: str, detail: str = "") -> str:
    """Stable locally-derived event id for a scheduler tick, never model input."""
    values = (str(scenario).strip(), str(period).strip(), str(target_id).strip(), str(detail).strip())
    if not all(values[:3]):
        raise ValueError("scenario, period and target_id are required")
    return "scheduled:" + hashlib.sha256("\0".join(values).encode("utf-8")).hexdigest()[:24]


def claim_social_decision(
    decision: SocialDecision, *, channel: str, scope: str = "", now: float | None = None,
    target_cooldown_seconds: float = 6 * 3600,
) -> bool:
    """Reserve a motivation once across channels; unknown claims remain sealed."""
    key = _scoped_key(decision, scope); timestamp = float(now if now is not None else time.time()); accepted = False
    try:
        from .data_store import get_data_store
        def _mutate(current: Any) -> dict[str, Any]:
            nonlocal accepted
            data = dict(current) if isinstance(current, dict) else {}
            cooldown = max(0.0, target_cooldown_seconds)
            existing = data.get(key)
            if isinstance(existing, dict) and existing.get("status") in {"reserved", "sent", "unknown"}:
                # Event-backed identities are durable attempts: unknown must
                # never replay the same source event. Eventless scheduling has
                # no identity, so it becomes a new attempt after cooldown.
                if decision.source_event_ids or timestamp - float(existing.get("at", 0) or 0) < cooldown:
                    return data
            # Target cooldown coordinates distinct event IDs across channels.
            # Explicit QZone composite children are independently receipted.
            children = {"qzone:like", "qzone:comment"}
            composite = bool(set(decision.source_event_ids) & children)
            parent = sorted(set(decision.source_event_ids) - children)
            target = f"target:{str(scope)[:160]}:{decision.target_id}"
            previous = data.get(target)
            sibling = (composite and bool(parent) and isinstance(previous, dict)
                       and previous.get("parent") == parent and previous.get("composite") is True)
            if isinstance(previous, dict) and timestamp - float(previous.get("at", 0) or 0) < cooldown and not sibling:
                return data
            data[target] = {"at": timestamp, "parent": parent, "composite": composite}
            data[key] = {"status": "reserved", "channel": str(channel)[:64], "scope": str(scope)[:160], "at": timestamp}
            accepted = True
            return data
        get_data_store().mutate_sync(_NAMESPACE, _mutate)
    except Exception:
        return False
    return accepted


def settle_social_decision(decision: SocialDecision, *, status: str, scope: str = "") -> None:
    """Only confirmed delivery becomes sent; unknown remains non-replayable."""
    normalized = "sent" if status == "sent" else "unknown" if status == "unknown" else "failed"
    key = _scoped_key(decision, scope)
    try:
        from .data_store import get_data_store
        def _mutate(current: Any) -> dict[str, Any]:
            data = dict(current) if isinstance(current, dict) else {}
            entry = data.get(key)
            if isinstance(entry, dict):
                entry = dict(entry); entry["status"] = normalized; entry["updated_at"] = time.time(); data[key] = entry
            return data
        get_data_store().mutate_sync(_NAMESPACE, _mutate)
    except Exception:
        return


__all__ = ["SocialDecision", "claim_social_decision", "parse_social_decision", "scheduled_event_id", "settle_social_decision"]
