from __future__ import annotations

import re
import threading
import time
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Callable


QZONE_ACTIONS = (
    "login_state",
    "own_feed_read",
    "friend_feed_read",
    "publish",
    "like",
    "forward",
    "top_level_comment",
    "child_comment_reply",
)
QZONE_CAPABILITY_STATES = frozenset(
    {"available", "degraded", "unavailable", "unknown", "disabled"}
)
_SAFE_CODE_RE = re.compile(r"[^A-Za-z0-9_.:-]+")


def _safe_code(value: Any, default: str) -> str:
    return _SAFE_CODE_RE.sub("_", str(value or "")[:96]).strip("_") or default


def _safe_interface(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    # Only retain the stable endpoint path or an internal operation name. Query
    # strings may contain identifiers and are never useful in this matrix.
    return text.split("?", 1)[0][:240]


@dataclass(frozen=True, slots=True)
class QzoneCapabilityObservation:
    action: str
    state: str = "unknown"
    interface: str = ""
    http_status: int | None = None
    business_code: str = ""
    missing_fields: tuple[str, ...] = ()
    auth_state: str = "unknown"
    detail_code: str = "not_observed"
    checked_at: float = 0.0
    source: str = "runtime_observation"

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "state": self.state,
            "interface": self.interface,
            "http_status": self.http_status,
            "business_code": self.business_code,
            "missing_fields": list(self.missing_fields),
            "auth_state": self.auth_state,
            "detail_code": self.detail_code,
            "checked_at": self.checked_at or None,
            "source": self.source,
        }


@dataclass(slots=True)
class QzoneCapabilityMatrix:
    time_source: Callable[[], float] = time.time
    _lock: threading.RLock = field(default_factory=threading.RLock, init=False)
    _observations: dict[str, dict[str, QzoneCapabilityObservation]] = field(
        default_factory=dict,
        init=False,
    )

    @staticmethod
    def _bot_key(bot_id: Any) -> str:
        return str(bot_id or "").strip() or "__default__"

    def observe(
        self,
        bot_id: Any,
        action: str,
        *,
        state: str,
        interface: str = "",
        http_status: int | None = None,
        business_code: Any = "",
        missing_fields: tuple[str, ...] | list[str] = (),
        auth_state: Any = "unknown",
        detail_code: Any = "observed",
        source: str = "runtime_observation",
    ) -> QzoneCapabilityObservation:
        normalized_action = str(action or "").strip()
        normalized_state = str(state or "unknown").strip().lower()
        if normalized_action not in QZONE_ACTIONS:
            raise ValueError("unknown qzone capability action")
        if normalized_state not in QZONE_CAPABILITY_STATES:
            raise ValueError("unknown qzone capability state")
        status: int | None
        try:
            status = int(http_status) if http_status is not None else None
        except (TypeError, ValueError):
            status = None
        observation = QzoneCapabilityObservation(
            action=normalized_action,
            state=normalized_state,
            interface=_safe_interface(interface),
            http_status=status,
            business_code=_safe_code(business_code, "") if business_code else "",
            missing_fields=tuple(
                dict.fromkeys(
                    _safe_code(item, "field")
                    for item in missing_fields
                    if str(item or "").strip()
                )
            )[:32],
            auth_state=_safe_code(auth_state, "unknown"),
            detail_code=_safe_code(detail_code, "observed"),
            checked_at=float(self.time_source()),
            source=_safe_code(source, "runtime_observation"),
        )
        with self._lock:
            bucket = self._observations.setdefault(self._bot_key(bot_id), {})
            bucket[normalized_action] = observation
        return observation

    def snapshot(
        self,
        bot_id: Any = "",
        *,
        enabled: bool = True,
        auth_status: dict[str, Any] | None = None,
        aggregate_status: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        key = self._bot_key(bot_id)
        with self._lock:
            observed = deepcopy(self._observations.get(key, {}))

        auth = dict(auth_status or {})
        aggregate = dict(aggregate_status or {})
        auth_state = _safe_code(auth.get("status"), "unknown")
        login_state = "unknown"
        login_code = "auth_not_observed"
        if not enabled:
            login_state = "disabled"
            login_code = "qzone_disabled"
        elif auth_state in {"healthy", "ready", "available", "authenticated"}:
            login_state = "available"
            login_code = "auth_ready"
        elif auth_state in {"login_required", "invalid", "expired", "unavailable"}:
            login_state = "unavailable"
            login_code = auth_state
        elif auth_state in {"refreshing", "degraded"}:
            login_state = "degraded"
            login_code = auth_state

        rows: list[dict[str, Any]] = []
        for action in QZONE_ACTIONS:
            item = observed.get(action)
            if action == "login_state":
                item = QzoneCapabilityObservation(
                    action=action,
                    state=login_state,
                    auth_state=auth_state,
                    detail_code=login_code,
                    checked_at=float(
                        auth.get("last_refresh_at")
                        or auth.get("last_success_at")
                        or auth.get("last_failure_at")
                        or 0
                    ),
                    source="auth_state",
                )
            elif item is None:
                # Coarse read/write state is intentionally diagnostic context
                # only. It cannot prove that a specific operation succeeded.
                group = "qzone.web_read" if action.endswith("feed_read") else "qzone.web_write"
                coarse = aggregate.get(group) if isinstance(aggregate.get(group), dict) else {}
                item = QzoneCapabilityObservation(
                    action=action,
                    state="disabled" if not enabled else "unknown",
                    auth_state=auth_state,
                    detail_code=(
                        "qzone_disabled"
                        if not enabled
                        else "operation_not_observed"
                    ),
                    checked_at=float(coarse.get("updated_at") or 0),
                    source="local_method_unverified",
                )
            elif not enabled:
                item = QzoneCapabilityObservation(
                    **{
                        **item.to_dict(),
                        "state": "disabled",
                        "missing_fields": item.missing_fields,
                        "detail_code": "qzone_disabled",
                    }
                )
            rows.append(item.to_dict())
        return {
            "bot_id": "" if key == "__default__" else key,
            "generated_at": float(self.time_source()),
            "items": rows,
            "write_available": any(
                row["state"] == "available"
                for row in rows
                if row["action"] in {
                    "publish",
                    "like",
                    "forward",
                    "top_level_comment",
                    "child_comment_reply",
                }
            ),
            "production_verified": any(
                row["state"] == "available" and row["source"] == "runtime_observation"
                for row in rows
            ),
        }

    def clear(self) -> None:
        with self._lock:
            self._observations.clear()


DEFAULT_QZONE_CAPABILITY_MATRIX = QzoneCapabilityMatrix()


__all__ = [
    "DEFAULT_QZONE_CAPABILITY_MATRIX",
    "QZONE_ACTIONS",
    "QZONE_CAPABILITY_STATES",
    "QzoneCapabilityMatrix",
    "QzoneCapabilityObservation",
]
