from __future__ import annotations
import asyncio
import time
from types import SimpleNamespace
import pytest
from ._loader import load_personification_module

cards = load_personification_module("plugin.personification.core.experience_cards")
timeliness = load_personification_module("plugin.personification.core.reply_timeliness")

@pytest.fixture
def service(tmp_path, monkeypatch):
    paths = load_personification_module("plugin.personification.core.paths")
    monkeypatch.setattr(paths, "get_data_dir", lambda _cfg=None: tmp_path)
    store_mod = load_personification_module("plugin.personification.core.memory_store")
    config = SimpleNamespace(personification_data_dir=str(tmp_path), personification_memory_enabled=True, personification_memory_palace_enabled=True)
    store = store_mod.MemoryStore(config)
    store.initialize()
    def source(scope, message_id):
        return {"user_id": "u", "timestamp": 123} if message_id == "real" else None
    return cards.ExperienceService(store, source)


def test_sources_scope_revision_and_forget(service):
    scope = cards.ExperienceScope("telegram", "bot", "group", topic_id="topic")
    with pytest.raises(ValueError, match="source_unavailable"):
        service.create(scope, summary="一起排错", source_refs=[{"message_id": "draft"}])
    card = service.create(scope, summary="一起排错", source_refs=[{"message_id": "real"}])
    assert card["participants"] == ["u"]
    assert card["confirmation"] == "pending"
    for other in [cards.ExperienceScope("onebot", "bot", "group", topic_id="topic"), cards.ExperienceScope("telegram", "other", "group", topic_id="topic"), cards.ExperienceScope("telegram", "bot", "group", topic_id="other")]:
        assert service.list(other) == []
        with pytest.raises(KeyError):
            service.forget(other, card["memory_id"], expected_revision=1)
    changed = service.revise(scope, card["memory_id"], expected_revision=1, summary="一起排查连接故障")
    assert changed["revision"] == 2
    with pytest.raises(cards.ExperienceConflict):
        service.revise(scope, card["memory_id"], expected_revision=1, summary="旧页面")
    service.forget(scope, card["memory_id"], expected_revision=2)
    assert service.list(scope) == []
    assert service.store.get_memory_item(card["memory_id"]) is None


def test_revise_invalidates_derived_memory_and_vectors(service):
    scope = cards.ExperienceScope("telegram", "bot", "group")
    card = service.create(scope, summary="过期事实", source_refs=[{"message_id": "real"}])
    service.store.write_memory_item({"memory_id": "derived", "summary": "派生旧事实", "source_memory_ids": [card["memory_id"]]})
    service.store.write_memory_item({"memory_id": "nested", "summary": "再次总结", "source_refs": [{"memory_id": "derived"}]})
    service.revise(scope, card["memory_id"], expected_revision=1, summary="更正事实")
    assert service.store.get_memory_item("derived") is None
    assert service.store.get_memory_item("nested") is None
    with cards._connect(service.store.memory_palace_dir / "memory_palace.db") as conn:
        assert not conn.execute("SELECT * FROM memory_text_versions WHERE memory_id IN ('derived','nested')").fetchall()
    assert service.store.get_memory_item(card["memory_id"])["supports_recall"] is False


def test_stable_identity_requires_confirmation_and_expiry(service):
    scope = cards.ExperienceScope("telegram", "bot", user_id="u")
    with pytest.raises(ValueError, match="requires_expiry"):
        service.create(scope, summary="喜欢下棋", source_refs=[{"message_id": "real"}], kind="stable_identity", confirmation="confirmed")
    card = service.create(scope, summary="喜欢下棋", source_refs=[{"message_id": "real"}], kind="stable_identity", confirmation="confirmed", expires_at=time.time()+100)
    assert service.list(scope, active_only=True)[0]["memory_id"] == card["memory_id"]
    service.revise(scope, card["memory_id"], expected_revision=1, summary="喜欢下棋", confirmation="pending", expires_at=1)
    assert service.list(scope, active_only=True) == []


