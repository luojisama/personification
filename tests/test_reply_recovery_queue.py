from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from ._loader import load_personification_module


recovery = load_personification_module(
    "plugin.personification.core.reply_recovery_queue"
)


def _queue(tmp_path, **kwargs):  # noqa: ANN001, ANN003, ANN202
    return recovery.ReplyRecoveryQueue(tmp_path / "state.sqlite3", **kwargs)


def _record(
    queue,
    message_id: str,
    *,
    text: str = "hello",
    failure_class: str = "generation_failed_before_send",
    failure_stage: str = "generation",
    conversation_id: str = "group-1",
    route: str = "route-a",
    now: float = 100.0,
    missing_parts=(),  # noqa: ANN001
):  # noqa: ANN001, ANN202
    return queue.record_failure(
        bot_id="bot-1",
        conversation_kind="group",
        conversation_id=conversation_id,
        original_message_id=message_id,
        normalized_text=text,
        failure_stage=failure_stage,
        failure_class=failure_class,
        route_fingerprint=route,
        trace_id=f"trace-{message_id}",
        missing_part_indexes=missing_parts,
        now=now,
    )


def test_list_items_supports_page_offset_and_matching_count(tmp_path) -> None:
    queue = _queue(tmp_path)
    for index in range(5):
        _record(queue, f"page-{index}", now=100 + index)

    assert queue.count_items(status="pending") == 5
    page = queue.list_items(status="pending", limit=2, offset=2)
    assert [item.original_message_id for item in page] == ["page-2", "page-3"]


def test_schema_is_self_contained_deduplicated_and_never_stores_old_reply(tmp_path) -> None:
    queue = _queue(tmp_path)

    with sqlite3.connect(queue.db_path) as conn:
        columns = {
            row[1]: row for row in conn.execute("PRAGMA table_info(reply_recovery_queue)")
        }
        indexes = {
            row[1] for row in conn.execute("PRAGMA index_list(reply_recovery_queue)")
        }
        table_sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' "
            "AND name='reply_recovery_queue'"
        ).fetchone()[0]

    assert {
        "bot_id",
        "conversation_kind",
        "conversation_id",
        "original_message_id",
        "normalized_text",
        "media_refs_json",
        "failure_stage",
        "last_failure_stage",
        "failure_class",
        "attempt_count",
        "status",
        "expires_at",
        "trace_id",
        "claim_token",
    } <= set(columns)
    assert not {
        "reply",
        "generated_reply",
        "candidate_reply",
        "assistant_text",
        "outbound_text",
    } & set(columns)
    assert {
        "idx_reply_recovery_ready",
        "idx_reply_recovery_conversation",
        "idx_reply_recovery_claim",
    } <= indexes
    assert "UNIQUE" in table_sql
    assert "original_message_id" in table_sql


def test_record_failure_normalizes_inbound_message_media_and_deduplicates(tmp_path) -> None:
    queue = _queue(tmp_path)
    first = queue.record_failure(
        bot_id=" bot-1 ",
        conversation_kind="GROUP",
        conversation_id=" group-1 ",
        original_message_id=" msg-1 ",
        normalized_text="  hello\tworld\r\n\n\nsecond\x00line  ",
        media_refs=[
            {
                "media_id": "media-1",
                "kind": "video",
                "origin": "quoted",
                "ref": "data:video/mp4;base64,SECRET",
                "file_id": "onebot-file-1",
                "safe_summary": "  clip\t summary  ",
                "confidence": 9,
                "raw_tool_result": "must-not-persist",
            },
            {"kind": "audio", "origin": "current"},
        ],
        failure_stage="provider_generation",
        failure_class="generation_failed_before_send",
        route_fingerprint="route-a",
        trace_id="trace-first",
        now=100,
    )
    duplicate = queue.record_failure(
        bot_id="bot-1",
        conversation_kind="group",
        conversation_id="group-1",
        original_message_id="msg-1",
        normalized_text="updated inbound text",
        failure_stage="provider_generation",
        failure_class="generation_failed_before_send",
        route_fingerprint="route-a",
        trace_id="trace-second",
        now=110,
    )

    assert first.normalized_text == "hello world\n\nsecondline"
    assert len(first.media_refs) == 1
    assert first.media_refs[0]["file_id"] == "onebot-file-1"
    assert "ref" not in first.media_refs[0]
    assert "raw_tool_result" not in first.media_refs[0]
    assert first.media_refs[0]["confidence"] == 1.0
    assert duplicate.id == first.id
    assert duplicate.first_failure_at == 100
    assert duplicate.last_failure_at == 110
    assert duplicate.normalized_text == "updated inbound text"
    assert duplicate.attempt_count == 0
    assert len(queue.list_items()) == 1
    with pytest.raises(ValueError, match="normalized_text or controlled media_refs"):
        _record(queue, "empty", text="")


