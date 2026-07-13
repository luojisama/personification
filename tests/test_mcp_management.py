from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from ._loader import load_personification_module


FAKE_SERVER = Path(__file__).resolve().parent / "fixtures" / "fake_mcp_server.py"


def _init_store(tmp_path: Path, monkeypatch) -> None:
    paths = load_personification_module("plugin.personification.core.paths")
    data_store = load_personification_module("plugin.personification.core.data_store")
    monkeypatch.setattr(paths, "get_data_dir", lambda _cfg=None: tmp_path)
    data_store.init_data_store(SimpleNamespace(personification_data_dir=str(tmp_path)))


def test_stdio_client_uses_json_lines_and_paginates() -> None:
    compat = load_personification_module("plugin.personification.skill_runtime.mcp_compat")

    async def run():
        async with compat.McpStdioClient(
            command=sys.executable,
            args=[str(FAKE_SERVER)],
            env=dict(),
            cwd=str(FAKE_SERVER.parent),
            timeout=5,
        ) as client:
            tools = await client.list_tools()
            result = await client.call_tool("read_demo", {"query": "hello"})
            return client.protocol_version, tools, result

    version, tools, result = asyncio.run(run())
    assert version == "2025-06-18"
    assert [tool["name"] for tool in tools] == ["read_demo", "write_demo"]
    assert "called:read_demo" in result


def test_build_launch_plan_pins_npm_and_separates_secret(monkeypatch) -> None:
    management = load_personification_module("plugin.personification.core.mcp_management")
    monkeypatch.setattr(management.shutil, "which", lambda name: f"/bin/{name}" if name == "npx" else None)
    plan = management.build_launch_plan(
        {
            "registryType": "npm",
            "identifier": "@demo/server",
            "version": "1.2.3",
            "transport": {"type": "stdio"},
            "environmentVariables": [
                {"name": "TOKEN", "isRequired": True, "isSecret": True},
                {"name": "MODE", "default": "safe"},
            ],
            "packageArguments": [{"type": "named", "name": "scope", "isRequired": True}],
        },
        {"TOKEN": "secret", "scope": "public"},
    )
    assert plan["command"] == "/bin/npx"
    assert plan["args"] == ["--yes", "@demo/server@1.2.3", "--scope=public"]
    assert plan["env"] == {"MODE": "safe"}
    assert plan["secrets"] == {"TOKEN": "secret"}
    serialized_public = json.dumps({key: value for key, value in plan.items() if key != "secrets"})
    assert '"TOKEN": "secret"' not in serialized_public

    with pytest.raises(ValueError):
        management.build_launch_plan(
            {"registryType": "npm", "identifier": "demo", "version": "latest", "transport": {"type": "stdio"}},
            {},
        )


def test_build_launch_plan_rejects_secret_argv_and_unsafe_package_metadata() -> None:
    management = load_personification_module("plugin.personification.core.mcp_management")
    base = {
        "registryType": "npm",
        "identifier": "demo-server",
        "version": "1.2.3",
        "transport": {"type": "stdio"},
    }

    with pytest.raises(ValueError, match="Secret package arguments"):
        management.build_launch_plan(
            {**base, "packageArguments": [{"type": "named", "name": "--token", "isSecret": True}]},
            {"--token": "secret"},
        )
    for override in (
        {"identifier": "--help"},
        {"registryBaseUrl": "https://packages.example.com"},
        {"runtimeArguments": [{"type": "positional", "value": "--inspect"}]},
        {"version": "1.x"},
    ):
        with pytest.raises(ValueError):
            management.build_launch_plan({**base, **override}, {})


def test_publisher_read_only_annotation_never_auto_enables_tool() -> None:
    management = load_personification_module("plugin.personification.core.mcp_management")
    policy = management._tool_policy(
        "install-1",
        "mcp_demo_",
        {
            "name": "read_demo",
            "description": "read",
            "inputSchema": {"type": "object", "properties": {}},
            "annotations": {"readOnlyHint": True},
        },
    )
    assert policy["publisher_read_only"] is True
    assert policy["enabled"] is False
    assert policy["side_effect"] == "unknown"
    assert policy["risk_level"] == "review"


def test_secret_store_is_separate_and_deletable(tmp_path: Path) -> None:
    management = load_personification_module("plugin.personification.core.mcp_management")
    store = management.McpSecretStore(SimpleNamespace(personification_data_dir=str(tmp_path), personification_mcp_secret_file=""))
    store.set("install-1", {"TOKEN": "top-secret"})
    assert store.get("install-1") == {"TOKEN": "top-secret"}
    assert "top-secret" in store.path.read_text(encoding="utf-8")
    store.delete("install-1")
    assert store.get("install-1") == {}


