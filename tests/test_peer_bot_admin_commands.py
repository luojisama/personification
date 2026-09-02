from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from ._loader import load_personification_module


admin_commands = load_personification_module(
    "plugin.personification.handlers.persona_admin_commands"
)


def _bundle(tmp_path: Path, monkeypatch):  # noqa: ANN001
    data_store = load_personification_module("plugin.personification.core.data_store")
    paths = load_personification_module("plugin.personification.core.paths")
    registry_mod = load_personification_module("plugin.personification.core.peer_bot_registry")
    runtime_mod = load_personification_module("plugin.personification.core.peer_bot_runtime")
    monkeypatch.setattr(paths, "get_data_dir", lambda _cfg=None: tmp_path)
    config = SimpleNamespace(
        personification_data_dir=str(tmp_path),
        personification_peer_bot_max_command_chars=500,
    )
    store = data_store.init_data_store(config)
    return SimpleNamespace(
        peer_bot_registry=registry_mod.PeerBotRegistry(store=store, plugin_config=config),
        peer_bot_tracker=runtime_mod.PeerBotRuntimeTracker(),
        peer_bot_observer=SimpleNamespace(flush_group=lambda _gid: None),
    )


def test_peer_bot_admin_command_lifecycle(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    bundle = _bundle(tmp_path, monkeypatch)

    assert "已启用" in asyncio.run(
        admin_commands.handle_peer_bot_command(bundle, group_id="g1", tokens=["启用"])
    )
    assert "已启用" in asyncio.run(
        admin_commands.handle_peer_bot_command(
            bundle,
            group_id="g1",
            tokens=["自动学习", "启用"],
        )
    )
    assert bundle.peer_bot_registry.get_group("g1")["policies"][
        "auto_learn_approved_commands"
    ] is True
    assert "approved" in asyncio.run(
        admin_commands.handle_peer_bot_command(
            bundle,
            group_id="g1",
            tokens=["确认", "20002"],
        )
    )
    added = asyncio.run(
        admin_commands.handle_peer_bot_command(
            bundle,
            group_id="g1",
            tokens=["命令", "添加", "20002", "write", ".mc", "say", "{message}"],
        )
    )
    command_id = next(iter(bundle.peer_bot_registry.get_group("g1")["commands"]))
    assert command_id in added
    approved = asyncio.run(
        admin_commands.handle_peer_bot_command(
            bundle,
            group_id="g1",
            tokens=["命令", "确认", "20002", command_id],
        )
    )
    assert "已确认" in approved
    listed = asyncio.run(
        admin_commands.handle_peer_bot_command(bundle, group_id="g1", tokens=["列表"])
    )
    assert ".mc say {message}" in listed
    assert "write/approved" in listed


def test_peer_bot_admin_command_rejects_invalid_or_dangerous_shape(
    tmp_path: Path,
    monkeypatch,
) -> None:  # noqa: ANN001
    bundle = _bundle(tmp_path, monkeypatch)
    result = asyncio.run(
        admin_commands.handle_peer_bot_command(
            bundle,
            group_id="g1",
            tokens=["命令", "添加", "not-a-qq", "dangerous", "/stop"],
        )
    )
    assert result.startswith("用法：")


def test_peer_bot_command_alias_normalizes() -> None:
    assert admin_commands.normalize_command_word("群Bot") == "peer_bot"


def test_peer_bot_admin_command_hides_unexpected_exception_details() -> None:
    class _BrokenRegistry:
        def get_group(self, _group_id: str):
            raise RuntimeError(r"C:\private\api_key=secret")

    bundle = SimpleNamespace(peer_bot_registry=_BrokenRegistry())
    result = asyncio.run(
        admin_commands.handle_peer_bot_command(bundle, group_id="g1", tokens=["列表"])
    )

    assert result == "Peer Bot 管理失败：peer_bot_admin_operation_failed"
    assert "C:\\private" not in result
    assert "secret" not in result
