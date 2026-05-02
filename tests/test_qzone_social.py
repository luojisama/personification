from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from ._loader import load_personification_module

proactive_flow = load_personification_module("plugin.personification.flows.proactive_flow")
qzone_flow = load_personification_module("plugin.personification.flows.qzone_social_flow")
qzone_service = load_personification_module("plugin.personification.core.qzone_service")
admin_commands = load_personification_module("plugin.personification.handlers.persona_admin_commands")


class _PersonaStore:
    def __init__(self, snippets: dict[str, str]) -> None:
        self.snippets = snippets

    def get_persona_snippet(self, user_id: str, max_chars: int = 150) -> str:
        return self.snippets.get(str(user_id), "")[:max_chars]


def test_proactive_candidates_require_profile_and_not_favorability_threshold() -> None:
    candidates = proactive_flow._build_candidates(
        all_user_data={
            "10001": {"favorability": 1.0, "nickname": "low"},
            "10002": {"favorability": 99.0, "nickname": "high"},
            "group_1": {"favorability": 100.0},
        },
        proactive_state={
            "10001": {"last_interaction": 1000},
            "10002": {"last_interaction": 1000},
        },
        inner_state={},
        emotion_state={},
        now=datetime.fromtimestamp(100000),
        threshold=60.0,
        idle_hours=0,
        friend_ids={"10001", "10002"},
        persona_store=_PersonaStore({"10001": "已有画像"}),
        require_user_profile=True,
    )

    assert [item["user_id"] for item in candidates] == ["10001"]
    assert candidates[0]["favorability"] == 1.0


def test_qzone_feed_normalization_extracts_text_images_and_keys() -> None:
    feed = qzone_service._normalize_qzone_feed(
        {
            "tid": "abc",
            "uin": "12345",
            "nickname": "好友",
            "content": "今天<br>不错",
            "pic": [{"url1": "//img.example/a.jpg"}],
            "created_time": 1710000000,
        },
        target_uin="12345",
    )

    assert feed is not None
    assert feed["feed_key"] == "12345:abc"
    assert feed["content"] == "今天 不错"
    assert feed["images"] == ["https://img.example/a.jpg"]
    assert feed["topic_id"] == "12345_abc__1"


def test_qzone_social_limits_use_zero_as_unlimited() -> None:
    assert qzone_flow._limit_reached(0, 9999) is False
    assert qzone_flow._limit_reached(2, 2) is True
    assert qzone_flow._limit_reached(2, 1) is False


def test_qzone_admin_target_accepts_at_and_plain_qq() -> None:
    at_message = [
        SimpleNamespace(type="at", data={"qq": "12345678"}),
        SimpleNamespace(type="text", data={"text": " "}),
    ]

    assert admin_commands._extract_qzone_target_user_id([], at_message) == "12345678"
    assert admin_commands._extract_qzone_target_user_id(["测试", "87654321"], None) == "87654321"
