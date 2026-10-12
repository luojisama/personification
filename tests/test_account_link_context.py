from __future__ import annotations

import asyncio
import json
import time
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest

from ._loader import load_personification_module

sharing = load_personification_module("plugin.personification.core.account_link_context")
links = load_personification_module("plugin.personification.core.account_links")
memory = load_personification_module("plugin.personification.core.memory_context")
gate = load_personification_module("plugin.personification.core.memory_recall_gate")
identity = load_personification_module("plugin.personification.core.llm_context")
session = load_personification_module("plugin.personification.core.session_store")
temporal = load_personification_module("plugin.personification.core.temporal_memory")


class LinkService:
    def __init__(self):
        self.link = links.LinkedAccount("123", "456", "900", "100", 1.0, "verified-1")

    def resolve_telegram(self, bot, user):
        return self.link if self.link and (bot, user) == (self.link.tg_bot_id, self.link.tg_user_id) else None

    def links_for_qq(self, bot, user):
        return [self.link] if self.link and (bot, user) == (self.link.qq_bot_id, self.link.qq_user_id) else []


class Policy:
    def __init__(self, allow=True):
        self.allow = allow
        self.calls = []
        self.service = SimpleNamespace(authorize=lambda _user: SimpleNamespace(allow_context_read=self.allow))

    async def current_authorization(self, user):
        self.calls.append(user)
        return SimpleNamespace(allow_context_read=self.allow)

    async def filter_context_messages(self, items, **_kwargs):
        return items, []

    async def ensure_current(self, _event):
        return None

    async def allows_current(self, _event):
        return True


@pytest.fixture
def runtime(monkeypatch):
    monkeypatch.setattr(session, "get_recent_session_candidates", lambda *_a, **_k: [])
    monkeypatch.setattr(temporal, "load_current_states", lambda _scope: [])
    provider = load_personification_module("plugin.personification.core.provider_router")
    monkeypatch.setattr(provider, "_read_env_api_pool_raw", lambda: "")
    return SimpleNamespace(plugin_config=SimpleNamespace(personification_memory_query_planning_enabled=False),
                           account_link_service=LinkService(), user_policy_gate=Policy())


def current_scope(runtime, direction="telegram"):
    link = runtime.account_link_service.link
    if direction == "telegram":
        bot, user = link.telegram_scope
        return dict(platform="telegram", bot_id=bot, user_id=user, group_id="")
    return dict(platform="onebot", bot_id=link.qq_bot_id, user_id=link.qq_user_id, group_id="")


def linked_source(runtime, direction="telegram"):
    return sharing.resolve_linked_sources(runtime, current_scope(runtime, direction))[0]


def record(source, memory_id="linked", **overrides):
    return {**source.scope, "memory_id": memory_id, "summary": "PRIVATEKEY 棋类比赛", "permission_type": "private_fact",
            "memory_type": "fact", "score": .95, "confidence": .95, "supports_recall": True,
            "source_refs": [dict(message_id="original", user_id=source.user_id, timestamp=123)], **overrides}


def history_row(source, role="user", **overrides):
    return {**source.scope, "id": 1, "role": role, "content": "原账号的私聊", "timestamp": time.time(),
            "user_id": source.bot_id if role == "assistant" else source.user_id,
            "source_kind": "bot_reply" if role == "assistant" else "user", "delivery_status": "confirmed", **overrides}


@pytest.mark.parametrize("direction", ["telegram", "onebot"])
def test_verified_link_resolves_both_directions_without_identity_alias(runtime, direction):
    scope = current_scope(runtime, direction)
    original = deepcopy(scope)
    token = identity.set_llm_context(**scope)
    try:
        transport_identity = deepcopy(identity.current_llm_context())
        source = linked_source(runtime, direction)
        assert source.platform == ("onebot" if direction == "telegram" else "telegram")
        assert source.active() and asyncio.run(sharing.source_authorized(runtime, source))
        assert scope == original and identity.current_llm_context() == transport_identity
        assert sharing.resolve_linked_sources(runtime, {**scope, "group_id": "300"}) == []
        for surface in ("qzone", "qzone_comment", "public", "followup"):
            assert sharing.resolve_linked_sources(runtime, scope, surface) == []
        assert sharing.resolve_linked_sources(SimpleNamespace(), scope) == []
        assert sharing.resolve_linked_sources(runtime, {**scope, "bot_id": "wrong"}) == []
        if direction == "telegram":
            assert sharing.resolve_linked_sources(runtime, {**scope, "user_id": "telegram:456"}) == []
            assert sharing.resolve_linked_sources(runtime, {**scope, "user_id": scope["user_id"].replace("u3:", "u99:")}) == []
    finally:
        identity.reset_llm_context(token)


