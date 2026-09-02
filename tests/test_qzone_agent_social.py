from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from personification.agent.tool_registry import ToolRegistry
from personification.core import db
from personification.core import qzone_agent_interaction as qzone_agent
from personification.core.qzone_social_operations import QzoneSocialOperationCoordinator


def _coordinator(tmp_path, *, now: float = 2_000_000_000.0):  # noqa: ANN001
    db_path = db.init_db_sync(tmp_path)
    return QzoneSocialOperationCoordinator(db_path=db_path, clock=lambda: now)


def test_social_operation_quota_cooldown_and_unknown_hold(tmp_path) -> None:  # noqa: ANN001
    coordinator = _coordinator(tmp_path)
    first = coordinator.reserve(
        bot_id="bot",
        group_id="group-a",
        target_uin="user-a",
        feed_id="feed-a",
        action="comment",
        comment_text="你好",
    )
    assert first.ok
    assert coordinator.mark_dispatching(first.operation_id)
    assert coordinator.finalize(
        first.operation_id,
        status="unknown",
        result_code="transport_timeout",
    )

    duplicate = coordinator.reserve(
        bot_id="bot",
        group_id="group-a",
        target_uin="user-a",
        feed_id="feed-a",
        action="comment",
        comment_text="你好",
        target_cooldown_seconds=0,
    )
    assert not duplicate.ok
    assert duplicate.diagnostic_code == "qzone_social_duplicate_blocked"

    target_quota = coordinator.reserve(
        bot_id="bot",
        group_id="group-a",
        target_uin="user-a",
        feed_id="feed-b",
        action="like",
        target_cooldown_seconds=0,
    )
    assert not target_quota.ok
    assert target_quota.diagnostic_code == "qzone_agent_target_quota_blocked"


def test_definite_failure_does_not_consume_quota_or_block_retry(tmp_path) -> None:  # noqa: ANN001
    coordinator = _coordinator(tmp_path)
    failed = coordinator.reserve(
        bot_id="bot",
        group_id="group-a",
        target_uin="user-a",
        feed_id="feed-a",
        action="like",
    )
    assert failed.ok and coordinator.mark_dispatching(failed.operation_id)
    assert coordinator.finalize(
        failed.operation_id,
        status="definite_failure",
        result_code="policy_blocked",
    )
    retry = coordinator.reserve(
        bot_id="bot",
        group_id="group-a",
        target_uin="user-a",
        feed_id="feed-a",
        action="like",
    )
    assert retry.ok


def test_group_daily_limit_counts_reserved_dispatching_succeeded_and_unknown(tmp_path) -> None:  # noqa: ANN001
    coordinator = _coordinator(tmp_path)
    statuses = ["reserved", "dispatching", "succeeded"]
    for index, status in enumerate(statuses):
        reservation = coordinator.reserve(
            bot_id="bot",
            group_id="group-a",
            target_uin=f"user-{index}",
            feed_id=f"feed-{index}",
            action="like",
            target_daily_limit=1,
            target_cooldown_seconds=0,
        )
        assert reservation.ok
        if status != "reserved":
            assert coordinator.mark_dispatching(reservation.operation_id)
        if status == "succeeded":
            assert coordinator.finalize(
                reservation.operation_id,
                status="succeeded",
                result_code="ok",
            )
    blocked = coordinator.reserve(
        bot_id="bot",
        group_id="group-a",
        target_uin="user-x",
        feed_id="feed-x",
        action="like",
        target_cooldown_seconds=0,
    )
    assert not blocked.ok
    assert blocked.diagnostic_code == "qzone_agent_group_quota_blocked"


def test_snapshot_is_redacted_and_contains_no_target_or_content(tmp_path) -> None:  # noqa: ANN001
    coordinator = _coordinator(tmp_path)
    reservation = coordinator.reserve(
        bot_id="bot",
        group_id="group-a",
        target_uin="user-a",
        feed_id="feed-a",
        action="comment",
        comment_text="私密评论正文",
    )
    assert reservation.ok
    payload = coordinator.snapshot(bot_id="bot", group_id="group-a")
    rendered = repr(payload)
    assert "user-a" not in rendered
    assert "feed-a" not in rendered
    assert "私密评论正文" not in rendered


