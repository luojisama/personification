from __future__ import annotations

"""QZone Bot 凭据的本地隔离存储。

Cookie 只能在 Bot 服务器本地读取和使用。这个模块故意不提供批量导出接口，
调用方只能按精确 ``bot_id`` 取回单条凭据，管理面只可读取无秘密的描述信息。
"""

import csv
import json
import os
import re
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from .paths import get_data_dir


_STORE_LOCK = threading.Lock()
_BOT_ID_RE = re.compile(r"[A-Za-z0-9_.:-]{1,64}")
_SOURCE_RE = re.compile(r"[A-Za-z0-9_.:-]{1,32}")
_WINDOWS_SID_RE = re.compile(r"S-\d+(?:-\d+)+")
_SCHEMA_VERSION = 1
_WINDOWS_OWNER_SID = ""


def _normalize_bot_id(value: Any) -> str:
    bot_id = str(value or "").strip()
    if not _BOT_ID_RE.fullmatch(bot_id):
        raise ValueError("qzone_credential_bot_id_invalid")
    return bot_id


def _normalize_source(value: Any) -> str:
    source = str(value or "unknown").strip().lower()
    return source if _SOURCE_RE.fullmatch(source) else "unknown"


def _current_windows_owner_sid() -> str:
    """Return the process owner's SID without passing secret material to a shell."""

    global _WINDOWS_OWNER_SID
    if _WINDOWS_OWNER_SID:
        return _WINDOWS_OWNER_SID
    try:
        result = subprocess.run(
            ["whoami", "/user", "/fo", "csv", "/nh"],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError("qzone_credential_store_permission_identity_failed") from exc
    if result.returncode != 0:
        raise RuntimeError("qzone_credential_store_permission_identity_failed")
    try:
        rows = tuple(csv.reader(result.stdout.splitlines()))
        sid = next(
            (
                field.strip()
                for row in rows
                for field in row
                if _WINDOWS_SID_RE.fullmatch(field.strip())
            ),
            "",
        )
    except (csv.Error, TypeError):
        sid = ""
    if not sid:
        raise RuntimeError("qzone_credential_store_permission_identity_failed")
    _WINDOWS_OWNER_SID = sid
    return sid


def _restrict_secret_file_permissions(path: Path) -> None:
    """Fail closed unless the new secret file is owner-only readable/writable.

    POSIX mode bits protect the file on Unix.  Windows ignores those bits for
    ACL authorization, so use the process owner's SID with ``icacls`` and
    remove inherited grants before the atomic rename.  The command receives
    only a generated local path and an OS-issued SID, never a Cookie.
    """

    try:
        os.chmod(path, 0o600)
    except OSError as exc:
        raise RuntimeError("qzone_credential_store_permission_failed") from exc
    if os.name != "nt":
        return
    owner_sid = _current_windows_owner_sid()
    try:
        result = subprocess.run(
            [
                "icacls",
                str(path),
                "/inheritance:r",
                "/grant:r",
                f"*{owner_sid}:(R,W)",
            ],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError("qzone_credential_store_permission_failed") from exc
    if result.returncode != 0:
        raise RuntimeError("qzone_credential_store_permission_failed")


class QzoneCredentialStore:
    """Atomically persist QZone Cookie values keyed by their exact Bot ID."""

    def __init__(self, plugin_config: Any) -> None:
        self.path = get_data_dir(plugin_config) / "qzone" / "credentials.secret.json"

    def _read_unlocked(self) -> dict[str, dict[str, Any]]:
        if not self.path.is_file():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise RuntimeError("qzone_credential_store_unreadable") from exc
        if not isinstance(raw, dict) or raw.get("version") != _SCHEMA_VERSION:
            raise RuntimeError("qzone_credential_store_invalid")
        credentials = raw.get("credentials")
        if not isinstance(credentials, dict):
            raise RuntimeError("qzone_credential_store_invalid")

        normalized: dict[str, dict[str, Any]] = {}
        for raw_bot_id, record in credentials.items():
            try:
                bot_id = _normalize_bot_id(raw_bot_id)
            except ValueError as exc:
                raise RuntimeError("qzone_credential_store_invalid") from exc
            if not isinstance(record, dict):
                raise RuntimeError("qzone_credential_store_invalid")
            cookie = record.get("cookie")
            source = record.get("source")
            updated_at = record.get("updated_at")
            if not isinstance(cookie, str) or not cookie.strip() or not isinstance(source, str):
                raise RuntimeError("qzone_credential_store_invalid")
            try:
                timestamp = float(updated_at)
            except (TypeError, ValueError) as exc:
                raise RuntimeError("qzone_credential_store_invalid") from exc
            if timestamp < 0:
                raise RuntimeError("qzone_credential_store_invalid")
            normalized[bot_id] = {
                "cookie": cookie.strip(),
                "source": _normalize_source(source),
                "updated_at": timestamp,
                # Older secret records have no verification marker.  Treat
                # that absence as unknown rather than implying that their
                # identity was checked by the isolated-store flow.
                "identity_verified": record.get("identity_verified") is True,
            }
        return normalized

    def _write_unlocked(self, credentials: dict[str, dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": _SCHEMA_VERSION,
            "credentials": credentials,
        }
        temp = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex[:12]}.tmp")
        try:
            # Create an empty exclusive file first, lock down its mode/ACL,
            # then write the Cookie.  No secret bytes ever exist in a
            # inherited-permission temporary file.
            descriptor = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(descriptor)
            _restrict_secret_file_permissions(temp)
            with temp.open("w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            # Same-directory replacement preserves this already restricted
            # ACL/mode, so a permission failure cannot leave a new credential
            # in place.
            os.replace(temp, self.path)
        finally:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass

    def get(self, bot_id: Any) -> str:
        exact_bot_id = _normalize_bot_id(bot_id)
        with _STORE_LOCK:
            record = self._read_unlocked().get(exact_bot_id)
            return str(record.get("cookie") or "") if isinstance(record, dict) else ""

    def describe(self, bot_id: Any) -> dict[str, Any]:
        exact_bot_id = _normalize_bot_id(bot_id)
        with _STORE_LOCK:
            record = self._read_unlocked().get(exact_bot_id)
        if not isinstance(record, dict):
            return {
                "configured": False,
                "source": "",
                "updated_at": 0.0,
                "identity_verification": "unknown",
            }
        return {
            "configured": True,
            "source": _normalize_source(record.get("source")),
            "updated_at": float(record.get("updated_at") or 0.0),
            "identity_verification": "verified" if record.get("identity_verified") is True else "unknown",
        }

    def bot_ids(self) -> tuple[str, ...]:
        with _STORE_LOCK:
            return tuple(sorted(self._read_unlocked()))

    def replace(
        self,
        *,
        bot_id: Any,
        cookie: str,
        source: Any,
        identity_verified: bool = False,
    ) -> None:
        exact_bot_id = _normalize_bot_id(bot_id)
        normalized_cookie = str(cookie or "").strip()
        if not normalized_cookie:
            raise ValueError("qzone_credential_empty")
        with _STORE_LOCK:
            credentials = self._read_unlocked()
            credentials[exact_bot_id] = {
                "cookie": normalized_cookie,
                "source": _normalize_source(source),
                "updated_at": time.time(),
                "identity_verified": bool(identity_verified),
            }
            self._write_unlocked(credentials)


__all__ = ["QzoneCredentialStore"]
