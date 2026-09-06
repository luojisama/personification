from __future__ import annotations

"""Wire-level regressions for the four chat protocol encoders.

The turn-media tests assert projection separately.  These tests deliberately
cross the caller boundary as well: a future change must not silently lose or
reorder selected image parts while translating to a provider-specific request.
"""

import asyncio
import base64
import io
import sys
import types
from types import SimpleNamespace

import pytest
from PIL import Image

from plugin.personification.skills.skillpacks.tool_caller.scripts import impl


def _image_content(count: int) -> list[dict]:
    # Deliberately distinct, data-URL image transports make ordering observable
    # in each provider's native wire format without accessing a media host.
    urls = []
    for index in range(count):
        output = io.BytesIO()
        Image.new("RGB", (8, 8), (index * 30, 50, 100)).save(output, "PNG")
        urls.append("data:image/png;base64," + base64.b64encode(output.getvalue()).decode())
    return [{"type": "text", "text": "compare in order"}] + [
        {"type": "image_url", "image_url": {"url": url, "detail": "auto"}}
        for url in urls
    ]


def _data_values_from_parts(parts: list[dict]) -> list[str]:
    return [
        item["image_url"]["url"].split(",", 1)[1]
        for item in parts
        if item.get("type") == "image_url"
    ]


@pytest.mark.parametrize("count", [1, 2, 4, 8])
def test_openai_chat_completions_wire_keeps_all_selected_images_in_order(monkeypatch, count: int) -> None:
    captured: list[dict] = []

    class _Completions:
        async def create(self, **payload):  # noqa: ANN202
            captured.append(payload)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="ok", tool_calls=[], annotations=[]))]
            )

    class _OpenAI:
        def __init__(self, **_kwargs) -> None:
            self.chat = SimpleNamespace(completions=_Completions())
            self.responses = SimpleNamespace()

    async def _http_client(*_args, **_kwargs):  # noqa: ANN202
        return object()

    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(AsyncOpenAI=_OpenAI))
    monkeypatch.setattr(impl, "_get_pooled_http_client", _http_client)
    caller = impl.OpenAIToolCaller(api_key="test", base_url="https://mock.invalid/v1", model="test")

    assert asyncio.run(caller.chat_with_tools([{"role": "user", "content": _image_content(count)}], [], False)).content == "ok"
    wire_parts = captured[0]["messages"][-1]["content"]
    assert _data_values_from_parts(wire_parts) == _data_values_from_parts(_image_content(count))


@pytest.mark.parametrize("count", [1, 2, 4, 8])
def test_openai_responses_wire_keeps_all_selected_images_in_order(monkeypatch, count: int) -> None:
    captured: list[dict] = []

    class _Responses:
        async def create(self, **payload):  # noqa: ANN202
            captured.append(payload)
            return {"output": [{"type": "message", "content": [{"type": "output_text", "text": "ok"}]}]}

    class _OpenAI:
        def __init__(self, **_kwargs) -> None:
            self.responses = _Responses()
            self.chat = SimpleNamespace(completions=SimpleNamespace())

    async def _http_client(*_args, **_kwargs):  # noqa: ANN202
        return object()

    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(AsyncOpenAI=_OpenAI))
    monkeypatch.setattr(impl, "_get_pooled_http_client", _http_client)
    caller = impl.OpenAIToolCaller(api_key="test", base_url="https://mock.invalid/v1", model="test")

    assert asyncio.run(caller.chat_with_tools([{"role": "user", "content": _image_content(count)}], [], True)).content == "ok"
    image_items = [item for item in captured[0]["input"][0]["content"] if item["type"] == "input_image"]
    assert [item["image_url"].split(",", 1)[1] for item in image_items] == _data_values_from_parts(_image_content(count))


@pytest.mark.parametrize("count", [1, 2, 4, 8])
def test_anthropic_wire_keeps_all_selected_images_in_order(monkeypatch, count: int) -> None:
    captured: list[dict] = []

    class _Messages:
        async def create(self, **payload):  # noqa: ANN202
            captured.append(payload)
            return SimpleNamespace(content=[SimpleNamespace(type="text", text="ok")], usage=None)

    class _Anthropic:
        def __init__(self, **_kwargs) -> None:
            self.messages = _Messages()

    monkeypatch.setitem(sys.modules, "anthropic", types.SimpleNamespace(AsyncAnthropic=_Anthropic))
    caller = impl.AnthropicToolCaller(api_key="test", base_url="https://mock.invalid", model="test")

    assert asyncio.run(caller.chat_with_tools([{"role": "user", "content": _image_content(count)}], [], False)).content == "ok"
    image_blocks = [item for item in captured[0]["messages"][0]["content"] if item["type"] == "image"]
    assert [item["source"]["data"] for item in image_blocks] == _data_values_from_parts(_image_content(count))