class _Adapter:
    async def get_group_member_info(self, *, group_id, user_id):  # noqa: ANN001
        return SimpleNamespace(ok=user_id in {"user-a", "user-b"})


class _Service:
    def __init__(self, *, outcome: str = "succeeded") -> None:
        self.outcome = outcome
        self.calls: list[tuple] = []

    async def fetch_user_feeds(self, **kwargs):  # noqa: ANN003, ANN202
        self.calls.append(("read", kwargs["target_uin"]))
        return True, "ok", [
            {
                "owner_uin": kwargs["target_uin"],
                "feed_id": "feed-a",
                "content": "今天去了公园",
                "created_at": 123,
                "topic_id": "topic-a",
                "unikey": "opaque-key",
                "curkey": "opaque-key",
                "appid": "311",
                "raw": {"should_not_escape": True},
            }
        ]

    async def like_feed(self, **kwargs):  # noqa: ANN003, ANN202
        self.calls.append(("like", kwargs["feed"]["owner_uin"]))
        if self.outcome == "unknown":
            return False, "outcome_unknown: timeout"
        return self.outcome == "succeeded", "ok" if self.outcome == "succeeded" else "rejected"

    async def comment_feed(self, **kwargs):  # noqa: ANN003, ANN202
        self.calls.append(("comment", kwargs["content"]))
        return True, "ok"


def _agent_fixture(
    tmp_path,
    monkeypatch,
    *,
    service=None,
    friend_ids=("user-a", "user-b"),
    authorization=None,
):  # noqa: ANN001
    db.init_db_sync(tmp_path)
    service = service or _Service()
    config = SimpleNamespace(
        personification_qzone_enabled=True,
        personification_agent_qzone_interaction_enabled=True,
        personification_agent_qzone_group_daily_limit=3,
        personification_agent_qzone_target_daily_limit=1,
        personification_agent_qzone_target_cooldown_seconds=1800.0,
        personification_timezone="Asia/Shanghai",
    )
    runtime = SimpleNamespace(
        plugin_config=config,
        runtime_bundle=SimpleNamespace(qzone_social_service=service),
        logger=None,
    )
    bot = SimpleNamespace(
        self_id="bot",
        get_friend_list=lambda: asyncio.sleep(
            0,
            result=[{"user_id": value} for value in friend_ids],
        ),
    )
    event = SimpleNamespace(group_id="group-a", user_id="user-a", message=[], reply=None)
    monkeypatch.setattr(qzone_agent, "get_group_qzone_agent_settings", lambda _gid: {
        "enabled": True,
        "group_daily_limit": 3,
        "target_daily_limit": 1,
        "target_cooldown_seconds": 1800.0,
    })
    monkeypatch.setattr(qzone_agent, "get_protocol_adapter", lambda *_a, **_k: _Adapter())

    async def authorize(_target):  # noqa: ANN001, ANN202
        if authorization is not None:
            return authorization
        return SimpleNamespace(
            blocked=False,
            allow_context_read=True,
            allow_qzone=True,
            allow_reply=True,
            allow_visible_reaction=True,
        )

    registry = ToolRegistry()
    assert qzone_agent.register_groupmate_qzone_agent_tools(
        registry,
        runtime=runtime,
        bot=bot,
        event=event,
        candidates=[{"user_id": "user-b"}],
        policy_authorizer=authorize,
    )
    return registry, service