def test_unknown_and_partial_are_quarantined_and_never_auto_claimed(tmp_path) -> None:
    queue = _queue(tmp_path)
    unknown = _record(
        queue,
        "unknown",
        failure_class="delivery_unknown",
        failure_stage="qq_send",
    )
    partial = _record(
        queue,
        "partial",
        failure_class="delivery_partial",
        failure_stage="qq_send",
        missing_parts=(3, 1, 3, -1),
    )

    assert unknown.status == "quarantined"
    assert partial.status == "quarantined"
    assert partial.missing_part_indexes == (1, 3)
    assert queue.wake_route("route-a", now=101) == 0
    assert queue.claim_next_batch(worker_id="worker", now=101) is None

    manually_cleared = queue.confirm_not_sent(
        [unknown.id], trace_id="manual-check", now=102
    )
    assert manually_cleared[0].status == "pending"
    assert manually_cleared[0].failure_class == "confirmed_not_sent"
    assert manually_cleared[0].failure_stage == "qq_send"
    assert manually_cleared[0].last_failure_stage == "manual_delivery_review"
    with pytest.raises(ValueError, match="delivery_unknown"):
        queue.confirm_not_sent([partial.id], now=102)

    batch = queue.claim_next_batch(worker_id="worker", now=103)
    assert batch is not None
    assert batch.item_ids == (unknown.id,)
    assert queue.get(partial.id).status == "quarantined"


def test_batch_limits_messages_characters_and_serializes_each_conversation(tmp_path) -> None:
    queue = _queue(tmp_path)
    for index in range(55):
        _record(queue, f"message-{index:02d}", text="x", now=100 + index / 100)

    first = queue.claim_next_batch(worker_id="worker-1", route_fingerprint="route-a", now=200)

    assert first is not None
    assert len(first.items) == 50
    assert first.character_count == 50
    assert queue.claim_next_batch(worker_id="worker-2", route_fingerprint="route-a", now=200) is None
    queue.mark_dispatch_started(first.claim_token, now=201)
    queue.finalize_delivery(first.claim_token, outcome="confirmed", now=202)
    second = queue.claim_next_batch(
        worker_id="worker-2", route_fingerprint="route-a", now=203
    )
    assert second is not None
    assert len(second.items) == 5

    char_queue = recovery.ReplyRecoveryQueue(tmp_path / "chars.sqlite3")
    for index in range(3):
        _record(
            char_queue,
            f"long-{index}",
            text=str(index) * 12_000,
            now=300 + index,
        )
    char_batch = char_queue.claim_next_batch(worker_id="char-worker", now=400)
    assert char_batch is not None
    assert len(char_batch.items) == 2
    assert char_batch.character_count == 24_000
    assert char_batch.character_count <= recovery.MAX_RECOVERY_BATCH_CHARS


def test_claim_is_atomic_across_workers(tmp_path) -> None:
    queue = _queue(tmp_path)
    item = _record(queue, "concurrent")

    def claim(index: int):  # noqa: ANN202
        return queue.claim_next_batch(worker_id=f"worker-{index}", now=101)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(claim, range(8)))

    batches = [batch for batch in results if batch is not None]
    assert len(batches) == 1
    assert batches[0].item_ids == (item.id,)
    assert queue.get(item.id).attempt_count == 1


def test_three_true_generation_attempts_are_the_hard_limit(tmp_path) -> None:
    queue = _queue(tmp_path)
    item = _record(queue, "retry-limit")

    first = queue.claim_next_batch(worker_id="worker", now=101)
    assert first is not None
    first_result = queue.mark_generation_failed(first.claim_token, now=102)
    assert first_result[0].status == "pending"
    assert first_result[0].attempt_count == 1

    second = queue.claim_next_batch(worker_id="worker", now=103)
    assert second is not None
    queue.mark_dispatch_started(second.claim_token, now=104)
    second_result = queue.finalize_delivery(
        second.claim_token,
        outcome="confirmed_not_sent",
        now=105,
    )
    assert second_result[0].status == "pending"
    assert second_result[0].attempt_count == 2

    third = queue.claim_next_batch(worker_id="worker", now=106)
    assert third is not None
    third_result = queue.mark_generation_failed(third.claim_token, now=107)
    assert third_result[0].status == "exhausted"
    assert third_result[0].attempt_count == recovery.MAX_RECOVERY_ATTEMPTS
    assert third_result[0].failure_stage == item.failure_stage
    assert third_result[0].last_failure_stage == "recovery_generation"
    assert queue.claim_next_batch(worker_id="worker", now=108) is None


