from __future__ import annotations

import json
import re
import time
import unicodedata
import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from .db import connect_sync, get_db_path


DEFAULT_RECOVERY_TTL_SECONDS = 24 * 60 * 60
DEFAULT_CLAIM_LEASE_SECONDS = 10 * 60
MAX_RECOVERY_ATTEMPTS = 3
MAX_RECOVERY_BATCH_MESSAGES = 50
MAX_RECOVERY_BATCH_CHARS = 30_000

RECOVERABLE_FAILURE_CLASSES = frozenset(
    {"generation_failed_before_send", "confirmed_not_sent"}
)
QUARANTINED_FAILURE_CLASSES = frozenset({"delivery_unknown", "delivery_partial"})
RECOVERY_FAILURE_CLASSES = RECOVERABLE_FAILURE_CLASSES | QUARANTINED_FAILURE_CLASSES
RECOVERY_STATUSES = frozenset(
    {
        "pending",
        "processing",
        "dispatching",
        "recovered",
        "quarantined",
        "expired",
        "exhausted",
        "abandoned",
    }
)
RECOVERY_DELIVERY_OUTCOMES = frozenset(
    {"confirmed", "confirmed_not_sent", "delivery_unknown", "delivery_partial"}
)

RecoveryFailureClass = Literal[
    "generation_failed_before_send",
    "confirmed_not_sent",
    "delivery_unknown",
    "delivery_partial",
]
RecoveryStatus = Literal[
    "pending",
    "processing",
    "dispatching",
    "recovered",
    "quarantined",
    "expired",
    "exhausted",
    "abandoned",
]
RecoveryDeliveryOutcome = Literal[
    "confirmed",
    "confirmed_not_sent",
    "delivery_unknown",
    "delivery_partial",
]

_TERMINAL_STATUSES = frozenset({"recovered", "expired", "exhausted", "abandoned"})
_ALLOWED_MEDIA_KINDS = frozenset(
    {"image", "sticker", "gif", "mface", "video", "audio", "unknown"}
)
_ALLOWED_MEDIA_ORIGINS = frozenset({"current", "quoted", "batch"})
_MEDIA_STRING_LIMITS = {
    "media_id": 256,
    "ref": 2048,
    "owner_user_id": 160,
    "message_id": 160,
    "content_hash": 128,
    "file_id": 512,
    "safe_summary": 500,
    "summary_scope": 64,
    "group_id": 160,
    "resolution_code": 96,
}
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_INLINE_SPACE_RE = re.compile(r"[\t\f\v ]+")
_DATA_REF_RE = re.compile(r"^(?:data:|base64://)", re.IGNORECASE)


class RecoveryClaimError(RuntimeError):
    """Raised when a worker mutates a batch it no longer owns."""


def _publish_recovery_updated(items: Iterable["RecoveryItem"], *, action: str) -> None:
    try:
        from .runtime_events import publish_runtime_event

        rows = tuple(items)
        publish_runtime_event(
            "recovery.updated",
            trace_id=next((item.trace_id for item in rows if item.trace_id), ""),
            payload={
                "action": str(action or "updated")[:48],
                "item_ids": [item.id for item in rows[:50]],
                "statuses": sorted({item.status for item in rows}),
                "count": len(rows),
            },
        )
    except Exception:
        pass


@dataclass(frozen=True)
class RecoveryItem:
    id: int
    bot_id: str
    conversation_kind: str
    conversation_id: str
    original_message_id: str
    normalized_text: str
    media_refs: tuple[dict[str, Any], ...]
    failure_stage: str
    last_failure_stage: str
    failure_class: str
    missing_part_indexes: tuple[int, ...]
    route_fingerprint: str
    first_failure_at: float
    last_failure_at: float
    attempt_count: int
    status: str
    expires_at: float
    next_attempt_at: float
    trace_id: str
    claim_token: str
    claimed_by: str
    claim_started_at: float
    claim_expires_at: float
    recovered_at: float
    updated_at: float

    @property
    def recoverable(self) -> bool:
        return (
            self.failure_class in RECOVERABLE_FAILURE_CLASSES
            and self.status == "pending"
            and self.attempt_count < MAX_RECOVERY_ATTEMPTS
        )


@dataclass(frozen=True)
class RecoveryBatch:
    claim_token: str
    claimed_by: str
    bot_id: str
    conversation_kind: str
    conversation_id: str
    route_fingerprint: str
    items: tuple[RecoveryItem, ...]
    character_count: int
    claimed_at: float
    lease_expires_at: float

    @property
    def item_ids(self) -> tuple[int, ...]:
        return tuple(item.id for item in self.items)


def normalize_recovery_text(value: Any, *, limit: int = MAX_RECOVERY_BATCH_CHARS) -> str:
    """Normalize inbound text without storing a generated reply or hidden state."""

    text = unicodedata.normalize("NFKC", str(value or ""))
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _CONTROL_CHAR_RE.sub("", text)
    text = "\n".join(_INLINE_SPACE_RE.sub(" ", line).strip() for line in text.split("\n"))
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text[: max(0, int(limit))]