@pytest.mark.parametrize("case", ["no_policy", "denied", "unlinked"])
def test_context_read_requires_current_source_policy(runtime, case):
    scope = current_scope(runtime)
    source = linked_source(runtime)
    if case == "no_policy":
        runtime.user_policy_gate = None
    elif case == "denied":
        runtime.user_policy_gate.allow = False
    else:
        runtime.account_link_service.link = None
    assert not asyncio.run(sharing.source_authorized(runtime, source))
    assert asyncio.run(sharing.collect_linked_context(runtime, scope)) == []


@pytest.mark.parametrize("direction", ["telegram", "onebot"])
def test_broad_memory_candidates_are_exact_private_only(runtime, direction):
    source = linked_source(runtime, direction)
    allowed = record(source)
    excluded = [dict(allowed, memory_id=f"bad-{n}", **changes) for n, changes in enumerate([
        {"group_id": "300"}, {"platform": "other"}, {"bot_id": "other"}, {"user_id": "other"},
        {"bot_id": ""}, {"platform": ""}, {"topic_id": "topic"}, {"thread_id": "thread"},
        {"expires_at": 1}, {"expires_at": "broken"}, {"expires_at": float("nan")},
        {"supports_recall": False}, {"memory_type": "experience_card"},
    ])]
    captured = []
    async def recall(**kwargs):
        captured.append(kwargs)
        return [allowed, *excluded]
    runtime.memory_store = SimpleNamespace(arecall_memories=recall)
    found = asyncio.run(sharing.recall_linked_memories(runtime, source, "PRIVATEKEY"))
    assert [item["memory_id"] for item in found] == ["linked"]
    assert captured[0]["platform"] == source.platform and captured[0]["bot_id"] == source.bot_id
    assert captured[0]["context_type"] == "private" and captured[0]["group_id"] == ""
    assert found[0]["source_scope"]["user_id"] == source.user_id
    assert found[0]["source_refs"][0]["user_id"] == source.user_id
    allowed["source_refs"] = "malformed"
    assert asyncio.run(sharing.recall_linked_memories(runtime, source, "PRIVATEKEY"))[0]["source_refs"] == []


@pytest.mark.parametrize("stage", ["recall", "policy"])
def test_unlink_during_await_discards_read(runtime, stage):
    source = linked_source(runtime)
    async def recall(**_kwargs):
        if stage == "recall":
            runtime.account_link_service.link = None
        await asyncio.sleep(0)
        return [record(source)]
    runtime.memory_store = SimpleNamespace(arecall_memories=recall)
    if stage == "policy":
        async def authorize(_user):
            runtime.account_link_service.link = None
            await asyncio.sleep(0)
            return SimpleNamespace(allow_context_read=True)
        runtime.user_policy_gate.current_authorization = authorize
    assert asyncio.run(sharing.recall_linked_memories(runtime, source, "PRIVATEKEY")) == []


def test_history_preserves_speakers_and_rejects_other_sources(runtime, monkeypatch):
    source = linked_source(runtime)
    rows = [history_row(source), history_row(source, "assistant", id=2, content="Bot 的建议")]
    rows += [history_row(source, id=3, **changes) for changes in [
        {"group_id": "300"}, {"topic_id": "topic"}, {"thread_id": "thread"}, {"platform": "other"},
        {"bot_id": "other"}, {"user_id": "other"}, {"is_summary": True}, {"source_kind": "plugin"},
        {"source_kind": "peer_bot_reply"}, {"role": "system"},
    ]]
    rows += [history_row(source, "assistant", id=4, **changes) for changes in [
        {"delivery_status": "unknown"}, {"delivery_status": "failed"}, {"source_kind": "plugin"}, {"user_id": source.user_id},
    ]]
    captured = []
    def read(session_id, **kwargs):
        captured.append((session_id, kwargs))
        return rows
    monkeypatch.setattr(session, "get_recent_session_candidates", read)
    found = asyncio.run(sharing.read_linked_history(runtime, source, limit=384, days=2))
    assert [(item["role"], item["speaker"]) for item in found] == [("user", source.user_id), ("assistant", source.bot_id)]
    assert found[1]["content"] == "Bot 的建议" and found[1]["source_scope"] == source.metadata()
    assert found[0]["time"] != "时间未知"
    assert captured == [(session.build_private_session_id(source.user_id), dict(limit=48, days=2, platform=source.platform, bot_id=source.bot_id, query_terms=()))]
    def unlink(*_a, **_k):
        runtime.account_link_service.link = None
        return rows
    monkeypatch.setattr(session, "get_recent_session_candidates", unlink)
    assert asyncio.run(sharing.read_linked_history(runtime, source)) == []


