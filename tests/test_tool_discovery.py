from __future__ import annotations

import json

from personification.agent.runtime.tool_discovery import (
    MAX_NAMESPACE_TOOLS,
    TOOL_SEARCH_NAME,
    ToolDisclosureSession,
    build_native_tool_search_payload,
    resolve_tool_disclosure_mode,
)
from personification.agent.tool_registry import AgentTool, ToolRegistry


def _tool(name: str, description: str, *, side_effect: str = "none", namespace: str = "demo", counter=None) -> AgentTool:
    async def handler(**_kwargs) -> str:
        if counter is not None:
            counter["calls"] += 1
        return "executed"

    return AgentTool(
        name=name,
        description=description,
        parameters={"type": "object", "properties": {}},
        handler=handler,
        metadata={
            "namespace": namespace,
            "risk_level": "medium" if side_effect != "none" else "low",
            "side_effect": side_effect,
            "permission": "confirmed_user_request" if side_effect != "none" else "runtime_policy",
            "intent_tags": ["lookup"],
        },
    )


def _registry(count: int = 12) -> ToolRegistry:
    registry = ToolRegistry()
    for index in range(count):
        registry.register(_tool(f"demo_tool_{index:02d}", f"演示工具 {index} 天气查询"))
    return registry


def test_client_disclosure_exposes_core_and_search_then_loads_schema() -> None:
    registry = _registry()
    session = ToolDisclosureSession(registry, mode="client", core_limit=3, search_limit=2)
    all_schemas = registry.openai_schemas()

    first = session.client_schemas(all_schemas)
    first_names = [item["function"]["name"] for item in first]
    assert len(first_names) == 4
    assert TOOL_SEARCH_NAME in first_names

    result = json.loads(session.search(query="天气查询"))
    assert result["executed"] is False
    assert len(result["candidates"]) == 2

    second = session.client_schemas(all_schemas)
    second_names = {item["function"]["name"] for item in second}
    assert set(result["loaded_next_step"]) <= second_names


def test_discovery_never_executes_side_effect_tool() -> None:
    counter = {"calls": 0}
    registry = ToolRegistry()
    registry.register(_tool("send_external", "发送外部消息", side_effect="external", counter=counter))
    session = ToolDisclosureSession(registry, mode="client")
    session.client_schemas(registry.openai_schemas())

    result = json.loads(session.search(query="发送外部消息"))

    assert result["candidates"][0]["has_side_effect"] is True
    assert result["candidates"][0]["permission"] == "confirmed_user_request"
    assert counter["calls"] == 0


def test_hidden_tool_call_is_blocked_until_disclosed() -> None:
    registry = _registry(8)
    session = ToolDisclosureSession(registry, mode="client", core_limit=1)
    schemas = registry.openai_schemas()
    visible = session.client_schemas(schemas)
    visible_names = {item["function"]["name"] for item in visible}
    hidden = next(tool.name for tool in registry.all() if tool.name not in visible_names)

    assert session.is_call_allowed(hidden) is False
    session.search(query=hidden)
    session.client_schemas(schemas)
    assert session.is_call_allowed(hidden) is True


def test_native_payload_uses_official_tool_search_and_defer_loading_shape() -> None:
    registry = _registry(MAX_NAMESPACE_TOOLS + 2)
    schemas = registry.openai_schemas()
    payload = build_native_tool_search_payload(
        registry,
        schemas,
        core_names={"demo_tool_00"},
    )

    assert payload[-1] == {"type": "tool_search"}
    namespaces = payload[:-1]
    assert len(namespaces) == 2
    assert all(item["type"] == "namespace" for item in namespaces)
    assert all(len(item["tools"]) <= MAX_NAMESPACE_TOOLS for item in namespaces)
    functions = [tool for item in namespaces for tool in item["tools"]]
    assert next(item for item in functions if item["name"] == "demo_tool_00").get("defer_loading") is None
    assert next(item for item in functions if item["name"] == "demo_tool_01")["defer_loading"] is True
    assert all("function" not in item for item in functions)


def test_native_mode_requires_explicit_caller_capability() -> None:
    class CompatibleCaller:
        supports_responses_tool_search = True

        async def chat_with_deferred_tools(self, *_args, **_kwargs):
            return None

    class CompatibleGatewayWithoutNativeAdapter:
        supports_responses_tool_search = True

    assert resolve_tool_disclosure_mode("auto", CompatibleCaller()) == "native"
    assert resolve_tool_disclosure_mode("auto", CompatibleGatewayWithoutNativeAdapter()) == "client"
    assert resolve_tool_disclosure_mode("native", object()) == "client"
