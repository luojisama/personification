from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from ._loader import load_personification_module


@pytest.fixture
def _runtime_context(tmp_path: Path, monkeypatch):
    """初始化 data_store + WebUI 运行时上下文，绑定到 tmp_path。"""
    data_store = load_personification_module("plugin.personification.core.data_store")
    paths = load_personification_module("plugin.personification.core.paths")
    cfg = SimpleNamespace(personification_data_dir=str(tmp_path))
    monkeypatch.setattr(paths, "get_data_dir", lambda _cfg=None: tmp_path)
    data_store.init_data_store(cfg)

    sent_messages: list[dict] = []

    class _FakeBot:
        async def call_api(self, _name: str, **kwargs):
            sent_messages.append(kwargs)
            return {"message_id": 1}

    def _get_bots() -> dict:
        return {"100": _FakeBot()}

    app_module = load_personification_module("plugin.personification.webui.app")
    app_module.set_runtime_context(
        plugin_config=cfg,
        superusers={"10001"},
        get_bots=_get_bots,
        logger=SimpleNamespace(info=lambda *_a, **_k: None, warning=lambda *_a, **_k: None),
    )
    return SimpleNamespace(plugin_config=cfg, sent=sent_messages, app_module=app_module)


def _build_client(runtime_context):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    router = runtime_context.app_module.build_router()
    app.include_router(router)
    return TestClient(app)


def test_health_endpoint(_runtime_context) -> None:
    client = _build_client(_runtime_context)
    res = client.get("/personification/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}


def test_me_unauthenticated_returns_401(_runtime_context) -> None:
    client = _build_client(_runtime_context)
    res = client.get("/personification/api/auth/me")
    assert res.status_code == 401


def test_login_non_admin_rejected(_runtime_context) -> None:
    client = _build_client(_runtime_context)
    res = client.post("/personification/api/auth/login", json={"qq": "99999"})
    assert res.status_code == 403


def test_login_verify_full_flow(_runtime_context) -> None:
    client = _build_client(_runtime_context)
    res = client.post("/personification/api/auth/login", json={"qq": "10001"})
    assert res.status_code == 200, res.text
    assert _runtime_context.sent, "Bot 应收到一次 send_private_msg"
    last = _runtime_context.sent[-1]
    msg = str(last.get("message", ""))
    import re

    match = re.search(r"\b(\d{6})\b", msg)
    assert match, f"应在消息中包含 6 位验证码：{msg}"
    code = match.group(1)

    res2 = client.post(
        "/personification/api/auth/verify",
        json={"qq": "10001", "code": code, "device_label": "测试机"},
    )
    assert res2.status_code == 200, res2.text
    assert res2.cookies.get("personification_webui_token")

    # 后续请求必须能识别身份
    res3 = client.get("/personification/api/auth/me")
    assert res3.status_code == 200
    body = res3.json()
    assert body["qq"] == "10001"
    assert body["label"] == "测试机"


def test_verify_wrong_code_rejected(_runtime_context) -> None:
    client = _build_client(_runtime_context)
    client.post("/personification/api/auth/login", json={"qq": "10001"})
    res = client.post(
        "/personification/api/auth/verify",
        json={"qq": "10001", "code": "000000", "device_label": ""},
    )
    assert res.status_code == 403


def test_config_entries_requires_auth(_runtime_context) -> None:
    client = _build_client(_runtime_context)
    res = client.get("/personification/api/config/entries")
    assert res.status_code == 401


def _login_as_admin(client, runtime_context) -> None:
    runtime_context.sent.clear()
    res = client.post("/personification/api/auth/login", json={"qq": "10001"})
    assert res.status_code == 200, res.text
    import re

    code = re.search(r"\b(\d{6})\b", str(runtime_context.sent[-1].get("message", ""))).group(1)
    res2 = client.post(
        "/personification/api/auth/verify",
        json={"qq": "10001", "code": code, "device_label": "测试"},
    )
    assert res2.status_code == 200, res2.text
    csrf = client.cookies.get("personification_webui_csrf", "")
    if csrf:
        client.headers["X-Personification-CSRF"] = csrf


def test_config_entries_authenticated_returns_groups(_runtime_context) -> None:
    client = _build_client(_runtime_context)
    _login_as_admin(client, _runtime_context)
    res = client.get("/personification/api/config/entries")
    assert res.status_code == 200
    body = res.json()
    assert body["entries"], "应返回 ConfigEntry 列表"
    assert any(g == "核心开关" for g in body["groups"])
    # 抽样验证一个 entry 的形态
    sample = next(e for e in body["entries"] if e["field_name"] == "personification_global_enabled")
    assert sample["kind"] == "toggle"
    assert sample["required"] is True
    assert sample["group"] == "核心开关"


def test_config_value_update_double_writes(_runtime_context, monkeypatch, tmp_path) -> None:
    # 重定向 .env.prod 探测到 tmp_path
    env_writer = load_personification_module("plugin.personification.core.env_writer")
    env_file = tmp_path / ".env.prod"
    env_file.write_text("personification_agent_max_steps=5\n", encoding="utf-8")
    monkeypatch.setattr(env_writer, "_resolve_dotenv_target", lambda: env_file)
    monkeypatch.setattr(env_writer, "read_env_file_value", lambda key: "")

    _runtime_context.plugin_config.personification_agent_max_steps = 5

    client = _build_client(_runtime_context)
    _login_as_admin(client, _runtime_context)

    res = client.post(
        "/personification/api/config/value",
        json={"field_name": "personification_agent_max_steps", "value": "8"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["success"] is True
    assert body["errors"] == []
    # .env.prod 已写入
    from dotenv import dotenv_values

    assert dotenv_values(str(env_file))["personification_agent_max_steps"] == "8"
    # env.json 也已写入
    import json as _json

    env_json_path = tmp_path / "env.json"
    assert env_json_path.exists()
    assert _json.loads(env_json_path.read_text(encoding="utf-8"))["personification_agent_max_steps"] == 8
    # 运行时也更新了
    assert _runtime_context.plugin_config.personification_agent_max_steps == 8


def test_config_value_validation_rejects_bad_value(_runtime_context) -> None:
    client = _build_client(_runtime_context)
    _login_as_admin(client, _runtime_context)
    res = client.post(
        "/personification/api/config/value",
        json={"field_name": "personification_agent_max_steps", "value": "not-a-number"},
    )
    assert res.status_code == 400


def test_devices_listing_and_revoke(_runtime_context) -> None:
    client = _build_client(_runtime_context)
    _login_as_admin(client, _runtime_context)
    res = client.get("/personification/api/auth/devices")
    assert res.status_code == 200
    body = res.json()
    assert len(body["devices"]) == 1
    current_id = body["current_device_id"]
    # 不允许撤销当前设备外的设备（这里只有一个，撤销 404 路径用伪 id）
    res2 = client.delete("/personification/api/auth/devices/not_my_device")
    assert res2.status_code == 404
    # 撤销自己（允许）
    res3 = client.delete(f"/personification/api/auth/devices/{current_id}")
    assert res3.status_code == 200
    # 再访问应 401
    res4 = client.get("/personification/api/auth/me")
    assert res4.status_code == 401