def test_secret_store_fails_closed_on_corruption(tmp_path: Path) -> None:
    management = load_personification_module("plugin.personification.core.mcp_management")
    store = management.McpSecretStore(SimpleNamespace(personification_data_dir=str(tmp_path), personification_mcp_secret_file=""))
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text("not-json", encoding="utf-8")
    with pytest.raises(RuntimeError, match="unreadable"):
        store.get("install-1")
    with pytest.raises(RuntimeError, match="unreadable"):
        store.set("install-1", {"TOKEN": "must-not-overwrite"})
    assert store.path.read_text(encoding="utf-8") == "not-json"


def test_stdio_request_timeout_is_absolute_during_notifications() -> None:
    compat = load_personification_module("plugin.personification.skill_runtime.mcp_compat")
    client = compat.McpStdioClient(command="unused", args=[], env={}, cwd=None, timeout=1)
    client.timeout = 0.03

    async def fake_write(_payload):
        return None

    async def fake_read():
        await asyncio.sleep(0)
        return {"jsonrpc": "2.0", "method": "notifications/progress"}

    client._write_message = fake_write
    client._read_message = fake_read
    with pytest.raises((TimeoutError, asyncio.TimeoutError)):
        asyncio.run(client.request("tools/list", {}))


def test_package_digest_binds_full_registry_metadata() -> None:
    management = load_personification_module("plugin.personification.core.mcp_management")
    package = {
        "registryType": "npm",
        "identifier": "demo-server",
        "version": "1.0.0",
        "transport": {"type": "stdio"},
        "packageArguments": [{"type": "named", "name": "--mode", "default": "safe"}],
    }
    original = management._package_digest(package)
    changed = management._package_digest({**package, "packageArguments": [{"type": "named", "name": "--mode", "default": "unsafe"}]})
    assert original != changed


def test_runtime_manager_registers_only_enabled_policy(tmp_path: Path, monkeypatch) -> None:
    _init_store(tmp_path, monkeypatch)
    management = load_personification_module("plugin.personification.core.mcp_management")
    registry_mod = load_personification_module("plugin.personification.agent.tool_registry")
    monkeypatch.setattr(management, "get_data_dir", lambda _cfg=None: tmp_path)
    config = SimpleNamespace(
        personification_data_dir=str(tmp_path),
        personification_mcp_secret_file="",
        personification_skill_mcp_timeout=5,
        personification_mcp_registry_sources=[],
        personification_mcp_registry_timeout=5,
    )
    registry = registry_mod.ToolRegistry()
    runtime = SimpleNamespace(plugin_config=config, runtime_bundle=SimpleNamespace(tool_registry=registry))
    manager = management.McpRuntimeManager(runtime, registry)
    item = {
        "installation_id": "install-1",
        "source_id": "official",
        "source_url": management.OFFICIAL_MCP_REGISTRY,
        "server_name": "io.example/demo",
        "server_title": "Demo",
        "server_version": "1.0.0",
        "package_type": "pypi",
        "package_identifier": "demo",
        "command": sys.executable,
        "args": [str(FAKE_SERVER)],
        "env": {},
        "secret_names": [],
        "name_prefix": "mcp_demo_",
        "metadata": {},
        "created_by": "10001",
    }
    tools = [
        {"remote_name": "read_demo", "registered_name": "mcp_demo_read_demo", "description": "read", "parameters": {"type": "object", "properties": {"query": {"type": "string"}}}, "enabled": True, "risk_level": "low", "side_effect": "none", "publisher_read_only": True},
        {"remote_name": "write_demo", "registered_name": "mcp_demo_write_demo", "description": "write", "parameters": {"type": "object", "properties": {}}, "enabled": False, "risk_level": "admin", "side_effect": "unknown", "publisher_read_only": False},
    ]
    manager.store.save_installation(item, tools)

    async def run():
        await manager.activate("install-1")
        read_tool = registry.get("mcp_demo_read_demo")
        assert read_tool is not None
        assert registry.get("mcp_demo_write_demo") is None
        result = await read_tool.handler(query="hello")
        await manager.shutdown()
        return result

    assert "called:read_demo" in asyncio.run(run())


