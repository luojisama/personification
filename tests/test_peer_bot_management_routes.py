from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from ._loader import load_personification_module


@pytest.fixture
def peer_route_runtime(tmp_path: Path, monkeypatch):  # noqa: ANN001
    data_store = load_personification_module("plugin.personification.core.data_store")
    paths = load_personification_module("plugin.personification.core.paths")
    registry_mod = load_personification_module("plugin.personification.core.peer_bot_registry")
    runtime_mod = load_personification_module("plugin.personification.core.peer_bot_runtime")
    observer_mod = load_personification_module("plugin.personification.core.peer_bot_observer")
    monkeypatch.setattr(paths, "get_data_dir", lambda _cfg=None: tmp_path)
    cfg = SimpleNamespace(
        personification_data_dir=str(tmp_path),
        personification_peer_bot_enabled=True,
        personification_peer_bot_detection_enabled=True,
        personification_peer_bot_max_command_chars=500,
        personification_peer_bot_detector_daily_quota=10,
    )
    store = data_store.init_data_store(cfg)
    registry = registry_mod.PeerBotRegistry(store=store, plugin_config=cfg)
    tracker = runtime_mod.PeerBotRuntimeTracker()
    observer = observer_mod.PeerBotObserver(
        registry=registry,
        plugin_config=cfg,
        call_ai_api=None,
    )

    app_module = load_personification_module("plugin.personification.webui.app")
    app_module.set_runtime_context(
        plugin_config=cfg,
        superusers={"10001"},
        get_bots=lambda: {"1": SimpleNamespace()},
        logger=SimpleNamespace(
            info=lambda *_a, **_k: None,
            warning=lambda *_a, **_k: None,
            debug=lambda *_a, **_k: None,
        ),
        runtime_bundle=SimpleNamespace(
            memory_store=None,
            profile_service=None,
            peer_bot_registry=registry,
            peer_bot_tracker=tracker,
            peer_bot_observer=observer,
        ),
    )
    return SimpleNamespace(
        app_module=app_module,
        registry=registry,
        tracker=tracker,
        observer=observer,
    )


def _client(rt):  # noqa: ANN001
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(rt.app_module.build_router())
    return TestClient(app)


def _login(client, rt) -> None:  # noqa: ANN001
    sent: list[dict] = []

    class _Bot:
        async def call_api(self, _name: str, **kwargs):  # noqa: ANN003
            sent.append(kwargs)
            return {"message_id": 1}

    rt.app_module.get_runtime_context().get_bots = lambda: {"1": _Bot()}
    response = client.post("/personification/api/auth/login", json={"qq": "10001"})
    assert response.status_code == 200, response.text
    code = re.search(r"\b(\d{6})\b", str(sent[-1].get("message", ""))).group(1)
    verified = client.post(
        "/personification/api/auth/verify",
        json={"qq": "10001", "code": code, "device_label": "peer-test"},
    )
    assert verified.status_code == 200, verified.text
    csrf = client.cookies.get("personification_webui_csrf", "")
    if csrf:
        client.headers["X-Personification-CSRF"] = csrf


