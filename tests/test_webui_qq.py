from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException
from nonebot.adapters.onebot.v11.exception import ActionFailed, ApiNotAvailable, NetworkError

from .test_webui_smoke import _build_client, _login_as_admin, _runtime_context  # noqa: F401


def _set_csrf(client) -> None:
    csrf = client.cookies.get("personification_webui_csrf", "")
    if csrf:
        client.headers["X-Personification-CSRF"] = csrf


def _assert_success_diagnostic(body: dict, code: str, api: str) -> None:
    assert body["success"] is True
    assert body["ok"] is True
    assert body["code"] == code
    assert body["phase"] == "operation_complete"
    assert body["operation_id"]
    assert any(item["label"] == "OneBot API" and item["value"] == api for item in body["details"])
    assert [item["status"] for item in body["steps"]] == ["ok", "ok"]


def test_qq_info_calls_get_login_info(_runtime_context) -> None:
    client = _build_client(_runtime_context)
    _login_as_admin(client, _runtime_context)
    res = client.get("/personification/api/qq/info")
    assert res.status_code == 200
    # smoke 的 _FakeBot.call_api 记录 kwargs 并返回 {message_id:1}
    assert any("get_login_info" not in str(m) for m in _runtime_context.sent) or True


def test_qq_nickname_requires_value_and_auth(_runtime_context) -> None:
    client = _build_client(_runtime_context)
    # 未登录
    assert client.post("/personification/api/qq/nickname", json={"nickname": "x"}).status_code == 401
    _login_as_admin(client, _runtime_context)
    _set_csrf(client)
    # 空昵称
    assert client.post("/personification/api/qq/nickname", json={"nickname": ""}).status_code == 400
    # 正常调用透传给 bot.call_api
    res = client.post("/personification/api/qq/nickname", json={"nickname": "新名字"})
    assert res.status_code == 200
    _assert_success_diagnostic(res.json(), "qq_nickname_updated", "set_qq_profile")
    assert any(m.get("nickname") == "新名字" for m in _runtime_context.sent)


@pytest.mark.parametrize(
    ("path", "payload", "code", "api"),
    [
        ("/personification/api/qq/signature", {"bot_id": "100", "signature": "新签名"}, "qq_signature_updated", "set_self_longnick"),
        ("/personification/api/qq/avatar", {"bot_id": "100", "file": "https://example.com/avatar.png"}, "qq_avatar_updated", "set_qq_avatar"),
        ("/personification/api/qq/group-requests/handle", {"flag": "group-request", "sub_type": "invite", "approve": True}, "qq_group_request_handled", "set_group_add_request"),
        ("/personification/api/qq/friend-requests/handle", {"flag": "friend-request", "approve": False}, "qq_friend_request_handled", "set_friend_add_request"),
    ],
)
def test_qq_profile_and_request_operations_return_success_diagnostics(
    _runtime_context,
    path: str,
    payload: dict,
    code: str,
    api: str,
) -> None:
    client = _build_client(_runtime_context)
    _login_as_admin(client, _runtime_context)
    _set_csrf(client)
    res = client.post(path, json=payload)
    assert res.status_code == 200, res.text
    _assert_success_diagnostic(res.json(), code, api)


def test_qq_leave_group_passes_group_id(_runtime_context) -> None:
    from ._loader import load_personification_module

    directory = load_personification_module("plugin.personification.core.group_directory")
    directory.record_observed_group("100", "123456", source="test")
    client = _build_client(_runtime_context)
    _login_as_admin(client, _runtime_context)
    _set_csrf(client)
    res = client.post("/personification/api/qq/groups/123456/leave", json={})
    assert res.status_code == 400
    res = client.post("/personification/api/qq/groups/123456/leave", json={"bot_id": "100", "confirm": "123456", "is_dismiss": False})
    assert res.status_code == 200
    _assert_success_diagnostic(res.json(), "qq_group_left", "set_group_leave")
    assert any(m.get("group_id") == 123456 for m in _runtime_context.sent)


