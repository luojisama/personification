from __future__ import annotations

from personification.core.backup_artifact_store import BackupArtifactStore


class _Ids:
    def __init__(self) -> None:
        self.value = 0

    def __call__(self, _length: int) -> str:
        self.value += 1
        return f"artifact-{self.value}-" + "x" * 32


def test_artifact_is_owner_device_bound_and_expires() -> None:
    clock = [100.0]
    store = BackupArtifactStore(clock=lambda: clock[0], id_factory=_Ids())
    artifact = store.put(
        b"zip",
        owner_qq="1",
        owner_device_id="device",
        package_type="state",
    )

    assert store.get(artifact.artifact_id, owner_qq="1", owner_device_id="device") == artifact
    assert store.get(artifact.artifact_id, owner_qq="2", owner_device_id="device") is None
    assert store.get(artifact.artifact_id, owner_qq="1", owner_device_id="other") is None
    clock[0] = 701.0
    assert store.get(artifact.artifact_id, owner_qq="1", owner_device_id="device") is None


def test_artifact_capacity_evicts_oldest_and_file_name_is_safe() -> None:
    clock = [100.0]
    store = BackupArtifactStore(clock=lambda: clock[0], id_factory=_Ids(), max_artifacts=1)
    first = store.put(
        b"one",
        owner_qq="1",
        owner_device_id="device",
        package_type="state",
        file_name="../secret.zip",
    )
    clock[0] += 1
    second = store.put(
        b"two",
        owner_qq="1",
        owner_device_id="device",
        package_type="secret",
    )

    assert "/" not in first.file_name and "\\" not in first.file_name
    assert store.get(first.artifact_id, owner_qq="1", owner_device_id="device") is None
    assert store.get(second.artifact_id, owner_qq="1", owner_device_id="device") is not None