def test_gate_trusts_full_tuple_only(runtime):
    source = linked_source(runtime)
    item = record(source)
    trusted = ((source.platform, source.bot_id, source.user_id),)
    assert gate._hard_filter(item, now=0, linked_private_scopes=trusted)[0]
    for changes in ({"bot_id": "other"}, {"platform": "other"}, {"user_id": "other"}, {"group_id": "300"},
                    {"topic_id": "topic"}, {"thread_id": "thread"}, {"permission_type": "sensitive_memory"},
                    {"permission_type": "conflict_memory"}):
        assert not gate._hard_filter({**item, **changes}, now=0, linked_private_scopes=trusted)[0]
    assert not gate._hard_filter(item, now=0, private_owner_id="different")[0]


def test_actual_stores_project_personal_profile_relationship_experiences_and_archived_history(runtime, monkeypatch, tmp_path):
    from .test_memory_temporal_context import wire
    connect = wire(monkeypatch, tmp_path)
    paths = load_personification_module("plugin.personification.core.paths")
    monkeypatch.setattr(paths, "get_data_dir", lambda _cfg=None: tmp_path)
    store_mod = load_personification_module("plugin.personification.core.memory_store")
    profile_mod = load_personification_module("plugin.personification.core.profile_service")
    scoped_mod = load_personification_module("plugin.personification.core.scoped_profile_service")
    favor_mod = load_personification_module("plugin.personification.core.favorability")
    cards_mod = load_personification_module("plugin.personification.core.experience_cards")
    runtime.plugin_config.personification_data_dir = str(tmp_path)
    runtime.plugin_config.personification_experience_cards_enabled = True
    runtime.memory_store = store_mod.MemoryStore(runtime.plugin_config)
    runtime.memory_store.initialize()
    runtime.profile_service = profile_mod.ProfileService(runtime.memory_store)
    # Every database/store touched by the actual services belongs to tmp_path.
    monkeypatch.setattr(scoped_mod, "get_data_store", lambda: SimpleNamespace(load_sync=lambda _name: {}))
    runtime.scoped_profile_service = scoped_mod.ScopedProfileService(profile_service=runtime.profile_service,
        tool_caller=None, logger=SimpleNamespace(), db_path=tmp_path / "isolated.db")
    source = linked_source(runtime)
    runtime.scoped_profile_service.put_scoped_document_v3(**source.scope, document={"claims": [
        {"key": "interests", "value": "私人下棋", "evidence_refs": [{"raw": "discard"}]},
        {"key": "group_role", "value": "群管理员"}]})
    runtime.scoped_profile_service.put_scoped_document_v3(**{**source.scope, "group_id": "300"},
        document={"claims": [{"key": "interests", "value": "群里内容不能共享"}]})
    runtime.profile_service.upsert_core_profile(user_id=source.user_id, profile_text="原文含群资料不能投影",
        profile_json={"structured": {"兴趣": "legacy 棋类", "群角色": "旧群角色"}, "qq_profile": {
            "nickname": "个人昵称", "age": 22, "group_card": "群名片", "role": "admin", "title": "群头衔"}})
    relation_document = {source.user_id: {"favorability": 42, "blacklist": True,
                                        "group_profiles": {"300": {"favorability": 99}}, "event_history": [{"reason": "群内理由"}]}}
    relation_before = deepcopy(relation_document)
    monkeypatch.setattr(favor_mod, "get_data_store", lambda: SimpleNamespace(load_sync=lambda _name: deepcopy(relation_document)))
    runtime.favorability_service = favor_mod.FavorabilityService(plugin_config=runtime.plugin_config)
    cards = cards_mod.ExperienceService(runtime.memory_store, source_reader=lambda scope, message_id: {
        "user_id": scope.user_id or source.user_id, "timestamp": time.time()})
    own_scope = cards_mod.ExperienceScope(**source.scope)
    personal_card = cards.create(own_scope, summary="私聊排错经历", source_refs=[{"message_id": "private-1"}])
    stable_card = cards.create(own_scope, summary="确认为棋手", source_refs=[{"message_id": "private-2"}],
        kind="stable_identity", confirmation="confirmed", expires_at=time.time() + 10000)
    cards.create(cards_mod.ExperienceScope(**{**source.scope, "group_id": "300"}), summary="群内经历不共享",
                 source_refs=[{"message_id": "group-1"}])
    runtime.memory_store.write_memory_item(record(source, summary="PRIVATEKEY 私人比赛"))
    runtime.memory_store.write_memory_item({**record(source, "group-long"), "group_id": "300"})
    runtime.memory_store.write_memory_item({**record(source, "legacy-long"), "bot_id": ""})
    now = time.time()
    with connect() as conn:
        for n, row in enumerate([history_row(source, content="私聊用户原话"), history_row(source, "assistant", content="Bot 私聊原话")], 1):
            conn.execute("INSERT INTO session_messages VALUES(?,?,?,?,?,?,?)", (n, session.build_private_session_id(source.user_id),
                row["role"], json.dumps(row["content"]), 0, now + n, json.dumps(row)))
        conn.commit()
    assert session._replace_history_with_summary_sync(session.build_private_session_id(source.user_id), "旧摘要不共享",
        candidate_ids=[1], cutoff_id=1, keep_boundary_timestamp=now + 2)
    # Restore the actual database reader after runtime fixture's safe empty stub.
    monkeypatch.setattr(session, "get_recent_session_candidates", _ACTUAL_SESSION_READER)
    result = asyncio.run(sharing.collect_linked_context(runtime, current_scope(runtime)))
    assert len(result) == 1
    rendered = json.dumps(result[0].render_payload(), ensure_ascii=False)
    assert "私人下棋" in rendered and "legacy 棋类" in rendered and "个人昵称" in rendered
    assert "legacy_onebot_user_global" in rendered and '"score": 42.0' in rendered
    for excluded in ("群管理员", "群里内容不能共享", "旧群角色", "群名片", "群头衔", "群内理由", "blacklist", "原文含群资料", "discard", "旧摘要"):
        assert excluded not in rendered
    assert [(row["role"], row["speaker"]) for row in result[0].history] == [("user", source.user_id), ("assistant", source.bot_id)]
    assert [item["memory_id"] for item in result[0].experience_candidates] == [personal_card["memory_id"]]
    assert stable_card["memory_id"] in rendered and "群内经历不共享" not in rendered
    found = asyncio.run(sharing.recall_linked_memories(runtime, source, "PRIVATEKEY"))
    assert [item["memory_id"] for item in found] == ["linked"]
    assert relation_document == relation_before
    # Reverse projection uses the real namespaced Telegram document and cards,
    # without reading the user-global QQ compatibility store for Telegram.
    reverse_source = linked_source(runtime, "onebot")
    runtime.scoped_profile_service.put_scoped_document_v3(**reverse_source.scope,
        document={"claims": [{"key": "interests", "value": "Telegram 私人围棋"}]})
    other_bot, other_user = links.telegram_user_scope("999", "456")
    other_scope = dict(platform="telegram", bot_id=other_bot, user_id=other_user, group_id="")
    runtime.scoped_profile_service.put_scoped_document_v3(**other_scope,
        document={"claims": [{"key": "interests", "value": "另一Bot同数字用户不能共享"}]})
    assert runtime.scoped_profile_service.get_scoped_document_v3(**other_scope)["document"]["claims"][0]["value"] == "另一Bot同数字用户不能共享"
    with pytest.raises(ValueError, match="invalid user_id"):
        runtime.scoped_profile_service.put_scoped_document_v3(**{**other_scope, "user_id": reverse_source.user_id}, document={})
    with pytest.raises(ValueError, match="invalid user_id"):
        runtime.scoped_profile_service.get_scoped_document_v3(**{**reverse_source.scope, "user_id": "telegram:456"})
    reverse_card = cards.create(cards_mod.ExperienceScope(**reverse_source.scope), summary="Telegram 私聊经历",
                               source_refs=[{"message_id": "tg-private"}])
    reverse = asyncio.run(sharing.collect_linked_context(runtime, current_scope(runtime, "onebot")))
    assert "Telegram 私人围棋" in json.dumps(reverse[0].render_payload(), ensure_ascii=False)
    assert "另一Bot同数字用户" not in json.dumps(reverse[0].render_payload(), ensure_ascii=False)
    assert [item["memory_id"] for item in reverse[0].experience_candidates] == [reverse_card["memory_id"]]
    assert all(item["source_scope"]["storage_scope"] != "legacy_onebot_user_global" for item in reverse[0].profiles)


