from __future__ import annotations

import secrets
import threading
import time
import re
from dataclasses import dataclass, field
from typing import Any, Callable


BACKUP_ARTIFACT_TTL_SECONDS = 600.0


@dataclass(frozen=True, slots=True)
class BackupArtifact:
    artifact_id: str
    owner_qq: str
    owner_device_id: str
    package_type: str
    payload: bytes = field(repr=False)
    created_at: float
    expires_at: float
    file_name: str

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "package_type": self.package_type,
            "size": len(self.payload),
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "file_name": self.file_name,
        }


@dataclass(slots=True)
class BackupArtifactStore:
    clock: Callable[[], float] = time.time
    id_factory: Callable[[int], str] = secrets.token_urlsafe
    ttl_seconds: float = BACKUP_ARTIFACT_TTL_SECONDS
    max_artifacts: int = 32
    _lock: threading.RLock = field(default_factory=threading.RLock, init=False)
    _items: dict[str, BackupArtifact] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        self.ttl_seconds = max(1.0, min(BACKUP_ARTIFACT_TTL_SECONDS, float(self.ttl_seconds)))
        self.max_artifacts = max(1, min(128, int(self.max_artifacts)))

    def _now(self) -> float:
        return float(self.clock())

    def _prune_locked(self, now: float) -> int:
        before = len(self._items)
        self._items = {
            key: value
            for key, value in self._items.items()
            if value.expires_at > now
        }
        return before - len(self._items)

    def put(
        self,
        payload: bytes,
        *,
        owner_qq: Any,
        owner_device_id: Any,
        package_type: Any,
        file_name: Any = "personification-backup.zip",
    ) -> BackupArtifact:
        if not isinstance(payload, bytes) or not payload:
            raise ValueError("backup artifact payload must be non-empty bytes")
        qq = str(owner_qq or "").strip()
        device = str(owner_device_id or "").strip()
        kind = str(package_type or "").strip()
        if not qq or not device or kind not in {"state", "secret"}:
            raise ValueError("backup artifact owner or type invalid")
        now = self._now()
        artifact_id = str(self.id_factory(24) or "").strip()
        if len(artifact_id) < 24:
            raise ValueError("backup artifact id invalid")
        name = re.sub(
            r"[^A-Za-z0-9._-]+",
            "_",
            str(file_name or "personification-backup.zip").replace("\\", "_").replace("/", "_")[:120],
        ).strip("._")
        artifact = BackupArtifact(
            artifact_id=artifact_id,
            owner_qq=qq,
            owner_device_id=device,
            package_type=kind,
            payload=bytes(payload),
            created_at=now,
            expires_at=now + self.ttl_seconds,
            file_name=name or "personification-backup.zip",
        )
        with self._lock:
            self._prune_locked(now)
            if len(self._items) >= self.max_artifacts:
                oldest = min(self._items.values(), key=lambda item: item.created_at)
                self._items.pop(oldest.artifact_id, None)
            self._items[artifact_id] = artifact
        return artifact

    def get(self, artifact_id: Any, *, owner_qq: Any, owner_device_id: Any) -> BackupArtifact | None:
        key = str(artifact_id or "").strip()
        now = self._now()
        with self._lock:
            self._prune_locked(now)
            artifact = self._items.get(key)
            if artifact is None:
                return None
            if artifact.owner_qq != str(owner_qq or "").strip():
                return None
            if artifact.owner_device_id != str(owner_device_id or "").strip():
                return None
            return artifact

    def delete(self, artifact_id: Any) -> bool:
        with self._lock:
            return self._items.pop(str(artifact_id or "").strip(), None) is not None

    def prune(self) -> int:
        with self._lock:
            return self._prune_locked(self._now())

    def clear(self) -> None:
        with self._lock:
            self._items.clear()


DEFAULT_BACKUP_ARTIFACT_STORE = BackupArtifactStore()


__all__ = [
    "BACKUP_ARTIFACT_TTL_SECONDS",
    "BackupArtifact",
    "BackupArtifactStore",
    "DEFAULT_BACKUP_ARTIFACT_STORE",
]
