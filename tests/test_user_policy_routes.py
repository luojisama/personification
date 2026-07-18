from __future__ import annotations

import time
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from ._loader import load_personification_module


db = load_personification_module("plugin.personification.core.db")
memory_store_mod = load_personification_module(
    "plugin.personification.core.memory_store"
)
route_mod = load_personification_module(
    "plugin.personification.webui.routes.user_policy_routes"
)
schemas = load_personification_module("plugin.personification.webui.schemas")
user_policy = load_personification_module(
    "plugin.personification.core.user_policy"
)


def _client(tmp_path):  # noqa: ANN001, ANN202
    db_path = db.init_db_sync(tmp_path)
    config = SimpleNamespace(
        personification_data_dir=str(tmp_path),
        personification_memory_enabled=True,
        personification_memory_palace_enabled=True,
    )
    store = memory_store_mod.MemoryStore(config)
    store.initialize()
    service = user_policy.UserPolicyService(
        db_path=db_path,
        evidence_key=b"p" * 32,
    )
    bundle = SimpleNamespace(
        user_policy_service=service,
        memory_store=store,
        profile_service=SimpleNamespace(memory_store=store),
        persona_store=None,
        scoped_profile_service=None,
        qq_user_policy_gate=None,
    )
    runtime = SimpleNamespace(runtime_bundle=bundle)
    app = FastAPI()
    app.include_router(route_mod.build_user_policy_router(runtime=runtime))
    app.dependency_overrides[route_mod.require_admin] = lambda: schemas.AdminIdentity(
        qq="90001",
        device_id="device",
        label="test",
    )
    return TestClient(app), service, store, db_path


def _violation():  # noqa: ANN202
    return user_policy.PolicyAssessment(
        verdict="confirmed_violation",
        category="harassment",
        intent="targeted_attack",
        severity="high",
        confidence=0.96,
        reason_code="route_test",
        confirmed=True,
    )


def test_user_policy_routes_are_revision_guarded_and_no_store(tmp_path) -> None:
    client, service, _store, _db_path = _client(tmp_path)
    now = time.time()
    service.apply_assessment(
        user_id="10001",
        idempotency_key="route-event",
        surface="qq_group",
        assessment=_violation(),
        content="需要加密的短摘",
        now=now,
    )
    initial = service.get_state("10001", now=now + 1)

    updated = client.post(
        "/api/user-policy/10001/override",
        json={
            "mode": "block",
            "expected_revision": initial.revision,
            "expires_at": 0,
        },
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["state"]["effective_tier"] == "manual_block"
    assert updated.headers["cache-control"] == "no-store, private"

    conflict = client.post(
        "/api/user-policy/10001/override",
        json={
            "mode": "allow",
            "expected_revision": initial.revision,
            "expires_at": 0,
        },
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "policy_revision_conflict"

    states = client.get("/api/user-policy/states?tier=manual_block")
    assert states.status_code == 200
    assert [item["user_id"] for item in states.json()["states"]] == ["10001"]
    assert states.headers["cache-control"] == "no-store, private"

    events = client.get(
        "/api/user-policy/10001/events?include_evidence=true"
    )
    assert events.status_code == 200
    assert events.headers["cache-control"] == "no-store, private"
    excerpts = [item.get("evidence_excerpt", "") for item in events.json()["events"]]
    assert "需要加密的短摘" in excerpts


def test_irreversible_profile_purge_retains_policy_state(tmp_path) -> None:
    client, service, store, db_path = _client(tmp_path)
    state = service.set_manual_override(
        user_id="10001",
        mode="block",
        actor="test",
        now=1000,
    )
    store.upsert_core_profile(
        user_id="10001",
        profile_text="core",
        profile_json={"structured": {"interest": "x"}},
    )
    store.upsert_core_profile(user_id="10002", profile_text="keep")
    store.upsert_local_profile(
        group_id="20001",
        user_id="10001",
        profile_text="local",
    )
    store.write_memory_item(
        {
            "memory_id": "purge-me",
            "memory_type": "fact",
            "summary": "target memory",
            "user_id": "10001",
        }
    )
    store.write_memory_item(
        {
            "memory_id": "keep-me",
            "memory_type": "fact",
            "summary": "other memory",
            "user_id": "10002",
        }
    )
    with db.connect_sync(db_path) as conn:
        conn.execute(
            "INSERT INTO user_personas(user_id,persona,updated_at) VALUES('10001','legacy',1)"
        )
        conn.execute(
            "INSERT INTO persona_histories(user_id,content,created_at) VALUES('10001','history',1)"
        )
        conn.execute(
            "INSERT INTO group_relation_edges(group_id,src_user_id,dst_user_id,edge_kind,weight,last_seen_at,sample_msg_id) VALUES('20001','10001','10002','reply',1,1,'m1')"
        )
        conn.execute(
            "INSERT INTO avatar_relation_evidence(group_id,left_user_id,right_user_id,relation,confidence,evidence_tags,asset_kinds,left_avatar_hash,right_avatar_hash,schema_version,observed_at,expires_at) VALUES('20001','10001','10002','coordinated_pair',0.9,'[]','[]',?, ?,1,1,999999)",
            ("a" * 64, "b" * 64),
        )
        conn.commit()

    wrong = client.request(
        "DELETE",
        "/api/user-policy/10001/profile",
        json={
            "expected_revision": state.revision,
            "confirmation": "wrong",
        },
    )
    assert wrong.status_code == 400

    response = client.request(
        "DELETE",
        "/api/user-policy/10001/profile",
        json={
            "expected_revision": state.revision,
            "confirmation": "PURGE PROFILE 10001",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["policy_state_retained"] is True
    assert body["state"]["revision"] == state.revision
    assert body["state"]["effective_tier"] == "manual_block"
    assert body["counts"]["core_profiles"] == 1
    assert body["counts"]["local_profiles"] == 1
    assert body["counts"]["memory_items"] == 1
    assert body["counts"]["relation_edges"] == 1
    assert body["counts"]["avatar_relation_evidence"] == 1
    assert store.get_core_profile("10001") is None
    assert store.get_core_profile("10002") is not None
    assert store.get_local_profile(group_id="20001", user_id="10001") is None
    memories = store.list_recent_memories(limit=20)
    assert {item["memory_id"] for item in memories} == {"keep-me"}

    with db.connect_sync(db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM user_policy_state WHERE user_id='10001'"
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM user_personas WHERE user_id='10001'"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM persona_histories WHERE user_id='10001'"
        ).fetchone()[0] == 0
