from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

from ._loader import load_personification_module

pipeline_context = load_personification_module("plugin.personification.handlers.reply_pipeline.pipeline_context")


class _FakeCaller:
    def __init__(self, content: str = "", *, should_fail: bool = False, vision_unavailable: bool = False) -> None:
        self._content = content
        self._should_fail = should_fail
        self._vision_unavailable = vision_unavailable
        self.calls = 0

    async def chat_with_tools(self, messages, tools, use_builtin_search):  # noqa: ANN001
        del messages, tools, use_builtin_search
        self.calls += 1
        if self._should_fail:
            raise RuntimeError("boom")
        return SimpleNamespace(
            content=self._content,
            vision_unavailable=self._vision_unavailable,
        )


class _SequenceCaller:
    def __init__(self, contents: list[str]) -> None:
        self._contents = list(contents)
        self.calls = 0

    async def chat_with_tools(self, messages, tools, use_builtin_search):  # noqa: ANN001
        del messages, tools, use_builtin_search
        content = self._contents[self.calls]
        self.calls += 1
        return SimpleNamespace(content=content, vision_unavailable=False)


def _build_runtime(
    lite_caller: object | None = None,
    *,
    agent_caller: object | None = None,
    api_type: str = "openai",
    model: str = "gpt-4o-mini",
    lite_model: str = "gpt-4o-mini",
) -> object:
    logger = SimpleNamespace(debug=lambda *_args, **_kwargs: None)
    plugin_config = SimpleNamespace(
        personification_api_type=api_type,
        personification_model=model,
        personification_lite_model=lite_model,
    )
    providers = [{"api_type": api_type, "model": model}]
    return SimpleNamespace(
        lite_tool_caller=lite_caller,
        agent_tool_caller=agent_caller,
        logger=logger,
        plugin_config=plugin_config,
        get_configured_api_providers=lambda: providers,
    )


def test_classify_incoming_image_mface_short_circuit() -> None:
    pipeline_context.clear_image_classify_cache()
    runtime = _build_runtime()

    result = asyncio.run(
        pipeline_context.classify_incoming_image(
            runtime=runtime,
            image_url="data:image/png;base64,aaa",
            source_kind="mface",
            width=512,
            height=512,
        )
    )

    assert result.kind == "sticker"
    assert result.source == "rule"


def test_classify_incoming_image_missing_size_is_unknown_without_vision() -> None:
    pipeline_context.clear_image_classify_cache()
    runtime = _build_runtime()

    result = asyncio.run(
        pipeline_context.classify_incoming_image(
            runtime=runtime,
            image_url="data:image/png;base64,aaa",
            source_kind="image",
            width=0,
            height=0,
        )
    )

    assert result.kind == "unknown"
    assert result.reason == "classifier_fallback"
    assert result.confidence == 0.0


def test_classify_incoming_image_does_not_infer_sticker_from_url_text() -> None:
    pipeline_context.clear_image_classify_cache()
    runtime = _build_runtime()

    result = asyncio.run(
        pipeline_context.classify_incoming_image(
            runtime=runtime,
            image_url="http://example.com/x.gif",
            source_kind="image",
            width=512,
            height=512,
        )
    )

    assert result.kind == "unknown"
    assert result.reason == "classifier_fallback"


def test_classifier_text_fallback_rejects_explanatory_or_negated_output() -> None:
    assert pipeline_context._parse_image_classifier_kind("photo") == "photo"
    assert pipeline_context._parse_image_classifier_kind("not photo") is None
    assert pipeline_context._parse_image_classifier_kind("I think this is sticker") is None
    assert pipeline_context._parse_image_classifier_kind('{"kind":"unknown","confidence":0}') == "unknown"


def test_classify_incoming_image_uses_content_digest_cache_not_file_id() -> None:
    pipeline_context.clear_image_classify_cache()
    lite_caller = _FakeCaller("photo")
    runtime = _build_runtime(lite_caller)

    first = asyncio.run(
        pipeline_context.classify_incoming_image(
            runtime=runtime,
            image_url="data:image/png;base64,aaa",
            source_kind="image",
            width=1920,
            height=1080,
            file_id="abc",
        )
    )
    second = asyncio.run(
        pipeline_context.classify_incoming_image(
            runtime=runtime,
            image_url="data:image/png;base64,aaa",
            source_kind="image",
            width=1920,
            height=1080,
            file_id="different-file-id",
        )
    )

    assert first.kind == "photo"
    assert first.source == "lite_tool_caller"
    assert second.kind == "photo"
    assert second.source == "cache"
    assert lite_caller.calls == 1


