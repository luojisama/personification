from __future__ import annotations

import asyncio
from types import SimpleNamespace

from ._loader import load_personification_module


builders = load_personification_module("plugin.personification.handlers.rule_builders")


class _GroupEvent:
    user_id = "user"
    group_id = "group"
    self_id = "bot"
    to_me = False
    reply = None

    def get_plaintext(self) -> str:
        return "普通消息"


async def _core(_event, state, **_kwargs):  # noqa: ANN001, ANN003, ANN202
    state["attention_admitted"] = True
    state["is_random_chat"] = False
    return False


def _builder(service):  # noqa: ANN001, ANN202
    return builders.build_personification_rule(
        personification_rule_core=_core,
        sign_in_available=False,
        get_user_data=lambda _user: {},
        user_blacklist={},
        logger=None,
        group_event_cls=_GroupEvent,
        private_event_cls=object,
        is_group_whitelisted=lambda _group, _items: True,
        plugin_whitelist=[],
        load_prompt=lambda _group: {},
        load_proactive_state=lambda: {},
        is_rest_time=lambda **_kwargs: True,
        probability=0.3,
        group_chat_follow_probability=0.6,
        looks_like_private_command=lambda _text: False,
        get_recent_group_msgs=lambda _group, _limit: [],
        attention_service=service,
    )


def test_shadow_records_decision_but_keeps_legacy_behavior() -> None:
    evaluation = SimpleNamespace(
        decision=SimpleNamespace(
            tier=3,
            wait_seconds=45.0,
            to_dict=lambda: {
                "action": "reply_candidate",
                "tier": 3,
                "wait_seconds": 45.0,
                "interest": 0.5,
                "reason_code": "ambient_participation",
            },
        ),
        mode=SimpleNamespace(value="shadow"),
        actual_should_reply=False,
        to_metrics=lambda: {"mode": "shadow", "actual_should_reply": False},
    )

    class _Service:
        async def evaluate(self, **_kwargs):  # noqa: ANN003, ANN202
            return evaluation

    state = {}
    matched = asyncio.run(_builder(_Service())(_GroupEvent(), state))
    assert matched is False
    assert state["attention_wait_seconds"] == 45.0
    assert state["attention_metrics"]["mode"] == "shadow"


def test_on_can_admit_ambient_candidate_as_random_chat() -> None:
    evaluation = SimpleNamespace(
        decision=SimpleNamespace(
            tier=3,
            wait_seconds=50.0,
            to_dict=lambda: {
                "action": "reply_candidate",
                "tier": 3,
                "wait_seconds": 50.0,
                "interest": 0.8,
                "reason_code": "ambient_participation",
            },
        ),
        mode=SimpleNamespace(value="on"),
        actual_should_reply=True,
        to_metrics=lambda: {"mode": "on", "actual_should_reply": True},
    )

    class _Service:
        async def evaluate(self, **_kwargs):  # noqa: ANN003, ANN202
            return evaluation

    state = {}
    matched = asyncio.run(_builder(_Service())(_GroupEvent(), state))
    assert matched is True
    assert state["is_random_chat"] is True