# Capture the original reader once; fixtures replace only the exposed function.
_ACTUAL_SESSION_READER = session.get_recent_session_candidates


def test_category_failure_and_disabled_features_do_not_discard_history(runtime, monkeypatch):
    source = linked_source(runtime)
    def broken(*_a, **_k):
        raise ValueError("isolated malformed profile")
    runtime.scoped_profile_service = SimpleNamespace(get_scoped_document_v3=broken)
    runtime.profile_service = SimpleNamespace(get_core_profile=broken)
    runtime.favorability_service = SimpleNamespace(peek_user_data=broken)
    monkeypatch.setattr(session, "get_recent_session_candidates", lambda *_a, **_k: [history_row(source)])
    result = asyncio.run(sharing.collect_linked_context(runtime, current_scope(runtime)))
    assert result[0].history and not result[0].profiles
    runtime.plugin_config.personification_persona_enabled = False
    runtime.plugin_config.personification_favorability_enabled = False
    def forbidden(*_a, **_k):
        raise AssertionError("disabled category was read")
    runtime.scoped_profile_service.get_scoped_document_v3 = forbidden
    runtime.profile_service.get_core_profile = forbidden
    runtime.favorability_service.peek_user_data = forbidden
    assert asyncio.run(sharing.collect_linked_context(runtime, current_scope(runtime)))[0].history


