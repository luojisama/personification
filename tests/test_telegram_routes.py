from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ._loader import load_personification_module

routes = load_personification_module("plugin.personification.webui.routes.connection_routes")
connections = load_personification_module("plugin.personification.core.telegram_connections")
TOKEN = "123456:" + "test_secret_not_for_real_use_12345"
PROXY = "http://test-user:test-password@127.0.0.1:9999"
BASE = "/api/v2/connections"


class Adapter:
    def __init__(self, registry):
        self.registry = registry

    def bot_connect(self, bot):
        self.registry[bot.self_id] = bot

    def bot_disconnect(self, bot):
        self.registry.pop(bot.self_id, None)


class FakeTelegramBot:
    __module__ = "nonebot.adapters.telegram.bot"

    def __init__(self, settings, registry, state):
        self.self_id = settings["token"].split(":")[0]
        self.username = "isolated_fixture_bot"
        self.adapter = Adapter(registry)
        self.state = state

    async def get_me(self):
        if self.state.get("fault"):
            raise RuntimeError("url contains " + TOKEN + " " + PROXY)
        return SimpleNamespace(id=int(self.self_id), username=self.username)

    async def get_webhook_info(self):
        return SimpleNamespace(url=self.state.get("webhook", ""))

    async def delete_webhook(self, **kwargs):
        self.state["deleted"] = kwargs
        self.state["webhook"] = ""


@pytest.fixture
def harness(tmp_path, monkeypatch):
    registry, state = {}, {}
    manager = connections.TelegramConnections(
        tmp_path / "isolated-connections",
        get_bots=lambda: registry,
        driver=lambda: None,
        factory=lambda settings, driver: FakeTelegramBot(settings, registry, state),
    )

    async def poll(ident, bot):
        # Exercise real manager task lifecycle while avoiding adapter/network IO.
        try:
            while ident not in manager.stopping:
                await asyncio.sleep(0.001)
        finally:
            manager.bots.pop(ident, None)
            bot.adapter.bot_disconnect(bot)

    monkeypatch.setattr(manager, "_poll", poll)
    monkeypatch.setattr(routes, "get_connections", lambda config: manager)
    app = FastAPI()
    app.include_router(routes.build_connection_router(runtime=SimpleNamespace(plugin_config=SimpleNamespace())))
    return app, manager, registry, state


def authorize(app):
    app.dependency_overrides[routes.require_admin] = lambda: SimpleNamespace(qq="fixture-admin", device_id="fixture-device")


def draft():
    return {"token": TOKEN, "name": "测试 Bot", "scope": "private", "persona": "测试人格", "proxy": PROXY, "api_server": "https://api.telegram.org"}


@pytest.mark.parametrize("method,path,payload", [
    ("GET", "", None),
    ("POST", "/probe", draft()),
    ("POST", "", draft()),
    ("PATCH", "/fixture", {"name": "changed"}),
    ("DELETE", "/fixture", None),
    ("POST", "/fixture/start", {}),
    ("POST", "/fixture/stop", None),
    ("POST", "/fixture/check", None),
    ("GET", "/fixture/status", None),
    ("GET", "/fixture/rooms", None),
    ("PATCH", "/fixture/rooms", {"chat_id": "-100", "participation": "on"}),
    ("POST", "/fixture/moderation", {"action": "pin", "chat_id": "-1"}),
])
def test_every_connection_route_requires_admin(harness, method, path, payload):
    app, manager, _, _ = harness
    with TestClient(app) as client:
        response = client.request(method, BASE + path, json=payload)
        assert response.status_code in {401, 403}
    assert not manager.root.exists(), "Unauthenticated requests must not touch connection data"


@pytest.mark.parametrize("method,path", [("POST", "/probe"), ("POST", ""), ("PATCH", "/fixture")])
def test_credentials_require_secure_transport(harness, method, path):
    app, manager, _, _ = harness
    authorize(app)
    with TestClient(app, base_url="http://public.example", client=("8.8.8.8", 1234)) as client:
        response = client.request(method, BASE + path, json=draft())
        assert response.status_code == 403
        assert response.json()["detail"]["code"] == "secure_transport_required"
        assert TOKEN not in response.text and "test-password" not in response.text
    assert not manager.root.exists()


