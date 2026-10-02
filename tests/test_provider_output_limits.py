from __future__ import annotations

import asyncio
import json
import sys
from types import SimpleNamespace

import httpx
import pytest

from ._loader import load_personification_module


impl = load_personification_module("plugin.personification.skills.skillpacks.tool_caller.scripts.impl")
routes = load_personification_module("plugin.personification.core.ai_routes")
context = load_personification_module("plugin.personification.core.llm_context")


@pytest.mark.parametrize("model,field", [("grok-4.6", "max_tokens"), ("gpt-6-luna", "max_completion_tokens")])
def test_openai_wire_uses_route_output_limit_and_restores_scope(monkeypatch, model, field):
    requests = []

    class Completions:
        async def create(self, **kwargs):
            requests.append(kwargs)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="ok", tool_calls=[], annotations=[]))])

    class Client:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=Completions())

    async def http_client(*args, **kwargs):
        return object()

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(AsyncOpenAI=Client))
    monkeypatch.setattr(impl, "_get_pooled_http_client", http_client)
    caller = impl.OpenAIToolCaller(api_key="fixture", base_url="https://fixture.invalid/v1", model=model)
    routed = routes.RoutedToolCaller(primary_callers=[caller], fallback_caller=None, logger=None,
                                    route_descriptors=[{"model": model, "max_output_tokens": 4096}])

    async def run():
        await routed.chat_with_tools([{"role": "user", "content": "hello"}], [], False)
        assert context.current_llm_output_limit() == 32_768
        await caller.chat_with_tools([{"role": "user", "content": "hello"}], [], False)

    asyncio.run(run())
    assert [r[field] for r in requests] == [4096, 32_768]


def test_gemini_wire_default_and_explicit_output_limit(monkeypatch):
    captured = []
    original_client = httpx.AsyncClient

    def respond(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "ok"}]}, "finishReason": "STOP"}]})

    monkeypatch.setattr(impl.httpx, "AsyncClient", lambda **kwargs: original_client(transport=httpx.MockTransport(respond), **kwargs))
    caller = impl.GeminiToolCaller(api_key="fixture", base_url="https://fixture.invalid/v1beta", model="gemini-3.8-flash", auth_mode="header")
    routed = routes.RoutedToolCaller(primary_callers=[caller], fallback_caller=None, logger=None,
                                    route_descriptors=[{"model": "gemini-3.8-flash", "max_output_tokens": 4096}])

    async def run():
        await routed.chat_with_tools([{"role": "user", "content": "hello"}], [], False)
        await caller.chat_with_tools([{"role": "user", "content": "hello"}], [], False)

    asyncio.run(run())
    assert [r["generationConfig"]["maxOutputTokens"] for r in captured] == [4096, 32_768]


def test_anthropic_wire_honors_limit_even_with_thinking(monkeypatch):
    captured = []

    class Messages:
        async def create(self, **kwargs):
            captured.append(kwargs)
            return {"content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn"}

    class Client:
        def __init__(self, **kwargs):
            self.messages = Messages()

    monkeypatch.setitem(sys.modules, "anthropic", SimpleNamespace(AsyncAnthropic=Client))
    monkeypatch.setattr(impl, "_maybe_anthropic_thinking", lambda mode: {"type": "enabled", "budget_tokens": 8192})
    caller = impl.AnthropicToolCaller(api_key="fixture", base_url="https://fixture.invalid", model="claude", thinking_mode="high")
    routed = routes.RoutedToolCaller(primary_callers=[caller], fallback_caller=None, logger=None,
                                    route_descriptors=[{"model": "claude", "max_output_tokens": 4096}])
    asyncio.run(routed.chat_with_tools([{"role": "user", "content": "hello"}], [], False))
    assert captured[0]["max_tokens"] == 4096
    assert captured[0]["thinking"]["budget_tokens"] < captured[0]["max_tokens"]


def test_parallel_routes_do_not_share_output_limits():
    class Caller:
        async def chat_with_tools(self, messages, tools, search):
            await asyncio.sleep(0)
            return impl.ToolCallerResponse(content=str(context.current_llm_output_limit()), finish_reason="stop", tool_calls=[], raw={})

    shared = Caller()
    callers = [routes.RoutedToolCaller(primary_callers=[shared], fallback_caller=None, logger=None,
                                      route_descriptors=[{"max_output_tokens": limit}]) for limit in (4096, 32_768)]

    async def run():
        replies = await asyncio.gather(*(caller.chat_with_tools([{"role": "user", "content": "hello"}], [], False) for caller in callers))
        assert [reply.content for reply in replies] == ["4096", "32768"]
        assert context.current_llm_output_limit() == 32_768

    asyncio.run(run())