def test_unknown_bot_delivery_is_not_an_experience(service):
    service.source_reader = lambda *_: {"is_bot": True, "delivery_status": "unknown"}
    with pytest.raises(ValueError, match="unconfirmed"):
        service.create(cards.ExperienceScope("telegram", "bot", "group"), summary="没有送达", source_refs=[{"message_id": "real"}])


def test_timeliness_tracks_edits_and_post_review_changes():
    rows = [{"message_id": "1", "content": "最初消息"}]
    tracker = timeliness.ReplyTimeliness.capture(lambda: [dict(row) for row in rows])
    assert tracker.review_message() is None
    rows[0]["content"] = "已编辑"
    message = tracker.review_message()
    assert message["role"] == "user" and "已编辑" in message["content"]
    assert not tracker.changed_after_review()
    rows.append({"message_id": "2", "content": "新消息"})
    assert tracker.changed_after_review()


def test_final_review_reuses_call_and_suppresses_changed_random_reply(monkeypatch):
    review = load_personification_module("plugin.personification.core.response_review")
    rows = []
    tracker = timeliness.ReplyTimeliness.capture(lambda: list(rows))
    rows.append({"message_id": "1", "content": "新话题"})
    calls = []
    async def fake_impl(call, **kwargs):
        await call([{"role": "system", "content": "review"}, {"role": "user", "content": "候选"}])
        return review.ResponseReviewDecision(action="accept", text="旧话题")
    async def call(messages):
        calls.append(messages)
        rows.append({"message_id": "2", "content": "再更新"})
        return '{"action":"accept","text":"旧话题"}'
    monkeypatch.setattr(review, "_review_response_text_impl", fake_impl)
    result = asyncio.run(review.review_response_text(call, timeliness=tracker, is_random_chat=True))
    assert len(calls) == 1
    assert any("新话题" in message["content"] for message in calls[0])
    assert result.action == "no_reply"
    assert result.diagnosis_code == "timeliness_superseded"

def test_experience_api_auth_scope_conflict_and_disabled_forget(service):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    routes = load_personification_module("plugin.personification.webui.routes.experience_routes")
    runtime = SimpleNamespace(memory_store=service.store, plugin_config=SimpleNamespace(personification_experience_cards_enabled=True))
    app = FastAPI()
    app.include_router(routes.build_experience_router(runtime=runtime))
    app.dependency_overrides[routes.require_admin] = lambda: SimpleNamespace()
    client = TestClient(app)
    scope = cards.ExperienceScope("telegram", "bot", "group")
    card = service.create(scope, summary="排错经历", source_refs=[{"message_id": "real"}])
    query = {"platform": "telegram", "bot_id": "bot", "group_id": "group"}
    assert client.get("/api/v2/experiences", params=query).json()["items"][0]["memory_id"] == card["memory_id"]
    url = "/api/v2/experiences/" + card["memory_id"]
    assert client.patch(url, params={**query, "bot_id": "other"}, json={"summary": "改写", "expected_revision": 1}).status_code == 404
    assert client.patch(url, params=query, json={"summary": "改写", "expected_revision": 2}).status_code == 409
    assert client.patch(url, params=query, json={"summary": "改写", "expected_revision": 1}).status_code == 200
    runtime.plugin_config.personification_experience_cards_enabled = False
    assert client.patch(url, params=query, json={"summary": "禁用", "expected_revision": 2}).status_code == 403
    assert client.delete(url, params={**query, "expected_revision": 2}).status_code == 200