def test_legacy_group_document_and_group_generated_claims_are_not_shared(runtime):
    personal = {"key": "communication_style", "value": "个人喜欢简短", "source": "user_confirmed"}
    group_only = {"key": "nickname_pref", "value": "本群专有称呼", "source": "group_generated"}
    document = {"scope": {"kind": "global"}, "claims": [personal, group_only]}
    runtime.profile_service = SimpleNamespace(get_core_profile=lambda _user: SimpleNamespace(
        profile_json={"scoped_profile": document, "qq_profile": {"nickname": "个人昵称", "signature": "x" * 10000}}))
    contexts = asyncio.run(sharing.collect_linked_context(runtime, current_scope(runtime)))
    rendered = json.dumps(contexts[0].render_payload(), ensure_ascii=False)
    assert "个人喜欢简短" in rendered and "本群专有称呼" not in rendered
    assert len(contexts[0].profiles[0]["personal_metadata"]["signature"]) == 240
    document["scope"]["kind"] = "group"
    rendered = json.dumps(asyncio.run(sharing.collect_linked_context(runtime, current_scope(runtime)))[0].render_payload(), ensure_ascii=False)
    assert "个人喜欢简短" not in rendered and "本群专有称呼" not in rendered
    assert "个人昵称" in rendered


def test_source_history_uses_existing_effective_retention_and_budget(runtime, monkeypatch):
    source = linked_source(runtime)
    runtime.plugin_config.personification_private_history_days = 2
    runtime.plugin_config.__pydantic_fields_set__ = {"personification_private_history_days"}
    captured = []
    def read(*_a, **kwargs):
        captured.append(kwargs)
        return [history_row(source, id=n, content="有预算的私聊原话" * 100) for n in range(12)]
    monkeypatch.setattr(session, "get_recent_session_candidates", read)
    result = asyncio.run(sharing.collect_linked_context(runtime, current_scope(runtime)))
    assert captured[0]["days"] == 2
    budget = load_personification_module("plugin.personification.core.context_budget")
    assert budget.estimate_request_tokens(result[0].history, []) <= 2048
    assert len(result[0].history) < 12


