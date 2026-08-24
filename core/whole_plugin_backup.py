from __future__ import annotations

"""Whole-plugin 状态包与秘密包的纯核心协议。

这个模块故意不读取插件配置、数据库、运行时数据目录或系统凭据。调用方必须显式
提供待打包的数据、归档目标和恢复后端，因此单元测试可以完全运行在内存或独立临时
目录中。状态包与秘密包使用同一份严格 manifest/ZIP 校验器，但秘密内容只会以
Scrypt + AES-256-GCM 密文进入归档。
"""

import base64
import copy
import hashlib
import hmac
import io
import json
import math
import os
import re
import stat
import threading
import time
import unicodedata
import uuid
import zipfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Protocol, runtime_checkable
from urllib.parse import parse_qsl, urlsplit


PACKAGE_FORMAT = "personification-whole-plugin-backup"
PACKAGE_VERSION = 1
STATE_PACKAGE = "state"
SECRET_PACKAGE = "secret"
MANIFEST_PATH = "manifest.json"
SECRET_DATASET_NAME = "portable_secrets"
SECRET_PAYLOAD_PATH = "secrets/payload.aesgcm"

DEFAULT_STATE_EXCLUSIONS: tuple[str, ...] = (
    "api_keys",
    "cookies",
    "webui_sessions",
    "verification_codes",
    "logs",
    "full_traces",
    "temporary_caches",
    "pid_files",
    "build_artifacts",
    "os_keyring",
    "browser_profiles",
)

DEFAULT_SECRET_EXCLUSIONS: tuple[str, ...] = (
    "webui_sessions",
    "verification_codes",
    "logs",
    "full_traces",
    "temporary_caches",
    "pid_files",
    "build_artifacts",
    "os_keyring",
    "browser_profiles",
)

DEFAULT_REAUTH_REQUIRED: tuple[str, ...] = (
    "os_keyring",
    "chromium_profile",
    "device_bound_login",
)

_PACKAGE_TYPES = frozenset({STATE_PACKAGE, SECRET_PACKAGE})
_JOURNAL_STATUSES = frozenset(
    {
        "preparing",
        "snapshot_ready",
        "applying",
        "health_check",
        "applied",
        "rollback_started",
        "rolled_back",
        "failed",
        "outcome_unknown",
    }
)
_INCOMPLETE_JOURNAL_STATUSES = frozenset(
    {"preparing", "snapshot_ready", "applying", "health_check", "rollback_started"}
)
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_HEX_64_RE = re.compile(r"^[0-9a-f]{64}$")
_HEX_32_RE = re.compile(r"^[0-9a-f]{32}$")

_SECRET_KEY_TOKENS = frozenset(
    {
        "apikey",
        "accesstoken",
        "refreshtoken",
        "token",
        "secret",
        "clientsecret",
        "password",
        "passwd",
        "cookie",
        "cookies",
        "authorization",
        "proxyauthorization",
        "pskey",
        "skey",
        "credential",
        "credentials",
        "verificationcode",
        "sessiontoken",
        "webuisession",
        "headers",
        "proxy",
        "authpath",
    }
)
_SECRET_QUERY_KEYS = frozenset(
    {
        "access_token",
        "refresh_token",
        "api_key",
        "apikey",
        "client_secret",
        "code",
        "cookie",
        "credential",
        "key",
        "password",
        "sig",
        "signature",
        "token",
        "x-amz-signature",
    }
)
_REAUTH_KEY_MARKERS: tuple[tuple[str, str], ...] = (
    ("oskeyring", "os_keyring"),
    ("keyring", "os_keyring"),
    ("chromiumprofile", "chromium_profile"),
    ("chromeprofile", "chromium_profile"),
    ("browserprofile", "chromium_profile"),
    ("deviceboundlogin", "device_bound_login"),
)
class WholePluginBackupError(ValueError):
    """带稳定诊断码、且不会回显底层异常文本的可见错误。"""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        self.code = str(code)
        self.message = str(message)
        self.details = dict(details or {})
        super().__init__(f"{self.message}（诊断码：{self.code}）")

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": False,
            "code": self.code,
            "message": self.message,
            "details": copy.deepcopy(self.details),
        }


def _error(code: str, message: str, **details: Any) -> WholePluginBackupError:
    return WholePluginBackupError(code, message, details=details)


@dataclass(frozen=True)
class ArchiveLimits:
    max_archive_bytes: int = 256 * 1024 * 1024
    max_manifest_bytes: int = 1024 * 1024
    max_entries: int = 512
    max_entry_bytes: int = 128 * 1024 * 1024
    max_expanded_bytes: int = 512 * 1024 * 1024
    max_compression_ratio: float = 200.0
    max_path_length: int = 240
    read_chunk_bytes: int = 1024 * 1024

    def __post_init__(self) -> None:
        positive_ints = (
            self.max_archive_bytes,
            self.max_manifest_bytes,
            self.max_entries,
            self.max_entry_bytes,
            self.max_expanded_bytes,
            self.max_path_length,
            self.read_chunk_bytes,
        )
        if any(isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in positive_ints):
            raise ValueError("archive limits must be positive integers")
        if not math.isfinite(self.max_compression_ratio) or self.max_compression_ratio <= 1:
            raise ValueError("max_compression_ratio must be finite and greater than one")


@dataclass(frozen=True)
class ScryptParameters:
    n: int = 2**15
    r: int = 8
    p: int = 1
    length: int = 32

    def validate(self) -> None:
        if (
            isinstance(self.n, bool)
            or not isinstance(self.n, int)
            or self.n < 2**14
            or self.n > 2**16
            or self.n & (self.n - 1)
            or self.r != 8
            or self.p < 1
            or self.p > 2
            or self.length != 32
        ):
            raise _error("secret_kdf_parameters_invalid", "秘密包的密钥派生参数不安全或不受支持")


@dataclass(frozen=True)
class PackageDependency:
    name: str
    version: str = ""
    required: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "version": self.version, "required": self.required}


@dataclass(frozen=True)
class SplitBackupPayload:
    state: Any
    secrets: Any
    reauth_required: tuple[str, ...]
    excluded_secret_fields: int


@dataclass(frozen=True)
class PackageInspection:
    package_type: str
    package_id: str
    package_sha256: str
    source_bot_id: str
    schema_version: str
    dataset_names: tuple[str, ...]
    encrypted: bool
    payload_size: int
    payload_sha256: str
    dependencies: tuple[PackageDependency, ...]
    exclusions: tuple[str, ...]
    reauth_required: tuple[str, ...]
    manifest: Mapping[str, Any] = field(repr=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": True,
            "code": "whole_backup_package_valid",
            "message": "备份包校验通过",
            "package_type": self.package_type,
            "package_id": self.package_id,
            "package_sha256": self.package_sha256,
            "source_bot_id": self.source_bot_id,
            "schema_version": self.schema_version,
            "dataset_names": list(self.dataset_names),
            "encrypted": self.encrypted,
            "payload_size": self.payload_size,
            "payload_sha256": self.payload_sha256,
            "dependencies": [item.to_dict() for item in self.dependencies],
            "exclusions": list(self.exclusions),
            "reauth_required": list(self.reauth_required),
            "manifest": copy.deepcopy(dict(self.manifest)),
        }