def test_runtime_manager_does_not_start_server_without_approved_tools(tmp_path: Path, monkeypatch) -> None:
    _init_store(tmp_path, monkeypatch)
    management = load_personification_module("plugin.personification.core.mcp_management")
    registry_mod = load_personification_module("plugin.personification.agent.tool_registry")
    config = SimpleNamespace(personification_data_dir=str(tmp_path), personification_mcp_secret_file="")
    registry = registry_mod.ToolRegistry()
    runtime = SimpleNamespace(plugin_config=config, runtime_bundle=SimpleNamespace(tool_registry=registry))
    manager = management.McpRuntimeManager(runtime, registry)
    item = {
        "installation_id": "install-ready",
        "source_id": "official",
        "source_url": management.OFFICIAL_MCP_REGISTRY,
        "server_name": "io.example/ready",
        "server_title": "Ready",
        "server_version": "1.0.0",
        "package_type": "npm",
        "package_identifier": "ready-server",
        "command": "must-not-run",
        "args": [],
        "env": {},
        "secret_names": [],
        "name_prefix": "mcp_ready_",
        "metadata": {},
        "created_by": "10001",
    }
    manager.store.save_installation(
        item,
        [{
            "remote_name": "read_demo",
            "registered_name": "mcp_ready_read_demo",
            "description": "read",
            "parameters": {"type": "object", "properties": {}},
            "enabled": False,
            "risk_level": "review",
            "side_effect": "unknown",
            "publisher_read_only": True,
        }],
    )
    monkeypatch.setattr(manager, "_client", lambda _item: (_ for _ in ()).throw(AssertionError("process started")))
    asyncio.run(manager.activate("install-ready"))
    assert manager.store.get_installation("install-ready")["observed_status"] == "ready"
    assert manager._clients == {}


def test_registry_sources_include_official_and_https_custom() -> None:
    management = load_personification_module("plugin.personification.core.mcp_management")
    sources = management.mcp_registry_sources(
        SimpleNamespace(
            personification_mcp_registry_sources=[
                {"name": "trusted", "url": "https://registry.example.com/"},
                {"name": "unsafe", "url": "http://localhost:8080"},
                {"name": "credentials", "url": "https://user:pass@registry.example.com"},
                {"name": "query", "url": "https://registry.example.com?token=secret"},
            ]
        )
    )
    assert sources[0]["id"] == "official"
    assert len(sources) == 2
    assert sources[1]["url"] == "https://registry.example.com"


def test_store_requires_explicit_risk_approval_to_enable_tool(tmp_path: Path, monkeypatch) -> None:
    _init_store(tmp_path, monkeypatch)
    management = load_personification_module("plugin.personification.core.mcp_management")
    store = management.McpStore()
    item = {
        "installation_id": "install-approval",
        "source_id": "official",
        "source_url": management.OFFICIAL_MCP_REGISTRY,
        "server_name": "io.example/approval",
        "server_title": "Approval",
        "server_version": "1.0.0",
        "package_type": "npm",
        "package_identifier": "approval-server",
        "command": "npx",
        "args": ["--yes", "approval-server@1.0.0"],
        "env": {},
        "secret_names": [],
        "name_prefix": "mcp_approval_",
        "metadata": {},
        "created_by": "10001",
    }
    store.save_installation(
        item,
        [{
            "remote_name": "read_demo",
            "registered_name": "mcp_approval_read_demo",
            "description": "read",
            "parameters": {"type": "object", "properties": {}},
            "enabled": False,
            "risk_level": "review",
            "side_effect": "unknown",
            "publisher_read_only": True,
        }],
    )
    with pytest.raises(ValueError, match="explicit risk approval"):
        store.set_tool_enabled("install-approval", "read_demo", True)
    store.set_tool_enabled("install-approval", "read_demo", True, approve_side_effect=True)
    policy = store.tools("install-approval")[0]
    assert policy["enabled"] is True
    assert policy["side_effect"] == "external"


def test_runtime_manager_serializes_lifecycle_operations(tmp_path: Path, monkeypatch) -> None:
    management = load_personification_module("plugin.personification.core.mcp_management")
    registry_mod = load_personification_module("plugin.personification.agent.tool_registry")
    config = SimpleNamespace(personification_data_dir=str(tmp_path), personification_mcp_secret_file="")
    registry = registry_mod.ToolRegistry()
    runtime = SimpleNamespace(plugin_config=config, runtime_bundle=SimpleNamespace(tool_registry=registry))
    manager = management.McpRuntimeManager(runtime, registry)
    active = 0
    max_active = 0

    manager.store.set_installation_enabled = lambda *_args, **_kwargs: None
    manager.public_installation = lambda installation_id: {"installation_id": installation_id}

    async def fake_activate(_installation_id: str) -> None:
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.02)
        active -= 1

    manager._activate_unlocked = fake_activate

    async def run() -> None:
        await asyncio.gather(
            manager.toggle_installation("one", True),
            manager.toggle_installation("two", True),
        )

    asyncio.run(run())
    assert max_active == 1


