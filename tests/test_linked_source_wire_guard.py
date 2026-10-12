"""Cached shared references retain their source checks through external use."""
from __future__ import annotations

import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest

from ._loader import load_personification_module

sharing = load_personification_module("plugin.personification.core.account_link_context")
links = load_personification_module("plugin.personification.core.account_links")
memory = load_personification_module("plugin.personification.core.memory_context")
recall_gate = load_personification_module("plugin.personification.core.memory_recall_gate")
identity = load_personification_module("plugin.personification.core.llm_context")
routes = load_personification_module("plugin.personification.core.ai_routes")
commit = load_personification_module("plugin.personification.handlers.reply_commit")
tool_impl = load_personification_module("plugin.personification.skills.skillpacks.tool_caller.scripts.impl")


class LinkService:
    def __init__(self):
        self.link = links.LinkedAccount("123", "456", "900", "100", 1.0, "verified-1")

    def resolve_telegram(self, bot, user):
        return self.link if self.link and (bot, user) == (self.link.tg_bot_id, self.link.tg_user_id) else None

    def links_for_qq(self, bot, user):
        return [self.link] if self.link and (bot, user) == (self.link.qq_bot_id, self.link.qq_user_id) else []


class Policy:
    def __init__(self):
        self.allow = True
        self.service = SimpleNamespace(authorize=lambda _user: SimpleNamespace(allow_context_read=self.allow))

    async def current_authorization(self, _user):
        return SimpleNamespace(allow_context_read=self.allow)


class CountedCaller:
    def __init__(self, *, count=None, chat=None):
        self.count_calls = []
        self.provider_calls = []
        self._count = count
        self._chat = chat

    async def count_tokens(self, messages, tools):
        self.count_calls.append((messages, tools))
        return await self._count() if self._count else 10

    async def chat_with_tools(self, messages, tools, use_builtin_search):
        self.provider_calls.append((messages, tools, use_builtin_search))
        return await self._chat() if self._chat else response()


def response(content="ready"):
    return tool_impl.ToolCallerResponse("stop", content, [], {})


@pytest.fixture
def runtime(monkeypatch):
    provider = load_personification_module("plugin.personification.core.provider_router")
    monkeypatch.setattr(provider, "_read_env_api_pool_raw", lambda: "")
    monkeypatch.setattr(routes.RoutedToolCaller, "_record_provider_request", lambda *_a, **_kw: None)
    return SimpleNamespace(account_link_service=LinkService(), user_policy_gate=Policy(),
                           plugin_config=SimpleNamespace(personification_memory_query_planning_enabled=False))


def current_scope(runtime, direction="telegram"):
    link = runtime.account_link_service.link
    if direction == "telegram":
        bot, user = link.telegram_scope
        return dict(platform="telegram", bot_id=bot, user_id=user, group_id="")
    return dict(platform="onebot", bot_id=link.qq_bot_id, user_id=link.qq_user_id, group_id="")


def shared_context(runtime, direction="telegram"):
    source = sharing.resolve_linked_sources(runtime, current_scope(runtime, direction))[0]
    return sharing.LinkedAccountContext(source, profiles=[{"value": "cached private reference"}],
                                        policy_gate=runtime.user_policy_gate)


def revoke(runtime, mode):
    if mode == "unlink":
        runtime.account_link_service.link = None
    elif mode == "rebind":
        runtime.account_link_service.link = replace(runtime.account_link_service.link, link_id="verified-2")
    else:
        runtime.user_policy_gate.allow = False


def rendered_messages(context):
    cached = memory.PreparedMemoryContext(linked_accounts=[context]).render()
    assert "cached private reference" in cached
    return [{"role": "system", "content": cached}, {"role": "user", "content": "continue"}]


def routed(caller, fallback=None):
    return routes.RoutedToolCaller(primary_callers=[caller], fallback_caller=fallback, logger=None)