def test_classify_incoming_remote_url_does_not_cache_url_digest() -> None:
    pipeline_context.clear_image_classify_cache()
    caller = _SequenceCaller(["photo", "sticker"])
    runtime = _build_runtime(caller)

    first = asyncio.run(
        pipeline_context.classify_incoming_image(
            runtime=runtime,
            image_url="https://cdn.example.test/same-url.png",
            source_kind="image",
        )
    )
    second = asyncio.run(
        pipeline_context.classify_incoming_image(
            runtime=runtime,
            image_url="https://cdn.example.test/same-url.png",
            source_kind="image",
        )
    )

    assert (first.kind, second.kind) == ("photo", "sticker")
    assert caller.calls == 2


def test_classifier_does_not_start_provider_call_after_turn_deadline() -> None:
    pipeline_context.clear_image_classify_cache()
    caller = _FakeCaller("photo")
    runtime = _build_runtime(caller)

    result = asyncio.run(
        pipeline_context.classify_incoming_image(
            runtime=runtime,
            image_url="data:image/png;base64,aaa",
            source_kind="image",
            response_deadline=time.monotonic() - 0.01,
        )
    )

    assert result.kind == "unknown"
    assert caller.calls == 0


def test_classify_incoming_image_falls_back_to_unknown_on_llm_failure() -> None:
    pipeline_context.clear_image_classify_cache()
    runtime = _build_runtime(_FakeCaller(should_fail=True))

    result = asyncio.run(
        pipeline_context.classify_incoming_image(
            runtime=runtime,
            image_url="data:image/png;base64,aaa",
            source_kind="image",
            width=1920,
            height=1080,
            file_id="fail",
        )
    )

    assert result.kind == "unknown"
    assert result.source == "fallback"


def test_classify_incoming_image_does_not_cache_failure() -> None:
    pipeline_context.clear_image_classify_cache()
    caller = _FakeCaller(should_fail=True)
    runtime = _build_runtime(caller)
    first = asyncio.run(
        pipeline_context.classify_incoming_image(
            runtime=runtime,
            image_url="data:image/png;base64,aaa",
            source_kind="image",
        )
    )
    caller._should_fail = False
    caller._content = "photo"
    second = asyncio.run(
        pipeline_context.classify_incoming_image(
            runtime=runtime,
            image_url="data:image/png;base64,aaa",
            source_kind="image",
        )
    )

    assert first.kind == "unknown"
    assert second.kind == "photo"
    assert caller.calls == 2


def test_classify_incoming_image_preserves_model_unknown_without_caching() -> None:
    pipeline_context.clear_image_classify_cache()
    caller = _SequenceCaller(["unknown", "photo"])
    runtime = _build_runtime(caller)

    first = asyncio.run(
        pipeline_context.classify_incoming_image(
            runtime=runtime,
            image_url="data:image/png;base64,aaa",
            source_kind="image",
        )
    )
    second = asyncio.run(
        pipeline_context.classify_incoming_image(
            runtime=runtime,
            image_url="data:image/png;base64,aaa",
            source_kind="image",
        )
    )

    assert first.kind == "unknown"
    assert first.source == "lite_tool_caller"
    assert second.kind == "photo"
    assert caller.calls == 2


def test_classify_incoming_image_same_dimensions_do_not_share_cache() -> None:
    pipeline_context.clear_image_classify_cache()
    caller = _SequenceCaller(["photo", "sticker"])
    runtime = _build_runtime(caller)

    first = asyncio.run(
        pipeline_context.classify_incoming_image(
            runtime=runtime,
            image_url="data:image/png;base64,first-image",
            source_kind="image",
            width=640,
            height=640,
        )
    )
    second = asyncio.run(
        pipeline_context.classify_incoming_image(
            runtime=runtime,
            image_url="data:image/png;base64,second-image",
            source_kind="image",
            width=640,
            height=640,
        )
    )

    assert first.kind == "photo"
    assert second.kind == "sticker"
    assert caller.calls == 2


def test_classify_incoming_image_without_vision_is_unknown_even_when_large() -> None:
    pipeline_context.clear_image_classify_cache()
    runtime = _build_runtime(
        _FakeCaller("photo"),
        api_type="openai_codex",
        model="gpt-5.4-mini",
        lite_model="gpt-5.4-mini",
    )

    result = asyncio.run(
        pipeline_context.classify_incoming_image(
            runtime=runtime,
            image_url="data:image/png;base64,aaa",
            source_kind="image",
            width=1920,
            height=1080,
            file_id="size-only",
        )
    )

    assert result.kind == "unknown"
    assert result.source == "fallback"