@pytest.mark.parametrize("count", [1, 2, 4, 8])
def test_gemini_http_wire_keeps_all_selected_images_in_order(monkeypatch, count: int) -> None:
    captured: list[dict] = []

    class _Response:
        status_code = 200

        def json(self):  # noqa: ANN201
            return {"candidates": [{"content": {"role": "model", "parts": [{"text": "ok"}]}}]}

    class _Client:
        def __init__(self, **_kwargs) -> None:
            pass

        async def __aenter__(self):  # noqa: ANN201
            return self

        async def __aexit__(self, *_args) -> None:
            return None

        async def post(self, _url, *, headers=None, params=None, json=None):  # noqa: ANN001, ANN201
            del headers, params
            captured.append(json or {})
            return _Response()

    monkeypatch.setattr(impl.httpx, "AsyncClient", _Client)
    caller = impl.GeminiToolCaller(api_key="test", base_url="https://mock.invalid/v1beta", model="gemini-test")

    assert asyncio.run(caller.chat_with_tools([{"role": "user", "content": _image_content(count)}], [], False)).content == "ok"
    inline = [part["inlineData"]["data"] for part in captured[0]["contents"][0]["parts"] if "inlineData" in part]
    assert inline == _data_values_from_parts(_image_content(count))


def test_openai_responses_tool_continuation_stays_on_successful_route_and_keeps_image_input(monkeypatch) -> None:
    captured: list[dict] = []
    responses = [
        {"output": [{"type": "function_call", "id": "fc-1", "call_id": "call-1", "name": "lookup", "arguments": "{}"}]},
        {"output": [{"type": "message", "content": [{"type": "output_text", "text": "done"}]}]},
    ]

    class _Responses:
        async def create(self, **payload):  # noqa: ANN202
            captured.append(payload)
            return responses.pop(0)

    class _OpenAI:
        def __init__(self, **_kwargs) -> None:
            self.responses = _Responses()
            # A route regression must not silently fall through to Chat Completions.
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=None))

    async def _http_client(*_args, **_kwargs):  # noqa: ANN202
        return object()

    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(AsyncOpenAI=_OpenAI))
    monkeypatch.setattr(impl, "_get_pooled_http_client", _http_client)
    caller = impl.OpenAIToolCaller(api_key="test", base_url="https://mock.invalid/v1", model="test")
    messages = [{"role": "user", "content": _image_content(2)}]

    first = asyncio.run(caller.chat_with_tools(messages, [], True))
    messages.append(caller.build_assistant_tool_calls_message(first))
    messages.append(caller.build_tool_result_message("call-1", "lookup", "result"))
    assert asyncio.run(caller.chat_with_tools(messages, [], True)).content == "done"
    assert len(captured) == 2
    assert captured[1]["input"][0]["role"] == "user"
    assert [item["type"] for item in captured[1]["input"][1:]] == ["function_call", "function_call_output"]
    assert [item for item in captured[1]["input"][0]["content"] if item["type"] == "input_image"]


@pytest.mark.parametrize("kind", ["audio", "video"])
def test_gemini_adapter_serializes_supported_audio_and_video_to_http_wire(monkeypatch, kind: str) -> None:
    captured: list[dict] = []

    class _Response:
        status_code = 200

        def json(self):  # noqa: ANN201
            return {"candidates": [{"content": {"role": "model", "parts": [{"text": "ok"}]}}]}

    class _Client:
        def __init__(self, **_kwargs) -> None:
            pass

        async def __aenter__(self):  # noqa: ANN201
            return self

        async def __aexit__(self, *_args) -> None:
            return None

        async def post(self, _url, *, headers=None, params=None, json=None):  # noqa: ANN001, ANN201
            del headers, params
            captured.append(json or {})
            return _Response()

    monkeypatch.setattr(impl.httpx, "AsyncClient", _Client)
    caller = impl.GeminiToolCaller(api_key="test", base_url="https://mock.invalid/v1beta", model="gemini-test")
    content = [{"type": f"{kind}_url", f"{kind}_url": {"url": f"https://media.test/sample.{ 'mp3' if kind == 'audio' else 'mp4'}"}}]

    assert asyncio.run(caller.chat_with_tools([{"role": "user", "content": content}], [], False)).content == "ok"
    file_data = captured[0]["contents"][0]["parts"][0]["fileData"]
    assert file_data["fileUri"].startswith("https://media.test/")
    assert file_data["mimeType"] == ("audio/mpeg" if kind == "audio" else "video/mp4")


def test_gemini_invalid_media_combination_fails_before_http_request(monkeypatch) -> None:
    calls = 0

    class _Client:
        def __init__(self, **_kwargs) -> None:
            pass

        async def __aenter__(self):  # noqa: ANN201
            return self

        async def __aexit__(self, *_args) -> None:
            return None

        async def post(self, *_args, **_kwargs):  # noqa: ANN202
            nonlocal calls
            calls += 1
            raise AssertionError("invalid media must fail before HTTP")

    monkeypatch.setattr(impl.httpx, "AsyncClient", _Client)
    caller = impl.GeminiToolCaller(api_key="test", base_url="https://mock.invalid/v1beta", model="gemini-test")

    with pytest.raises(ValueError, match="invalid_audio_ref"):
        asyncio.run(caller.chat_with_tools([{"role": "user", "content": [{"type": "audio_url", "audio_url": {"url": "file:///not-allowed.wav"}}]}], [], False))
    assert calls == 0
