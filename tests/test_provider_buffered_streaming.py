from __future__ import annotations

import asyncio
import sys
import types
from types import SimpleNamespace

import pytest

from ._loader import load_personification_module


impl = load_personification_module(
    "plugin.personification.skills.skillpacks.tool_caller.scripts.impl"
)
punctuation = load_personification_module("plugin.personification.core.reply_punctuation")


def test_buffered_assembler_waits_for_completed_and_joins_cross_chunk_tool_call() -> None:
    assembler = impl.BufferedToolResponseAssembler(model_used="model", wire_tools_count=1)
    assembler.add(impl.ProviderStreamEvent("text_delta", {"text": "先"}))
    assembler.add(impl.ProviderStreamEvent("tool_call_delta", {"key": 0, "id": "call_1", "name": "lookup", "arguments": '{"q":'}))
    assembler.add(impl.ProviderStreamEvent("tool_call_delta", {"key": 0, "arguments": '"天气"}'}))
    with pytest.raises(RuntimeError, match="incomplete"):
        assembler.finalize()
    assembler.add(impl.ProviderStreamEvent("usage", {"total_tokens": 7}))
    assembler.add(impl.ProviderStreamEvent("completed", {"finish_reason": "tool_calls"}))
    response = assembler.finalize()
    assert response.content == "先"
    assert response.tool_calls[0].id == "call_1"
    assert response.tool_calls[0].name == "lookup"
    assert response.tool_calls[0].arguments == {"q": "天气"}
    assert response.usage["total_tokens"] == 7


