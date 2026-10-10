"""Real WebUI routes with official b20 Bot and an isolated transport boundary."""
from __future__ import annotations

import asyncio
import json
import re
from types import SimpleNamespace

import pytest

from ._loader import load_personification_module
from .test_telegram_bridge import bot as telegram_bot
from .test_webui_security import _client, _runtime  # noqa: F401

identity = load_personification_module("plugin.personification.core.webui_admin_identity")
notify = load_personification_module("plugin.personification.core.notify")
auth_store = load_personification_module("plugin.personification.core.webui_auth_store")
config_mod = load_personification_module("plugin.personification.config")
registry = load_personification_module("plugin.personification.core.config_registry")

TG = "telegram:10001"


@pytest.fixture
def tg_runtime(_runtime):
    runtime = _runtime.app_module.get_runtime_context()
    runtime.plugin_config.personification_telegram_admins = ["10001"]
    native, calls = telegram_bot()
    runtime.get_bots = lambda: {"telegram": native}
    return SimpleNamespace(rt=_runtime, runtime=runtime, bot=native, calls=calls)


def request_code(setup, client, target=TG):
    response = client.post("/personification/api/auth/login", json={"qq": target})
    assert response.status_code == 200, response.text
    return re.search(r"验证码：(\d{6})", setup.calls[-1][1]["text"]).group(1)


def verify_code(client, code, target=TG, **kwargs):
    return client.post("/personification/api/auth/verify", json={"qq": target, "code": code, **kwargs})


def test_tg_only_real_route_login_csrf_and_audit(tg_runtime):
    setup = tg_runtime
    setup.runtime.superusers.clear()
    client = _client(setup.rt)
    admins = client.get("/personification/api/auth/eligible-admins").json()
    assert admins == {"admins": [{"qq": TG, "source": "telegram_admins", "platform": "telegram", "label": "Telegram 10001"}], "manual_entry": False, "source_hidden": False}
    code = request_code(setup, client)
    assert setup.calls[0][0] == "send_message"
    assert setup.calls[0][1]["chat_id"] == 10001
    assert "message" not in setup.calls[0][1]
    verified = verify_code(client, code)
    assert verified.status_code == 200
    assert verified.json()["pending"] is False
    me = client.get("/personification/api/auth/me")
    assert me.status_code == 200
    assert me.json()["qq"] == TG
    assert me.json()["platform"] == "telegram"
    assert me.json()["identity_source"] == "telegram_admin"
    v2_identity = client.get("/personification/api/v2/admin-identity")
    assert v2_identity.status_code == 200
    assert v2_identity.json()["qq"] == TG
    assert v2_identity.json()["platform"] == "telegram"
    assert v2_identity.json()["identity_source"] == "telegram_admin"
    assert client.delete("/personification/api/auth/devices/fake").status_code == 403
    client.headers["X-Personification-CSRF"] = client.cookies.get("personification_webui_csrf")
    assert client.delete("/personification/api/auth/devices/fake").status_code == 404
    audit = load_personification_module("plugin.personification.core.webui_audit_log")
    entries = audit.query_recent(qq=TG)
    assert {entry["action"] for entry in entries} >= {"login_code_sent", "login_verify"}
    assert code not in json.dumps(entries, ensure_ascii=False)


def test_same_number_qq_and_tg_are_separate_routes_challenges_and_devices(tg_runtime, monkeypatch):
    setup = tg_runtime
    qq_calls = []

    async def qq_api(api, **kwargs):
        qq_calls.append((api, kwargs))
        return {"message_id": 42}

    setup.runtime.get_bots = lambda: {"qq": SimpleNamespace(call_api=qq_api), "tg": setup.bot}
    client = _client(setup.rt)
    admins = client.get("/personification/api/auth/eligible-admins").json()["admins"]
    assert {entry["qq"] for entry in admins} >= {"10001", TG}
    tg_code = request_code(setup, client)
    # Grant QQ a different code under the same browser challenge: swapping the
    # platform must not consume or verify the TG code.
    challenge = client.cookies.get("personification_webui_login_challenge")
    with monkeypatch.context() as patch:
        patch.setattr(auth_store.secrets, "randbelow", lambda _n: (int(tg_code) + 1) % 1000000)
        auth_store.create_verify_code("10001", challenge)
    assert verify_code(client, tg_code, "10001").status_code == 403
    assert verify_code(client, tg_code).status_code == 200
    assert {call[1]["user_id"] for call in qq_calls} == {10001, 20002}
    assert all(tg_code not in call[1]["message"] for call in qq_calls)
    tg_devices = client.get("/personification/api/auth/devices").json()["devices"]
    assert {entry["qq"] for entry in tg_devices} == {TG}
    assert auth_store.list_devices("10001") == []
    # The same QQ grant remains live when the Telegram config grant is revoked.
    qq_token = auth_store.issue_device_token("10001", "testclient", "testclient", status="approved")
    setup.runtime.plugin_config.personification_telegram_admins = []
    assert client.get("/personification/api/auth/me").status_code == 401
    other = _client(setup.rt)
    other.cookies.set("personification_webui_token", qq_token, path="/personification")
    assert other.get("/personification/api/auth/me").status_code == 200