def _clean_scalar(value: Any, *, limit: int) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = _CONTROL_CHAR_RE.sub("", text).strip()
    return text[: max(0, int(limit))]


def _required_scalar(value: Any, *, field: str, limit: int = 160) -> str:
    text = _clean_scalar(value, limit=limit)
    if not text:
        raise ValueError(f"{field} is required")
    return text


def _normalize_scope(
    bot_id: Any,
    conversation_kind: Any,
    conversation_id: Any,
) -> tuple[str, str, str]:
    normalized_bot_id = _required_scalar(bot_id, field="bot_id")
    normalized_kind = _clean_scalar(conversation_kind, limit=16).lower()
    if normalized_kind not in {"group", "private"}:
        raise ValueError("conversation_kind must be group or private")
    normalized_conversation_id = _required_scalar(
        conversation_id,
        field="conversation_id",
    )
    return normalized_bot_id, normalized_kind, normalized_conversation_id


def _normalize_failure_class(value: Any) -> str:
    normalized = _clean_scalar(value, limit=64).lower()
    if normalized not in RECOVERY_FAILURE_CLASSES:
        raise ValueError("invalid reply recovery failure class")
    return normalized


def _normalize_delivery_outcome(value: Any) -> str:
    normalized = _clean_scalar(value, limit=64).lower()
    if normalized not in RECOVERY_DELIVERY_OUTCOMES:
        raise ValueError("invalid reply recovery delivery outcome")
    return normalized


def _media_mapping(value: Any) -> Mapping[str, Any] | None:
    if isinstance(value, Mapping):
        return value
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        candidate = to_dict()
        if isinstance(candidate, Mapping):
            return candidate
    return None


