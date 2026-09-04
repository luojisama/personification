from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from ._loader import load_personification_module


quota = load_personification_module("plugin.personification.core.subscription_quota")


class _Response:
    status_code = 200
    headers: dict[str, str] = {}
    content = b"{}"

    def json(self):  # noqa: ANN201
        return {
            "status_code": 200,
            "header": {},
            "body": json.dumps(
                {
                    "secondary": {
                        "limit_window_seconds": 604800,
                        "used_percent": 20,
                        "reset_at": "2026-09-11T00:00:00Z",
                    },
                    "primary": {
                        "limit_window_seconds": 18000,
                        "remaining_percent": 65,
                        "reset_at": 1788480000,
                    },
                }
            ),
        }


class _Client:
    calls: list[tuple[str, dict, dict]] = []

    def __init__(self, **kwargs):  # noqa: ANN003
        self.kwargs = kwargs

    async def __aenter__(self):  # noqa: ANN204
        return self

    async def __aexit__(self, *_args):  # noqa: ANN002, ANN204
        return False

    async def post(self, url, *, headers, json):  # noqa: ANN001, A002, ANN201
        self.calls.append((url, headers, json))
        return _Response()


def _config(**updates):  # noqa: ANN001, ANN201
    provider = {
        "name": "subscription-main",
        "api_type": "openai",
        "model": "gpt-test",
        "enabled": True,
        "account_mode": "subscription_proxy",
        "subscription_quota_kind": "codex_wham_proxy",
        "subscription_management_url": "https://quota.example.test/base",
        "subscription_auth_index": "auth-1",
        "subscription_management_key": "top-secret-management-key",
    }
    provider.update(updates)
    return SimpleNamespace(personification_api_pools=[provider])


def test_subscription_quota_uses_fixed_upstream_and_classifies_by_window_seconds(monkeypatch) -> None:  # noqa: ANN001
    quota.reset_subscription_quota_cache_for_testing()
    _Client.calls.clear()

    async def _valid(_endpoint):  # noqa: ANN001
        return True, ""

    monkeypatch.setattr(quota, "_validate_resolved_endpoint", _valid)
    result = asyncio.run(
        quota.query_subscription_quotas(
            _config(), now=1000.0, client_factory=_Client
        )
    )

    assert result["items"][0]["status"] == "available"
    windows = {item["window_type"]: item for item in result["items"][0]["windows"]}
    assert windows["five_hour"]["used_percent"] == 35.0
    assert windows["weekly"]["remaining_percent"] == 80.0
    endpoint, headers, payload = _Client.calls[0]
    assert endpoint == "https://quota.example.test/base/v0/management/api-call"
    assert payload["url"] == "https://chatgpt.com/backend-api/wham/usage"
    assert payload["method"] == "GET"
    assert headers["Authorization"].startswith("Bearer ")
    rendered = json.dumps(result, ensure_ascii=False)
    assert "top-secret-management-key" not in rendered
    assert "quota.example.test" not in rendered


def test_non_subscription_routes_are_absent() -> None:
    result = asyncio.run(
        quota.query_subscription_quotas(
            _config(account_mode="api_key"), now=1000.0, client_factory=_Client
        )
    )
    assert result["items"] == []
    assert result["diagnostic_code"] == "subscription_quota_not_configured"


def test_management_endpoint_rejects_userinfo_public_http_and_private_resolution(monkeypatch) -> None:  # noqa: ANN001
    assert quota._normalize_management_endpoint("https://user:pass@example.com")[1].endswith(
        "userinfo_forbidden"
    )
    assert quota._normalize_management_endpoint("http://example.com")[1].endswith(
        "https_required"
    )

    monkeypatch.setattr(quota, "_resolve_host_addresses", lambda _host, _port: ["192.168.1.20"])
    ok, code = asyncio.run(
        quota._validate_resolved_endpoint("https://example.com/v0/management/api-call")
    )
    assert ok is False
    assert code == "subscription_management_private_address_forbidden"


def test_quota_cache_and_force_refresh_cooldown(monkeypatch) -> None:  # noqa: ANN001
    quota.reset_subscription_quota_cache_for_testing()
    _Client.calls.clear()

    async def _valid(_endpoint):  # noqa: ANN001
        return True, ""

    monkeypatch.setattr(quota, "_validate_resolved_endpoint", _valid)
    first = asyncio.run(quota.query_subscription_quotas(_config(), now=1000.0, client_factory=_Client))
    cached = asyncio.run(quota.query_subscription_quotas(_config(), now=1010.0, client_factory=_Client))
    forced = asyncio.run(
        quota.query_subscription_quotas(_config(), force=True, now=1020.0, client_factory=_Client)
    )

    assert first["items"][0]["cached"] is False
    assert cached["items"][0]["cached"] is True
    assert forced["items"][0]["refresh_diagnostic_code"] == "subscription_force_refresh_cooldown"
    assert len(_Client.calls) == 1


def test_management_key_rotation_invalidates_the_quota_cache(monkeypatch) -> None:  # noqa: ANN001
    quota.reset_subscription_quota_cache_for_testing()
    _Client.calls.clear()

    async def _valid(_endpoint):  # noqa: ANN001
        return True, ""

    monkeypatch.setattr(quota, "_validate_resolved_endpoint", _valid)
    asyncio.run(quota.query_subscription_quotas(_config(), now=1000.0, client_factory=_Client))
    rotated = asyncio.run(
        quota.query_subscription_quotas(
            _config(subscription_management_key="rotated-management-key"),
            now=1010.0,
            client_factory=_Client,
        )
    )

    assert rotated["items"][0]["cached"] is False
    assert len(_Client.calls) == 2
    assert "rotated-management-key" not in json.dumps(rotated, ensure_ascii=False)


def test_auth_failure_and_unknown_usage_shape_return_diagnostics_without_fake_windows(monkeypatch) -> None:  # noqa: ANN001
    quota.reset_subscription_quota_cache_for_testing()

    async def _valid(_endpoint):  # noqa: ANN001
        return True, ""

    monkeypatch.setattr(quota, "_validate_resolved_endpoint", _valid)

    class _AuthResponse(_Response):
        status_code = 401

    class _AuthClient(_Client):
        async def post(self, url, *, headers, json):  # noqa: ANN001, A002, ANN201
            return _AuthResponse()

    auth_failed = asyncio.run(
        quota.query_subscription_quotas(_config(), now=2000.0, client_factory=_AuthClient)
    )
    assert auth_failed["items"][0]["status"] == "auth_failed"
    assert auth_failed["items"][0]["windows"] == []

    quota.reset_subscription_quota_cache_for_testing()

    class _UnknownShapeResponse(_Response):
        def json(self):  # noqa: ANN201
            return {"status_code": 200, "body": "{}"}

    class _UnknownShapeClient(_Client):
        async def post(self, url, *, headers, json):  # noqa: ANN001, A002, ANN201
            return _UnknownShapeResponse()

    unsupported = asyncio.run(
        quota.query_subscription_quotas(
            _config(),
            now=3000.0,
            client_factory=_UnknownShapeClient,
        )
    )
    assert unsupported["items"][0]["status"] == "unsupported"
    assert unsupported["items"][0]["diagnostic_code"] == "subscription_usage_schema_unsupported"
    assert unsupported["items"][0]["windows"] == []