@pytest.mark.parametrize("direction", ["telegram", "onebot"])
@pytest.mark.parametrize("mode", ["unlink", "rebind", "policy"])
def test_revoke_during_post_render_await_stops_counter_and_provider(runtime, direction, mode):
    context = shared_context(runtime, direction)
    caller = CountedCaller()

    async def run():
        token = identity.set_llm_context(**current_scope(runtime, direction))
        try:
            messages = rendered_messages(context)
            await asyncio.sleep(0)
            revoke(runtime, mode)
            with pytest.raises(identity.LLMSourceAuthorizationRevoked):
                await routed(caller).chat_with_tools(messages, [], False)
        finally:
            identity.reset_llm_context(token)

    asyncio.run(run())
    assert not caller.count_calls and not caller.provider_calls


@pytest.mark.parametrize("mode", ["unlink", "rebind", "policy"])
def test_revoke_during_native_token_count_stops_provider(runtime, mode):
    context = shared_context(runtime)

    async def count():
        await asyncio.sleep(0)
        revoke(runtime, mode)
        return 10

    caller = CountedCaller(count=count)

    async def run():
        token = identity.set_llm_context(**current_scope(runtime))
        try:
            with pytest.raises(identity.LLMSourceAuthorizationRevoked):
                await routed(caller).chat_with_tools(rendered_messages(context), [], False)
        finally:
            identity.reset_llm_context(token)

    asyncio.run(run())
    assert len(caller.count_calls) == 1 and not caller.provider_calls


@pytest.mark.parametrize("mode", ["unlink", "rebind", "policy"])
def test_revoke_during_provider_await_discards_response(runtime, mode):
    context = shared_context(runtime)

    async def chat():
        await asyncio.sleep(0)
        revoke(runtime, mode)
        return response()

    caller = CountedCaller(chat=chat)

    async def run():
        token = identity.set_llm_context(**current_scope(runtime))
        try:
            with pytest.raises(identity.LLMSourceAuthorizationRevoked):
                await routed(caller).chat_with_tools(rendered_messages(context), [], False)
        finally:
            identity.reset_llm_context(token)

    asyncio.run(run())
    assert len(caller.count_calls) == len(caller.provider_calls) == 1


def test_revoke_during_retry_backoff_cancels_without_second_request(runtime, monkeypatch):
    context = shared_context(runtime)
    original_sleep = asyncio.sleep
    backoff = []

    async def fail():
        error = RuntimeError("HTTP 503")
        error.status_code = 503
        raise error

    async def revoke_in_backoff(delay):
        backoff.append(delay)
        await original_sleep(0)
        revoke(runtime, "unlink")

    monkeypatch.setattr(routes.asyncio, "sleep", revoke_in_backoff)
    caller = CountedCaller(chat=fail)

    async def run():
        token = identity.set_llm_context(**current_scope(runtime))
        try:
            with pytest.raises(identity.LLMSourceAuthorizationRevoked):
                await routed(caller).chat_with_tools(rendered_messages(context), [], False)
        finally:
            identity.reset_llm_context(token)

    asyncio.run(run())
    assert backoff == [1.0]
    assert len(caller.count_calls) == len(caller.provider_calls) == 1


def test_revoke_after_failed_primary_stops_fallback(runtime):
    context = shared_context(runtime)

    async def fail():
        revoke(runtime, "policy")
        error = RuntimeError("HTTP 400")
        error.status_code = 400
        raise error

    primary, fallback = CountedCaller(chat=fail), CountedCaller()

    async def run():
        token = identity.set_llm_context(**current_scope(runtime))
        try:
            with pytest.raises(identity.LLMSourceAuthorizationRevoked):
                await routed(primary, fallback).chat_with_tools(rendered_messages(context), [], False)
        finally:
            identity.reset_llm_context(token)

    asyncio.run(run())
    assert len(primary.provider_calls) == 1
    assert not fallback.count_calls and not fallback.provider_calls


