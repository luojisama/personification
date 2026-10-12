"""Real explicit tool reads over isolated stores and verified synthetic links."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from types import SimpleNamespace

import pytest

from ._loader import load_personification_module

agent_runtime = load_personification_module("plugin.personification.core.services.agent_runtime")
impl = load_personification_module("plugin.personification.skills.skillpacks.memory_palace.scripts.impl")
links = load_personification_module("plugin.personification.core.account_links")
context = load_personification_module("plugin.personification.core.llm_context")
TOOLS = ("recall_user_memory", "memory_recall", "search_conversation_history")


class PolicyGate:
    def __init__(self):
        self.allow = True
        self.calls = []
        self.on_check = None
        self.service = SimpleNamespace(authorize=lambda _user: SimpleNamespace(allow_context_read=self.allow))
        self.legacy_block_checker = None

    async def current_authorization(self, user_id):
        self.calls.append(user_id)
        if self.on_check:
            self.on_check(len(self.calls))
        return SimpleNamespace(allow_context_read=self.allow)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    db = load_personification_module("plugin.personification.core.db")
    data_module = load_personification_module("plugin.personification.core.data_store")
    memory_module = load_personification_module("plugin.personification.core.memory_store")
    monkeypatch.setattr(db, "_db_path", tmp_path / "personification.db")
    db.init_db_sync(tmp_path)
    cfg = SimpleNamespace(
        personification_data_dir=str(tmp_path), personification_memory_enabled=True,
        personification_memory_palace_enabled=True, personification_memory_recall_top_k=32,
        personification_memory_search_scan_limit=300, personification_experience_cards_enabled=True,
        personification_real_embedding_enabled=False, personification_embedding_enabled=False,
        personification_use_skillpacks=False, personification_tool_web_fetch_enabled=False,
        personification_60s_enabled=False, personification_agent_memory_write_enabled=False,
        personification_sticker_path=str(tmp_path / "missing_stickers"),
    )
    logger = logging.getLogger("isolated-linked-memory-tools")
    store = memory_module.MemoryStore(plugin_config=cfg, logger=logger)
    store.initialize()
    clock = [time.time()]
    service = links.AccountLinkService(data_module.DataStore(cfg), clock=lambda: clock[0])
    gate = PolicyGate()
    runtime = SimpleNamespace(
        plugin_config=cfg, memory_store=store, account_link_service=service,
        user_policy_gate=gate, qq_user_policy_gate=None, logger=logger,
        background_intelligence=None,
    )
    env = SimpleNamespace(db=db, cfg=cfg, store=store, service=service, gate=gate,
                          runtime=runtime, clock=clock, logger=logger)
    env.link = bind(env)
    env.qq = {"platform": "onebot", "bot_id": "456", "user_id": "42", "group_id": ""}
    env.tg = {"platform": "telegram", "bot_id": env.link.telegram_scope[0],
              "user_id": env.link.telegram_scope[1], "group_id": ""}
    return env


def bind(env):
    claim = env.service.begin(tg_bot_id="123", tg_user_id="42", qq_bot_id="456",
                              qq_user_id="42", issue_code=True)
    assert env.service.mark_delivery(claim.request_id, confirmed=True)
    return env.service.confirm(qq_bot_id="456", qq_user_id="42", code=claim.code)


def memory(env, identity, key, *, summary="SHAREDKEY source memory", **extra):
    env.store.write_memory_item({
        **identity, "memory_id": key, "memory_type": "fact", "palace_zone": "person",
        "summary": summary, "permission_type": "private_fact", "supports_recall": True,
        "confidence": .95, "salience": .9, "time_created": time.time(), **extra,
    })


def history(env, identity, key, *, archived=False, role="user", summary=False, **extra):
    metadata = {**identity, "message_id": key, "speaker": "untrusted-display-name",
                "source_kind": "human_message" if role == "user" else "bot_reply", **extra}
    with env.db.connect_sync() as conn:
        cursor = conn.execute(
            "INSERT INTO session_messages(session_id,role,content,is_summary,timestamp,metadata) VALUES (?,?,?,?,?,?)",
            ("private_" + identity["user_id"], role, json.dumps("SHAREDKEY " + key),
             int(summary), time.time(), json.dumps(metadata)),
        )
        row_id = cursor.lastrowid
        if archived:
            conn.execute("CREATE TABLE IF NOT EXISTS session_message_archive AS SELECT * FROM session_messages WHERE 0")
            conn.execute("INSERT INTO session_message_archive SELECT * FROM session_messages WHERE id=?", (row_id,))
            conn.execute("DELETE FROM session_messages WHERE id=?", (row_id,))
        conn.commit()
    return row_id


def tool(env, name):
    if name == "memory_recall":
        return impl.build_memory_recall_tool(env.runtime)
    factory = (agent_runtime._build_recall_user_memory_tool if name == "recall_user_memory"
               else agent_runtime._build_search_conversation_history_tool)
    return factory(env.store, env.cfg, env.logger, runtime=env.runtime)


def invoke(env, name, identity=None, *, purpose="reply", **kwargs):
    identity = identity or env.tg
    token = context.set_llm_context(**identity, purpose=purpose)
    try:
        return json.loads(asyncio.run(tool(env, name).handler(query="SHAREDKEY", **kwargs)))
    finally:
        context.reset_llm_context(token)


def shared(result, name):
    rows = result["messages" if name == "search_conversation_history" else "memories"]
    return [item for item in rows if str(item.get("source", "")).startswith("linked_")]


@pytest.mark.parametrize("name", TOOLS)
@pytest.mark.parametrize("direction", ("tg_to_qq", "qq_to_tg"))
def test_explicit_reads_are_bidirectional_and_exact(setup, name, direction):
    env = setup
    actor, source = (env.tg, env.qq) if direction == "tg_to_qq" else (env.qq, env.tg)
    memory(env, actor, "actor", summary="SHAREDKEY actor memory")
    memory(env, source, "source")
    memory(env, {**source, "bot_id": "999"}, "other_bot")
    memory(env, {**source, "platform": "satori"}, "other_platform")
    memory(env, {**source, "user_id": "43"}, "other_user")
    memory(env, {**source, "group_id": "777"}, "group")
    memory(env, {**source, "topic_id": "topic"}, "topic")
    memory(env, {**source, "platform": "", "bot_id": ""}, "legacy")
    memory(env, source, "expired", expires_at=time.time() - 1)
    memory(env, source, "unrelated", summary="COMPLETELY_DIFFERENT")
    history(env, source, "live")
    history(env, source, "archive", archived=True)
    history(env, source, "assistant", role="assistant", user_id=source["bot_id"], delivery_status="confirmed")
    history(env, source, "failed", role="assistant", user_id=source["bot_id"], delivery_status="failed")
    history(env, source, "unknown", role="assistant", user_id=source["bot_id"], delivery_status="unknown")
    history(env, source, "unconfirmed", role="assistant", user_id=source["bot_id"], delivery_status="unconfirmed")
    history(env, source, "wrong_owner", user_id="43")
    history(env, source, "history_group", group_id="777")
    history(env, source, "history_topic", topic_id="topic")
    history(env, source, "history_bot", bot_id="999")
    history(env, source, "history_platform", platform="satori")
    history(env, source, "history_legacy", platform="", bot_id="")
    history(env, source, "summary", summary=True)
    result = invoke(env, name, actor)
    rows = shared(result, name)
    assert rows, result
    if name == "search_conversation_history":
        assert {item["content"] for item in rows} == {"SHAREDKEY live", "SHAREDKEY archive", "SHAREDKEY assistant"}
        assert {item["speaker"] for item in rows if item["role"] == "assistant"} == {source["bot_id"]}
        assert {item["speaker"] for item in rows if item["role"] == "user"} == {source["user_id"]}
    else:
        assert {item["memory_id"] for item in rows} == {"source"}
    for item in rows:
        assert item["source_scope"] == {**source, "storage_scope": "private", "authorization": "verified_account_link"}
        assert item["trust"] == "untrusted_data_only" and item["usage"] == "reference_only"
    assert set(env.gate.calls) == {source["user_id"]}


@pytest.mark.parametrize("name", ("recall_user_memory", "memory_recall"))
def test_experiences_use_real_source_evidence_and_query_filter(setup, name):
    env = setup
    experiences = load_personification_module("plugin.personification.core.experience_cards")
    history(env, env.qq, "experience-source")
    cards = experiences.ExperienceService(env.store)
    scope = experiences.ExperienceScope(**env.qq)
    good = cards.create(scope, summary="SHAREDKEY private journey", source_refs=[{"message_id": "experience-source"}])
    cards.create(scope, summary="OTHERJOURNEY", source_refs=[{"message_id": "experience-source"}])
    cards.create(scope, summary="SHAREDKEY expired journey", source_refs=[{"message_id": "experience-source"}], expires_at=time.time() - 1)
    rows = shared(invoke(env, name), name)
    assert {item["memory_id"] for item in rows} == {good["memory_id"]}
    assert rows[0].get("type", rows[0].get("memory_type")) == "experience_card"
    if name == "memory_recall":
        assert rows[0]["source_refs"][0]["user_id"] == "42"
        assert shared(invoke(env, name, scope="person"), name) == []
        assert shared(invoke(env, name, scope="recent_episode"), name)


@pytest.mark.parametrize("name", TOOLS)
@pytest.mark.parametrize("boundary", ("no_service", "no_gate", "policy_denied", "group", "qzone", "unknown_surface", "disabled", "palace_disabled", "unlinked"))
def test_sharing_fails_closed_at_boundaries(setup, name, boundary):
    env = setup
    memory(env, env.qq, "source")
    history(env, env.qq, "source")
    actor, purpose = env.tg, "reply"
    if boundary == "no_service":
        env.runtime.account_link_service = None
    elif boundary == "no_gate":
        env.runtime.user_policy_gate = None
    elif boundary == "policy_denied":
        env.gate.allow = False
    elif boundary == "group":
        actor = {**actor, "group_id": "777"}
    elif boundary == "qzone":
        purpose = "qzone_post"
    elif boundary == "unknown_surface":
        purpose = ""
    elif boundary == "disabled":
        env.cfg.personification_memory_enabled = False
    elif boundary == "palace_disabled":
        env.cfg.personification_memory_palace_enabled = False
    else:
        env.service.revoke_telegram("123", "42")
    assert shared(invoke(env, name, actor, purpose=purpose), name) == []


@pytest.mark.parametrize("name", TOOLS)
@pytest.mark.parametrize("change", ("unlink", "rebind"))
def test_link_change_during_real_async_read_discards_old_result(setup, monkeypatch, name, change):
    env = setup
    memory(env, env.qq, "source")
    history(env, env.qq, "source")

    def change_link():
        env.service.revoke_telegram("123", "42")
        if change == "rebind":
            env.clock[0] += 61
            assert bind(env).link_id != env.link.link_id

    if name == "search_conversation_history":
        session = load_personification_module("plugin.personification.core.session_store")
        original = session.get_recent_session_candidates

        def delayed(session_id, **kwargs):
            rows = original(session_id, **kwargs)
            if session_id == "private_42":
                change_link()
            return rows

        monkeypatch.setattr(session, "get_recent_session_candidates", delayed)
    else:
        original = env.store.arecall_memories

        async def delayed(**kwargs):
            rows = await original(**kwargs)
            if kwargs.get("user_id") == "42":
                change_link()
            return rows

        monkeypatch.setattr(env.store, "arecall_memories", delayed)
    assert shared(invoke(env, name), name) == []


@pytest.mark.parametrize("name", TOOLS)
def test_policy_revoked_at_final_return_check(setup, name):
    env = setup
    memory(env, env.qq, "source")
    history(env, env.qq, "source")
    final_check = 3 if name == "search_conversation_history" else 5
    env.gate.on_check = lambda count: setattr(env.gate, "allow", count < final_check)
    assert shared(invoke(env, name), name) == []
    assert len(env.gate.calls) == final_check


@pytest.mark.parametrize("name", TOOLS)
def test_multiple_sources_recheck_earlier_policy_after_later_await(setup, monkeypatch, name):
    env = setup
    claim = env.service.begin(tg_bot_id="124", tg_user_id="42", qq_bot_id="456",
                              qq_user_id="42", issue_code=True)
    env.service.mark_delivery(claim.request_id, confirmed=True)
    second = env.service.confirm(qq_bot_id="456", qq_user_id="42", code=claim.code)
    later = {"platform": "telegram", "bot_id": second.telegram_scope[0],
             "user_id": second.telegram_scope[1], "group_id": ""}
    for identity, key in ((env.tg, "first"), (later, "later")):
        memory(env, identity, key)
        history(env, identity, key)
    revoked = set()
    original = env.gate.current_authorization
    final_check = 6 if name == "search_conversation_history" else 10

    async def authorization(user_id):
        result = await original(user_id)
        if len(env.gate.calls) == final_check:
            revoked.add(env.tg["user_id"])
        return result

    monkeypatch.setattr(env.gate, "current_authorization", authorization)
    env.gate.service.authorize = lambda user: SimpleNamespace(allow_context_read=user not in revoked)
    rows = shared(invoke(env, name, env.qq), name)
    assert rows and {item["source_scope"]["user_id"] for item in rows} == {later["user_id"]}
    assert len(env.gate.calls) == final_check


def test_skillpack_identity_arguments_and_scope_never_choose_linked_identity(setup):
    env = setup
    memory(env, env.qq, "source")
    for kwargs in ({"user_id": "42"}, {"group_id": "777"}):
        result = invoke(env, "memory_recall", **kwargs)
        assert result["memories"] == [] and "不一致" in result["note"]
    assert shared(invoke(env, "memory_recall", scope="person"), "memory_recall")
    assert shared(invoke(env, "memory_recall", scope="topic"), "memory_recall") == []
    for name in ("recall_user_memory", "search_conversation_history"):
        assert not {"user_id", "group_id", "platform", "bot_id"} & set(tool(env, name).parameters["properties"])


def test_reinforcement_callback_cannot_release_unlinked_results(setup):
    env = setup
    memory(env, env.qq, "source")
    calls = []

    def schedule(ids):
        calls.append(ids)
        env.service.revoke_telegram("123", "42")

    env.runtime.background_intelligence = SimpleNamespace(schedule_recall_reinforcement=schedule)
    assert shared(invoke(env, "memory_recall"), "memory_recall") == []
    assert calls == [[]]


@pytest.mark.parametrize("name", TOOLS)
@pytest.mark.parametrize("revocation", ("unlink", "rebind", "policy"))
def test_returned_tool_result_cannot_cross_provider_boundary_after_revocation(setup, name, revocation):
    env = setup
    memory(env, env.qq, "source")
    history(env, env.qq, "source")
    token = context.set_llm_context(**env.tg, purpose="reply")
    try:
        result = json.loads(asyncio.run(tool(env, name).handler(query="SHAREDKEY")))
        assert shared(result, name)
        context.assert_llm_sources_current()
        if revocation in {"unlink", "rebind"}:
            env.service.revoke_telegram("123", "42")
            if revocation == "rebind":
                env.clock[0] += 61
                assert bind(env).link_id != env.link.link_id
        else:
            env.gate.allow = False
        with pytest.raises(context.LLMSourceAuthorizationRevoked):
            context.assert_llm_sources_current()
        with pytest.raises(asyncio.CancelledError):
            context.reserve_wire_attempt()
    finally:
        context.reset_llm_context(token)
    fresh = context.set_llm_context(**env.tg, purpose="reply")
    try:
        context.assert_llm_sources_current()
        assert not context.current_llm_context()["turn_state"].get("source_guards")
    finally:
        context.reset_llm_context(fresh)


@pytest.mark.parametrize("skillpacks", (False, True))
def test_registry_passes_shared_dependencies_to_actual_memory_handlers(setup, monkeypatch, skillpacks):
    env = setup
    env.cfg.personification_use_skillpacks = skillpacks
    if skillpacks:
        def load(*, runtime, registry):
            registry.register(impl.build_memory_recall_tool(runtime))
        monkeypatch.setattr(agent_runtime, "load_builtin_skillpacks_sync", load)
    memory(env, env.qq, "source")
    history(env, env.qq, "source")
    registry = agent_runtime.build_agent_tool_registry(
        plugin_config=env.cfg, logger=env.logger, get_now=lambda: None, memory_store=env.store,
        account_link_service=env.service, user_policy_gate=env.gate,
    )
    token = context.set_llm_context(**env.tg, purpose="reply")
    try:
        for name in TOOLS:
            result = json.loads(asyncio.run(registry.get(name).handler(query="SHAREDKEY")))
            assert shared(result, name), (name, result)
    finally:
        context.reset_llm_context(token)


def test_agent_runtime_dependency_builder_passes_link_services(setup, monkeypatch):
    env = setup
    captured = {}
    for name in ("_build_compress_tool_caller", "_build_agent_tool_caller", "_build_lite_tool_caller", "build_inner_state_updater"):
        monkeypatch.setattr(agent_runtime, name, lambda *_a, **_kw: None)
    monkeypatch.setattr(agent_runtime, "init_session_store", lambda *_a: None)
    monkeypatch.setattr(agent_runtime, "init_utils_config", lambda *_a: None)

    def registry(**kwargs):
        captured.update(kwargs)
        return "registry"

    monkeypatch.setattr(agent_runtime, "build_agent_tool_registry", registry)
    result = agent_runtime.build_agent_runtime_deps(
        plugin_config=env.cfg, logger=env.logger, get_now=lambda: None,
        account_link_service=env.service, user_policy_gate=env.gate, qq_user_policy_gate=env.gate,
    )
    assert result[0] == "registry"
    assert captured["account_link_service"] is env.service
    assert captured["user_policy_gate"] is captured["qq_user_policy_gate"] is env.gate