@dataclass(frozen=True)
class PreflightReport:
    can_apply: bool = True
    changes: Mapping[str, int] = field(default_factory=dict)
    conflicts: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    schema_migrations: tuple[str, ...] = ()

    def normalized(self) -> PreflightReport:
        changes: dict[str, int] = {}
        for key, value in self.changes.items():
            safe_key = _safe_machine_id(key, "restore_preflight_change_invalid")
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise _error("restore_preflight_change_invalid", "恢复预检返回了非法变更计数")
            changes[safe_key] = value
        return PreflightReport(
            can_apply=bool(self.can_apply),
            changes=dict(sorted(changes.items())),
            conflicts=_safe_code_list(self.conflicts, "restore_preflight_conflict_invalid"),
            warnings=_safe_code_list(self.warnings, "restore_preflight_warning_invalid"),
            schema_migrations=_safe_code_list(
                self.schema_migrations,
                "restore_preflight_migration_invalid",
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        normalized = self.normalized()
        return {
            "can_apply": normalized.can_apply,
            "changes": dict(normalized.changes),
            "conflicts": list(normalized.conflicts),
            "warnings": list(normalized.warnings),
            "schema_migrations": list(normalized.schema_migrations),
        }


@dataclass(frozen=True)
class DryRunPlan:
    plan_id: str
    package_id: str
    package_type: str
    package_sha256: str
    payload_sha256: str
    target_fingerprint: str
    dataset_names: tuple[str, ...]
    preflight: PreflightReport
    created_at: float
    expires_at: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": True,
            "code": "whole_backup_dry_run_ready",
            "message": "恢复预检已完成",
            "plan_id": self.plan_id,
            "package_id": self.package_id,
            "package_type": self.package_type,
            "package_sha256": self.package_sha256,
            "payload_sha256": self.payload_sha256,
            "target_fingerprint": self.target_fingerprint,
            "dataset_names": list(self.dataset_names),
            "preflight": self.preflight.to_dict(),
            "created_at": self.created_at,
            "expires_at": self.expires_at,
        }


@dataclass(frozen=True)
class SnapshotReference:
    reference: str
    checksum: str = ""

    def normalized(self) -> SnapshotReference:
        reference = _safe_machine_id(self.reference, "restore_snapshot_reference_invalid")
        checksum = str(self.checksum or "").strip().lower()
        if checksum and not _HEX_64_RE.fullmatch(checksum):
            raise _error("restore_snapshot_reference_invalid", "恢复快照引用无效")
        return SnapshotReference(reference=reference, checksum=checksum)


@dataclass(frozen=True)
class HealthCheckReport:
    ok: bool
    code: str = "restore_health_ok"

    def normalized(self) -> HealthCheckReport:
        return HealthCheckReport(
            ok=bool(self.ok),
            code=_safe_machine_id(self.code, "restore_health_code_invalid"),
        )


@dataclass(frozen=True)
class RestoreJournalRecord:
    journal_id: str
    plan_id: str
    package_id: str
    package_type: str
    package_sha256: str
    target_fingerprint: str
    status: str
    snapshot_reference: str = ""
    snapshot_checksum: str = ""
    diagnostic_code: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0

    def validate(self) -> None:
        _safe_machine_id(self.journal_id, "restore_journal_invalid")
        if not _HEX_64_RE.fullmatch(self.plan_id):
            raise _error("restore_journal_invalid", "恢复 journal 内容无效")
        if not _HEX_32_RE.fullmatch(self.package_id):
            raise _error("restore_journal_invalid", "恢复 journal 内容无效")
        if self.package_type not in _PACKAGE_TYPES:
            raise _error("restore_journal_invalid", "恢复 journal 内容无效")
        if not _HEX_64_RE.fullmatch(self.package_sha256):
            raise _error("restore_journal_invalid", "恢复 journal 内容无效")
        if not _HEX_64_RE.fullmatch(self.target_fingerprint):
            raise _error("restore_journal_invalid", "恢复 journal 内容无效")
        if self.status not in _JOURNAL_STATUSES:
            raise _error("restore_journal_invalid", "恢复 journal 状态无效")
        if self.snapshot_reference:
            SnapshotReference(self.snapshot_reference, self.snapshot_checksum).normalized()
        if self.diagnostic_code:
            _safe_machine_id(self.diagnostic_code, "restore_journal_invalid")
        if not all(math.isfinite(value) and value >= 0 for value in (self.created_at, self.updated_at)):
            raise _error("restore_journal_invalid", "恢复 journal 时间无效")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> RestoreJournalRecord:
        expected = {field_name for field_name in cls.__dataclass_fields__}
        if not isinstance(value, Mapping) or set(value) != expected:
            raise _error("restore_journal_invalid", "恢复 journal 内容无效")
        try:
            record = cls(**dict(value))
        except (TypeError, ValueError) as exc:
            raise _error("restore_journal_invalid", "恢复 journal 内容无效") from exc
        record.validate()
        return record


@dataclass(frozen=True)
class ApplyResult:
    journal_id: str
    status: str
    idempotent: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.status in {"applied", "rolled_back"},
            "code": "whole_backup_apply_completed" if self.status == "applied" else "whole_backup_rollback_completed",
            "message": "全量恢复已完成" if self.status == "applied" else "恢复操作已回滚",
            "journal_id": self.journal_id,
            "status": self.status,
            "idempotent": self.idempotent,
        }


@runtime_checkable
class BackupFileSystem(Protocol):
    def read_bytes(self, path: str | Path) -> bytes: ...

    def write_bytes_atomic(self, path: str | Path, payload: bytes) -> None: ...

    def exists(self, path: str | Path) -> bool: ...


class LocalBackupFileSystem:
    """只操作调用方显式传入的路径，不提供任何插件数据目录默认值。"""

    def read_bytes(self, path: str | Path) -> bytes:
        return Path(path).read_bytes()

    def write_bytes_atomic(self, path: str | Path, payload: bytes) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
        try:
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
            try:
                os.chmod(target, 0o600)
            except OSError:
                pass
        finally:
            temporary.unlink(missing_ok=True)

    def exists(self, path: str | Path) -> bool:
        return Path(path).is_file()


@runtime_checkable
class RestoreJournalStore(Protocol):
    def upsert(self, record: RestoreJournalRecord) -> None: ...

    def get(self, journal_id: str) -> RestoreJournalRecord | None: ...

    def find_by_plan(self, plan_id: str) -> tuple[RestoreJournalRecord, ...]: ...

    def list_records(self) -> tuple[RestoreJournalRecord, ...]: ...


class InMemoryRestoreJournalStore:
    def __init__(self) -> None:
        self._records: dict[str, RestoreJournalRecord] = {}
        self._lock = threading.RLock()

    def upsert(self, record: RestoreJournalRecord) -> None:
        record.validate()
        with self._lock:
            self._records[record.journal_id] = record

    def get(self, journal_id: str) -> RestoreJournalRecord | None:
        with self._lock:
            return self._records.get(str(journal_id))

    def find_by_plan(self, plan_id: str) -> tuple[RestoreJournalRecord, ...]:
        with self._lock:
            return tuple(record for record in self._records.values() if record.plan_id == plan_id)

    def list_records(self) -> tuple[RestoreJournalRecord, ...]:
        with self._lock:
            return tuple(sorted(self._records.values(), key=lambda item: (item.created_at, item.journal_id)))


class JsonRestoreJournalStore:
    """显式路径上的小型原子 journal；不会保存包内容或秘密。"""

    def __init__(self, path: str | Path, *, file_system: BackupFileSystem | None = None) -> None:
        self.path = path
        self.file_system = file_system or LocalBackupFileSystem()
        self._lock = threading.RLock()

    def _read_all(self) -> dict[str, RestoreJournalRecord]:
        if not self.file_system.exists(self.path):
            return {}
        try:
            raw = self.file_system.read_bytes(self.path)
            payload = _strict_json_loads(raw, code="restore_journal_invalid", message="恢复 journal 内容无效")
        except WholePluginBackupError:
            raise
        except Exception as exc:
            raise _error("restore_journal_unavailable", "无法读取恢复 journal") from exc
        if not isinstance(payload, dict):
            raise _error("restore_journal_invalid", "恢复 journal 内容无效")
        records: dict[str, RestoreJournalRecord] = {}
        for key, value in payload.items():
            if not isinstance(key, str) or not isinstance(value, Mapping):
                raise _error("restore_journal_invalid", "恢复 journal 内容无效")
            record = RestoreJournalRecord.from_dict(value)
            if record.journal_id != key:
                raise _error("restore_journal_invalid", "恢复 journal 内容无效")
            records[key] = record
        return records

    def _write_all(self, records: Mapping[str, RestoreJournalRecord]) -> None:
        payload = _canonical_json({key: value.to_dict() for key, value in sorted(records.items())})
        try:
            self.file_system.write_bytes_atomic(self.path, payload)
        except WholePluginBackupError:
            raise
        except Exception as exc:
            raise _error("restore_journal_unavailable", "无法写入恢复 journal") from exc

    def upsert(self, record: RestoreJournalRecord) -> None:
        record.validate()
        with self._lock:
            records = self._read_all()
            records[record.journal_id] = record
            self._write_all(records)

    def get(self, journal_id: str) -> RestoreJournalRecord | None:
        with self._lock:
            return self._read_all().get(str(journal_id))

    def find_by_plan(self, plan_id: str) -> tuple[RestoreJournalRecord, ...]:
        with self._lock:
            records = self._read_all().values()
            return tuple(record for record in records if record.plan_id == plan_id)

    def list_records(self) -> tuple[RestoreJournalRecord, ...]:
        with self._lock:
            return tuple(sorted(self._read_all().values(), key=lambda item: (item.created_at, item.journal_id)))


@runtime_checkable
class AtomicRestoreBackend(Protocol):
    """真实数据恢复由集成层实现；核心只协调快照、应用、健康检查和回滚。"""

    def target_fingerprint(self) -> str: ...

    def preflight(
        self,
        manifest: Mapping[str, Any],
        datasets: Mapping[str, Any],
    ) -> PreflightReport: ...

    def create_snapshot(
        self,
        manifest: Mapping[str, Any],
        datasets: Mapping[str, Any],
    ) -> SnapshotReference: ...

    def apply_datasets(
        self,
        manifest: Mapping[str, Any],
        datasets: Mapping[str, Any],
        snapshot: SnapshotReference,
    ) -> None: ...

    def health_check(self, manifest: Mapping[str, Any]) -> HealthCheckReport: ...

    def rollback_snapshot(self, snapshot: SnapshotReference) -> None: ...


@dataclass(frozen=True)
class _ValidatedPackage:
    raw: bytes = field(repr=False)
    package_sha256: str
    manifest: Mapping[str, Any]
    datasets: Mapping[str, Any] | None = field(default=None, repr=False)


class _Missing:
    pass


_MISSING = _Missing()


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError) as exc:
        raise _error("backup_json_value_invalid", "备份数据不是有效的 JSON 值") from exc


def _strict_json_loads(raw: bytes, *, code: str, message: str) -> Any:
    def reject_duplicate(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise _error(code, message)
            result[key] = value
        return result

    def reject_constant(_value: str) -> Any:
        raise _error(code, message)

    try:
        text = raw.decode("utf-8")
        return json.loads(
            text,
            object_pairs_hook=reject_duplicate,
            parse_constant=reject_constant,
        )
    except WholePluginBackupError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError) as exc:
        raise _error(code, message) from exc


def _normal_key(value: Any) -> str:
    return "".join(character for character in str(value or "").casefold() if character.isalnum())


