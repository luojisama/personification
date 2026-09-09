from __future__ import annotations

import asyncio

import pytest

from plugin.personification.flows.social_intelligence.gate import gate_should_send


class _Caller:
    def __init__(self, content: str) -> None:
        self.content = content

    async def chat_with_tools(self, **_kwargs):  # noqa: ANN003
        return type("Response", (), {"content": self.content})()


@pytest.mark.parametrize("content", ["", "not json", '{"allow":"true"}', '{"reason":"missing"}'])
def test_gate_rejects_missing_or_invalid_boolean(content: str) -> None:
    allow, _, _ = asyncio.run(gate_should_send(
        tool_caller=_Caller(content), logger=type("Log", (), {"debug": lambda *_args: None})(),
        scenario="test", user_id="1", draft="hello",
    ))
    assert allow is False


def test_gate_accepts_only_true_boolean() -> None:
    allow, _, _ = asyncio.run(gate_should_send(
        tool_caller=_Caller('{"allow":true,"reason":"specific event"}'),
        logger=type("Log", (), {"debug": lambda *_args: None})(), scenario="test", user_id="1", draft="hello",
    ))
    assert allow is True
