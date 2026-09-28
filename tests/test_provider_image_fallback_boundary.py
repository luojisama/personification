from __future__ import annotations

import asyncio

import pytest

from ._loader import load_personification_module

impl = load_personification_module("plugin.personification.skills.skillpacks.tool_caller.scripts.impl")


def _upstream_error(code: str, status: int = 400) -> RuntimeError:
    error = RuntimeError("private provider diagnostic")
    error.status_code = status
    error.upstream_code = code
    return error


def test_codex_image_retries_only_explicit_model_rejection(monkeypatch) -> None:  # noqa: ANN001
    caller = impl.OpenAICodexToolCaller(model="test")
    async def token():
        return "fake", None
    monkeypatch.setattr(caller, "_get_access_token", token)
    monkeypatch.setattr(caller, "_extract_image_payload_candidate", lambda _value: ("synthetic-base64", ""))
    payloads = []

    async def request(payload, **_kwargs):  # noqa: ANN001
        payloads.append(payload)
        if len(payloads) == 1:
            raise _upstream_error("unsupported_image_model")
        return {"output": [{"type": "image_generation_call", "result": {"b64_json": "synthetic"}}]}

    monkeypatch.setattr(caller, "_request_codex_response", request)
    result = asyncio.run(caller.generate_image("draw", image_model="gpt-image-2"))
    assert len(payloads) == 2
    assert "model" in payloads[0]["tools"][0]
    assert "model" not in payloads[1]["tools"][0]
    assert "provider default model" in result["warning"]


@pytest.mark.parametrize("error", [TypeError("internal model parser"), _upstream_error("auth_failed", 401), TimeoutError("network timeout")])
def test_codex_image_does_not_drop_reference_on_unrelated_failure(monkeypatch, error: Exception) -> None:  # noqa: ANN001
    caller = impl.OpenAICodexToolCaller(model="test")
    async def token():
        return "fake", None
    monkeypatch.setattr(caller, "_get_access_token", token)
    payloads = []

    async def request(payload, **_kwargs):  # noqa: ANN001
        payloads.append(payload)
        raise error

    monkeypatch.setattr(caller, "_request_codex_response", request)
    with pytest.raises(type(error)):
        asyncio.run(caller.generate_image("draw", images=["https://example.invalid/image.png"], reference_mode="auto"))
    assert len(payloads) == 1
    assert payloads[0]["input"]


def test_codex_image_strict_reference_mode_does_not_degrade(monkeypatch) -> None:  # noqa: ANN001
    caller = impl.OpenAICodexToolCaller(model="test")
    async def token():
        return "fake", None
    monkeypatch.setattr(caller, "_get_access_token", token)
    count = 0

    async def request(_payload, **_kwargs):  # noqa: ANN001
        nonlocal count
        count += 1
        raise _upstream_error("unsupported_reference_image")

    monkeypatch.setattr(caller, "_request_codex_response", request)
    with pytest.raises(RuntimeError):
        asyncio.run(caller.generate_image("draw", images=["https://example.invalid/image.png"], reference_mode="strict"))
    assert count == 1