def _secret_key(value: str) -> bool:
    normalized = _normal_key(value)
    if normalized in _SECRET_KEY_TOKENS:
        return True
    suffixes = (
        "apikey",
        "accesstoken",
        "refreshtoken",
        "clientsecret",
        "password",
        "cookie",
        "authpath",
    )
    return any(normalized.endswith(marker) for marker in suffixes)


def _reauth_category(value: str) -> str:
    normalized = _normal_key(value)
    for marker, category in _REAUTH_KEY_MARKERS:
        if marker in normalized:
            return category
    return ""


def _string_contains_url_credentials(value: str) -> bool:
    text = str(value or "").strip()
    if "://" not in text:
        return False
    try:
        parsed = urlsplit(text)
    except ValueError:
        return True
    if parsed.username is not None or parsed.password is not None:
        return True
    try:
        query = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=False)
    except ValueError:
        return True
    return any(key.casefold() in _SECRET_QUERY_KEYS for key, _value in query)


def _split_value(value: Any) -> tuple[Any, Any, set[str], int]:
    if isinstance(value, Mapping):
        state: dict[str, Any] = {}
        secrets: dict[str, Any] = {}
        reauth: set[str] = set()
        excluded_count = 0
        for raw_key, item in value.items():
            if not isinstance(raw_key, str):
                raise _error("backup_json_key_invalid", "备份对象只能使用字符串字段名")
            reauth_category = _reauth_category(raw_key)
            if reauth_category:
                reauth.add(reauth_category)
                excluded_count += 1
                continue
            if _dataset_exclusion_category(raw_key):
                excluded_count += 1
                continue
            if _secret_key(raw_key):
                _canonical_json(item)
                secrets[raw_key] = copy.deepcopy(item)
                excluded_count += 1
                continue
            state_item, secret_item, child_reauth, child_count = _split_value(item)
            reauth.update(child_reauth)
            excluded_count += child_count
            if state_item is not _MISSING:
                state[raw_key] = state_item
            if secret_item is not _MISSING:
                secrets[raw_key] = secret_item
        return (
            state,
            secrets if secrets else _MISSING,
            reauth,
            excluded_count,
        )
    if isinstance(value, list):
        state_items: list[Any] = []
        secret_items: list[Any] = []
        has_secret = False
        reauth: set[str] = set()
        excluded_count = 0
        for item in value:
            state_item, secret_item, child_reauth, child_count = _split_value(item)
            state_items.append(None if state_item is _MISSING else state_item)
            secret_items.append(None if secret_item is _MISSING else secret_item)
            has_secret = has_secret or secret_item is not _MISSING
            reauth.update(child_reauth)
            excluded_count += child_count
        return state_items, secret_items if has_secret else _MISSING, reauth, excluded_count
    if isinstance(value, tuple):
        return _split_value(list(value))
    if isinstance(value, str) and _string_contains_url_credentials(value):
        return _MISSING, value, set(), 1
    _canonical_json(value)
    return copy.deepcopy(value), _MISSING, set(), 0


def split_state_and_secrets(value: Any) -> SplitBackupPayload:
    """把结构化输入拆为可公开状态与秘密；不会修改原始对象。"""

    state, secrets, reauth, excluded_count = _split_value(value)
    if state is _MISSING:
        state = None
    if secrets is _MISSING:
        secrets = {}
    return SplitBackupPayload(
        state=state,
        secrets=secrets,
        reauth_required=tuple(sorted(set(DEFAULT_REAUTH_REQUIRED) | reauth)),
        excluded_secret_fields=excluded_count,
    )


def _strip_nonportable_secrets(value: Any) -> tuple[Any, set[str]]:
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        reauth: set[str] = set()
        for key, item in value.items():
            if not isinstance(key, str):
                raise _error("backup_json_key_invalid", "秘密对象只能使用字符串字段名")
            category = _reauth_category(key)
            if category:
                reauth.add(category)
                continue
            normalized_key = _normal_key(key)
            if normalized_key in {
                "verificationcode",
                "verificationcodes",
                "captchacode",
                "oauthcode",
            } or _dataset_exclusion_category(key):
                continue
            cleaned, child_reauth = _strip_nonportable_secrets(item)
            result[key] = cleaned
            reauth.update(child_reauth)
        return result, reauth
    if isinstance(value, list):
        result_list: list[Any] = []
        reauth: set[str] = set()
        for item in value:
            cleaned, child_reauth = _strip_nonportable_secrets(item)
            result_list.append(cleaned)
            reauth.update(child_reauth)
        return result_list, reauth
    if isinstance(value, tuple):
        return _strip_nonportable_secrets(list(value))
    _canonical_json(value)
    return copy.deepcopy(value), set()


def _dataset_exclusion_category(name: str) -> str:
    normalized = _normal_key(name)
    exact_categories = {
        "log": "logs",
        "logs": "logs",
        "runtimelog": "logs",
        "runtimelogs": "logs",
        "pluginruntimelog": "logs",
        "pluginruntimelogs": "logs",
        "applicationlog": "logs",
        "applicationlogs": "logs",
        "auditlog": "logs",
        "auditlogs": "logs",
        "providerlog": "logs",
        "providerlogs": "logs",
        "trace": "full_traces",
        "traces": "full_traces",
        "fulltrace": "full_traces",
        "fulltraces": "full_traces",
        "rawtrace": "full_traces",
        "rawtraces": "full_traces",
        "replytrace": "full_traces",
        "replytraces": "full_traces",
        "turntrace": "full_traces",
        "turntraces": "full_traces",
        "cache": "temporary_caches",
        "caches": "temporary_caches",
        "temporarycache": "temporary_caches",
        "temporarycaches": "temporary_caches",
        "cachedata": "temporary_caches",
        "cachefiles": "temporary_caches",
        "pid": "pid_files",
        "pidfile": "pid_files",
        "pidfiles": "pid_files",
        "build": "build_artifacts",
        "buildartifact": "build_artifacts",
        "buildartifacts": "build_artifacts",
        "buildoutput": "build_artifacts",
        "buildoutputs": "build_artifacts",
        "dist": "build_artifacts",
        "webuisession": "webui_sessions",
        "webuisessions": "webui_sessions",
        "browserprofile": "browser_profiles",
        "browserprofiles": "browser_profiles",
        "oskeyring": "os_keyring",
        "keyring": "os_keyring",
    }
    if normalized in exact_categories:
        return exact_categories[normalized]
    return ""


def _safe_machine_id(value: Any, code: str) -> str:
    text = str(value or "").strip()
    if not _SAFE_ID_RE.fullmatch(text):
        raise _error(code, "备份协议包含非法标识符")
    return text


def _safe_code_list(values: Sequence[str], code: str) -> tuple[str, ...]:
    if isinstance(values, (str, bytes, bytearray)):
        raise _error(code, "恢复预检返回了非法诊断列表")
    result = tuple(_safe_machine_id(value, code) for value in values)
    if len(result) != len(set(result)):
        raise _error(code, "恢复预检返回了重复诊断码")
    return result


def _normalize_dependency(value: PackageDependency | Mapping[str, Any] | str) -> PackageDependency:
    if isinstance(value, PackageDependency):
        dependency = value
    elif isinstance(value, str):
        dependency = PackageDependency(name=value)
    elif isinstance(value, Mapping) and set(value) == {"name", "version", "required"}:
        if not isinstance(value["required"], bool):
            raise _error("backup_dependency_invalid", "备份依赖声明无效")
        dependency = PackageDependency(
            name=str(value["name"]),
            version=str(value["version"]),
            required=value["required"],
        )
    else:
        raise _error("backup_dependency_invalid", "备份依赖声明无效")
    name = _safe_machine_id(dependency.name, "backup_dependency_invalid")
    version = str(dependency.version or "").strip()
    if len(version) > 128 or any(character in version for character in "\r\n\x00"):
        raise _error("backup_dependency_invalid", "备份依赖声明无效")
    if not isinstance(dependency.required, bool):
        raise _error("backup_dependency_invalid", "备份依赖声明无效")
    return PackageDependency(name=name, version=version, required=dependency.required)


def _normalize_dependencies(
    values: Sequence[PackageDependency | Mapping[str, Any] | str],
) -> tuple[PackageDependency, ...]:
    normalized = tuple(_normalize_dependency(value) for value in values)
    names = [item.name for item in normalized]
    if len(names) != len(set(names)):
        raise _error("backup_dependency_invalid", "备份依赖声明重复")
    return tuple(sorted(normalized, key=lambda item: item.name))


def _safe_archive_name(name: str, limits: ArchiveLimits) -> str:
    if not isinstance(name, str) or not name or len(name) > limits.max_path_length:
        raise _error("backup_archive_path_invalid", "备份包包含不安全的文件路径")
    if (
        "\\" in name
        or any(ord(character) < 32 or ord(character) == 127 for character in name)
        or unicodedata.normalize("NFC", name) != name
    ):
        raise _error("backup_archive_path_invalid", "备份包包含不安全的文件路径")
    posix = PurePosixPath(name)
    windows = PureWindowsPath(name)
    if (
        posix.is_absolute()
        or windows.is_absolute()
        or windows.drive
        or name.endswith("/")
        or any(part in {"", ".", ".."} for part in posix.parts)
        or posix.as_posix() != name
    ):
        raise _error("backup_archive_path_invalid", "备份包包含不安全的文件路径")
    return name