def test_qq_admin_cannot_claim_same_number_tg_or_prefix_grant(tg_runtime):
    setup = tg_runtime
    setup.runtime.plugin_config.personification_telegram_admins = []
    setup.runtime.superusers.add(TG)
    acl = load_personification_module("plugin.personification.core.admin_acl")
    acl.add_plugin_admin("telegram:20002")
    try:
        client = _client(setup.rt)
        assert client.post("/personification/api/auth/login", json={"qq": TG}).status_code == 403
        assert client.post("/personification/api/auth/login", json={"qq": "telegram:20002"}).status_code == 403
        assert TG not in {entry["qq"] for entry in client.get("/personification/api/auth/eligible-admins").json()["admins"]}
        assert setup.calls == []
    finally:
        acl.remove_plugin_admin("telegram:20002")


@pytest.mark.parametrize("target", ["telegram:010001", "telegram:+10001", "telegram:-10001", "telegram:１０００１", "Telegram:10001", "telegram:telegram:10001", "qq:10001", "telegram:4503599627370496"])
def test_forged_identity_is_rejected_by_login_and_authorizer(tg_runtime, target):
    setup = tg_runtime
    setup.runtime.superusers.add(target)
    client = _client(setup.rt)
    assert client.post("/personification/api/auth/login", json={"qq": target}).status_code == 403
    token = auth_store.issue_device_token(target, "testclient", "testclient")
    client.cookies.set("personification_webui_token", token, path="/personification")
    assert client.get("/personification/api/auth/me").status_code == 401
    assert setup.calls == []


def test_long_52_bit_tg_identity_fits_request_and_actual_route(tg_runtime):
    setup = tg_runtime
    setup.runtime.plugin_config.personification_telegram_admins = ["4503599627370495"]
    code = request_code(setup, client := _client(setup.rt), "telegram:4503599627370495")
    assert verify_code(client, code, "telegram:4503599627370495").status_code == 200


def test_tg_browser_binding_and_send_cooldown_remain_enforced(tg_runtime):
    setup = tg_runtime
    client, other = _client(setup.rt), _client(setup.rt)
    code = request_code(setup, client)
    assert client.post("/personification/api/auth/login", json={"qq": TG}).status_code == 429
    assert len(setup.calls) == 1
    assert verify_code(other, code).status_code == 403
    assert verify_code(client, code).status_code == 200


def test_tg_revoked_before_verify_and_trusted_refresh(tg_runtime):
    setup = tg_runtime
    client = _client(setup.rt)
    code = request_code(setup, client)
    setup.runtime.plugin_config.personification_telegram_admins = []
    assert verify_code(client, code).status_code == 403
    assert not client.cookies.get("personification_webui_token")
    setup.runtime.plugin_config.personification_telegram_admins = ["10001"]
    assert verify_code(client, code, trust_device=True).status_code == 200
    assert client.cookies.get("personification_webui_trusted_device")
    assert client.post("/personification/api/auth/refresh", headers={"Origin": "http://testserver"}).status_code == 200
    setup.runtime.plugin_config.personification_telegram_admins = []
    assert client.post("/personification/api/auth/refresh", headers={"Origin": "http://testserver"}).status_code == 401
    assert not client.cookies.get("personification_webui_trusted_device")


@pytest.mark.parametrize("revoked", [False, True])
def test_legacy_approved_login_status_uses_same_tg_authorizer(tg_runtime, revoked):
    setup = tg_runtime
    rid, code = auth_store.create_login_request(TG, "testclient", "testclient")
    assert auth_store.approve_login_request(TG, code)
    if revoked:
        setup.runtime.plugin_config.personification_telegram_admins = []
    client = _client(setup.rt)
    result = client.get("/personification/api/auth/login-status", params={"request_id": rid})
    assert result.status_code == (403 if revoked else 200)
    assert bool(client.cookies.get("personification_webui_token")) is not revoked


@pytest.mark.parametrize("result", [None, {}, {"message_id": 0}, {"message_id": True}, {"message_id": "123"}])
def test_tg_notification_requires_confirmed_receipt_without_qq_fallback(result):
    native, _ = telegram_bot()
    calls = []

    async def send(_bot, api, **kwargs):
        calls.append(api)
        return result

    native.adapter._call_api = send
    qq = SimpleNamespace(call_api=send)
    second, second_calls = telegram_bot()
    assert asyncio.run(notify.send_to_admin(lambda: {"tg": native, "qq": qq, "tg2": second}, TG, "验证码：123456")) is False
    assert calls == ["send_message"]
    assert second_calls == []


