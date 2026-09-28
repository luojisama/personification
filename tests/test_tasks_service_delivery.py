from __future__ import annotations

import asyncio

import pytest

from ._loader import load_personification_module


tasks_service = load_personification_module("plugin.personification.core.tasks_service")


@pytest.mark.parametrize("outcome", ["sent", "unknown", "failed", "safety_blocked"])
def test_scheduled_task_persists_explicit_delivery_outcome(monkeypatch, outcome: str) -> None:
    saved: list[str] = []
    monkeypatch.setattr(tasks_service, "_save_task", lambda task, _user: saved.append(task["last_status"]))
    task = {"user_id": "10001"}

    asyncio.run(tasks_service._execute_task(task, lambda _task: outcome))

    assert task["last_status"] == outcome
    assert saved == [outcome]


def test_scheduled_task_cancel_persists_unknown_and_propagates(monkeypatch) -> None:
    saved: list[str] = []
    monkeypatch.setattr(tasks_service, "_save_task", lambda task, _user: saved.append(task["last_status"]))
    task = {"user_id": "10001"}

    async def cancelled(_task):  # noqa: ANN001, ANN202
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(tasks_service._execute_task(task, cancelled))

    assert task["last_status"] == "unknown"
    assert saved == ["unknown"]


def test_scheduled_task_missing_caller_or_receipt_never_claims_sent(monkeypatch) -> None:
    saved: list[str] = []
    monkeypatch.setattr(tasks_service, "_save_task", lambda task, _user: saved.append(task["last_status"]))
    absent = {"user_id": "10001"}
    no_receipt = {"user_id": "10001"}

    asyncio.run(tasks_service._execute_task(absent, None))
    asyncio.run(tasks_service._execute_task(no_receipt, lambda _task: None))

    assert saved == ["failed", "unknown"]


def test_scheduled_task_save_failure_does_not_replace_cancellation(monkeypatch) -> None:
    def broken_save(_task, _user):  # noqa: ANN001, ANN202
        raise OSError("storage unavailable")

    monkeypatch.setattr(tasks_service, "_save_task", broken_save)

    async def cancelled(_task):  # noqa: ANN001, ANN202
        raise asyncio.CancelledError("original cancellation")

    with pytest.raises(asyncio.CancelledError, match="original cancellation"):
        asyncio.run(tasks_service._execute_task({"user_id": "10001"}, cancelled))