def _tree_payload_metadata(entries: Mapping[str, bytes]) -> dict[str, Any]:
    files = [
        {"path": name, "size": len(payload), "sha256": _sha256(payload)}
        for name, payload in sorted(entries.items())
    ]
    return {
        "size": sum(item["size"] for item in files),
        "sha256": _sha256(_canonical_json(files)),
    }


def _zip_bytes(entries: Mapping[str, bytes], manifest: Mapping[str, Any]) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(
        stream,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=6,
        allowZip64=True,
    ) as archive:
        for name, payload in sorted(entries.items()):
            info = zipfile.ZipInfo(name)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (stat.S_IFREG | 0o600) << 16
            archive.writestr(info, payload)
        info = zipfile.ZipInfo(MANIFEST_PATH)
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = (stat.S_IFREG | 0o600) << 16
        archive.writestr(info, _canonical_json(manifest))
    return stream.getvalue()


def _base_manifest(
    *,
    package_type: str,
    package_id: str,
    created_at: float,
    source_bot_id: str,
    schema_version: str,
    datasets: list[dict[str, Any]],
    dependencies: tuple[PackageDependency, ...],
    exclusions: Sequence[str],
    reauth_required: Sequence[str],
    payload: Mapping[str, Any],
    encryption: Mapping[str, Any] | None,
) -> dict[str, Any]:
    return {
        "format": PACKAGE_FORMAT,
        "version": PACKAGE_VERSION,
        "package_type": package_type,
        "package_id": package_id,
        "created_at": created_at,
        "source": {"bot_id": source_bot_id},
        "schema_version": schema_version,
        "datasets": datasets,
        "dependencies": [dependency.to_dict() for dependency in dependencies],
        "exclusions": sorted(set(exclusions)),
        "reauth_required": sorted(set(reauth_required)),
        "payload": dict(payload),
        "encryption": copy.deepcopy(encryption),
    }


def _secret_aad(manifest: Mapping[str, Any]) -> bytes:
    projection = copy.deepcopy(dict(manifest))
    datasets = projection.get("datasets")
    if isinstance(datasets, list) and datasets and isinstance(datasets[0], dict):
        datasets[0]["size"] = 0
        datasets[0]["sha256"] = "0" * 64
    projection["payload"] = {"size": 0, "sha256": "0" * 64}
    encryption = projection.get("encryption")
    if isinstance(encryption, dict):
        encryption["aad_sha256"] = "0" * 64
    return _canonical_json(projection)


def _derive_secret_key(passphrase: str, salt: bytes, parameters: ScryptParameters) -> bytes:
    if not isinstance(passphrase, str) or not passphrase:
        raise _error("secret_passphrase_required", "秘密包需要非空口令")
    if len(passphrase.encode("utf-8")) > 4096:
        raise _error("secret_passphrase_invalid", "秘密包口令过长")
    parameters.validate()
    try:
        from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
    except ImportError as exc:
        raise _error("secret_crypto_dependency_missing", "当前环境缺少秘密包加密组件") from exc
    password_bytes = bytearray(passphrase.encode("utf-8"))
    try:
        kdf = Scrypt(
            salt=salt,
            length=parameters.length,
            n=parameters.n,
            r=parameters.r,
            p=parameters.p,
        )
        return kdf.derive(bytes(password_bytes))
    finally:
        for index in range(len(password_bytes)):
            password_bytes[index] = 0


def _encrypt_secret_payload(
    payload: bytes,
    *,
    passphrase: str,
    salt: bytes,
    nonce: bytes,
    parameters: ScryptParameters,
    aad: bytes,
) -> bytes:
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as exc:
        raise _error("secret_crypto_dependency_missing", "当前环境缺少秘密包加密组件") from exc
    key = bytearray(_derive_secret_key(passphrase, salt, parameters))
    try:
        return AESGCM(bytes(key)).encrypt(nonce, payload, aad)
    finally:
        for index in range(len(key)):
            key[index] = 0


def _decode_b64(value: Any, *, code: str, expected_length: int | None = None) -> bytes:
    if not isinstance(value, str) or len(value) > 256:
        raise _error(code, "秘密包加密元数据无效")
    try:
        decoded = base64.b64decode(value.encode("ascii"), validate=True)
    except (ValueError, UnicodeEncodeError) as exc:
        raise _error(code, "秘密包加密元数据无效") from exc
    if expected_length is not None and len(decoded) != expected_length:
        raise _error(code, "秘密包加密元数据无效")
    return decoded


def _decrypt_secret_payload(
    ciphertext: bytes,
    *,
    passphrase: str,
    manifest: Mapping[str, Any],
) -> bytes:
    encryption = manifest.get("encryption")
    if not isinstance(encryption, Mapping):
        raise _error("secret_encryption_metadata_invalid", "秘密包加密元数据无效")
    parameters = ScryptParameters(
        n=encryption.get("n"),
        r=encryption.get("r"),
        p=encryption.get("p"),
        length=encryption.get("key_length"),
    )
    parameters.validate()
    salt = _decode_b64(encryption.get("salt"), code="secret_encryption_metadata_invalid")
    if len(salt) < 16 or len(salt) > 64:
        raise _error("secret_encryption_metadata_invalid", "秘密包加密元数据无效")
    nonce = _decode_b64(
        encryption.get("nonce"),
        code="secret_encryption_metadata_invalid",
        expected_length=12,
    )
    aad = _secret_aad(manifest)
    if encryption.get("aad_sha256") != _sha256(aad):
        raise _error("secret_manifest_authentication_invalid", "秘密包清单认证信息无效")
    try:
        from cryptography.exceptions import InvalidTag
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as exc:
        raise _error("secret_crypto_dependency_missing", "当前环境缺少秘密包加密组件") from exc
    key = bytearray(_derive_secret_key(passphrase, salt, parameters))
    try:
        try:
            return AESGCM(bytes(key)).decrypt(nonce, ciphertext, aad)
        except InvalidTag as exc:
            raise _error(
                "secret_password_or_integrity_invalid",
                "秘密包口令错误或归档完整性校验失败",
            ) from exc
    finally:
        for index in range(len(key)):
            key[index] = 0


