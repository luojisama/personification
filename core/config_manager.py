from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
import threading
from pathlib import Path
from typing import Any, Iterable, Mapping

from .config_registry import get_config_entries
from .paths import get_data_dir


_ENV_CONFIG_INFO_ATTR = "_personification_env_config_info"
_ASYNC_LOCKS: dict[str, asyncio.Lock] = {}
_SYNC_LOCKS: dict[str, threading.RLock] = {}
_DEPRECATED_MANAGED_PREFIXES = ("personification_qwen_web_",)


def get_env_config_path(plugin_config: Any) -> Path:
    return Path(get_data_dir(plugin_config)) / "env.json"


def _managed_field_names() -> list[str]:
    return [entry.field_name for entry in get_config_entries("global")]


def _collect_explicit_env_fields(plugin_config: Any) -> set[str]:
    """收集 .env / OS 环境变量 / pydantic 显式声明的插件字段。

    这些字段只用于首次导入 env.json。导入后 env.json 是 WebUI 管理的权威层，
    不再被 .env.prod 里的旧值永久压制。
    """
    explicit: set[str] = set()
    raw_fields = getattr(plugin_config, "__pydantic_fields_set__", None)
    if isinstance(raw_fields, Iterable):
        explicit.update(
            str(field or "").strip()
            for field in raw_fields
            if str(field or "").strip().startswith("personification_")
        )
    for key in os.environ.keys():
        lowered = str(key or "").strip().lower()
        if lowered.startswith("personification_"):
            explicit.add(lowered)
    # 兜底：直接扫描 .env / .env.prod 文件，确保被 env 文件显式声明的字段
    # 永远不会被 env.json 持久化层覆盖。复用 runtime_config 的同名函数避免重复。
    try:
        from .runtime_config import _collect_env_file_keys
        explicit.update(_collect_env_file_keys())
    except Exception:
        pass
    return explicit


def _set_env_config_info(plugin_config: Any, info: dict[str, Any]) -> None:
    try:
        plugin_config.__dict__[_ENV_CONFIG_INFO_ATTR] = dict(info)
    except Exception:
        try:
            object.__setattr__(plugin_config, _ENV_CONFIG_INFO_ATTR, dict(info))
        except Exception:
            return


def get_env_config_load_info(plugin_config: Any) -> dict[str, Any]:
    info = getattr(plugin_config, _ENV_CONFIG_INFO_ATTR, None)
    return dict(info) if isinstance(info, dict) else {}


def _path_key(path: Path) -> str:
    return str(path.resolve())


def _get_async_lock(path: Path) -> asyncio.Lock:
    key = _path_key(path)
    lock = _ASYNC_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _ASYNC_LOCKS[key] = lock
    return lock


def _get_sync_lock(path: Path) -> threading.RLock:
    key = _path_key(path)
    lock = _SYNC_LOCKS.get(key)
    if lock is None:
        lock = threading.RLock()
        _SYNC_LOCKS[key] = lock
    return lock


def _new_load_info(path: Path) -> dict[str, Any]:
    return {
        "path": str(path),
        "applied_fields": [],
        "imported_fields": [],
        "removed_fields": [],
        "skipped_fields": [],
        "errors": [],
        "loaded": False,
        # Set before env.json assignment.  Pydantic records setattr fields,
        # so reading __pydantic_fields_set__ after load is not provenance.
        "pre_load_explicit_fields": [],
        "provenance_fields": [],
        "provenance_unknown": False,
    }


def _restrict_sensitive_file_permissions(path: Path) -> None:
    """Best-effort: keep config files containing tokens/cookies owner-readable only."""
    try:
        os.chmod(path, 0o600)
    except Exception:
        pass


