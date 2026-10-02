from __future__ import annotations

import hashlib
import json

import pytest

from ._loader import load_personification_module
from .test_webui_smoke import _build_client, _runtime_context  # noqa: F401
from .test_webui_device_approval import _extract_code

BASE = "/personification/api/auth"
TRUST = "personification_webui_trusted_device"
SESSION = "personification_webui_token"


def _store():
    return load_personification_module("plugin.personification.core.webui_auth_store")


def _trusted_login(client, ctx):
    assert client.post(BASE + "/login", json={"qq": "10001"}).status_code == 200
    response = client.post(BASE + "/verify", json={"qq": "10001", "code": _extract_code(ctx.sent), "trust_device": True})
    assert response.status_code == 200
    client.headers["X-Personification-CSRF"] = client.cookies.get("personification_webui_csrf")
    return response


def _refresh(client, origin="http://testserver"):
    return client.post(BASE + "/refresh", headers={"Origin": origin, "X-Personification-Refresh": "1"})


def test_trust_cookie_recovers_expired_session_and_survives_store_restart(_runtime_context, monkeypatch):
    client = _build_client(_runtime_context)
    response = _trusted_login(client, _runtime_context)
    trust = client.cookies.get(TRUST)
    assert len(trust) >= 64
    assert any("HttpOnly" in item and "SameSite=strict" in item and "Max-Age=31536000" in item for item in response.headers.get_list("set-cookie") if item.startswith(TRUST))
    store = _store()
    data = store.get_data_store().load_sync("webui_trusted_devices")
    assert trust not in json.dumps(data)
    assert hashlib.sha256(trust.encode()).hexdigest() in data
    timestamp = store._now()
    monkeypatch.setattr(store, "_now", lambda: timestamp + 8 * 86400)
    assert client.get(BASE + "/me").status_code == 401
    data_store = load_personification_module("plugin.personification.core.data_store")
    data_store.init_data_store(_runtime_context.plugin_config)
    assert _refresh(client).status_code == 200
    me = client.get(BASE + "/me")
    assert me.status_code == 200 and me.json()["trusted"] is True
    assert trust not in me.text
    assert client.cookies.get(SESSION)


def test_same_ua_and_legacy_trust_are_not_credentials(_runtime_context):
    client = _build_client(_runtime_context)
    _store().add_trusted_device("10001", "testclient")
    assert _refresh(client).status_code == 401
    assert client.get(BASE + "/me").status_code == 401


@pytest.mark.parametrize("action", ["expire", "untrust", "device", "logout", "acl"])
def test_revocation_blocks_trust_and_all_linked_sessions(_runtime_context, monkeypatch, action):
    client = _build_client(_runtime_context)
    _trusted_login(client, _runtime_context)
    stolen_trust = client.cookies.get(TRUST)
    me = client.get(BASE + "/me").json()
    store = _store()
    if action == "expire":
        now = store._now()
        monkeypatch.setattr(store, "_now", lambda: now + 366 * 86400)
    elif action == "untrust":
        assert client.delete(BASE + "/trusted-devices/" + me["trust_id"]).status_code == 200
    elif action == "device":
        assert client.delete(BASE + "/devices/" + me["device_id"]).status_code == 200
    elif action == "logout":
        assert client.post(BASE + "/logout").status_code == 200
        assert not client.cookies.get(TRUST)
    else:
        _runtime_context.app_module.set_runtime_context(plugin_config=_runtime_context.plugin_config, superusers=set(), get_bots=lambda: {}, logger=None)
    assert client.get(BASE + "/me").status_code == 401
    client.cookies.set(TRUST, stolen_trust, domain="testserver.local", path="/personification")
    assert _refresh(client).status_code == 401


