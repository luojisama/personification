"""Replay the healthy-provider cancellation from trace c909e13adab64e21."""
from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace

from ._loader import load_personification_module

quality = load_personification_module("plugin.personification.agent.runtime.reply_quality")
review = load_personification_module("plugin.personification.core.response_review")
synthesis = load_personification_module("plugin.personification.agent.runtime.final_synthesis")


def test_healthy_review_after_eight_seconds_uses_existing_turn_deadline():
    async def exercise():
        calls = []
        class SlowHealthyProvider:
            async def chat_with_tools(self, messages, tools, search):
                calls.append("rewrite")
                await asyncio.sleep(8.05)
                return SimpleNamespace(content="那先别绕远，就看当前这个点")
        async def final_provider(messages):
            calls.append("review")
            await asyncio.sleep(8.05)
            return json.dumps({"action":"accept", "persona_verdict":"consistent", "flags":[]})
        deadline = time.monotonic() + 12
        rewritten, accepted = await asyncio.gather(
            quality.finalize_agent_reply_quality(
                synthesis.AgentResult(text="我先看看情况，等会再说", pending_actions=[]),
                tool_caller=SlowHealthyProvider(), messages=[{"role":"system","content":"温和的群友"}],
                response_deadline=deadline,
            ),
            review.final_dialogue_gate(
                final_provider, candidate_text="有点不好意思啦", raw_message_text="我喜欢你",
                core_persona="温和害羞，不无故攻击他人", response_deadline=deadline,
            ),
        )
        assert rewritten.text == "那先别绕远，就看当前这个点"
        assert accepted.action == "accept"
        assert sorted(calls) == ["review", "rewrite"]
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
