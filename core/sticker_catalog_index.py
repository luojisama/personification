from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Mapping

from .data_store import get_data_store
from .sticker_library import (
    list_local_sticker_files,
    load_sticker_metadata,
    sticker_metadata_path,
)


STORE_NAME = "sticker_catalog_index_v1"
SCHEMA_VERSION = 1


def _safe_stat(path: Path) -> tuple[int, float]:
    try:
        stat = path.stat()
    except OSError:
        return 0, 0.0
    return max(0, int(stat.st_size)), max(0.0, float(stat.st_mtime))


def _source_fingerprint(sticker_dir: Path) -> dict[str, int]:
    try:
        directory_stat = sticker_dir.stat()
        directory_mtime_ns = int(directory_stat.st_mtime_ns)
        directory_ctime_ns = int(directory_stat.st_ctime_ns)
    except OSError:
        directory_mtime_ns = 0
        directory_ctime_ns = 0
    try:
        with os.scandir(sticker_dir) as entries:
            directory_entry_count = sum(1 for _entry in entries)
    except OSError:
        directory_entry_count = 0
    manifest = sticker_metadata_path(sticker_dir)
    try:
        manifest_stat = manifest.stat()
        manifest_mtime_ns = int(manifest_stat.st_mtime_ns)
        manifest_size = max(0, int(manifest_stat.st_size))
    except OSError:
        manifest_mtime_ns = 0
        manifest_size = 0
    return {
        "directory_mtime_ns": directory_mtime_ns,
        "directory_ctime_ns": directory_ctime_ns,
        "directory_entry_count": directory_entry_count,
        "manifest_mtime_ns": manifest_mtime_ns,
        "manifest_size": manifest_size,
    }


def _raw_manifest(sticker_dir: Path) -> dict[str, Any]:
    path = sticker_metadata_path(sticker_dir)
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def rebuild_sticker_catalog_index(sticker_dir: str | Path) -> dict[str, Any]:
    """Scan once in a management task and persist a request-time stat-free index."""

    root = Path(sticker_dir).resolve()
    metadata = load_sticker_metadata(root)
    raw_manifest = _raw_manifest(root)
    items: list[dict[str, Any]] = []
    for file_path in list_local_sticker_files(root, include_gif=True):
        entry = metadata.get(file_path.name)
        normalized = dict(entry) if isinstance(entry, Mapping) else {}
        raw_entry = raw_manifest.get(file_path.name)
        raw = dict(raw_entry) if isinstance(raw_entry, Mapping) else {}
        size_bytes, modified_at = _safe_stat(file_path)
        raw_description = str(raw.get("description", "") or "").strip()
        items.append(
            {
                "filename": file_path.name,
                "size_bytes": size_bytes,
                "modified_at": modified_at,
                "thumbnail_url": f"/personification/api/stickers/file/{file_path.name}",
                "description": raw_description or str(normalized.get("description", "") or ""),
                "mood_tags": [str(item or "")[:40] for item in list(normalized.get("mood_tags") or [])[:20]],
                "scene_tags": [str(item or "")[:40] for item in list(normalized.get("scene_tags") or [])[:20]],
                "proactive_send": bool(normalized.get("proactive_send", False)),
                "use_hint": str(normalized.get("use_hint", "") or "")[:240],
                "avoid_hint": str(normalized.get("avoid_hint", "") or "")[:240],
                "weight": float(normalized.get("weight", 1.0) or 1.0),
                "style": str(normalized.get("style", "") or "")[:80],
                "labeled_at": str(normalized.get("labeled_at", "") or "")[:64],
                "labeled": bool(raw_description),
            }
        )
    items.sort(key=lambda item: str(item.get("filename", "")).casefold())
    snapshot = {
        "schema_version": SCHEMA_VERSION,
        "root": str(root),
        "source": _source_fingerprint(root),
        "updated_at": time.time(),
        "items": items,
    }
    get_data_store().save_sync(STORE_NAME, snapshot)
    return snapshot


def load_sticker_catalog_index(sticker_dir: str | Path) -> dict[str, Any]:
    root = Path(sticker_dir).resolve()
    try:
        loaded = get_data_store().load_sync(STORE_NAME)
    except Exception:
        loaded = {}
    if not isinstance(loaded, dict) or str(loaded.get("root", "") or "") != str(root):
        return {
            "schema_version": SCHEMA_VERSION,
            "root": str(root),
            "source": {},
            "updated_at": 0.0,
            "items": [],
            "stale": True,
        }
    items = loaded.get("items") if isinstance(loaded.get("items"), list) else []
    source = loaded.get("source") if isinstance(loaded.get("source"), dict) else {}
    return {
        "schema_version": SCHEMA_VERSION,
        "root": str(root),
        "source": dict(source),
        "updated_at": float(loaded.get("updated_at", 0) or 0),
        "items": [dict(item) for item in items if isinstance(item, dict)],
        "stale": source != _source_fingerprint(root),
    }


__all__ = [
    "SCHEMA_VERSION",
    "STORE_NAME",
    "load_sticker_catalog_index",
    "rebuild_sticker_catalog_index",
]
