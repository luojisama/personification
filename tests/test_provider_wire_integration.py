"""The outer retry budget counts requests made by the real OpenAI caller."""

from __future__ import annotations

import asyncio
import sys
import types

import pytest

from ._loader import load_personification_module


ai_routes = load_personification_module("plugin.personification.core.ai_routes")
impl = load_personification_module("plugin.personification.skills.skillpacks.tool_caller.scripts.impl")
llm_context = load_personification_module("plugin.personification.core.llm_context")


def _http_error(status: int) -> RuntimeError:
    error = RuntimeError(f"HTTP {status}")
    error.status_code = status
    return error


@pytest.mark.parametrize(
    ("single_attempt", "expected_stream_flags"),
    [(False, [True, False, True, False]), (True, [True])],
)
def test_real_buffered_caller_respects_shared_wire_budget(
    monkeypatch: pytest.MonkeyPatch,
    single_attempt: bool,
    expected_stream_flags: list[bool],
) -> None:
    sdk_calls: list[bool] = []
    sdk_retry_settings: list[int | None] = []

    class Completions:
        async def create(self, **kwargs):  # noqa: ANN003, ANN202
            streamed = bool(kwargs.get("stream"))
            sdk_calls.append(streamed)
            raise _http_error(405 if streamed else 503)

    class FakeAsyncOpenAI:
        def __init__(self, **kwargs):  # noqa: ANN003
            sdk_retry_settings.append(kwargs.get("max_retries"))
            self.chat = types.SimpleNamespace(completions=Completions())
            self.responses = types.SimpleNamespace()

    async def fake_http_client(*_args, **_kwargs):  # noqa: ANN002, ANN003, ANN202
        return object()

    async def no_sleep(_delay: float) -> None:
        return None

    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(AsyncOpenAI=FakeAsyncOpenAI))
    monkeypatch.setattr(impl, "_get_pooled_http_client", fake_http_client)
    monkeypatch.setattr(ai_routes.asyncio, "sleep", no_sleep)

    caller = impl.OpenAIToolCaller(
        api_key="synthetic-key", base_url="https://example.invalid/v1",
        model="synthetic-model", streaming_mode="buffered",
    )
    routed = ai_routes.RoutedToolCaller(
        primary_callers=[caller], fallback_caller=None, logger=None,
    )

    async def run() -> None:
        token = None
        if single_attempt:
            token = llm_context.set_llm_context(
                purpose="capability_probe",
                retry_policy=llm_context.LLM_RETRY_POLICY_SINGLE_ATTEMPT,
            )
        try:
            with pytest.raises(ai_routes.RoutedToolCallerError):
                await routed.chat_with_tools([{"role": "user", "content": "probe"}], [], False)
        finally:
            if token is not None:
                llm_context.reset_llm_context(token)

    asyncio.run(run())

    assert sdk_calls == expected_stream_flags
    assert sdk_retry_settings == [0] * expected_stream_flags.count(True)
