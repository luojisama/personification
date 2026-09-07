from __future__ import annotations

import asyncio
import json
import time

import pytest

from ._loader import load_personification_module

review = load_personification_module("plugin.personification.core.response_review")
trace = load_personification_module("plugin.personification.core.reply_turn_trace")


@pytest.mark.parametrize(
    ("result", "code"),
    [
        ("timeout", "review_timeout"),
        ("exception", "review_call_failed"),
        ("not json", "review_unparseable"),
        ({"action": "rewrite", "text": ""}, "review_rewrite_empty"),
        ({"action": "accept"}, "review_verification_rejected"),
        ({"action": "no_reply", "persona_verdict": "consistent"}, "review_model_no_reply"),
        ({"action": "accept", "persona_verdict": "consistent"}, "review_accepted"),
    ],
)
def test_final_review_distinguishes_failure_without_exposing_provider_text(monkeypatch, result, code):
    stages = []
    monkeypatch.setattr(trace, "record_stage", lambda **data: stages.append(data))
    secret_marker = "private-body-https://private.invalid/?token=do-not-log"

    async def provider(messages):
        if result == "timeout":
            raise asyncio.TimeoutError(secret_marker)
        if result == "exception":
            raise RuntimeError(secret_marker)
        if isinstance(result, dict):
            return json.dumps({**result, "reason": secret_marker, "flags": [secret_marker]})
        return result

    decision = asyncio.run(review.final_dialogue_gate(
        provider, candidate_text=secret_marker, raw_message_text=secret_marker,
        core_persona="友好的同伴", response_deadline=time.monotonic() + 5,
    ))
    assert decision.diagnosis_code == code
    assert decision.action == ("accept" if code == "review_accepted" else "no_reply")
    assert [s["key"] for s in stages] == ["final_review_start", "final_review_call", "final_review_decision"]
    assert secret_marker not in json.dumps(stages, ensure_ascii=False)
    assert code in stages[-1]["detail"]
    assert all(s.get("elapsed_ms", -1) >= 0 for s in stages)


def test_expired_review_does_not_start_provider_and_has_own_diagnosis(monkeypatch):
    stages = []
    monkeypatch.setattr(trace, "record_stage", lambda **data: stages.append(data))

    async def provider(messages):
        raise AssertionError("must not start provider")

    result = asyncio.run(review.final_dialogue_gate(
        provider, candidate_text="候选", raw_message_text="问题",
        response_deadline=time.monotonic() - 1,
    ))
    assert result.action == "no_reply"
    assert result.diagnosis_code == "review_budget_exhausted"
    assert "review_budget_exhausted" in stages[-1]["detail"]


def test_independent_rewrite_verification_failure_is_visible_and_silent(monkeypatch):
    stages = []
    calls = []
    monkeypatch.setattr(trace, "record_stage", lambda **data: stages.append(data))

    async def provider(messages):
        calls.append(messages)
        if len(calls) == 1:
            return json.dumps({"action": "rewrite", "text": "改写后的正文", "persona_verdict": "rewrite"})
        raise asyncio.TimeoutError("private provider response")

    result = asyncio.run(review.final_dialogue_gate(
        provider, candidate_text="原来的正文", raw_message_text="问题", core_persona="同伴",
    ))
    assert len(calls) == 2
    assert result.action == "no_reply"
    assert result.diagnosis_code == "review_timeout"
    call_stages = [s for s in stages if s["key"] == "final_review_call"]
    assert "source=initial" in call_stages[0]["detail"]
    assert "source=verification" in call_stages[1]["detail"]


def test_review_trace_dto_keeps_real_duration_budget_and_safe_diagnosis(monkeypatch):
    routes = load_personification_module("plugin.personification.webui.routes.v2_routes")
    stages = []
    monkeypatch.setattr(trace, "record_stage", lambda **data: stages.append({**data, "ts": 1.0}))

    async def provider(messages):
        return json.dumps({"action": "accept", "persona_verdict": "consistent", "reason": "PRIVATE MODEL REASON"})

    asyncio.run(review.final_dialogue_gate(
        provider, candidate_text="PRIVATE CANDIDATE", raw_message_text="PRIVATE USER INPUT", core_persona="同伴",
        response_deadline=time.monotonic() + 20,
    ))
    dto = routes._trace_detail({"trace_id": "offline", "outcome": "ok", "stages": stages})
    assert [s["key"] for s in dto["stages"]] == ["final_review_start", "final_review_call", "final_review_decision"]
    assert 0 < dto["stages"][0]["remaining_ms"] <= 20000
    assert all(s["duration_ms"] is not None for s in dto["stages"])
    assert "reason=review_accepted" in dto["stages"][-1]["summary"]
    assert "PRIVATE" not in json.dumps(dto)


@pytest.mark.parametrize("reason", ["care_rewrite_unverified", "review_rewrite_empty", "review_timeout"])
def test_model_reason_cannot_impersonate_runtime_diagnosis(reason):
    async def provider(messages):
        return json.dumps({"action": "accept", "persona_verdict": "consistent", "reason": reason})

    decision = asyncio.run(review.final_dialogue_gate(
        provider, candidate_text="正常候选", raw_message_text="问题", core_persona="同伴",
    ))
    assert decision.action == "accept"
    assert decision.diagnosis_code == "review_accepted"


def test_external_cancellation_is_not_swallowed_or_mislabeled(monkeypatch):
    stages = []
    monkeypatch.setattr(trace, "record_stage", lambda **data: stages.append(data))

    async def exercise():
        entered = asyncio.Event()
        async def provider(messages):
            entered.set()
            await asyncio.Event().wait()

        task = asyncio.create_task(review.final_dialogue_gate(
            provider, candidate_text="候选", raw_message_text="问题",
        ))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(exercise())
    assert "reason=review_cancelled" in stages[-1]["detail"]
    assert "source=initial" in stages[-1]["detail"]
    assert "review_timeout" not in str(stages)
