from __future__ import annotations

import copy
import hashlib
import io
import json
import stat
import warnings
import zipfile
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from ._loader import load_personification_module


@pytest.fixture(scope="module")
def backup_mod():
    return load_personification_module(
        "plugin.personification.core.whole_plugin_backup"
    )


class MemoryFileSystem:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.atomic_writes: list[str] = []

    def read_bytes(self, path: str | Path) -> bytes:
        return self.files[str(path)]

    def write_bytes_atomic(self, path: str | Path, payload: bytes) -> None:
        key = str(path)
        self.files[key] = bytes(payload)
        self.atomic_writes.append(key)

    def exists(self, path: str | Path) -> bool:
        return str(path) in self.files


class FakeRestoreBackend:
    def __init__(self, module, *, target_id: str = "fresh-instance") -> None:
        self.module = module
        self.target_id = target_id
        self.target: dict[str, Any] = {"old": {"value": "before"}}
        self.preflight_report = module.PreflightReport(
            changes={"profiles": 2},
            warnings=("requires_runtime_reload",),
            schema_migrations=("profiles_v1_to_v2",),
        )
        self.snapshots: dict[str, dict[str, Any]] = {}
        self.calls: list[str] = []
        self.fail_snapshot = False
        self.fail_apply = False
        self.fail_rollback = False
        self.health_ok = True

    def target_fingerprint(self) -> str:
        self.calls.append("target_fingerprint")
        return self.target_id

    def preflight(self, manifest, datasets):  # noqa: ANN001
        self.calls.append("preflight")
        assert manifest["format"] == self.module.PACKAGE_FORMAT
        assert isinstance(datasets, dict)
        return self.preflight_report

    def create_snapshot(self, manifest, datasets):  # noqa: ANN001
        self.calls.append("create_snapshot")
        if self.fail_snapshot:
            raise RuntimeError("C:/private/token=must-not-leak")
        reference = f"snapshot-{len(self.snapshots) + 1}"
        self.snapshots[reference] = copy.deepcopy(self.target)
        checksum = hashlib.sha256(
            json.dumps(self.target, sort_keys=True).encode()
        ).hexdigest()
        return self.module.SnapshotReference(reference, checksum)

    def apply_datasets(self, manifest, datasets, snapshot):  # noqa: ANN001
        self.calls.append("apply_datasets")
        assert snapshot.reference in self.snapshots
        self.target = copy.deepcopy(dict(datasets))
        if self.fail_apply:
            self.target["partial"] = {"secret": "must-not-leak"}
            raise RuntimeError("C:/private/api_key=must-not-leak")

    def health_check(self, manifest):  # noqa: ANN001
        self.calls.append("health_check")
        return self.module.HealthCheckReport(
            ok=self.health_ok,
            code="restore_health_ok" if self.health_ok else "runtime_reload_failed",
        )

    def rollback_snapshot(self, snapshot):  # noqa: ANN001
        self.calls.append("rollback_snapshot")
        if self.fail_rollback:
            raise RuntimeError("Cookie=must-not-leak")
        self.target = copy.deepcopy(self.snapshots[snapshot.reference])


class FailAfterPreparingJournalStore:
    def __init__(self, module) -> None:
        self.delegate = module.InMemoryRestoreJournalStore()
        self.upsert_calls = 0

    def upsert(self, record):  # noqa: ANN001
        self.upsert_calls += 1
        if self.upsert_calls > 1:
            raise RuntimeError("token=journal-secret-must-not-leak")
        self.delegate.upsert(record)

    def get(self, journal_id):  # noqa: ANN001
        return self.delegate.get(journal_id)

    def find_by_plan(self, plan_id):  # noqa: ANN001
        return self.delegate.find_by_plan(plan_id)

    def list_records(self):
        return self.delegate.list_records()