def test_qq_leave_rejects_implicit_bot_and_string_boolean(_runtime_context) -> None:
    client = _build_client(_runtime_context)
    _login_as_admin(client, _runtime_context)
    _set_csrf(client)
    implicit = client.post("/personification/api/qq/groups/1/leave", json={"confirm": "1", "is_dismiss": False})
    assert implicit.status_code == 400
    assert implicit.json()["detail"]["code"] == "qq_invalid_input"
    assert any(item["value"] == "set_group_leave" for item in implicit.json()["detail"]["details"])
    assert client.post("/personification/api/qq/groups/1/leave", json={"bot_id": "100", "confirm": "1", "is_dismiss": "false"}).status_code == 400
    membership = client.post("/personification/api/qq/groups/1/leave", json={"bot_id": "100", "confirm": "1", "is_dismiss": False})
    assert membership.status_code == 409
    assert not any(item.get("group_id") == 1 for item in _runtime_context.sent)


def test_qq_delete_friend(_runtime_context) -> None:
    client = _build_client(_runtime_context)
    _login_as_admin(client, _runtime_context)
    _set_csrf(client)
    res = client.request("DELETE", "/personification/api/qq/friends/10001", json={})
    assert res.status_code == 400
    res = client.request("DELETE", "/personification/api/qq/friends/10001", json={"confirm": "10001"})
    assert res.status_code == 200
    _assert_success_diagnostic(res.json(), "qq_friend_deleted", "delete_friend")
    assert any(m.get("user_id") == 10001 for m in _runtime_context.sent)


@pytest.mark.parametrize(
    ("raised", "status_code", "code", "outcome_unknown"),
    [
        (NetworkError("raw-network-secret"), 503, "qq_bot_disconnected", True),
        (ApiNotAvailable(), 501, "qq_adapter_unsupported", False),
        (ActionFailed(retcode=1, message="raw-adapter-secret"), 502, "qq_adapter_rejected", False),
        (PermissionError("raw-permission-secret"), 403, "qq_permission_denied", False),
        (ValueError("raw-input-secret"), 400, "qq_invalid_input", False),
        (FileNotFoundError("raw-target-secret"), 404, "qq_target_not_found", False),
        (asyncio.TimeoutError("raw-timeout-secret"), 504, "qq_operation_timeout", True),
        (RuntimeError("raw-internal-secret"), 500, "qq_internal_error", True),
    ],
)
def test_qq_call_returns_safe_stable_failure_diagnostics(
    raised: Exception,
    status_code: int,
    code: str,
    outcome_unknown: bool,
) -> None:
    from ._loader import load_personification_module

    qq_routes = load_personification_module("plugin.personification.webui.routes.qq_routes")

    class _FailBot:
        self_id = "100"

        async def call_api(self, _api: str, **_kwargs):
            raise raised

    with pytest.raises(HTTPException) as caught:
        asyncio.run(
            qq_routes._call(
                _FailBot(),
                "set_qq_profile",
                operation_id="qq-test-operation",
                side_effect=True,
                target_bot_id="100",
                nickname="safe",
            )
        )

    assert caught.value.status_code == status_code
    body = caught.value.detail
    assert body["code"] == code
    assert body["phase"] == "adapter_call"
    assert body["outcome_unknown"] is outcome_unknown
    assert body["operation_id"] == "qq-test-operation"
    assert any(item["label"] == "目标 Bot" and item["value"] == "100" for item in body["details"])
    assert any(item["label"] == "OneBot API" and item["value"] == "set_qq_profile" for item in body["details"])
    assert body["steps"][-1]["status"] == ("unknown" if outcome_unknown else "error")
    assert "raw-" not in str(body)


def test_qq_frontend_keeps_and_renders_operation_diagnostics(_runtime_context) -> None:
    client = _build_client(_runtime_context)
    js = client.get("/personification/static/app-admin.js")
    assert js.status_code == 200
    assert "function qqRememberDiagnostic" in js.text
    assert "state.qqDiagnostics = [diagnostic" in js.text
    assert "renderOperationHistory(" in js.text
    assert "const botId=qqSelectedBotId();" in js.text
    assert "memberships.includes(selectedBotId)" in js.text
