from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from plugin.personification.core import provider_router
from plugin.personification.skills.skillpacks.tool_caller.scripts.impl import ToolCallerResponse


class _Logger:
    def __init__(self) -> None:
        self.warnings: list[str] = []

    def info(self, *_args, **_kwargs) -> None:
        return None

    def warning(self, message, *_args, **_kwargs) -> None:  # noqa: ANN001
        self.warnings.append(str(message))


class _HttpError(RuntimeError):
    def __init__(self, status_code: int, *, retry_after: str = "") -> None:
        super().__init__(f"request failed at https://secret.example/v1/models/private: HTTP {status_code}")
        self.response = SimpleNamespace(
            status_code=status_code,
            headers={"Retry-After": retry_after} if retry_after else {},
        )


def _provider(name: str, *, attempts: int = 5) -> dict:
    return {
        "name": name,
        "api_type": "openai",
        "model": "test-model",
        "max_retries": attempts,
    }


def _success() -> ToolCallerResponse:
    return ToolCallerResponse("stop", "ok", [], {})


def test_provider_defaults_and_explicit_overrides() -> None:
    base = {
        "name": "main",
        "api_type": "openai",
        "api_url": "https://example.test/v1",
        "api_key": "secret",
        "model": "model",
    }

    defaulted = provider_router.parse_api_pool_config([base])[0]
    overridden = provider_router.parse_api_pool_config([{**base, "timeout": 90, "max_retries": 3}])[0]

    assert defaulted["timeout"] == 200
    assert defaulted["max_retries"] == 5
    assert overridden["timeout"] == 90
    assert overridden["max_retries"] == 3


def test_gemini_caller_receives_provider_timeout() -> None:
    caller = provider_router._build_provider_caller(
        {
            "name": "gemini",
            "api_type": "gemini",
            "api_url": "https://example.test/v1beta",
            "api_key": "secret",
            "model": "gemini-test",
            "timeout": 73,
        },
        SimpleNamespace(personification_thinking_mode="none"),
    )

    assert caller.timeout == 73


def test_transient_failure_retries_up_to_five_attempts(monkeypatch) -> None:  # noqa: ANN001
    calls = 0
    delays: list[float] = []

    async def _call(*_args, **_kwargs):  # noqa: ANN202
        nonlocal calls
        calls += 1
        if calls < 5:
            raise _HttpError(503)
        return _success()

    async def _sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr(provider_router, "_call_provider_once", _call)
    monkeypatch.setattr(provider_router.asyncio, "sleep", _sleep)
    provider_router.PROVIDER_FAILURE_STATE.clear()

    response, errors, _ = asyncio.run(
        provider_router._try_provider_chain(
            [_provider("primary")],
            messages=[],
            plugin_config=SimpleNamespace(),
            logger=_Logger(),
        )
    )

    assert response is not None and response.content == "ok"
    assert calls == 5
    assert delays == [1.0, 2.0, 4.0, 8.0]
    assert len(errors) == 4
    assert "primary" not in provider_router.PROVIDER_FAILURE_STATE


def test_timeout_retries_instead_of_skipping_remaining_attempts(monkeypatch) -> None:  # noqa: ANN001
    calls = 0
    delays: list[float] = []

    async def _call(*_args, **_kwargs):  # noqa: ANN202
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TimeoutError()
        return _success()

    async def _sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr(provider_router, "_call_provider_once", _call)
    monkeypatch.setattr(provider_router.asyncio, "sleep", _sleep)

    response, _, _ = asyncio.run(
        provider_router._try_provider_chain(
            [_provider("slow")],
            messages=[],
            plugin_config=SimpleNamespace(),
            logger=_Logger(),
        )
    )

    assert response is not None
    assert calls == 2
    assert delays == [1.0]


def test_http_400_does_not_retry_and_switches_provider(monkeypatch) -> None:  # noqa: ANN001
    calls = {"bad": 0, "backup": 0}
    delays: list[float] = []
    logger = _Logger()

    async def _call(provider, *_args, **_kwargs):  # noqa: ANN001, ANN202
        calls[provider["name"]] += 1
        if provider["name"] == "bad":
            raise _HttpError(400)
        return _success()

    async def _sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr(provider_router, "_call_provider_once", _call)
    monkeypatch.setattr(provider_router.asyncio, "sleep", _sleep)
    provider_router.PROVIDER_FAILURE_STATE.clear()

    response, errors, _ = asyncio.run(
        provider_router._try_provider_chain(
            [_provider("bad"), _provider("backup")],
            messages=[],
            plugin_config=SimpleNamespace(),
            logger=logger,
        )
    )

    assert response is not None
    assert calls == {"bad": 1, "backup": 1}
    assert delays == []
    assert any("status=400" in item for item in errors)
    assert all("secret.example" not in item for item in errors)
    assert all("secret.example" not in item for item in logger.warnings)
    assert "secret.example" not in provider_router.PROVIDER_FAILURE_STATE["bad"]["last_error"]


@pytest.mark.parametrize("status", [408, 409, 425, 429, 500, 503])
def test_retryable_http_statuses(status: int) -> None:
    assert provider_router._is_retryable_provider_error(_HttpError(status)) is True


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_non_retryable_http_statuses(status: int) -> None:
    assert provider_router._is_retryable_provider_error(_HttpError(status)) is False


def test_rate_limit_retry_after_is_capped() -> None:
    assert provider_router._provider_retry_delay(_HttpError(429, retry_after="900"), 0) == 30.0
