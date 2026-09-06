from __future__ import annotations

import asyncio
import json

from ._loader import load_personification_module


review = load_personification_module("plugin.personification.core.response_review")


class _Caller:
    def __init__(self, replies): self.replies = iter(replies); self.messages = []
    async def __call__(self, messages):
        self.messages.append(messages)
        return next(self.replies)


def _run(caller):
    return asyncio.run(review.review_response_text(
        caller, candidate_text="普通接话", raw_message_text="群友说话",
        core_persona="管理员核心：温和克制，不无因攻击。", relationship_hint="动态关系：熟人",
    ))


def test_persona_review_accepts_only_explicit_consistent_verdict():
    caller = _Caller([json.dumps({"action":"accept", "text":"", "persona_verdict":"consistent", "flags":[]})])
    result = _run(caller)
    assert result.action == "accept"
    assert "管理员核心" in caller.messages[0][0]["content"]
    assert "动态关系" in caller.messages[0][1]["content"]


def test_persona_rewrite_is_independently_rechecked_once():
    caller = _Caller([
        json.dumps({"action":"rewrite", "text":"换成温和接话", "persona_verdict":"rewrite", "flags":[]}),
        json.dumps({"action":"accept", "text":"", "persona_verdict":"consistent", "flags":[]}),
    ])
    result = _run(caller)
    assert result.action == "rewrite" and result.text == "换成温和接话"
    assert len(caller.messages) == 2


def test_persona_rewrite_second_failure_and_missing_verdict_fail_closed():
    failed = _run(_Caller([
        json.dumps({"action":"rewrite", "text":"改写", "persona_verdict":"rewrite", "flags":[]}),
        json.dumps({"action":"no_reply", "persona_verdict":"invalid", "flags":[]}),
    ]))
    missing = _run(_Caller([json.dumps({"action":"accept", "text":"", "flags":[]})]))
    assert failed.action == "no_reply"
    assert missing.action == "no_reply"