def test_experience_ui_raw_telegram_ids_resolve_topic_and_migration(service, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    routes = load_personification_module("plugin.personification.webui.routes.experience_routes")
    transport = load_personification_module("plugin.personification.core.telegram_connections")
    interaction = load_personification_module("plugin.personification.core.interaction_adapter")
    aliases = SimpleNamespace(canonical_chat=lambda bot, chat: "-10" if bot == "123" and chat == "-10010" else chat)
    monkeypatch.setattr(transport, "get_connections", lambda _cfg: aliases)
    runtime = SimpleNamespace(memory_store=service.store, plugin_config=SimpleNamespace(personification_experience_cards_enabled=True))
    app = FastAPI()
    app.include_router(routes.build_experience_router(runtime=runtime))
    app.dependency_overrides[routes.require_admin] = lambda: SimpleNamespace()
    client = TestClient(app)
    envelope = interaction.InteractionEnvelope("telegram", "123", "group", "-10", "", "", topic_id="4")
    scope = cards.ExperienceScope("telegram", envelope.bot_namespace, envelope.conversation_namespace)
    card = service.create(scope, summary="这个话题的经历", source_refs=[{"message_id": "real"}])
    query = {"platform": "telegram", "bot_id": "123", "group_id": "-10010", "topic_id": "4"}
    assert client.get("/api/v2/experiences", params=query).json()["items"][0]["memory_id"] == card["memory_id"]
    assert client.get("/api/v2/experiences", params={**query, "topic_id": "5"}).json()["items"] == []
    assert client.get("/api/v2/experiences", params={**query, "bot_id": "456"}).json()["items"] == []
    assert client.patch("/api/v2/experiences/" + card["memory_id"], params=query, json={"summary": "已纠正", "expected_revision": 1}).status_code == 200


def test_confirmed_turn_capture_creates_pending_card_with_real_source(service, monkeypatch):
    curator_module = load_personification_module("plugin.personification.core.memory_curator")
    context = load_personification_module("plugin.personification.core.llm_context")
    monkeypatch.setattr(cards, "read_source_message", service.source_reader)
    service.store.plugin_config.personification_experience_cards_enabled = True
    async def run():
        token = context.set_llm_context(platform="telegram", bot_id="bot", group_id="group", user_id="u")
        try:
            await curator_module.MemoryCurator(service.store).capture_turn(user_utterance="一起修复连接", bot_response="已修好", user_id="u", group_id="group", source_message_id="real", delivery_confirmed=True)
        finally:
            context.reset_llm_context(token)
    asyncio.run(run())
    result = service.list(cards.ExperienceScope("telegram", "bot", "group", "u"))
    assert len(result) == 1
    assert result[0]["confirmation"] == "pending"
    assert result[0]["source_refs"][0]["message_id"] == "real"


def test_stable_identity_survives_semantic_recall_outage(service, monkeypatch):
    context = load_personification_module("plugin.personification.core.memory_context")
    source = cards.ExperienceScope("telegram", "bot", user_id="u")
    service.create(source, summary="喜欢下棋", source_refs=[{"message_id": "real"}], kind="stable_identity", confirmation="confirmed", expires_at=time.time()+100)
    service.store.plugin_config.personification_experience_cards_enabled = True
    service.store.plugin_config.personification_memory_query_planning_enabled = False
    runtime = SimpleNamespace(plugin_config=service.store.plugin_config, memory_store=service.store)
    # No caller -> semantic gate unavailable, but confirmed identity has a local path.
    result = asyncio.run(context.prepare_memory_context(runtime=runtime, event=SimpleNamespace(platform="telegram", user_id="u"), bot=SimpleNamespace(self_id="bot"), messages=[], refresh=False))
    assert "喜欢下棋" in result.render()
    assert result.memories == []


def test_experience_api_resolves_real_webui_runtime_context(service):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app_module = load_personification_module("plugin.personification.webui.app")
    routes = load_personification_module("plugin.personification.webui.routes.experience_routes")
    runtime = app_module._RuntimeContext(
        plugin_config=SimpleNamespace(personification_experience_cards_enabled=False),
        superusers=set(), get_bots=lambda: {}, logger=None,
        runtime_bundle=SimpleNamespace(memory_store=service.store),
    )
    app = FastAPI()
    app.include_router(routes.build_experience_router(runtime=runtime))
    app.dependency_overrides[routes.require_admin] = lambda: SimpleNamespace()
    response = TestClient(app).get("/api/v2/experiences", params={"platform": "telegram", "bot_id": "bot", "user_id": "u"})
    assert response.status_code == 200
    assert response.json() == {"items": [], "enabled": False}