def test_tg_send_failure_discards_otp_and_never_grants_session(tg_runtime, monkeypatch):
    setup = tg_runtime
    challenge = "isolated-known-challenge"
    captured = []
    original = auth_store.create_verify_code

    def create(target, token):
        code = original(target, token)
        captured.append(code)
        return code

    async def failed(_bot, _api, **_kwargs):
        raise TimeoutError("sensitive transport body 123456")

    monkeypatch.setattr(auth_store, "create_verify_code", create)
    setup.bot.adapter._call_api = failed
    client = _client(setup.rt)
    client.cookies.set("personification_webui_login_challenge", challenge, path="/personification")
    response = client.post("/personification/api/auth/login", json={"qq": TG})
    assert response.status_code == 502
    assert "123456" not in response.text
    assert not auth_store.consume_verify_code(TG, captured[0], challenge)
    assert not client.cookies.get("personification_webui_token")


def test_tg_operator_can_use_qq_health_test_but_target_still_requires_qq_grant(tg_runtime, monkeypatch):
    setup = tg_runtime
    health = load_personification_module("plugin.personification.webui.routes.health_routes")
    monkeypatch.setattr(health, "_first_bot", lambda _runtime: None)
    setup.runtime.superusers = {"10001"}
    setup.runtime.plugin_config.personification_webui_test_user_id = "10001"
    client = _client(setup.rt)
    assert verify_code(client, request_code(setup, client)).status_code == 200
    client.headers["X-Personification-CSRF"] = client.cookies.get("personification_webui_csrf")
    allowed = client.post("/personification/api/health/interaction-test", json={"target": "private"})
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["diagnosis_code"] == "bot_not_connected"
    setup.runtime.plugin_config.personification_webui_test_user_id = "20002"
    rejected = client.post("/personification/api/health/interaction-test", json={"target": "private"})
    assert rejected.status_code == 403
    assert rejected.json()["detail"]["code"] == "health_interaction_test_identity_forbidden"
    assert setup.calls and all(call[0] == "send_message" for call in setup.calls)


def test_official_adapter_debug_does_not_expose_private_otp(monkeypatch):
    from nonebot.adapters.telegram import Adapter, adapter
    from nonebot.adapters.telegram.config import BotConfig
    from nonebot.drivers import Response
    from nonebot.adapters.telegram import Bot

    # Execute the real b20 _call_api serialization and DEBUG logging with only
    # its HTTP request replaced. Restore the module logger after this test.
    logs, requests = [], []
    monkeypatch.setattr(adapter, "log", lambda *_a, **_k: logs.append(_a))
    transport = object.__new__(Adapter)
    transport.adapter_config = SimpleNamespace(proxy=None)

    async def request(value):
        requests.append(value)
        return Response(200, content=json.dumps({"result": {"message_id": 9, "date": 1700000000, "chat": {"id": 10001, "type": "private"}, "text": "验证码：123456"}}))

    transport.request = request
    native = Bot(transport, "123", config=BotConfig(token="123:isolated-test-token"))
    assert asyncio.run(notify.send_to_admin(lambda: {"tg": native}, TG, "验证码：123456")) is True
    assert requests[0].json["text"] == "验证码：123456"
    assert logs == []
    # The context is reset after send; ordinary non-sensitive debug still logs.
    adapter.log("DEBUG", "ordinary chat")
    assert logs == [("DEBUG", "ordinary chat")]


def test_telegram_admin_config_is_editable_validated_and_hot_reloaded(tg_runtime):
    setup = tg_runtime
    entry = next(item for item in registry.get_config_entries() if item.key == "telegram_admins")
    assert entry.hot_reloadable and entry.admin_only and entry.kind == "strlist"
    assert entry.normalize_value('["10001","10001","20002"]') == ["10001", "20002"]
    assert config_mod.Config(personification_telegram_admins=["10001"]).personification_telegram_admins == ["10001"]
    setup.runtime.plugin_config.personification_telegram_admins = []
    client = _client(setup.rt)
    from .test_webui_security import _login

    # QQ login is used to grant the first TG administrator.
    csrf = _login(setup.rt, client)
    client.headers["X-Personification-CSRF"] = csrf
    saved = client.post("/personification/api/config/value", json={"field_name": "personification_telegram_admins", "value": '["10001"]'})
    assert saved.status_code == 200, saved.text
    assert setup.runtime.plugin_config.personification_telegram_admins == ["10001"]
    assert TG in {item["qq"] for item in client.get("/personification/api/auth/eligible-admins").json()["admins"]}


@pytest.mark.parametrize("values", [["telegram:10001"], ["@admin"], ["-10001"], ["010001"], ["１０００１"], [10001], [True], ["4503599627370496"], "10001"])
def test_invalid_telegram_config_is_rejected_and_never_grants(values):
    entry = next(item for item in registry.get_config_entries() if item.key == "telegram_admins")
    with pytest.raises(ValueError):
        config_mod.Config(personification_telegram_admins=values)
    with pytest.raises(ValueError):
        entry.normalize_value(json.dumps(values))
    assert identity.configured_telegram_identities(SimpleNamespace(personification_telegram_admins=values)) == []
