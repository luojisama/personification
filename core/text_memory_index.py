"""Versioned lexical projection; never a model or a substitute vector space."""
from __future__ import annotations

import re
import unicodedata
from typing import Any

VERSION = "zh-char12-latin-v1"
_PARTS = re.compile(r"[\u3400-\u9fff]+|[^\W_]+", re.UNICODE)


def tokens(text: str, *, limit: int = 8192) -> list[str]:
    result: dict[str, None] = {}
    for part in _PARTS.findall(unicodedata.normalize("NFKC", str(text)).casefold()[:32000]):
        if all("\u3400" <= char <= "\u9fff" for char in part):
            values = [part[i:i + 2] for i in range(len(part) - 1)] + list(part)
        else:
            values = [part[:80]]
        for value in values:
            result[value] = None
            if len(result) >= limit:
                return list(result)
    return list(result)


def match_expression(query: str) -> str:
    return " OR ".join('"' + value.replace('"', '""') + '"' for value in tokens(query, limit=128))


def ensure(conn: Any) -> None:
    conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS memory_text_fts_v1 USING fts5(memory_id UNINDEXED, terms)")
    conn.execute("CREATE TABLE IF NOT EXISTS memory_text_versions (memory_id TEXT PRIMARY KEY, version TEXT NOT NULL, updated_at REAL NOT NULL)")
    conn.execute("""CREATE TRIGGER IF NOT EXISTS memory_text_delete_v1 AFTER DELETE ON memory_items
        BEGIN DELETE FROM memory_text_fts_v1 WHERE memory_id=old.memory_id;
        DELETE FROM memory_text_versions WHERE memory_id=old.memory_id; END""")


def write(conn: Any, memory_id: str, text: str, updated_at: float) -> None:
    conn.execute("DELETE FROM memory_text_fts_v1 WHERE memory_id=?", (memory_id,))
    conn.execute("INSERT INTO memory_text_fts_v1 VALUES (?,?)", (memory_id, " ".join(tokens(text))))
    conn.execute("INSERT OR REPLACE INTO memory_text_versions VALUES (?,?,?)", (memory_id, VERSION, updated_at))