def test_webui_contract_probe_create_edit_lifecycle_and_secret_redaction(harness):
    app, manager, _, _ = harness
    authorize(app)
    with TestClient(app) as client:
        probe = client.post(BASE + "/probe", json=draft())
        assert probe.status_code == 200
        assert probe.json()["connection"]["status"]["credentials"] == "verified"
        assert probe.json()["connection"]["status"]["reception"] == "unverified"
        saved = client.post(BASE, json=draft())
        assert saved.status_code == 200
        ident = saved.json()["connection"]["id"]
        changed = client.patch(BASE + "/" + ident, json={"name": "新名称", "scope": "all"})
        assert changed.status_code == 200
        assert changed.json()["connection"]["name"] == "新名称"
        assert manager.records[ident]["token"] == TOKEN
        assert manager.records[ident]["proxy"] == PROXY
        checked = client.post(BASE + "/" + ident + "/check")
        assert checked.status_code == 200
        started = client.post(BASE + "/" + ident + "/start")
        assert started.status_code == 200
        assert started.json()["connection"]["state"] == "running"
        assert started.json()["connection"]["test_url"].startswith("https://t.me/isolated_fixture_bot?start=pf_")
        assert client.patch(BASE + "/" + ident, json={"name": "bad"}).status_code == 400
        stopped = client.post(BASE + "/" + ident + "/stop")
        assert stopped.status_code == 200 and stopped.json()["connection"]["state"] == "stopped"
        listed = client.get(BASE)
        status = client.get(BASE + "/" + ident + "/status")
        for response in (probe, saved, changed, checked, started, stopped, listed, status):
            assert TOKEN not in response.text and PROXY not in response.text and "test-password" not in response.text
        assert not manager.tasks
        assert client.delete(BASE + "/" + ident).json() == {"ok": True}
        assert client.get(BASE).json() == {"items": []}


def test_webhook_migration_requires_boolean_true(harness):
    app, _, _, state = harness
    authorize(app)
    state["webhook"] = "https://old.example/webhook"
    with TestClient(app) as client:
        assert client.post(BASE + "/probe", json=draft()).json()["connection"]["webhook_conflict"]
        ident = client.post(BASE, json=draft()).json()["connection"]["id"]
        for payload in ({}, {"migrate_webhook": "true"}, {"migrate_webhook": 1}):
            result = client.post(BASE + "/" + ident + "/start", json=payload)
            assert result.status_code == 409
            assert result.json()["detail"]["code"] == "webhook_conflict"
            assert "deleted" not in state
        current = client.get(BASE).json()["items"][0]
        assert current["last_error"] == "webhook_conflict"
        assert client.post(BASE + "/" + ident + "/start", json={"migrate_webhook": True}).status_code == 200
        assert state["deleted"] == {"drop_pending_updates": False}
        client.post(BASE + "/" + ident + "/stop")


def test_native_bot_is_read_only_but_can_be_checked(harness):
    app, manager, registry, state = harness
    authorize(app)
    bot = FakeTelegramBot(draft(), registry, state)
    registry[bot.self_id] = bot
    path = BASE + "/native:" + bot.self_id
    with TestClient(app) as client:
        item = client.get(BASE).json()["items"][0]
        assert item["source"] == "native"
        assert client.post(path + "/check").json()["connection"]["status"]["credentials"] == "verified"
        for method, suffix, payload in (("PATCH", "", {"name": "mutate"}), ("DELETE", "", None), ("POST", "/start", {}), ("POST", "/stop", None)):
            result = client.request(method, path + suffix, json=payload)
            assert result.status_code in {400, 404}
        assert registry[bot.self_id] is bot and not manager.records
        assert client.post(BASE, json=draft()).status_code == 409


def test_provider_failure_never_echoes_credentials(harness):
    app, _, _, state = harness
    authorize(app)
    state["fault"] = True
    with TestClient(app) as client:
        for suffix in ("/probe", ""):
            response = client.post(BASE + suffix, json=draft())
            assert response.status_code == 503
            assert TOKEN not in response.text and "test-password" not in response.text
            assert response.json()["detail"]["code"] == "connection_unavailable"


def test_missing_status_has_structured_not_found(harness):
    app, _, _, _ = harness
    authorize(app)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get(BASE + "/missing/status")
        assert response.status_code == 404
        assert response.json()["detail"]["code"] == "not_found"

def test_corrupt_credential_file_has_sanitized_service_error(harness):
    app, manager, _, _ = harness
    authorize(app)
    manager.root.mkdir(parents=True)
    (manager.root / "connections.secret.json").write_text(TOKEN, encoding="utf-8")
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get(BASE)
        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "credentials_unreadable"
        assert TOKEN not in response.text