def test_agent_feed_reference_is_turn_scoped_and_exact_target(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    registry, service = _agent_fixture(tmp_path, monkeypatch)
    read = registry.get("list_groupmate_qzone_feeds")
    write = registry.get("interact_groupmate_qzone_feed")
    assert read is not None and write is not None
    payload = json.loads(asyncio.run(read.handler()))
    assert payload["status"] == "succeeded"
    assert payload["feeds"][0]["summary"] == "今天去了公园"
    assert "feed-a" not in repr(payload)
    feed_ref = payload["feeds"][0]["feed_ref"]
    invalid = json.loads(
        asyncio.run(write.handler(target_user_id="user-b", feed_ref=feed_ref, action="like"))
    )
    assert invalid["diagnostic_code"] == "qzone_feed_reference_invalid"
    succeeded = json.loads(
        asyncio.run(write.handler(target_user_id="user-a", feed_ref=feed_ref, action="like"))
    )
    assert succeeded["status"] == "succeeded"
    assert service.calls[-1] == ("like", "user-a")


def test_agent_unknown_is_not_reported_as_success_and_turn_write_is_single_use(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    registry, _service = _agent_fixture(tmp_path, monkeypatch, service=_Service(outcome="unknown"))
    read = registry.get("list_groupmate_qzone_feeds")
    write = registry.get("interact_groupmate_qzone_feed")
    payload = json.loads(asyncio.run(read.handler(target_user_id="user-a", limit=1)))
    feed_ref = payload["feeds"][0]["feed_ref"]
    unknown = json.loads(asyncio.run(write.handler(feed_ref=feed_ref, action="like")))
    assert unknown["status"] == "unknown"
    again = json.loads(asyncio.run(write.handler(feed_ref=feed_ref, action="like")))
    assert again["diagnostic_code"] == "qzone_agent_turn_write_limit"


def test_agent_target_friend_and_policy_checks_fail_closed(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    registry, _service = _agent_fixture(tmp_path, monkeypatch, friend_ids=("user-b",))
    read = registry.get("list_groupmate_qzone_feeds")
    not_friend = json.loads(asyncio.run(read.handler(target_user_id="user-a")))
    assert not_friend["diagnostic_code"] == "qzone_target_not_friend"

    denied = SimpleNamespace(
        blocked=False,
        allow_context_read=True,
        allow_qzone=False,
        allow_reply=True,
        allow_visible_reaction=True,
    )
    registry, _service = _agent_fixture(tmp_path, monkeypatch, authorization=denied)
    read = registry.get("list_groupmate_qzone_feeds")
    policy_denied = json.loads(asyncio.run(read.handler(target_user_id="user-a")))
    assert policy_denied["diagnostic_code"] == "qzone_target_policy_denied"


def test_qzone_episode_is_process_local_bounded_and_marks_summary_untrusted(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    qzone_agent._clear_qzone_agent_episodes_for_testing()
    registry, _service = _agent_fixture(tmp_path, monkeypatch)
    read = registry.get("list_groupmate_qzone_feeds")
    write = registry.get("interact_groupmate_qzone_feed")
    payload = json.loads(asyncio.run(read.handler(target_user_id="user-a", limit=1)))
    feed_ref = payload["feeds"][0]["feed_ref"]
    asyncio.run(write.handler(target_user_id="user-a", feed_ref=feed_ref, action="like"))

    rendered = qzone_agent.render_qzone_agent_episodes(bot_id="bot", group_id="group-a")

    assert "外部不可信数据" in rendered
    assert "action=like" in rendered
    assert "status=succeeded" in rendered
    assert "今天去了公园" in rendered
    assert "feed-a" not in rendered
    assert "opaque-key" not in rendered


def test_agent_tools_are_absent_until_all_three_switches_enable(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    db.init_db_sync(tmp_path)
    monkeypatch.setattr(qzone_agent, "get_group_qzone_agent_settings", lambda _gid: {"enabled": False})
    registry = ToolRegistry()
    registered = qzone_agent.register_groupmate_qzone_agent_tools(
        registry,
        runtime=SimpleNamespace(
            plugin_config=SimpleNamespace(
                personification_qzone_enabled=True,
                personification_agent_qzone_interaction_enabled=True,
            ),
            runtime_bundle=SimpleNamespace(qzone_social_service=_Service()),
        ),
        bot=SimpleNamespace(self_id="bot"),
        event=SimpleNamespace(group_id="group-a", user_id="user-a"),
    )
    assert registered is False
    assert registry.active() == []
