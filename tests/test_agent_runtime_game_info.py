from __future__ import annotations

from types import SimpleNamespace

from ._loader import load_personification_module


agent_runtime = load_personification_module("plugin.personification.core.services.agent_runtime")


class _Logger:
    def warning(self, _message: str) -> None:
        pass

    def debug(self, _message: str) -> None:
        pass


def test_legacy_registry_exposes_game_info_by_default() -> None:
    config = SimpleNamespace(
        personification_use_skillpacks=False,
        personification_game_info_enabled=True,
        personification_memory_enabled=False,
        personification_tool_web_fetch_enabled=False,
        personification_60s_enabled=False,
        personification_timezone="Asia/Shanghai",
        personification_sticker_path="__missing_stickers__",
    )
    registry = agent_runtime.build_agent_tool_registry(
        plugin_config=config,
        logger=_Logger(),
        get_now=lambda: None,
    )

    assert "game_info" in {tool.name for tool in registry.active()}