def test_only_confirmed_delivery_completes_and_restart_keeps_state(tmp_path) -> None:
    queue = _queue(tmp_path)
    item = _record(queue, "success")
    batch = queue.claim_next_batch(worker_id="worker", now=101)
    assert batch is not None
    with pytest.raises(recovery.RecoveryClaimError, match="dispatching"):
        queue.finalize_delivery(batch.claim_token, outcome="confirmed", now=102)

    queue.mark_dispatch_started(batch.claim_token, now=102)
    completed = queue.finalize_delivery(
        batch.claim_token,
        outcome="confirmed",
        trace_id="trace-confirmed",
        now=103,
    )
    assert completed[0].status == "recovered"
    assert completed[0].recovered_at == 103
    assert completed[0].claim_token == ""

    restarted = recovery.ReplyRecoveryQueue(queue.db_path)
    persisted = restarted.get(item.id)
    assert persisted is not None
    assert persisted.status == "recovered"
    assert restarted.claim_next_batch(worker_id="worker", now=104) is None


@pytest.mark.parametrize(
    ("outcome", "missing", "expected_missing"),
    [
        ("delivery_unknown", (), ()),
        ("delivery_partial", (4, 2, 4), (2, 4)),
    ],
)
def test_unconfirmed_recovery_delivery_is_quarantined(
    tmp_path,
    outcome: str,
    missing: tuple[int, ...],
    expected_missing: tuple[int, ...],
) -> None:
    queue = _queue(tmp_path)
    item = _record(queue, outcome)
    batch = queue.claim_next_batch(worker_id="worker", now=101)
    assert batch is not None
    queue.mark_dispatch_started(batch.claim_token, now=102)

    result = queue.finalize_delivery(
        batch.claim_token,
        outcome=outcome,
        missing_part_indexes=missing,
        now=103,
    )

    assert result[0].status == "quarantined"
    assert result[0].failure_class == outcome
    assert result[0].missing_part_indexes == expected_missing
    assert result[0].recovered_at == 0
    assert queue.claim_next_batch(worker_id="worker", now=104) is None
    assert queue.get(item.id).status == "quarantined"


def test_stale_generation_requeues_but_stale_dispatch_becomes_unknown(tmp_path) -> None:
    queue = _queue(tmp_path, claim_lease_seconds=10)
    generation_item = _record(queue, "generation-crash", now=100)
    generation_batch = queue.claim_next_batch(worker_id="worker", now=101)
    assert generation_batch is not None

    queue.expire_due(now=112)
    generation_after_crash = queue.get(generation_item.id)
    assert generation_after_crash.status == "pending"
    assert generation_after_crash.failure_class == "generation_failed_before_send"

    dispatch_batch = queue.claim_next_batch(worker_id="worker", now=113)
    assert dispatch_batch is not None
    queue.mark_dispatch_started(dispatch_batch.claim_token, now=114)
    queue.expire_due(now=125)
    dispatch_after_crash = queue.get(generation_item.id)
    assert dispatch_after_crash.status == "quarantined"
    assert dispatch_after_crash.failure_class == "delivery_unknown"
    assert queue.claim_next_batch(worker_id="worker", now=126) is None


def test_pending_items_expire_after_24_hours(tmp_path) -> None:
    queue = _queue(tmp_path)
    item = _record(queue, "expires", now=10)
    unknown = _record(
        queue,
        "unknown-expires",
        failure_class="delivery_unknown",
        failure_stage="qq_send",
        now=10,
    )

    assert item.expires_at == 10 + recovery.DEFAULT_RECOVERY_TTL_SECONDS
    assert queue.expire_due(now=item.expires_at + 1) >= 1
    expired = queue.get(item.id)
    expired_unknown = queue.get(unknown.id)
    assert expired.status == "expired"
    assert expired_unknown.status == "expired"
    assert expired_unknown.failure_class == "delivery_unknown"
    assert queue.claim_next_batch(worker_id="worker", now=item.expires_at + 2) is None


def test_route_wakeup_only_advances_recoverable_items(tmp_path) -> None:
    queue = _queue(tmp_path)
    pending = queue.record_failure(
        bot_id="bot-1",
        conversation_kind="private",
        conversation_id="user-1",
        original_message_id="pending",
        normalized_text="pending",
        failure_stage="provider_generation",
        failure_class="generation_failed_before_send",
        route_fingerprint="route-a",
        now=100,
        next_attempt_at=500,
    )
    _record(
        queue,
        "unknown",
        failure_class="delivery_unknown",
        failure_stage="send",
        now=100,
    )

    assert queue.claim_next_batch(worker_id="worker", route_fingerprint="route-a", now=101) is None
    assert queue.wake_route("route-a", now=102) == 1
    batch = queue.claim_next_batch(
        worker_id="worker", route_fingerprint="route-a", now=102
    )
    assert batch is not None
    assert batch.item_ids == (pending.id,)
