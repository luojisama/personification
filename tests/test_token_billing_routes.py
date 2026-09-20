from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from ._loader import load_personification_module


@pytest.fixture
def billing_client(tmp_path: Path, monkeypatch):
    data_store = load_personification_module("plugin.personification.core.data_store")
    paths = load_personification_module("plugin.personification.core.paths")
    monkeypatch.setattr(paths, "get_data_dir", lambda _cfg=None: tmp_path)
    cfg = SimpleNamespace(
        personification_data_dir=str(tmp_path),
        personification_api_pools=[{
            "provider_id": "route-safe", "name": "安全路由", "api_type": "openai",
            "api_key": "must-never-leak", "base_url": "https://secret.invalid/v1",
            "models": [{"model_id": "model-safe", "display_name": "Safe"}],
        }],
    )
    data_store.init_data_store(cfg)
    app_module = load_personification_module("plugin.personification.webui.app")
    app_module.set_runtime_context(
        plugin_config=cfg, superusers={"10001"},
        get_bots=lambda: {},
        logger=SimpleNamespace(info=lambda *_a, **_k: None, warning=lambda *_a, **_k: None),
        runtime_bundle=SimpleNamespace(memory_store=None, profile_service=None),
    )
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI()
    app.include_router(app_module.build_router())
    return TestClient(app), app_module


def _login(client, app_module) -> str:
    sent: list[dict] = []

    class Bot:
        async def call_api(self, _name: str, **kwargs):
            sent.append(kwargs)
            return {"message_id": 1}

    app_module.get_runtime_context().get_bots = lambda: {"1": Bot()}
    response = client.post("/personification/api/auth/login", json={"qq": "10001"})
    assert response.status_code == 200
    code = re.search(r"\b(\d{6})\b", str(sent[-1]["message"])).group(1)
    verified = client.post(
        "/personification/api/auth/verify",
        json={"qq": "10001", "code": code, "device_label": "billing-test"},
    )
    assert verified.status_code == 200
    return str(client.cookies.get("personification_webui_csrf") or "")


def test_billing_routes_require_auth_and_csrf(billing_client) -> None:
    client, app_module = billing_client
    assert client.get("/personification/api/v2/metrics/usage").status_code == 401
    csrf = _login(client, app_module)
    body = {"route_id": "route-safe", "model": "model-safe", "currency": "USD",
            "input_per_million": "1", "output_per_million": "2"}
    assert client.post("/personification/api/v2/metrics/prices", json=body).status_code == 403
    created = client.post(
        "/personification/api/v2/metrics/prices", json=body,
        headers={"X-Personification-CSRF": csrf},
    )
    assert created.status_code == 201, created.text


def test_price_routes_project_no_secrets_and_support_preview(billing_client) -> None:
    client, app_module = billing_client
    csrf = _login(client, app_module)
    routes = client.get("/personification/api/v2/metrics/price-routes")
    assert routes.status_code == 200
    assert routes.json() == {"items": [{
        "route_id": "route-safe", "name": "安全路由",
        "models": ["model-safe"],
    }]}
    assert "must-never-leak" not in routes.text
    assert "secret.invalid" not in routes.text

    created = client.post(
        "/personification/api/v2/metrics/prices",
        json={
            "route_id": "route-safe", "model": "model-safe", "currency": "USD",
            "input_per_million": "1", "output_per_million": "2",
            "cache_read_per_million": "0.1", "cache_create_per_million": "1.25",
            "effective_from": 0,
        },
        headers={"X-Personification-CSRF": csrf},
    )
    assert created.status_code == 201, created.text
    version_id = created.json()["version_id"]
    assert created.json()["route_id"] == "route-safe"

    ledger = load_personification_module("plugin.personification.core.token_ledger")
    ledger.record_llm_call(
        event_id="api-event", route_id="route-safe", provider="openai", model="model-safe",
        prompt_tokens=1000, completion_tokens=100, cache_read_tokens=100,
        cache_create_tokens=0, bot_id="bot-a", group_id="group-a",
    )
    usage = client.get("/personification/api/v2/metrics/usage?window=all&bot_id=bot-a")
    assert usage.status_code == 200
    assert usage.json()["recent_events"][0]["event_id"] == "api-event"
    assert usage.json()["costs"][0]["currency"] == "USD"

    listed = client.get("/personification/api/v2/metrics/prices")
    assert listed.status_code == 200
    assert listed.json()["items"][0]["version_id"] == version_id
    assert listed.json()["items"][0]["route_id"] == "route-safe"
    preview = client.post(
        "/personification/api/v2/metrics/reprice-preview",
        json={"version_id": version_id, "event_ids": ["api-event"]},
        headers={"X-Personification-CSRF": csrf},
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["persisted"] is False
    assert preview.json()["event_count"] == 1
    assert preview.json()["costs"][0]["currency"] == "USD"
    assert preview.json()["unpriced_call_count"] == 0
    assert preview.json()["incomplete_priced_call_count"] == 0
