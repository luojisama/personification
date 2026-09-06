from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from ._loader import load_personification_module

routes = load_personification_module("plugin.personification.webui.routes.moderation_routes")
moderation = load_personification_module("plugin.personification.core.moderation")


def _operation(store: Path, *, incident: str = "incident-a") -> tuple[object, dict[str, str], str]:
    ledger = moderation.ModerationLedger(store / "moderation.sqlite3")
    scope = dict(platform="onebot", bot_id="100", group_id="1", target_id="2", incident=incident)
    for number in range(1, 4):
        ledger.event(**scope, message_id=f"event-{number}", occurred_at=float(number), negative=True, offensive=True)
    ledger.warning(**scope, round_id="round-1", message_id="warning-1", source_message_id="event-1", confirmed_at=1.0)
    ledger.warning(**scope, round_id="round-2", message_id="warning-2", source_message_id="event-2", confirmed_at=2.0)
    operation = ledger.reserve(**scope, minutes=1, now=4.0)
    assert operation is not None
    ledger.finish(operation["operation_id"], "sent")
    return ledger, scope, operation["operation_id"]


def _client(monkeypatch, store: Path, bot=None, *, admin: bool = True) -> TestClient:
    monkeypatch.setattr(routes, "get_data_dir", lambda _config: store)
    runtime = SimpleNamespace(plugin_config=SimpleNamespace(), get_bots=lambda: {"100": bot} if bot else {})
    app = FastAPI()
    app.include_router(routes.build_moderation_router(runtime=runtime))
    if admin:
        app.dependency_overrides[routes.require_admin] = lambda: SimpleNamespace()
    else:
        def denied():
            raise HTTPException(403, detail="forbidden")
        app.dependency_overrides[routes.require_admin] = denied
    return TestClient(app)


def test_non_admin_get_and_post_are_forbidden(monkeypatch, tmp_path: Path):
    client = _client(monkeypatch, tmp_path, admin=False)
    assert client.get("/api/v2/moderation/status").status_code == 403
    assert client.post("/api/v2/moderation/operations/missing/release").status_code == 403


def test_warning_only_incidents_are_paginated_with_safe_refs(monkeypatch, tmp_path: Path):
    ledger = moderation.ModerationLedger(tmp_path / "moderation.sqlite3")
    for incident in ("incident-one", "incident-two"):
        scope = dict(platform="onebot", bot_id="100", group_id="1", target_id="2", incident=incident)
        ledger.event(**scope, message_id=f"event-{incident}", occurred_at=1.0, negative=True, offensive=True)
        ledger.warning(**scope, round_id=f"round-{incident}", message_id=f"warning-{incident}", source_message_id=f"event-{incident}", confirmed_at=1.0)
    client = _client(monkeypatch, tmp_path)
    body = client.get("/api/v2/moderation/incidents", params={"page": 1, "page_size": 1}).json()
    assert body["total"] == 2 and body["total_pages"] == 2 and len(body["items"]) == 1
    item = body["items"][0]
    assert item["status"] == "warning_only"
    assert item["warning_message_ids"] and item["evidence_message_ids"]
    assert item["expires_at"] == item["updated_at"] + 30 * 60
    assert "raw_text" not in item and "reasoning" not in item


def test_release_uses_stored_scope_duration_zero_and_is_idempotent(monkeypatch, tmp_path: Path):
    _ledger, _scope, operation_id = _operation(tmp_path)
    calls: list[tuple[tuple, dict]] = []

    class Bot:
        async def get_group_member_info(self, **kwargs):
            return {"role": "admin"} if str(kwargs["user_id"]) == "100" else {"role": "member", "shut_up_timestamp": 99_999_999_999}
        async def call_api(self, *args, **kwargs):
            calls.append((args, kwargs))
            return None

    client = _client(monkeypatch, tmp_path, Bot())
    # Any caller-supplied scope is intentionally ignored; only the stored
    # operation can select the member or group to release.
    first = client.post(f"/api/v2/moderation/operations/{operation_id}/release", json={"group_id": "999", "target_id": "999", "duration": 99})
    second = client.post(f"/api/v2/moderation/operations/{operation_id}/release")
    assert first.json()["ok"] and second.json()["code"] == "moderation_release_released"
    assert len(calls) == 1
    assert calls[0][1] == {"group_id": 1, "user_id": 2, "duration": 0}