@pytest.mark.parametrize("unlink", [False, True])
def test_one_semantic_gate_includes_linked_candidate_without_starvation_and_unlink_invalidation(runtime, monkeypatch, unlink):
    source = linked_source(runtime)
    scope = current_scope(runtime)
    runtime.plugin_config.personification_memory_auto_recall_candidate_limit = 2
    runtime.plugin_config.personification_memory_auto_recall_inject_limit = 2
    runtime.scoped_profile_service = SimpleNamespace(get_scoped_document_v3=lambda **_scope: (
        {"document": {"claims": [{"key": "interests", "value": "共享棋类"}]}} if _scope["platform"] == source.platform else None))
    async def recall(**kwargs):
        if kwargs["platform"] == source.platform:
            return [record(source)]
        return [{**scope, "memory_id": f"native-{n}", "summary": f"本账号比赛 {n}", "permission_type": "private_fact",
                 "score": .99, "confidence": .99} for n in range(32)]
    runtime.memory_store = SimpleNamespace(arecall_memories=recall)
    calls = []
    async def judge(**kwargs):
        calls.append(kwargs)
        assert identity.current_llm_context()["purpose"] == "memory_recall_gate"
        assert "linked" in kwargs["messages"][-1]["content"]
        if unlink:
            runtime.account_link_service.link = None
        return SimpleNamespace(content=json.dumps({"keep_memory_ids": ["linked", "native-0"], "drop_memory_ids": []}))
    runtime.lite_tool_caller = SimpleNamespace(chat_with_tools=judge)
    token = identity.set_llm_context(**scope)
    try:
        result = asyncio.run(memory.prepare_memory_context(runtime=runtime, event=SimpleNamespace(**scope),
            bot=SimpleNamespace(self_id=scope["bot_id"]), messages=[{"role": "user", "content": "棋类比赛"}], refresh=False))
        assert identity.current_llm_context()["user_id"] == scope["user_id"]
    finally:
        identity.reset_llm_context(token)
    assert len(calls) == 1 and calls[0]["tools"] == [] and calls[0]["use_builtin_search"] is False
    assert result.diagnostics["candidate_count"] == 2 and result.diagnostics["injected_count"] == (1 if unlink else 2)
    assert [item["memory_id"] for item in result.memories] == ["native-0"]
    if unlink:
        assert not result.linked_accounts and "共享棋类" not in result.render()
    else:
        assert result.linked_accounts[0].memories[0]["memory_id"] == "linked"
        assert "共享棋类" in result.render()
        old_link = runtime.account_link_service.link
        runtime.account_link_service.link = None
        assert "linked_account_reference" not in result.render()
        runtime.account_link_service.link = replace(old_link, link_id="verified-2")
        assert "linked_account_reference" not in result.render()


def test_context_only_adds_local_shared_references_without_remote_calls(runtime, monkeypatch):
    source = linked_source(runtime)
    scope = current_scope(runtime)
    runtime.scoped_profile_service = SimpleNamespace(get_scoped_document_v3=lambda **_scope: {"document": {"claims": [{"key": "interests", "value": "共享棋类"}]}})
    monkeypatch.setattr(session, "get_recent_session_candidates", lambda *_a, **_k: [history_row(source)])
    def forbidden(*_a, **_k):
        raise AssertionError("context-only performed remote/model work")
    runtime.memory_store = SimpleNamespace(arecall_memories=forbidden)
    runtime.lite_tool_caller = SimpleNamespace(chat_with_tools=forbidden)
    monkeypatch.setattr(temporal, "update_current_states", forbidden)
    token = identity.set_llm_context(**scope)
    try:
        result = asyncio.run(memory.prepare_memory_context(runtime=runtime, event=SimpleNamespace(**scope),
            bot=SimpleNamespace(self_id=scope["bot_id"]), messages=[{"role": "user", "content": "还在吗"}],
            execution_policy=SimpleNamespace(is_context_only=True)))
    finally:
        identity.reset_llm_context(token)
    assert result.status == "local_context_only" and result.linked_accounts[0].history
    assert "共享棋类" in result.render() and "原账号的私聊" in result.render()


