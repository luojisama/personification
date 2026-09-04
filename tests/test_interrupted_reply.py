from __future__ import annotations

import pytest

from ._loader import load_personification_module


interrupted_reply = load_personification_module(
    "plugin.personification.core.interrupted_reply"
)


def _active_generation_state(*, confirmed: bool = True) -> tuple[dict, dict]:
    entry: dict = {
        "processing": True,
        "current_generation": 7,
        "active_generation_token": 7,
        "interrupt_requested_generation": 0,
        "interrupted_outgoing_drafts": None,
    }
    state: dict = {
        "batch_runtime_ref": {"entry": entry, "generation": 7},
        "reply_delivery_confirmed": confirmed,
    }
    entry["active_state"] = state
    return entry, state


def test_confirmed_generation_stores_bounded_not_sent_drafts_and_consumes_once() -> None:
    entry, state = _active_generation_state()

    assert interrupted_reply.request_cooperative_reply_interruption(entry)
    aggregate = interrupted_reply.finalize_cooperative_reply_interruption(
        state,
        ["already confirmed is absent", "draft two", "draft three", "draft four", "drop"],
    )

    assert aggregate == {"source_generation": 7, "draft_count": 4, "draft_chars": 57}
    assert state["terminal_reason"] == "interrupted_after_confirmed_segment"
    assert entry["interrupt_requested_generation"] == 0
    context = interrupted_reply.consume_interrupted_reply_context(entry)
    assert context == {
        "source_generation": 7,
        "status": "not_sent",
        "segments": ["already confirmed is absent", "draft two", "draft three", "draft four"],
    }
    assert interrupted_reply.consume_interrupted_reply_context(entry) is None

    next_state: dict = {"interrupted_reply_context": context}
    contract = interrupted_reply.render_interrupted_reply_system_contract(next_state)
    assert '"status":"not_sent"' in contract
    assert "不是用户消息，也不是已经发生的助手历史" in contract
    assert "不得自动续发" in contract
    assert "结合本轮最新输入重新决定" in contract


@pytest.mark.parametrize(
    ("state_patch", "expected"),
    [
        ({}, False),  # started but not confirmed
        ({"reply_delivery_confirmed": True, "delivery_unknown": True}, False),
        ({"reply_delivery_confirmed": True, "reply_delivery_complete": True}, False),
    ],
)
def test_interruption_never_claims_started_unknown_or_complete_delivery(
    state_patch: dict,
    expected: bool,
) -> None:
    entry, state = _active_generation_state(confirmed=False)
    state["reply_delivery_started"] = True
    state.update(state_patch)

    assert interrupted_reply.request_cooperative_reply_interruption(entry) is expected
    assert entry["interrupt_requested_generation"] == 0


def test_draft_bounds_apply_to_segments_and_total_characters() -> None:
    entry, state = _active_generation_state()
    assert interrupted_reply.request_cooperative_reply_interruption(entry)

    aggregate = interrupted_reply.finalize_cooperative_reply_interruption(
        state,
        ["x" * 500, "y" * 500, "z" * 500, "w" * 500, "ignored"],
    )

    assert aggregate == {"source_generation": 7, "draft_count": 3, "draft_chars": 720}
    context = interrupted_reply.consume_interrupted_reply_context(entry)
    assert context is not None
    assert len(context["segments"]) == 3
    assert [len(item) for item in context["segments"]] == [240, 240, 240]


def test_media_boundary_interruption_does_not_inject_an_empty_draft_contract() -> None:
    entry, state = _active_generation_state()
    assert interrupted_reply.request_cooperative_reply_interruption(entry)

    aggregate = interrupted_reply.finalize_cooperative_reply_interruption(state, ())

    assert aggregate == {"source_generation": 7, "draft_count": 0, "draft_chars": 0}
    assert state["terminal_reason"] == "interrupted_after_confirmed_segment"
    assert interrupted_reply.consume_interrupted_reply_context(entry) is None
    next_state: dict = {}
    assert interrupted_reply.attach_interrupted_reply_context(next_state, entry) is None
    assert interrupted_reply.render_interrupted_reply_system_contract(next_state) == ""
