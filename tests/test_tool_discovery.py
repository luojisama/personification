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
    session.configure_turn(tool_intents=["lookup"])
    all_schemas = registry.openai_schemas()

    first = session.client_schemas(all_schemas)
    first_names = [item["function"]["name"] for item in first]
    assert len(first_names) == 3
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
    session.configure_turn(tool_intents=["lookup"])
    session.client_schemas(registry.openai_schemas())

    result = json.loads(session.search(query="发送外部消息"))

    # A non-action TurnPlan does not even disclose write-capability metadata
    # through the client directory.
    assert result["candidates"] == []
    assert counter["calls"] == 0


def test_hidden_tool_call_is_blocked_until_disclosed() -> None:
    registry = _registry(8)
    session = ToolDisclosureSession(registry, mode="client", core_limit=1)
    session.configure_turn(tool_intents=["lookup"])
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


def test_auto_is_the_default_but_explicit_off_is_preserved() -> None:
    assert resolve_tool_disclosure_mode(None, object()) == "client"
    assert resolve_tool_disclosure_mode("off", object()) == "off"


def test_gemini_client_starts_with_at_most_two_matching_real_schemas() -> None:
    registry = _registry(12)

    class GeminiToolCaller:
        pass

    session = ToolDisclosureSession(registry, mode="client", core_limit=6)
    session.configure_turn(tool_intents=["lookup"], caller=GeminiToolCaller())
    schemas = session.client_schemas(registry.openai_schemas())
    names = [item["function"]["name"] for item in schemas]
    assert names[-1] == TOOL_SEARCH_NAME
    assert len(names) - 1 == 2


def test_search_is_bounded_and_never_evicts_executed_schema() -> None:
    registry = _registry(12)
    session = ToolDisclosureSession(registry, mode="client", core_limit=1, search_limit=4)
    session.configure_turn(tool_intents=["lookup"])
    session.client_schemas(registry.openai_schemas())
    first = json.loads(session.search(query="天气查询"))
    session.mark_executed(first["loaded_next_step"][0])
    session.search(query="天气查询")
    assert len(session.loaded_names) <= 8
    assert first["loaded_next_step"][0] in session.loaded_names


def test_search_reports_stable_budget_exhaustion_when_only_executed_schemas_remain() -> None:
    registry = _registry(10)
    session = ToolDisclosureSession(registry, mode="client", core_limit=2, search_limit=1)
    session.configure_turn(tool_intents=["lookup"])
    session.client_schemas(registry.openai_schemas())
    for name in list(session.loaded_names):
        session.mark_executed(name)
    for index in range(2, 8):
        result = json.loads(session.search(query=f"demo_tool_{index:02d}"))
        assert result["loaded_next_step"] == [f"demo_tool_{index:02d}"]
        session.mark_executed(f"demo_tool_{index:02d}")

    exhausted = json.loads(session.search(query="demo_tool_08"))
    assert exhausted["status"] == "tool_disclosure_budget_exhausted"
    assert exhausted["loaded_next_step"] == []


def test_search_does_not_claim_evicted_names_from_the_same_batch() -> None:
    registry = ToolRegistry()
    for index in range(12):
        registry.register(_tool(f"tool_{index}", f"topic-{index}"))
    session = ToolDisclosureSession(registry, mode="client", core_limit=2, search_limit=1)
    session.configure_turn(tool_intents=["lookup"])
    session.client_schemas(registry.openai_schemas())
    for name in list(session.loaded_names):
        session.mark_executed(name)
    for index in range(2, 7):
        loaded = json.loads(session.search(query=f"topic-{index}"))["loaded_next_step"]
        session.mark_executed(loaded[0])

    assert len(session.executed_names) == 7
    session.search_limit = 4
    batch = json.loads(session.search(query="topic-7 topic-8 topic-9 topic-10"))
    assert len(batch["candidates"]) == 4
    assert len(batch["loaded_next_step"]) == 1
    assert batch["loaded_next_step"][0] in session.loaded_names
