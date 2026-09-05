from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from ._loader import load_personification_module


def test_one_shot_build_can_start_while_persistent_switch_stays_disabled(tmp_path, monkeypatch) -> None:
    builder = load_personification_module("plugin.personification.core.knowledge_builder")
    store_mod = load_personification_module("plugin.personification.core.knowledge_store")
    store = store_mod.PluginKnowledgeStore(tmp_path)
    config = SimpleNamespace(personification_plugin_knowledge_build_enabled=False)
    holder = {"task": None}

    async def _wait_forever() -> None:
        await asyncio.Event().wait()

    def _start_knowledge_builder(**_kwargs):
        return asyncio.create_task(_wait_forever())

    monkeypatch.setattr(builder, "start_knowledge_builder", _start_knowledge_builder)

    async def _scenario() -> None:
        normal = await builder.maybe_start_plugin_knowledge_builder(
            plugin_config=config,
            tool_caller=object(),
            knowledge_store=store,
            logger=SimpleNamespace(warning=lambda *_args: None),
            get_knowledge_build_task=lambda: holder["task"],
            set_knowledge_build_task=lambda task: holder.__setitem__("task", task),
            trigger="startup",
        )
        assert normal["result"] == "disabled_skip"

        started = await builder.maybe_start_plugin_knowledge_builder(
            plugin_config=config,
            tool_caller=object(),
            knowledge_store=store,
            logger=SimpleNamespace(warning=lambda *_args: None),
            get_knowledge_build_task=lambda: holder["task"],
            set_knowledge_build_task=lambda task: holder.__setitem__("task", task),
            trigger="webui_one_shot",
            force=True,
            allow_one_shot_disabled=True,
        )
        assert started == {"started": True, "result": "started", "reasons": ["force_start"]}
        assert config.personification_plugin_knowledge_build_enabled is False
        state = await store.load_build_state()
        assert state["control"]["enabled"] is False

        duplicate = await builder.maybe_start_plugin_knowledge_builder(
            plugin_config=config,
            tool_caller=object(),
            knowledge_store=store,
            logger=SimpleNamespace(warning=lambda *_args: None),
            get_knowledge_build_task=lambda: holder["task"],
            set_knowledge_build_task=lambda task: holder.__setitem__("task", task),
            trigger="webui_one_shot",
            force=True,
            allow_one_shot_disabled=True,
        )
        assert duplicate["result"] == "already_running"
        holder["task"].cancel()
        try:
            await holder["task"]
        except asyncio.CancelledError:
            pass

    asyncio.run(_scenario())


def test_unexpected_builder_failure_clears_stale_current_and_marks_plugin_failed(monkeypatch) -> None:  # noqa: ANN001
    builder = load_personification_module("plugin.personification.core.knowledge_builder")

    class _Store:
        def __init__(self) -> None:
            self.state = {
                "plugins": {"demo": {"status": "pending", "retry_count": 0}},
                "current": {"plugin_name": "demo", "phase": "snapshot"},
            }

        async def load_build_state(self):  # noqa: ANN201
            return self.state

        async def save_build_state(self, value):  # noqa: ANN001, ANN201
            self.state = value

    store = _Store()
    monkeypatch.setattr(
        builder.nonebot,
        "get_loaded_plugins",
        lambda: (_ for _ in ()).throw(RuntimeError("access_token=raw-secret")),
    )
    logger = SimpleNamespace(
        warning=lambda *_args: None,
        info=lambda *_args: None,
    )

    with pytest.raises(RuntimeError):
        asyncio.run(
            builder.build_plugin_knowledge_async(
                SimpleNamespace(),
                object(),
                store,
                logger,
            )
        )

    assert store.state["current"] == {}
    assert store.state["plugins"]["demo"]["status"] == "failed"
    assert store.state["plugins"]["demo"]["retry_count"] == 1
    assert "raw-secret" not in store.state["plugins"]["demo"]["error_message"]
