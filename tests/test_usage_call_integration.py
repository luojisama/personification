from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from ._loader import load_personification_module


def test_usage_survives_generation_invalidation_and_deduplicates(monkeypatch, tmp_path):
    paths = load_personification_module("plugin.personification.core.paths")
    store = load_personification_module("plugin.personification.core.data_store")
    monkeypatch.setattr(paths, "get_data_dir", lambda _cfg=None: tmp_path)
    store.init_data_store(SimpleNamespace(personification_data_dir=str(tmp_path)))
    router = load_personification_module("plugin.personification.core.provider_router")
    fence = load_personification_module("plugin.personification.core.generation_fence")
    ledger = load_personification_module("plugin.personification.core.token_ledger")
    impl = load_personification_module("plugin.personification.skills.skillpacks.tool_caller.scripts.impl")
    response = impl.ToolCallerResponse(finish_reason="stop", content="", tool_calls=[], raw=None,
                                      model_used="actual", usage={"prompt_tokens": 20, "completion_tokens": 2})
    calls = 0

    def generation_check():
        nonlocal calls
        calls += 1
        if calls > 1:
            raise RuntimeError("generation invalidated")

    async def chat(**_kwargs):
        return response

    monkeypatch.setattr(fence, "assert_current_generation", generation_check)
    monkeypatch.setattr(router, "_build_provider_caller", lambda *_args: SimpleNamespace(chat_with_tools=chat))
    with pytest.raises(RuntimeError, match="generation invalidated"):
        asyncio.run(router._call_provider_once(
            {"provider_id": "test-route", "api_type": "openai", "model": "configured", "context_budget_enabled": False},
            [], plugin_config=SimpleNamespace(),
        ))
    assert ledger.record_response_usage(response) is False
    summary = ledger.query_usage_insights("all", route_id="test-route", model="actual")
    assert summary["call_count"] == 1
    assert summary["input_tokens"] == 20
    assert summary["output_tokens"] == 2


def test_memory_scope_is_storage_attribution_not_permission():
    routes = load_personification_module("plugin.personification.webui.routes.memory_routes")
    result = routes._decorate_memory_item({"group_id": "123", "user_id": "456", "permission_type": "private"})
    assert result["scope_kind"] == "group_user"
    assert result["scope_label"] == "群内用户"
    assert result["permission_type"] == "private"
    assert routes._decorate_memory_item({})["scope_kind"] == "unknown"


def test_failed_stream_keeps_valid_usage_without_partial_reply(monkeypatch, tmp_path):
    paths = load_personification_module("plugin.personification.core.paths")
    store = load_personification_module("plugin.personification.core.data_store")
    monkeypatch.setattr(paths, "get_data_dir", lambda _cfg=None: tmp_path)
    store.init_data_store(SimpleNamespace(personification_data_dir=str(tmp_path)))
    impl = load_personification_module("plugin.personification.skills.skillpacks.tool_caller.scripts.impl")
    context = load_personification_module("plugin.personification.core.llm_context")
    ledger = load_personification_module("plugin.personification.core.token_ledger")

    async def stream():
        yield {"choices": [{"delta": {"content": "partial"}}],
               "usage": {"prompt_tokens": 10, "completion_tokens": 4,
                         "prompt_tokens_details": {"cached_tokens": 5}}}
        raise RuntimeError("wire lost")

    token = context.set_wire_retry_disabled(usage_route_id="failed-route", usage_provider="openai")
    try:
        with pytest.raises(RuntimeError, match="wire lost"):
            asyncio.run(impl._assemble_openai_chat_stream(stream(), model_used="actual", wire_tools_count=0))
    finally:
        context.reset_llm_context(token)
    result = ledger.query_usage_insights("all", route_id="failed-route")
    assert result["call_count"] == 1
    assert result["total_tokens"] == 14
    assert result["cache_read_tokens"] == 5


def test_stream_final_usage_supersedes_partial_unknown_marker():
    impl = load_personification_module("plugin.personification.skills.skillpacks.tool_caller.scripts.impl")
    assembler = impl.BufferedToolResponseAssembler(model_used="m", wire_tools_count=0)
    assembler.add(impl.ProviderStreamEvent("usage", impl._extract_usage({"usage": {"output_tokens": 1}})))
    assembler.add(impl.ProviderStreamEvent("usage", impl._extract_usage({"usage": {"input_tokens": 8, "output_tokens": 2}})))
    assembler.add(impl.ProviderStreamEvent("completed", {}))
    assert assembler.finalize().usage.get("usage_complete") is not False