def test_refresh_requires_same_origin_and_trust_only_current_device(_runtime_context, monkeypatch):
    actions = []
    routes = load_personification_module("plugin.personification.webui.routes.auth_routes")
    monkeypatch.setattr(routes.webui_audit_log, "record", lambda **kwargs: actions.append(kwargs["action"]))
    client = _build_client(_runtime_context)
    _trusted_login(client, _runtime_context)
    assert _refresh(client, "https://evil.example").status_code == 403
    assert client.post(BASE + "/refresh").status_code == 403
    assert client.post(BASE + "/refresh", headers={"Sec-Fetch-Site": "same-origin", "X-Personification-Refresh": "1"}).status_code == 200
    client.headers["X-Personification-CSRF"] = client.cookies.get("personification_webui_csrf")
    assert client.post(BASE + "/devices/other/trust").status_code == 403
    me = client.get(BASE + "/me").json()
    trusted = client.post(BASE + "/devices/" + me["device_id"] + "/trust")
    assert trusted.status_code == 200
    assert trusted.json()["code"] == "device_trusted"
    assert actions[-1] == "device_trust"


def test_refresh_revocation_invalidates_multiple_issued_sessions(_runtime_context):
    client = _build_client(_runtime_context)
    _trusted_login(client, _runtime_context)
    previous = client.cookies.get(SESSION)
    assert _refresh(client).status_code == 200
    latest = client.cookies.get(SESSION)
    assert latest != previous
    client.headers["X-Personification-CSRF"] = client.cookies.get("personification_webui_csrf")
    trust_id = client.get(BASE + "/me").json()["trust_id"]
    assert client.delete(BASE + "/trusted-devices/" + trust_id).status_code == 200
    other = _build_client(_runtime_context)
    for token in (previous, latest):
        other.cookies.set(SESSION, token, domain="testserver.local", path="/personification")
        assert other.get(BASE + "/me").status_code == 401


def test_public_http_peer_cannot_forge_https_trust_transport(_runtime_context):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI()
    app.include_router(_runtime_context.app_module.build_router())
    client = TestClient(app, client=("8.8.8.8", 50000))
    response = client.post(BASE + "/refresh", headers={"Origin": "https://testserver", "X-Forwarded-Proto": "https"})
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "trusted_device_transport_required"


def test_trust_binding_revoked_during_issue_fails_closed(_runtime_context, monkeypatch):
    store = _store()
    session = store.issue_device_token("10001", "ua", "127.0.0.1")
    device_id = hashlib.sha256(session.encode()).hexdigest()
    data_store = store.get_data_store()
    original = data_store.mutate_sync
    def mutate(namespace, callback):
        result = original(namespace, callback)
        if namespace == "webui_trusted_devices":
            original("webui_devices", lambda data: {})
        return result
    monkeypatch.setattr(data_store, "mutate_sync", mutate)
    with pytest.raises(ValueError):
        store.issue_trusted_device("10001", "ua", device_id)
    assert data_store.load_sync("webui_trusted_devices") == {}


def test_expired_session_can_logout_with_only_trust_and_same_origin(_runtime_context, monkeypatch):
    client = _build_client(_runtime_context)
    _trusted_login(client, _runtime_context)
    token = client.cookies.get(TRUST)
    now = _store()._now()
    monkeypatch.setattr(_store(), "_now", lambda: now + 8 * 86400)
    assert client.post(BASE + "/logout").status_code == 403
    assert client.post(BASE + "/logout", headers={"Origin": "http://testserver"}).status_code == 200
    assert not client.cookies.get(TRUST)
    assert _store().lookup_trusted_device(token) is None


def test_verify_replaces_old_browser_trust_and_failed_refresh_clears_cookie(_runtime_context):
    client = _build_client(_runtime_context)
    _trusted_login(client, _runtime_context)
    previous = client.cookies.get(TRUST)
    _trusted_login(client, _runtime_context)
    assert client.cookies.get(TRUST) != previous
    assert _store().lookup_trusted_device(previous) is None
    other = _build_client(_runtime_context)
    other.cookies.set(TRUST, previous, domain="testserver.local", path="/personification")
    failed = _refresh(other)
    assert failed.status_code == 401
    assert not other.cookies.get(TRUST)


def test_legacy_trust_list_is_explicitly_invalid(_runtime_context):
    client = _build_client(_runtime_context)
    _trusted_login(client, _runtime_context)
    _store().add_trusted_device("10001", "old")
    items = client.get(BASE + "/trusted-devices").json()["devices"]
    assert any(item["legacy"] and not item["valid"] for item in items)
    assert any(not item["legacy"] and item["valid"] for item in items)