def test_get_mcp_manager_reuses_manager_for_same_registry(tmp_path: Path) -> None:
    management = load_personification_module("plugin.personification.core.mcp_management")
    registry_mod = load_personification_module("plugin.personification.agent.tool_registry")
    registry = registry_mod.ToolRegistry()
    config = SimpleNamespace(personification_data_dir=str(tmp_path), personification_mcp_secret_file="")
    runtime_one = SimpleNamespace(plugin_config=config, runtime_bundle=SimpleNamespace(tool_registry=registry))
    runtime_two = SimpleNamespace(plugin_config=config, runtime_bundle=SimpleNamespace(tool_registry=registry))
    first = management.get_mcp_manager(runtime_one)
    second = management.get_mcp_manager(runtime_two)
    assert first is second
    assert second.runtime is runtime_two


def test_custom_reload_replaces_stale_sources_and_preserves_managed_mcp(tmp_path: Path, monkeypatch) -> None:
    reload_mod = load_personification_module("plugin.personification.skill_runtime.reload")
    registry_mod = load_personification_module("plugin.personification.agent.tool_registry")
    registry = registry_mod.ToolRegistry()

    async def noop(**_kwargs):
        return ""

    registry.register(registry_mod.AgentTool(name="base", description="base", parameters={}, handler=noop, metadata={"source_kind": "bundled"}))
    registry.register(registry_mod.AgentTool(name="stale", description="stale", parameters={}, handler=noop, metadata={"source_kind": "local"}))
    registry.register(registry_mod.AgentTool(name="managed", description="managed", parameters={}, handler=noop, metadata={"source_kind": "mcp_managed"}))

    async def fake_load(_root, candidate, _logger, **_kwargs):
        candidate.register(registry_mod.AgentTool(name="fresh", description="fresh", parameters={}, handler=noop, metadata={"source_kind": "generated"}))

    monkeypatch.setattr(reload_mod, "load_custom_skills", fake_load)
    logger = SimpleNamespace(info=lambda *_a, **_k: None, warning=lambda *_a, **_k: None)
    config = SimpleNamespace(personification_data_dir=str(tmp_path), personification_skills_path="")
    bundle = SimpleNamespace(
        tool_registry=registry,
        reply_processor_deps=SimpleNamespace(runtime=SimpleNamespace(agent_tool_caller=None, vision_caller=None, knowledge_store=None)),
        scheduler=None,
    )
    runtime = SimpleNamespace(plugin_config=config, logger=logger, get_bots=lambda: {}, runtime_bundle=bundle)
    asyncio.run(reload_mod.reload_custom_skills_for_runtime(runtime))
    assert {tool.name for tool in registry.all()} == {"base", "managed", "fresh"}


def test_full_runtime_reload_is_serial_and_restores_mcp(tmp_path: Path, monkeypatch) -> None:
    reload_mod = load_personification_module("plugin.personification.skill_runtime.reload")
    management = load_personification_module("plugin.personification.core.mcp_management")
    registry_mod = load_personification_module("plugin.personification.agent.tool_registry")
    registry = registry_mod.ToolRegistry()
    events: list[str] = []
    active = 0
    max_active = 0

    def reload_base():
        events.append("base")

    async def reload_custom(_runtime):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        events.append("custom")
        await asyncio.sleep(0.01)
        active -= 1
        return 2

    class FakeManager:
        async def reload(self):
            events.append("mcp")
            return {"running": 1, "ready": 0, "failed": 0}

    monkeypatch.setattr(reload_mod, "reload_custom_skills_for_runtime", reload_custom)
    monkeypatch.setattr(management, "get_mcp_manager", lambda _runtime: FakeManager())
    bundle = SimpleNamespace(tool_registry=registry, reload_runtime_services=reload_base)
    runtime = SimpleNamespace(
        plugin_config=SimpleNamespace(personification_data_dir=str(tmp_path)),
        logger=SimpleNamespace(),
        get_bots=lambda: {},
        runtime_bundle=bundle,
    )

    async def run():
        return await asyncio.gather(
            reload_mod.reload_all_runtime_services(runtime),
            reload_mod.reload_all_runtime_services(runtime),
        )

    results = asyncio.run(run())
    assert max_active == 1
    assert events == ["base", "custom", "mcp", "base", "custom", "mcp"]
    assert all(result["mcp"]["failed"] == 0 for result in results)