class WholePluginBackupService:
    """不绑定真实数据目录的 whole-plugin 备份协议协调器。"""

    def __init__(
        self,
        *,
        file_system: BackupFileSystem | None = None,
        journal_store: RestoreJournalStore | None = None,
        limits: ArchiveLimits | None = None,
        clock: Callable[[], float] = time.time,
        id_factory: Callable[[], str] = lambda: uuid.uuid4().hex,
        random_bytes: Callable[[int], bytes] = os.urandom,
        plan_signing_key: bytes | None = None,
        plan_ttl_seconds: float = 600.0,
    ) -> None:
        self.file_system = file_system or LocalBackupFileSystem()
        self.journal_store = journal_store or InMemoryRestoreJournalStore()
        self.limits = limits or ArchiveLimits()
        self.clock = clock
        self.id_factory = id_factory
        self.random_bytes = random_bytes
        if plan_signing_key is None:
            self._plan_signing_key = self._random_exact(32)
        elif isinstance(plan_signing_key, bytes) and len(plan_signing_key) >= 32:
            self._plan_signing_key = bytes(plan_signing_key)
        else:
            raise ValueError("plan_signing_key must contain at least 32 bytes")
        self.plan_ttl_seconds = float(plan_ttl_seconds)
        if not math.isfinite(self.plan_ttl_seconds) or self.plan_ttl_seconds <= 0:
            raise ValueError("plan_ttl_seconds must be positive and finite")
        self._apply_lock = threading.RLock()

    def write_package(self, path: str | Path, payload: bytes) -> None:
        if not isinstance(payload, bytes):
            raise _error("backup_package_bytes_invalid", "备份包必须是字节数据")
        try:
            self.file_system.write_bytes_atomic(path, payload)
        except WholePluginBackupError:
            raise
        except Exception as exc:
            raise _error("backup_package_write_failed", "无法写入备份包") from exc

    def create_state_package(
        self,
        *,
        source_bot_id: str,
        datasets: Mapping[str, Any],
        schema_version: str = "1",
        dataset_schemas: Mapping[str, str] | None = None,
        dataset_dependencies: Mapping[str, Sequence[str]] | None = None,
        dependencies: Sequence[PackageDependency | Mapping[str, Any] | str] = (),
        exclusions: Sequence[str] = (),
        reauth_required: Sequence[str] = (),
    ) -> bytes:
        source_bot = _safe_machine_id(source_bot_id, "backup_source_bot_invalid")
        schema = _safe_machine_id(schema_version, "backup_schema_version_invalid")
        if not isinstance(datasets, Mapping):
            raise _error("backup_datasets_invalid", "状态包数据集声明无效")
        schemas = dict(dataset_schemas or {})
        dependencies_by_dataset = dict(dataset_dependencies or {})
        entries: dict[str, bytes] = {}
        declarations: list[dict[str, Any]] = []
        discovered_reauth = set(DEFAULT_REAUTH_REQUIRED) | set(reauth_required)
        seen_names: set[str] = set()
        if any(not isinstance(raw_name, str) for raw_name in datasets):
            raise _error("backup_dataset_name_invalid", "状态包数据集名称无效")
        for raw_name in sorted(datasets):
            name = _safe_machine_id(raw_name, "backup_dataset_name_invalid")
            if name in seen_names:
                raise _error("backup_dataset_duplicate", "状态包包含重复数据集")
            seen_names.add(name)
            if _dataset_exclusion_category(name):
                continue
            split = split_state_and_secrets(datasets[raw_name])
            discovered_reauth.update(split.reauth_required)
            payload = _canonical_json(split.state)
            path = f"datasets/{name}.json"
            dataset_schema = _safe_machine_id(
                schemas.get(name, schema),
                "backup_schema_version_invalid",
            )
            dataset_deps = _safe_code_list(
                tuple(dependencies_by_dataset.get(name, ())),
                "backup_dataset_dependency_invalid",
            )
            entries[path] = payload
            declarations.append(
                {
                    "name": name,
                    "path": path,
                    "schema_version": dataset_schema,
                    "size": len(payload),
                    "sha256": _sha256(payload),
                    "dependencies": list(dataset_deps),
                }
            )
        if not declarations:
            raise _error("backup_state_empty", "状态包没有可迁移的数据集")
        package_id = self._new_package_id()
        manifest = _base_manifest(
            package_type=STATE_PACKAGE,
            package_id=package_id,
            created_at=self._now(),
            source_bot_id=source_bot,
            schema_version=schema,
            datasets=declarations,
            dependencies=_normalize_dependencies(dependencies),
            exclusions=tuple(DEFAULT_STATE_EXCLUSIONS) + tuple(exclusions),
            reauth_required=tuple(discovered_reauth),
            payload=_tree_payload_metadata(entries),
            encryption=None,
        )
        package = _zip_bytes(entries, manifest)
        if len(package) > self.limits.max_archive_bytes:
            raise _error("backup_archive_too_large", "生成的状态包超过大小限制")
        self._validate_package_bytes(package, expected_type=STATE_PACKAGE, decrypt=False)
        return package

    def create_secret_package(
        self,
        *,
        source_bot_id: str,
        secrets: Mapping[str, Any],
        passphrase: str,
        schema_version: str = "1",
        dependencies: Sequence[PackageDependency | Mapping[str, Any] | str] = (),
        exclusions: Sequence[str] = (),
        reauth_required: Sequence[str] = (),
        scrypt_parameters: ScryptParameters | None = None,
    ) -> bytes:
        source_bot = _safe_machine_id(source_bot_id, "backup_source_bot_invalid")
        schema = _safe_machine_id(schema_version, "backup_schema_version_invalid")
        if not isinstance(secrets, Mapping):
            raise _error("secret_payload_invalid", "秘密包内容必须是结构化对象")
        portable, discovered_reauth = _strip_nonportable_secrets(secrets)
        if not portable:
            raise _error("secret_payload_empty", "秘密包没有可迁移的秘密数据")
        plaintext = _canonical_json({"schema_version": schema, "secrets": portable})
        parameters = scrypt_parameters or ScryptParameters()
        parameters.validate()
        salt = self._random_exact(32)
        nonce = self._random_exact(12)
        package_id = self._new_package_id()
        declaration = {
            "name": SECRET_DATASET_NAME,
            "path": SECRET_PAYLOAD_PATH,
            "schema_version": schema,
            "size": 0,
            "sha256": "0" * 64,
            "dependencies": [],
        }
        encryption = {
            "algorithm": "AES-256-GCM",
            "kdf": "scrypt",
            "salt": base64.b64encode(salt).decode("ascii"),
            "nonce": base64.b64encode(nonce).decode("ascii"),
            "n": parameters.n,
            "r": parameters.r,
            "p": parameters.p,
            "key_length": parameters.length,
            "aad_sha256": "0" * 64,
        }
        manifest = _base_manifest(
            package_type=SECRET_PACKAGE,
            package_id=package_id,
            created_at=self._now(),
            source_bot_id=source_bot,
            schema_version=schema,
            datasets=[declaration],
            dependencies=_normalize_dependencies(dependencies),
            exclusions=tuple(DEFAULT_SECRET_EXCLUSIONS) + tuple(exclusions),
            reauth_required=tuple(set(DEFAULT_REAUTH_REQUIRED) | set(reauth_required) | discovered_reauth),
            payload={"size": 0, "sha256": "0" * 64},
            encryption=encryption,
        )
        aad = _secret_aad(manifest)
        manifest_encryption = manifest["encryption"]
        if not isinstance(manifest_encryption, dict):
            raise _error("secret_encryption_metadata_invalid", "秘密包加密元数据无效")
        manifest_encryption["aad_sha256"] = _sha256(aad)
        ciphertext = _encrypt_secret_payload(
            plaintext,
            passphrase=passphrase,
            salt=salt,
            nonce=nonce,
            parameters=parameters,
            aad=_secret_aad(manifest),
        )
        declaration["size"] = len(ciphertext)
        declaration["sha256"] = _sha256(ciphertext)
        manifest["payload"] = _tree_payload_metadata({SECRET_PAYLOAD_PATH: ciphertext})
        package = _zip_bytes({SECRET_PAYLOAD_PATH: ciphertext}, manifest)
        if len(package) > self.limits.max_archive_bytes:
            raise _error("backup_archive_too_large", "生成的秘密包超过大小限制")
        self._validate_package_bytes(package, expected_type=SECRET_PACKAGE, decrypt=False)
        return package

    def inspect(
        self,
        source: bytes | bytearray | memoryview | str | Path,
        *,
        expected_type: str | None = None,
    ) -> PackageInspection:
        validated = self._validate_package_bytes(
            self._read_source(source),
            expected_type=expected_type,
            decrypt=False,
        )
        return self._inspection(validated)

    def decrypt_secret_package(
        self,
        source: bytes | bytearray | memoryview | str | Path,
        *,
        passphrase: str,
    ) -> Mapping[str, Any]:
        validated = self._validate_package_bytes(
            self._read_source(source),
            expected_type=SECRET_PACKAGE,
            decrypt=True,
            passphrase=passphrase,
        )
        return copy.deepcopy(dict(validated.datasets or {}))

    def dry_run(
        self,
        source: bytes | bytearray | memoryview | str | Path,
        *,
        backend: AtomicRestoreBackend,
        passphrase: str | None = None,
    ) -> DryRunPlan:
        validated = self._load_for_restore(source, passphrase=passphrase)
        report = self._backend_preflight(backend, validated).normalized()
        now = self._now()
        inspection = self._inspection(validated)
        target = self._target_fingerprint(backend)
        unsigned = {
            "package_id": inspection.package_id,
            "package_type": inspection.package_type,
            "package_sha256": inspection.package_sha256,
            "payload_sha256": inspection.payload_sha256,
            "target_fingerprint": target,
            "dataset_names": list(inspection.dataset_names),
            "preflight": report.to_dict(),
            "created_at": now,
            "expires_at": now + self.plan_ttl_seconds,
        }
        return DryRunPlan(
            plan_id=self._sign_plan(unsigned),
            package_id=inspection.package_id,
            package_type=inspection.package_type,
            package_sha256=inspection.package_sha256,
            payload_sha256=inspection.payload_sha256,
            target_fingerprint=target,
            dataset_names=inspection.dataset_names,
            preflight=report,
            created_at=now,
            expires_at=now + self.plan_ttl_seconds,
        )

    def apply(
        self,
        source: bytes | bytearray | memoryview | str | Path,
        *,
        backend: AtomicRestoreBackend,
        plan: DryRunPlan,
        passphrase: str | None = None,
    ) -> ApplyResult:
        with self._apply_lock:
            validated = self._load_for_restore(source, passphrase=passphrase)
            self._verify_plan(plan, backend=backend, validated=validated)
            if not plan.preflight.can_apply:
                raise _error("restore_preflight_blocked", "恢复预检存在未解决冲突，不能应用")
            prior = self._journal_find_by_plan(plan.plan_id)
            for record in prior:
                if record.status == "applied":
                    return ApplyResult(record.journal_id, "applied", idempotent=True)
                if record.status in _INCOMPLETE_JOURNAL_STATUSES or record.status == "outcome_unknown":
                    raise _error(
                        "restore_previous_outcome_unresolved",
                        "同一恢复计划存在未决 journal，请先核对或回滚",
                    )
            journal_id = self._new_journal_id()
            now = self._now()
            record = RestoreJournalRecord(
                journal_id=journal_id,
                plan_id=plan.plan_id,
                package_id=plan.package_id,
                package_type=plan.package_type,
                package_sha256=plan.package_sha256,
                target_fingerprint=plan.target_fingerprint,
                status="preparing",
                created_at=now,
                updated_at=now,
            )
            self._journal_upsert(record)
            snapshot: SnapshotReference | None = None
            try:
                raw_snapshot = backend.create_snapshot(
                    copy.deepcopy(dict(validated.manifest)),
                    copy.deepcopy(dict(validated.datasets or {})),
                )
                if not isinstance(raw_snapshot, SnapshotReference):
                    raise _error("restore_snapshot_reference_invalid", "恢复后端没有返回有效快照引用")
                snapshot = raw_snapshot.normalized()
                record = self._update_journal(
                    record,
                    status="snapshot_ready",
                    snapshot_reference=snapshot.reference,
                    snapshot_checksum=snapshot.checksum,
                )
                record = self._update_journal(record, status="applying")
                backend.apply_datasets(
                    copy.deepcopy(dict(validated.manifest)),
                    copy.deepcopy(dict(validated.datasets or {})),
                    snapshot,
                )
                record = self._update_journal(record, status="health_check")
                health = backend.health_check(copy.deepcopy(dict(validated.manifest)))
                if not isinstance(health, HealthCheckReport):
                    raise _error("restore_health_result_invalid", "恢复后端没有返回有效健康检查结果")
                health = health.normalized()
                if not health.ok:
                    raise _error("restore_health_check_failed", "恢复后的运行时健康检查未通过")
                record = self._update_journal(record, status="applied", diagnostic_code=health.code)
                return ApplyResult(record.journal_id, "applied", idempotent=False)
            except Exception as exc:
                if snapshot is None:
                    self._update_journal(record, status="failed", diagnostic_code="restore_snapshot_failed")
                    if isinstance(exc, WholePluginBackupError):
                        raise
                    raise _error("restore_snapshot_failed", "创建恢复前快照失败，尚未修改目标数据") from exc
                return self._rollback_after_apply_failure(record, snapshot, backend, exc)

    def rollback(self, journal_id: str, *, backend: AtomicRestoreBackend) -> ApplyResult:
        with self._apply_lock:
            record = self._journal_get(journal_id)
            if record is None:
                raise _error("restore_journal_not_found", "找不到指定的恢复 journal")
            if record.status == "rolled_back":
                return ApplyResult(record.journal_id, "rolled_back", idempotent=True)
            if not record.snapshot_reference:
                raise _error("restore_snapshot_unavailable", "该恢复 journal 没有可用快照")
            if record.target_fingerprint != self._target_fingerprint(backend):
                raise _error("restore_target_changed", "恢复目标与 journal 记录不一致")
            snapshot = SnapshotReference(record.snapshot_reference, record.snapshot_checksum).normalized()
            record = self._update_journal(record, status="rollback_started")
            try:
                backend.rollback_snapshot(snapshot)
            except Exception as exc:
                try:
                    self._update_journal(
                        record,
                        status="outcome_unknown",
                        diagnostic_code="restore_rollback_outcome_unknown",
                    )
                except WholePluginBackupError:
                    pass
                raise _error(
                    "restore_rollback_outcome_unknown",
                    "回滚结果未知，禁止继续自动应用，请人工核对 journal",
                ) from exc
            try:
                record = self._update_journal(
                    record,
                    status="rolled_back",
                    diagnostic_code="restore_rollback_completed",
                )
            except WholePluginBackupError as exc:
                raise _error(
                    "restore_journal_unavailable_after_rollback",
                    "数据已回滚，但无法确认 journal 已持久化，请人工核对后再继续",
                ) from exc
            return ApplyResult(record.journal_id, "rolled_back", idempotent=False)

    def recover_incomplete(self, *, backend: AtomicRestoreBackend) -> tuple[ApplyResult, ...]:
        """只自动回滚结果确定且已有快照的中断项；outcome_unknown 保持隔离。"""

        results: list[ApplyResult] = []
        target = self._target_fingerprint(backend)
        for record in self._journal_list_records():
            if record.target_fingerprint != target or record.status not in _INCOMPLETE_JOURNAL_STATUSES:
                continue
            if not record.snapshot_reference:
                self._update_journal(
                    record,
                    status="failed",
                    diagnostic_code="restore_interrupted_before_snapshot",
                )
                continue
            results.append(self.rollback(record.journal_id, backend=backend))
        return tuple(results)

    def _rollback_after_apply_failure(
        self,
        record: RestoreJournalRecord,
        snapshot: SnapshotReference,
        backend: AtomicRestoreBackend,
        apply_error: Exception,
    ) -> ApplyResult:
        diagnostic = apply_error.code if isinstance(apply_error, WholePluginBackupError) else "restore_apply_failed"
        rollback_record = replace(
            record,
            snapshot_reference=snapshot.reference,
            snapshot_checksum=snapshot.checksum,
        )
        try:
            rollback_record = self._update_journal(
                rollback_record,
                status="rollback_started",
                diagnostic_code=diagnostic,
            )
        except WholePluginBackupError:
            pass
        try:
            backend.rollback_snapshot(snapshot)
        except Exception as rollback_error:
            try:
                self._update_journal(
                    rollback_record,
                    status="outcome_unknown",
                    diagnostic_code="restore_rollback_outcome_unknown",
                )
            except WholePluginBackupError:
                pass
            raise _error(
                "restore_rollback_outcome_unknown",
                "恢复失败且回滚结果未知，禁止自动重试，请人工核对 journal",
            ) from rollback_error
        try:
            self._update_journal(
                rollback_record,
                status="rolled_back",
                diagnostic_code=diagnostic,
            )
        except WholePluginBackupError as exc:
            raise _error(
                "restore_journal_unavailable_after_rollback",
                "数据已自动回滚，但无法确认 journal 已持久化，请人工核对后再继续",
            ) from exc
        raise _error(
            "restore_apply_failed_rolled_back",
            "恢复应用失败，已自动回滚到操作前快照",
        ) from apply_error

    def _verify_plan(
        self,
        plan: DryRunPlan,
        *,
        backend: AtomicRestoreBackend,
        validated: _ValidatedPackage,
    ) -> None:
        if not isinstance(plan, DryRunPlan):
            raise _error("restore_plan_invalid", "恢复计划无效")
        now = self._now()
        if plan.expires_at < now:
            raise _error("restore_plan_expired", "恢复计划已过期，请重新执行 dry-run")
        inspection = self._inspection(validated)
        report = self._backend_preflight(backend, validated).normalized()
        expected = {
            "package_id": inspection.package_id,
            "package_type": inspection.package_type,
            "package_sha256": inspection.package_sha256,
            "payload_sha256": inspection.payload_sha256,
            "target_fingerprint": self._target_fingerprint(backend),
            "dataset_names": list(inspection.dataset_names),
            "preflight": report.to_dict(),
            "created_at": plan.created_at,
            "expires_at": plan.expires_at,
        }
        expected_plan_id = self._sign_plan(expected)
        if (
            not hmac.compare_digest(plan.plan_id, expected_plan_id)
            or plan.package_id != expected["package_id"]
            or plan.package_type != expected["package_type"]
            or plan.package_sha256 != expected["package_sha256"]
            or plan.payload_sha256 != expected["payload_sha256"]
            or plan.target_fingerprint != expected["target_fingerprint"]
            or tuple(plan.dataset_names) != tuple(expected["dataset_names"])
            or plan.preflight.normalized() != report
        ):
            raise _error("restore_plan_mismatch", "备份包、目标或预检结果已变化，请重新执行 dry-run")

    def _backend_preflight(
        self,
        backend: AtomicRestoreBackend,
        validated: _ValidatedPackage,
    ) -> PreflightReport:
        try:
            report = backend.preflight(
                copy.deepcopy(dict(validated.manifest)),
                copy.deepcopy(dict(validated.datasets or {})),
            )
        except WholePluginBackupError:
            raise
        except Exception as exc:
            raise _error("restore_preflight_failed", "恢复预检失败，目标数据未修改") from exc
        if not isinstance(report, PreflightReport):
            raise _error("restore_preflight_result_invalid", "恢复后端没有返回有效预检结果")
        return report

    def _target_fingerprint(self, backend: AtomicRestoreBackend) -> str:
        try:
            raw = backend.target_fingerprint()
        except Exception as exc:
            raise _error("restore_target_unavailable", "无法确认恢复目标身份") from exc
        if not isinstance(raw, str) or not raw or len(raw) > 4096:
            raise _error("restore_target_unavailable", "无法确认恢复目标身份")
        return _sha256(raw.encode("utf-8"))

    def _update_journal(
        self,
        record: RestoreJournalRecord,
        *,
        status: str,
        snapshot_reference: str | None = None,
        snapshot_checksum: str | None = None,
        diagnostic_code: str | None = None,
    ) -> RestoreJournalRecord:
        updated = replace(
            record,
            status=status,
            snapshot_reference=record.snapshot_reference if snapshot_reference is None else snapshot_reference,
            snapshot_checksum=record.snapshot_checksum if snapshot_checksum is None else snapshot_checksum,
            diagnostic_code=record.diagnostic_code if diagnostic_code is None else diagnostic_code,
            updated_at=self._now(),
        )
        self._journal_upsert(updated)
        return updated

    def _journal_upsert(self, record: RestoreJournalRecord) -> None:
        try:
            self.journal_store.upsert(record)
        except WholePluginBackupError:
            raise
        except Exception as exc:
            raise _error("restore_journal_unavailable", "无法写入恢复 journal") from exc

    def _journal_get(self, journal_id: str) -> RestoreJournalRecord | None:
        try:
            return self.journal_store.get(journal_id)
        except WholePluginBackupError:
            raise
        except Exception as exc:
            raise _error("restore_journal_unavailable", "无法读取恢复 journal") from exc

    def _journal_find_by_plan(self, plan_id: str) -> tuple[RestoreJournalRecord, ...]:
        try:
            return self.journal_store.find_by_plan(plan_id)
        except WholePluginBackupError:
            raise
        except Exception as exc:
            raise _error("restore_journal_unavailable", "无法读取恢复 journal") from exc

    def _journal_list_records(self) -> tuple[RestoreJournalRecord, ...]:
        try:
            return self.journal_store.list_records()
        except WholePluginBackupError:
            raise
        except Exception as exc:
            raise _error("restore_journal_unavailable", "无法读取恢复 journal") from exc

    def _load_for_restore(
        self,
        source: bytes | bytearray | memoryview | str | Path,
        *,
        passphrase: str | None,
    ) -> _ValidatedPackage:
        raw = self._read_source(source)
        initial = self._validate_package_bytes(raw, decrypt=False)
        package_type = initial.manifest["package_type"]
        if package_type == SECRET_PACKAGE:
            if passphrase is None:
                raise _error("secret_passphrase_required", "恢复秘密包前必须提供口令")
            return self._validate_package_bytes(
                raw,
                expected_type=SECRET_PACKAGE,
                decrypt=True,
                passphrase=passphrase,
            )
        return initial

    def _read_source(self, source: bytes | bytearray | memoryview | str | Path) -> bytes:
        if isinstance(source, bytes):
            raw = source
        elif isinstance(source, (bytearray, memoryview)):
            raw = bytes(source)
        elif isinstance(source, (str, Path)):
            try:
                raw = self.file_system.read_bytes(source)
            except Exception as exc:
                raise _error("backup_package_read_failed", "无法读取备份包") from exc
        else:
            raise _error("backup_package_source_invalid", "备份包来源无效")
        if not raw:
            raise _error("backup_package_empty", "备份包为空")
        if len(raw) > self.limits.max_archive_bytes:
            raise _error("backup_archive_too_large", "备份包超过大小限制")
        return raw

    def _validate_package_bytes(
        self,
        raw: bytes,
        *,
        expected_type: str | None = None,
        decrypt: bool,
        passphrase: str | None = None,
    ) -> _ValidatedPackage:
        if expected_type is not None and expected_type not in _PACKAGE_TYPES:
            raise _error("backup_package_type_invalid", "备份包类型无效")
        try:
            archive = zipfile.ZipFile(io.BytesIO(raw), mode="r")
        except (zipfile.BadZipFile, zipfile.LargeZipFile, OSError) as exc:
            raise _error("backup_archive_invalid", "备份包不是有效的 ZIP 归档") from exc
        with archive:
            infos = archive.infolist()
            if len(infos) > self.limits.max_entries:
                raise _error("backup_archive_too_many_entries", "备份包文件数量超过限制")
            names: set[str] = set()
            normalized_names: set[str] = set()
            expanded_total = 0
            compressed_total = 0
            info_by_name: dict[str, zipfile.ZipInfo] = {}
            for info in infos:
                name = _safe_archive_name(info.filename, self.limits)
                normalized = unicodedata.normalize("NFC", name).casefold()
                if name in names or normalized in normalized_names:
                    raise _error("backup_archive_duplicate_entry", "备份包包含重复或冲突文件")
                names.add(name)
                normalized_names.add(normalized)
                info_by_name[name] = info
                file_mode = (info.external_attr >> 16) & 0o170000
                if stat.S_ISLNK(file_mode):
                    raise _error("backup_archive_symlink_forbidden", "备份包不允许符号链接")
                if file_mode and not stat.S_ISREG(file_mode):
                    raise _error("backup_archive_special_file_forbidden", "备份包不允许特殊文件")
                if info.flag_bits & 0x1:
                    raise _error("backup_archive_encrypted_entry_forbidden", "备份包不允许 ZIP 层加密文件")
                if info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
                    raise _error("backup_archive_compression_unsupported", "备份包使用了不受支持的压缩方式")
                if info.file_size < 0 or info.compress_size < 0:
                    raise _error("backup_archive_size_invalid", "备份包文件大小声明无效")
                expanded_total += info.file_size
                compressed_total += info.compress_size
                if info.file_size > self.limits.max_entry_bytes:
                    raise _error("backup_archive_entry_too_large", "备份包中的单个文件超过限制")
                if info.file_size and info.file_size / max(info.compress_size, 1) > self.limits.max_compression_ratio:
                    raise _error("backup_archive_compression_bomb", "备份包压缩比异常，已拒绝解压")
                if expanded_total > self.limits.max_expanded_bytes or compressed_total > self.limits.max_archive_bytes:
                    raise _error("backup_archive_expanded_too_large", "备份包解压后大小超过限制")
            if MANIFEST_PATH not in names:
                raise _error("backup_manifest_missing", "备份包缺少 manifest.json")
            if info_by_name[MANIFEST_PATH].file_size > self.limits.max_manifest_bytes:
                raise _error("backup_manifest_too_large", "备份包清单超过大小限制")
            manifest_raw = self._read_entry(archive, info_by_name[MANIFEST_PATH])
            manifest = _strict_json_loads(
                manifest_raw,
                code="backup_manifest_invalid",
                message="备份包清单格式无效",
            )
            declarations = self._validate_manifest(manifest, expected_type=expected_type)
            declared_names = {declaration["path"] for declaration in declarations}
            if names != declared_names | {MANIFEST_PATH}:
                raise _error("backup_archive_undeclared_entry", "备份包包含未声明、缺失或多余文件")
            entries: dict[str, bytes] = {}
            for declaration in declarations:
                name = declaration["path"]
                info = info_by_name[name]
                payload = self._read_entry(archive, info)
                if len(payload) != declaration["size"] or _sha256(payload) != declaration["sha256"]:
                    raise _error("backup_dataset_checksum_mismatch", "备份数据集大小或校验和不匹配")
                entries[name] = payload
            if manifest["payload"] != _tree_payload_metadata(entries):
                raise _error("backup_payload_checksum_mismatch", "备份包总体校验和不匹配")
            datasets: dict[str, Any] | None = None
            if manifest["package_type"] == STATE_PACKAGE:
                datasets = {}
                for declaration in declarations:
                    datasets[declaration["name"]] = _strict_json_loads(
                        entries[declaration["path"]],
                        code="backup_dataset_json_invalid",
                        message="状态包数据集不是有效 JSON",
                    )
            elif decrypt:
                if passphrase is None:
                    raise _error("secret_passphrase_required", "解密秘密包前必须提供口令")
                plaintext = _decrypt_secret_payload(
                    entries[SECRET_PAYLOAD_PATH],
                    passphrase=passphrase,
                    manifest=manifest,
                )
                if len(plaintext) > self.limits.max_entry_bytes:
                    raise _error("secret_payload_too_large", "秘密包解密内容超过限制")
                secret_payload = _strict_json_loads(
                    plaintext,
                    code="secret_payload_invalid",
                    message="秘密包解密内容无效",
                )
                if (
                    not isinstance(secret_payload, Mapping)
                    or set(secret_payload) != {"schema_version", "secrets"}
                    or secret_payload["schema_version"] != manifest["schema_version"]
                    or not isinstance(secret_payload["secrets"], Mapping)
                ):
                    raise _error("secret_payload_invalid", "秘密包解密内容无效")
                portable, nonportable = _strip_nonportable_secrets(secret_payload["secrets"])
                if nonportable or portable != secret_payload["secrets"]:
                    raise _error("secret_nonportable_content_forbidden", "秘密包包含不可迁移的设备绑定凭据")
                datasets = {SECRET_DATASET_NAME: portable}
            return _ValidatedPackage(
                raw=raw,
                package_sha256=_sha256(raw),
                manifest=manifest,
                datasets=datasets,
            )

    def _read_entry(self, archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> bytes:
        total = 0
        output = io.BytesIO()
        try:
            with archive.open(info, mode="r") as stream:
                while True:
                    chunk = stream.read(self.limits.read_chunk_bytes)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > info.file_size or total > self.limits.max_entry_bytes:
                        raise _error("backup_archive_entry_too_large", "备份包文件实际大小超过限制")
                    output.write(chunk)
        except WholePluginBackupError:
            raise
        except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
            raise _error("backup_archive_read_failed", "读取备份包内容失败") from exc
        if total != info.file_size:
            raise _error("backup_archive_size_mismatch", "备份包文件实际大小与声明不一致")
        return output.getvalue()

    def _validate_manifest(
        self,
        manifest: Any,
        *,
        expected_type: str | None,
    ) -> list[dict[str, Any]]:
        required = {
            "format",
            "version",
            "package_type",
            "package_id",
            "created_at",
            "source",
            "schema_version",
            "datasets",
            "dependencies",
            "exclusions",
            "reauth_required",
            "payload",
            "encryption",
        }
        if not isinstance(manifest, Mapping) or set(manifest) != required:
            raise _error("backup_manifest_invalid", "备份包清单字段不完整或包含未知字段")
        if manifest["format"] != PACKAGE_FORMAT or manifest["version"] != PACKAGE_VERSION:
            raise _error("backup_manifest_version_unsupported", "备份包格式或版本不受支持")
        package_type = manifest["package_type"]
        if package_type not in _PACKAGE_TYPES or (expected_type is not None and package_type != expected_type):
            raise _error("backup_package_type_mismatch", "备份包类型与当前操作不匹配")
        if not isinstance(manifest["package_id"], str) or not _HEX_32_RE.fullmatch(manifest["package_id"]):
            raise _error("backup_manifest_invalid", "备份包 ID 无效")
        created_at = manifest["created_at"]
        if (
            isinstance(created_at, bool)
            or not isinstance(created_at, (int, float))
            or not math.isfinite(float(created_at))
            or created_at < 0
        ):
            raise _error("backup_manifest_invalid", "备份包创建时间无效")
        source = manifest["source"]
        if not isinstance(source, Mapping) or set(source) != {"bot_id"}:
            raise _error("backup_manifest_invalid", "备份包来源身份无效")
        _safe_machine_id(source["bot_id"], "backup_source_bot_invalid")
        _safe_machine_id(manifest["schema_version"], "backup_schema_version_invalid")
        dependencies_raw = manifest["dependencies"]
        if not isinstance(dependencies_raw, list):
            raise _error("backup_dependency_invalid", "备份依赖声明无效")
        normalized_dependencies = _normalize_dependencies(dependencies_raw)
        if [item.to_dict() for item in normalized_dependencies] != dependencies_raw:
            raise _error("backup_dependency_invalid", "备份依赖声明未规范化")
        exclusions = manifest["exclusions"]
        reauth = manifest["reauth_required"]
        if not isinstance(exclusions, list) or not isinstance(reauth, list):
            raise _error("backup_manifest_invalid", "备份包排除项声明无效")
        normalized_exclusions = list(_safe_code_list(tuple(exclusions), "backup_manifest_invalid"))
        normalized_reauth = list(_safe_code_list(tuple(reauth), "backup_manifest_invalid"))
        if normalized_exclusions != sorted(normalized_exclusions) or normalized_reauth != sorted(normalized_reauth):
            raise _error("backup_manifest_invalid", "备份包排除项声明未规范化")
        required_exclusions = (
            DEFAULT_STATE_EXCLUSIONS
            if package_type == STATE_PACKAGE
            else DEFAULT_SECRET_EXCLUSIONS
        )
        if not set(required_exclusions).issubset(exclusions):
            raise _error("backup_manifest_secret_exclusions_missing", "备份包缺少必要的秘密排除声明")
        if not set(DEFAULT_REAUTH_REQUIRED).issubset(reauth):
            raise _error("backup_manifest_reauth_missing", "备份包缺少重新认证声明")
        payload = manifest["payload"]
        if (
            not isinstance(payload, Mapping)
            or set(payload) != {"size", "sha256"}
            or isinstance(payload["size"], bool)
            or not isinstance(payload["size"], int)
            or payload["size"] < 0
            or not isinstance(payload["sha256"], str)
            or not _HEX_64_RE.fullmatch(payload["sha256"])
        ):
            raise _error("backup_manifest_invalid", "备份包总体校验声明无效")
        declarations_raw = manifest["datasets"]
        if not isinstance(declarations_raw, list) or len(declarations_raw) > self.limits.max_entries - 1:
            raise _error("backup_datasets_invalid", "备份包数据集声明无效")
        declarations: list[dict[str, Any]] = []
        seen_names: set[str] = set()
        seen_paths: set[str] = set()
        normalized_paths: set[str] = set()
        declaration_fields = {"name", "path", "schema_version", "size", "sha256", "dependencies"}
        for raw_declaration in declarations_raw:
            if not isinstance(raw_declaration, Mapping) or set(raw_declaration) != declaration_fields:
                raise _error("backup_dataset_declaration_invalid", "备份包数据集声明无效")
            declaration = dict(raw_declaration)
            name = _safe_machine_id(declaration["name"], "backup_dataset_name_invalid")
            path = _safe_archive_name(declaration["path"], self.limits)
            schema_version = _safe_machine_id(
                declaration["schema_version"],
                "backup_schema_version_invalid",
            )
            size = declaration["size"]
            checksum = declaration["sha256"]
            if (
                isinstance(size, bool)
                or not isinstance(size, int)
                or size < 0
                or size > self.limits.max_entry_bytes
                or not isinstance(checksum, str)
                or not _HEX_64_RE.fullmatch(checksum)
            ):
                raise _error("backup_dataset_declaration_invalid", "备份包数据集大小或校验声明无效")
            deps = declaration["dependencies"]
            if not isinstance(deps, list):
                raise _error("backup_dataset_dependency_invalid", "备份数据集依赖声明无效")
            normalized_deps = list(_safe_code_list(tuple(deps), "backup_dataset_dependency_invalid"))
            normalized_path = unicodedata.normalize("NFC", path).casefold()
            if name in seen_names or path in seen_paths or normalized_path in normalized_paths:
                raise _error("backup_dataset_duplicate", "备份包包含重复数据集声明")
            seen_names.add(name)
            seen_paths.add(path)
            normalized_paths.add(normalized_path)
            declarations.append(
                {
                    "name": name,
                    "path": path,
                    "schema_version": schema_version,
                    "size": size,
                    "sha256": checksum,
                    "dependencies": normalized_deps,
                }
            )
        if declarations != declarations_raw:
            raise _error("backup_dataset_declaration_invalid", "备份包数据集声明未规范化")
        if package_type == STATE_PACKAGE:
            if not declarations:
                raise _error("backup_state_empty", "状态包没有可迁移的数据集")
            if manifest["encryption"] is not None:
                raise _error("backup_state_encryption_invalid", "状态包不能声明秘密包加密元数据")
            for declaration in declarations:
                if declaration["path"] != f"datasets/{declaration['name']}.json":
                    raise _error("backup_dataset_path_invalid", "状态包数据集路径无效")
                if _dataset_exclusion_category(declaration["name"]):
                    raise _error("backup_state_dataset_forbidden", "状态包包含默认排除的数据集")
        else:
            if (
                len(declarations) != 1
                or declarations[0]["name"] != SECRET_DATASET_NAME
                or declarations[0]["path"] != SECRET_PAYLOAD_PATH
            ):
                raise _error("secret_dataset_declaration_invalid", "秘密包数据集声明无效")
            self._validate_encryption_manifest(manifest)
        return declarations

    def _validate_encryption_manifest(self, manifest: Mapping[str, Any]) -> None:
        encryption = manifest["encryption"]
        expected = {
            "algorithm",
            "kdf",
            "salt",
            "nonce",
            "n",
            "r",
            "p",
            "key_length",
            "aad_sha256",
        }
        if not isinstance(encryption, Mapping) or set(encryption) != expected:
            raise _error("secret_encryption_metadata_invalid", "秘密包加密元数据无效")
        if encryption["algorithm"] != "AES-256-GCM" or encryption["kdf"] != "scrypt":
            raise _error("secret_encryption_unsupported", "秘密包加密算法不受支持")
        parameters = ScryptParameters(
            n=encryption["n"],
            r=encryption["r"],
            p=encryption["p"],
            length=encryption["key_length"],
        )
        parameters.validate()
        salt = _decode_b64(encryption["salt"], code="secret_encryption_metadata_invalid")
        _decode_b64(
            encryption["nonce"],
            code="secret_encryption_metadata_invalid",
            expected_length=12,
        )
        if len(salt) < 16 or len(salt) > 64 or not _HEX_64_RE.fullmatch(str(encryption["aad_sha256"])):
            raise _error("secret_encryption_metadata_invalid", "秘密包加密元数据无效")
        if encryption["aad_sha256"] != _sha256(_secret_aad(manifest)):
            raise _error("secret_manifest_authentication_invalid", "秘密包清单认证信息无效")

    def _inspection(self, validated: _ValidatedPackage) -> PackageInspection:
        manifest = validated.manifest
        dependencies = tuple(_normalize_dependency(value) for value in manifest["dependencies"])
        return PackageInspection(
            package_type=manifest["package_type"],
            package_id=manifest["package_id"],
            package_sha256=validated.package_sha256,
            source_bot_id=manifest["source"]["bot_id"],
            schema_version=manifest["schema_version"],
            dataset_names=tuple(declaration["name"] for declaration in manifest["datasets"]),
            encrypted=manifest["package_type"] == SECRET_PACKAGE,
            payload_size=manifest["payload"]["size"],
            payload_sha256=manifest["payload"]["sha256"],
            dependencies=dependencies,
            exclusions=tuple(manifest["exclusions"]),
            reauth_required=tuple(manifest["reauth_required"]),
            manifest=copy.deepcopy(dict(manifest)),
        )

    def _new_package_id(self) -> str:
        value = str(self.id_factory() or "").strip().lower()
        if not _HEX_32_RE.fullmatch(value):
            raise _error("backup_id_factory_invalid", "备份包 ID 生成器返回了无效结果")
        return value

    def _sign_plan(self, claims: Mapping[str, Any]) -> str:
        return hmac.new(
            self._plan_signing_key,
            _canonical_json(claims),
            hashlib.sha256,
        ).hexdigest()

    def _new_journal_id(self) -> str:
        value = str(self.id_factory() or "").strip().lower()
        if not _HEX_32_RE.fullmatch(value):
            raise _error("backup_id_factory_invalid", "恢复 journal ID 生成器返回了无效结果")
        return value

    def _now(self) -> float:
        value = float(self.clock())
        if not math.isfinite(value) or value < 0:
            raise _error("backup_clock_invalid", "备份服务时钟无效")
        return value

    def _random_exact(self, length: int) -> bytes:
        try:
            value = self.random_bytes(length)
        except Exception as exc:
            raise _error("secret_random_source_failed", "无法生成秘密包随机参数") from exc
        if not isinstance(value, bytes) or len(value) != length:
            raise _error("secret_random_source_failed", "秘密包随机源返回了无效结果")
        return value


__all__ = [
    "ArchiveLimits",
    "ApplyResult",
    "AtomicRestoreBackend",
    "BackupFileSystem",
    "DEFAULT_REAUTH_REQUIRED",
    "DEFAULT_SECRET_EXCLUSIONS",
    "DEFAULT_STATE_EXCLUSIONS",
    "DryRunPlan",
    "HealthCheckReport",
    "InMemoryRestoreJournalStore",
    "JsonRestoreJournalStore",
    "LocalBackupFileSystem",
    "PACKAGE_FORMAT",
    "PACKAGE_VERSION",
    "PackageDependency",
    "PackageInspection",
    "PreflightReport",
    "RestoreJournalRecord",
    "RestoreJournalStore",
    "SECRET_PACKAGE",
    "STATE_PACKAGE",
    "ScryptParameters",
    "SnapshotReference",
    "SplitBackupPayload",
    "WholePluginBackupError",
    "WholePluginBackupService",
    "split_state_and_secrets",
]