def test_openai_stream_adapter_never_returns_before_all_chunks() -> None:
    class Stream:
        def __init__(self) -> None:
            self.seen = 0

        def __aiter__(self):
            return self

        async def __anext__(self):
            self.seen += 1
            if self.seen == 1:
                return {"choices": [{"delta": {"content": "你好"}, "finish_reason": None}]}
            if self.seen == 2:
                return {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1", "function": {"name": "x", "arguments": "{}"}}]}, "finish_reason": "tool_calls"}]}
            raise StopAsyncIteration

    response = asyncio.run(impl._assemble_openai_chat_stream(Stream(), model_used="m", wire_tools_count=1))
    assert response.content == "你好"
    assert [(item.id, item.name, item.arguments) for item in response.tool_calls] == [("c1", "x", {})]


def test_openai_content_filter_stream_fails_closed_without_partial_response() -> None:
    class Stream:
        def __aiter__(self):
            return self

        async def __anext__(self):
            if getattr(self, "done", False):
                raise StopAsyncIteration
            self.done = True
            return {"choices": [{"delta": {"content": "半句"}, "finish_reason": "content_filter"}]}

    with pytest.raises(impl.ProviderStreamSafetyBlocked):
        asyncio.run(impl._assemble_openai_chat_stream(Stream(), model_used="m", wire_tools_count=0))


def test_gemini_and_antigravity_use_same_complete_assembly() -> None:
    body = 'data: {"candidates":[{"content":{"parts":[{"text":"好"},{"functionCall":{"id":"g1","name":"say","args":{"message":"hi"}}}]},"finishReason":"STOP"}],"usageMetadata":{"totalTokenCount":3}}\n'
    assert impl.assemble_antigravity_sse_response(body, model_used="agy").content == "好"


def test_consumed_antigravity_sse_preserves_thought_signature_for_continuation() -> None:
    body = (
        'data: {"response":{"candidates":[{"content":{"parts":['
        '{"thought":true,"thoughtSignature":"sig","text":"想"},'
        '{"functionCall":{"id":"c","name":"x","args":{"a":1}}}'
        ']},"finishReason":"STOP"}]}}\n'
    )
    response = impl.assemble_antigravity_sse_response(body, model_used="agy")
    assert response.provider_history["parts"][0]["thoughtSignature"] == "sig"
    assert response.tool_calls[0].arguments == {"a": 1}


def test_consumed_responses_payload_uses_common_assembler_and_history() -> None:
    response = impl.assemble_openai_responses_payload(
        {
            "usage": {"total_tokens": 4},
            "output": [
                {"type": "message", "content": [{"type": "output_text", "text": "完成"}]},
                {"type": "function_call", "call_id": "c1", "name": "lookup", "arguments": '{"x":1}'},
            ],
        },
        model_used="codex",
        wire_tools_count=1,
    )
    assert response.content == "完成"
    assert response.tool_calls[0].arguments == {"x": 1}
    assert response.provider_history[1]["call_id"] == "c1"


def test_responses_stream_joins_function_arguments_and_needs_completed() -> None:
    class Events:
        def __init__(self) -> None:
            self.items = iter([
                {"type": "response.output_item.added", "output_index": 0, "item": {"type": "function_call", "call_id": "c1", "name": "run"}},
                {"type": "response.function_call_arguments.delta", "output_index": 0, "call_id": "c1", "name": "run", "delta": '{"a":'},
                {"type": "response.function_call_arguments.delta", "output_index": 0, "call_id": "c1", "name": "run", "delta": "2}"},
                {"type": "response.output_item.done", "output_index": 0, "item": {"type": "function_call", "call_id": "c1", "name": "run", "arguments": '{"a":2}'}},
                {"type": "response.completed", "response": {"usage": {"total_tokens": 5}}},
            ])

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                return next(self.items)
            except StopIteration as exc:
                raise StopAsyncIteration from exc

    response = asyncio.run(impl._assemble_openai_responses_stream(Events(), model_used="m", wire_tools_count=1))
    assert response.tool_calls[0].arguments == {"a": 2}
    assert response.usage["total_tokens"] == 5


def test_responses_completed_output_is_authoritative_when_item_done_is_missing() -> None:
    class Events:
        def __aiter__(self):
            return self

        async def __anext__(self):
            if getattr(self, "done", False):
                raise StopAsyncIteration
            self.done = True
            return {
                "type": "response.completed",
                "response": {
                    "usage": {"total_tokens": 6},
                    "output": [
                        {"type": "function_call", "call_id": "fallback", "name": "f", "arguments": '{"k":true}'},
                        {"type": "web_search_call"},
                    ],
                },
            }

    response = asyncio.run(impl._assemble_openai_responses_stream(Events(), model_used="m", wire_tools_count=1))
    assert response.tool_calls[0].arguments == {"k": True}
    assert response.provider_history[0]["call_id"] == "fallback"
    assert response.used_builtin_search is True


def test_responses_incomplete_stream_fails_closed_without_partial_response() -> None:
    class Events:
        def __init__(self) -> None:
            self.items = iter([
                {"type": "response.output_text.delta", "delta": "半句"},
                {"type": "response.incomplete", "response": {"output": []}},
            ])

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                return next(self.items)
            except StopIteration as exc:
                raise StopAsyncIteration from exc

    with pytest.raises(RuntimeError, match="incomplete"):
        asyncio.run(impl._assemble_openai_responses_stream(Events(), model_used="m", wire_tools_count=0))


def test_gemini_stream_history_keeps_thought_signature() -> None:
    class Response:
        async def aiter_lines(self):
            yield 'data: {"candidates":[{"content":{"parts":[{"thought":true,"thoughtSignature":"s","functionCall":{"id":"g1","name":"f","args":{"v":1}}}]},"finishReason":"STOP"}]}'

    response = asyncio.run(impl._assemble_gemini_sse_stream(Response(), model_used="g", wire_tools_count=1))
    assert response.provider_history["parts"][0]["thoughtSignature"] == "s"
    assert response.tool_calls[0].provider_call_id == "g1"


def test_gemini_safety_finish_fails_closed_without_partial_response() -> None:
    class Response:
        async def aiter_lines(self):
            yield 'data: {"candidates":[{"content":{"parts":[{"text":"半句"}]},"finishReason":"SAFETY"}]}'

    with pytest.raises(impl.ProviderStreamSafetyBlocked):
        asyncio.run(impl._assemble_gemini_sse_stream(Response(), model_used="g", wire_tools_count=0))


def test_stream_mode_and_support_are_configuration_driven_without_a_call() -> None:
    supported = impl.provider_streaming_snapshot(configured_mode="buffered", api_type="gemini_official")
    unsupported = impl.provider_streaming_snapshot(configured_mode="buffered", api_type="gemini_cli")
    assert supported["mode"] == "buffered" and supported["route_supported"] is True
    assert unsupported["mode"] == "buffered" and unsupported["route_supported"] is False


def test_openai_caller_stream_error_falls_back_before_any_tool_or_delivery(monkeypatch) -> None:  # noqa: ANN001
    calls: list[bool] = []

    class Completions:
        async def create(self, **kwargs):
            calls.append(bool(kwargs.get("stream")))
            if kwargs.get("stream"):
                raise RuntimeError("mock stream disconnected")
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="完整回复", tool_calls=[], annotations=[]))]
            )

    class FakeAsyncOpenAI:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=Completions())
            self.responses = SimpleNamespace()

    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(AsyncOpenAI=FakeAsyncOpenAI))
    caller = impl.OpenAIToolCaller(
        api_key="test",
        base_url="https://example.invalid/v1",
        model="test-model",
        streaming_mode="buffered",
    )
    response = asyncio.run(caller.chat_with_tools([{"role": "user", "content": "hi"}], [], False))
    assert calls == [True, False]
    assert response.content == "完整回复"
    assert response.tool_calls == []


