from __future__ import annotations

import hashlib
import hmac
import json
import os
import base64
from contextlib import nullcontext
import sqlite3
import threading
import time
import unicodedata
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, Iterable

from ..db import connect_sync, get_db_path
from ..paths import get_data_transfer_dir
from .constants import (
    DATASETS, DEFAULT_DATASETS, EXCLUDED_CATEGORIES, FORMAT, GROUP_CONFIG_FIELDS,
    GROUP_KV_NAMESPACES, MAX_ARCHIVE_BYTES, MAX_COMPRESSION_RATIO,
    MAX_ENTRIES, MAX_ENTRY_BYTES, PRIMARY_KEYS, TABLE_FIELDS, VERSION,
    ARTIFACT_RETENTION_SECONDS, BACKUP_RETENTION_SECONDS, MEMORY_PAYLOAD_FIELDS,
    PLAN_TOKEN_TTL_SECONDS,
)


class DataTransferError(ValueError):
    pass


_SCOPE_LOCKS: dict[tuple[str, str, str], threading.Lock] = {}
_SCOPE_LOCKS_GUARD = threading.Lock()


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


class DataTransferService:
    def __init__(self, *, data_dir: Path | None = None, db_path: Path | None = None, memory_store: Any = None) -> None:
        self.root = Path(data_dir) if data_dir else get_data_transfer_dir()
        self.db_path = Path(db_path) if db_path else get_db_path()
        self.memory_store = memory_store
        for name in ("exports", "uploads", "backups"):
            (self.root / name).mkdir(parents=True, exist_ok=True)
        self._cleanup_stale_artifacts()
        self._recover_all_interrupted()

    def _conn(self) -> sqlite3.Connection:
        return connect_sync(self.db_path)

    def _scope_lock(self, bot_id: str, group_id: str) -> threading.Lock:
        key = (str(self.db_path.resolve()), str(bot_id), str(group_id))
        with _SCOPE_LOCKS_GUARD:
            return _SCOPE_LOCKS.setdefault(key, threading.Lock())

    def _cleanup_stale_artifacts(self) -> None:
        cutoff = time.time() - ARTIFACT_RETENTION_SECONDS
        for folder in (self.root / "exports", self.root / "uploads"):
            for path in folder.glob("*.zip"):
                try:
                    if path.stat().st_mtime < cutoff:
                        path.unlink(missing_ok=True)
                except OSError:
                    continue
        with self._conn() as conn:
            conn.execute(
                "UPDATE data_transfer_journal SET status='expired',updated_at=? WHERE status='applied' AND updated_at<?",
                (time.time(), time.time() - BACKUP_RETENTION_SECONDS),
            )
            conn.commit()
            active = {str(row[0]) for row in conn.execute(
                "SELECT backup_path FROM data_transfer_journal WHERE status NOT IN ('rolled_back','failed','expired')"
            ).fetchall()}
        for path in (self.root / "backups").iterdir():
            try:
                if path.is_dir() and str(path) not in active and path.stat().st_mtime < cutoff:
                    for child in path.iterdir():
                        child.unlink(missing_ok=True)
                    path.rmdir()
            except OSError:
                continue

    def _plan_secret(self) -> bytes:
        path = self.root / ".plan-token.key"
        try:
            return path.read_bytes()
        except FileNotFoundError:
            secret = os.urandom(32)
            try:
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as stream:
                    stream.write(secret)
                return secret
            except FileExistsError:
                return path.read_bytes()

    def _make_plan_token(self, claims: dict[str, Any]) -> str:
        payload = base64.urlsafe_b64encode(_json_bytes(claims)).rstrip(b"=")
        signature = hmac.new(self._plan_secret(), payload, hashlib.sha256).digest()
        return payload.decode("ascii") + "." + base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii")

    def _verify_plan_token(self, token: str, expected: dict[str, Any]) -> None:
        try:
            encoded, encoded_signature = str(token).split(".", 1)
            payload = encoded.encode("ascii")
            actual = hmac.new(self._plan_secret(), payload, hashlib.sha256).digest()
            expected_signature = base64.urlsafe_b64encode(actual).rstrip(b"=").decode("ascii")
            claims = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
        except Exception as exc:
            raise DataTransferError("invalid plan token") from exc
        if not hmac.compare_digest(expected_signature, encoded_signature) or not isinstance(claims, dict):
            raise DataTransferError("invalid plan token")
        if float(claims.get("expires_at", 0) or 0) < time.time():
            raise DataTransferError("plan token expired")
        if any(claims.get(key) != value for key, value in expected.items()):
            raise DataTransferError("plan token does not match package or target")

    def _task(self, task_id: str, kind: str, status: str, **values: Any) -> None:
        now = time.time()
        metadata = values.pop("metadata", {})
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO data_transfer_tasks(task_id,kind,status,package_id,group_id,bot_id,mode,created_at,updated_at,metadata,error)
                VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(task_id) DO UPDATE SET status=excluded.status,
                package_id=excluded.package_id,updated_at=excluded.updated_at,metadata=excluded.metadata,error=excluded.error""",
                (task_id, kind, status, values.get("package_id", ""), values.get("group_id", ""), values.get("bot_id", ""), values.get("mode", ""), now, now, json.dumps(metadata), values.get("error", "")),
            )
            conn.commit()

    def task_status(self, task_id: str) -> dict[str, Any]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM data_transfer_tasks WHERE task_id=?", (task_id,)).fetchone()
        if not row:
            raise DataTransferError("task not found")
        result = dict(row)
        result["metadata"] = json.loads(result.get("metadata") or "{}")
        return result

    def _table_rows(self, conn: sqlite3.Connection, dataset: str, group_id: str) -> list[dict[str, Any]]:
        fields = TABLE_FIELDS[dataset]
        if dataset == "session_messages":
            rows = conn.execute(f"SELECT {','.join(fields)} FROM session_messages WHERE session_id=? ORDER BY timestamp,id", (f"group_{group_id}",)).fetchall()
        else:
            rows = conn.execute(f"SELECT {','.join(fields)} FROM {dataset} WHERE group_id=? ORDER BY rowid", (group_id,)).fetchall()
        return [dict(row) for row in rows]

    def _group_state(self, conn: sqlite3.Connection, group_id: str) -> dict[str, Any]:
        state: dict[str, Any] = {"group_config": {}, "kv": {}}
        rows = conn.execute(
            "SELECT namespace,value FROM kv_store WHERE key='__root__' AND namespace IN (%s)" % ",".join("?" for _ in ({"group_config"} | GROUP_KV_NAMESPACES)),
            tuple(sorted({"group_config"} | GROUP_KV_NAMESPACES)),
        ).fetchall()
        for row in rows:
            try:
                root = json.loads(row["value"])
            except Exception:
                continue
            if not isinstance(root, dict) or group_id not in root:
                continue
            value = root[group_id]
            if row["namespace"] == "group_config" and isinstance(value, dict):
                state["group_config"] = {k: value[k] for k in GROUP_CONFIG_FIELDS if k in value}
            elif row["namespace"] in GROUP_KV_NAMESPACES:
                state["kv"][row["namespace"]] = value
        return state

    @staticmethod
    def _safe_profile_json(value: Any) -> dict[str, Any]:
        payload = dict(value) if isinstance(value, dict) else {}
        qq_profile = payload.get("qq_profile")
        if isinstance(qq_profile, dict):
            allowed = {"user_id", "nickname", "card", "avatar_url", "homepage_url", "role", "title"}
            payload["qq_profile"] = {key: qq_profile[key] for key in allowed if key in qq_profile}
        return payload

    def _local_profiles(self, group_id: str) -> list[dict[str, Any]]:
        store = self.memory_store
        if store is None or not callable(getattr(store, "list_local_profiles", None)):
            return []
        rows = store.list_local_profiles(group_id)
        return [
            {
                "user_id": str(row.get("user_id", "") or ""),
                "profile_text": str(row.get("profile_text", "") or ""),
                "profile_json": self._safe_profile_json(row.get("profile_json")),
                "updated_at": float(row.get("updated_at", 0) or 0),
            }
            for row in rows if isinstance(row, dict) and str(row.get("user_id", "") or "")
        ]

    def _group_memories(self, group_id: str) -> list[dict[str, Any]]:
        store = self.memory_store
        path = Path(getattr(store, "memory_palace_dir", "")) / "memory_palace.db" if store is not None else Path()
        if store is None or not path.is_file():
            return []
        with sqlite3.connect(path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT payload FROM memory_items WHERE group_id=? ORDER BY updated_at,memory_id",
                (str(group_id),),
            ).fetchall()
        memories: list[dict[str, Any]] = []
        for row in rows:
            try:
                payload = json.loads(row["payload"] or "{}")
            except Exception:
                continue
            if isinstance(payload, dict) and str(payload.get("group_id", "") or "") == str(group_id):
                memories.append(self._safe_memory_payload(payload, group_id))
        return memories

    @staticmethod
    def _safe_memory_payload(payload: Any, group_id: str) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise DataTransferError("invalid memory payload")
        safe = {key: payload[key] for key in MEMORY_PAYLOAD_FIELDS if key in payload}
        safe["memory_id"] = str(safe.get("memory_id", "") or "")
        safe["group_id"] = str(group_id)
        safe["group_scope"] = "isolated"
        safe["cross_group_allowed"] = False
        thread_id = str(safe.get("thread_id", "") or "")
        safe["thread_id"] = thread_id if not thread_id or thread_id.startswith(f"{group_id}:") else ""
        return safe

    def create_export(self, *, bot_id: str, group_id: str, datasets: Iterable[str] | None = None) -> dict[str, Any]:
        bot_id, group_id = str(bot_id).strip(), str(group_id).strip()
        if not bot_id or not group_id:
            raise DataTransferError("bot_id and group_id are required")
        selected = tuple(dict.fromkeys(DEFAULT_DATASETS if datasets is None else datasets))
        if not selected or any(name not in DATASETS for name in selected):
            raise DataTransferError("unknown dataset")
        task_id, package_id = uuid.uuid4().hex, uuid.uuid4().hex
        target = self.root / "exports" / f"{package_id}.zip"
        self._task(task_id, "export", "running", package_id=package_id, group_id=group_id, bot_id=bot_id)
        try:
            payloads: dict[str, bytes] = {}
            with self._conn() as conn:
                for name in selected:
                    if name == "group_state":
                        value = self._group_state(conn, group_id)
                    elif name == "local_user_profiles":
                        value = self._local_profiles(group_id)
                    elif name == "group_memories":
                        value = self._group_memories(group_id)
                    else:
                        value = self._table_rows(conn, name, group_id)
                    payloads[f"datasets/{name}.json"] = _json_bytes(value)
            files = {name: {"sha256": _sha256(data), "size": len(data)} for name, data in payloads.items()}
            manifest = {
                "format": FORMAT, "version": VERSION, "package_id": package_id,
                "source": {"bot_id": bot_id, "group_id": group_id},
                "scope": {"kind": "group", "group_id": group_id},
                "datasets": list(selected), "files": files,
                "checksum": _sha256(_json_bytes(files)), "excluded": list(EXCLUDED_CATEGORIES),
                "created_at": time.time(),
            }
            with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as archive:
                for name, data in payloads.items():
                    with archive.open(name, "w", force_zip64=True) as stream:
                        stream.write(data)
                archive.writestr("manifest.json", _json_bytes(manifest))
            self._task(task_id, "export", "completed", package_id=package_id, group_id=group_id, bot_id=bot_id, metadata={"path": str(target), "manifest": manifest})
            return self.task_status(task_id)
        except Exception as exc:
            target.unlink(missing_ok=True)
            self._task(task_id, "export", "failed", package_id=package_id, group_id=group_id, bot_id=bot_id, error=str(exc))
            raise

    def export_path(self, task_id: str) -> Path:
        task = self.task_status(task_id)
        path = Path(task["metadata"].get("path", ""))
        if task["kind"] != "export" or task["status"] != "completed" or not path.is_file():
            raise DataTransferError("export is not downloadable")
        return path

    def store_upload(self, stream: BinaryIO, *, filename: str = "package.zip") -> dict[str, Any]:
        task_id = uuid.uuid4().hex
        target = self.root / "uploads" / f"{task_id}.zip"
        total = 0
        with target.open("xb") as out:
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_ARCHIVE_BYTES:
                    out.close(); target.unlink(missing_ok=True)
                    raise DataTransferError("archive exceeds size limit")
                out.write(chunk)
        self._task(task_id, "import", "uploaded", metadata={"path": str(target), "filename": Path(filename).name, "size": total})
        return self.task_status(task_id)

    def _validated_package(self, task_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        try:
            result = self._read_validated_package(task_id)
            task = self.task_status(task_id)
            self._task(task_id, "import", "inspected", metadata=task["metadata"])
            return result
        except DataTransferError:
            raise
        except (OSError, KeyError, TypeError, ValueError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
            raise DataTransferError(f"invalid package: {exc}") from exc

    def _read_validated_package(self, task_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        task = self.task_status(task_id)
        path = Path(task["metadata"].get("path", ""))
        if not path.is_file():
            raise DataTransferError("upload missing")
        values: dict[str, Any] = {}
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_ENTRIES:
                raise DataTransferError("too many archive entries")
            names: set[str] = set()
            normalized_names: set[str] = set()
            total_size = 0
            for info in infos:
                name = info.filename
                pure = PurePosixPath(name)
                normalized_name = unicodedata.normalize("NFC", name).casefold()
                if (
                    name in names
                    or normalized_name in normalized_names
                    or pure.is_absolute()
                    or ".." in pure.parts
                    or "\\" in name
                    or "\x00" in name
                    or ":" in pure.parts[0]
                ):
                    raise DataTransferError("unsafe or duplicate archive entry")
                names.add(name)
                normalized_names.add(normalized_name)
                mode = (info.external_attr >> 16) & 0o170000
                if mode == 0o120000:
                    raise DataTransferError("symlink entries are forbidden")
                if info.flag_bits & 0x1:
                    raise DataTransferError("encrypted entries are forbidden")
                total_size += info.file_size
                if total_size > MAX_ARCHIVE_BYTES:
                    raise DataTransferError("expanded archive exceeds size limit")
                if info.file_size > MAX_ENTRY_BYTES or (info.compress_size and info.file_size / info.compress_size > MAX_COMPRESSION_RATIO):
                    raise DataTransferError("archive entry exceeds safety limits")
            if "manifest.json" not in names:
                raise DataTransferError("manifest missing")
            manifest = json.loads(archive.read("manifest.json"))
            self._validate_manifest(manifest)
            declared = set(manifest["files"])
            if names != declared | {"manifest.json"}:
                raise DataTransferError("archive contains undeclared files")
            if manifest["checksum"] != _sha256(_json_bytes(manifest["files"])):
                raise DataTransferError("manifest checksum mismatch")
            for name, expected in manifest["files"].items():
                data = archive.read(name)
                if len(data) != expected["size"] or _sha256(data) != expected["sha256"]:
                    raise DataTransferError(f"checksum mismatch: {name}")
                values[name.removeprefix("datasets/").removesuffix(".json")] = json.loads(data)
        self._validate_values(manifest, values)
        return manifest, values

    def _validate_manifest(self, manifest: Any) -> None:
        if not isinstance(manifest, dict) or manifest.get("format") != FORMAT or manifest.get("version") != VERSION:
            raise DataTransferError("unsupported manifest")
        scope = manifest.get("scope")
        source = manifest.get("source")
        datasets = manifest.get("datasets")
        if not isinstance(scope, dict) or scope.get("kind") != "group" or not str(scope.get("group_id", "")):
            raise DataTransferError("invalid group scope")
        if not isinstance(source, dict) or source.get("group_id") != scope.get("group_id") or not str(source.get("bot_id", "")):
            raise DataTransferError("invalid source identity")
        if not isinstance(datasets, list) or len(datasets) != len(set(datasets)) or any(x not in DATASETS for x in datasets):
            raise DataTransferError("invalid datasets")
        expected = {f"datasets/{name}.json" for name in datasets}
        files = manifest.get("files")
        if not isinstance(files, dict) or set(files) != expected:
            raise DataTransferError("dataset/file declaration mismatch")
        for value in files.values():
            if not isinstance(value, dict) or set(value) != {"sha256", "size"}:
                raise DataTransferError("invalid file declaration")
            if not isinstance(value["sha256"], str) or len(value["sha256"]) != 64 or not isinstance(value["size"], int) or value["size"] < 0:
                raise DataTransferError("invalid file checksum metadata")

    def _validate_values(self, manifest: dict[str, Any], values: dict[str, Any]) -> None:
        group_id = str(manifest["scope"]["group_id"])
        for name, data in values.items():
            if name == "group_state":
                if not isinstance(data, dict) or set(data) - {"group_config", "kv"}:
                    raise DataTransferError("invalid group state")
                if set((data.get("group_config") or {})) - GROUP_CONFIG_FIELDS or set((data.get("kv") or {})) - GROUP_KV_NAMESPACES:
                    raise DataTransferError("group state contains forbidden fields")
                continue
            if name == "local_user_profiles":
                if not isinstance(data, list):
                    raise DataTransferError("local_user_profiles must be a list")
                for row in data:
                    if not isinstance(row, dict) or set(row) != {"user_id", "profile_text", "profile_json", "updated_at"}:
                        raise DataTransferError("invalid local profile")
                    if not str(row.get("user_id", "")) or not isinstance(row.get("profile_json"), dict):
                        raise DataTransferError("invalid local profile identity")
                continue
            if name == "group_memories":
                if not isinstance(data, list):
                    raise DataTransferError("group_memories must be a list")
                for row in data:
                    if not isinstance(row, dict) or str(row.get("group_id", "") or "") != group_id or not str(row.get("memory_id", "") or ""):
                        raise DataTransferError("invalid or cross-group memory")
                    if set(row) - (MEMORY_PAYLOAD_FIELDS | {"group_scope", "cross_group_allowed"}):
                        raise DataTransferError("memory payload contains forbidden fields")
                    if row.get("cross_group_allowed") or row.get("group_scope") not in (None, "isolated"):
                        raise DataTransferError("cross-scope memory is forbidden")
                    thread_id = str(row.get("thread_id", "") or "")
                    if thread_id and not thread_id.startswith(f"{group_id}:"):
                        raise DataTransferError("cross-scope memory thread is forbidden")
                continue
            if not isinstance(data, list):
                raise DataTransferError(f"{name} must be a list")
            fields = set(TABLE_FIELDS[name])
            for row in data:
                if not isinstance(row, dict) or set(row) != fields:
                    raise DataTransferError(f"invalid row schema: {name}")
                scoped = row["session_id"] == f"group_{group_id}" if name == "session_messages" else str(row["group_id"]) == group_id
                if not scoped:
                    raise DataTransferError(f"cross-group row: {name}")

    def inspect(self, task_id: str) -> dict[str, Any]:
        manifest, values = self._validated_package(task_id)
        return {"valid": True, "manifest": manifest, "counts": {k: len(v) if isinstance(v, list) else len(v.get("kv", {})) + bool(v.get("group_config")) for k, v in values.items()}}

    def dry_run(self, task_id: str, *, target_bot_id: str, target_group_id: str, mode: str = "merge", allow_same_identity: bool = False) -> dict[str, Any]:
        manifest, values = self._validated_package(task_id)
        self._check_target(manifest, target_bot_id, target_group_id, mode, allow_same_identity)
        claims = {
            "task_id": task_id, "package_id": manifest["package_id"],
            "package_checksum": manifest["checksum"], "target_bot_id": str(target_bot_id),
            "target_group_id": str(target_group_id), "mode": mode,
            "allow_same_identity": bool(allow_same_identity),
            "expires_at": int(time.time()) + PLAN_TOKEN_TTL_SECONDS,
        }
        return {"valid": True, "mode": mode, "target": {"bot_id": target_bot_id, "group_id": target_group_id}, "changes": {k: len(v) if isinstance(v, list) else 1 for k, v in values.items()}, "activations": [], "plan_token": self._make_plan_token(claims), "plan_expires_at": claims["expires_at"]}

    def _check_target(self, manifest: dict[str, Any], bot_id: str, group_id: str, mode: str, allow_same_identity: bool) -> None:
        if mode not in {"merge", "scope-replace"}:
            raise DataTransferError("unsupported import mode")
        if str(group_id) != str(manifest["scope"]["group_id"]):
            raise DataTransferError("target group must match package scope")
        source_bot = str(manifest["source"]["bot_id"])
        if str(bot_id) != source_bot and not allow_same_identity:
            raise DataTransferError("cross-bot import is denied")

    def validate_target_scope(self, *, target_bot_id: str, target_group_id: str) -> dict[str, str]:
        bot_id, group_id = str(target_bot_id).strip(), str(target_group_id).strip()
        if not bot_id or not group_id:
            raise DataTransferError("target bot and group are required")
        return {"bot_id": bot_id, "group_id": group_id}

    def apply(self, task_id: str, *, target_bot_id: str, target_group_id: str, mode: str = "merge", allow_same_identity: bool = False, plan_token: str = "") -> dict[str, Any]:
        manifest, values = self._validated_package(task_id)
        self._check_target(manifest, target_bot_id, target_group_id, mode, allow_same_identity)
        expected = {"task_id": task_id, "package_id": manifest["package_id"], "package_checksum": manifest["checksum"], "target_bot_id": str(target_bot_id), "target_group_id": str(target_group_id), "mode": mode, "allow_same_identity": bool(allow_same_identity)}
        self._verify_plan_token(plan_token, expected)
        lock = self._scope_lock(target_bot_id, target_group_id)
        if not lock.acquire(blocking=False):
            raise DataTransferError("target scope is busy")
        try:
            return self._apply_locked(task_id, manifest, values, target_bot_id, target_group_id, mode)
        finally:
            lock.release()

    def _apply_locked(self, task_id: str, manifest: dict[str, Any], values: dict[str, Any], target_bot_id: str, target_group_id: str, mode: str) -> dict[str, Any]:
        self._recover_interrupted(target_bot_id, target_group_id)
        with self._conn() as conn:
            prior = conn.execute("SELECT journal_id FROM data_transfer_journal WHERE package_id=? AND group_id=? AND bot_id=? AND mode=? AND status='applied'", (manifest["package_id"], target_group_id, target_bot_id, mode)).fetchone()
        if prior:
            return {"success": True, "journal_id": prior["journal_id"], "idempotent": True}
        journal_id = uuid.uuid4().hex
        backup = self.root / "backups" / journal_id
        backup.mkdir(parents=True, exist_ok=False)
        now = time.time()
        with self._conn() as conn:
            conn.execute("INSERT INTO data_transfer_journal VALUES(?,?,?,?,?,?,?,?,?,?,?)", (journal_id, task_id, manifest["package_id"], target_group_id, target_bot_id, mode, "preparing", str(backup), now, now, "{}"))
            conn.commit()
        try:
            maintenance_lock = getattr(self.memory_store, "maintenance_lock", None)
            with maintenance_lock if maintenance_lock is not None else nullcontext():
                self._check_memory_conflicts(target_group_id, values.get("group_memories", []))
                with self._conn() as conn:
                    conn.execute("BEGIN IMMEDIATE")
                    self._check_row_conflicts(conn, target_group_id, values)
                    detail = self._write_scope_snapshot(conn, backup, target_group_id, values, mode)
                    conn.execute(
                        "UPDATE data_transfer_journal SET status='applying_main',updated_at=?,detail=? WHERE journal_id=?",
                        (time.time(), json.dumps(detail, ensure_ascii=False), journal_id),
                    )
                    for name, data in values.items():
                        if name == "group_state":
                            self._apply_group_state(conn, target_group_id, data, mode)
                        elif name in {"local_user_profiles", "group_memories"}:
                            continue
                        else:
                            self._apply_rows(conn, name, target_group_id, data, mode)
                    conn.commit()
                self._journal(journal_id, "applying_memory", detail)
                self._apply_memory_scope(target_group_id, values, mode)
            self._journal(journal_id, "applied", detail)
            self._task(task_id, "import", "applied", package_id=manifest["package_id"], group_id=target_group_id, bot_id=target_bot_id, mode=mode, metadata={**self.task_status(task_id)["metadata"], "journal_id": journal_id})
            return {"success": True, "journal_id": journal_id}
        except Exception as exc:
            try:
                self._rollback_locked(journal_id)
            except Exception as rollback_exc:
                self._journal(journal_id, "rollback_failed", {"apply_error": str(exc), "rollback_error": str(rollback_exc)})
            raise

    def _journal(self, journal_id: str, status: str, detail: dict[str, Any]) -> None:
        with self._conn() as conn:
            conn.execute("UPDATE data_transfer_journal SET status=?,updated_at=?,detail=? WHERE journal_id=?", (status, time.time(), json.dumps(detail, ensure_ascii=False), journal_id))
            conn.commit()

    def _recover_interrupted(self, bot_id: str, group_id: str) -> None:
        with self._conn() as conn:
            rows = conn.execute("SELECT journal_id,status FROM data_transfer_journal WHERE bot_id=? AND group_id=? AND status IN ('snapshot_ready','applying_main','applying_memory','rollback_started')", (bot_id, group_id)).fetchall()
        for row in rows:
            self._rollback_locked(str(row["journal_id"]))

    def _recover_all_interrupted(self) -> None:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT journal_id,bot_id,group_id FROM data_transfer_journal WHERE status IN ('preparing','snapshot_ready','applying_main','applying_memory','rollback_started')"
            ).fetchall()
        for row in rows:
            lock = self._scope_lock(str(row["bot_id"]), str(row["group_id"]))
            if not lock.acquire(blocking=False):
                continue
            try:
                self._rollback_locked(str(row["journal_id"]))
            finally:
                lock.release()

    def _check_memory_conflicts(self, group_id: str, memories: list[dict[str, Any]]) -> None:
        palace, _local = self._memory_paths(group_id)
        ids = [str(row["memory_id"]) for row in memories]
        if not ids or palace is None or not palace.is_file():
            return
        with sqlite3.connect(palace) as conn:
            for memory_id in ids:
                row = conn.execute("SELECT group_id FROM memory_items WHERE memory_id=?", (memory_id,)).fetchone()
                if row and str(row[0] or "") != str(group_id):
                    raise DataTransferError("memory id belongs to another group")

    def _apply_rows(self, conn: sqlite3.Connection, name: str, group_id: str, rows: list[dict[str, Any]], mode: str) -> None:
        if mode == "scope-replace":
            clause, value = ("session_id=?", f"group_{group_id}") if name == "session_messages" else ("group_id=?", group_id)
            conn.execute(f"DELETE FROM {name} WHERE {clause}", (value,))
        fields = TABLE_FIELDS[name]
        sql = f"INSERT OR REPLACE INTO {name}({','.join(fields)}) VALUES({','.join('?' for _ in fields)})"
        for row in rows:
            conn.execute(sql, tuple(row[field] for field in fields))

    def _check_row_conflicts(self, conn: sqlite3.Connection, group_id: str, values: dict[str, Any]) -> None:
        for name, rows in values.items():
            if name in {"group_state", "local_user_profiles", "group_memories"}:
                continue
            scope_column = "session_id" if name == "session_messages" else "group_id"
            scope_value = f"group_{group_id}" if name == "session_messages" else group_id
            for row in rows:
                key_fields = PRIMARY_KEYS[name]
                conflict = conn.execute(
                    f"SELECT {scope_column} FROM {name} WHERE " + " AND ".join(f"{field}=?" for field in key_fields),
                    tuple(row[field] for field in key_fields),
                ).fetchone()
                if conflict and str(conflict[scope_column]) != scope_value:
                    raise DataTransferError(f"{name} id conflicts with another group")

    def _apply_group_state(self, conn: sqlite3.Connection, group_id: str, state: dict[str, Any], mode: str) -> None:
        incoming = {"group_config": state.get("group_config", {}), **(state.get("kv", {}) or {})}
        for namespace, value in incoming.items():
            row = conn.execute("SELECT value FROM kv_store WHERE namespace=? AND key='__root__'", (namespace,)).fetchone()
            try: root = json.loads(row["value"]) if row else {}
            except Exception: root = {}
            if not isinstance(root, dict): root = {}
            if mode == "merge" and isinstance(root.get(group_id), dict) and isinstance(value, dict):
                root[group_id] = {**root[group_id], **value}
            else:
                root[group_id] = value
            conn.execute("INSERT INTO kv_store(namespace,key,value,updated_at) VALUES(?,'__root__',?,?) ON CONFLICT(namespace,key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at", (namespace, json.dumps(root, ensure_ascii=False), time.time()))

    def _memory_paths(self, group_id: str) -> tuple[Path | None, Path | None]:
        store = self.memory_store
        if store is None:
            return None, None
        palace = Path(getattr(store, "memory_palace_dir", "")) / "memory_palace.db"
        local = Path(getattr(store, "groups_dir", "")) / str(group_id) / "local_user_profiles.db"
        return palace, local

    def _write_scope_snapshot(self, conn: sqlite3.Connection, backup: Path, group_id: str, values: dict[str, Any], mode: str) -> dict[str, Any]:
        snapshot: dict[str, Any] = {"tables": {}, "incoming_keys": {}, "group_state": {}, "profiles": [], "memories": [], "memory_cluster": None}
        for name, rows in values.items():
            if name in {"group_state", "local_user_profiles", "group_memories"}:
                continue
            current = self._table_rows(conn, name, group_id)
            incoming = {tuple(row[field] for field in PRIMARY_KEYS[name]) for row in rows}
            snapshot["incoming_keys"][name] = [list(key) for key in incoming]
            snapshot["tables"][name] = current if mode == "scope-replace" else [row for row in current if tuple(row[field] for field in PRIMARY_KEYS[name]) in incoming]
        if "group_state" in values:
            namespaces = {"group_config", *GROUP_KV_NAMESPACES}
            for namespace in namespaces:
                row = conn.execute("SELECT value FROM kv_store WHERE namespace=? AND key='__root__'", (namespace,)).fetchone()
                try:
                    root = json.loads(row["value"]) if row else {}
                except Exception:
                    root = {}
                snapshot["group_state"][namespace] = {"present": isinstance(root, dict) and group_id in root, "value": root.get(group_id) if isinstance(root, dict) else None}
        profiles = values.get("local_user_profiles")
        if profiles is not None and self.memory_store is not None:
            current_profiles = self._local_profiles(group_id)
            incoming_users = {str(row["user_id"]) for row in profiles}
            snapshot["profile_users"] = sorted(incoming_users)
            snapshot["profiles"] = current_profiles if mode == "scope-replace" else [row for row in current_profiles if str(row["user_id"]) in incoming_users]
        memories = values.get("group_memories")
        palace, _local = self._memory_paths(group_id)
        if memories is not None and palace is not None and palace.is_file():
            incoming_ids = {str(row["memory_id"]) for row in memories}
            snapshot["memory_ids"] = sorted(incoming_ids)
            with sqlite3.connect(palace) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute("SELECT memory_id,payload FROM memory_items WHERE group_id=?", (group_id,)).fetchall()
                snapshot["memories"] = [json.loads(row["payload"] or "{}") for row in rows if mode == "scope-replace" or str(row["memory_id"]) in incoming_ids]
                cluster = conn.execute("SELECT cluster_id,cluster_type,label,payload,updated_at FROM memory_clusters WHERE cluster_id=?", (f"topic:{group_id}",)).fetchone()
                snapshot["memory_cluster"] = dict(cluster) if cluster else None
        payload = _json_bytes(snapshot)
        (backup / "scope-snapshot.json").write_bytes(payload)
        return {"snapshot_sha256": _sha256(payload), "datasets": sorted(values), "mode": mode}

    def _apply_memory_scope(self, group_id: str, values: dict[str, Any], mode: str) -> None:
        store = self.memory_store
        profiles = values.get("local_user_profiles")
        memories = values.get("group_memories")
        if store is None:
            if profiles or memories:
                raise DataTransferError("memory store is unavailable")
            return
        if profiles is not None:
            if mode == "scope-replace":
                _palace, local_path = self._memory_paths(group_id)
                if local_path is not None and local_path.is_file():
                    with sqlite3.connect(local_path) as conn:
                        conn.execute("DELETE FROM profiles")
                        conn.commit()
            for row in profiles:
                store.upsert_local_profile(
                    group_id=group_id,
                    user_id=str(row["user_id"]),
                    profile_text=str(row["profile_text"]),
                    profile_json=self._safe_profile_json(row["profile_json"]),
                )
        if memories is not None:
            palace_path, _local = self._memory_paths(group_id)
            if mode == "scope-replace" and palace_path is not None and palace_path.is_file():
                with sqlite3.connect(palace_path) as conn:
                    ids = [row[0] for row in conn.execute("SELECT memory_id FROM memory_items WHERE group_id=?", (group_id,)).fetchall()]
                    for table, column in (("memory_fts", "memory_id"), ("memory_embeddings", "memory_id"), ("memory_vector_chunks", "memory_id"), ("memory_entities", "memory_id"), ("memory_relations", "source_memory_id")):
                        conn.executemany(f"DELETE FROM {table} WHERE {column}=?", ((item,) for item in ids))
                    conn.execute("DELETE FROM memory_items WHERE group_id=?", (group_id,))
                    conn.commit()
            for item in memories:
                store.write_memory_item(self._safe_memory_payload(item, group_id))

    def rollback(self, journal_id: str) -> dict[str, Any]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM data_transfer_journal WHERE journal_id=?", (journal_id,)).fetchone()
        if not row:
            raise DataTransferError("journal not found")
        if row["status"] == "rolled_back":
            return {"success": True, "journal_id": journal_id, "idempotent": True}
        lock = self._scope_lock(str(row["bot_id"]), str(row["group_id"]))
        if not lock.acquire(blocking=False):
            raise DataTransferError("target scope is busy")
        try:
            return self._rollback_locked(journal_id)
        finally:
            lock.release()

    def _rollback_locked(self, journal_id: str) -> dict[str, Any]:
        maintenance_lock = getattr(self.memory_store, "maintenance_lock", None)
        with maintenance_lock if maintenance_lock is not None else nullcontext():
            return self._rollback_with_maintenance(journal_id)

    def _rollback_with_maintenance(self, journal_id: str) -> dict[str, Any]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM data_transfer_journal WHERE journal_id=?", (journal_id,)).fetchone()
        if not row:
            raise DataTransferError("journal not found")
        if row["status"] == "rolled_back":
            return {"success": True, "journal_id": journal_id, "idempotent": True}
        if row["status"] == "preparing":
            self._journal(journal_id, "failed", {"reason": "interrupted before snapshot"})
            return {"success": True, "journal_id": journal_id, "idempotent": False}
        backup = Path(row["backup_path"])
        path = backup / "scope-snapshot.json"
        if not path.is_file():
            raise DataTransferError("scope snapshot missing")
        snapshot = json.loads(path.read_text(encoding="utf-8"))
        self._journal(journal_id, "rollback_started", json.loads(row["detail"] or "{}"))
        group_id = str(row["group_id"])
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for name, keys in snapshot.get("incoming_keys", {}).items():
                scope_column = "session_id" if name == "session_messages" else "group_id"
                scope_value = f"group_{group_id}" if name == "session_messages" else group_id
                for key in keys:
                    where = " AND ".join(f"{field}=?" for field in PRIMARY_KEYS[name])
                    conn.execute(f"DELETE FROM {name} WHERE {where} AND {scope_column}=?", (*key, scope_value))
                fields = TABLE_FIELDS[name]
                sql = f"INSERT OR REPLACE INTO {name}({','.join(fields)}) VALUES({','.join('?' for _ in fields)})"
                for old in snapshot.get("tables", {}).get(name, []):
                    conn.execute(sql, tuple(old[field] for field in fields))
            for namespace, old in snapshot.get("group_state", {}).items():
                current = conn.execute("SELECT value FROM kv_store WHERE namespace=? AND key='__root__'", (namespace,)).fetchone()
                try:
                    root = json.loads(current["value"]) if current else {}
                except Exception:
                    root = {}
                if not isinstance(root, dict):
                    root = {}
                if old.get("present"):
                    root[group_id] = old.get("value")
                else:
                    root.pop(group_id, None)
                conn.execute("INSERT INTO kv_store(namespace,key,value,updated_at) VALUES(?,'__root__',?,?) ON CONFLICT(namespace,key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at", (namespace, json.dumps(root, ensure_ascii=False), time.time()))
            conn.commit()
        self._restore_memory_snapshot(group_id, snapshot)
        self._journal(journal_id, "rolled_back", {"scope_only": True})
        return {"success": True, "journal_id": journal_id, "idempotent": False}

    def _delete_memory_ids(self, conn: sqlite3.Connection, group_id: str, memory_ids: Iterable[str]) -> None:
        for memory_id in memory_ids:
            owned = conn.execute("SELECT 1 FROM memory_items WHERE memory_id=? AND group_id=?", (memory_id, group_id)).fetchone()
            if not owned:
                continue
            for table, column in (("memory_fts", "memory_id"), ("memory_embeddings", "memory_id"), ("memory_vector_chunks", "memory_id"), ("memory_entities", "memory_id"), ("memory_relations", "source_memory_id")):
                conn.execute(f"DELETE FROM {table} WHERE {column}=?", (memory_id,))
            conn.execute("DELETE FROM memory_items WHERE memory_id=? AND group_id=?", (memory_id, group_id))

    def _restore_memory_snapshot(self, group_id: str, snapshot: dict[str, Any]) -> None:
        store = self.memory_store
        if store is None:
            return
        _palace, local_path = self._memory_paths(group_id)
        profile_users = {str(value) for value in snapshot.get("profile_users", [])}
        if local_path is not None and local_path.is_file() and profile_users:
            with sqlite3.connect(local_path) as conn:
                conn.executemany("DELETE FROM profiles WHERE user_id=?", ((value,) for value in profile_users))
                conn.commit()
        for profile in snapshot.get("profiles", []):
            store.upsert_local_profile(group_id=group_id, user_id=str(profile["user_id"]), profile_text=str(profile["profile_text"]), profile_json=self._safe_profile_json(profile["profile_json"]))
        palace, _local = self._memory_paths(group_id)
        memory_ids = [str(value) for value in snapshot.get("memory_ids", [])]
        if palace is not None and palace.is_file() and memory_ids:
            with sqlite3.connect(palace) as conn:
                self._delete_memory_ids(conn, group_id, memory_ids)
                conn.commit()
            for payload in snapshot.get("memories", []):
                store.write_memory_item(self._safe_memory_payload(payload, group_id))
            with sqlite3.connect(palace) as conn:
                cluster_id = f"topic:{group_id}"
                current = conn.execute("SELECT payload FROM memory_clusters WHERE cluster_id=?", (cluster_id,)).fetchone()
                try:
                    last_id = str(json.loads(current[0] or "{}").get("last_memory_id", "")) if current else ""
                except Exception:
                    last_id = ""
                if not current or last_id in memory_ids:
                    old = snapshot.get("memory_cluster")
                    if old:
                        conn.execute("INSERT OR REPLACE INTO memory_clusters(cluster_id,cluster_type,label,payload,updated_at) VALUES(?,?,?,?,?)", (old["cluster_id"], old["cluster_type"], old["label"], old["payload"], old["updated_at"]))
                    else:
                        conn.execute("DELETE FROM memory_clusters WHERE cluster_id=?", (cluster_id,))
                conn.commit()
