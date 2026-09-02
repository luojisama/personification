from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from personification.core.bot_self_continuity import (
    BotSelfClaimDraft,
    BotSelfContinuityStore,
    claims_for_segment,
    deliver_self_consistent_segment,
    parse_self_claim_drafts,
)


def _draft(
    key: str,
    summary: str,
    *,
    category: str = "activity",
    segment_index: int = 0,
) -> BotSelfClaimDraft:
    return BotSelfClaimDraft(
        segment_index=segment_index,
        subject="self",
        category=category,
        fact_key=key,
        summary=summary,
    )


def test_claim_parser_accepts_only_bounded_self_claims() -> None:
    values = [
        {
            "segment_index": 0,
            "subject": "self",
            "category": "completion",
            "fact_key": "homework.status",
            "summary": "我已经写完作业",
        },
        {
            "segment_index": 0,
            "subject": "other",
            "category": "activity",
            "fact_key": "friend.activity",
            "summary": "我说群友正在睡觉",
        },
        {
            "segment_index": 0,
            "subject": "self",
            "category": "activity",
            "fact_key": "private.id",
            "summary": "我刚和 12345 聊过",
        },
        {
            "segment_index": 0,
            "subject": "self",
            "category": "location",
            "fact_key": "place",
            "summary": "我在图书馆",
        },
    ]
    assert parse_self_claim_drafts(values, segment_count=1) == (
        _draft("homework.status", "我已经写完作业", category="completion"),
    )


def test_store_ttl_status_and_capacity_are_bounded() -> None:
    store = BotSelfContinuityStore(max_facts=20)
    now = 2_000_000_000.0
    store.commit("bot", [_draft("activity.now", "我正在写作业")], status="confirmed", now=now)
    store.commit(
        "bot",
        [_draft("availability.now", "我现在有空", category="availability")],
        status="tentative",
        now=now,
    )
    snapshot = store.snapshot("bot", now=now)
    assert snapshot.revision == 2
    assert {item.status for item in snapshot.facts} == {"confirmed", "tentative"}
    tentative = next(item for item in snapshot.facts if item.status == "tentative")
    assert tentative.expires_at - now == 1800
    assert len(store.snapshot("bot", now=now + 1801).facts) == 1
    assert not store.snapshot("bot", now=now + 7201).facts

    for index in range(25):
        store.commit(
            "bot",
            [_draft(f"plan.item-{index}", f"我计划处理第{index}项", category="plan")],
            status="confirmed",
            now=now + 8000 + index,
        )
    capped = store.snapshot("bot", now=now + 9000)
    assert len(capped.facts) == 20
    assert "plan.item-0" not in {item.fact_key for item in capped.facts}


def test_claims_are_bound_to_their_source_segment() -> None:
    drafts = (
        _draft("activity.now", "我正在写作业", segment_index=0),
        _draft("plan.next", "我等会去吃饭", category="plan", segment_index=1),
    )
    assert claims_for_segment(drafts, 0) == drafts[:1]
    assert claims_for_segment(drafts, 1) == drafts[1:]


def test_revision_change_rechecks_second_group_before_send() -> None:
    async def scenario() -> None:
        store = BotSelfContinuityStore()
        initial = store.snapshot("bot")
        first_entered = asyncio.Event()
        release_first = asyncio.Event()
        sent_texts: list[str] = []

        async def first_send(text: str):  # noqa: ANN202
            first_entered.set()
            await release_first.wait()
            sent_texts.append(text)
            return SimpleNamespace(status="sent")

        async def second_send(text: str):  # noqa: ANN202
            sent_texts.append(text)
            return SimpleNamespace(status="sent")

        async def recheck(_messages, **_kwargs):  # noqa: ANN001, ANN202
            return (
                '{"action":"rewrite","text":"我已经写完了",'
                '"self_claims":[{"segment_index":0,"subject":"self",'
                '"category":"completion","fact_key":"homework.status",'
                '"summary":"我已经写完作业"}]}'
            )

        first = asyncio.create_task(
            deliver_self_consistent_segment(
                store=store,
                bot_id="bot",
                expected_revision=initial.revision,
                candidate_text="我作业写完了",
                claim_drafts=[_draft("homework.status", "我已经写完作业", category="completion")],
                send=first_send,
                call_ai_api=recheck,
            )
        )
        await first_entered.wait()
        second = asyncio.create_task(
            deliver_self_consistent_segment(
                store=store,
                bot_id="bot",
                expected_revision=initial.revision,
                candidate_text="我作业还没写完",
                claim_drafts=[_draft("homework.status", "我还没写完作业", category="completion")],
                send=second_send,
                call_ai_api=recheck,
            )
        )
        await asyncio.sleep(0)
        assert not second.done()
        release_first.set()
        first_result, second_result = await asyncio.gather(first, second)
        assert first_result.action == "accepted"
        assert second_result.action == "rewrite"
        assert second_result.diagnosis_code == "self_continuity_revision_conflict_rewrite"
        assert sent_texts == ["我作业写完了", "我已经写完了"]
        fact = store.snapshot("bot").facts[-1]
        assert fact.summary == "我已经写完作业"

    asyncio.run(scenario())


def test_failed_unknown_and_partial_delivery_commit_only_observable_claims() -> None:
    async def scenario() -> None:
        store = BotSelfContinuityStore()
        snapshot = store.snapshot("bot")

        sent = await deliver_self_consistent_segment(
            store=store,
            bot_id="bot",
            expected_revision=snapshot.revision,
            candidate_text="我现在有空",
            claim_drafts=[_draft("availability.now", "我现在有空", category="availability")],
            send=lambda _text: asyncio.sleep(0, result=SimpleNamespace(status="sent")),
            call_ai_api=None,
        )
        failed = await deliver_self_consistent_segment(
            store=store,
            bot_id="bot",
            expected_revision=sent.revision,
            candidate_text="我等会去散步",
            claim_drafts=[_draft("plan.walk", "我等会去散步", category="plan")],
            send=lambda _text: asyncio.sleep(0, result=SimpleNamespace(status="failed")),
            call_ai_api=None,
        )
        unknown = await deliver_self_consistent_segment(
            store=store,
            bot_id="bot",
            expected_revision=failed.revision,
            candidate_text="我晚点回来",
            claim_drafts=[_draft("plan.return", "我晚点回来", category="plan")],
            send=lambda _text: asyncio.sleep(0, result=SimpleNamespace(status="unknown")),
            call_ai_api=None,
        )

        facts = {item.fact_key: item for item in store.snapshot("bot").facts}
        assert facts["availability.now"].status == "confirmed"
        assert "plan.walk" not in facts
        assert facts["plan.return"].status == "tentative"
        assert failed.sent is False
        assert unknown.action == "tentative"

    asyncio.run(scenario())


def test_unknown_exception_commits_tentative_without_swallowing_error() -> None:
    async def scenario() -> None:
        store = BotSelfContinuityStore()

        async def send(_text: str):  # noqa: ANN202
            error = RuntimeError("dispatch outcome unavailable")
            error.qq_outbound_receipt = SimpleNamespace(status="unknown")
            raise error

        with pytest.raises(RuntimeError, match="outcome unavailable"):
            await deliver_self_consistent_segment(
                store=store,
                bot_id="bot",
                expected_revision=0,
                candidate_text="我今晚再来",
                claim_drafts=[_draft("plan.return", "我今晚再来", category="plan")],
                send=send,
                call_ai_api=None,
            )
        assert store.snapshot("bot").facts[0].status == "tentative"

    asyncio.run(scenario())
