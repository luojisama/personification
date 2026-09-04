"""Tests for CLI routes and removed-provider caller boundaries."""
from __future__ import annotations

import asyncio
import json
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Optional
from uuid import uuid4

import httpx
import pytest

from plugin.personification.core import ai_routes, gemini_transport, provider_router
from plugin.personification.skills.skillpacks.tool_caller.scripts import impl as caller_impl
from plugin.personification.skills.skillpacks.image_gen.scripts.main import (
    build_image_gen_nanobanan_tool,
    build_image_gen_tool,
)


@dataclass
class _DummyConfig:
    personification_api_type: str = "openai"
    personification_api_key: str = ""
    personification_api_url: str = ""
    personification_model: str = ""
    personification_thinking_mode: str = "none"
    personification_codex_auth_path: str = ""
    personification_gemini_cli_auth_path: str = ""
    personification_gemini_cli_project: str = ""
    personification_antigravity_cli_auth_path: str = ""
    personification_antigravity_cli_project: str = ""
    personification_gemini_auth_mode: str = "auto"
    personification_api_pools: object = None


class _Logger:
    def info(self, *_args, **_kwargs) -> None:
        return None

    def warning(self, *_args, **_kwargs) -> None:
        return None

    def error(self, *_args, **_kwargs) -> None:
        return None


def _make_workspace_temp_dir(prefix: str) -> Path:
    base_dir = Path(__file__).resolve().parent / ".tmp"
    base_dir.mkdir(parents=True, exist_ok=True)
    temp_dir = base_dir / f"{prefix}{uuid4().hex}"
    temp_dir.mkdir(parents=True, exist_ok=False)
    return temp_dir


def test_normalize_api_type_gemini_cli_aliases() -> None:
    assert caller_impl._normalize_api_type("gemini_cli") == "gemini_cli"
    assert caller_impl._normalize_api_type("gemini-cli") == "gemini_cli"
    assert caller_impl._normalize_api_type("GEMINICLI") == "gemini_cli"


def test_normalize_api_type_antigravity_cli_aliases() -> None:
    assert caller_impl._normalize_api_type("antigravity_cli") == "antigravity_cli"
    assert caller_impl._normalize_api_type("antigravity-cli") == "antigravity_cli"
    assert caller_impl._normalize_api_type("agy") == "antigravity_cli"
    assert provider_router.normalize_api_type("agy_cli") == "antigravity_cli"


@pytest.mark.parametrize("api_type", ("claude_code", "claude-code", "ClaudeCode", "claude_cli", "claude-cli"))
def test_normalize_api_type_removed_aliases_are_tombstones(api_type: str) -> None:
    assert caller_impl._normalize_api_type(api_type) == "provider_type_removed"
    assert provider_router.normalize_api_type(api_type) == "provider_type_removed"
    assert ai_routes._normalize_api_type(api_type) == "provider_type_removed"


def test_normalize_api_type_keeps_existing_routes() -> None:
    assert caller_impl._normalize_api_type("openai") == "openai"
    assert caller_impl._normalize_api_type("anthropic") == "anthropic"
    assert caller_impl._normalize_api_type("gemini_official") == "gemini_official"
    assert caller_impl._normalize_api_type("openai_codex") == "openai_codex"
    assert caller_impl._normalize_api_type("codex") == "openai_codex"


def test_openai_and_gemini_base_urls_fill_version_suffixes() -> None:
    assert caller_impl._normalize_openai_base_url("https://anti.zellon.me") == "https://anti.zellon.me/v1"
    assert caller_impl._normalize_openai_base_url("https://anti.zellon.me/v1/chat/completions") == "https://anti.zellon.me/v1"
    assert caller_impl._normalize_gemini_base_url("https://anti.zellon.me") == "https://anti.zellon.me/v1beta"
    assert (
        caller_impl._normalize_gemini_base_url(
            "https://anti.zellon.me/v1beta/models/gemini-3-flash-agent:generateContent"
        )
        == "https://anti.zellon.me/v1beta"
    )


def test_custom_gemini_endpoint_uses_google_header_and_v1beta(monkeypatch) -> None:
    captured: dict = {}

    class _Response:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self):  # noqa: ANN201
            return {
                "candidates": [
                    {"content": {"parts": [{"text": "ok"}], "role": "model"}, "finishReason": "STOP"}
                ],
                "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1, "totalTokenCount": 2},
            }

    class _Client:
        def __init__(self, **kwargs) -> None:  # noqa: ANN001
            captured["client_kwargs"] = kwargs

        async def __aenter__(self):  # noqa: ANN201
            return self

        async def __aexit__(self, *_args) -> None:
            return None

        async def post(self, url, headers=None, params=None, json=None):  # noqa: ANN001, ANN201
            captured["url"] = url
            captured["headers"] = headers or {}
            captured["params"] = params or {}
            captured["json"] = json or {}
            return _Response()

    monkeypatch.setattr(caller_impl.httpx, "AsyncClient", _Client)
    caller = caller_impl.GeminiToolCaller(
        api_key="sk-test",
        base_url="https://anti.zellon.me",
        model="gemini-3-flash-agent",
        thinking_mode="none",
        timeout=77,
    )

    response = asyncio.run(caller.chat_with_tools([{"role": "user", "content": "hi"}], [], False))

    assert captured["url"] == "https://anti.zellon.me/v1beta/models/gemini-3-flash-agent:generateContent"
    assert captured["headers"]["x-goog-api-key"] == "sk-test"
    assert "Authorization" not in captured["headers"]
    assert captured["params"] == {}
    assert captured["client_kwargs"]["timeout"].read == 77
    assert captured["client_kwargs"]["follow_redirects"] is False
    assert "generationConfig" not in captured["json"]
    assert response.content == "ok"
    assert response.usage["total_tokens"] == 2