def normalize_recovery_media_refs(
    values: Iterable[Any] | None,
    *,
    limit: int = 64,
) -> tuple[dict[str, Any], ...]:
    """Keep only the durable, allowlisted portion of inbound media provenance.

    Data/base64 references and unknown fields are deliberately omitted. Callers
    should materialize expiring media into their own controlled reference before
    recording a failure; this queue never stores media bytes.
    """

    normalized: list[dict[str, Any]] = []
    for raw in values or ():
        if len(normalized) >= max(0, int(limit)):
            break
        mapping = _media_mapping(raw)
        if mapping is None:
            continue
        kind = _clean_scalar(mapping.get("kind"), limit=16).lower()
        origin = _clean_scalar(mapping.get("origin"), limit=16).lower()
        item: dict[str, Any] = {
            "kind": kind if kind in _ALLOWED_MEDIA_KINDS else "unknown",
            "origin": origin if origin in _ALLOWED_MEDIA_ORIGINS else "current",
        }
        for key, field_limit in _MEDIA_STRING_LIMITS.items():
            if key in {"safe_summary", "ref"}:
                continue
            text = _clean_scalar(mapping.get(key), limit=field_limit)
            if text:
                item[key] = text
        media_ref = _clean_scalar(mapping.get("ref"), limit=_MEDIA_STRING_LIMITS["ref"])
        if media_ref and not _DATA_REF_RE.match(media_ref):
            item["ref"] = media_ref
        summary = normalize_recovery_text(
            mapping.get("safe_summary"),
            limit=_MEDIA_STRING_LIMITS["safe_summary"],
        )
        if summary:
            item["safe_summary"] = summary
        try:
            confidence = float(mapping.get("confidence", 0.0) or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        item["confidence"] = max(0.0, min(1.0, confidence))
        if item.get("media_id") or item.get("file_id") or item.get("ref"):
            normalized.append(item)
    return tuple(normalized)


def _normalize_missing_parts(values: Iterable[Any] | None) -> tuple[int, ...]:
    normalized: set[int] = set()
    for raw in values or ():
        if isinstance(raw, bool):
            continue
        try:
            index = int(raw)
        except (TypeError, ValueError):
            continue
        if 0 <= index <= 10_000:
            normalized.add(index)
    return tuple(sorted(normalized))


def _json_load_list(value: Any) -> list[Any]:
    try:
        parsed = json.loads(str(value or "[]"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []


def _row_to_item(row: Any) -> RecoveryItem:
    media_refs = tuple(item for item in _json_load_list(row["media_refs_json"]) if isinstance(item, dict))
    missing_parts = _normalize_missing_parts(_json_load_list(row["missing_parts_json"]))
    return RecoveryItem(
        id=int(row["id"]),
        bot_id=str(row["bot_id"]),
        conversation_kind=str(row["conversation_kind"]),
        conversation_id=str(row["conversation_id"]),
        original_message_id=str(row["original_message_id"]),
        normalized_text=str(row["normalized_text"]),
        media_refs=media_refs,
        failure_stage=str(row["failure_stage"]),
        last_failure_stage=str(row["last_failure_stage"]),
        failure_class=str(row["failure_class"]),
        missing_part_indexes=missing_parts,
        route_fingerprint=str(row["route_fingerprint"]),
        first_failure_at=float(row["first_failure_at"]),
        last_failure_at=float(row["last_failure_at"]),
        attempt_count=int(row["attempt_count"]),
        status=str(row["status"]),
        expires_at=float(row["expires_at"]),
        next_attempt_at=float(row["next_attempt_at"]),
        trace_id=str(row["trace_id"]),
        claim_token=str(row["claim_token"]),
        claimed_by=str(row["claimed_by"]),
        claim_started_at=float(row["claim_started_at"]),
        claim_expires_at=float(row["claim_expires_at"]),
        recovered_at=float(row["recovered_at"]),
        updated_at=float(row["updated_at"]),
    )


class ReplyRecoveryQueue:
    """Persistent inbound-message queue for safely regenerating failed replies.

    The queue never accepts or stores the old generated reply. A worker claims
    inbound messages, regenerates from current context, then calls
    :meth:`mark_dispatch_started` immediately before the first external send.
    A stale generation claim may be retried; a stale dispatch claim is always
    quarantined as ``delivery_unknown``.
    """

    def __init__(
        self,
        db_path: str | Path | None = None,
        *,
        clock: Callable[[], float] = time.time,
        ttl_seconds: float = DEFAULT_RECOVERY_TTL_SECONDS,
        claim_lease_seconds: float = DEFAULT_CLAIM_LEASE_SECONDS,
        max_attempts: int = MAX_RECOVERY_ATTEMPTS,
        max_batch_messages: int = MAX_RECOVERY_BATCH_MESSAGES,
        max_batch_chars: int = MAX_RECOVERY_BATCH_CHARS,
    ) -> None:
        self.db_path = Path(db_path or get_db_path())
        self._clock = clock
        self.ttl_seconds = max(1.0, float(ttl_seconds))
        self.claim_lease_seconds = max(1.0, float(claim_lease_seconds))
        self.max_attempts = max(1, min(MAX_RECOVERY_ATTEMPTS, int(max_attempts)))
        self.max_batch_messages = max(
            1,
            min(MAX_RECOVERY_BATCH_MESSAGES, int(max_batch_messages)),
        )
        self.max_batch_chars = max(
            1,
            min(MAX_RECOVERY_BATCH_CHARS, int(max_batch_chars)),
        )
        self._ensure_schema()

    def _timestamp(self, value: float | None = None) -> float:
        return float(self._clock() if value is None else value)

    def _ensure_schema(self) -> None:
        with connect_sync(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS reply_recovery_queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    bot_id TEXT NOT NULL,
                    conversation_kind TEXT NOT NULL
                        CHECK(conversation_kind IN ('group', 'private')),
                    conversation_id TEXT NOT NULL,
                    original_message_id TEXT NOT NULL,
                    normalized_text TEXT NOT NULL DEFAULT '',
                    media_refs_json TEXT NOT NULL DEFAULT '[]',
                    failure_stage TEXT NOT NULL,
                    last_failure_stage TEXT NOT NULL,
                    failure_class TEXT NOT NULL CHECK(failure_class IN (
                        'generation_failed_before_send', 'confirmed_not_sent',
                        'delivery_unknown', 'delivery_partial'
                    )),
                    missing_parts_json TEXT NOT NULL DEFAULT '[]',
                    route_fingerprint TEXT NOT NULL DEFAULT '',
                    first_failure_at REAL NOT NULL,
                    last_failure_at REAL NOT NULL,
                    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(attempt_count >= 0),
                    status TEXT NOT NULL CHECK(status IN (
                        'pending', 'processing', 'dispatching', 'recovered',
                        'quarantined', 'expired', 'exhausted', 'abandoned'
                    )),
                    expires_at REAL NOT NULL,
                    next_attempt_at REAL NOT NULL,
                    trace_id TEXT NOT NULL DEFAULT '',
                    claim_token TEXT NOT NULL DEFAULT '',
                    claimed_by TEXT NOT NULL DEFAULT '',
                    claim_started_at REAL NOT NULL DEFAULT 0,
                    claim_expires_at REAL NOT NULL DEFAULT 0,
                    recovered_at REAL NOT NULL DEFAULT 0,
                    updated_at REAL NOT NULL,
                    UNIQUE(
                        bot_id, conversation_kind, conversation_id,
                        original_message_id, failure_stage
                    )
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_reply_recovery_ready
                ON reply_recovery_queue(
                    status, failure_class, next_attempt_at, expires_at,
                    route_fingerprint, first_failure_at, id
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_reply_recovery_conversation
                ON reply_recovery_queue(
                    bot_id, conversation_kind, conversation_id,
                    route_fingerprint, first_failure_at, id
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_reply_recovery_claim
                ON reply_recovery_queue(claim_token, status)
                """
            )
            conn.commit()

    @staticmethod
    def _clear_claim_sql() -> str:
        return (
            "claim_token='', claimed_by='', claim_started_at=0, "
            "claim_expires_at=0"
        )

    def _maintain_locked(self, conn: Any, now: float) -> None:
        clear_claim = self._clear_claim_sql()
        conn.execute(
            f"""
            UPDATE reply_recovery_queue
            SET status='quarantined', last_failure_stage='recovery_delivery',
                failure_class='delivery_unknown', last_failure_at=?, updated_at=?,
                {clear_claim}
            WHERE status='dispatching' AND claim_expires_at>0 AND claim_expires_at<=?
            """,
            (now, now, now),
        )
        conn.execute(
            f"""
            UPDATE reply_recovery_queue
            SET status=CASE
                    WHEN expires_at<=? THEN 'expired'
                    WHEN attempt_count>=? THEN 'exhausted'
                    ELSE 'pending'
                END,
                last_failure_stage='recovery_generation',
                failure_class='generation_failed_before_send',
                last_failure_at=?, updated_at=?, next_attempt_at=?,
                {clear_claim}
            WHERE status='processing' AND claim_expires_at>0 AND claim_expires_at<=?
            """,
            (now, self.max_attempts, now, now, now, now),
        )
        conn.execute(
            """
            UPDATE reply_recovery_queue
            SET status='expired', updated_at=?
            WHERE status IN ('pending', 'quarantined') AND expires_at<=?
            """,
            (now, now),
        )

    def expire_due(self, *, now: float | None = None) -> int:
        timestamp = self._timestamp(now)
        with connect_sync(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            before = conn.total_changes
            self._maintain_locked(conn, timestamp)
            changed = conn.total_changes - before
            conn.commit()
        return int(changed)

    def record_failure(
        self,
        *,
        bot_id: str,
        conversation_kind: str,
        conversation_id: str,
        original_message_id: str,
        normalized_text: Any = "",
        media_refs: Iterable[Any] | None = None,
        failure_stage: str,
        failure_class: RecoveryFailureClass | str,
        route_fingerprint: str = "",
        trace_id: str = "",
        missing_part_indexes: Iterable[Any] | None = None,
        now: float | None = None,
        next_attempt_at: float | None = None,
    ) -> RecoveryItem:
        scope = _normalize_scope(bot_id, conversation_kind, conversation_id)
        message_id = _required_scalar(
            original_message_id,
            field="original_message_id",
        )
        stage = _required_scalar(failure_stage, field="failure_stage", limit=96)
        classification = _normalize_failure_class(failure_class)
        text = normalize_recovery_text(normalized_text, limit=MAX_RECOVERY_BATCH_CHARS)
        controlled_media = normalize_recovery_media_refs(media_refs)
        if not text and not controlled_media:
            raise ValueError("recovery item requires normalized_text or controlled media_refs")
        timestamp = self._timestamp(now)
        retry_at = timestamp if next_attempt_at is None else max(timestamp, float(next_attempt_at))
        route = _clean_scalar(route_fingerprint, limit=128)
        trace = _clean_scalar(trace_id, limit=128)
        missing_parts = _normalize_missing_parts(missing_part_indexes)
        status = "pending" if classification in RECOVERABLE_FAILURE_CLASSES else "quarantined"
        media_json = json.dumps(controlled_media, ensure_ascii=False, separators=(",", ":"))
        missing_json = json.dumps(missing_parts, separators=(",", ":"))

        with connect_sync(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                existing = conn.execute(
                    """
                    SELECT * FROM reply_recovery_queue
                    WHERE bot_id=? AND conversation_kind=? AND conversation_id=?
                      AND original_message_id=? AND failure_stage=?
                    """,
                    (*scope, message_id, stage),
                ).fetchone()
                if existing is None:
                    cursor = conn.execute(
                        """
                        INSERT INTO reply_recovery_queue(
                            bot_id, conversation_kind, conversation_id,
                            original_message_id, normalized_text, media_refs_json,
                            failure_stage, last_failure_stage, failure_class,
                            missing_parts_json,
                            route_fingerprint, first_failure_at, last_failure_at,
                            attempt_count, status, expires_at, next_attempt_at,
                            trace_id, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?)
                        """,
                        (
                            *scope,
                            message_id,
                            text,
                            media_json,
                            stage,
                            stage,
                            classification,
                            missing_json,
                            route,
                            timestamp,
                            timestamp,
                            status,
                            timestamp + self.ttl_seconds,
                            retry_at,
                            trace,
                            timestamp,
                        ),
                    )
                    item_id = int(cursor.lastrowid)
                else:
                    item_id = int(existing["id"])
                    existing_status = str(existing["status"])
                    if existing_status not in _TERMINAL_STATUSES:
                        next_status = existing_status
                        next_class = str(existing["failure_class"])
                        clear_claim = False
                        if classification in QUARANTINED_FAILURE_CLASSES:
                            next_status = "quarantined"
                            next_class = classification
                            clear_claim = True
                        elif existing_status not in {"processing", "dispatching", "quarantined"}:
                            next_status = "pending"
                            next_class = classification
                        assignment = ""
                        if clear_claim:
                            assignment = ", " + self._clear_claim_sql()
                        conn.execute(
                            f"""
                            UPDATE reply_recovery_queue
                            SET normalized_text=?, media_refs_json=?,
                                last_failure_stage=?, failure_class=?,
                                missing_parts_json=?, route_fingerprint=?,
                                last_failure_at=?, status=?, trace_id=?, updated_at=?
                                {assignment}
                            WHERE id=?
                            """,
                            (
                                text,
                                media_json,
                                stage,
                                next_class,
                                missing_json,
                                route,
                                timestamp,
                                next_status,
                                trace,
                                timestamp,
                                item_id,
                            ),
                        )
                row = conn.execute(
                    "SELECT * FROM reply_recovery_queue WHERE id=?",
                    (item_id,),
                ).fetchone()
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
        if row is None:
            raise RuntimeError("reply recovery item disappeared after record")
        item = _row_to_item(row)
        _publish_recovery_updated((item,), action="recorded")
        return item

    def get(self, item_id: int) -> RecoveryItem | None:
        with connect_sync(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM reply_recovery_queue WHERE id=?",
                (int(item_id),),
            ).fetchone()
        return _row_to_item(row) if row is not None else None

    def list_items(
        self,
        *,
        status: str = "",
        failure_class: str = "",
        bot_id: str = "",
        conversation_kind: str = "",
        conversation_id: str = "",
        limit: int = 200,
        offset: int = 0,
    ) -> list[RecoveryItem]:
        normalized_status = _clean_scalar(status, limit=32).lower()
        if normalized_status and normalized_status not in RECOVERY_STATUSES:
            raise ValueError("invalid reply recovery status")
        normalized_class = _clean_scalar(failure_class, limit=64).lower()
        if normalized_class and normalized_class not in RECOVERY_FAILURE_CLASSES:
            raise ValueError("invalid reply recovery failure class")
        normalized_kind = _clean_scalar(conversation_kind, limit=16).lower()
        if normalized_kind and normalized_kind not in {"group", "private"}:
            raise ValueError("conversation_kind must be group or private")
        clauses = ["1=1"]
        params: list[Any] = []
        for clause, value in (
            ("status=?", normalized_status),
            ("failure_class=?", normalized_class),
            ("bot_id=?", _clean_scalar(bot_id, limit=160)),
            ("conversation_kind=?", normalized_kind),
            ("conversation_id=?", _clean_scalar(conversation_id, limit=160)),
        ):
            if value:
                clauses.append(clause)
                params.append(value)
        params.extend(
            (
                max(1, min(1000, int(limit))),
                max(0, int(offset)),
            )
        )
        with connect_sync(self.db_path) as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM reply_recovery_queue
                WHERE {' AND '.join(clauses)}
                ORDER BY first_failure_at ASC, id ASC
                LIMIT ? OFFSET ?
                """,
                tuple(params),
            ).fetchall()
        return [_row_to_item(row) for row in rows]

    def count_items(
        self,
        *,
        status: str = "",
        failure_class: str = "",
        bot_id: str = "",
        conversation_kind: str = "",
        conversation_id: str = "",
    ) -> int:
        normalized_status = _clean_scalar(status, limit=32).lower()
        if normalized_status and normalized_status not in RECOVERY_STATUSES:
            raise ValueError("invalid reply recovery status")
        normalized_class = _clean_scalar(failure_class, limit=64).lower()
        if normalized_class and normalized_class not in RECOVERY_FAILURE_CLASSES:
            raise ValueError("invalid reply recovery failure class")
        normalized_kind = _clean_scalar(conversation_kind, limit=16).lower()
        if normalized_kind and normalized_kind not in {"group", "private"}:
            raise ValueError("conversation_kind must be group or private")
        clauses = ["1=1"]
        params: list[Any] = []
        for clause, value in (
            ("status=?", normalized_status),
            ("failure_class=?", normalized_class),
            ("bot_id=?", _clean_scalar(bot_id, limit=160)),
            ("conversation_kind=?", normalized_kind),
            ("conversation_id=?", _clean_scalar(conversation_id, limit=160)),
        ):
            if value:
                clauses.append(clause)
                params.append(value)
        with connect_sync(self.db_path) as conn:
            row = conn.execute(
                f"SELECT COUNT(*) AS count FROM reply_recovery_queue WHERE {' AND '.join(clauses)}",
                tuple(params),
            ).fetchone()
        return int(row["count"] if row is not None else 0)

    def status_counts(self) -> dict[str, int]:
        with connect_sync(self.db_path) as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) AS count FROM reply_recovery_queue GROUP BY status"
            ).fetchall()
        result = {status: 0 for status in sorted(RECOVERY_STATUSES)}
        for row in rows:
            result[str(row["status"])] = int(row["count"])
        return result

    def wake_route(self, route_fingerprint: str, *, now: float | None = None) -> int:
        route = _required_scalar(
            route_fingerprint,
            field="route_fingerprint",
            limit=128,
        )
        timestamp = self._timestamp(now)
        with connect_sync(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._maintain_locked(conn, timestamp)
            cursor = conn.execute(
                """
                UPDATE reply_recovery_queue
                SET next_attempt_at=?, updated_at=?
                WHERE route_fingerprint=? AND status='pending'
                  AND failure_class IN ('generation_failed_before_send', 'confirmed_not_sent')
                  AND attempt_count<? AND expires_at>?
                """,
                (timestamp, timestamp, route, self.max_attempts, timestamp),
            )
            conn.commit()
        return int(cursor.rowcount)

    def claim_next_batch(
        self,
        *,
        worker_id: str,
        route_fingerprint: str | None = None,
        now: float | None = None,
    ) -> RecoveryBatch | None:
        worker = _required_scalar(worker_id, field="worker_id", limit=128)
        route_filter = (
            _required_scalar(route_fingerprint, field="route_fingerprint", limit=128)
            if route_fingerprint is not None
            else None
        )
        timestamp = self._timestamp(now)
        claim_token = uuid.uuid4().hex
        lease_expires_at = timestamp + self.claim_lease_seconds

        with connect_sync(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                self._maintain_locked(conn, timestamp)
                route_clause = " AND candidate.route_fingerprint=?" if route_filter is not None else ""
                params: list[Any] = [self.max_attempts, timestamp, timestamp]
                if route_filter is not None:
                    params.append(route_filter)
                anchor = conn.execute(
                    f"""
                    SELECT candidate.* FROM reply_recovery_queue AS candidate
                    WHERE candidate.status='pending'
                      AND candidate.failure_class IN (
                          'generation_failed_before_send', 'confirmed_not_sent'
                      )
                      AND candidate.attempt_count<?
                      AND candidate.expires_at>?
                      AND candidate.next_attempt_at<=?
                      {route_clause}
                      AND NOT EXISTS (
                          SELECT 1 FROM reply_recovery_queue AS active
                          WHERE active.bot_id=candidate.bot_id
                            AND active.conversation_kind=candidate.conversation_kind
                            AND active.conversation_id=candidate.conversation_id
                            AND active.status IN ('processing', 'dispatching')
                      )
                    ORDER BY candidate.first_failure_at ASC, candidate.id ASC
                    LIMIT 1
                    """,
                    tuple(params),
                ).fetchone()
                if anchor is None:
                    conn.commit()
                    return None
                rows = conn.execute(
                    """
                    SELECT * FROM reply_recovery_queue
                    WHERE bot_id=? AND conversation_kind=? AND conversation_id=?
                      AND route_fingerprint=? AND status='pending'
                      AND failure_class IN (
                          'generation_failed_before_send', 'confirmed_not_sent'
                      )
                      AND attempt_count<? AND expires_at>? AND next_attempt_at<=?
                    ORDER BY first_failure_at ASC, id ASC
                    LIMIT ?
                    """,
                    (
                        str(anchor["bot_id"]),
                        str(anchor["conversation_kind"]),
                        str(anchor["conversation_id"]),
                        str(anchor["route_fingerprint"]),
                        self.max_attempts,
                        timestamp,
                        timestamp,
                        self.max_batch_messages,
                    ),
                ).fetchall()
                selected_ids: list[int] = []
                character_count = 0
                for row in rows:
                    text_length = len(str(row["normalized_text"] or ""))
                    if selected_ids and character_count + text_length > self.max_batch_chars:
                        break
                    selected_ids.append(int(row["id"]))
                    character_count += text_length
                    if len(selected_ids) >= self.max_batch_messages:
                        break
                if not selected_ids:
                    conn.commit()
                    return None
                placeholders = ",".join("?" for _ in selected_ids)
                cursor = conn.execute(
                    f"""
                    UPDATE reply_recovery_queue
                    SET status='processing', attempt_count=attempt_count+1,
                        claim_token=?, claimed_by=?, claim_started_at=?,
                        claim_expires_at=?, updated_at=?
                    WHERE id IN ({placeholders}) AND status='pending'
                    """,
                    (
                        claim_token,
                        worker,
                        timestamp,
                        lease_expires_at,
                        timestamp,
                        *selected_ids,
                    ),
                )
                if cursor.rowcount != len(selected_ids):
                    raise RuntimeError("reply recovery batch claim lost atomic ownership")
                claimed_rows = conn.execute(
                    f"""
                    SELECT * FROM reply_recovery_queue
                    WHERE id IN ({placeholders})
                    ORDER BY first_failure_at ASC, id ASC
                    """,
                    tuple(selected_ids),
                ).fetchall()
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
        items = tuple(_row_to_item(row) for row in claimed_rows)
        return RecoveryBatch(
            claim_token=claim_token,
            claimed_by=worker,
            bot_id=str(anchor["bot_id"]),
            conversation_kind=str(anchor["conversation_kind"]),
            conversation_id=str(anchor["conversation_id"]),
            route_fingerprint=str(anchor["route_fingerprint"]),
            items=items,
            character_count=character_count,
            claimed_at=timestamp,
            lease_expires_at=lease_expires_at,
        )

    def renew_claim(self, claim_token: str, *, now: float | None = None) -> float:
        token = _required_scalar(claim_token, field="claim_token", limit=128)
        timestamp = self._timestamp(now)
        lease_expires_at = timestamp + self.claim_lease_seconds
        with connect_sync(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                """
                UPDATE reply_recovery_queue
                SET claim_expires_at=?, updated_at=?
                WHERE claim_token=? AND status IN ('processing', 'dispatching')
                """,
                (lease_expires_at, timestamp, token),
            )
            conn.commit()
        if cursor.rowcount <= 0:
            raise RecoveryClaimError("reply recovery claim is not active")
        return lease_expires_at

    def mark_dispatch_started(
        self,
        claim_token: str,
        *,
        now: float | None = None,
    ) -> tuple[RecoveryItem, ...]:
        token = _required_scalar(claim_token, field="claim_token", limit=128)
        timestamp = self._timestamp(now)
        with connect_sync(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                """
                UPDATE reply_recovery_queue
                SET status='dispatching', claim_expires_at=?, updated_at=?
                WHERE claim_token=? AND status='processing'
                """,
                (timestamp + self.claim_lease_seconds, timestamp, token),
            )
            if cursor.rowcount <= 0:
                conn.rollback()
                raise RecoveryClaimError("reply recovery claim is not generating")
            rows = conn.execute(
                """
                SELECT * FROM reply_recovery_queue
                WHERE claim_token=? ORDER BY first_failure_at ASC, id ASC
                """,
                (token,),
            ).fetchall()
            conn.commit()
        items = tuple(_row_to_item(row) for row in rows)
        _publish_recovery_updated(items, action="dispatch_started")
        return items

    def mark_generation_failed(
        self,
        claim_token: str,
        *,
        trace_id: str = "",
        retry_at: float | None = None,
        now: float | None = None,
    ) -> tuple[RecoveryItem, ...]:
        token = _required_scalar(claim_token, field="claim_token", limit=128)
        timestamp = self._timestamp(now)
        next_attempt = timestamp if retry_at is None else max(timestamp, float(retry_at))
        trace = _clean_scalar(trace_id, limit=128)
        clear_claim = self._clear_claim_sql()
        with connect_sync(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows_before = conn.execute(
                """
                SELECT id FROM reply_recovery_queue
                WHERE claim_token=? AND status='processing'
                ORDER BY first_failure_at ASC, id ASC
                """,
                (token,),
            ).fetchall()
            if not rows_before:
                conn.rollback()
                raise RecoveryClaimError("reply recovery claim is not generating")
            item_ids = [int(row["id"]) for row in rows_before]
            placeholders = ",".join("?" for _ in item_ids)
            cursor = conn.execute(
                f"""
                UPDATE reply_recovery_queue
                SET status=CASE
                        WHEN expires_at<=? THEN 'expired'
                        WHEN attempt_count>=? THEN 'exhausted'
                        ELSE 'pending'
                    END,
                    last_failure_stage='recovery_generation',
                    failure_class='generation_failed_before_send',
                    last_failure_at=?, next_attempt_at=?, trace_id=?, updated_at=?,
                    {clear_claim}
                WHERE id IN ({placeholders}) AND status='processing'
                """,
                (
                    timestamp,
                    self.max_attempts,
                    timestamp,
                    next_attempt,
                    trace,
                    timestamp,
                    *item_ids,
                ),
            )
            if cursor.rowcount != len(item_ids):
                conn.rollback()
                raise RecoveryClaimError("reply recovery generation ownership changed")
            rows = conn.execute(
                f"""
                SELECT * FROM reply_recovery_queue WHERE id IN ({placeholders})
                ORDER BY first_failure_at ASC, id ASC
                """,
                tuple(item_ids),
            ).fetchall()
            conn.commit()
        items = tuple(_row_to_item(row) for row in rows)
        _publish_recovery_updated(items, action="generation_failed")
        return items

    def finalize_delivery(
        self,
        claim_token: str,
        *,
        outcome: RecoveryDeliveryOutcome | str,
        trace_id: str = "",
        missing_part_indexes: Iterable[Any] | None = None,
        retry_at: float | None = None,
        now: float | None = None,
    ) -> tuple[RecoveryItem, ...]:
        token = _required_scalar(claim_token, field="claim_token", limit=128)
        normalized_outcome = _normalize_delivery_outcome(outcome)
        timestamp = self._timestamp(now)
        trace = _clean_scalar(trace_id, limit=128)
        missing_parts = _normalize_missing_parts(missing_part_indexes)
        missing_json = json.dumps(missing_parts, separators=(",", ":"))
        next_attempt = timestamp if retry_at is None else max(timestamp, float(retry_at))
        clear_claim = self._clear_claim_sql()

        with connect_sync(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows_before = conn.execute(
                """
                SELECT id FROM reply_recovery_queue
                WHERE claim_token=? AND status='dispatching'
                ORDER BY first_failure_at ASC, id ASC
                """,
                (token,),
            ).fetchall()
            if not rows_before:
                conn.rollback()
                raise RecoveryClaimError("reply recovery claim is not dispatching")
            item_ids = [int(row["id"]) for row in rows_before]
            placeholders = ",".join("?" for _ in item_ids)
            if normalized_outcome == "confirmed":
                conn.execute(
                    f"""
                    UPDATE reply_recovery_queue
                    SET status='recovered', recovered_at=?, trace_id=?, updated_at=?,
                        {clear_claim}
                    WHERE id IN ({placeholders}) AND status='dispatching'
                    """,
                    (timestamp, trace, timestamp, *item_ids),
                )
            elif normalized_outcome == "confirmed_not_sent":
                conn.execute(
                    f"""
                    UPDATE reply_recovery_queue
                    SET status=CASE
                            WHEN expires_at<=? THEN 'expired'
                            WHEN attempt_count>=? THEN 'exhausted'
                            ELSE 'pending'
                        END,
                        last_failure_stage='recovery_delivery',
                        failure_class='confirmed_not_sent',
                        missing_parts_json='[]', last_failure_at=?,
                        next_attempt_at=?, trace_id=?, updated_at=?,
                        {clear_claim}
                    WHERE id IN ({placeholders}) AND status='dispatching'
                    """,
                    (
                        timestamp,
                        self.max_attempts,
                        timestamp,
                        next_attempt,
                        trace,
                        timestamp,
                        *item_ids,
                    ),
                )
            else:
                conn.execute(
                    f"""
                    UPDATE reply_recovery_queue
                    SET status='quarantined', last_failure_stage='recovery_delivery',
                        failure_class=?, missing_parts_json=?, last_failure_at=?,
                        trace_id=?, updated_at=?, {clear_claim}
                    WHERE id IN ({placeholders}) AND status='dispatching'
                    """,
                    (
                        normalized_outcome,
                        missing_json,
                        timestamp,
                        trace,
                        timestamp,
                        *item_ids,
                    ),
                )
            rows = conn.execute(
                f"""
                SELECT * FROM reply_recovery_queue WHERE id IN ({placeholders})
                ORDER BY first_failure_at ASC, id ASC
                """,
                tuple(item_ids),
            ).fetchall()
            conn.commit()
        items = tuple(_row_to_item(row) for row in rows)
        _publish_recovery_updated(items, action=f"delivery_{normalized_outcome}")
        return items

    def confirm_not_sent(
        self,
        item_ids: Sequence[int],
        *,
        trace_id: str = "",
        now: float | None = None,
    ) -> tuple[RecoveryItem, ...]:
        ids = tuple(dict.fromkeys(int(item_id) for item_id in item_ids))
        if not ids:
            return ()
        timestamp = self._timestamp(now)
        trace = _clean_scalar(trace_id, limit=128)
        placeholders = ",".join("?" for _ in ids)
        with connect_sync(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows_before = conn.execute(
                f"SELECT * FROM reply_recovery_queue WHERE id IN ({placeholders})",
                ids,
            ).fetchall()
            if len(rows_before) != len(ids) or any(
                str(row["status"]) != "quarantined"
                or str(row["failure_class"]) != "delivery_unknown"
                for row in rows_before
            ):
                conn.rollback()
                raise ValueError(
                    "only quarantined delivery_unknown items may be confirmed not sent"
                )
            conn.execute(
                f"""
                UPDATE reply_recovery_queue
                SET status=CASE
                        WHEN expires_at<=? THEN 'expired'
                        WHEN attempt_count>=? THEN 'exhausted'
                        ELSE 'pending'
                    END,
                    last_failure_stage='manual_delivery_review',
                    failure_class='confirmed_not_sent',
                    missing_parts_json='[]', last_failure_at=?,
                    next_attempt_at=?, trace_id=?, updated_at=?
                WHERE id IN ({placeholders})
                """,
                (
                    timestamp,
                    self.max_attempts,
                    timestamp,
                    timestamp,
                    trace,
                    timestamp,
                    *ids,
                ),
            )
            rows = conn.execute(
                f"""
                SELECT * FROM reply_recovery_queue WHERE id IN ({placeholders})
                ORDER BY first_failure_at ASC, id ASC
                """,
                ids,
            ).fetchall()
            conn.commit()
        items = tuple(_row_to_item(row) for row in rows)
        _publish_recovery_updated(items, action="confirmed_not_sent")
        return items

    def abandon(
        self,
        item_ids: Sequence[int],
        *,
        now: float | None = None,
    ) -> int:
        ids = tuple(dict.fromkeys(int(item_id) for item_id in item_ids))
        if not ids:
            return 0
        timestamp = self._timestamp(now)
        placeholders = ",".join("?" for _ in ids)
        clear_claim = self._clear_claim_sql()
        with connect_sync(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                f"""
                UPDATE reply_recovery_queue
                SET status='abandoned', updated_at=?, {clear_claim}
                WHERE id IN ({placeholders}) AND status NOT IN ('recovered', 'expired')
                """,
                (timestamp, *ids),
            )
            conn.commit()
        changed = int(cursor.rowcount)
        if changed:
            _publish_recovery_updated(
                tuple(item for item_id in ids if (item := self.get(item_id)) is not None),
                action="abandoned",
            )
        return changed


__all__ = [
    "DEFAULT_CLAIM_LEASE_SECONDS",
    "DEFAULT_RECOVERY_TTL_SECONDS",
    "MAX_RECOVERY_ATTEMPTS",
    "MAX_RECOVERY_BATCH_CHARS",
    "MAX_RECOVERY_BATCH_MESSAGES",
    "QUARANTINED_FAILURE_CLASSES",
    "RECOVERABLE_FAILURE_CLASSES",
    "RECOVERY_DELIVERY_OUTCOMES",
    "RECOVERY_FAILURE_CLASSES",
    "RECOVERY_STATUSES",
    "RecoveryBatch",
    "RecoveryClaimError",
    "RecoveryItem",
    "ReplyRecoveryQueue",
    "normalize_recovery_media_refs",
    "normalize_recovery_text",
]