def _read_archive(package: bytes) -> tuple[dict[str, Any], dict[str, bytes]]:
    with zipfile.ZipFile(io.BytesIO(package)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        entries = {
            name: archive.read(name)
            for name in archive.namelist()
            if name != "manifest.json"
        }
    return manifest, entries


def _rewrite_archive(
    package: bytes,
    *,
    update_manifest=None,  # noqa: ANN001
    update_entries=None,  # noqa: ANN001
    extra_entries: tuple[tuple[str, bytes], ...] = (),
) -> bytes:
    manifest, entries = _read_archive(package)
    if update_manifest:
        update_manifest(manifest)
    if update_entries:
        update_entries(entries)
    output = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, payload in entries.items():
                archive.writestr(name, payload)
            archive.writestr(
                "manifest.json",
                json.dumps(
                    manifest,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode(),
            )
            for name, payload in extra_entries:
                archive.writestr(name, payload)
    return output.getvalue()


def _package_tree_metadata(entries: dict[str, bytes]) -> dict[str, Any]:
    files = [
        {
            "path": name,
            "size": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        }
        for name, payload in sorted(entries.items())
    ]
    encoded = json.dumps(
        files,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return {
        "size": sum(item["size"] for item in files),
        "sha256": hashlib.sha256(encoded).hexdigest(),
    }


def _mark_entry_as_zip_encrypted(package: bytes, name: str) -> bytes:
    """只改 ZIP header 的 encryption flag，用于验证读取前拒绝边界。"""

    raw = bytearray(package)
    encoded_name = name.encode("utf-8")
    cursor = 0
    changed = 0
    while True:
        position = raw.find(encoded_name, cursor)
        if position < 0:
            break
        if position >= 30 and raw[position - 30 : position - 26] == b"PK\x03\x04":
            flag_offset = position - 24
            flags = int.from_bytes(raw[flag_offset : flag_offset + 2], "little") | 0x1
            raw[flag_offset : flag_offset + 2] = flags.to_bytes(2, "little")
            changed += 1
        if position >= 46 and raw[position - 46 : position - 42] == b"PK\x01\x02":
            flag_offset = position - 38
            flags = int.from_bytes(raw[flag_offset : flag_offset + 2], "little") | 0x1
            raw[flag_offset : flag_offset + 2] = flags.to_bytes(2, "little")
            changed += 1
        cursor = position + len(encoded_name)
    assert changed == 2
    return bytes(raw)


def _state_package(service):  # noqa: ANN001
    return service.create_state_package(
        source_bot_id="bot-10001",
        schema_version="whole-v1",
        datasets={
            "profiles": {
                "users": [
                    {
                        "qq": "10001",
                        "nickname": "小明",
                        "api_key": "provider-super-secret",
                    }
                ],
                "provider": {
                    "api_url": "https://api.example/v1?token=url-secret",
                    "model": "example-model",
                },
            },
            "memories": [{"memory_id": "m1", "summary": "安全摘要"}],
            "logs": [{"message": "default-excluded-log"}],
        },
        dataset_schemas={"profiles": "profile-v2", "memories": "memory-v1"},
        dataset_dependencies={"profiles": ("config_registry",)},
        dependencies=(
            {"name": "personification", "version": "0.8", "required": True},
        ),
    )


def test_split_state_and_secrets_recursively_separates_nested_values(backup_mod) -> None:
    source = {
        "name": "route-a",
        "api_key": "key-secret",
        "headers": {"Authorization": "Bearer nested-secret"},
        "providers": [
            {
                "name": "p1",
                "api_url": "https://api.example/v1?access_token=url-secret",
                "model": "m1",
            }
        ],
        "browser_profile": "D:/private/profile",
        "os_keyring": {"service": "personification"},
        "personification_qzone_cookie": "uin=o123; p_skey=server-only;",
    }
    original = copy.deepcopy(source)

    split = backup_mod.split_state_and_secrets(source)

    assert source == original
    rendered_state = json.dumps(split.state, ensure_ascii=False)
    rendered_secrets = json.dumps(split.secrets, ensure_ascii=False)
    for secret in ("key-secret", "nested-secret", "url-secret"):
        assert secret not in rendered_state
        assert secret in rendered_secrets
    assert split.state["providers"][0]["model"] == "m1"
    assert split.secrets["providers"][0]["api_url"].endswith("url-secret")
    assert "os_keyring" in split.reauth_required
    assert "chromium_profile" in split.reauth_required
    assert "device_bound_login" in split.reauth_required
    assert "browser_profile" not in split.state
    assert "browser_profile" not in split.secrets
    assert "personification_qzone_cookie" not in split.state
    assert "personification_qzone_cookie" not in split.secrets
    assert "server-only" not in rendered_state
    assert "server-only" not in rendered_secrets


def test_state_package_manifest_and_default_exclusions(backup_mod) -> None:
    service = backup_mod.WholePluginBackupService()
    package = _state_package(service)
    inspection = service.inspect(package, expected_type=backup_mod.STATE_PACKAGE)
    manifest, entries = _read_archive(package)

    assert inspection.package_type == "state"
    assert inspection.source_bot_id == "bot-10001"
    assert inspection.schema_version == "whole-v1"
    assert inspection.dataset_names == ("memories", "profiles")
    assert inspection.encrypted is False
    assert manifest["format"] == backup_mod.PACKAGE_FORMAT
    assert manifest["version"] == backup_mod.PACKAGE_VERSION
    assert manifest["source"] == {"bot_id": "bot-10001"}
    assert manifest["payload"] == _package_tree_metadata(entries)
    assert manifest["dependencies"] == [
        {"name": "personification", "required": True, "version": "0.8"}
    ]
    declarations = {item["name"]: item for item in manifest["datasets"]}
    assert declarations["profiles"]["schema_version"] == "profile-v2"
    assert declarations["profiles"]["dependencies"] == ["config_registry"]
    for item in manifest["datasets"]:
        payload = entries[item["path"]]
        assert item["size"] == len(payload)
        assert item["sha256"] == hashlib.sha256(payload).hexdigest()
    required_exclusions = {
        "api_keys",
        "cookies",
        "webui_sessions",
        "logs",
        "full_traces",
        "temporary_caches",
        "pid_files",
        "build_artifacts",
    }
    assert required_exclusions <= set(inspection.exclusions)
    assert {
        "os_keyring",
        "chromium_profile",
        "device_bound_login",
    } <= set(inspection.reauth_required)
    rendered = b"\n".join(entries.values()).decode("utf-8")
    for secret in ("provider-super-secret", "url-secret", "default-excluded-log"):
        assert secret not in rendered


def test_state_package_round_trip_contains_only_sanitized_json(backup_mod) -> None:
    service = backup_mod.WholePluginBackupService()
    package = _state_package(service)
    backend = FakeRestoreBackend(backup_mod)

    plan = service.dry_run(package, backend=backend)

    assert plan.preflight.can_apply is True
    assert plan.preflight.changes == {"profiles": 2}
    result = service.apply(package, backend=backend, plan=plan)
    assert result.status == "applied"
    assert backend.target["profiles"]["users"][0] == {
        "nickname": "小明",
        "qq": "10001",
    }
    assert backend.target["profiles"]["provider"] == {"model": "example-model"}
    assert backend.target["memories"][0]["summary"] == "安全摘要"
    assert "logs" not in backend.target
    assert "apply_datasets" in backend.calls
    assert "health_check" in backend.calls


def test_nested_operational_artifacts_are_excluded_from_state(backup_mod) -> None:
    service = backup_mod.WholePluginBackupService()
    package = service.create_state_package(
        source_bot_id="bot-1",
        datasets={
            "runtime_state": {
                "status": "ready",
                "logs": [{"message": "raw-log"}],
                "traces": [{"prompt": "raw-trace"}],
                "cache_data": {"token": "raw-cache"},
                "pid_file": 1234,
                "build_artifacts": ["dist/app.js"],
                "webui_sessions": {"session": "raw-session"},
            }
        },
    )
    _manifest, entries = _read_archive(package)
    payload = json.loads(entries["datasets/runtime_state.json"])

    assert payload == {"status": "ready"}
    rendered = entries["datasets/runtime_state.json"].decode()
    for forbidden in (
        "raw-log",
        "raw-trace",
        "raw-cache",
        "raw-session",
        "dist/app.js",
        "1234",
    ):
        assert forbidden not in rendered


def test_state_package_can_use_injected_file_system(backup_mod) -> None:
    file_system = MemoryFileSystem()
    service = backup_mod.WholePluginBackupService(file_system=file_system)
    package = _state_package(service)

    service.write_package("exports/state.zip", package)
    inspection = service.inspect("exports/state.zip")

    assert file_system.atomic_writes == ["exports/state.zip"]
    assert inspection.package_sha256 == hashlib.sha256(package).hexdigest()


def test_secret_package_uses_random_scrypt_and_aes_gcm(backup_mod) -> None:
    service = backup_mod.WholePluginBackupService()
    secret_values = {
        "providers": [
            {
                "name": "p1",
                "api_key": "provider-key-secret",
                "headers": {"Authorization": "Bearer header-secret"},
            }
        ],
        "qzone": {"cookie": "p_skey=qzone-secret"},
        "mcp": {"credential": "mcp-secret"},
        "verification_code": "one-time-code",
        "webui_sessions": {"session": "webui-secret"},
        "logs": [{"message": "secret-log"}],
        "browser_profile": "D:/not-portable",
        "os_keyring": {"service": "not-portable"},
    }

    first = service.create_secret_package(
        source_bot_id="bot-10001",
        secrets=secret_values,
        passphrase="correct horse battery staple",
        schema_version="secret-v1",
    )
    second = service.create_secret_package(
        source_bot_id="bot-10001",
        secrets=secret_values,
        passphrase="correct horse battery staple",
        schema_version="secret-v1",
    )

    assert first != second
    for secret in (
        b"provider-key-secret",
        b"header-secret",
        b"qzone-secret",
        b"mcp-secret",
        b"correct horse battery staple",
        b"D:/not-portable",
        b"one-time-code",
        b"webui-secret",
        b"secret-log",
    ):
        assert secret not in first
    inspection = service.inspect(first, expected_type=backup_mod.SECRET_PACKAGE)
    assert inspection.encrypted is True
    assert inspection.dataset_names == (backup_mod.SECRET_DATASET_NAME,)
    encryption = inspection.manifest["encryption"]
    assert encryption["algorithm"] == "AES-256-GCM"
    assert encryption["kdf"] == "scrypt"
    assert encryption["key_length"] == 32
    assert encryption["n"] >= 2**14
    assert encryption["nonce"] != service.inspect(second).manifest["encryption"]["nonce"]
    decrypted = service.decrypt_secret_package(
        first,
        passphrase="correct horse battery staple",
    )
    restored = decrypted[backup_mod.SECRET_DATASET_NAME]
    assert restored["providers"][0]["api_key"] == "provider-key-secret"
    assert restored["qzone"]["cookie"] == "p_skey=qzone-secret"
    assert "browser_profile" not in restored
    assert "os_keyring" not in restored
    assert "verification_code" not in restored
    assert "webui_sessions" not in restored
    assert "logs" not in restored
    assert "api_keys" not in inspection.exclusions
    assert "cookies" not in inspection.exclusions
    assert set(backup_mod.DEFAULT_SECRET_EXCLUSIONS) <= set(inspection.exclusions)
    assert "chromium_profile" in inspection.reauth_required
    assert "os_keyring" in inspection.reauth_required


def test_secret_package_wrong_password_and_tampering_fail_closed(backup_mod) -> None:
    service = backup_mod.WholePluginBackupService()
    package = service.create_secret_package(
        source_bot_id="bot-1",
        secrets={"api_key": "top-secret"},
        passphrase="right-password",
    )

    with pytest.raises(backup_mod.WholePluginBackupError) as wrong:
        service.decrypt_secret_package(package, passphrase="wrong-password")
    assert wrong.value.code == "secret_password_or_integrity_invalid"
    assert "top-secret" not in str(wrong.value)
    assert "right-password" not in str(wrong.value)

    def alter_encryption(manifest):  # noqa: ANN001
        manifest["encryption"]["nonce"] = "AAAAAAAAAAAAAAAA"

    tampered_manifest = _rewrite_archive(package, update_manifest=alter_encryption)
    with pytest.raises(backup_mod.WholePluginBackupError) as tampered:
        service.inspect(tampered_manifest)
    assert tampered.value.code == "secret_manifest_authentication_invalid"

    tampered_ciphertext = _rewrite_archive(
        package,
        update_entries=lambda entries: entries.__setitem__(
            backup_mod.SECRET_PAYLOAD_PATH,
            entries[backup_mod.SECRET_PAYLOAD_PATH][:-1]
            + bytes([entries[backup_mod.SECRET_PAYLOAD_PATH][-1] ^ 1]),
        ),
    )
    with pytest.raises(backup_mod.WholePluginBackupError) as checksum:
        service.inspect(tampered_ciphertext)
    assert checksum.value.code == "backup_dataset_checksum_mismatch"


def test_secret_package_rejects_weak_or_invalid_kdf_parameters(backup_mod) -> None:
    service = backup_mod.WholePluginBackupService()
    weak = backup_mod.ScryptParameters(n=1024, r=8, p=1, length=32)
    with pytest.raises(backup_mod.WholePluginBackupError) as error:
        service.create_secret_package(
            source_bot_id="bot-1",
            secrets={"api_key": "secret"},
            passphrase="password",
            scrypt_parameters=weak,
        )
    assert error.value.code == "secret_kdf_parameters_invalid"


def test_empty_or_only_excluded_packages_are_rejected(backup_mod) -> None:
    service = backup_mod.WholePluginBackupService()
    with pytest.raises(backup_mod.WholePluginBackupError) as state_error:
        service.create_state_package(
            source_bot_id="bot-1",
            datasets={"logs": [{"message": "excluded"}]},
        )
    assert state_error.value.code == "backup_state_empty"

    with pytest.raises(backup_mod.WholePluginBackupError) as secret_error:
        service.create_secret_package(
            source_bot_id="bot-1",
            secrets={"browser_profile": "D:/device-bound"},
            passphrase="password",
        )
    assert secret_error.value.code == "secret_payload_empty"


def test_inspect_rejects_externally_forged_empty_state_package(backup_mod) -> None:
    service = backup_mod.WholePluginBackupService()
    package = _state_package(service)

    def empty_manifest(manifest):  # noqa: ANN001
        manifest["datasets"] = []
        manifest["payload"] = _package_tree_metadata({})

    empty = _rewrite_archive(
        package,
        update_manifest=empty_manifest,
        update_entries=lambda entries: entries.clear(),
    )
    with pytest.raises(backup_mod.WholePluginBackupError) as error:
        service.inspect(empty)
    assert error.value.code == "backup_state_empty"


@pytest.mark.parametrize(
    ("name", "expected_code"),
    (
        ("../escape.json", "backup_archive_path_invalid"),
        ("/absolute.json", "backup_archive_path_invalid"),
        ("C:/drive.json", "backup_archive_path_invalid"),
    ),
)
def test_inspect_rejects_zip_slip_and_absolute_paths(
    backup_mod,
    name: str,
    expected_code: str,
) -> None:
    service = backup_mod.WholePluginBackupService()
    package = _state_package(service)
    malicious = _rewrite_archive(package, extra_entries=((name, b"x"),))
    with pytest.raises(backup_mod.WholePluginBackupError) as error:
        service.inspect(malicious)
    assert error.value.code == expected_code


def test_archive_name_guard_rejects_windows_backslashes(backup_mod) -> None:
    # Windows 上 zipfile 写入器会先把反斜杠正规化为斜杠，因此直接验证底层守卫。
    with pytest.raises(backup_mod.WholePluginBackupError) as error:
        backup_mod._safe_archive_name(  # noqa: SLF001
            r"folder\backslash.json",
            backup_mod.ArchiveLimits(),
        )
    assert error.value.code == "backup_archive_path_invalid"


def test_inspect_rejects_duplicate_casefold_and_unicode_entries(backup_mod) -> None:
    service = backup_mod.WholePluginBackupService()
    package = _state_package(service)
    cases = (
        (("same.json", b"1"), ("same.json", b"2")),
        (("A.json", b"1"), ("a.json", b"2")),
        (("é.json", b"1"), ("e\u0301.json", b"2")),
    )
    for extras in cases:
        malicious = _rewrite_archive(package, extra_entries=extras)
        with pytest.raises(backup_mod.WholePluginBackupError) as error:
            service.inspect(malicious)
        assert error.value.code in {
            "backup_archive_duplicate_entry",
            "backup_archive_path_invalid",
        }


def test_inspect_rejects_symlink_and_zip_level_encryption(backup_mod) -> None:
    service = backup_mod.WholePluginBackupService()
    package = _state_package(service)
    manifest, entries = _read_archive(package)

    symlink_stream = io.BytesIO()
    with zipfile.ZipFile(symlink_stream, "w") as archive:
        for name, payload in entries.items():
            archive.writestr(name, payload)
        archive.writestr("manifest.json", json.dumps(manifest).encode())
        info = zipfile.ZipInfo("link")
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(info, "datasets/profiles.json")
    with pytest.raises(backup_mod.WholePluginBackupError) as symlink:
        service.inspect(symlink_stream.getvalue())
    assert symlink.value.code == "backup_archive_symlink_forbidden"

    encrypted_package = _mark_entry_as_zip_encrypted(
        package,
        "datasets/profiles.json",
    )
    with pytest.raises(backup_mod.WholePluginBackupError) as encrypted:
        service.inspect(encrypted_package)
    assert encrypted.value.code == "backup_archive_encrypted_entry_forbidden"


def test_inspect_rejects_undeclared_missing_and_checksum_mismatch(backup_mod) -> None:
    service = backup_mod.WholePluginBackupService()
    package = _state_package(service)

    undeclared = _rewrite_archive(package, extra_entries=(("extra.json", b"x"),))
    with pytest.raises(backup_mod.WholePluginBackupError) as extra:
        service.inspect(undeclared)
    assert extra.value.code == "backup_archive_undeclared_entry"

    missing = _rewrite_archive(
        package,
        update_entries=lambda entries: entries.pop("datasets/memories.json"),
    )
    with pytest.raises(backup_mod.WholePluginBackupError) as missing_error:
        service.inspect(missing)
    assert missing_error.value.code == "backup_archive_undeclared_entry"

    modified = _rewrite_archive(
        package,
        update_entries=lambda entries: entries.__setitem__(
            "datasets/profiles.json", b'{"poisoned":true}'
        ),
    )
    with pytest.raises(backup_mod.WholePluginBackupError) as mismatch:
        service.inspect(modified)
    assert mismatch.value.code == "backup_dataset_checksum_mismatch"


def test_inspect_rejects_compression_bomb_and_entry_limits(backup_mod) -> None:
    source_service = backup_mod.WholePluginBackupService(
        limits=backup_mod.ArchiveLimits(max_compression_ratio=2000.0)
    )
    package = source_service.create_state_package(
        source_bot_id="bot-1",
        datasets={"repeated": {"text": "A" * 100_000}},
    )
    strict_service = backup_mod.WholePluginBackupService(
        limits=backup_mod.ArchiveLimits(
            max_archive_bytes=1024 * 1024,
            max_manifest_bytes=1024 * 1024,
            max_entries=10,
            max_entry_bytes=200_000,
            max_expanded_bytes=400_000,
            max_compression_ratio=20.0,
        )
    )
    with pytest.raises(backup_mod.WholePluginBackupError) as bomb:
        strict_service.inspect(package)
    assert bomb.value.code == "backup_archive_compression_bomb"

    tiny_entry_service = backup_mod.WholePluginBackupService(
        limits=backup_mod.ArchiveLimits(
            max_archive_bytes=1024 * 1024,
            max_manifest_bytes=1024 * 1024,
            max_entries=10,
            max_entry_bytes=100,
            max_expanded_bytes=1024 * 1024,
            max_compression_ratio=1000.0,
        )
    )
    with pytest.raises(backup_mod.WholePluginBackupError) as entry:
        tiny_entry_service.inspect(package)
    assert entry.value.code == "backup_archive_entry_too_large"


def test_manifest_rejects_unknown_fields_duplicate_json_keys_and_nan(backup_mod) -> None:
    service = backup_mod.WholePluginBackupService()
    package = _state_package(service)

    unknown = _rewrite_archive(
        package,
        update_manifest=lambda manifest: manifest.__setitem__("unexpected", True),
    )
    with pytest.raises(backup_mod.WholePluginBackupError) as unknown_error:
        service.inspect(unknown)
    assert unknown_error.value.code == "backup_manifest_invalid"

    manifest, entries = _read_archive(package)
    duplicate_json = json.dumps(manifest, ensure_ascii=False)[:-1] + ',"format":"evil"}'
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        for name, payload in entries.items():
            archive.writestr(name, payload)
        archive.writestr("manifest.json", duplicate_json.encode())
    with pytest.raises(backup_mod.WholePluginBackupError) as duplicate:
        service.inspect(stream.getvalue())
    assert duplicate.value.code == "backup_manifest_invalid"

    nan_manifest = _rewrite_archive(
        package,
        update_manifest=lambda manifest: manifest.__setitem__("created_at", float("nan")),
    )
    with pytest.raises(backup_mod.WholePluginBackupError) as nan_error:
        service.inspect(nan_manifest)
    assert nan_error.value.code == "backup_manifest_invalid"


def test_dry_run_is_bound_to_package_target_and_preflight(backup_mod) -> None:
    service = backup_mod.WholePluginBackupService(clock=lambda: 1000.0)
    package = _state_package(service)
    backend = FakeRestoreBackend(backup_mod)
    plan = service.dry_run(package, backend=backend)

    assert plan.created_at == 1000.0
    assert plan.expires_at == 1600.0
    backend.target_id = "different-instance"
    with pytest.raises(backup_mod.WholePluginBackupError) as target_changed:
        service.apply(package, backend=backend, plan=plan)
    assert target_changed.value.code == "restore_plan_mismatch"

    backend.target_id = "fresh-instance"
    backend.preflight_report = backup_mod.PreflightReport(changes={"profiles": 3})
    with pytest.raises(backup_mod.WholePluginBackupError) as preflight_changed:
        service.apply(package, backend=backend, plan=plan)
    assert preflight_changed.value.code == "restore_plan_mismatch"

    changed_package = service.create_state_package(
        source_bot_id="bot-10001",
        datasets={"profiles": {"changed": True}},
    )
    with pytest.raises(backup_mod.WholePluginBackupError) as package_changed:
        service.apply(changed_package, backend=backend, plan=plan)
    assert package_changed.value.code == "restore_plan_mismatch"


def test_dry_run_plan_is_authenticated_by_injected_service_key(backup_mod) -> None:
    package_service = backup_mod.WholePluginBackupService(
        plan_signing_key=b"a" * 32,
    )
    other_service = backup_mod.WholePluginBackupService(
        plan_signing_key=b"b" * 32,
    )
    compatible_service = backup_mod.WholePluginBackupService(
        plan_signing_key=b"a" * 32,
    )
    package = _state_package(package_service)
    backend = FakeRestoreBackend(backup_mod)
    plan = package_service.dry_run(package, backend=backend)

    with pytest.raises(backup_mod.WholePluginBackupError) as foreign:
        other_service.apply(package, backend=backend, plan=plan)
    assert foreign.value.code == "restore_plan_mismatch"

    result = compatible_service.apply(package, backend=backend, plan=plan)
    assert result.status == "applied"


def test_expired_and_blocked_dry_run_cannot_apply(backup_mod) -> None:
    current_time = [1000.0]
    service = backup_mod.WholePluginBackupService(
        clock=lambda: current_time[0],
        plan_ttl_seconds=5,
    )
    package = _state_package(service)
    backend = FakeRestoreBackend(backup_mod)
    plan = service.dry_run(package, backend=backend)
    current_time[0] = 1006.0
    with pytest.raises(backup_mod.WholePluginBackupError) as expired:
        service.apply(package, backend=backend, plan=plan)
    assert expired.value.code == "restore_plan_expired"

    current_time[0] = 2000.0
    backend.preflight_report = backup_mod.PreflightReport(
        can_apply=False,
        conflicts=("dataset_primary_key_conflict",),
    )
    blocked_plan = service.dry_run(package, backend=backend)
    with pytest.raises(backup_mod.WholePluginBackupError) as blocked:
        service.apply(package, backend=backend, plan=blocked_plan)
    assert blocked.value.code == "restore_preflight_blocked"
    assert "create_snapshot" not in backend.calls


def test_successful_apply_is_idempotent_and_manual_rollback_restores_snapshot(backup_mod) -> None:
    store = backup_mod.InMemoryRestoreJournalStore()
    service = backup_mod.WholePluginBackupService(journal_store=store)
    package = _state_package(service)
    backend = FakeRestoreBackend(backup_mod)
    before = copy.deepcopy(backend.target)
    plan = service.dry_run(package, backend=backend)

    first = service.apply(package, backend=backend, plan=plan)
    second = service.apply(package, backend=backend, plan=plan)

    assert first.status == "applied"
    assert second.idempotent is True
    assert second.journal_id == first.journal_id
    records = store.list_records()
    assert len(records) == 1
    assert records[0].status == "applied"
    assert records[0].snapshot_reference
    assert records[0].diagnostic_code == "restore_health_ok"
    assert backend.target != before

    rolled = service.rollback(first.journal_id, backend=backend)
    repeated = service.rollback(first.journal_id, backend=backend)
    assert rolled.status == "rolled_back"
    assert rolled.to_dict()["ok"] is True
    assert repeated.idempotent is True
    assert backend.target == before
    assert store.get(first.journal_id).status == "rolled_back"


@pytest.mark.parametrize("failure", ("apply", "health"))
def test_apply_or_health_failure_automatically_rolls_back(
    backup_mod,
    failure: str,
) -> None:
    store = backup_mod.InMemoryRestoreJournalStore()
    service = backup_mod.WholePluginBackupService(journal_store=store)
    package = _state_package(service)
    backend = FakeRestoreBackend(backup_mod)
    before = copy.deepcopy(backend.target)
    backend.fail_apply = failure == "apply"
    backend.health_ok = failure != "health"
    plan = service.dry_run(package, backend=backend)

    with pytest.raises(backup_mod.WholePluginBackupError) as error:
        service.apply(package, backend=backend, plan=plan)

    assert error.value.code == "restore_apply_failed_rolled_back"
    assert "must-not-leak" not in str(error.value)
    assert backend.target == before
    record = store.list_records()[0]
    assert record.status == "rolled_back"
    assert "rollback_snapshot" in backend.calls


def test_snapshot_failure_never_calls_apply_or_rollback(backup_mod) -> None:
    store = backup_mod.InMemoryRestoreJournalStore()
    service = backup_mod.WholePluginBackupService(journal_store=store)
    package = _state_package(service)
    backend = FakeRestoreBackend(backup_mod)
    backend.fail_snapshot = True
    plan = service.dry_run(package, backend=backend)

    with pytest.raises(backup_mod.WholePluginBackupError) as error:
        service.apply(package, backend=backend, plan=plan)

    assert error.value.code == "restore_snapshot_failed"
    assert "must-not-leak" not in str(error.value)
    assert "apply_datasets" not in backend.calls
    assert "rollback_snapshot" not in backend.calls
    assert store.list_records()[0].status == "failed"


def test_rollback_failure_is_quarantined_as_outcome_unknown(backup_mod) -> None:
    store = backup_mod.InMemoryRestoreJournalStore()
    service = backup_mod.WholePluginBackupService(journal_store=store)
    package = _state_package(service)
    backend = FakeRestoreBackend(backup_mod)
    backend.fail_apply = True
    backend.fail_rollback = True
    plan = service.dry_run(package, backend=backend)

    with pytest.raises(backup_mod.WholePluginBackupError) as error:
        service.apply(package, backend=backend, plan=plan)
    assert error.value.code == "restore_rollback_outcome_unknown"
    assert "must-not-leak" not in str(error.value)
    record = store.list_records()[0]
    assert record.status == "outcome_unknown"

    backend.fail_apply = False
    backend.fail_rollback = False
    with pytest.raises(backup_mod.WholePluginBackupError) as retry:
        service.apply(package, backend=backend, plan=plan)
    assert retry.value.code == "restore_previous_outcome_unresolved"


def test_journal_failure_after_snapshot_still_rolls_back_target(backup_mod) -> None:
    store = FailAfterPreparingJournalStore(backup_mod)
    service = backup_mod.WholePluginBackupService(journal_store=store)
    package = _state_package(service)
    backend = FakeRestoreBackend(backup_mod)
    before = copy.deepcopy(backend.target)
    plan = service.dry_run(package, backend=backend)

    with pytest.raises(backup_mod.WholePluginBackupError) as error:
        service.apply(package, backend=backend, plan=plan)

    assert error.value.code == "restore_journal_unavailable_after_rollback"
    assert "journal-secret-must-not-leak" not in str(error.value)
    assert backend.target == before
    assert "create_snapshot" in backend.calls
    assert "apply_datasets" not in backend.calls
    assert "rollback_snapshot" in backend.calls


def test_secret_restore_requires_password_and_uses_same_atomic_contract(backup_mod) -> None:
    service = backup_mod.WholePluginBackupService()
    package = service.create_secret_package(
        source_bot_id="bot-1",
        secrets={"api_key": "secret-to-restore"},
        passphrase="secret-password",
    )
    backend = FakeRestoreBackend(backup_mod)

    with pytest.raises(backup_mod.WholePluginBackupError) as missing:
        service.dry_run(package, backend=backend)
    assert missing.value.code == "secret_passphrase_required"

    plan = service.dry_run(
        package,
        backend=backend,
        passphrase="secret-password",
    )
    result = service.apply(
        package,
        backend=backend,
        plan=plan,
        passphrase="secret-password",
    )
    assert result.status == "applied"
    assert backend.target[backup_mod.SECRET_DATASET_NAME]["api_key"] == "secret-to-restore"
    record_json = json.dumps(
        service.journal_store.list_records()[0].to_dict(),
        ensure_ascii=False,
    )
    assert "secret-password" not in record_json
    assert "secret-to-restore" not in record_json


def test_json_journal_store_uses_injected_atomic_file_system(backup_mod) -> None:
    file_system = MemoryFileSystem()
    store = backup_mod.JsonRestoreJournalStore(
        "journals/whole-backup.json",
        file_system=file_system,
    )
    record = backup_mod.RestoreJournalRecord(
        journal_id="1" * 32,
        plan_id="2" * 64,
        package_id="3" * 32,
        package_type=backup_mod.STATE_PACKAGE,
        package_sha256="4" * 64,
        target_fingerprint="5" * 64,
        status="preparing",
        created_at=100.0,
        updated_at=100.0,
    )

    store.upsert(record)
    loaded = store.get(record.journal_id)
    store.upsert(replace(record, status="applied", updated_at=101.0))

    assert loaded == record
    assert store.find_by_plan(record.plan_id)[0].status == "applied"
    assert file_system.atomic_writes == [
        "journals/whole-backup.json",
        "journals/whole-backup.json",
    ]
    rendered = file_system.files["journals/whole-backup.json"].decode()
    assert "passphrase" not in rendered
    assert "secret" not in rendered


def test_recover_incomplete_rolls_back_only_records_with_known_snapshots(backup_mod) -> None:
    store = backup_mod.InMemoryRestoreJournalStore()
    service = backup_mod.WholePluginBackupService(journal_store=store)
    backend = FakeRestoreBackend(backup_mod)
    target_hash = hashlib.sha256(backend.target_id.encode()).hexdigest()
    backend.snapshots["snapshot-1"] = {"old": {"value": "recovered"}}
    applying = backup_mod.RestoreJournalRecord(
        journal_id="a" * 32,
        plan_id="b" * 64,
        package_id="c" * 32,
        package_type=backup_mod.STATE_PACKAGE,
        package_sha256="d" * 64,
        target_fingerprint=target_hash,
        status="applying",
        snapshot_reference="snapshot-1",
        snapshot_checksum="e" * 64,
        created_at=1.0,
        updated_at=1.0,
    )
    before_snapshot = replace(
        applying,
        journal_id="f" * 32,
        plan_id="1" * 64,
        package_id="2" * 32,
        status="preparing",
        snapshot_reference="",
        snapshot_checksum="",
    )
    unknown = replace(
        applying,
        journal_id="3" * 32,
        plan_id="4" * 64,
        package_id="5" * 32,
        status="outcome_unknown",
    )
    for record in (applying, before_snapshot, unknown):
        store.upsert(record)

    results = service.recover_incomplete(backend=backend)

    assert len(results) == 1
    assert results[0].journal_id == applying.journal_id
    assert backend.target == {"old": {"value": "recovered"}}
    assert store.get(applying.journal_id).status == "rolled_back"
    assert store.get(before_snapshot.journal_id).status == "failed"
    assert store.get(unknown.journal_id).status == "outcome_unknown"


def test_visible_errors_are_chinese_with_stable_codes_and_no_raw_exception(backup_mod) -> None:
    service = backup_mod.WholePluginBackupService()
    with pytest.raises(backup_mod.WholePluginBackupError) as error:
        service.inspect(b"not-a-zip password=must-not-leak")

    diagnostic = error.value.to_dict()
    rendered = json.dumps(diagnostic, ensure_ascii=False)
    assert diagnostic["code"] == "backup_archive_invalid"
    assert "备份包" in diagnostic["message"]
    assert "诊断码" in str(error.value)
    assert "must-not-leak" not in rendered
    assert "password" not in rendered


def test_local_file_system_only_touches_explicit_temporary_paths(
    backup_mod,
    tmp_path: Path,
) -> None:
    service = backup_mod.WholePluginBackupService()
    package = _state_package(service)
    target = tmp_path / "isolated" / "whole-state.zip"

    service.write_package(target, package)

    assert target.read_bytes() == package
    assert service.inspect(target).package_type == "state"
    assert list(target.parent.iterdir()) == [target]
