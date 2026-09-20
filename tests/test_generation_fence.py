from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from ._loader import load_personification_module

fence = load_personification_module("plugin.personification.core.generation_fence")
commit = load_personification_module("plugin.personification.handlers.reply_commit")
executor = load_personification_module("plugin.personification.agent.runtime.executor")
relation = load_personification_module("plugin.personification.core.supplement_relation")


def state():
    entry = {"current_generation": 1, "superseded_generation": 0}
    return {"batch_runtime_ref": {"entry": entry, "generation": 1}, "reply_commit_lock": asyncio.Lock()}


def test_late_provider_result_cannot_start_delivery_after_cancel_is_ignored():
    async def run():
        current = state()
        ready = asyncio.Event()
        async def late():
            token = fence.bind_generation(current)
            try:
                ready.set()
                try:
                    await asyncio.sleep(60)
                except asyncio.CancelledError:
                    pass  # Models/transports can suppress cancellation.
                commit.mark_reply_delivery_started(current)
            finally:
                fence.reset_generation(token)
        task = asyncio.create_task(late())
        await ready.wait()
        async with current["reply_commit_lock"]:
            assert fence.invalidate_generation(current)
        task.cancel()
        with pytest.raises(fence.SupersededGeneration):
            await task
        assert not current.get("reply_delivery_started")
    asyncio.run(run())


def test_external_tool_barrier_stops_replay_and_read_tools_allow_replacement():
    async def run():
        current = state()
        token = fence.bind_generation(current)
        calls = []
        async def handler(**_):
            calls.append(1)
            return "ok"
        try:
            read = SimpleNamespace(local=True, handler=handler, metadata={"evidence_kind": "web", "side_effect": "none"})
            assert await executor._invoke_tool_handler(tool_name="web_search", tool=read, tool_args={}) == "ok"
            assert fence.safe_to_supersede(current)
            write = SimpleNamespace(local=True, handler=handler, metadata={"side_effect": "send_message"})
            await executor._invoke_tool_handler(tool_name="write", tool=write, tool_args={})
            assert not fence.invalidate_generation(current)
            assert len(calls) == 2
        finally:
            fence.reset_generation(token)
    asyncio.run(run())


def test_model_config_snapshot_is_independent_and_scoped():
    config = SimpleNamespace(personification_api_pools=[{"model": "first"}], personification_lite_model="small")
    current = state()
    token = fence.bind_generation(current, config)
    try:
        config.personification_api_pools[0]["model"] = "second"
        assert fence.active_config(config).personification_api_pools[0]["model"] == "first"
    finally:
        fence.reset_generation(token)
    assert fence.active_config(config).personification_api_pools[0]["model"] == "second"


def test_relation_judge_accepts_only_locally_bound_candidate_ids():
    observed = []
    async def api(messages):
        observed.extend(messages)
        return '{"results":[{"message_id":"new","relation":"related"},{"message_id":"invented","relation":"related"}]}'
    actual = asyncio.run(relation.build_relation_judge(api)([{"message_id":"old","text":"first"}], [{"message_id":"new","text":"supplement"}]))
    assert actual == {"results":[{"message_id":"new","relation":"related"}]}
    assert observed[0]["role"] == "system" and "first" in observed[1]["content"]


