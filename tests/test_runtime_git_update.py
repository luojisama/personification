from __future__ import annotations

import asyncio

from ._loader import load_personification_module


runtime_commands = load_personification_module("plugin.personification.handlers.runtime_commands")
plugin_update_manager = load_personification_module("plugin.personification.core.plugin_update_manager")


def test_runtime_git_update_compatibility_calls_shared_manager(tmp_path, monkeypatch) -> None:
    calls: list[tuple[str, str]] = []

    async def fake_status(*, plugin_root=None, plugin_config=None, refresh=False, history_limit=12):  # noqa: ANN001
        calls.append(("status", str(plugin_root)))
        assert refresh is True
        return {
            "ok": True,
            "available": True,
            "update_available": True,
            "local": {"hash": "a" * 40},
            "remote": {"hash": "b" * 40},
        }

    async def fake_update(*, plugin_root=None, plugin_config=None):  # noqa: ANN001
        calls.append(("update", str(plugin_root)))
        return {"ok": True, "updated": True, "message": "已完成本地 fast-forward"}

    monkeypatch.setattr(plugin_update_manager, "get_plugin_update_status", fake_status)
    monkeypatch.setattr(plugin_update_manager, "perform_plugin_update", fake_update)

    available, local_hash, remote_hash = asyncio.run(
        runtime_commands.check_git_update_available(plugin_dir=str(tmp_path))
    )
    updated, message = asyncio.run(runtime_commands.perform_git_pull(plugin_dir=str(tmp_path)))

    assert available is True
    assert local_hash == "a" * 40
    assert remote_hash == "b" * 40
    assert updated is True
    assert "fast-forward" in message
    assert calls == [("status", str(tmp_path)), ("update", str(tmp_path))]


def test_runtime_commands_have_no_second_mirror_or_pull_implementation() -> None:
    assert not hasattr(runtime_commands, "_probe_mirror")
    assert not hasattr(runtime_commands, "_run_git_with_mirror_fallback")
    assert not hasattr(runtime_commands, "_looks_like_network_failure")
