from __future__ import annotations

from ._loader import load_personification_module


deadline_module = load_personification_module("plugin.personification.core.turn_deadline")


def test_deadline_uses_injected_monotonic_clock_and_reserves() -> None:
    now = [100.0]
    deadline = deadline_module.TurnDeadline.start(
        timeout_seconds=180,
        clock=lambda: now[0],
    )
    now[0] = 130.0
    assert deadline.remaining() == 150.0
    assert deadline.remaining(reserve_seconds=20) == 130.0
    assert deadline.timeout_for(200, reserve_seconds=20) == 130.0


def test_deadline_hard_caps_total_turn_at_ten_minutes() -> None:
    deadline = deadline_module.TurnDeadline.start(
        timeout_seconds=900,
        started_at=10.0,
        clock=lambda: 10.0,
    )
    assert deadline.timeout_seconds == 600.0
    assert deadline.expires_at == 610.0


def test_snapshot_exposes_stage_budget_without_payloads() -> None:
    deadline = deadline_module.TurnDeadline.start(
        timeout_seconds=60,
        started_at=10.0,
        clock=lambda: 20.0,
    )
    snapshot = deadline.snapshot(stage="tools")
    assert snapshot == {
        "stage": "tools",
        "timeout_seconds": 60.0,
        "elapsed_seconds": 10.0,
        "remaining_seconds": 50.0,
        "generation_remaining_seconds": 30.0,
        "send_remaining_seconds": 45.0,
        "expired": False,
    }


def test_attach_preserves_one_deadline_for_all_phases() -> None:
    state: dict[str, object] = {}
    first = deadline_module.attach_turn_deadline(
        state,
        timeout_seconds=180,
        started_at=5.0,
        clock=lambda: 5.0,
    )
    second = deadline_module.attach_turn_deadline(
        state,
        timeout_seconds=600,
        started_at=99.0,
        clock=lambda: 99.0,
    )
    assert first is second
    assert state["response_deadline"] == 185.0
