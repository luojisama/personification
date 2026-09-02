from __future__ import annotations

import hashlib
import sqlite3
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from .db import connect_sync, get_db_path


COUNTED_STATUSES = ("reserved", "dispatching", "succeeded", "unknown")
FINAL_STATUSES = frozenset({"succeeded", "definite_failure", "unknown"})


@dataclass(frozen=True)
class QzoneSocialReservation:
    ok: bool
    operation_id: str = ""
    status: str = ""
    diagnostic_code: str = ""
    retry_after_seconds: int = 0


@dataclass(frozen=True)
class QzoneSocialDispatch:
    status: str
    diagnostic_code: str
    operation_id: str = ""

    @property
    def succeeded(self) -> bool:
        return self.status == "succeeded"


class QzoneSocialOperationCoordinator:
    """Durable no-replay coordinator shared by Agent and background QZone writes."""

    def __init__(
        self,
        *,
        db_path: str | Path | None = None,
        clock: Callable[[], float] = time.time,
        timezone_name: str = "Asia/Shanghai",
    ) -> None:
        self.db_path = Path(db_path) if db_path is not None else get_db_path()
        self.clock = clock
        self.timezone_name = str(timezone_name or "Asia/Shanghai")

    def _period_day(self, now: float) -> str:
        try:
            timezone = ZoneInfo(self.timezone_name)
        except Exception:
            timezone = ZoneInfo("Asia/Shanghai")
        return datetime.fromtimestamp(now, timezone).date().isoformat()

    @staticmethod
    def _payload_hash(action: str, comment_text: str) -> str:
        body = str(comment_text or "") if action == "comment" else ""
        return hashlib.sha256(f"{action}\0{body}".encode("utf-8")).hexdigest()

    def reserve(
        self,
        *,
        bot_id: str,
        group_id: str,
        target_uin: str,
        feed_id: str,
        action: str,
        comment_text: str = "",
        group_daily_limit: int = 3,
        target_daily_limit: int = 1,
        target_cooldown_seconds: float = 1800.0,
        now: float | None = None,
    ) -> QzoneSocialReservation:
        timestamp = float(self.clock() if now is None else now)
        bot = str(bot_id or "").strip()
        group = str(group_id or "").strip()
        target = str(target_uin or "").strip()
        feed = str(feed_id or "").strip()
        kind = str(action or "").strip().lower()
        if not bot or not group or not target or not feed or kind not in {"like", "comment"}:
            return QzoneSocialReservation(False, diagnostic_code="qzone_social_invalid_operation")
        period = self._period_day(timestamp)
        counted = COUNTED_STATUSES
        placeholders = ",".join("?" for _ in counted)
        payload_hash = self._payload_hash(kind, comment_text)
        operation_id = f"qzs:{uuid.uuid4().hex}"
        with connect_sync(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            duplicate = conn.execute(
                f"""
                SELECT status FROM qzone_social_operations
                WHERE bot_id=? AND target_uin=? AND feed_id=? AND action_kind=?
                  AND payload_hash=? AND status IN ({placeholders})
                LIMIT 1
                """,
                (bot, target, feed, kind, payload_hash, *counted),
            ).fetchone()
            if duplicate is not None:
                conn.commit()
                return QzoneSocialReservation(
                    False,
                    status=str(duplicate["status"] or ""),
                    diagnostic_code="qzone_social_duplicate_blocked",
                )
            last_row = conn.execute(
                f"""
                SELECT MAX(CASE WHEN dispatch_started_at>0 THEN dispatch_started_at ELSE created_at END) AS last_at
                FROM qzone_social_operations
                WHERE bot_id=? AND target_uin=? AND status IN ({placeholders})
                """,
                (bot, target, *counted),
            ).fetchone()
            last_at = float(last_row["last_at"] or 0.0) if last_row else 0.0
            cooldown = max(0.0, float(target_cooldown_seconds))
            if last_at and timestamp - last_at < cooldown:
                conn.commit()
                return QzoneSocialReservation(
                    False,
                    diagnostic_code="qzone_agent_cooldown",
                    retry_after_seconds=max(1, int(cooldown - (timestamp - last_at))),
                )
            group_count = int(
                conn.execute(
                    f"""
                    SELECT COUNT(*) AS n FROM qzone_social_operations
                    WHERE bot_id=? AND group_id=? AND period_day=? AND status IN ({placeholders})
                    """,
                    (bot, group, period, *counted),
                ).fetchone()["n"]
            )
            if int(group_daily_limit) >= 0 and group_count >= int(group_daily_limit):
                conn.commit()
                return QzoneSocialReservation(False, diagnostic_code="qzone_agent_group_quota_blocked")
            target_count = int(
                conn.execute(
                    f"""
                    SELECT COUNT(*) AS n FROM qzone_social_operations
                    WHERE bot_id=? AND target_uin=? AND period_day=? AND status IN ({placeholders})
                    """,
                    (bot, target, period, *counted),
                ).fetchone()["n"]
            )
            if int(target_daily_limit) >= 0 and target_count >= int(target_daily_limit):
                conn.commit()
                return QzoneSocialReservation(False, diagnostic_code="qzone_agent_target_quota_blocked")
            try:
                conn.execute(
                    """
                    INSERT INTO qzone_social_operations(
                        operation_id,bot_id,group_id,target_uin,feed_id,action_kind,
                        period_day,payload_hash,status,created_at,updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        operation_id,
                        bot,
                        group,
                        target,
                        feed,
                        kind,
                        period,
                        payload_hash,
                        "reserved",
                        timestamp,
                        timestamp,
                    ),
                )
            except sqlite3.IntegrityError:
                conn.commit()
                return QzoneSocialReservation(False, diagnostic_code="qzone_social_duplicate_blocked")
            conn.commit()
        return QzoneSocialReservation(
            True,
            operation_id=operation_id,
            status="reserved",
            diagnostic_code="qzone_social_reserved",
        )

    def mark_dispatching(self, operation_id: str, *, now: float | None = None) -> bool:
        timestamp = float(self.clock() if now is None else now)
        with connect_sync(self.db_path) as conn:
            cursor = conn.execute(
                """
                UPDATE qzone_social_operations
                SET status='dispatching',dispatch_started_at=?,updated_at=?
                WHERE operation_id=? AND status='reserved'
                """,
                (timestamp, timestamp, str(operation_id or "")),
            )
            conn.commit()
        return cursor.rowcount == 1

    def finalize(
        self,
        operation_id: str,
        *,
        status: str,
        result_code: str,
        now: float | None = None,
    ) -> bool:
        final = str(status or "").strip().lower()
        if final not in FINAL_STATUSES:
            raise ValueError("invalid qzone social final status")
        timestamp = float(self.clock() if now is None else now)
        safe_code = str(result_code or "").strip()[:64]
        with connect_sync(self.db_path) as conn:
            cursor = conn.execute(
                """
                UPDATE qzone_social_operations
                SET status=?,result_code=?,completed_at=?,updated_at=?
                WHERE operation_id=? AND status='dispatching'
                """,
                (final, safe_code, timestamp, timestamp, str(operation_id or "")),
            )
            conn.commit()
        return cursor.rowcount == 1

    def snapshot(self, *, bot_id: str, group_id: str, now: float | None = None) -> dict[str, Any]:
        timestamp = float(self.clock() if now is None else now)
        period = self._period_day(timestamp)
        with connect_sync(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT operation_id,action_kind,status,result_code,created_at,updated_at
                FROM qzone_social_operations
                WHERE bot_id=? AND group_id=? AND period_day=?
                ORDER BY created_at DESC LIMIT 20
                """,
                (str(bot_id or ""), str(group_id or ""), period),
            ).fetchall()
        return {
            "period_day": period,
            "count": len(rows),
            "operations": [
                {
                    "operation_id": str(row["operation_id"] or ""),
                    "action": str(row["action_kind"] or ""),
                    "status": str(row["status"] or ""),
                    "result_code": str(row["result_code"] or ""),
                    "created_at": float(row["created_at"] or 0.0),
                    "updated_at": float(row["updated_at"] or 0.0),
                }
                for row in rows
            ],
        }


async def coordinate_qzone_social_write(
    *,
    coordinator: QzoneSocialOperationCoordinator,
    service: Any,
    bot_id: str,
    group_id: str,
    target_uin: str,
    feed: dict[str, Any],
    action: str,
    comment_text: str = "",
    group_daily_limit: int = 3,
    target_daily_limit: int = 1,
    target_cooldown_seconds: float = 1800.0,
) -> QzoneSocialDispatch:
    reservation = coordinator.reserve(
        bot_id=bot_id,
        group_id=group_id,
        target_uin=target_uin,
        feed_id=str(feed.get("feed_id", "") or ""),
        action=action,
        comment_text=comment_text,
        group_daily_limit=group_daily_limit,
        target_daily_limit=target_daily_limit,
        target_cooldown_seconds=target_cooldown_seconds,
    )
    if not reservation.ok:
        return QzoneSocialDispatch(
            "definite_failure",
            reservation.diagnostic_code,
            reservation.operation_id,
        )
    if not coordinator.mark_dispatching(reservation.operation_id):
        return QzoneSocialDispatch("unknown", "qzone_social_dispatch_unknown", reservation.operation_id)
    try:
        if action == "like":
            ok, message = await service.like_feed(feed=feed, bot_id=bot_id)
        else:
            ok, message = await service.comment_feed(
                feed=feed,
                bot_id=bot_id,
                content=comment_text,
            )
    except Exception as exc:
        coordinator.finalize(
            reservation.operation_id,
            status="unknown",
            result_code=f"dispatch_{type(exc).__name__}",
        )
        return QzoneSocialDispatch("unknown", "qzone_social_dispatch_unknown", reservation.operation_id)
    status = "succeeded" if ok else (
        "unknown" if "outcome_unknown" in str(message or "").lower() else "definite_failure"
    )
    coordinator.finalize(
        reservation.operation_id,
        status=status,
        result_code="ok" if ok else status,
    )
    return QzoneSocialDispatch(
        status,
        "qzone_social_dispatch_succeeded"
        if status == "succeeded"
        else "qzone_social_dispatch_unknown"
        if status == "unknown"
        else "qzone_social_dispatch_failed",
        reservation.operation_id,
    )


__all__ = [
    "COUNTED_STATUSES",
    "FINAL_STATUSES",
    "QzoneSocialOperationCoordinator",
    "QzoneSocialDispatch",
    "QzoneSocialReservation",
    "coordinate_qzone_social_write",
]
