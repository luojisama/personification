from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from ._loader import load_personification_module

routes = load_personification_module("plugin.personification.webui.routes.quality_routes")
lab_mod = load_personification_module("plugin.personification.core.quality_lab")


def test_quality_routes_admin_gate_and_case_preview(tmp_path):
    config = SimpleNamespace(personification_api_pools=[{"api_type": "openai", "model": "fixture", "api_url": "http://localhost:1", "api_key": "fixture-key"}])
    app = FastAPI()
    app.include_router(routes.build_quality_router(runtime=SimpleNamespace(plugin_config=config), service=lab_mod.QualityLab(tmp_path)))
    with TestClient(app) as client:
        assert client.get("/api/v2/quality").status_code in {401, 403}
        app.dependency_overrides[routes.require_admin] = lambda: SimpleNamespace(qq="fixture-admin", device_id="fixture-device")
        data = client.get("/api/v2/quality").json()
        assert data["providers"] == [{"id": "0", "model": "fixture", "api_type": "openai"}]
        case = {"surface": "group", "persona": "测试人格", "events": [{"kind": "mention", "text": "你好"}]}
        assert client.post("/api/v2/quality/cases", json={"case": case}).status_code == 400
        saved = client.post("/api/v2/quality/cases", json={"case": case, "redact_confirmed": True}).json()
        request = {"case_ids": [saved["id"]], "provider_id": "0", "request_budget": 8}
        preview = client.post("/api/v2/quality/preview", json=request)
        assert preview.status_code == 200 and preview.json()["candidate_count"] == 3
        # Missing explicit confirmation never launches a worker/model call.
        assert client.post("/api/v2/quality/jobs", json=request).status_code == 400
        assert client.get("/api/v2/quality/jobs/missing").status_code == 400


def test_quality_business_error_body_never_echoes_exception(tmp_path, monkeypatch):
    lab = lab_mod.QualityLab(tmp_path)
    def failed(*_args):
        raise lab_mod.QualityLabError("private-fixture-secret")
    monkeypatch.setattr(lab, "preview", failed)
    app = FastAPI()
    app.include_router(routes.build_quality_router(runtime=SimpleNamespace(plugin_config=SimpleNamespace()), service=lab))
    app.dependency_overrides[routes.require_admin] = lambda: SimpleNamespace()
    with TestClient(app) as client:
        response = client.post("/api/v2/quality/preview", json={})
        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "quality_request_invalid"
        assert "private-fixture-secret" not in response.text


def test_bot_identity_keeps_telegram_out_of_qq_apis_and_ids(monkeypatch):
    import asyncio
    services = load_personification_module("plugin.personification.webui.v2_services")
    monkeypatch.setattr(services, "_BOT_IDENTITY_CACHE", {})
    class TelegramBot:
        platform = "telegram"
        self_id = "12345"
        username = "test_tg"
        adapter = SimpleNamespace(personification_managed=True)
        async def get_login_info(self):
            raise AssertionError("Telegram must not call QQ API")
    class QQBot:
        self_id = "12345"
        async def get_login_info(self):
            return {"nickname": "QQ"}
    rows = asyncio.run(services.list_bot_identities(SimpleNamespace(get_bots=lambda: {"tg": TelegramBot(), "qq": QQBot()})))
    assert [r["bot_id"] for r in rows] == ["telegram:12345", "12345"]
    assert rows[0]["avatar_url"] is None and rows[0]["source"] == "managed"
    assert rows[0]["capabilities"]["qzone"] is False
    assert services.normalized_bot_selector("telegram:12345") == "telegram:12345"