def test_routed_caller_uses_frozen_bound_model_and_its_own_capacity(monkeypatch):
    routes = load_personification_module("plugin.personification.core.ai_routes")
    router = load_personification_module("plugin.personification.core.provider_router")
    monkeypatch.setattr(router, "_read_env_api_pool_raw", lambda: "")
    monkeypatch.setattr(routes, "resolve_global_fallback_provider", lambda *_a, **_k: None)
    built = []
    class Fake:
        def __init__(self, config):
            self.model = config.personification_model
            built.append(self.model)
        async def chat_with_tools(self, *_a, **_k):
            return SimpleNamespace(content=self.model)
    monkeypatch.setattr(routes, "_build_tool_caller", Fake)
    async def invoke(self, *_a, **_k):
        return await self._primary_callers[0].chat_with_tools()
    # Keep the real dynamic dispatch/factory but isolate downstream wire/usage.
    original = routes.RoutedToolCaller.chat_with_tools
    async def boundary(self, *args, **kwargs):
        if getattr(self, "_dynamic_source_config", None) is not None:
            return await original(self, *args, **kwargs)
        return await invoke(self, *args, **kwargs)
    monkeypatch.setattr(routes.RoutedToolCaller, "chat_with_tools", boundary)
    config = SimpleNamespace(personification_api_pools=[{
        "provider_id":"p", "name":"p", "api_type":"openai", "api_url":"https://example.test/v1", "api_key":"synthetic",
        "model":"large", "models":[{"model_id":"large","context_window_tokens":1050000},{"model_id":"small","context_window_tokens":272000}]}],
        personification_model_purpose_bindings={"lite":{"provider_id":"p","model_id":"small"}}, personification_strict_main_model=False)
    logger = SimpleNamespace(info=lambda *_:None, warning=lambda *_:None, error=lambda *_:None)
    caller = routes.build_routed_tool_caller(config, logger, purpose="lite")
    assert caller._caller_route_descriptors[id(caller._primary_callers[0])]["context_window_tokens"] == 272000
    async def run():
        current = state()
        token = fence.bind_generation(current, config)
        config.personification_model_purpose_bindings["lite"]["model_id"] = "large"
        try:
            assert (await caller.chat_with_tools([], [], False)).content == "small"
        finally:
            fence.reset_generation(token)
        assert (await caller.chat_with_tools([], [], False)).content == "large"
        config.personification_model_purpose_bindings.clear()
        assert (await caller.chat_with_tools([], [], False)).content == "large"
        config.personification_api_pools[0]["default_model_id"] = "small"
        assert (await caller.chat_with_tools([], [], False)).content == "small"
    asyncio.run(run())


def test_dynamic_response_builders_keep_originating_wire_protocol():
    routes = load_personification_module("plugin.personification.core.ai_routes")
    expected = [{"role": "tool", "tool_call_id": "local-call", "content": "read result"}]
    origin = SimpleNamespace(
        build_assistant_tool_calls_message=lambda response: {"role": "assistant", "content": "origin"},
        build_tool_result_messages=lambda response, results: expected,
        build_synthetic_tool_evidence_message=lambda *args: {"role": "tool", "content": "origin evidence"},
    )
    response = SimpleNamespace(_routed_origin=origin)
    # No callers on this facade: looking up a stale factory route would fail.
    facade = routes.RoutedToolCaller(primary_callers=[], fallback_caller=None, logger=None)
    assert facade.build_tool_result_messages(response, []) == expected
    assert facade.build_assistant_tool_calls_message(response)["content"] == "origin"
    assert facade.build_synthetic_tool_evidence_message(response, "read", {}, "result")["content"] == "origin evidence"


def test_stale_generation_cannot_start_a_followup_wire_request():
    routes = load_personification_module("plugin.personification.core.ai_routes")
    async def run():
        current = state()
        token = fence.bind_generation(current)
        async def forbidden(*args, **kwargs):
            raise AssertionError("superseded route must not open another request")
        caller = SimpleNamespace(chat_with_tools=forbidden)
        facade = routes.RoutedToolCaller(primary_callers=[caller], fallback_caller=None, logger=None)
        fence.invalidate_generation(current)
        try:
            with pytest.raises(fence.SupersededGeneration):
                await facade._call_provider_with_trace(caller, [], [], False, {}, reframe=True)
        finally:
            fence.reset_generation(token)
    asyncio.run(run())


def test_cancelled_context_rejects_yaml_local_state_copy():
    original = state()
    local_copy = dict(original)
    token = fence.bind_generation(original)
    try:
        original["_generation_invalidated"] = True
        with pytest.raises(fence.SupersededGeneration):
            commit.mark_reply_delivery_started(local_copy)
        pipeline = load_personification_module("plugin.personification.handlers.reply_pipeline.pipeline_context")
        assert pipeline.batch_has_newer_messages(local_copy)
    finally:
        fence.reset_generation(token)


def test_pending_relation_judge_keeps_snapshot_without_cancelled_reply_lifetime():
    async def run():
        config = SimpleNamespace(personification_model="old")
        snapshot = fence.capture_config_snapshot(config)
        current = state()
        token = fence.bind_generation(current, config)
        current["_generation_invalidated"] = True
        config.personification_model = "new"
        async def api(messages):
            fence.assert_current_generation()
            assert fence.active_config(config).personification_model == "old"
            return '{"results":[{"message_id":"2","relation":"related"}]}'
        try:
            result = await relation.build_relation_judge(api, config_snapshot=snapshot)([], [{"message_id":"2"}])
            assert result["results"][0]["relation"] == "related"
            assert not fence.generation_is_current()
        finally:
            fence.reset_generation(token)
    asyncio.run(run())