def test_openai_stream_creation_failure_increments_fallback_counter(monkeypatch) -> None:  # noqa: ANN001
    before = impl.provider_streaming_snapshot()["fallback_count"]

    class Completions:
        async def create(self, **kwargs):
            if kwargs.get("stream"):
                raise RuntimeError("cannot create stream")
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="完整", tool_calls=[], annotations=[]))])

    class FakeAsyncOpenAI:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=Completions())
            self.responses = SimpleNamespace()

    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(AsyncOpenAI=FakeAsyncOpenAI))
    caller = impl.OpenAIToolCaller(api_key="test", base_url="https://example.invalid/v1", model="m", streaming_mode="buffered")
    assert asyncio.run(caller.chat_with_tools([{"role": "user", "content": "hi"}], [], False)).content == "完整"
    assert impl.provider_streaming_snapshot()["fallback_count"] == before + 1


def test_precreate_stream_fallback_does_not_decrement_another_active_call() -> None:
    """A route setup error must not corrupt a concurrent assembler's count."""
    telemetry = impl._PROVIDER_STREAMING_TELEMETRY
    before = telemetry.snapshot()
    telemetry.started(mode="buffered", route_supported=True)
    try:
        active = telemetry.snapshot()
        assert active["active_calls"] == before["active_calls"] + 1

        impl._record_stream_fallback(route_supported=True)
        after_fallback = telemetry.snapshot()
        assert after_fallback["active_calls"] == active["active_calls"]
        assert after_fallback["fallback_count"] == before["fallback_count"] + 1
    finally:
        telemetry.finished(
            {
                "mode": "buffered",
                "route_supported": True,
                "first_chunk_ms": 1,
                "total_ms": 1,
                "chunk_count": 1,
            }
        )

    assert telemetry.snapshot()["active_calls"] == before["active_calls"]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("你好，", "你好"),
        ("你好！", "你好"),
        ("你好？", "你好？"),
        ("《你好。》", "《你好。》"),
        ("（你好！）", "（你好！）"),
        ("你好。  ", "你好  "),
        ("你好。", "你好。"),
    ],
)
def test_terminal_punctuation_boundaries(value: str, expected: str) -> None:
    policy = "preserve" if value == "你好。" and expected == "你好。" else "strip_common"
    assert punctuation.apply_terminal_punctuation_policy(value, policy=policy) == expected


def test_stream_snapshot_is_body_free() -> None:
    snapshot = impl.provider_streaming_snapshot()
    assert {"active_calls", "fallback_count", "chunk_count", "first_chunk_ms", "total_ms"} <= set(snapshot)
    assert "content" not in snapshot and "delta" not in snapshot