def _write_payload_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=str(path.parent),
        prefix=f".{path.stem}-",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        _restrict_sensitive_file_permissions(Path(tmp_path))
        os.replace(tmp_path, path)
        _restrict_sensitive_file_permissions(path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


_PROVIDER_CATALOG_BACKUP_SUFFIX = ".provider-catalog-v1.bak"


def _pool_is_catalog(value: Any) -> bool:
    return isinstance(value, list) and any(
        isinstance(item, dict) and any(key in item for key in (
            "provider_id", "models", "default_model_id", "purpose_models",
        ))
        for item in value
    )


def _pool_is_legacy(value: Any) -> bool:
    return isinstance(value, list) and any(
        isinstance(item, dict) and not any(key in item for key in (
            "provider_id", "models", "default_model_id", "purpose_models",
        ))
        for item in value
    )


def backup_legacy_provider_catalog_once(path: Path, next_payload: Mapping[str, Any]) -> Path | None:
    """Preserve exact old env.json bytes before its first catalog migration.

    The fixed sibling is deliberately never overwritten.  A failure to create
    it aborts the migration write: credentials in a legacy pool are material
    configuration, not regenerable cache data.
    """
    if not path.exists() or not _pool_is_catalog(next_payload.get("personification_api_pools")):
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(raw, dict) or not _pool_is_legacy(raw.get("personification_api_pools")):
        return None
    backup = path.with_name(path.name + _PROVIDER_CATALOG_BACKUP_SUFFIX)
    if backup.exists():
        return backup
    shutil.copy2(path, backup)
    _restrict_sensitive_file_permissions(backup)
    return backup


class ConfigManager:
    def __init__(self, *, plugin_config: Any, logger: Any) -> None:
        self.plugin_config = plugin_config
        self.logger = logger
        self.path = get_env_config_path(plugin_config)
        self.provenance_path = self.path.with_name(self.path.name + ".provenance.json")
        self._async_lock = _get_async_lock(self.path)
        self._sync_lock = _get_sync_lock(self.path)

    def save(self) -> None:
        with self._sync_lock:
            self._save_unlocked()

    async def reload(self) -> None:
        async with self._async_lock:
            with self._sync_lock:
                self._load_unlocked()

    async def update(self, updates: Mapping[str, Any]) -> None:
        async with self._async_lock:
            with self._sync_lock:
                had_provenance = self._read_provenance_unlocked() is not None
                had_payload = self.path.exists()
                payload = self._managed_payload()
                for field_name, value in dict(updates or {}).items():
                    if field_name in payload:
                        payload[field_name] = value
                backup_legacy_provider_catalog_once(self.path, payload)
                _write_payload_atomic(self.path, payload)
                self._write_provenance_unlocked(
                    set(dict(updates or {})),
                    legacy_snapshot_unknown=not had_provenance and had_payload,
                )
                for field_name, value in payload.items():
                    try:
                        setattr(self.plugin_config, field_name, value)
                    except Exception as exc:
                        if self.logger is not None:
                            self.logger.warning(
                                f"personification: update env config apply failed field={field_name}: {exc}"
                            )

    def load(self) -> None:
        with self._sync_lock:
            self._load_unlocked()

    def _managed_payload(self) -> dict[str, Any]:
        return {
            field_name: getattr(self.plugin_config, field_name, None)
            for field_name in _managed_field_names()
        }

    def _read_provenance_payload_unlocked(self) -> dict[str, Any] | None:
        if not self.provenance_path.exists():
            return None
        try:
            raw = json.loads(self.provenance_path.read_text(encoding="utf-8"))
        except Exception:
            return None
        return raw if isinstance(raw, dict) else None

    def _read_provenance_unlocked(self) -> set[str] | None:
        raw = self._read_provenance_payload_unlocked()
        fields = raw.get("user_fields") if isinstance(raw, dict) else None
        if not isinstance(fields, list):
            return None
        return {str(field) for field in fields if str(field).startswith("personification_")}

    def _write_provenance_unlocked(self, changed_fields: set[str] | None = None, *, legacy_snapshot_unknown: bool = False) -> None:
        existing = self._read_provenance_unlocked() or set()
        existing_payload = self._read_provenance_payload_unlocked() or {}
        fields = existing | {str(field) for field in (changed_fields or set()) if str(field).startswith("personification_")}
        _write_payload_atomic(self.provenance_path, {
            "schema_version": 1,
            "user_fields": sorted(fields),
            "legacy_snapshot_unknown": bool(legacy_snapshot_unknown or existing_payload.get("legacy_snapshot_unknown")),
        })

    def _save_unlocked(self) -> None:
        payload = self._managed_payload()
        had_payload = self.path.exists()
        had_provenance = self._read_provenance_unlocked() is not None
        try:
            backup_legacy_provider_catalog_once(self.path, payload)
            _write_payload_atomic(self.path, payload)
            info = get_env_config_load_info(self.plugin_config)
            initial = set(info.get("pre_load_explicit_fields", []) if isinstance(info, dict) else [])
            # Do not erase uncertainty when saving an old full snapshot.
            self._write_provenance_unlocked(initial, legacy_snapshot_unknown=not had_provenance and had_payload)
        except Exception as exc:
            if self.logger is not None:
                self.logger.warning(f"personification: save env config failed path={self.path}: {exc}")
            return

    def _load_unlocked(self) -> None:
        info = _new_load_info(self.path)
        pre_load_explicit = _collect_explicit_env_fields(self.plugin_config)
        info["pre_load_explicit_fields"] = sorted(pre_load_explicit)
        provenance = self._read_provenance_unlocked()
        if provenance is not None:
            info["provenance_fields"] = sorted(provenance)
            provenance_payload = self._read_provenance_payload_unlocked() or {}
            info["provenance_unknown"] = bool(provenance_payload.get("legacy_snapshot_unknown"))
        had_file = self.path.exists()
        payload: dict[str, Any] = {}
        if not had_file:
            payload = {}
        else:
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
            except Exception as exc:
                info["errors"].append(str(exc))
                _set_env_config_info(self.plugin_config, info)
                if self.logger is not None:
                    self.logger.warning(f"personification: load env config failed path={self.path}: {exc}")
                return
            if isinstance(loaded, dict):
                payload = loaded
            else:
                payload = {}

        managed_fields = set(_managed_field_names())
        removed_fields = sorted(
            field_name
            for field_name in payload
            if any(field_name.startswith(prefix) for prefix in _DEPRECATED_MANAGED_PREFIXES)
        )
        for field_name in removed_fields:
            payload.pop(field_name, None)
        imported_fields: list[str] = []
        for field_name in sorted(pre_load_explicit):
            if field_name not in managed_fields or field_name in payload:
                continue
            if not hasattr(self.plugin_config, field_name):
                continue
            payload[field_name] = getattr(self.plugin_config, field_name, None)
            imported_fields.append(field_name)

        mode_field = "personification_memory_retrieval_mode"
        if mode_field not in payload and mode_field not in pre_load_explicit:
            # Preserve an existing explicitly usable API deployment. New/invalid
            # hash configurations use the complete algorithm path by default.
            if (payload.get("personification_real_embedding_enabled") is True
                and payload.get("personification_embedding_provider") in {"openai", "gemini"}
                and str(payload.get("personification_embedding_model") or "").strip()):
                payload[mode_field] = "hybrid_api"
                imported_fields.append(mode_field)
        if imported_fields or removed_fields:
            try:
                _write_payload_atomic(self.path, payload)
                self._write_provenance_unlocked(set(imported_fields) | set(pre_load_explicit), legacy_snapshot_unknown=False)
            except Exception as exc:
                info["errors"].append(f"managed config migration save failed: {exc}")
                if self.logger is not None:
                    self.logger.warning(
                        f"personification: migrate env.json failed path={self.path}: {exc}"
                    )

        if not had_file and not imported_fields:
            _set_env_config_info(self.plugin_config, info)
            return

        # A pre-provenance full snapshot cannot reveal which legacy settings
        # were chosen.  Preserve it conservatively and make the uncertainty
        # visible; do not infer intent from equal-to-default values.
        if had_file and provenance is None:
            info["provenance_unknown"] = True

        for field_name in _managed_field_names():
            if field_name not in payload:
                continue
            try:
                setattr(self.plugin_config, field_name, payload[field_name])
            except Exception as exc:
                info["errors"].append(f"{field_name}: {exc}")
                continue
            info["applied_fields"].append(field_name)
        info["imported_fields"] = imported_fields
        info["removed_fields"] = removed_fields
        info["loaded"] = True
        _set_env_config_info(self.plugin_config, info)
        if imported_fields and self.logger is not None:
            self.logger.info(
                "personification: imported initial .env fields into env.json; fields="
                + ", ".join(imported_fields)
            )
        if removed_fields and self.logger is not None:
            self.logger.info(
                "personification: removed deprecated managed config fields; fields="
                + ", ".join(removed_fields)
            )
        if had_file and self.logger is not None:
            env_shadowed = sorted(set(info["applied_fields"]) & _collect_explicit_env_fields(self.plugin_config))
            if env_shadowed:
                self.logger.info(
                    "personification: env.json overrides legacy .env bootstrap fields; fields="
                    + ", ".join(env_shadowed)
                )


def save_managed_env_config(plugin_config: Any, logger: Any) -> None:
    ConfigManager(plugin_config=plugin_config, logger=logger).save()


def load_managed_env_config(plugin_config: Any, logger: Any) -> None:
    ConfigManager(plugin_config=plugin_config, logger=logger).load()


__all__ = [
    "ConfigManager",
    "get_env_config_load_info",
    "get_env_config_path",
    "load_managed_env_config",
    "save_managed_env_config",
]
