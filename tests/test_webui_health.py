from __future__ import annotations

from ._loader import load_personification_module

# 复用 smoke fixture + 登录
from .test_webui_smoke import _build_client, _login_as_admin, _runtime_context  # noqa: F401


def _statuses(body: dict) -> dict:
    out = {}
    for cat in body["categories"]:
        for c in cat["checks"]:
            out[c["key"]] = c["status"]
    return out


def test_health_check_overall_structure(_runtime_context) -> None:
    cfg = _runtime_context.plugin_config
    cfg.personification_global_enabled = True
    cfg.personification_api_pools = [
        {"name": "main", "api_type": "openai", "model": "gpt-4o", "api_key": "k", "api_url": "u", "enabled": True, "priority": 1}
    ]
    client = _build_client(_runtime_context)
    _login_as_admin(client, _runtime_context)
    res = client.get("/personification/api/health/check")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["overall"] in ("ok", "warn", "error")
    assert "summary" in body and "categories" in body
    assert len(body["categories"]) >= 10
    st = _statuses(body)
    # superusers 已配置（fixture 注入 10001）→ ok
    assert st["superusers"] == "ok"
    # provider 已配置 → ok
    assert st["api_pools"] == "ok"


def test_health_flags_tts_enabled_without_key(_runtime_context) -> None:
    cfg = _runtime_context.plugin_config
    cfg.personification_tts_enabled = True
    cfg.personification_tts_api_key = ""
    cfg.personification_tts_api_url = ""
    client = _build_client(_runtime_context)
    _login_as_admin(client, _runtime_context)
    body = client.get("/personification/api/health/check").json()
    st = _statuses(body)
    assert st["tts"] == "error"
    assert body["overall"] == "error"


def test_health_flags_no_enabled_groups(_runtime_context) -> None:
    cfg = _runtime_context.plugin_config
    cfg.personification_whitelist = []
    client = _build_client(_runtime_context)
    _login_as_admin(client, _runtime_context)
    body = client.get("/personification/api/health/check").json()
    st = _statuses(body)
    assert st["group_whitelist"] == "warn"


def test_health_requires_auth(_runtime_context) -> None:
    client = _build_client(_runtime_context)
    assert client.get("/personification/api/health/check").status_code == 401