def test_moderation_keeps_unknown_receipt_without_retry(harness, monkeypatch):
    app, manager, registry, state = harness
    authorize(app)
    bot = FakeTelegramBot(draft(), registry, state)
    registry[bot.self_id] = bot
    actions = load_personification_module("plugin.personification.core.telegram_actions")
    calls = []

    async def moderate(actual_bot, action, chat_id, **kwargs):
        calls.append((actual_bot, action, chat_id, kwargs))
        return SimpleNamespace(state="unknown", code="telegram_delivery_unknown", message_id="")

    monkeypatch.setattr(actions, "moderate", moderate)
    original_guard = manager.send_guarded
    guards = []
    async def guarded(actual_bot, operation):
        guards.append(actual_bot)
        return await original_guard(actual_bot, operation)
    monkeypatch.setattr(manager, "send_guarded", guarded)
    with TestClient(app) as client:
        response = client.post(BASE + "/native:" + bot.self_id + "/moderation", json={"action": "delete", "chat_id": "-100", "message_id": "7"})
        assert response.status_code == 200
        assert response.json() == {"state": "unknown", "code": "telegram_delivery_unknown", "message_id": ""}
        assert len(calls) == 1
        assert guards == [bot]
        assert calls[0][:3] == (bot, "delete", "-100")
        assert calls[0][3]["message_id"] == "7"


@pytest.mark.parametrize("method,path", [("POST", "/probe"), ("POST", ""), ("PATCH", "/fixture"), ("PATCH", "/fixture/rooms"), ("POST", "/fixture/start"), ("POST", "/fixture/moderation")])
def test_malformed_body_never_echoes_token(harness, method, path):
    app, _, _, _ = harness
    authorize(app)
    with TestClient(app) as client:
        for body in (TOKEN, [TOKEN], 123):
            response = client.request(method, BASE + path, json=body)
            assert response.status_code == 422
            assert TOKEN not in response.text
            assert "input" not in response.text


def test_rooms_contract_preserves_bot_and_topic_isolation(harness):
    app, manager, _, _ = harness
    authorize(app)
    with TestClient(app) as client:
        ident = client.post(BASE, json=draft()).json()["connection"]["id"]
        path = BASE + "/" + ident + "/rooms"
        manager.room("123456", "-100", "4", scope_key="telegram:123456:-100:4", title="测试话题")
        manager.room("123456", "-100", "5", scope_key="telegram:123456:-100:5", title="其他话题")
        manager.room("other-bot", "-100", "4", scope_key="telegram:other-bot:-100:4", title="另一个 Bot")
        rows = client.get(path).json()["items"]
        assert len(rows) == 2
        assert all(row["participation"] == "shadow" and row["proactive_enabled"] is False for row in rows)
        updated = client.patch(path, json={"chat_id": "-100", "topic_id": "4", "participation": "on", "proactive_enabled": True})
        assert updated.status_code == 200
        assert updated.json()["room"] == {"chat_id": "-100", "topic_id": "4", "title": "测试话题", "scope_key": "telegram:123456:-100:4", "participation": "on", "proactive_enabled": True}
        assert manager.room("123456", "-100", "5")["participation"] == "shadow"
        assert manager.room("other-bot", "-100", "4")["participation"] == "shadow"
        for body in ({"participation": "invalid"}, {"proactive_enabled": "true"}):
            result = client.patch(path, json={"chat_id": "-100", "topic_id": "4", **body})
            assert result.status_code == 400
        assert client.patch(path, json={"chat_id": "-999", "topic_id": "4", "participation": "on"}).status_code == 400
        assert client.get(BASE + "/missing/rooms").status_code == 404


def test_native_rooms_settings_do_not_mutate_native_credentials(harness):
    app, manager, registry, state = harness
    authorize(app)
    bot = FakeTelegramBot(draft(), registry, state)
    registry[bot.self_id] = bot
    manager.room(bot.self_id, "-100", "", scope_key="fixture-scope")
    with TestClient(app) as client:
        path = BASE + "/native:" + bot.self_id + "/rooms"
        assert client.get(path).status_code == 200
        assert client.patch(path, json={"chat_id": "-100", "participation": "off", "proactive_enabled": False}).status_code == 200
        assert manager.records == {}
        assert registry[bot.self_id] is bot
