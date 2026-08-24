from __future__ import annotations

import asyncio

from ._loader import load_personification_module


service_module = load_personification_module(
    "plugin.personification.core.attention_service"
)


def test_shadow_calls_agent_but_preserves_legacy_result() -> None:
    calls = []

    async def caller(messages, **kwargs):  # noqa: ANN001, ANN003, ANN202
        calls.append((messages, kwargs))
        return '{"action":"reply_candidate","tier":3,"wait_seconds":45,"interest":0.6,"reason_code":"ambient_participation"}'

    service = service_module.AttentionParticipationService(
        call_ai_api=caller,
        mode="shadow",
        microbatch_seconds=0,
    )
    result = asyncio.run(
        service.evaluate(
            session_key="bot:group",
            user_text="普通群消息",
            legacy_should_reply=False,
            is_private=False,
            is_at_bot=False,
            is_reply_to_bot=False,
            is_continuation=False,
        )
    )

    assert calls
    assert result.v2_should_reply in {True, False}
    assert result.actual_should_reply is False
    assert result.to_metrics()["reason_code"] == "ambient_participation"
    assert "普通群消息" not in str(result.to_metrics())


def test_service_failure_uses_structural_fallback() -> None:
    async def caller(*_args, **_kwargs):
        raise TimeoutError("upstream")

    service = service_module.AttentionParticipationService(
        call_ai_api=caller,
        mode="on",
        microbatch_seconds=0,
    )
    result = asyncio.run(
        service.evaluate(
            session_key="bot:user",
            user_text="hello",
            legacy_should_reply=True,
            is_private=True,
            is_at_bot=False,
            is_reply_to_bot=False,
            is_continuation=False,
        )
    )
    assert result.decision_source.value == "fallback"
    assert result.decision.tier == 1
    assert result.decision.wait_seconds == 10


def test_confirmed_send_resets_accumulated_interactions() -> None:
    async def caller(*_args, **_kwargs):
        return '{"action":"reply_candidate","tier":2,"wait_seconds":30,"interest":0.8,"reason_code":"conversation_continuation"}'

    async def scenario():
        service = service_module.AttentionParticipationService(
            call_ai_api=caller,
            mode="shadow",
            microbatch_seconds=0,
        )
        first = await service.evaluate(
            session_key="bot:group",
            user_text="one",
            legacy_should_reply=False,
            is_private=False,
            is_at_bot=False,
            is_reply_to_bot=False,
            is_continuation=True,
        )
        second = await service.evaluate(
            session_key="bot:group",
            user_text="two",
            legacy_should_reply=False,
            is_private=False,
            is_at_bot=False,
            is_reply_to_bot=False,
            is_continuation=True,
        )
        await service.reset_confirmed("bot:group")
        third = await service.evaluate(
            session_key="bot:group",
            user_text="three",
            legacy_should_reply=False,
            is_private=False,
            is_at_bot=False,
            is_reply_to_bot=False,
            is_continuation=True,
        )
        return first, second, third

    first, second, third = asyncio.run(scenario())
    assert (first.unanswered_interactions, second.unanswered_interactions) == (1, 2)
    assert third.unanswered_interactions == 1
