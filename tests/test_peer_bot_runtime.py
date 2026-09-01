from __future__ import annotations

import asyncio
import copy
import json
from types import SimpleNamespace

import pytest

from ._loader import load_personification_module


db = load_personification_module("plugin.personification.core.db")
provenance = load_personification_module("plugin.personification.core.message_provenance")
qq_outbound = load_personification_module("plugin.personification.core.qq_outbound")
registry_module = load_personification_module("plugin.personification.core.peer_bot_registry")
runtime_module = load_personification_module("plugin.personification.core.peer_bot_runtime")


class _Store:
    def __init__(self) -> None:
        self.namespaces: dict[str, object] = {}

    def load_sync(self, name: str):
        return copy.deepcopy(self.namespaces.get(name, {}))

    def mutate_sync(self, name: str, mutator):
        updated = mutator(copy.deepcopy(self.namespaces.get(name, {})))
        self.namespaces[name] = copy.deepcopy(updated)
        return copy.deepcopy(updated)


def _config(**overrides):
    values = {
        "personification_peer_bot_enabled": True,
        "personification_peer_bot_max_command_chars": 500,
        "personification_peer_bot_cooldown_seconds": 10.0,
        "personification_peer_bot_pending_ttl_seconds": 30.0,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _approved_registry(*, risk: str = "write", template: str = ".mc say {message}", schema=None):
    cfg = _config()
    registry = registry_module.PeerBotRegistry(store=_Store(), plugin_config=cfg)
    registry.set_settings("415442985", enabled=True, cooldown_seconds=0)
    registry.set_bot_status("415442985", user_id="10001", action="approve", nickname="Usagi")
    command = registry.upsert_command(
        "415442985",
        target_bot_id="10001",
        full_template=template,
        parameter_schema=schema,
        risk_level=risk,
        status="approved",
        source="manual",
        manual_override=True,
    )
    return registry, cfg, command


@pytest.mark.parametrize(
    ("template", "schema", "arguments", "expected"),
    [
        (".mc say {message}", None, {"message": "大家好"}, ".mc say 大家好"),
        ("/抽卡", None, {}, "/抽卡"),
        (
            "/roll {count} {public}",
            {
                "type": "object",
                "properties": {
                    "count": {"type": "integer", "minimum": 1, "maximum": 10},
                    "public": {"type": "boolean"},
                },
                "required": ["count", "public"],
                "additionalProperties": False,
            },
            {"count": 3, "public": True},
            "/roll 3 true",
        ),
    ],
)
def test_render_approved_command_is_template_driven(template, schema, arguments, expected) -> None:
    command = {"full_template": template, "parameter_schema": schema or {}}
    assert runtime_module.render_approved_command(command, arguments=arguments) == expected
    assert runtime_module.render_approved_command(
        command,
        arguments=arguments,
        full_command=expected,
    ) == expected


def test_full_command_is_exactly_parsed_against_approved_template() -> None:
    command = {
        "full_template": "/roll {count}",
        "parameter_schema": {
            "type": "object",
            "properties": {"count": {"type": "integer", "minimum": 1, "maximum": 10}},
            "required": ["count"],
            "additionalProperties": False,
        },
    }
    assert runtime_module.render_approved_command(command, full_command="/roll 3") == "/roll 3"
    rejected = [
        ({"count": 3, "extra": "x"}, None),
        ({"count": 3}, "/roll 4"),
        ({"count": 11}, None),
        ({"count": 3}, "/roll 3\n/admin"),
    ]
    for arguments, full_command in rejected:
        with pytest.raises(registry_module.PeerBotRegistryError):
            runtime_module.render_approved_command(
                command,
                arguments=arguments,
                full_command=full_command,
            )


class _Event:
    group_id = "415442985"
    user_id = "20002"
    self_id = "99999"
    message_id = "incoming-1"
    message = []
    reply = None

    def get_plaintext(self):
        return "请去服务器打个招呼"


class _SentBot:
    self_id = "99999"

    def __init__(self) -> None:
        self.sent: list[tuple[object, str]] = []

    async def send_group_msg(self, *, group_id, message):
        self.sent.append((group_id, message))
        return {"message_id": "outbound-1"}


def _ledger(tmp_path):
    return qq_outbound.QQOutboundLedger(db.init_db_sync(tmp_path))


def test_invoke_tool_sent_records_ledger_pending_and_group_history(tmp_path) -> None:
    registry, cfg, command = _approved_registry()
    tracker = runtime_module.PeerBotRuntimeTracker()
    bot = _SentBot()
    recorded: list[tuple[tuple, dict]] = []
    tool = runtime_module.build_invoke_peer_bot_tool(
        bot=bot,
        event=_Event(),
        registry=registry,
        tracker=tracker,
        plugin_config=cfg,
        qq_outbound_ledger=_ledger(tmp_path),
        record_group_msg=lambda *args, **kwargs: recorded.append((args, kwargs)),
    )

    result = json.loads(
        asyncio.run(
            tool.handler(
                target_bot_id="10001",
                command_id=command["command_id"],
                arguments={"message": "大家下午好"},
                full_command=None,
            )
        )
    )
    assert result["status"] == "sent"
    assert result["diagnostic_code"] == "peer_bot_dispatch_sent"
    assert result["pending"] is True
    assert bot.sent == [(415442985, ".mc say 大家下午好")]
    assert tracker.snapshot(group_id="415442985")["pending_count"] == 1
    assert recorded[0][1]["source_kind"] == "peer_bot_command"
    assert recorded[0][1]["message_id"] == "outbound-1"


def test_failed_and_unknown_dispatches_never_create_pending(tmp_path) -> None:
    registry, cfg, command = _approved_registry()

    class ExplicitFailure(_SentBot):
        async def send_group_msg(self, **_kwargs):
            error = RuntimeError("explicit rejection")
            error.retcode = 1404
            raise error

    class UnknownFailure(_SentBot):
        async def send_group_msg(self, **_kwargs):
            raise TimeoutError("delivery may have happened")

    for bot, expected in ((ExplicitFailure(), "failed"), (UnknownFailure(), "unknown")):
        tracker = runtime_module.PeerBotRuntimeTracker()
        tool = runtime_module.build_invoke_peer_bot_tool(
            bot=bot,
            event=_Event(),
            registry=registry,
            tracker=tracker,
            plugin_config=cfg,
            qq_outbound_ledger=_ledger(tmp_path / expected),
            record_group_msg=None,
        )
        result = json.loads(
            asyncio.run(
                tool.handler(
                    target_bot_id="10001",
                    command_id=command["command_id"],
                    arguments={"message": "hello"},
                )
            )
        )
        assert result["status"] == expected
        assert tracker.snapshot(group_id="415442985")["pending_count"] == 0


@pytest.mark.parametrize("risk", ["admin", "dangerous"])
def test_admin_and_dangerous_commands_are_always_rejected(risk, tmp_path) -> None:
    registry, cfg, command = _approved_registry(risk=risk, template="/manage")
    bot = _SentBot()
    tool = runtime_module.build_invoke_peer_bot_tool(
        bot=bot,
        event=_Event(),
        registry=registry,
        tracker=runtime_module.PeerBotRuntimeTracker(),
        plugin_config=cfg,
        qq_outbound_ledger=_ledger(tmp_path),
        record_group_msg=None,
    )
    result = json.loads(
        asyncio.run(
            tool.handler(
                target_bot_id="10001",
                command_id=command["command_id"],
                arguments={},
            )
        )
    )
    assert result["diagnostic_code"] == "peer_bot_command_risk_blocked"
    assert bot.sent == []


def test_list_tool_exposes_only_callable_ids_and_risks_without_templates(tmp_path) -> None:
    registry, _cfg, command = _approved_registry()
    registry.upsert_command(
        "415442985",
        target_bot_id="10001",
        full_template="/admin stop",
        risk_level="admin",
        status="approved",
        source="manual",
        manual_override=True,
    )
    tool = runtime_module.build_list_peer_bots_tool(
        group_id="415442985",
        registry=registry,
        tracker=runtime_module.PeerBotRuntimeTracker(),
    )

    result = json.loads(asyncio.run(tool.handler()))

    commands = result["approved_bots"][0]["commands"]
    assert commands == [{"command_id": command["command_id"], "risk_level": "write"}]
    assert "full_template" not in json.dumps(result)


def test_single_turn_limit_applies_after_send_begins(tmp_path) -> None:
    registry, cfg, command = _approved_registry()
    bot = _SentBot()
    tool = runtime_module.build_invoke_peer_bot_tool(
        bot=bot,
        event=_Event(),
        registry=registry,
        tracker=runtime_module.PeerBotRuntimeTracker(),
        plugin_config=cfg,
        qq_outbound_ledger=_ledger(tmp_path),
        record_group_msg=None,
    )

    async def run():
        first = await tool.handler(
            target_bot_id="10001",
            command_id=command["command_id"],
            arguments={"message": "one"},
        )
        second = await tool.handler(
            target_bot_id="10001",
            command_id=command["command_id"],
            arguments={"message": "two"},
        )
        return json.loads(first), json.loads(second)

    first, second = asyncio.run(run())
    assert first["status"] == "sent"
    assert second["diagnostic_code"] == "peer_bot_turn_limit"
    assert len(bot.sent) == 1


def test_pending_reference_match_then_fifo_and_timeout() -> None:
    now = [100.0]
    tracker = runtime_module.PeerBotRuntimeTracker(clock=lambda: now[0])
    for index in (1, 2):
        tracker.record_dispatch(
            group_id="g",
            target_bot_id="b",
            trigger_user_id="u",
            tracking_id=f"t{index}",
            operation_id=f"o{index}",
            command_id=f"c{index}",
            send_status="sent",
            outbound_message_id=f"m{index}",
            ttl_seconds=10,
            now=now[0],
        )
    assert tracker.match_reply(
        group_id="g", target_bot_id="b", reply_to_message_id="not-ours", now=100.1
    ) is None
    second = tracker.match_reply(
        group_id="g", target_bot_id="b", reply_to_message_id="m2", now=100.2
    )
    assert second and second.tracking_id == "t2"
    first = tracker.match_reply(group_id="g", target_bot_id="b", now=100.3)
    assert first and first.tracking_id == "t1"
    assert tracker.match_reply(group_id="g", target_bot_id="b", now=100.4) is None

    tracker.record_dispatch(
        group_id="g",
        target_bot_id="b",
        trigger_user_id="u",
        tracking_id="expired",
        operation_id="expired-op",
        command_id="c",
        send_status="sent",
        outbound_message_id="expired-message",
        ttl_seconds=1,
        now=100.0,
    )
    now[0] = 102.0
    assert tracker.snapshot(group_id="g")["pending_count"] == 0
    episodes = tracker.recent_episodes("g")
    assert episodes[-1].status == "timeout"
    assert not any(item.tracking_id == "expired" and item.status == "pending" for item in episodes)


def test_coordinator_marks_candidate_and_matches_approved_reply() -> None:
    registry, _cfg, command = _approved_registry()
    registry.observe_candidate_bot("415442985", user_id="30003", confidence=0.8)
    tracker = runtime_module.PeerBotRuntimeTracker()
    tracker.record_dispatch(
        group_id="415442985",
        target_bot_id="10001",
        trigger_user_id="20002",
        tracking_id="tracking",
        operation_id="operation",
        command_id=command["command_id"],
        send_status="sent",
        outbound_message_id="outbound-1",
        ttl_seconds=30,
    )
    coordinator = runtime_module.PeerBotCoordinator(
        registry=registry,
        tracker=tracker,
        plugin_config=_config(),
    )

    candidate = _Event()
    candidate.user_id = "30003"
    assert coordinator.classify_event(candidate).source_kind == "peer_bot_candidate"

    class Reply:
        message_id = "outbound-1"

    reply = _Event()
    reply.user_id = "10001"
    reply.message_id = "reply-1"
    reply.reply = Reply()
    classification = coordinator.classify_event(reply)
    assert classification.source_kind == "peer_bot_reply"
    assert classification.matched_request is not None
    assert tracker.snapshot(group_id="415442985")["pending_count"] == 0
    completed = tracker.recent_episodes("415442985")[-1]
    assert completed.status == "completed"
    assert completed.reply_message_ids == ("reply-1",)


def test_peer_sources_are_not_human_or_personification_replies() -> None:
    for source_kind in runtime_module.PEER_BOT_SOURCE_KINDS:
        record = {"source_kind": source_kind, "user_id": "10001", "is_bot": False}
        assert provenance.is_human_chat_record(record) is False
        assert provenance.is_personification_reply_record(record) is False
        assert provenance.is_external_plugin_record(record) is True
        assert provenance.is_peer_bot_record(record) is True


def test_disabled_matching_preserves_bot_provenance_without_consuming_pending() -> None:
    registry, cfg, command = _approved_registry()
    registry.set_settings("415442985", enabled=False)
    tracker = runtime_module.PeerBotRuntimeTracker()
    tracker.record_dispatch(
        group_id="415442985",
        target_bot_id="10001",
        trigger_user_id="20002",
        tracking_id="tracking",
        operation_id="operation",
        command_id=command["command_id"],
        send_status="sent",
        outbound_message_id="outbound-1",
        ttl_seconds=30,
    )
    coordinator = runtime_module.PeerBotCoordinator(
        registry=registry,
        tracker=tracker,
        plugin_config=cfg,
    )
    event = _Event()
    event.user_id = "10001"
    event.reply = type("Reply", (), {"message_id": "outbound-1"})()
    classification = coordinator.classify_event(event)
    assert classification.source_kind == "peer_bot_reply"
    assert classification.matched_request is None
    assert classification.diagnostic_code == "peer_bot_matching_disabled"
    assert tracker.snapshot(group_id="415442985")["pending_count"] == 1


def test_missing_ledger_records_unknown_episode_without_pending() -> None:
    registry, cfg, command = _approved_registry()
    tracker = runtime_module.PeerBotRuntimeTracker()
    tool = runtime_module.build_invoke_peer_bot_tool(
        bot=_SentBot(),
        event=_Event(),
        registry=registry,
        tracker=tracker,
        plugin_config=cfg,
        qq_outbound_ledger=None,
        record_group_msg=None,
    )
    result = json.loads(
        asyncio.run(
            tool.handler(
                target_bot_id="10001",
                command_id=command["command_id"],
                arguments={"message": "hello"},
            )
        )
    )
    assert result["status"] == "unknown"
    assert result["diagnostic_code"] == "peer_bot_ledger_unavailable"
    assert tracker.snapshot(group_id="415442985")["pending_count"] == 0
    assert tracker.recent_episodes("415442985")[-1].send_status == "unknown"