@pytest.mark.parametrize("context_only", [False, True])
def test_policy_revocation_at_final_preparation_boundary_removes_profiles(runtime, monkeypatch, context_only):
    scope = current_scope(runtime)
    source = linked_source(runtime)
    runtime.scoped_profile_service = SimpleNamespace(get_scoped_document_v3=lambda **profile_scope: (
        {"document": {"claims": [{"key": "interests", "value": "撤权后不外发"}]}} if profile_scope == source.scope else None))
    async def recall(**_kwargs):
        return []
    runtime.memory_store = SimpleNamespace(arecall_memories=recall)
    def load_states(_scope):
        if context_only:
            runtime.user_policy_gate.allow = False
        return []
    monkeypatch.setattr(temporal, "load_current_states", load_states)
    async def update_states(**_kwargs):
        await asyncio.sleep(.02)
        runtime.user_policy_gate.allow = False
        return []
    monkeypatch.setattr(temporal, "update_current_states", update_states)
    token = identity.set_llm_context(**scope)
    try:
        result = asyncio.run(memory.prepare_memory_context(runtime=runtime, event=SimpleNamespace(**scope),
            bot=SimpleNamespace(self_id=scope["bot_id"]), messages=[{"role": "user", "content": "还在吗"}],
            execution_policy=SimpleNamespace(is_context_only=context_only)))
    finally:
        identity.reset_llm_context(token)
    assert runtime.account_link_service.link and not result.linked_accounts
    assert "撤权后不外发" not in result.render()


def test_policy_revocation_before_judge_never_exposes_linked_candidate(runtime, monkeypatch):
    scope = current_scope(runtime)
    source = linked_source(runtime)
    context = sharing.LinkedAccountContext(source, profiles=[{"claims": "撤权后不外发"}],
        experience_candidates=[record(source)], policy_gate=runtime.user_policy_gate)
    async def collect(*_a, **_k):
        return [context]
    monkeypatch.setattr(sharing, "collect_linked_context", collect)
    async def recall(**kwargs):
        runtime.user_policy_gate.allow = False
        return [{**scope, "memory_id": "native", "summary": "本账号比赛", "permission_type": "private_fact", "score": .99}]
    runtime.memory_store = SimpleNamespace(arecall_memories=recall)
    calls = []
    async def judge(**kwargs):
        calls.append(kwargs)
        assert "PRIVATEKEY" not in str(kwargs["messages"])
        return SimpleNamespace(content='{"keep_memory_ids":["native"],"drop_memory_ids":[]}')
    runtime.lite_tool_caller = SimpleNamespace(chat_with_tools=judge)
    token = identity.set_llm_context(**scope)
    try:
        result = asyncio.run(memory.prepare_memory_context(runtime=runtime, event=SimpleNamespace(**scope),
            bot=SimpleNamespace(self_id=scope["bot_id"]), messages=[{"role": "user", "content": "比赛"}], refresh=False))
    finally:
        identity.reset_llm_context(token)
    assert len(calls) == 1 and result.memories[0]["memory_id"] == "native"
    assert not result.linked_accounts and "撤权后不外发" not in result.render()


@pytest.mark.parametrize("missing", ["service", "async_authorize", "async_legacy", "broken"])
def test_render_requires_synchronous_current_policy_contract(runtime, missing):
    source = linked_source(runtime)
    context = sharing.LinkedAccountContext(source, profiles=[{"value": "私人资料"}], policy_gate=runtime.user_policy_gate)
    assert context.render_payload()
    async def async_check(_user):
        return SimpleNamespace(allow_context_read=True)
    if missing == "service":
        runtime.user_policy_gate.service = None
    elif missing == "async_authorize":
        runtime.user_policy_gate.service.authorize = async_check
    elif missing == "async_legacy":
        runtime.user_policy_gate.legacy_block_checker = async_check
    else:
        def broken(_user):
            raise OSError("isolated unavailable policy")
        runtime.user_policy_gate.service.authorize = broken
    assert context.render_payload() is None


def test_real_policy_revocation_between_prepare_and_render_is_immediate(runtime, monkeypatch, tmp_path):
    db = load_personification_module("plugin.personification.core.db")
    user_policy = load_personification_module("plugin.personification.core.user_policy")
    qq_policy = load_personification_module("plugin.personification.core.qq_user_policy")
    path = db.init_db_sync(tmp_path)
    service = user_policy.UserPolicyService(db_path=path, evidence_key=b"q" * 32)
    runtime.user_policy_gate = qq_policy.QQUserPolicyGate(service)
    source = linked_source(runtime)
    runtime.scoped_profile_service = SimpleNamespace(get_scoped_document_v3=lambda **_scope: {"document": {
        "claims": [{"key": "interests", "value": "绑定账号私人资料"}]}})
    try:
        contexts = asyncio.run(sharing.collect_linked_context(runtime, current_scope(runtime)))
        prepared = memory.PreparedMemoryContext(linked_accounts=contexts)
        assert "绑定账号私人资料" in prepared.render()
        service.set_manual_override(user_id=source.user_id, mode="block", expected_revision=0,
                                    actor="isolated-test", reason_code="isolated-revoke")
        assert runtime.account_link_service.link and "绑定账号私人资料" not in prepared.render()
    finally:
        asyncio.run(db.close_db())


