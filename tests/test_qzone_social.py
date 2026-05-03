from __future__ import annotations

import asyncio
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


class _RecordingPersonaStore(_PersonaStore):
    def __init__(self) -> None:
        super().__init__({})
        self.records: list[tuple[str, str]] = []

    async def record_message(self, user_id: str, text: str) -> None:
        self.records.append((str(user_id), str(text)))


class _Logger:
    def warning(self, _message: str) -> None:
        return None


class _Bot:
    self_id = "99999"


class _InboundQzoneService:
    def __init__(self) -> None:
        self.replies: list[str] = []

    async def fetch_user_feeds(self, **_kwargs):  # noqa: ANN003
        return True, "ok", [
            {
                "feed_key": "99999:feed1",
                "feed_id": "feed1",
                "owner_uin": "99999",
                "topic_id": "99999_feed1__1",
                "content": "今天也普通地发一条",
                "raw": {
                    "commentlist": [
                        {
                            "uin": "20001",
                            "nickname": "好友",
                            "content": "真寻今天也在吗",
                            "created_time": 1710000010,
                            "commentid": "c1",
                        }
                    ]
                },
            }
        ]

    async def comment_feed(self, *, content: str, **_kwargs):  # noqa: ANN003
        self.replies.append(str(content))
        return True, "ok"


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


def test_qzone_comment_extraction_normalizes_user_and_content() -> None:
    comments = qzone_service._extract_qzone_comments(
        {
            "commentlist": [
                {
                    "uin": "22222",
                    "nickname": "留言者",
                    "content": "看到啦<br>挺好",
                    "created_time": 1710000010,
                    "commentid": "c1",
                }
            ]
        }
    )

    assert comments[0]["comment_key"].startswith("22222:c1")
    assert comments[0]["nickname"] == "留言者"
    assert comments[0]["content"] == "看到啦 挺好"


def test_qzone_test_target_can_use_open_user_without_friend_profile() -> None:
    candidates = qzone_flow._collect_candidates(
        friend_profiles={},
        proactive_state={},
        persona_store=_PersonaStore({}),
        persona_snippet_max_chars=80,
        target_user_id="3330186224",
        allow_open_user=True,
    )

    assert candidates == [
        {
            "user_id": "3330186224",
            "nickname": "3330186224",
            "persona_snippet": "暂无画像",
            "last_interaction": 0.0,
            "is_friend": False,
        }
    ]


def test_qzone_non_test_target_still_requires_bot_friend() -> None:
    candidates = qzone_flow._collect_candidates(
        friend_profiles={},
        proactive_state={},
        persona_store=_PersonaStore({}),
        persona_snippet_max_chars=80,
        target_user_id="3330186224",
        allow_open_user=False,
    )

    assert candidates == []


def test_qzone_regular_scan_falls_back_to_bot_friends_without_private_state() -> None:
    candidates = qzone_flow._collect_candidates(
        friend_profiles={
            "20002": {"nickname": "二号好友"},
            "20001": {"nickname": "一号好友"},
        },
        proactive_state={},
        persona_store=_PersonaStore({"20001": "画像一"}),
        persona_snippet_max_chars=80,
    )

    assert [item["user_id"] for item in candidates] == ["20001", "20002"]
    assert candidates[0]["is_friend"] is True
    assert candidates[0]["persona_snippet"] == "画像一"


def test_qzone_profile_evidence_records_once_with_source_label() -> None:
    store = _RecordingPersonaStore()
    state: dict[str, object] = {}
    result: dict[str, object] = {}

    asyncio.run(
        qzone_flow._record_qzone_profile_evidence(
            persona_store=store,
            user_id="20001",
            evidence_key="feed:20001:abc",
            kind="动态",
            content="今天去了便利店",
            image_summary="一张饮料和便当的照片",
            state=state,
            result=result,
            logger=_Logger(),
        )
    )
    asyncio.run(
        qzone_flow._record_qzone_profile_evidence(
            persona_store=store,
            user_id="20001",
            evidence_key="feed:20001:abc",
            kind="动态",
            content="今天去了便利店",
            state=state,
            result=result,
            logger=_Logger(),
        )
    )

    assert len(store.records) == 1
    assert store.records[0][0] == "20001"
    assert "[QQ空间动态]" in store.records[0][1]
    assert "图片摘要：一张饮料和便当的照片" in store.records[0][1]
    assert result["profile_records"] == 1


def test_qzone_inbound_comment_replies_once_and_records_profile() -> None:
    store = _RecordingPersonaStore()
    service = _InboundQzoneService()
    state: dict[str, object] = {}
    result: dict[str, object] = {"inbound_comments": 0, "replied": 0, "profile_records": 0}

    async def _call_ai(_messages):  # noqa: ANN001
        return '{"action":"reply","reply":"在呀，刚看到","reason":"自然接话"}'

    kwargs = dict(
        bot=_Bot(),
        qzone_social_service=service,
        friend_profiles={"20001": {"nickname": "好友"}},
        proactive_state={},
        persona_store=store,
        persona_snippet_max_chars=80,
        system_prompt="你是绪山真寻。",
        call_ai_api=_call_ai,
        inner_state={},
        emotion_state={},
        max_feeds=20,
        max_comments_per_feed=20,
        count_checked_feeds=True,
        state=state,
        result=result,
        logger=_Logger(),
    )

    asyncio.run(qzone_flow._scan_bot_space_comments(**kwargs))
    asyncio.run(qzone_flow._scan_bot_space_comments(**kwargs))

    assert service.replies == ["在呀，刚看到"]
    assert result["inbound_comments"] == 1
    assert result["replied"] == 1
    assert len(store.records) == 1
    assert "[QQ空间留言]" in store.records[0][1]


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
