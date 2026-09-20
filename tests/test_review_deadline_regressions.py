"""Replay the healthy-provider cancellation from trace c909e13adab64e21."""
from __future__ import annotations

import asyncio
import json
import time

from ._loader import load_personification_module

review = load_personification_module("plugin.personification.core.response_review")


def test_healthy_review_after_eight_seconds_uses_existing_turn_deadline():
    async def exercise():
        calls = []
        async def final_provider(messages):
            calls.append("review")
            await asyncio.sleep(8.05)
            return json.dumps({"action":"accept", "persona_verdict":"consistent", "flags":[]})
        deadline = time.monotonic() + 12
        accepted = await review.final_dialogue_gate(
            final_provider, candidate_text="有点不好意思啦", raw_message_text="我喜欢你",
            core_persona="温和害羞，不无故攻击他人", response_deadline=deadline,
            timeout_seconds=10.0,
        )
        assert accepted.action == "accept"
        assert calls == ["review"]
    asyncio.run(exercise())


def test_expired_review_deadline_is_silent_without_starting_provider():
    calls = []
    async def provider(messages):
        calls.append(messages)
        raise AssertionError("expired turn must not start another request")
    result = asyncio.run(review.final_dialogue_gate(
        provider, candidate_text="候选", raw_message_text="当前消息", core_persona="温和",
        response_deadline=time.monotonic() - 1,
    ))
    assert result.action == "no_reply"
    assert not calls
