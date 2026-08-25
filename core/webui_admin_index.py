from __future__ import annotations

import json
import sqlite3
import threading
import time
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from .paths import get_data_dir


INDEX_FILENAME = "webui_admin_index.db"
SCHEMA_VERSION = 1


class WebUIAdminIndex:
    """Rebuildable, non-authoritative summaries for administration list pages."""

    def __init__(self, plugin_config: Any = None) -> None:
        self.path = Path(get_data_dir(plugin_config)) / INDEX_FILENAME
        self._lock = threading.RLock()
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _init_schema(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS projection_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS persona_summary (
                    qq_id TEXT PRIMARY KEY,
                    nickname TEXT NOT NULL DEFAULT '',
                    nickname_search TEXT NOT NULL DEFAULT '',
                    recent_group_id TEXT NOT NULL DEFAULT '',
                    favorability REAL NOT NULL DEFAULT 0,
                    favorability_level TEXT NOT NULL DEFAULT '',
                    updated_at REAL NOT NULL DEFAULT 0,
                    source TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_admin_persona_nickname ON persona_summary(nickname_search, qq_id);
                CREATE INDEX IF NOT EXISTS idx_admin_persona_updated ON persona_summary(updated_at DESC, qq_id);
                CREATE INDEX IF NOT EXISTS idx_admin_persona_favorability ON persona_summary(favorability DESC, qq_id);
                CREATE TABLE IF NOT EXISTS persona_group (
                    qq_id TEXT NOT NULL,
                    group_id TEXT NOT NULL,
                    PRIMARY KEY (qq_id, group_id)
                );
                CREATE INDEX IF NOT EXISTS idx_admin_persona_group ON persona_group(group_id, qq_id);
                CREATE TABLE IF NOT EXISTS group_summary (
                    group_id TEXT PRIMARY KEY,
                    group_name TEXT NOT NULL DEFAULT '',
                    group_name_search TEXT NOT NULL DEFAULT '',
                    enabled INTEGER NOT NULL DEFAULT 0,
                    membership_state TEXT NOT NULL DEFAULT 'unconfirmed',
                    bot_ids_json TEXT NOT NULL DEFAULT '[]',
                    sources_json TEXT NOT NULL DEFAULT '[]',
                    member_count INTEGER,
                    last_active_at REAL NOT NULL DEFAULT 0,
                    source TEXT NOT NULL DEFAULT 'none',
                    static_config_readonly INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_admin_group_name ON group_summary(group_name_search, group_id);
                CREATE INDEX IF NOT EXISTS idx_admin_group_state ON group_summary(membership_state, enabled, group_id);
                CREATE INDEX IF NOT EXISTS idx_admin_group_activity ON group_summary(last_active_at DESC, group_id);
                CREATE TABLE IF NOT EXISTS group_membership (
                    group_id TEXT NOT NULL,
                    bot_id TEXT NOT NULL,
                    membership_state TEXT NOT NULL,
                    last_confirmed_at REAL NOT NULL DEFAULT 0,
                    PRIMARY KEY (group_id, bot_id)
                );
                CREATE INDEX IF NOT EXISTS idx_admin_membership_bot ON group_membership(bot_id, membership_state, group_id);
                CREATE TABLE IF NOT EXISTS sticker_summary (
                    filename TEXT PRIMARY KEY,
                    filename_search TEXT NOT NULL DEFAULT '',
                    description TEXT NOT NULL DEFAULT '',
                    description_search TEXT NOT NULL DEFAULT '',
                    mood_tags_json TEXT NOT NULL DEFAULT '[]',
                    scene_tags_json TEXT NOT NULL DEFAULT '[]',
                    size_bytes INTEGER NOT NULL DEFAULT 0,
                    modified_at REAL NOT NULL DEFAULT 0,
                    labeled INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_admin_sticker_modified ON sticker_summary(modified_at DESC, filename);
                """
            )
            conn.execute(
                "INSERT INTO projection_meta(key, value) VALUES('schema_version', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(SCHEMA_VERSION),),
            )
            conn.commit()

    def status(self) -> dict[str, Any]:
        with self._connect() as conn:
            meta = {str(row["key"]): str(row["value"]) for row in conn.execute("SELECT key, value FROM projection_meta")}
            counts = {
                table: int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                for table in ("persona_summary", "group_summary", "sticker_summary")
            }
        return {
            "schema_version": int(meta.get("schema_version", SCHEMA_VERSION)),
            "state": meta.get("state", "empty"),
            "indexed_at": float(meta.get("indexed_at", 0) or 0),
            "detail_code": meta.get("detail_code", "admin_index_empty"),
            "counts": counts,
        }

    def rebuild(
        self,
        *,
        personas: Iterable[Mapping[str, Any]],
        groups: Iterable[Mapping[str, Any]],
        stickers: Iterable[Mapping[str, Any]],
    ) -> dict[str, Any]:
        persona_rows = [dict(item) for item in personas]
        group_rows = [dict(item) for item in groups]
        sticker_rows = [dict(item) for item in stickers]
        indexed_at = time.time()
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("INSERT INTO projection_meta(key, value) VALUES('state', 'rebuilding') ON CONFLICT(key) DO UPDATE SET value=excluded.value")
            conn.execute("DELETE FROM persona_group")
            conn.execute("DELETE FROM persona_summary")
            conn.execute("DELETE FROM group_membership")
            conn.execute("DELETE FROM group_summary")
            conn.execute("DELETE FROM sticker_summary")
            for item in persona_rows:
                qq_id = _digits(item.get("qq_id") or item.get("user_id"))
                if not qq_id:
                    continue
                nickname = _text(item.get("nickname"), 160)
                recent_group_id = _digits(item.get("recent_group_id"))
                conn.execute(
                    """
                    INSERT INTO persona_summary(
                        qq_id, nickname, nickname_search, recent_group_id,
                        favorability, favorability_level, updated_at, source
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        qq_id,
                        nickname,
                        nickname.casefold(),
                        recent_group_id,
                        _float(item.get("favorability_score")),
                        _text(item.get("favorability_level"), 80),
                        _float(item.get("updated_at")),
                        _text(item.get("source"), 80),
                    ),
                )
                group_ids = {_digits(value) for value in item.get("group_ids") or []}
                if recent_group_id:
                    group_ids.add(recent_group_id)
                for group_id in sorted(value for value in group_ids if value):
                    conn.execute("INSERT OR IGNORE INTO persona_group(qq_id, group_id) VALUES (?, ?)", (qq_id, group_id))
            for item in group_rows:
                group_id = _digits(item.get("group_id"))
                if not group_id:
                    continue
                group_name = _text(item.get("group_name"), 200)
                bot_ids = sorted({_digits(value) for value in (item.get("bot_ids") or item.get("bot_self_ids") or []) if _digits(value)})
                sources = sorted({_text(value, 80) for value in item.get("sources") or [] if _text(value, 80)})
                membership_state = _text(item.get("membership_state"), 24) or "unconfirmed"
                conn.execute(
                    """
                    INSERT INTO group_summary(
                        group_id, group_name, group_name_search, enabled, membership_state,
                        bot_ids_json, sources_json, member_count, last_active_at, source,
                        static_config_readonly
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        group_id,
                        group_name,
                        group_name.casefold(),
                        int(bool(item.get("enabled"))),
                        membership_state,
                        json.dumps(bot_ids, ensure_ascii=False, separators=(",", ":")),
                        json.dumps(sources, ensure_ascii=False, separators=(",", ":")),
                        _optional_int(item.get("member_count")),
                        _float(item.get("last_active_at") or item.get("freshness")),
                        _text(item.get("source"), 32) or "none",
                        int(bool(item.get("static_config_readonly"))),
                    ),
                )
                for bot_id in bot_ids or [""]:
                    conn.execute(
                        "INSERT INTO group_membership(group_id, bot_id, membership_state, last_confirmed_at) VALUES (?, ?, ?, ?)",
                        (group_id, bot_id, membership_state, _float(item.get("last_active_at") or item.get("freshness"))),
                    )
            for item in sticker_rows:
                filename = _text(item.get("filename"), 240)
                if not filename:
                    continue
                description = _text(item.get("description"), 500)
                mood_tags = [_text(value, 40) for value in item.get("mood_tags") or [] if _text(value, 40)][:20]
                scene_tags = [_text(value, 40) for value in item.get("scene_tags") or [] if _text(value, 40)][:20]
                conn.execute(
                    """
                    INSERT INTO sticker_summary(
                        filename, filename_search, description, description_search,
                        mood_tags_json, scene_tags_json, size_bytes, modified_at, labeled
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        filename,
                        filename.casefold(),
                        description,
                        description.casefold(),
                        json.dumps(mood_tags, ensure_ascii=False, separators=(",", ":")),
                        json.dumps(scene_tags, ensure_ascii=False, separators=(",", ":")),
                        max(0, _int(item.get("size_bytes"))),
                        _float(item.get("modified_at")),
                        int(bool(item.get("labeled"))),
                    ),
                )
            meta = {
                "state": "ready",
                "indexed_at": str(indexed_at),
                "detail_code": "admin_index_ready",
            }
            for key, value in meta.items():
                conn.execute("INSERT INTO projection_meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
            conn.commit()
        return self.status()

    def update_group_enabled(self, group_id: str, enabled: bool, *, source: str = "group_config") -> None:
        normalized = _digits(group_id)
        if not normalized:
            return
        with self._lock, self._connect() as conn:
            conn.execute("UPDATE group_summary SET enabled=?, source=? WHERE group_id=?", (int(enabled), _text(source, 32), normalized))
            conn.commit()

    def upsert_sticker(self, item: Mapping[str, Any]) -> None:
        """Incrementally project one confirmed sticker mutation."""
        filename = _text(item.get("filename"), 240)
        if not filename:
            return
        description = _text(item.get("description"), 500)
        mood_tags = [_text(value, 40) for value in item.get("mood_tags") or [] if _text(value, 40)][:20]
        scene_tags = [_text(value, 40) for value in item.get("scene_tags") or [] if _text(value, 40)][:20]
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO sticker_summary(
                    filename, filename_search, description, description_search,
                    mood_tags_json, scene_tags_json, size_bytes, modified_at, labeled
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(filename) DO UPDATE SET
                    filename_search=excluded.filename_search,
                    description=excluded.description,
                    description_search=excluded.description_search,
                    mood_tags_json=excluded.mood_tags_json,
                    scene_tags_json=excluded.scene_tags_json,
                    size_bytes=excluded.size_bytes,
                    modified_at=excluded.modified_at,
                    labeled=excluded.labeled
                """,
                (
                    filename,
                    filename.casefold(),
                    description,
                    description.casefold(),
                    json.dumps(mood_tags, ensure_ascii=False, separators=(",", ":")),
                    json.dumps(scene_tags, ensure_ascii=False, separators=(",", ":")),
                    max(0, _int(item.get("size_bytes"))),
                    _float(item.get("modified_at")),
                    int(bool(item.get("labeled"))),
                ),
            )
            self._touch_ready(conn, detail_code="admin_index_incremental_sticker")
            conn.commit()

    def delete_sticker(self, filename: str) -> None:
        normalized = _text(filename, 240)
        if not normalized:
            return
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM sticker_summary WHERE filename=?", (normalized,))
            self._touch_ready(conn, detail_code="admin_index_incremental_sticker_delete")
            conn.commit()

    def mark_stale(self, detail_code: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("INSERT INTO projection_meta(key, value) VALUES('state', 'stale') ON CONFLICT(key) DO UPDATE SET value=excluded.value")
            conn.execute("INSERT INTO projection_meta(key, value) VALUES('detail_code', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (_text(detail_code, 120) or "admin_index_stale",))
            conn.commit()

    @staticmethod
    def _touch_ready(conn: sqlite3.Connection, *, detail_code: str) -> None:
        for key, value in (
            ("state", "ready"),
            ("indexed_at", str(time.time())),
            ("detail_code", detail_code),
        ):
            conn.execute("INSERT INTO projection_meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))

    def personas_page(
        self,
        *,
        page: int,
        page_size: int,
        search: str = "",
        group_id: str = "",
        favorability_level: str = "",
        sort_by: str = "updated_at",
        direction: str = "desc",
    ) -> dict[str, Any]:
        order_map = {"updated_at": "p.updated_at", "favorability": "p.favorability", "user_id": "p.qq_id", "qq_id": "p.qq_id"}
        order = order_map.get(sort_by)
        if order is None:
            raise ValueError("persona_sort_invalid")
        clauses = ["1=1"]
        values: list[Any] = []
        needle = str(search or "").strip().casefold()
        if needle:
            clauses.append("(p.qq_id LIKE ? ESCAPE '\\' OR p.nickname_search LIKE ? ESCAPE '\\')")
            values.extend((f"%{_like(needle)}%", f"%{_like(needle)}%"))
        normalized_group = _digits(group_id)
        join = ""
        if normalized_group:
            join = " JOIN persona_group pg ON pg.qq_id=p.qq_id "
            clauses.append("pg.group_id=?")
            values.append(normalized_group)
        if favorability_level:
            clauses.append("p.favorability_level=?")
            values.append(str(favorability_level))
        return self._page_query(
            table=f"persona_summary p {join}",
            select="p.*",
            clauses=clauses,
            values=values,
            order=f"{order} {'ASC' if direction == 'asc' else 'DESC'}, p.qq_id ASC",
            page=page,
            page_size=page_size,
            transform=_persona_item,
        )

    def groups_page(
        self,
        *,
        page: int,
        page_size: int,
        search: str = "",
        membership_state: str = "",
        include_unconfirmed: bool = False,
        enabled: str = "",
        bot_id: str = "",
        sort_by: str = "group_id",
        direction: str = "asc",
    ) -> dict[str, Any]:
        order_map = {"group_id": "g.group_id", "group_name": "g.group_name_search", "freshness": "g.last_active_at", "last_active_at": "g.last_active_at"}
        order = order_map.get(sort_by)
        if order is None:
            raise ValueError("group_sort_invalid")
        clauses = ["1=1"]
        values: list[Any] = []
        needle = str(search or "").strip().casefold()
        if needle:
            clauses.append("(g.group_id LIKE ? ESCAPE '\\' OR g.group_name_search LIKE ? ESCAPE '\\')")
            values.extend((f"%{_like(needle)}%", f"%{_like(needle)}%"))
        if membership_state:
            clauses.append("g.membership_state=?")
            values.append(membership_state)
        elif not include_unconfirmed:
            clauses.append("g.membership_state IN ('confirmed', 'configured')")
        if enabled.lower() in {"true", "1", "enabled"}:
            clauses.append("g.enabled=1")
        elif enabled.lower() in {"false", "0", "disabled"}:
            clauses.append("g.enabled=0")
        join = ""
        normalized_bot = _digits(bot_id)
        if normalized_bot:
            join = " JOIN group_membership gm ON gm.group_id=g.group_id "
            clauses.append("gm.bot_id=?")
            values.append(normalized_bot)
        return self._page_query(
            table=f"group_summary g {join}",
            select="g.*",
            clauses=clauses,
            values=values,
            order=f"{order} {'ASC' if direction == 'asc' else 'DESC'}, g.group_id ASC",
            page=page,
            page_size=page_size,
            transform=_group_item,
        )

    def group_switch_counts(self, *, bot_id: str = "") -> dict[str, int]:
        clauses = ["g.membership_state IN ('confirmed', 'configured')"]
        values: list[Any] = []
        join = ""
        normalized_bot = _digits(bot_id)
        if normalized_bot:
            join = " JOIN group_membership gm ON gm.group_id=g.group_id "
            clauses.append("gm.bot_id=?")
            values.append(normalized_bot)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT g.enabled, COUNT(*) AS count FROM group_summary g {join} WHERE {' AND '.join(clauses)} GROUP BY g.enabled",
                tuple(values),
            ).fetchall()
        counts = {bool(row["enabled"]): int(row["count"] or 0) for row in rows}
        return {"enabled": counts.get(True, 0), "disabled": counts.get(False, 0)}

    def stickers_page(self, *, page: int, page_size: int, search: str = "", labeled: bool | None = None, sort_by: str = "filename", direction: str = "asc") -> dict[str, Any]:
        order_map = {"filename": "filename_search", "modified_at": "modified_at", "size_bytes": "size_bytes"}
        order = order_map.get(sort_by)
        if order is None:
            raise ValueError("sticker_sort_invalid")
        clauses = ["1=1"]
        values: list[Any] = []
        needle = str(search or "").strip().casefold()
        if needle:
            clauses.append("(filename_search LIKE ? ESCAPE '\\' OR description_search LIKE ? ESCAPE '\\' OR mood_tags_json LIKE ? ESCAPE '\\' OR scene_tags_json LIKE ? ESCAPE '\\')")
            token = f"%{_like(needle)}%"
            values.extend((token, token, token, token))
        if labeled is not None:
            clauses.append("labeled=?")
            values.append(int(labeled))
        return self._page_query(table="sticker_summary", select="*", clauses=clauses, values=values, order=f"{order} {'ASC' if direction == 'asc' else 'DESC'}, filename ASC", page=page, page_size=page_size, transform=_sticker_item)

    def _page_query(
        self,
        *,
        table: str,
        select: str,
        clauses: list[str],
        values: list[Any],
        order: str,
        page: int,
        page_size: int,
        transform: Any,
    ) -> dict[str, Any]:
        safe_page = max(1, int(page))
        safe_size = max(1, min(int(page_size), 100))
        offset = (safe_page - 1) * safe_size
        where = " AND ".join(clauses)
        with self._connect() as conn:
            total = int(conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}", tuple(values)).fetchone()[0])
            rows = conn.execute(f"SELECT {select} FROM {table} WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?", (*values, safe_size, offset)).fetchall()
        return {
            "items": [transform(row) for row in rows],
            "page": safe_page,
            "page_size": safe_size,
            "total": total,
            "total_pages": max(1, (total + safe_size - 1) // safe_size),
        }


_INDEXES: dict[str, WebUIAdminIndex] = {}
_INDEXES_LOCK = threading.RLock()


def get_webui_admin_index(plugin_config: Any = None) -> WebUIAdminIndex:
    path = str(Path(get_data_dir(plugin_config)).resolve())
    with _INDEXES_LOCK:
        index = _INDEXES.get(path)
        if index is None:
            index = WebUIAdminIndex(plugin_config)
            _INDEXES[path] = index
        return index


def _text(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _digits(value: Any) -> str:
    text = str(value or "").strip()
    return text if text.isdigit() else ""


def _float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _json_list(value: Any) -> list[str]:
    try:
        parsed = json.loads(str(value or "[]"))
    except Exception:
        return []
    return [str(item or "") for item in parsed if str(item or "")] if isinstance(parsed, list) else []


def _persona_item(row: sqlite3.Row) -> dict[str, Any]:
    score = float(row["favorability"] or 0)
    level = str(row["favorability_level"] or "")
    qq_id = str(row["qq_id"] or "")
    return {
        "qq_id": qq_id,
        "user_id": qq_id,
        "nickname": str(row["nickname"] or qq_id),
        "recent_group_id": str(row["recent_group_id"] or ""),
        "favorability_score": score,
        "favorability_level": level,
        "favorability": {"score": score, "level": level},
        "updated_at": float(row["updated_at"] or 0),
        "source": str(row["source"] or "projection"),
        "cache_only": True,
    }


def _group_item(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "group_id": str(row["group_id"] or ""),
        "group_name": str(row["group_name"] or ""),
        "enabled": bool(row["enabled"]),
        "membership_state": str(row["membership_state"] or "unconfirmed"),
        "bot_ids": _json_list(row["bot_ids_json"]),
        "bot_self_ids": _json_list(row["bot_ids_json"]),
        "sources": _json_list(row["sources_json"]),
        "member_count": int(row["member_count"]) if row["member_count"] is not None else None,
        "last_active_at": float(row["last_active_at"] or 0) or None,
        "freshness": float(row["last_active_at"] or 0),
        "source": str(row["source"] or "none"),
        "static_config_readonly": bool(row["static_config_readonly"]),
        "cache_only": True,
    }


def _sticker_item(row: sqlite3.Row) -> dict[str, Any]:
    filename = str(row["filename"] or "")
    return {
        "filename": filename,
        "size_bytes": int(row["size_bytes"] or 0),
        "modified_at": float(row["modified_at"] or 0),
        "thumbnail_url": f"/personification/api/stickers/file/{filename}",
        "description": str(row["description"] or ""),
        "mood_tags": _json_list(row["mood_tags_json"]),
        "scene_tags": _json_list(row["scene_tags_json"]),
        "labeled": bool(row["labeled"]),
    }


__all__ = ["INDEX_FILENAME", "SCHEMA_VERSION", "WebUIAdminIndex", "get_webui_admin_index"]
