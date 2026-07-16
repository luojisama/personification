from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from ._loader import load_personification_module


def _client(monkeypatch, manager):
    route_mod = load_personification_module("plugin.personification.webui.routes.mcp_routes")
    schemas = load_personification_module("plugin.personification.webui.schemas")
    runtime = SimpleNamespace(plugin_config=SimpleNamespace(personification_mcp_registry_sources=[]))
    monkeypatch.setattr(route_mod, "get_mcp_manager", lambda _runtime: manager)
    monkeypatch.setattr(route_mod.webui_audit_log, "record", lambda **_kwargs: None)
    app = FastAPI()
    app.include_router(route_mod.build_mcp_router(runtime=runtime))
    app.dependency_overrides[route_mod.require_admin] = lambda: schemas.AdminIdentity(
        qq="10001",
        device_id="device-1",
        label="test",
    )
    return TestClient(app)


class _RegistryClient:
    def __init__(self) -> None:
        self.fresh_values: list[bool] = []

    async def detail(self, _source, name: str, *, fresh: bool = False):
        self.fresh_values.append(fresh)
        return {
            "server": {"name": name, "title": "Demo", "status": "active"},
            "packages": [],
            "raw": {"name": name, "packages": []},
        }


class _Manager:
    def __init__(self) -> None:
        self.registry_client = _RegistryClient()
        self.refreshed = 0
        self.current = {
            "installation_id": "install-1",
            "desired_enabled": False,
            "process_state": "stopped",
            "authorized_count": 1,
            "registered_count": 0,
            "effective_count": 0,
            "tools": [{"remote_name": "read_demo", "authorized": True}],
        }

    async def refresh_process_states(self) -> int:
        self.refreshed += 1
        return 0

    def list_public(self):
        return [self.current]

    def public_installation(self, _installation_id: str):
        return self.current

    async def install(self, **_kwargs):
        return {**self.current, "tools": []}

    async def toggle_installation(self, _installation_id: str, enabled: bool):
        self.current["desired_enabled"] = enabled
        self.current["process_state"] = "ready" if enabled else "stopped"
        return self.current

    async def toggle_tool(self, _installation_id: str, _remote_name: str, enabled: bool, **_kwargs):
        self.current["tools"][0]["authorized"] = enabled
        return self.current

    async def reload(self):
        return {
            "running": 0,
            "ready": 1,
            "failed": 1,
            "catalog_added": 1,
            "catalog_updated": 2,
            "catalog_removed": 3,
        }


def test_mcp_api_uses_fresh_registry_fetch_and_strict_bool_requests(monkeypatch) -> None:
    manager = _Manager()
    client = _client(monkeypatch, manager)

    detail_response = client.get("/api/mcp/detail", params={"name": "io.example/demo", "fresh": "true"})
    assert detail_response.status_code == 200
    assert manager.registry_client.fresh_values == [True]

    install_response = client.post(
        "/api/mcp/install",
        json={
            "server_name": "io.example/demo",
            "package_digest": "digest",
            "confirm_execution": True,
        },
    )
    assert install_response.status_code == 200
    assert manager.registry_client.fresh_values[-1] is True

    invalid_toggle = client.post("/api/mcp/installations/install-1/toggle", json={"enabled": "false"})
    assert invalid_toggle.status_code == 400
    assert invalid_toggle.json()["detail"]["code"] == "mcp_request_invalid"

    invalid_tool = client.post(
        "/api/mcp/installations/install-1/tools/read_demo/toggle",
        json={"enabled": True, "confirm_side_effect": "true"},
    )
    assert invalid_tool.status_code == 400
    assert invalid_tool.json()["detail"]["phase"] == "validation"


def test_mcp_api_refreshes_process_state_and_returns_operation_diagnostics(monkeypatch) -> None:
    manager = _Manager()
    client = _client(monkeypatch, manager)

    listed = client.get("/api/mcp/installations")
    assert listed.status_code == 200
    assert manager.refreshed == 1

    toggled = client.post("/api/mcp/installations/install-1/toggle", json={"enabled": True})
    assert toggled.status_code == 200
    assert toggled.json()["diagnostic"]["code"] == "mcp_server_toggled"

    tool_toggled = client.post(
        "/api/mcp/installations/install-1/tools/read_demo/toggle",
        json={"enabled": False, "confirm_side_effect": False},
    )
    assert tool_toggled.status_code == 200
    assert tool_toggled.json()["diagnostic"]["code"] == "mcp_tool_toggled"

    reloaded = client.post("/api/mcp/reload")
    assert reloaded.status_code == 200
    report = reloaded.json()["diagnostic"]
    assert report["code"] == "mcp_reload_partial"
    assert report["partial"] is True
    assert report["ok"] is False


def test_mcp_start_failure_marks_partial_without_raw_exception(monkeypatch) -> None:
    manager = _Manager()

    async def fail_after_persist(_installation_id: str, enabled: bool):
        manager.current["desired_enabled"] = enabled
        manager.current["process_state"] = "error"
        raise RuntimeError("raw-process-secret")

    manager.toggle_installation = fail_after_persist
    client = _client(monkeypatch, manager)
    response = client.post("/api/mcp/installations/install-1/toggle", json={"enabled": True})
    assert response.status_code == 500
    report = response.json()["detail"]
    assert report["code"] == "mcp_server_start_partial"
    assert report["partial"] is True
    assert report["operation_id"] == "install-1"
    assert manager.current["desired_enabled"] is True
    assert "raw-process-secret" not in str(report)
