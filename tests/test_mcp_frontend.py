from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "webui" / "static"


def _source(filename: str) -> str:
    return (STATIC / filename).read_text(encoding="utf-8")


def test_mcp_is_a_real_lazy_view_next_to_skill_management() -> None:
    core = _source("app-core.js")
    app = (ROOT / "webui" / "app.py").read_text(encoding="utf-8")

    assert 'mcp:"app-mcp.js"' in core
    assert "${navItem('skills','Skill 管理','plug')}\n        ${navItem('mcp','MCP 管理','server')}" in core
    assert 'mcp:"MCP 管理"' in core
    assert 'mcp: "正在读取 MCP Registry 与运行状态..."' in core
    assert '} else if (view === "mcp") {' in core
    assert 'if (state.view === "mcp") return renderMcp();' in core
    assert 'state.view==="mcp"&&nextView!=="mcp"' in core
    assert '"app-mcp.js",' in app


def test_skill_page_has_no_managed_mcp_state_or_handlers() -> None:
    core = _source("app-core.js")
    tools = _source("app-tools.js")

    skill_branch = core.split('} else if (view === "skills") {', 1)[1].split(
        '} else if (view === "mcp") {', 1
    )[0]
    assert 'api("/mcp/sources")' not in skill_branch
    assert 'api("/mcp/installations")' not in skill_branch
    assert "renderMcpRegistry" not in tools
    assert "renderMcpInstallations" not in tools
    assert "loadMcpInstallations" not in tools
    assert "data-mcp-" not in tools
    assert "__personificationMcp" not in tools
    assert 'api("/mcp/' not in tools

    assert "renderRemoteSkillSources()" in tools
    assert "renderLegacyMcpTools()" in tools
    assert "Legacy Skill MCP 工具" in tools
    assert 'Number(s.mcp_tools || 0)' in tools


def test_registry_discovery_is_name_scoped_and_cursor_safe() -> None:
    source = _source("app-mcp.js")

    assert "Registry discovery" in source
    assert 'placeholder="按 Server 名称搜索"' in source
    assert "不声称支持能力全文搜索" in source
    assert "Official" in source
    assert "compatible" in source
    assert "Preview" in source
    for field in (
        "server.name",
        "server.title",
        "server.description",
        "server.status",
        "server.status_message",
        "server.repository",
        "server.website",
        "server.schema",
        "server.stdio_packages",
        "server.remote_count",
    ):
        assert field in source

    assert "function mergeMcpRegistryResults" in source
    assert "params.cursor = state.mcpNextCursor" in source
    assert "state.mcpResults = append" in source
    assert "加载更多" in source
    assert "opaque next cursor 将原样发送" in source
    assert "item.supported === true" in source
    assert "item.unsupported_reason" in source
    assert "requestSourceId" in source
    assert "requestQuery" in source
    assert "requestCursor" in source
    assert 'state.mcpSourceId !== requestSourceId' in source
    assert 'id="mcp-source-select" aria-label="Registry source" ${state.mcpBusy ? "disabled" : ""}' in source
    assert "const safe = safeHttpUrl(raw)" in source
    assert re.search(r"return safe\s*\? `<a href=", source)


def test_runtime_installations_show_distinct_process_and_tool_states() -> None:
    source = _source("app-mcp.js")

    assert "Runtime installations" in source
    for field in (
        "item.run_allowed",
        "item.desired_enabled",
        "item.process_state",
        "item.authorized_count",
        "item.registered_count",
        "item.effective_count",
        "tool.title",
        "tool.remote_name",
        "tool.registered_name",
        "tool.description",
        "tool.inputSchema",
        "tool.outputSchema",
        "tool.annotations",
        "tool.authorized",
        "tool.registered",
        "tool.effective",
    ):
        assert field in source

    assert "允许启动" in source
    assert "停止运行" in source
    assert "授权" in source
    assert "撤销授权" in source
    assert "Server 未运行，当前不可调用" in source
    assert "不会清除 tool 授权" in source
    assert "publisher 声明，untrusted" in source
    assert "该声明未受信任，也不会自动授权" in source


def test_mcp_operations_use_independent_persistent_diagnostic() -> None:
    mcp = _source("app-mcp.js")
    tools = _source("app-tools.js")

    assert '"personification_mcp_operation_result_v1"' in mcp
    assert 'sessionStorage.setItem(_MCP_OPERATION_RESULT_STORAGE_KEY' in mcp
    assert "renderOperationDiagnostic(result" in mcp
    assert "personification_skill_operation_result_v1" not in mcp
    assert "personification_mcp_operation_result_v1" not in tools
    for function_name in (
        "installMcpServer",
        "toggleMcpInstallation",
        "toggleManagedMcpTool",
        "deleteMcpInstallation",
        "reloadMcpRuntime",
    ):
        body = mcp.split(f"async function {function_name}", 1)[1].split("\n}", 1)[0]
        assert "persistMcpOperationResult(" in body
        assert "operationDiagnosticFromError(" in body
        assert ".message" not in body


def test_install_confirmation_is_fresh_and_does_not_invent_paths() -> None:
    source = _source("app-mcp.js")

    assert "function mcpCommandPlan" in source
    assert '["npx", "--yes", identity]' in source
    assert '["uvx", "--from", identity, item.identifier || "unknown"]' in source
    assert "exact package identity" in source
    assert "command token plan" in source
    assert "不伪造本机路径" in source
    assert "已提供，值不显示" in source
    assert "confirm_execution:true" in source
    assert "fresh_fetch:true" in source
    assert "package_digest:String(selected.digest" in source
    assert "defaultIsChoice" in source
    assert 'String(choice) === registryDefault ? "selected"' in source


def test_mcp_events_are_single_registration_and_page_has_no_polling() -> None:
    source = _source("app-mcp.js")

    assert source.count("if (!window.__personificationMcpPageEvents)") == 1
    assert source.count('document.addEventListener("click"') == 1
    assert source.count('document.addEventListener("change"') == 1
    assert source.count('document.addEventListener("keydown"') == 1
    assert "setInterval(" not in source
    assert "setTimeout(" not in source
    assert "function stopMcpViewLifecycle" in source


def test_mcp_external_links_reject_empty_values_before_url_resolution() -> None:
    source = _source("app-mcp.js")

    helper = source.split("function renderMcpExternalLink", 1)[1].split("\n}", 1)[0]
    assert 'const raw = String(url || "").trim()' in helper
    assert 'if (!raw) return ""' in helper
    assert "safeHttpUrl(raw)" in helper