@pytest.mark.parametrize("mode", ["unlink", "rebind", "policy"])
def test_revoked_source_cannot_reserve_wire_attempt_or_start_delivery(runtime, mode):
    token = identity.set_llm_context(**current_scope(runtime))
    budget_token = identity.set_wire_budget(2)
    state = {}
    try:
        rendered_messages(shared_context(runtime))
        revoke(runtime, mode)
        with pytest.raises(identity.LLMSourceAuthorizationRevoked):
            identity.reserve_wire_attempt()
        assert identity.wire_budget_remaining() == 2
        with pytest.raises(identity.LLMSourceAuthorizationRevoked):
            commit.mark_reply_delivery_started(state)
        assert not state.get("reply_delivery_started")
    finally:
        identity.reset_llm_context(budget_token)
        identity.reset_llm_context(token)


def test_guard_in_copied_task_and_nested_purpose_is_not_inherited_by_new_turn(runtime):
    scope = current_scope(runtime)
    context = shared_context(runtime)
    caller = CountedCaller()

    async def run():
        token = identity.set_llm_context(**scope)
        try:
            async def render_in_child():
                purpose_token = identity.set_llm_purpose("memory_recall_gate")
                try:
                    return rendered_messages(context)
                finally:
                    identity.reset_llm_context(purpose_token)
            await asyncio.create_task(render_in_child())
            revoke(runtime, "unlink")
            with pytest.raises(identity.LLMSourceAuthorizationRevoked):
                identity.assert_llm_sources_current()
            next_token = identity.set_llm_context(**scope)
            try:
                identity.assert_llm_sources_current()
                actual = await routed(caller).chat_with_tools([{"role": "user", "content": "fresh turn"}], [], False)
                assert actual.content == "ready"
            finally:
                identity.reset_llm_context(next_token)
            with pytest.raises(identity.LLMSourceAuthorizationRevoked):
                identity.assert_llm_sources_current()
        finally:
            identity.reset_llm_context(token)

    asyncio.run(run())
    assert len(caller.provider_calls) == 1


@pytest.mark.parametrize("has_candidate", [False, True])
def test_semantic_gate_binds_only_sources_in_selected_candidates(runtime, monkeypatch, has_candidate):
    scope = current_scope(runtime)
    context = shared_context(runtime)
    candidate = {**context.source.scope, "memory_id": "shared-candidate", "summary": "a linked private fact",
                 "permission_type": "private_fact", "memory_type": "fact", "score": .99, "confidence": .99}
    if has_candidate:
        context.experience_candidates.append(candidate)

    async def collect(*_a, **_kw):
        return [context]

    async def recall(**_kwargs):
        return []

    async def gate(**kwargs):
        guards = identity.current_llm_context()["turn_state"].get("source_guards", {})
        assert bool(guards) is has_candidate
        assert kwargs["candidates"] == ([candidate] if has_candidate else [])
        if has_candidate:
            # The gate can await its own setup after the candidates were built.
            await asyncio.sleep(0)
            revoke(runtime, "policy")
            with pytest.raises(identity.LLMSourceAuthorizationRevoked):
                identity.assert_llm_sources_current()
        return []

    temporal = load_personification_module("plugin.personification.core.temporal_memory")
    monkeypatch.setattr(temporal, "load_current_states", lambda _scope: [])
    monkeypatch.setattr(sharing, "collect_linked_context", collect)
    monkeypatch.setattr(recall_gate, "gate_memory_candidates", gate)
    runtime.memory_store = SimpleNamespace(arecall_memories=recall)
    token = identity.set_llm_context(**scope)
    try:
        result = asyncio.run(memory.prepare_memory_context(runtime=runtime, event=SimpleNamespace(**scope),
            bot=SimpleNamespace(self_id=scope["bot_id"]), messages=[{"role": "user", "content": "continue"}],
            refresh=False))
        assert result.diagnostics["candidate_count"] == int(has_candidate)
    finally:
        identity.reset_llm_context(token)
