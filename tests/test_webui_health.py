from __future__ import annotations

from types import SimpleNamespace

import pytest

from ._loader import load_personification_module

from .test_webui_smoke import _build_client, _login_as_admin, _runtime_context  # noqa: F401

diagnostics = load_personification_module("plugin.personification.core.diagnostics")
ai_routes = load_personification_module("plugin.personification.core.ai_routes")
visual_capabilities = load_personification_module("plugin.personification.core.visual_capabilities")


class _FakeResp:
    def __init__(self, content="红绿蓝黄", raw=None):
        self.content = content
        self.raw = raw
        self.finish_reason = "stop"


class _FakeCaller:
    def __init__(self, content="红绿蓝黄"):
        self._c = content

    async def chat_with_tools(self, messages, tools, use_builtin_search):
        return _FakeResp(self._c)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """屏蔽真实网络/模型调用：模型探测用假 caller，连通探测直接返回可达。"""
    monkeypatch.setattr(ai_routes, "build_single_provider_caller", lambda cfg, prov, **kw: _FakeCaller())
    monkeypatch.setattr(ai_routes, "list_primary_providers", lambda cfg, lg: [
        {"name": "main", "api_type": "openai", "model": "gpt-4o", "priority": 1}
    ])

    async def _reachable(url, *, timeout=8):
        return True, "HTTP 200"

    monkeypatch.setattr(diagnostics, "_http_reachable", _reachable)
    monkeypatch.setattr(diagnostics, "_CACHE", {"result": None})  # 每个测试清空体检缓存


def _set_csrf(client) -> None:
    csrf = client.cookies.get("personification_webui_csrf", "")
    if csrf:
        client.headers["X-Personification-CSRF"] = csrf


def _statuses(body: dict) -> dict:
    out = {}
    for cat in body["categories"]:
        for c in cat["checks"]:
            out[c["key"]] = c
    return out


def test_health_does_live_model_call(_runtime_context) -> None:
    client = _build_client(_runtime_context)
    _login_as_admin(client, _runtime_context)
    body = client.get("/personification/api/health/check").json()
    assert body["live"] is True
    st = _statuses(body)
    # 模型探测产生 per-provider 项，且为真实调用结果
    model_keys = [k for k in st if k.startswith("model_")]
    assert model_keys, "应有逐 provider 的模型调用探测项"
    assert st[model_keys[0]]["status"] == "ok"
    assert "调用正常" in st[model_keys[0]]["detail"]


def test_health_model_call_failure_is_error(_runtime_context, monkeypatch) -> None:
    class _Boom:
        async def chat_with_tools(self, messages, tools, use_builtin_search):
            raise RuntimeError("connection refused")

    monkeypatch.setattr(ai_routes, "build_single_provider_caller", lambda cfg, prov, **kw: _Boom())
    client = _build_client(_runtime_context)
    _login_as_admin(client, _runtime_context)
    body = client.get("/personification/api/health/check", params={"refresh": "true"}).json()
    st = _statuses(body)
    model_keys = [k for k in st if k.startswith("model_")]
    assert st[model_keys[0]]["status"] == "error"
    assert "调用失败" in st[model_keys[0]]["detail"]
    assert body["overall"] == "error"


def test_health_db_is_live_query(_runtime_context) -> None:
    client = _build_client(_runtime_context)
    _login_as_admin(client, _runtime_context)
    st = _statuses(client.get("/personification/api/health/check").json())
    assert st["db"]["status"] == "ok"
    assert "查询正常" in st["db"]["detail"]


def test_health_only_runs_single_category(_runtime_context) -> None:
    client = _build_client(_runtime_context)
    _login_as_admin(client, _runtime_context)
    body = client.get("/personification/api/health/check", params={"only": "存储"}).json()
    assert body["partial"] is True
    assert [c["name"] for c in body["categories"]] == ["存储"]


def test_health_caches_full_run_and_serves_fast(_runtime_context, monkeypatch) -> None:
    diag = load_personification_module("plugin.personification.core.diagnostics")
    monkeypatch.setattr(diag, "_CACHE", {"result": None})
    client = _build_client(_runtime_context)
    _login_as_admin(client, _runtime_context)
    # refresh 真跑并写缓存
    first = client.get("/personification/api/health/check", params={"refresh": "true"}).json()
    assert first["cached"] is False
    # 默认读缓存
    second = client.get("/personification/api/health/check").json()
    assert second["cached"] is True
    assert second["generated_at"] == first["generated_at"]


def test_health_includes_llm_subconfig_category(_runtime_context) -> None:
    client = _build_client(_runtime_context)
    _login_as_admin(client, _runtime_context)
    body = client.get("/personification/api/health/check", params={"refresh": "true"}).json()
    assert "LLM 子模型" in [c["name"] for c in body["categories"]]
    assert "视觉能力" in [c["name"] for c in body["categories"]]


def test_health_visual_probe_refreshes_stale_negative_cache(_runtime_context) -> None:
    visual_capabilities.set_visual_capability(
        visual_capabilities.VISUAL_ROUTE_REPLY_PLAIN,
        "openai",
        "gpt-4o",
        False,
        source="unit",
    )
    client = _build_client(_runtime_context)
    _login_as_admin(client, _runtime_context)
    body = client.get("/personification/api/health/check", params={"only": "视觉能力"}).json()
    st = _statuses(body)
    assert st["vision_reply_plain"]["status"] == "ok"
    assert visual_capabilities.provider_supports_vision(
        "openai",
        "gpt-4o",
        route_name=visual_capabilities.VISUAL_ROUTE_REPLY_PLAIN,
    ) is True


def test_interaction_test_requires_configured_target(_runtime_context) -> None:
    cfg = _runtime_context.plugin_config
    cfg.personification_webui_test_group_id = ""
    cfg.personification_webui_test_user_id = ""
    client = _build_client(_runtime_context)
    _login_as_admin(client, _runtime_context)
    _set_csrf(client)
    res = client.post("/personification/api/health/interaction-test", json={"target": "group"})
    assert res.status_code == 400


def test_health_requires_auth(_runtime_context) -> None:
    client = _build_client(_runtime_context)
    assert client.get("/personification/api/health/check").status_code == 401
