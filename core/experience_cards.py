"""Source-backed experience cards on the existing memory database.

Cards deliberately stay outside generic/global recall. Only exact-scope reads in
memory_context may expose them, and ordinary experiences still need semantic recall.
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass
from typing import Any, Callable

from .memory_store import _connect


@dataclass(frozen=True)
class ExperienceScope:
    platform: str
    bot_id: str
    group_id: str = ""
    user_id: str = ""
    topic_id: str = ""

    def __post_init__(self):
        if not self.platform or not self.bot_id or not (self.group_id or self.user_id):
            raise ValueError("experience_scope_required")
        if self.topic_id and not self.group_id:
            raise ValueError("experience_topic_requires_group")

    def matches(self, item: dict) -> bool:
        return all(str(item.get(k) or "") == v for k, v in asdict(self).items())


class ExperienceConflict(ValueError):
    pass


class ExperienceService:
    def __init__(self, store: Any, source_reader: Callable | None = None):
        self.store = store
        self.source_reader = source_reader or read_source_message

    def list(self, scope: ExperienceScope, *, limit: int = 50, active_only: bool = False) -> list[dict]:
        clauses = ["memory_type='experience_card'"]
        params: list[Any] = []
        for key, value in asdict(scope).items():
            clauses.append(f"COALESCE(json_extract(payload, '$.{key}'), '')=?")
            params.append(value)
        params.append(max(1, min(200, limit)))
        with _connect(self.store.memory_palace_dir / "memory_palace.db") as conn:
            rows = conn.execute(f"SELECT payload FROM memory_items WHERE {' AND '.join(clauses)} ORDER BY updated_at DESC LIMIT ?", params).fetchall()
        items = [json.loads(row["payload"]) for row in rows]
        if active_only:
            items = [item for item in items if not item.get("expires_at") or float(item["expires_at"]) > time.time()]
        return [self.project(item) for item in items]

    @staticmethod
    def project(item: dict) -> dict:
        keys = ("memory_id", "summary", "participants", "source_refs", "time_created", "updated_at", "revision", "confirmation", "kind", "expires_at", "platform", "bot_id", "group_id", "user_id", "topic_id", "revision_history")
        return {key: item.get(key) for key in keys}

    def create(self, scope: ExperienceScope, *, summary: str, source_refs: list[dict], kind: str = "experience", confirmation: str = "pending", expires_at: float = 0) -> dict:
        self._validate(summary, kind, confirmation, expires_at)
        if not source_refs or len(source_refs) > 30:
            raise ValueError("experience_sources_required")
        verified = []
        for ref in source_refs:
            message_id = str(ref.get("message_id") or "")
            source = self.source_reader(scope, message_id)
            if not source or not message_id:
                raise ValueError("experience_source_unavailable")
            # A generated draft or uncertain delivery is never an experience.
            if source.get("is_bot") and source.get("delivery_status") != "confirmed":
                raise ValueError("experience_source_unconfirmed")
            verified.append({"message_id": message_id, "user_id": str(source.get("user_id") or ""), "timestamp": float(source.get("timestamp", source.get("time", 0)) or 0)})
        if scope.user_id and not any(ref["user_id"] == scope.user_id for ref in verified):
            raise ValueError("experience_source_owner_mismatch")
        item = {**asdict(scope), "memory_id": "experience_" + uuid.uuid4().hex,
                "memory_type": "experience_card", "palace_zone": "recent_episode", "summary": summary.strip(),
                "source_kind": "experience_card", "source_refs": verified,
                "participants": sorted({ref["user_id"] for ref in verified if ref["user_id"]}),
                "time_created": min(ref["timestamp"] for ref in verified), "kind": kind,
                "confirmation": confirmation, "expires_at": expires_at, "revision": 1,
                "revision_history": [{"revision": 1, "action": "created", "timestamp": time.time()}],
                "supports_recall": False, "supports_autofill": False, "cross_group_allowed": False,
                "permission_type": "public_preference" if scope.group_id else "private_fact"}
        self.store.write_memory_item(item)
        return self.project(self.store.get_memory_item(item["memory_id"]))

    @staticmethod
    def _validate(summary: str, kind: str, confirmation: str, expires_at: float):
        import math
        if not summary.strip() or len(summary) > 800:
            raise ValueError("experience_summary_invalid")
        if kind not in {"experience", "stable_identity"} or confirmation not in {"pending", "confirmed"}:
            raise ValueError("experience_kind_invalid")
        if not math.isfinite(expires_at) or expires_at < 0:
            raise ValueError("experience_expiry_invalid")
        if kind == "stable_identity" and confirmation == "confirmed" and expires_at <= time.time():
            raise ValueError("experience_identity_requires_expiry")

    def revise(self, scope: ExperienceScope, memory_id: str, *, expected_revision: int, summary: str, confirmation: str | None = None, expires_at: float | None = None) -> dict:
        with self.store.maintenance_lock:
            item = self._get(scope, memory_id, expected_revision)
            self._validate(summary, item["kind"], confirmation or item["confirmation"], item["expires_at"] if expires_at is None else expires_at)
            item.update(summary=summary.strip(), confirmation=confirmation or item["confirmation"], expires_at=item["expires_at"] if expires_at is None else expires_at)
            item["revision_history"] = [*item.get("revision_history", []), {"revision": expected_revision + 1, "action": "revised", "timestamp": time.time()}][-100:]
            self._invalidate(memory_id, delete_root=False)
            self.store.write_memory_item(item)
            return self.project(self.store.get_memory_item(memory_id))

    def forget(self, scope: ExperienceScope, memory_id: str, *, expected_revision: int) -> None:
        with self.store.maintenance_lock:
            self._get(scope, memory_id, expected_revision)
            self._invalidate(memory_id, delete_root=True)

    def _get(self, scope, memory_id, revision):
        item = self.store.get_memory_item(memory_id)
        if not item or item.get("memory_type") != "experience_card" or not scope.matches(item):
            raise KeyError("experience_not_found")
        if int(item.get("revision", 0)) != revision:
            raise ExperienceConflict("experience_revision_conflict")
        return item

    def _invalidate(self, memory_id: str, *, delete_root: bool):
        # No card cache exists. Remove every known derived projection and vector;
        # the text-index delete trigger handles lexical versions atomically.
        with _connect(self.store.memory_palace_dir / "memory_palace.db") as conn:
            rows = conn.execute("SELECT memory_id,payload FROM memory_items").fetchall()
            payloads = {row["memory_id"]: json.loads(row["payload"]) for row in rows}
            affected = {memory_id}
            while True:
                before = len(affected)
                for mid, item in payloads.items():
                    refs = set(str(x) for x in item.get("source_memory_ids", []))
                    refs.update(str(x.get("memory_id") or "") for x in item.get("source_refs", []) if isinstance(x, dict))
                    if refs & affected:
                        affected.add(mid)
                    if mid in affected and item.get("summarized_by"):
                        affected.add(str(item["summarized_by"]))
                if len(affected) == before:
                    break
            for mid in affected:
                for table in ("memory_embeddings", "memory_vector_chunks", "memory_embedding_queue"):
                    conn.execute(f"DELETE FROM {table} WHERE memory_id=?", (mid,))
                if mid == memory_id and not delete_root:
                    continue
                conn.execute("DELETE FROM memory_entities WHERE memory_id=?", (mid,))
                conn.execute("DELETE FROM memory_relations WHERE source_memory_id=? OR target_ref=?", (mid, mid))
                if self.store._fts_available:
                    conn.execute("DELETE FROM memory_fts WHERE memory_id=?", (mid,))
                conn.execute("DELETE FROM memory_items WHERE memory_id=?", (mid,))
            conn.commit()


def read_source_message(scope: ExperienceScope, message_id: str) -> dict | None:
    from .db import connect_sync
    with connect_sync() as conn:
        if scope.group_id:
            rows = conn.execute("SELECT * FROM group_messages WHERE group_id=? AND message_id=? AND platform=? AND bot_id=? ORDER BY timestamp DESC LIMIT 10", (scope.group_id, message_id, scope.platform, scope.bot_id)).fetchall()
            for row in rows:
                item = dict(row)
                if str(item.get("topic_id") or "") == scope.topic_id:
                    return item
        else:
            # Metadata identity must be explicit; legacy unbound history cannot
            # establish permission or prove an experience belongs to this Bot.
            rows = conn.execute("SELECT * FROM session_messages WHERE json_extract(metadata,'$.message_id')=? AND json_extract(metadata,'$.platform')=? AND json_extract(metadata,'$.bot_id')=? AND json_extract(metadata,'$.user_id')=? AND role='user' AND session_id=? ORDER BY timestamp DESC LIMIT 1", (message_id, scope.platform, scope.bot_id, scope.user_id, 'private_' + scope.user_id)).fetchall()
            if rows:
                item = dict(rows[0]); item.update(json.loads(item.pop("metadata")))
                return item
    return None