def test_gemini_function_schema_uses_compatibility_subset(monkeypatch) -> None:  # noqa: ANN001
    captured: dict = {}

    class _Response:
        status_code = 200

        def json(self):  # noqa: ANN201
            return {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}

    class _Client:
        def __init__(self, **_kwargs) -> None:
            pass

        async def __aenter__(self):  # noqa: ANN201
            return self

        async def __aexit__(self, *_args) -> None:
            return None

        async def post(self, _url, headers=None, params=None, json=None):  # noqa: ANN001, ANN201
            captured["json"] = json or {}
            return _Response()

    monkeypatch.setattr(caller_impl.httpx, "AsyncClient", _Client)
    caller = caller_impl.GeminiToolCaller(
        api_key="sk-test",
        base_url="https://anti.zellon.me",
        model="gemini-3-flash-agent",
    )
    tools = [
        {
            "type": "function",
            "function": {
                "name": "safe_lookup",
                "description": "",
                "parameters": {
                    "type": "object",
                    "title": "legacy title",
                    "additionalProperties": False,
                    "properties": {
                        "query": {
                            "type": ["string", "null"],
                            "description": "query text",
                            "default": "ignored",
                            "examples": ["private example"],
                        },
                    },
                    "required": ["query", "missing"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "optional_bad_schema",
                "description": "must be skipped without dropping optional fields",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "mode": {
                            "oneOf": [
                                {"type": "string", "enum": ["fast", "deep"]},
                                {"type": "integer", "minimum": 1, "maximum": 2},
                            ]
                        },
                        "bad": {"type": "date", "description": "unsupported type"},
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "required_bad_schema",
                "description": "must be skipped without weakening required args",
                "parameters": {
                    "type": "object",
                    "properties": {"bad": {"type": "date"}},
                    "required": ["bad"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "invalid tool name",
                "description": "must be skipped",
                "parameters": {"type": "object"},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "invalid_root_schema",
                "description": "must also be skipped",
                "parameters": {"type": "string"},
            },
        },
    ]

    response = asyncio.run(caller.chat_with_tools([{"role": "user", "content": "hi"}], tools, False))

    declarations = captured["json"]["tools"][0]["functionDeclarations"]
    assert len(declarations) == 1
    assert response.wire_tools_count == 1
    declaration = declarations[0]
    assert declaration["name"] == "safe_lookup"
    assert declaration["description"] == "safe_lookup"
    parameters = declaration["parameters"]
    assert parameters["type"] == "OBJECT"
    assert parameters["required"] == ["query"]
    assert "title" not in parameters
    assert "additionalProperties" not in parameters
    query = parameters["properties"]["query"]
    assert query == {"type": "STRING", "nullable": True, "description": "query text"}
    assert "default" not in str(parameters)
    assert "private example" not in str(parameters)


def test_gemini_auth_auto_negotiates_only_on_401_and_caches() -> None:
    gemini_transport.clear_gemini_auth_cache()
    calls: list[str] = []

    class _Response:
        def __init__(self, status_code: int) -> None:
            self.status_code = status_code

    async def _send(auth):  # noqa: ANN001, ANN202
        calls.append(auth.mode)
        return _Response(401 if len(calls) == 1 else 200)

    first = asyncio.run(gemini_transport.request_with_gemini_auth(
        endpoint="https://gateway.example/v1beta",
        api_key="secret-a",
        auth_mode="auto",
        send=_send,
    ))
    assert calls == ["x-goog-api-key", "bearer"]
    assert first.mode == "bearer"
    assert first.request_count == 2

    cached_calls: list[str] = []

    async def _send_cached(auth):  # noqa: ANN001, ANN202
        cached_calls.append(auth.mode)
        return _Response(200)

    second = asyncio.run(gemini_transport.request_with_gemini_auth(
        endpoint="https://gateway.example/v1beta",
        api_key="secret-a",
        auth_mode="auto",
        send=_send_cached,
    ))
    assert cached_calls == ["bearer"]
    assert second.request_count == 1


def test_gemini_auth_cache_preserves_case_sensitive_endpoint_path() -> None:
    gemini_transport.clear_gemini_auth_cache()

    class _Response:
        def __init__(self, status_code: int) -> None:
            self.status_code = status_code

    seed_calls: list[str] = []

    async def _seed(auth):  # noqa: ANN001, ANN202
        seed_calls.append(auth.mode)
        return _Response(401 if len(seed_calls) == 1 else 200)

    asyncio.run(gemini_transport.request_with_gemini_auth(
        endpoint="https://Gateway.Example/TenantA/v1beta",
        api_key="case-sensitive-secret",
        auth_mode="auto",
        send=_seed,
    ))
    calls: list[str] = []

    async def _send(auth):  # noqa: ANN001, ANN202
        calls.append(auth.mode)
        return _Response(200)

    asyncio.run(gemini_transport.request_with_gemini_auth(
        endpoint="https://gateway.example/tenanta/v1beta",
        api_key="case-sensitive-secret",
        auth_mode="auto",
        send=_send,
        allow_negotiation=False,
    ))

    assert calls == ["x-goog-api-key"]


def test_gemini_auth_does_not_retry_400_or_single_attempt_401() -> None:
    gemini_transport.clear_gemini_auth_cache()

    class _Response:
        def __init__(self, status_code: int) -> None:
            self.status_code = status_code

    async def _run(status_code: int, *, allow_negotiation: bool) -> list[str]:
        calls: list[str] = []

        async def _send(auth):  # noqa: ANN001, ANN202
            calls.append(auth.mode)
            return _Response(status_code)

        await gemini_transport.request_with_gemini_auth(
            endpoint=f"https://gateway-{status_code}-{allow_negotiation}.example/v1beta",
            api_key="secret-b",
            auth_mode="auto",
            send=_send,
            allow_negotiation=allow_negotiation,
        )
        return calls

    assert asyncio.run(_run(400, allow_negotiation=True)) == ["x-goog-api-key"]
    assert asyncio.run(_run(401, allow_negotiation=False)) == ["x-goog-api-key"]


def test_gemini_auth_uses_one_explicit_credential_carrier() -> None:
    header = gemini_transport.gemini_auth_payload("secret", "x-goog-api-key")
    bearer = gemini_transport.gemini_auth_payload("secret", "bearer")
    query = gemini_transport.gemini_auth_payload("secret", "query_legacy")

    assert header.headers == {"x-goog-api-key": "secret"}
    assert header.params == {}
    assert bearer.headers == {"Authorization": "Bearer secret"}
    assert bearer.params == {}
    assert query.headers == {}
    assert query.params == {"key": "secret"}


def test_gemini_status_error_redacts_legacy_query_key() -> None:
    request = httpx.Request(
        "POST",
        "https://gateway.example/v1beta/models/gemini-test:generateContent?key=legacy-secret",
    )
    response = httpx.Response(
        401,
        headers={"Location": "https://gateway.example/retry?key=legacy-secret"},
        request=request,
    )

    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        gemini_transport.raise_for_gemini_status(
            response,
            auth_mode="bearer",
            request_count=2,
        )

    error = exc_info.value
    assert "legacy-secret" not in str(error)
    assert "legacy-secret" not in str(error.request.url)
    assert "legacy-secret" not in str(error.response.request.url)
    assert "legacy-secret" not in str(error.response.headers)
    assert "location" not in error.response.headers
    assert error.auth_mode == "bearer"
    assert error.request_count == 2


def test_gemini_status_error_exposes_only_allowlisted_upstream_diagnostics() -> None:
    request = httpx.Request(
        "POST",
        "https://gateway.example/v1beta/models/gemini-test:generateContent",
    )
    response = httpx.Response(
        400,
        request=request,
        json={
            "error": {
                "code": 400,
                "status": "INVALID_ARGUMENT",
                "message": (
                    "Function response id does not match the function call id; "
                    "Authorization: Bearer top-secret"
                ),
                "details": [{"reason": "ARBITRARY_PROVIDER_TEXT_top-secret"}],
            }
        },
    )

    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        gemini_transport.raise_for_gemini_status(response)

    error = exc_info.value
    assert error.upstream_status == "INVALID_ARGUMENT"
    assert error.upstream_reason == ""
    assert error.upstream_detail_code == "function_response_mismatch"
    assert "top-secret" not in str(error)
    assert "top-secret" not in str(error.response.headers)
    assert error.response.content == b""


def test_gemini_status_error_does_not_parse_oversized_diagnostic_body() -> None:
    request = httpx.Request(
        "POST",
        "https://gateway.example/v1beta/models/gemini-test:generateContent",
    )
    oversized = {
        "error": {
            "status": "INVALID_ARGUMENT",
            "message": "Function response mismatch " + ("x" * (65 * 1024)),
        }
    }
    response = httpx.Response(400, request=request, json=oversized)

    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        gemini_transport.raise_for_gemini_status(response)

    error = exc_info.value
    assert error.upstream_status == ""
    assert error.upstream_detail_code == "upstream_rejected"
    assert error.response.content == b""


def test_single_provider_caller_receives_gemini_auth_mode_and_timeout() -> None:
    caller = ai_routes.build_single_provider_caller(
        _DummyConfig(personification_api_type="gemini"),
        {
            "api_type": "gemini",
            "api_url": "https://gateway.example",
            "api_key": "secret",
            "model": "gemini-test",
            "gemini_auth_mode": "bearer",
            "timeout": 37,
        },
    )

    assert isinstance(caller, caller_impl.GeminiToolCaller)
    assert caller.auth_mode == "bearer"
    assert caller.timeout == 37


def test_provider_router_caller_receives_gemini_auth_mode_and_timeout() -> None:
    caller = provider_router._build_provider_caller(
        {
            "api_type": "gemini",
            "api_url": "https://gateway.example/v1beta",
            "api_key": "secret",
            "model": "gemini-test",
            "gemini_auth_mode": "bearer",
            "timeout": 41,
        },
        _DummyConfig(),
    )

    assert isinstance(caller, caller_impl.GeminiToolCaller)
    assert caller.auth_mode == "bearer"
    assert caller.timeout == 41


def test_flat_global_fallback_preserves_gemini_auth_mode() -> None:
    config = SimpleNamespace(
        personification_fallback_enabled=True,
        personification_fallback_api_type="gemini",
        personification_fallback_api_url="https://fallback.example/v1beta",
        personification_fallback_api_key="fallback-secret",
        personification_fallback_model="gemini-fallback",
        personification_fallback_auth_path="",
        personification_gemini_auth_mode="bearer",
        personification_api_type="openai",
        personification_api_url="https://primary.example/v1",
        personification_api_key="primary-secret",
        personification_model="primary-model",
        personification_api_pools=[],
    )

    fallback = ai_routes.resolve_global_fallback_provider(config)
    video_fallback = ai_routes.resolve_video_fallback_provider(
        SimpleNamespace(
            **vars(config),
            personification_video_fallback_enabled=True,
            personification_video_fallback_provider="gemini",
            personification_video_fallback_api_url="https://video.example/v1beta",
            personification_video_fallback_api_key="video-secret",
            personification_video_fallback_model="gemini-video",
            personification_video_fallback_auth_path="",
        )
    )

    assert fallback is not None
    assert fallback.provider["gemini_auth_mode"] == "bearer"
    assert video_fallback is not None
    assert video_fallback.provider["gemini_auth_mode"] == "bearer"


def test_non_gemini_route_signatures_ignore_gemini_auth_mode() -> None:
    first = {
        "api_type": "openai",
        "api_url": "https://openai.example/v1",
        "api_key": "secret",
        "model": "model",
        "auth_path": "",
        "gemini_auth_mode": "auto",
    }
    second = {**first, "gemini_auth_mode": "bearer"}

    assert ai_routes._provider_signature(first) == ai_routes._provider_signature(second)
    assert provider_router._provider_signature(first) == provider_router._provider_signature(second)

    gemini_first = {**first, "api_type": "gemini"}
    gemini_second = {**second, "api_type": "gemini"}
    assert ai_routes._provider_signature(gemini_first) != ai_routes._provider_signature(gemini_second)
    assert provider_router._provider_signature(gemini_first) != provider_router._provider_signature(gemini_second)


def test_google_gemini_rest_call_uses_configured_timeout(monkeypatch) -> None:
    captured: dict = {}

    class _Response:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self):  # noqa: ANN201
            return {
                "candidates": [{"content": {"parts": [{"text": "ok"}]}}],
                "usageMetadata": {"totalTokenCount": 1},
            }

    class _Client:
        def __init__(self, **kwargs) -> None:  # noqa: ANN001
            captured["client_kwargs"] = kwargs

        async def __aenter__(self):  # noqa: ANN201
            return self

        async def __aexit__(self, *_args) -> None:
            return None

        async def post(self, url, headers=None, params=None, json=None):  # noqa: ANN001, ANN201
            captured.update(url=url, headers=headers or {}, params=params or {}, json=json or {})
            return _Response()

    monkeypatch.setattr(caller_impl.httpx, "AsyncClient", _Client)
    caller = caller_impl.GeminiToolCaller(
        api_key="secret",
        base_url="",
        model="gemini-test",
        timeout=83,
    )

    response = asyncio.run(caller.chat_with_tools([{"role": "user", "content": "hi"}], [], False))

    assert response.content == "ok"
    assert captured["url"] == "https://generativelanguage.googleapis.com/v1beta/models/gemini-test:generateContent"
    assert captured["headers"] == {"Content-Type": "application/json", "x-goog-api-key": "secret"}
    assert captured["params"] == {}
    assert captured["client_kwargs"]["timeout"].read == 83
    assert captured["client_kwargs"]["follow_redirects"] is False


def test_mimo_endpoint_detection_accepts_api_and_token_plan_urls() -> None:
    assert caller_impl._is_mimo_endpoint("https://api.xiaomimimo.com/v1") is True
    assert caller_impl._is_mimo_endpoint("https://token-plan-cn.xiaomimimo.com/v1") is True
    assert caller_impl._is_mimo_endpoint("https://api.xiaomimimo.com/anthropic") is True
    assert caller_impl._is_mimo_endpoint("https://token-plan-cn.xiaomimimo.com/anthropic") is True
    assert caller_impl._is_mimo_endpoint("https://api.openai.com/v1") is False


def test_mimo_openai_message_sanitizer_merges_system_and_cleans_images() -> None:
    messages = [
        {"role": "system", "content": "当前时间块"},
        {
            "role": "system",
            "content": [
                {"type": "text", "text": "人格设定"},
                {"type": "input_text", "text": "附加约束"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,SYSTEM"}},
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "看这张图"},
                {
                    "type": "image_url",
                    "image_url": {
                        "url": "data:image/png;base64,USER",
                        "detail": "high",
                        "mime_type": "image/png",
                    },
                    "alt_text": "user image",
                    "mime_type": "image/png",
                },
            ],
        },
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "lookup", "arguments": "{}"},
                }
            ],
        },
    ]

    sanitized = caller_impl._sanitize_mimo_openai_messages(messages)

    assert sanitized[0]["role"] == "system"
    assert isinstance(sanitized[0]["content"], str)
    assert "当前时间块" in sanitized[0]["content"]
    assert "人格设定" in sanitized[0]["content"]
    assert "附加约束" in sanitized[0]["content"]
    assert "SYSTEM" not in sanitized[0]["content"]
    assert [item.get("role") for item in sanitized].count("system") == 1

    user_parts = sanitized[1]["content"]
    assert user_parts == [
        {"type": "text", "text": "看这张图"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,USER"}},
    ]
    assert "detail" not in user_parts[1]["image_url"]
    assert "mime_type" not in user_parts[1]
    assert "alt_text" not in user_parts[1]
    assert sanitized[2]["content"] == ""
    assert sanitized[2]["tool_calls"][0]["id"] == "call_1"


def test_mimo_message_shape_error_is_detected_only_for_param_incorrect() -> None:
    error = RuntimeError(
        "Error code: 400 - {'error': {'code': '400', 'message': "
        "'Param Incorrect', 'param': 'messages[1] system only supports a string or an array of text parts'}}"
    )
    assert caller_impl._error_indicates_mimo_message_shape_or_vision_unavailable(error) is True
    assert caller_impl._error_indicates_mimo_message_shape_or_vision_unavailable(
        RuntimeError("system only supports text")
    ) is False


def test_build_tool_caller_returns_gemini_cli_instance() -> None:
    cfg = _DummyConfig(
        personification_api_type="gemini_cli",
        personification_model="gemini-3.1-pro-preview",
    )
    caller = caller_impl.build_tool_caller(cfg)
    assert isinstance(caller, caller_impl.GeminiCliToolCaller)
    assert caller.model == "gemini-3.1-pro-preview"


def test_build_tool_caller_returns_antigravity_cli_instance() -> None:
    cfg = _DummyConfig(
        personification_api_type="antigravity_cli",
        personification_model="gemini-3.1-pro-preview",
        personification_antigravity_cli_auth_path="C:/tmp/agy.json",
        personification_antigravity_cli_project="agy-project",
    )
    caller = caller_impl.build_tool_caller(cfg)
    assert isinstance(caller, caller_impl.AntigravityCliToolCaller)
    assert caller.model == "gemini-3.1-pro-preview"
    assert caller.auth_path_override == "C:/tmp/agy.json"
    assert caller.project_override == "agy-project"


@pytest.mark.parametrize("api_type", ("claude_code", "claude-code", "ClaudeCode", "claude_cli", "claude-cli"))
def test_build_tool_caller_rejects_removed_route_before_reading_credentials(api_type: str) -> None:
    class _TombstoneConfig:
        personification_api_type = api_type

        def __getattr__(self, name: str):  # noqa: ANN204
            raise AssertionError(f"removed route unexpectedly read {name}")

    with pytest.raises(caller_impl.ProviderTypeRemovedError) as exc_info:
        caller_impl.build_tool_caller(_TombstoneConfig())
    assert exc_info.value.code == "provider_type_removed"


def test_build_tool_caller_keeps_standard_anthropic_api_available() -> None:
    caller = caller_impl.build_tool_caller(
        _DummyConfig(
            personification_api_type="anthropic",
            personification_api_key="anthropic-api-key",
            personification_api_url="https://api.anthropic.com",
            personification_model="claude-opus-4-7",
        )
    )
    assert isinstance(caller, caller_impl.AnthropicToolCaller)
    assert caller.model == "claude-opus-4-7"


def test_provider_router_accepts_cli_legacy_routes_without_api_key(monkeypatch) -> None:
    monkeypatch.setattr(provider_router, "_load_env_api_pool_config", lambda _logger: [])
    gemini_cfg = _DummyConfig(
        personification_api_type="gemini_cli",
        personification_model="gemini-3.1-pro-preview",
        personification_gemini_cli_auth_path="C:/tmp/gemini.json",
        personification_gemini_cli_project="cloud-project",
    )
    gemini_providers = provider_router.get_configured_api_providers(gemini_cfg, _Logger())
    assert gemini_providers[0]["api_type"] == "gemini_cli"
    assert gemini_providers[0]["auth_path"] == "C:/tmp/gemini.json"
    assert gemini_providers[0]["project"] == "cloud-project"

    antigravity_cfg = _DummyConfig(
        personification_api_type="antigravity_cli",
        personification_model="gemini-3.1-pro-preview",
        personification_antigravity_cli_auth_path="C:/tmp/agy.json",
        personification_antigravity_cli_project="agy-project",
    )
    antigravity_providers = provider_router.get_configured_api_providers(antigravity_cfg, _Logger())
    assert antigravity_providers[0]["api_type"] == "antigravity_cli"
    assert antigravity_providers[0]["auth_path"] == "C:/tmp/agy.json"
    assert antigravity_providers[0]["project"] == "agy-project"

def test_provider_router_skips_removed_pool_route_without_copying_credentials() -> None:
    cfg = _DummyConfig(
        personification_api_pools=(
            '[{"name":"local-gemini","api_type":"gemini_cli","model":"gemini-3.1-pro-preview",'
            '"auth_path":"C:/tmp/gemini.json","project":"cloud-project"},'
            '{"name":"removed-route","api_type":"claude_code","model":"claude-opus-4-7",'
            '"auth_path":"C:/tmp/claude.json","api_key":"do-not-echo"}]'
        )
    )
    providers = provider_router.get_configured_api_providers(cfg, _Logger())
    assert [item["api_type"] for item in providers] == ["gemini_cli"]
    assert providers[0]["project"] == "cloud-project"
    tombstones = provider_router.detect_removed_provider_routes(cfg)
    assert tombstones == [{
        "api_type": "provider_type_removed",
        "enabled": False,
        "diagnostic_code": "provider_type_removed",
        "migration_hint": tombstones[0]["migration_hint"],
        "source": "personification_api_pools",
        "route_index": 1,
    }]
    assert "do-not-echo" not in repr(tombstones)
    assert "C:/tmp/claude.json" not in repr(tombstones)


def test_ai_routes_reject_removed_provider_before_building_a_caller() -> None:
    with pytest.raises(RuntimeError) as exc_info:
        ai_routes.build_single_provider_caller(
            _DummyConfig(),
            {
                "api_type": "claude_cli",
                "model": "claude-opus-4-7",
                "auth_path": "C:/tmp/should-not-be-read.json",
            },
        )
    assert getattr(exc_info.value, "code", "") == "provider_type_removed"


def test_removed_legacy_primary_is_not_a_provider_or_fallback_candidate(monkeypatch) -> None:
    monkeypatch.setattr(provider_router, "_load_env_api_pool_config", lambda _logger: [])
    cfg = SimpleNamespace(
        personification_api_pools=None,
        personification_api_type="claude_code",
        personification_model="claude-opus-4-7",
        personification_api_url="https://unused.example/v1",
        personification_api_key="do-not-copy",
    )

    class _CollectingLogger:
        def __init__(self) -> None:
            self.warnings: list[str] = []

        def info(self, *_args, **_kwargs) -> None:
            return None

        def warning(self, message: str, *_args, **_kwargs) -> None:
            self.warnings.append(str(message))

        def error(self, *_args, **_kwargs) -> None:
            return None

    logger = _CollectingLogger()
    assert provider_router.get_configured_api_providers(cfg, logger) == []
    assert provider_router.get_provider_candidates(cfg, logger) == []
    assert any("provider_type_removed" in message for message in logger.warnings)
    assert "do-not-copy" not in "\n".join(logger.warnings)


@pytest.mark.parametrize("api_type", ("claude_code", "claude-code", "ClaudeCode", "claude_cli", "claude-cli"))
def test_removed_primary_and_fallback_types_do_not_read_stale_credentials(monkeypatch, api_type: str) -> None:
    monkeypatch.setattr(provider_router, "_load_env_api_pool_config", lambda _logger: [])
    monkeypatch.setattr(provider_router, "_read_env_api_pool_raw", lambda: "")

    class _RemovedPrimaryConfig:
        personification_api_pools = None
        personification_api_type = api_type

        def __getattr__(self, name: str):  # noqa: ANN204
            if name in {"personification_api_url", "personification_api_key", "personification_model"}:
                raise AssertionError(f"removed primary unexpectedly read {name}")
            return ""

    primary = ai_routes.get_primary_provider_config(_RemovedPrimaryConfig(), _Logger())
    assert primary == {
        "api_type": "provider_type_removed",
        "api_url": "",
        "api_key": "",
        "model": "",
        "auth_path": "",
        "gemini_auth_mode": "auto",
    }

    class _RemovedFallbackConfig:
        personification_api_pools = []
        personification_api_type = "openai"
        personification_api_url = ""
        personification_api_key = ""
        personification_model = ""
        personification_fallback_enabled = True
        personification_fallback_api_type = api_type
        personification_gemini_auth_mode = "auto"

        def __getattr__(self, name: str):  # noqa: ANN204
            if name.startswith("personification_fallback_"):
                raise AssertionError(f"removed fallback unexpectedly read {name}")
            return ""

    assert ai_routes.resolve_global_fallback_provider(_RemovedFallbackConfig()) is None

    class _RemovedVideoFallbackConfig:
        personification_video_fallback_enabled = True
        personification_video_fallback_provider = api_type

        def __getattr__(self, name: str):  # noqa: ANN204
            if name.startswith("personification_video_fallback_"):
                raise AssertionError(f"removed video fallback unexpectedly read {name}")
            return ""

    assert ai_routes.resolve_video_fallback_provider(_RemovedVideoFallbackConfig()) is None


def test_provider_router_prefers_multiline_env_pool_when_runtime_value_is_truncated(monkeypatch) -> None:
    temp_dir = _make_workspace_temp_dir("provider-env-")
    try:
        env_payload = [
            {
                "name": "gemini_cli_primary",
                "api_type": "gemini_cli",
                "model": "gemini-3-flash-preview",
                "auth_path": "~/.gemini/oauth_creds.json",
                "priority": 1,
                "enabled": True,
            },
            {
                "name": "codex_primary",
                "api_type": "openai_codex",
                "model": "gpt-5.4-mini",
                "auth_path": "~/.codex/auth.json",
                "priority": 2,
                "enabled": True,
            },
        ]
        (temp_dir / ".env.prod").write_text(
            "personification_api_pools='"
            + json.dumps(env_payload, ensure_ascii=False, indent=2)
            + "'\n",
            encoding="utf-8",
        )
        monkeypatch.chdir(temp_dir)
        cfg = _DummyConfig(
            personification_api_pools=[
                {
                    "name": "codex_primary",
                    "api_type": "openai_codex",
                    "model": "gpt-5.4-mini",
                    "auth_path": "~/.codex/auth.json",
                    "priority": 1,
                }
            ]
        )

        providers = provider_router.get_configured_api_providers(cfg, _Logger())

        assert [item["name"] for item in providers] == ["gemini_cli_primary", "codex_primary"]
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_provider_router_loads_multiline_env_pool_when_runtime_value_is_none(monkeypatch) -> None:
    temp_dir = _make_workspace_temp_dir("provider-env-none-")
    try:
        env_payload = [
            {
                "name": "gemini_cli_primary",
                "api_type": "gemini_cli",
                "model": "gemini-3-flash-preview",
                "auth_path": "~/.gemini/oauth_creds.json",
                "priority": 1,
                "enabled": True,
            },
            {
                "name": "codex_primary",
                "api_type": "openai_codex",
                "model": "gpt-5.4-mini",
                "auth_path": "~/.codex/auth.json",
                "priority": 2,
                "enabled": True,
            },
        ]
        (temp_dir / ".env.prod").write_text(
            "personification_api_pools='"
            + json.dumps(env_payload, ensure_ascii=False, indent=2)
            + "'\n",
            encoding="utf-8",
        )
        monkeypatch.chdir(temp_dir)
        monkeypatch.setattr(
            provider_router,
            "_load_env_api_pool_config",
            lambda logger: provider_router.parse_api_pool_config(
                (temp_dir / ".env.prod").read_text(encoding="utf-8").split("=", 1)[1],
                logger,
            ),
        )
        cfg = _DummyConfig(personification_api_pools=None)

        providers = provider_router.get_configured_api_providers(cfg, _Logger())

        assert [item["name"] for item in providers] == ["gemini_cli_primary", "codex_primary"]
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_provider_candidates_preserve_priority_over_rotation() -> None:
    cfg = _DummyConfig(
        personification_api_pools=(
            '[{"name":"gemini_cli_primary","api_type":"gemini_cli","model":"gemini-3-flash-preview",'
            '"auth_path":"~/.gemini/oauth_creds.json","priority":1},'
            '{"name":"codex_primary","api_type":"openai_codex","model":"gpt-5.4-mini",'
            '"auth_path":"~/.codex/auth.json","priority":2}]'
        )
    )
    provider_router.PROVIDER_FAILURE_STATE.clear()
    provider_router.PROVIDER_ROTATION_CURSOR = 1

    first = provider_router.get_provider_candidates(cfg, _Logger())
    second = provider_router.get_provider_candidates(cfg, _Logger())

    assert [item["name"] for item in first] == ["gemini_cli_primary", "codex_primary"]
    assert [item["name"] for item in second] == ["gemini_cli_primary", "codex_primary"]


def test_provider_candidates_rotate_only_same_priority_tier() -> None:
    cfg = _DummyConfig(
        personification_api_pools=(
            '[{"name":"gemini_a","api_type":"gemini_cli","model":"gemini-3-flash-preview",'
            '"auth_path":"~/.gemini/oauth_creds.json","priority":1},'
            '{"name":"gemini_b","api_type":"gemini_cli","model":"gemini-3-flash-preview",'
            '"auth_path":"~/.gemini/oauth_creds.json","priority":1},'
            '{"name":"codex_fallback","api_type":"openai_codex","model":"gpt-5.4-mini",'
            '"auth_path":"~/.codex/auth.json","priority":2}]'
        )
    )
    provider_router.PROVIDER_FAILURE_STATE.clear()
    provider_router.PROVIDER_ROTATION_CURSOR = 1

    candidates = provider_router.get_provider_candidates(cfg, _Logger())

    assert [item["name"] for item in candidates] == ["gemini_b", "gemini_a", "codex_fallback"]


def test_provider_candidates_skip_rate_limited_gemini_cli_until_cooldown() -> None:
    cfg = _DummyConfig(
        personification_api_pools=(
            '[{"name":"gemini_cli_primary","api_type":"gemini_cli","model":"gemini-3-flash-preview",'
            '"auth_path":"~/.gemini/oauth_creds.json","priority":1},'
            '{"name":"codex_primary","api_type":"openai_codex","model":"gpt-5.4-mini",'
            '"auth_path":"~/.codex/auth.json","priority":2}]'
        )
    )

    class _Response:
        status_code = 429
        headers = {"Retry-After": "900"}

    class _RateLimitError(Exception):
        response = _Response()

    provider_router.PROVIDER_FAILURE_STATE.clear()
    provider_router._mark_provider_failure("gemini_cli_primary", _RateLimitError("429 Too Many Requests"))

    state = provider_router.PROVIDER_FAILURE_STATE["gemini_cli_primary"]
    assert state["rate_limited"] is True
    assert state["cooldown_until"] - time.time() > 850

    candidates = provider_router.get_provider_candidates(cfg, _Logger())

    assert [item["name"] for item in candidates] == ["codex_primary", "gemini_cli_primary"]


def test_routed_config_proxy_passes_cli_auth_fields_to_tool_caller() -> None:
    base = _DummyConfig()
    gemini_proxy = ai_routes._ProviderConfigProxy(
        base,
        {
            "api_type": "gemini_cli",
            "model": "gemini-3.1-pro-preview",
            "auth_path": "C:/tmp/gemini.json",
            "project": "cloud-project",
        },
    )
    gemini_caller = caller_impl.build_tool_caller(gemini_proxy)
    assert isinstance(gemini_caller, caller_impl.GeminiCliToolCaller)
    assert gemini_caller.auth_path_override == "C:/tmp/gemini.json"
    assert gemini_caller.project_override == "cloud-project"

    antigravity_proxy = ai_routes._ProviderConfigProxy(
        base,
        {
            "api_type": "antigravity_cli",
            "model": "gemini-3.1-pro-preview",
            "auth_path": "C:/tmp/agy.json",
            "project": "agy-project",
        },
    )
    antigravity_caller = caller_impl.build_tool_caller(antigravity_proxy)
    assert isinstance(antigravity_caller, caller_impl.AntigravityCliToolCaller)
    assert antigravity_caller.auth_path_override == "C:/tmp/agy.json"
    assert antigravity_caller.project_override == "agy-project"

def test_gemini_cli_resolves_project_from_load_code_assist_before_local_file(monkeypatch) -> None:
    temp_dir = _make_workspace_temp_dir("gemini-project-")
    try:
        monkeypatch.delenv("GEMINI_CLI_PROJECT", raising=False)
        monkeypatch.delenv("GEMINI_PROJECT", raising=False)
        monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
        monkeypatch.delenv("GCLOUD_PROJECT", raising=False)
        monkeypatch.delenv("CLOUDSDK_CORE_PROJECT", raising=False)
        auth_file = temp_dir / "oauth_creds.json"
        auth_file.write_text(
            json.dumps({"access_token": "token", "quota_project_id": "local-project-123"}),
            encoding="utf-8",
        )

        class _Response:
            def raise_for_status(self) -> None:
                return None

            def json(self):  # noqa: ANN201
                return {"cloudaicompanionProject": "server-project-123"}

        class _Client:
            def __init__(self, *_args, **_kwargs) -> None:
                return None

            async def __aenter__(self):  # noqa: ANN201
                return self

            async def __aexit__(self, *_args) -> None:
                return None

            async def post(self, *_args, **_kwargs):  # noqa: ANN201
                return _Response()

        monkeypatch.setattr(caller_impl.httpx, "AsyncClient", _Client)
        caller_impl.GeminiCliToolCaller._project_cache.clear()
        caller = caller_impl.GeminiCliToolCaller(model="gemini-3-flash-preview")

        project = asyncio.run(caller._resolve_project("token", auth_file))

        assert project == "server-project-123"
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_gemini_cli_model_candidates_expand_auto_to_backend_models() -> None:
    assert caller_impl.GeminiCliToolCaller(model="").model == "auto-gemini-3"
    assert caller_impl._gemini_cli_model_candidates("auto-gemini-3") == [
        "gemini-2.5-flash",
        "gemini-3-flash-preview",
        "gemini-2.5-pro",
    ]
    assert caller_impl._gemini_cli_model_candidates("gemini-3-flash-preview") == [
        "gemini-3-flash-preview",
        "gemini-2.5-flash",
        "gemini-2.5-pro",
    ]


def test_antigravity_cli_project_prefers_antigravity_env(monkeypatch) -> None:
    monkeypatch.setenv("ANTIGRAVITY_CLI_PROJECT", "agy-project-123")
    monkeypatch.setenv("GEMINI_CLI_PROJECT", "gemini-project-123")

    assert caller_impl._resolve_local_antigravity_cli_project() == "agy-project-123"


@dataclass
class _DummyRuntime:
    plugin_config: object
    tool_caller: object


@dataclass
class _RuntimeConfig:
    personification_image_gen_enabled: bool = True
    personification_image_gen_model: str = "gpt-image-2"
    personification_image_gen_nanobanan_model: str = "gemini-3-pro-image-preview"
    personification_image_gen_timeout: int = 180


def test_nanobanan_tool_only_on_gemini_cli_route() -> None:
    cfg = _RuntimeConfig()
    gemini_caller = caller_impl.GeminiCliToolCaller(model="gemini-3.1-pro-preview")
    runtime = _DummyRuntime(plugin_config=cfg, tool_caller=gemini_caller)
    nano_tool = build_image_gen_nanobanan_tool(runtime)
    codex_tool = build_image_gen_tool(runtime)
    assert nano_tool is not None and nano_tool.name == "generate_image"
    assert codex_tool is not None and codex_tool.name == "generate_image"


def test_nanobanan_tool_available_on_antigravity_cli_route() -> None:
    cfg = _RuntimeConfig()
    agy_caller = caller_impl.AntigravityCliToolCaller(model="gemini-3.1-pro-preview")
    runtime = _DummyRuntime(plugin_config=cfg, tool_caller=agy_caller)
    nano_tool = build_image_gen_nanobanan_tool(runtime)
    assert nano_tool is not None and nano_tool.name == "generate_image"


def test_nanobanan_tool_disabled_on_openai_route() -> None:
    cfg = _RuntimeConfig()
    openai_caller = caller_impl.OpenAIToolCaller(
        api_key="sk-test",
        base_url="",
        model="gpt-4o-mini",
        thinking_mode="none",
    )
    runtime = _DummyRuntime(plugin_config=cfg, tool_caller=openai_caller)
    assert build_image_gen_nanobanan_tool(runtime) is None