@pytest.mark.parametrize("pipeline", ["normal", "yaml"])
def test_real_reply_processors_receive_shared_reference_and_keep_current_scope(runtime, monkeypatch, tmp_path, pipeline):
    from scripts.quality_eval.pipeline_adapter import run_full_path_case
    db = load_personification_module("plugin.personification.core.db")
    source = linked_source(runtime, "onebot")
    monkeypatch.setattr(session, "get_recent_session_candidates", lambda *_a, **_k: [history_row(source, content="绑定账号私聊原话")])
    original_prepare = memory.prepare_memory_context
    prepared = []
    generation = []
    purposes = []
    async def inject_runtime(**kwargs):
        current = kwargs["runtime"]
        current.account_link_service = runtime.account_link_service
        current.user_policy_gate = runtime.user_policy_gate
        current.scoped_profile_service = SimpleNamespace(get_scoped_document_v3=lambda **scope: (
            {"document": {"claims": [{"key": "interests", "value": "绑定账号个人画像"}]}} if scope["platform"] == source.platform else None))
        current.memory_store = SimpleNamespace(arecall_memories=lambda **_kw: pytest.fail("idle chat remote recall"))
        result = await original_prepare(**kwargs)
        prepared.append(result)
        return result
    monkeypatch.setattr(memory, "prepare_memory_context", inject_runtime)
    class Caller:
        async def chat_with_tools(self, messages, tools, use_builtin_search):
            purpose = identity.current_llm_context().get("purpose", "")
            purposes.append(purpose)
            system = "\n".join(str(item.get("content", "")) for item in messages if item.get("role") == "system")
            assert purpose not in {"memory_query_plan", "memory_recall_gate", "memory_state_update"}
            if purpose == "reply_generation":
                generation.append((deepcopy(messages), deepcopy(identity.current_llm_context())))
                content = "<output><message>在呀</message></output>" if pipeline == "yaml" else "在呀"
            elif "speech_act" in system and "reply_action" in system:
                content = json.dumps({"reply_action": "reply", "speech_act": "participate", "memory_need": "none",
                    "research_need": "none", "vision_need": "none", "output_mode": "chat_short", "tool_intent": ["none"],
                    "ambiguity_level": "low", "message_target": "bot", "confidence": .95, "reason": "日常接话"})
            elif "chat_intent" in system and "plugin_question_intent" in system:
                content = json.dumps({"chat_intent": "banter", "ambiguity_level": "low", "confidence": .95,
                    "reason": "日常接话", "evidence_policy": "none", "vision_need": "none"})
            else:
                content = '{"action":"accept","text":"","flags":[],"persona_verdict":"consistent"}'
            return SimpleNamespace(content=content, tool_calls=[], finish_reason="stop", raw={})
    case = {"id": "linked-context-" + pipeline, "surface": "private", "pipeline": pipeline, "bot_id": "900",
        "trusted_persona": "自然接话的群友", "events": [{"kind": "message", "sender": "Alice", "user_id": "100",
        "text": "还在吗", "message_id": "local-private"}]}
    db.init_db_sync(tmp_path)
    try:
        result = asyncio.run(run_full_path_case(case, caller=Caller(), isolated_dir=str(tmp_path),
            behavior_config={"personification_agent_enabled": True, "personification_turn_planner_enabled": True,
                             "personification_final_dialogue_gate_enabled": True}))
    finally:
        asyncio.run(db.close_db())
    assert prepared and prepared[0].status == "local_context_only"
    assert generation and "绑定账号个人画像" in str(generation[0][0]) and "绑定账号私聊原话" in str(generation[0][0]), json.dumps({"purposes": purposes, "result": result}, ensure_ascii=False)
    assert source.user_id in str(generation[0][0])
    assert generation[0][1]["platform"] == "onebot" and generation[0][1]["bot_id"] == "900"
    assert generation[0][1]["user_id"] == "100"
    assert result["status"] == "completed" and result["turns"][0]["reply_delivery_confirmed"]
