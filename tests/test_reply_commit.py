from __future__ import annotations

import asyncio
from typing import Any

from ._loader import load_personification_module


reply_commit = load_personification_module("plugin.personification.handlers.reply_commit")


class _Executor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def execute(self, action: str, params: dict[str, Any]) -> None:
        self.calls.append((action, params))


def test_commit_gate_is_reentrant_for_same_turn_and_released() -> None:
    async def run() -> None:
        lock = asyncio.Lock()
        state: dict[str, Any] = {"reply_commit_lock": lock}

        await reply_commit.acquire_reply_commit(state)
        await reply_commit.acquire_reply_commit(state)

        assert lock.locked() is True
        reply_commit.release_reply_commit(state)
        assert lock.locked() is False

    asyncio.run(run())


def test_pending_actions_execute_once_and_are_consumed(monkeypatch) -> None:  # noqa: ANN001
    async def run() -> None:
        executor = _Executor()
        actions = [{"type": "send_sticker", "params": {"path": "x.png"}}]
        expression_tools = load_personification_module("plugin.personification.core.qq_expression_tools")
        monkeypatch.setattr(expression_tools, "qq_action_history_text", lambda _action: "[发送了表情包]")

        history = await reply_commit.execute_pending_actions(executor, actions)
        second = await reply_commit.execute_pending_actions(executor, actions)

        assert executor.calls == [("send_sticker", {"path": "x.png"})]
        assert history == ["[发送了表情包]"]
        assert second == []
        assert actions == []

    asyncio.run(run())


def test_pending_actions_record_confirmed_delivery() -> None:
    async def run() -> None:
        executor = _Executor()
        actions = [{"type": "send", "params": {"text": "hello"}}]
        state: dict[str, Any] = {}

        await reply_commit.execute_pending_actions(executor, actions, state=state)

        assert state["reply_delivery_started"] is True
        assert state["reply_delivery_confirmed"] is True

    asyncio.run(run())


def test_pending_action_without_external_send_is_not_confirmed() -> None:
    class _FailedExecutor:
        last_delivery_confirmed = False

        async def execute(self, _action: str, _params: dict[str, Any]) -> str:
            return "发送失败"

    async def run() -> None:
        actions = [{"type": "send_image_url", "params": {"url": ""}}]
        state: dict[str, Any] = {}

        await reply_commit.execute_pending_actions(_FailedExecutor(), actions, state=state)

        assert state["reply_delivery_started"] is True
        assert "reply_delivery_confirmed" not in state

    asyncio.run(run())