def test_peer_bot_management_lifecycle_and_v2_compat(peer_route_runtime) -> None:  # noqa: ANN001
    client = _client(peer_route_runtime)
    _login(client, peer_route_runtime)

    initial = client.get("/personification/api/groups/g1/peer-bots")
    assert initial.status_code == 200, initial.text
    assert initial.json()["enabled"] is False
    assert initial.json()["bots"] == []

    settings = client.put(
        "/personification/api/v2/group-management/g1/peer-bots/settings",
        json={
            "enabled": True,
            "max_calls_per_turn": 1,
            "cooldown_seconds": 15,
            "pending_ttl_seconds": 45,
            "max_chain_depth": 1,
        },
    )
    assert settings.status_code == 200, settings.text
    assert settings.json()["settings"]["enabled"] is True

    approved = client.put(
        "/personification/api/groups/g1/peer-bots/20002",
        json={"action": "approve", "nickname": "Usagi"},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["bot"]["status"] == "approved"

    command = client.put(
        "/personification/api/groups/g1/peer-bots/20002/commands/mc_say",
        json={
            "full_template": ".mc say {message}",
            "parameter_schema": {
                "type": "object",
                "properties": {"message": {"type": "string", "maxLength": 160}},
                "required": ["message"],
                "additionalProperties": False,
            },
            "risk_level": "write",
            "status": "approved",
        },
    )
    assert command.status_code == 200, command.text
    assert command.json()["command"]["command_head"] == ".mc say"

    listed = client.get("/personification/api/groups/g1/peer-bots").json()
    assert listed["bots"][0]["nickname"] == "Usagi"
    assert listed["commands"][0]["full_template"] == ".mc say {message}"
    assert listed["pending_count"] == 0

    reset = client.post("/personification/api/groups/g1/peer-bots/reset-loop")
    assert reset.status_code == 200, reset.text
    assert reset.json()["loop_protection"]["pending_count"] == 0

    deleted = client.delete(
        "/personification/api/groups/g1/peer-bots/20002/commands/mc_say"
    )
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["deleted"] is True


def test_peer_bot_management_is_authenticated_and_validates_templates(peer_route_runtime) -> None:  # noqa: ANN001
    client = _client(peer_route_runtime)
    assert client.get("/personification/api/groups/g1/peer-bots").status_code == 401
    _login(client, peer_route_runtime)

    invalid = client.put(
        "/personification/api/groups/g1/peer-bots/20002/commands/bad",
        json={
            "full_template": ".mc say {message}\n/stop",
            "parameter_schema": {},
            "risk_level": "dangerous",
            "status": "approved",
        },
    )
    assert invalid.status_code == 400
    assert invalid.json()["detail"]["code"].startswith("peer_bot_")


def test_peer_bot_management_get_never_returns_reply_or_command_body(peer_route_runtime) -> None:  # noqa: ANN001
    client = _client(peer_route_runtime)
    _login(client, peer_route_runtime)
    tracker = peer_route_runtime.tracker
    tracker.record_dispatch(
        group_id="g1",
        target_bot_id="20002",
        trigger_user_id="30003",
        tracking_id="track_1",
        operation_id="operation_1",
        command_id="mc_say",
        send_status="sent",
        outbound_message_id="message_1",
    )
    tracker.match_reply(
        group_id="g1",
        target_bot_id="20002",
        reply_to_message_id="message_1",
        reply_message_id="message_2",
        reply_content="PRIVATE RAW PEER REPLY",
    )

    response = client.get("/personification/api/groups/g1/peer-bots")
    assert response.status_code == 200
    assert "PRIVATE RAW PEER REPLY" not in response.text
    invocation = response.json()["recent_invocations"][0]
    assert "reply_content" not in invocation
    assert "full_command" not in invocation


def test_peer_bot_discover_flushes_only_current_group(peer_route_runtime, monkeypatch) -> None:  # noqa: ANN001
    client = _client(peer_route_runtime)
    _login(client, peer_route_runtime)
    calls: list[str] = []

    async def _flush_group(group_id: str) -> list[dict]:
        calls.append(group_id)
        return [{"status": "unknown", "diagnostic": "peer_bot_detector_model_missing"}]

    monkeypatch.setattr(peer_route_runtime.observer, "flush_group", _flush_group)
    response = client.post("/personification/api/groups/g1/peer-bots/discover")
    assert response.status_code == 200, response.text
    assert calls == ["g1"]
    assert response.json()["evaluated_count"] == 1
    assert response.json()["state"]["enabled"] is False


def test_peer_bot_discover_can_evaluate_bounded_recent_group_projection(
    peer_route_runtime,
    monkeypatch,
) -> None:  # noqa: ANN001
    db = load_personification_module("plugin.personification.core.db")
    with db.connect_sync() as conn:
        conn.execute(
            """
            INSERT INTO group_messages(
                group_id,user_id,nickname,content,message_id,sender_role,
                mentioned_ids,is_at_bot,is_bot,timestamp
            ) VALUES('g1','20002','Usagi','[MC] fixed response','m1','member','[]',0,0,1)
            """
        )
        conn.commit()
    client = _client(peer_route_runtime)
    _login(client, peer_route_runtime)
    evaluated: list[list] = []

    async def _flush_group(_group_id: str) -> list[dict]:
        return []

    async def _evaluate_packets(packets: list) -> dict:
        evaluated.append(packets)
        return {"status": "unknown", "diagnostic": "peer_bot_detector_test"}

    monkeypatch.setattr(peer_route_runtime.observer, "flush_group", _flush_group)
    monkeypatch.setattr(peer_route_runtime.observer, "evaluate_packets", _evaluate_packets)
    response = client.post("/personification/api/groups/g1/peer-bots/discover")

    assert response.status_code == 200, response.text
    assert len(evaluated) == 1
    assert evaluated[0][0].group_id == "g1"
    assert evaluated[0][0].user_id == "20002"
    assert evaluated[0][0].text == "[MC] fixed response"
    assert "[MC] fixed response" not in response.text
