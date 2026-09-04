from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from ._loader import load_personification_module
from .test_webui_smoke import _build_client, _login_as_admin, _runtime_context  # noqa: F401


qzone_service = load_personification_module("plugin.personification.core.qzone_service")


class _Bot:
    self_id = "10001"

    def __init__(self, runtime_context=None) -> None:  # noqa: ANN001
        self._runtime_context = runtime_context

    async def call_api(self, _name, **kwargs):  # noqa: ANN001, ANN003
        if self._runtime_context is not None:
            self._runtime_context.sent.append(kwargs)
        return {"message_id": 1}

    async def send_private_msg(self, **kwargs):  # noqa: ANN003
        if self._runtime_context is not None:
            self._runtime_context.sent.append(kwargs)
        return {"message_id": 1}


class _Logger:
    def info(self, *_args, **_kwargs) -> None:  # noqa: ANN002, ANN003
        return None

    def warning(self, *_args, **_kwargs) -> None:  # noqa: ANN002, ANN003
        return None

    def error(self, *_args, **_kwargs) -> None:  # noqa: ANN002, ANN003
        return None


@pytest.fixture(autouse=True)
def _reset_auth_states():  # noqa: ANN202
    with qzone_service._AUTH_STATE_LOCK:
        qzone_service._AUTH_STATES.clear()
    yield
    with qzone_service._AUTH_STATE_LOCK:
        qzone_service._AUTH_STATES.clear()


def _stages(*, target_count: int = 0) -> list[dict]:
    return [
        {"key": "bot_online", "status": "ok", "code": "qzone_read_only_bot_online", "elapsed_ms": 1},
        {"key": "cookie_export", "status": "ok", "code": "onebot_cookie_export_succeeded", "elapsed_ms": 1},
        {"key": "identity_match", "status": "ok", "code": "qzone_read_only_identity_matched", "elapsed_ms": 1},
        {"key": "login_page_check", "status": "ok", "code": "qzone_read_only_login_page_clear", "elapsed_ms": 1},
        {"key": "self_feed_read", "status": "ok", "code": "qzone_feed_read_ok", "elapsed_ms": 1, "count": 1},
        {"key": "target_feed_read", "status": "ok", "code": "qzone_feed_read_ok", "elapsed_ms": 1, "count": target_count},
        {"key": "normalization_commit", "status": "ok", "code": "qzone_read_only_diagnostics_succeeded", "elapsed_ms": 1},
    ]


