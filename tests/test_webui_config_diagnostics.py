from __future__ import annotations

from types import SimpleNamespace

import httpx

from ._loader import load_personification_module
from .test_webui_smoke import _build_client, _login_as_admin, _runtime_context  # noqa: F401


def test_config_value_reports_persistence_and_runtime_reload(
    _runtime_context, monkeypatch  # noqa: ANN001
) -> None:
    config_routes = load_personification_module("plugin.personification.webui.routes.config_routes")
    monkeypatch.setattr(config_routes, "_schedule_diagnostics_warm", lambda _runtime: None)
    reload_calls: list[str] = []
    runtime = _runtime_context.app_module.get_runtime_context()
    runtime.runtime_bundle = SimpleNamespace(
        reload_runtime_services=lambda: reload_calls.append("reload")
    )
    client = _build_client(_runtime_context)
    _login_as_admin(client, _runtime_context)

    response = client.post(
        "/personification/api/config/value",
        json={"field_name": "personification_agent_max_steps", "value": "7"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["success"] is True
    assert body["errors"] == []
    assert body["env_json_path"]
    assert body["dotenv_path"] is None
    assert body["new_value"] == 7
    assert body["diagnostic"]["code"] == "config_value_updated"
    steps = {item["key"]: item for item in body["diagnostic"]["steps"]}
    assert steps["persist_config"]["status"] == "ok"
    assert steps["runtime_config_sync"]["status"] == "ok"
    assert steps["runtime_reload"]["status"] == "ok"
    assert any(item["label"] == ".env.prod / .env" for item in steps["persist_config"]["details"])
    assert reload_calls == ["reload"]


def test_config_value_unexpected_exception_is_structured_and_safe(
    _runtime_context, monkeypatch  # noqa: ANN001
) -> None:
    config_routes = load_personification_module("plugin.personification.webui.routes.config_routes")

    def _raise(*_args, **_kwargs):  # noqa: ANN002, ANN003, ANN202
        raise RuntimeError("https://api.example.test/models?api_key=super-secret")

    monkeypatch.setattr(config_routes.env_writer, "write_both", _raise)
    client = _build_client(_runtime_context)
    _login_as_admin(client, _runtime_context)

    response = client.post(
        "/personification/api/config/value",
        json={"field_name": "personification_agent_max_steps", "value": 6},
    )

    assert response.status_code == 500
    diagnostic = response.json()["detail"]
    assert diagnostic["code"] == "config_value_persist_exception"
    assert diagnostic["trace_id"]
    assert diagnostic["details"] == [
        {"label": "异常类型", "value": "RuntimeError", "status": "error"}
    ]
    rendered = str(diagnostic)
    assert "api.example.test" not in rendered
    assert "super-secret" not in rendered


def test_apply_recommended_reports_unknown_field_skip_reason(_runtime_context) -> None:  # noqa: ANN001
    client = _build_client(_runtime_context)
    _login_as_admin(client, _runtime_context)

    response = client.post(
        "/personification/api/config/apply-recommended",
        json={"fields": ["personification_not_a_recommended_field"]},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["applied"] == []
    assert body["skipped"] == [
        {
            "field_name": "personification_not_a_recommended_field",
            "reason": "不在推荐默认值列表",
        }
    ]
    assert body["diagnostic"]["code"] == "config_recommended_failed"
    assert any(
        item["label"] == "跳过 personification_not_a_recommended_field"
        and item["value"] == "不在推荐默认值列表"
        for item in body["diagnostic"]["details"]
    )


def test_provider_model_probe_classifies_failures_without_raw_exception_data(
    _runtime_context, monkeypatch  # noqa: ANN001
) -> None:
    config_routes = load_personification_module("plugin.personification.webui.routes.config_routes")
    request = httpx.Request(
        "GET",
        "https://user:password@api.example.test/models?api_key=super-secret",
    )
    cases = [
        (
            httpx.HTTPStatusError(
                "auth failed at raw URL",
                request=request,
                response=httpx.Response(401, request=request),
            ),
            "provider_model_probe_auth_failed",
        ),
        (httpx.ReadTimeout("raw timeout URL", request=request), "provider_model_probe_timeout"),
        (httpx.ConnectError("raw network URL", request=request), "provider_model_probe_network_failed"),
        (
            httpx.HTTPStatusError(
                "HTTP failed at raw URL",
                request=request,
                response=httpx.Response(503, request=request),
            ),
            "provider_model_probe_http_failed",
        ),
        (ValueError("parse failed at https://api.example.test/?token=super-secret"), "provider_model_probe_parse_failed"),
        (RuntimeError("unexpected https://api.example.test/?token=super-secret"), "provider_model_probe_exception"),
    ]
    client = _build_client(_runtime_context)
    _login_as_admin(client, _runtime_context)

    for raised, expected_code in cases:
        async def _fail(_provider, error=raised):  # noqa: ANN001, ANN202
            raise error

        monkeypatch.setattr(config_routes, "_probe_http_models", _fail)
        response = client.post(
            "/personification/api/config/provider-models",
            json={
                "provider": {
                    "api_type": "openai",
                    "api_url": "https://api.example.test/v1?api_key=super-secret",
                    "api_key": "super-secret",
                }
            },
        )

        assert response.status_code == 502
        diagnostic = response.json()["detail"]
        assert diagnostic["code"] == expected_code
        assert diagnostic["steps"]
        rendered = str(diagnostic)
        assert "api.example.test" not in rendered
        assert "super-secret" not in rendered


def test_provider_model_probe_success_preserves_fields_and_adds_diagnostic(
    _runtime_context,  # noqa: ANN001
) -> None:
    client = _build_client(_runtime_context)
    _login_as_admin(client, _runtime_context)

    response = client.post(
        "/personification/api/config/provider-models",
        json={"provider": {"api_type": "openai_codex", "model": "gpt-test-codex"}},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["api_type"] == "openai_codex"
    assert body["source"] == "local_cache"
    assert body["manual_allowed"] is True
    assert body["models"]
    assert body["diagnostic"]["code"] == "provider_model_probe_complete"
    assert body["diagnostic"]["ok"] is True


def test_remote_provider_probe_does_not_return_full_endpoint(_runtime_context, monkeypatch) -> None:  # noqa: ANN001
    config_routes = load_personification_module("plugin.personification.webui.routes.config_routes")

    async def _success(_provider):  # noqa: ANN001, ANN202
        return ([{"id": "safe-model", "label": "safe-model"}], "https://private.example.test/v1/models?token=secret")

    monkeypatch.setattr(config_routes, "_probe_http_models", _success)
    client = _build_client(_runtime_context)
    _login_as_admin(client, _runtime_context)
    response = client.post(
        "/personification/api/config/provider-models",
        json={"provider": {"api_type": "openai", "api_url": "https://private.example.test/v1", "api_key": "secret"}},
    )
    assert response.status_code == 200, response.text
    assert response.json()["source"] == "remote_models"
    assert "private.example.test" not in response.text
    assert "secret" not in response.text