def test_release_permission_loss_and_already_unmuted_never_call_api(monkeypatch, tmp_path: Path):
    _ledger, _scope, operation_id = _operation(tmp_path)
    calls: list[object] = []

    class PermissionLostBot:
        async def get_group_member_info(self, **_kwargs):
            return {"role": "member", "shut_up_timestamp": 99_999_999_999}
        async def call_api(self, *_args, **_kwargs):
            calls.append(True)
            return None

    result = _client(monkeypatch, tmp_path, PermissionLostBot()).post(f"/api/v2/moderation/operations/{operation_id}/release")
    assert result.json()["code"] == "moderation_release_permission_blocked" and not calls

    _ledger, _scope, operation_id = _operation(tmp_path, incident="incident-unmuted")
    class UnmutedBot:
        async def get_group_member_info(self, **kwargs):
            return {"role": "admin"} if str(kwargs["user_id"]) == "100" else {"role": "member", "shut_up_timestamp": 0}
        async def call_api(self, *_args, **_kwargs):
            calls.append(True)
            return None

    result = _client(monkeypatch, tmp_path, UnmutedBot()).post(f"/api/v2/moderation/operations/{operation_id}/release")
    assert result.json()["code"] == "moderation_release_already_resolved" and not calls


def test_member_query_failure_is_not_delivery_unknown(monkeypatch, tmp_path: Path):
    _ledger, _scope, operation_id = _operation(tmp_path)
    calls: list[object] = []

    class QueryFailureBot:
        async def get_group_member_info(self, **_kwargs):
            raise RuntimeError("adapter unavailable")
        async def call_api(self, *_args, **_kwargs):
            calls.append(True)
            return None

    client = _client(monkeypatch, tmp_path, QueryFailureBot())
    response = client.post(f"/api/v2/moderation/operations/{operation_id}/release")
    assert response.json()["code"] == "moderation_release_member_query_unavailable"
    assert not calls


def test_timeout_is_unknown_and_is_never_replayed(monkeypatch, tmp_path: Path):
    _ledger, _scope, operation_id = _operation(tmp_path)
    calls: list[object] = []

    class TimeoutBot:
        async def get_group_member_info(self, **kwargs):
            return {"role": "admin"} if str(kwargs["user_id"]) == "100" else {"role": "member", "shut_up_timestamp": 99_999_999_999}
        async def call_api(self, *_args, **_kwargs):
            calls.append(True)
            raise TimeoutError("outcome unknown")

    client = _client(monkeypatch, tmp_path, TimeoutBot())
    first = client.post(f"/api/v2/moderation/operations/{operation_id}/release")
    second = client.post(f"/api/v2/moderation/operations/{operation_id}/release")
    assert first.json()["code"] == "moderation_release_unknown"
    assert second.json()["code"] == "moderation_release_unknown"
    assert len(calls) == 1


def test_release_reservation_has_one_owner_and_unknown_cannot_become_retryable(tmp_path):
    ledger, _scope, operation_id = _operation(tmp_path)
    assert ledger.reserve_release(operation_id)=="releasing"
    assert ledger.reserve_release(operation_id)=="in_progress"
    ledger.finish_release(operation_id,"unknown")
    ledger.finish_release(operation_id,"unavailable")
    assert ledger.reserve_release(operation_id)=="unknown"


def test_unknown_release_can_only_query_to_confirm_not_repeat_write(monkeypatch,tmp_path):
    ledger, _scope, operation_id = _operation(tmp_path)
    ledger.reserve_release(operation_id);ledger.finish_release(operation_id,"unknown")
    class Bot:
        async def get_group_member_info(self,**kwargs):
            return {"role":"admin"} if kwargs["user_id"]==100 else {"role":"member","shut_up_timestamp":0}
        async def call_api(self,*args,**kwargs): raise AssertionError("must only query")
    response=_client(monkeypatch,tmp_path,Bot()).post(f"/api/v2/moderation/operations/{operation_id}/release")
    assert response.json()["ok"]
    assert ledger.get_operation(operation_id)["status"]=="released"