def test_read_only_diagnostics_runs_all_stages_without_social_writes(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    config = SimpleNamespace(personification_data_dir=str(tmp_path))
    calls: list[str] = []
    cookie = "uin=o10001; p_skey=diagnostic-cookie-secret;"

    async def export(**_kwargs):  # noqa: ANN003
        calls.append("cookie_export")
        return True, "onebot_cookie_export_succeeded", cookie

    async def login_probe(_cookie: str, _qq: str, _p_skey: str):  # noqa: ANN001
        calls.append("login_page_check")
        return True, "ok"

    async def read_probe(*, target_uin: str, **_kwargs):  # noqa: ANN003
        calls.append(f"read:{target_uin}")
        return True, "qzone_feed_read_ok", 2 if target_uin == "20002" else 1

    async def forbidden_write(*_args, **_kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("read-only diagnostics must not invoke QZone writes")

    monkeypatch.setattr(qzone_service, "_export_qzone_cookie_from_bot", export)
    monkeypatch.setattr(qzone_service, "_probe_qzone_cookie", login_probe)
    monkeypatch.setattr(qzone_service, "_read_qzone_feed_probe", read_probe)
    for name in ("like_feed", "comment_feed", "forward_feed"):
        monkeypatch.setattr(qzone_service.QzoneSocialService, name, forbidden_write, raising=False)

    result = asyncio.run(
        qzone_service.run_qzone_read_only_diagnostics(
            bot=_Bot(),
            plugin_config=config,
            logger=_Logger(),
            target_user_id="20002",
        )
    )

    assert result["ok"] is True
    assert [item["key"] for item in result["stages"]] == [
        "bot_online",
        "cookie_export",
        "identity_match",
        "login_page_check",
        "self_feed_read",
        "target_feed_read",
        "normalization_commit",
    ]
    assert calls == ["cookie_export", "login_page_check", "read:10001", "read:20002"]
    assert result["stages"][4]["count"] == 1
    assert result["stages"][5]["count"] == 2
    assert result["target"] == {"provided": True, "summary": "20***02"}
    rendered = str(result)
    assert "diagnostic-cookie-secret" not in rendered
    assert "uin=o10001" not in rendered


def test_read_only_diagnostics_probe_failure_keeps_old_stored_credential(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    config = SimpleNamespace(personification_data_dir=str(tmp_path))
    store = qzone_service.QzoneCredentialStore(config)
    store.replace(bot_id="10001", cookie="uin=o10001; p_skey=old-secret;", source="onebot")

    async def export(**_kwargs):  # noqa: ANN003
        return True, "onebot_cookie_export_succeeded", "uin=o10001; p_skey=new-secret;"

    async def rejected_login(_cookie: str, _qq: str, _p_skey: str):  # noqa: ANN001
        return False, "auth_blocked"

    monkeypatch.setattr(qzone_service, "_export_qzone_cookie_from_bot", export)
    monkeypatch.setattr(qzone_service, "_probe_qzone_cookie", rejected_login)

    result = asyncio.run(
        qzone_service.run_qzone_read_only_diagnostics(
            bot=_Bot(),
            plugin_config=config,
            logger=_Logger(),
        )
    )

    assert result["ok"] is False
    assert result["failure_code"] == "qzone_read_only_login_required"
    assert result["stages"][-1]["status"] == "skipped"
    assert store.get("10001") == "uin=o10001; p_skey=old-secret;"


def _admin_client(runtime_context):  # noqa: ANN001, ANN202
    runtime_context.app_module.set_runtime_context(
        plugin_config=runtime_context.plugin_config,
        superusers={"10001"},
        get_bots=lambda: {"10001": _Bot(runtime_context)},
        logger=_Logger(),
        runtime_bundle=SimpleNamespace(),
    )
    client = _build_client(runtime_context)
    _login_as_admin(client, runtime_context)
    csrf = client.cookies.get("personification_webui_csrf", "")
    if csrf:
        client.headers["X-Personification-CSRF"] = csrf
    return client


def test_read_only_api_rejects_unconfirmed_request_before_any_external_read(_runtime_context, monkeypatch) -> None:  # noqa: ANN001
    calls: list[dict] = []

    async def should_not_run(**kwargs):  # noqa: ANN003
        calls.append(kwargs)
        raise AssertionError("unconfirmed requests must not start diagnostics")

    monkeypatch.setattr(qzone_service, "run_qzone_read_only_diagnostics", should_not_run)
    client = _admin_client(_runtime_context)

    response = client.post(
        "/personification/api/v2/qzone/diagnostics/read-only",
        json={"bot_id": "10001", "target_user_id": "20002"},
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "qzone_read_only_confirmation_required"
    assert calls == []


def test_read_only_api_returns_only_safe_stage_projection(_runtime_context, monkeypatch) -> None:  # noqa: ANN001
    async def diagnostic(**_kwargs):  # noqa: ANN003
        return {
            "ok": True,
            "code": "qzone_read_only_diagnostics_succeeded",
            "stages": _stages(target_count=3),
            "target": {"provided": True, "summary": "20***02"},
            "raw_cookie": "uin=o10001; p_skey=must-not-leak",
            "feed_body": "must-not-leak-feed-body",
            "url": "https://user.qzone.qq.com/private?secret=must-not-leak-url",
        }

    monkeypatch.setattr(qzone_service, "run_qzone_read_only_diagnostics", diagnostic)
    client = _admin_client(_runtime_context)

    response = client.post(
        "/personification/api/v2/qzone/diagnostics/read-only",
        json={"bot_id": "10001", "target_user_id": "20002", "confirm_external_read": True},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    assert body["target"] == {"provided": True, "summary": "20***02"}
    assert [item["key"] for item in body["stages"]] == [item["key"] for item in _stages()]
    assert body["stages"][5]["count"] == 3
    assert "must-not-leak" not in response.text
    assert "20002" not in response.text
    assert "https://" not in response.text
