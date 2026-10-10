from __future__ import annotations

import asyncio
from copy import deepcopy
from types import SimpleNamespace

import pytest

from ._loader import load_personification_module

memory = load_personification_module("plugin.personification.core.memory_context")
identity = load_personification_module("plugin.personification.core.llm_context")
utils = load_personification_module("plugin.personification.utils")


@pytest.mark.parametrize("history_available", [True, False])
def test_group_context_projects_final_sources_once(monkeypatch, history_available):
    messages = [
        {"role": "system", "content": "旧摘要", "is_summary": True, "timestamp": 1},
        {"role": "user", "message_id": "session", "user_id": "alice", "content": "昨天的话", "timestamp": 3},
        {"role": "user", "message_id": "current", "user_id": "alice", "content": [{"type": "text", "text": "还在吗"}], "timestamp": 6},
    ]
    original = deepcopy(messages)
    rows = [
        {"message_id": "session", "user_id": "alice", "content": "重复记录", "time": 3},
        {"message_id": "hidden", "user_id": "blocked", "content": "不可见", "time": 4},
        {"message_id": "bot", "user_id": "bot", "source_kind": "bot_reply", "content": "在呢", "time": 5},
    ]
    quote = {"message_id": "quoted", "user_id": "bob", "content": "引用资料", "time": 2, "bot_id": "bot", "platform": "onebot"}
    calls = []

    def history(group_id, **kwargs):
        assert group_id == "group" and kwargs["bot_id"] == "bot" and kwargs["platform"] == "onebot"
        if not history_available:
            raise OSError("isolated history failure")
        return deepcopy(rows)

    async def filter_history(items, **kwargs):
        assert kwargs["bot_self_id"] == "bot"
        return [item for item in items if item["user_id"] != "blocked"], []

    project = memory.project_history

    def observed_projection(items, timezone):
        calls.append(len(items))
        return project(items, timezone)

    monkeypatch.setattr(utils, "get_recent_group_msgs", history)
    monkeypatch.setattr(utils, "get_group_msg_by_message_id", lambda *args: deepcopy(quote))
    monkeypatch.setattr(memory, "project_history", observed_projection)
    token = identity.set_llm_context(platform="onebot", bot_id="bot")
    try:
        result = asyncio.run(memory.prepare_memory_context(
            runtime=SimpleNamespace(plugin_config=SimpleNamespace(personification_memory_enabled=False),
                                    user_policy_gate=SimpleNamespace(filter_context_messages=filter_history)),
            event=SimpleNamespace(group_id="group", user_id="alice", reply_to_message_id="quoted"),
            bot=SimpleNamespace(self_id="bot"), messages=messages,
        ))
    finally:
        identity.reset_llm_context(token)

    expected_ids = [None, "quoted", "session"] + (["bot"] if history_available else []) + ["current"]
    assert [item.get("message_id") for item in result.history] == expected_ids
    assert calls == [len(expected_ids)]  # No discarded projection of the original session.
    assert messages == original
    assert result.history[0]["role"] == "user" and '"source": "history_summary"' in result.history[0]["content"]
    assert result.history[1]["_evidence_table"] == "group_messages"
    assert result.history[-1]["content"][1:] == original[-1]["content"]
    if history_available:
        assert result.history[-2]["role"] == "assistant"
    else:
        assert result.diagnostics["group_history_status"] == "unavailable"


@pytest.mark.parametrize("context_enabled, has_store", [(False, False), (True, False), (True, True)])
def test_private_early_return_keeps_projected_history(monkeypatch, context_enabled, has_store):
    def forbidden(*args, **kwargs):
        raise AssertionError("private history requested group data")

    monkeypatch.setattr(utils, "get_recent_group_msgs", forbidden)
    messages = [{"role": "assistant", "content": "先前回复", "timestamp": 2}]
    runtime = SimpleNamespace(plugin_config=SimpleNamespace(personification_memory_context_enabled=context_enabled),
                              memory_store=object() if has_store else None)
    token = identity.set_llm_context()
    try:
        result = asyncio.run(memory.prepare_memory_context(
            runtime=runtime, event=SimpleNamespace(), bot=SimpleNamespace(), messages=messages,
        ))
    finally:
        identity.reset_llm_context(token)
    assert result.history == memory.project_history(messages)
    assert result.status == ("disabled" if not context_enabled else "scope_filtered" if has_store else "unavailable")
