from pathlib import Path


def test_skill_page_contains_managed_mcp_workflow() -> None:
    root = Path(__file__).resolve().parents[1]
    core = (root / "webui" / "static" / "app-core.js").read_text(encoding="utf-8")
    tools = (root / "webui" / "static" / "app-tools.js").read_text(encoding="utf-8")

    assert 'api("/mcp/sources")' in core
    assert 'api("/mcp/installations")' in core
    assert "renderMcpRegistry()" in tools
    assert "confirm_execution:true" in tools
    assert "package_digest:selected.digest" in tools
    assert "data-mcp-secret" in tools
    assert "confirm_side_effect" in tools
    assert "__personificationMcpManagementEvents" in tools
