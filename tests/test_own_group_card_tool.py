from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent

from ._loader import load_personification_module


card_tool = load_personification_module("plugin.personification.core.own_group_card_tool")
protocol = load_personification_module("plugin.personification.core.protocol_adapter")
catalog = load_personification_module("plugin.personification.agent.runtime.tool_catalog")
discovery = load_personification_module("plugin.personification.agent.runtime.tool_discovery")


def _event():
    if hasattr(GroupMessageEvent, "model_construct"):
        return GroupMessageEvent.model_construct(self_id=10000, group_id=20001, user_id=10002)
    return GroupMessageEvent.construct(self_id=10000, group_id=20001, user_id=10002)


def test_own_group_card_uses_trusted_scope_and_never_retries_unknown_write(monkeypatch):
    bot = Bot(adapter=object(), self_id="10000")
    adapter = SimpleNamespace(
        get_group_member_info=AsyncMock(return_value=protocol.ProtocolResult(
            "succeeded", "ok", data={"card": "原名片"},
        )),
        set_group_card=AsyncMock(return_value=protocol.ProtocolResult("outcome_unknown", "timeout")),
    )
    monkeypatch.setattr(card_tool, "get_protocol_adapter", lambda *_args, **_kwargs: adapter)
    registry = card_tool.ToolRegistry()
    card_tool.register_own_group_card_tool(registry, bot=bot, event=_event())
    tool = registry.get("update_own_group_card")
    assert tool is not None
    assert set(tool.parameters["properties"]) == {"card"}

    async def call_twice():
        assert json.loads(await tool.handler(card="a\nb"))["code"] == "invalid_card"
        assert json.loads(await tool.handler(card=" 新   群名片 ")) == {
            "ok": False, "status": "outcome_unknown", "code": "timeout",
        }
        assert json.loads(await tool.handler(card="另一个名字"))["code"] == "already_attempted_this_turn"

    asyncio.run(call_twice())
    adapter.get_group_member_info.assert_awaited_once_with(group_id=20001, user_id=10000)
    adapter.set_group_card.assert_awaited_once_with(group_id=20001, user_id=10000, card="新 群名片")


def test_own_group_card_hidden_in_private_chat_and_for_mismatched_bot():
    registry = card_tool.ToolRegistry()
    bot = Bot(adapter=object(), self_id="10000")
    card_tool.register_own_group_card_tool(registry, bot=bot, event=SimpleNamespace(self_id=10000))
    card_tool.register_own_group_card_tool(registry, bot=Bot(adapter=object(), self_id="999"), event=_event())
    assert registry.all() == []


def test_own_group_card_is_disclosed_for_planned_action():
    registry = card_tool.ToolRegistry()
    card_tool.register_own_group_card_tool(
        registry, bot=Bot(adapter=object(), self_id="10000"), event=_event(),
    )
    discovery_session = discovery.ToolDisclosureSession(registry=registry)
    discovery_session.configure_turn(tool_intents=["own_group_card"], speech_act="execute_action")
    schemas = catalog.select_tool_schemas(
        registry, has_images=False, chat_intent="banter", speech_act="execute_action",
    )
    assert "update_own_group_card" in {
        schema["function"]["name"] for schema in discovery_session.client_schemas(schemas)
    }
