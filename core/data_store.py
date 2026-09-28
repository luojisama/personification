from __future__ import annotations

import asyncio
import json
import logging
from contextlib import closing
from pathlib import Path
from typing import Any, Callable, Optional

from .db import connect_sync, init_db_sync
from .migration import migrate_all
from .paths import get_data_dir as _get_data_dir


_ROOT_KEY = "__root__"
_LOGGER = logging.getLogger(__name__)


class CorruptDataError(ValueError):
    """An existing namespace contains invalid JSON or an incompatible shape.

    The namespace name and stable reason code are safe diagnostics; stored
    bytes and database paths are intentionally omitted.
    """

    def __init__(self, namespace: str, reason: str) -> None:
        self.namespace = namespace
        self.reason = reason
        super().__init__(f"data_store_corrupt namespace={namespace} reason={reason}")


def _decode_existing(name: str, raw: Any) -> Any:
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError, UnicodeDecodeError) as exc:
        raise CorruptDataError(name, "invalid_json") from exc


class DataStore:
    """
    基于 SQLite kv_store 的命名空间存储。

    对外仍保持 load/save/mutate/update 接口，以兼容原有调用方。
    每个 namespace 仍然表现为“一整个 JSON 文档”，只是在底层存进 SQLite。
    """

    def __init__(self, plugin_config: Any = None, logger: Any = None) -> None:
        self._base = Path(_get_data_dir(plugin_config))
        self._logger = logger
        self._async_locks: dict[str, asyncio.Lock] = {}

    def _alock(self, name: str) -> asyncio.Lock:
        if name not in self._async_locks:
            self._async_locks[name] = asyncio.Lock()
        return self._async_locks[name]

    async def _run_locked_thread(self, func: Callable[..., Any], *args: Any) -> Any:
        worker = asyncio.create_task(asyncio.to_thread(func, *args))
        try:
            return await asyncio.shield(worker)
        except asyncio.CancelledError:
            while not worker.done():
                try:
                    await asyncio.wait({worker})
                except asyncio.CancelledError:
                    continue
            try:
                worker.result()
            except Exception as exc:  # noqa: BLE001 - observe arbitrary worker failure after cancellation
                # Cancellation still owns the caller result, but a failed
                # background write must remain diagnosable without data text.
                try:
                    _LOGGER.warning("data_store_cancelled_worker_failed error_type=%s", type(exc).__name__)
                except Exception:  # noqa: BLE001,S110 - logger failure cannot replace owner cancellation
                    pass
            raise asyncio.CancelledError

    def _read(self, name: str) -> Any:
        with closing(connect_sync()) as conn:
            row = conn.execute(
                "SELECT value FROM kv_store WHERE namespace=? AND key=?",
                (name, _ROOT_KEY),
            ).fetchone()
        if not row:
            return {}
        return _decode_existing(name, row["value"])

    def _write(self, name: str, data: Any) -> None:
        payload = json.dumps(data, ensure_ascii=False)
        with closing(connect_sync()) as conn:
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT value FROM kv_store WHERE namespace=? AND key=?",
                    (name, _ROOT_KEY),
                ).fetchone()
                if row is not None:
                    _decode_existing(name, row["value"])
                conn.execute(
                    """
                    INSERT INTO kv_store(namespace, key, value, updated_at)
                    VALUES (?, ?, ?, unixepoch('now'))
                    ON CONFLICT(namespace, key)
                    DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
                    """,
                    (name, _ROOT_KEY, payload),
                )

    def load_sync(self, name: str) -> Any:
        return self._read(name)

    def save_sync(self, name: str, data: Any) -> None:
        self._write(name, data)

    def mutate_sync(self, name: str, mutator: Callable[[Any], Any]) -> Any:
        with closing(connect_sync()) as conn:
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT value FROM kv_store WHERE namespace=? AND key=?",
                    (name, _ROOT_KEY),
                ).fetchone()
                current = {} if row is None else _decode_existing(name, row["value"])
                updated = mutator(current)
                if updated is None:
                    updated = current
                payload = json.dumps(updated, ensure_ascii=False)
                conn.execute(
                    """
                    INSERT INTO kv_store(namespace, key, value, updated_at)
                    VALUES (?, ?, ?, unixepoch('now'))
                    ON CONFLICT(namespace, key)
                    DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
                    """,
                    (name, _ROOT_KEY, payload),
                )
                return updated

    def update_sync(self, name: str, patch: dict[str, Any]) -> dict[str, Any]:
        def _mutate(current: Any) -> dict[str, Any]:
            if not isinstance(current, dict):
                raise CorruptDataError(name, "expected_object")
            data = current
            data.update(patch)
            return data

        return self.mutate_sync(name, _mutate)

    async def load(self, name: str) -> Any:
        async with self._alock(name):
            return await asyncio.to_thread(self.load_sync, name)

    async def save(self, name: str, data: Any) -> None:
        async with self._alock(name):
            await self._run_locked_thread(self.save_sync, name, data)

    async def mutate(self, name: str, mutator: Callable[[Any], Any]) -> Any:
        async with self._alock(name):
            return await self._run_locked_thread(self.mutate_sync, name, mutator)

    async def update(self, name: str, patch: dict[str, Any]) -> dict[str, Any]:
        async with self._alock(name):
            return await self._run_locked_thread(self.update_sync, name, patch)


_store: Optional[DataStore] = None


def init_data_store(plugin_config: Any, logger: Any = None) -> DataStore:
    global _store
    data_dir = _get_data_dir(plugin_config)
    init_db_sync(data_dir)
    migrate_all(data_dir, logger=logger or _SilentLogger())
    _store = DataStore(plugin_config, logger=logger)
    return _store


def get_data_store() -> DataStore:
    if _store is None:
        raise RuntimeError("DataStore not initialized. Call init_data_store() first.")
    return _store


class _SilentLogger:
    def warning(self, _msg: str) -> None:
        return None
