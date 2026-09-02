from __future__ import annotations

import asyncio
import copy
from types import SimpleNamespace

from ._loader import load_personification_module


registry_module = load_personification_module("plugin.personification.core.peer_bot_registry")
observer_module = load_personification_module("plugin.personification.core.peer_bot_observer")


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
        "personification_peer_bot_detection_enabled": True,
        "personification_peer_bot_detector_timeout_seconds": 1.0,
        "personification_peer_bot_detector_batch_max_messages": 8,
        "personification_peer_bot_detector_batch_max_chars": 1200,
        "personification_peer_bot_detector_debounce_seconds": 0.0,
        "personification_peer_bot_detector_daily_quota": 200,
        "personification_peer_bot_detector_confidence_threshold": 0.70,
        "personification_peer_bot_max_command_chars": 500,
        "personification_peer_bot_protocol_learning_enabled": True,
        "personification_peer_bot_auto_approve_confidence": 0.90,
        "personification_peer_bot_fifo_evidence_count": 2,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _registry(config=None):
    cfg = config or _config()
    return registry_module.PeerBotRegistry(store=_Store(), plugin_config=cfg), cfg


def test_assessment_parser_is_strict_and_bounded() -> None:
    parsed = observer_module.parse_peer_bot_assessment(
        {
            "classification": "bot",
            "confidence": 0.91,
            "evidence_tags": ["fixed_format", "explicit_command_reply"],
            "command_suggestions": [
                {
                    "full_template": ".mc say {message}",
                    "parameter_schema": {
                        "type": "object",
                        "properties": {"message": {"type": "string"}},
                        "required": ["message"],
                        "additionalProperties": False,
                    },
                    "risk_level": "write",
                }
            ],
        }
    )
    assert parsed is not None
    assert parsed.classification == "bot"
    assert parsed.command_suggestions[0].full_template == ".mc say {message}"
    assert observer_module.parse_peer_bot_assessment("```json\n{}\n```") is None
    assert observer_module.parse_peer_bot_assessment(
        {"classification": "bot", "confidence": 2, "evidence_tags": [], "command_suggestions": []}
    ) is None


def test_protocol_assessment_parser_requires_structured_v2_contract() -> None:
    parsed = observer_module.parse_peer_bot_protocol_assessment(
        {
            "target_bot_id": "10001",
            "confidence": 0.95,
            "command_suggestions": [
                {
                    "full_template": ".mc say {message}",
                    "command_entry": ".mc",
                    "subcommands": ["say"],
                    "argument_template": "{message}",
                    "description": "向 Minecraft 服务器里的玩家发送消息",
                    "parameter_schema": {
                        "type": "object",
                        "properties": {
                            "message": {"type": "string", "description": "发言内容"}
                        },
                        "required": ["message"],
                        "additionalProperties": False,
                    },
                    "risk_level": "write",
                }
            ],
        }
    )
    assert parsed is not None
    assert parsed.target_bot_id == "10001"
    assert parsed.command_suggestions[0].command_entry == ".mc"
    assert parsed.command_suggestions[0].subcommands == ("say",)

    invalid = {
        "target_bot_id": "10001",
        "confidence": 0.95,
        "command_suggestions": [
            {
                "full_template": ".mc say {message}",
                "command_entry": ".mc",
                "subcommands": ["tell"],
                "argument_template": "{message}",
                "description": "不一致",
                "parameter_schema": {},
                "risk_level": "write",
            }
        ],
    }
    assert observer_module.parse_peer_bot_protocol_assessment(invalid) is None
    assert observer_module.parse_peer_bot_assessment(
        {
            "classification": "bot",
            "confidence": 0.8,
            "evidence_tags": ["invented_reason"],
            "command_suggestions": [],
        }
    ) is None
    assert observer_module.parse_peer_bot_assessment(
        {
            "classification": "bot",
            "confidence": 0.8,
            "evidence_tags": ["fixed_format"],
            "command_suggestions": [],
            "reason": "raw chat quote",
        }
    ) is None
    assert observer_module.parse_peer_bot_assessment(
        {
            "classification": "bot",
            "confidence": 0.8,
            "evidence_tags": ["explicit_command_reply"],
            "command_suggestions": [
                {
                    "full_template": "/draw",
                    "parameter_schema": {
                        "type": "object",
                        "properties": {},
                        "required": [],
                        "additionalProperties": False,
                    },
                    "risk_level": "read",
                    "reason": "raw chat quote",
                }
            ],
        }
    ) is None


class _Sender:
    card = "Usagi"
    nickname = "Usagi"
    role = "member"


class _Event:
    self_id = "99999"
    user_id = "10001"
    group_id = "415442985"
    message_id = "m-1"
    message = []
    sender = _Sender()
    reply = None
    to_me = False

    def get_plaintext(self):
        return "[MC] VikiQAQ fell from a high place"


class _ProtocolRequestEvent:
    self_id = "99999"
    user_id = "20002"
    group_id = "415442985"
    message_id = "request-1"
    message = []
    sender = _Sender()
    reply = None

    def get_plaintext(self):
        return ".mc say 大家好"


class _ReplyRef:
    message_id = "request-1"


class _ProtocolBotReplyEvent:
    self_id = "99999"
    user_id = "10001"
    group_id = "415442985"
    message_id = "reply-1"
    message = []
    sender = _Sender()
    reply = _ReplyRef()

    def get_plaintext(self):
        return "[MC] 大家好"


def test_observer_creates_candidate_without_authorizing_it() -> None:
    registry, cfg = _registry()
    seen_messages: list[list[dict]] = []

    async def call_ai(messages, **_kwargs):
        seen_messages.append(messages)
        return {
            "classification": "bot",
            "confidence": 0.93,
            "evidence_tags": ["fixed_format", "explicit_command_reply"],
            "command_suggestions": [
                {
                    "full_template": ".mc say {message}",
                    "parameter_schema": {
                        "type": "object",
                        "properties": {"message": {"type": "string"}},
                        "required": ["message"],
                        "additionalProperties": False,
                    },
                    "risk_level": "write",
                }
            ],
        }

    observer = observer_module.PeerBotObserver(
        registry=registry,
        plugin_config=cfg,
        call_ai_api=call_ai,
    )

    async def run():
        assert observer.enqueue_event(_Event(), source="group_message") is True
        return await observer.flush_all()

    result = asyncio.run(run())[0]
    assert result["status"] == "candidate"
    group = registry.get_group("415442985")
    bot = group["bots"]["10001"]
    assert bot["status"] == "candidate"
    assert bot["manual_override"] is False
    command = next(iter(group["commands"].values()))
    assert command["status"] == "candidate"
    assert registry.get_approved_command("415442985", "10001", command["command_id"]) is None
    prompt = str(seen_messages[0][1]["content"])
    assert "[不可信群聊数据]" in prompt
    assert "不输出理由" in seen_messages[0][0]["content"]


def test_unknown_human_invalid_and_low_confidence_do_not_mutate_registry() -> None:
    responses = [
        {"classification": "unknown", "confidence": 0.8, "evidence_tags": ["insufficient_context"], "command_suggestions": []},
        {"classification": "human", "confidence": 0.9, "evidence_tags": [], "command_suggestions": []},
        "not-json",
        {"classification": "bot", "confidence": 0.69, "evidence_tags": ["fixed_format"], "command_suggestions": []},
    ]
    for response in responses:
        registry, cfg = _registry()

        async def call_ai(_messages, **_kwargs):
            return response

        observer = observer_module.PeerBotObserver(
            registry=registry,
            plugin_config=cfg,
            call_ai_api=call_ai,
        )

        async def run():
            assert observer.enqueue_event(_Event(), source="group_message") is True
            return await observer.flush_all()

        asyncio.run(run())
        assert registry.get_group("415442985")["bots"] == {}


def test_timeout_degrades_to_unknown_without_mutation() -> None:
    registry, cfg = _registry(_config(personification_peer_bot_detector_timeout_seconds=0.01))

    async def call_ai(_messages, **_kwargs):
        await asyncio.sleep(0.05)
        return {}

    observer = observer_module.PeerBotObserver(
        registry=registry,
        plugin_config=cfg,
        call_ai_api=call_ai,
    )

    async def run():
        assert observer.enqueue_event(_Event(), source="group_message") is True
        return await observer.flush_all()

    result = asyncio.run(run())[0]
    assert result["status"] == "unknown"
    assert registry.get_group("415442985")["bots"] == {}


def test_registry_write_failure_isolated_as_unknown() -> None:
    registry, cfg = _registry()

    async def call_ai(_messages, **_kwargs):
        return {
            "classification": "bot",
            "confidence": 0.9,
            "evidence_tags": ["fixed_format"],
            "command_suggestions": [],
        }

    def fail_write(*_args, **_kwargs):
        raise OSError("disk unavailable")

    registry.observe_candidate_bot = fail_write
    observer = observer_module.PeerBotObserver(
        registry=registry,
        plugin_config=cfg,
        call_ai_api=call_ai,
    )

    async def run():
        assert observer.enqueue_event(_Event(), source="group_message") is True
        return await observer.flush_all()

    result = asyncio.run(run())[0]
    assert result == {"status": "unknown", "diagnostic": "peer_bot_candidate_store_failed"}


def test_approved_bot_reply_protocol_episode_can_auto_approve_exact_match() -> None:
    registry, cfg = _registry()
    registry.set_settings(
        "415442985",
        enabled=True,
        auto_learn_approved_commands=True,
    )
    registry.set_bot_status("415442985", user_id="10001", action="approve", nickname="Usagi")

    async def call_ai(messages, **_kwargs):
        assert "[不可信群聊数据]" in messages[1]["content"]
        assert "[不可信外部 Bot 数据]" in messages[1]["content"]
        return {
            "target_bot_id": "10001",
            "confidence": 0.95,
            "command_suggestions": [
                {
                    "full_template": ".mc say {message}",
                    "command_entry": ".mc",
                    "subcommands": ["say"],
                    "argument_template": "{message}",
                    "description": "向 Minecraft 服务器里的玩家发送消息",
                    "parameter_schema": {
                        "type": "object",
                        "properties": {
                            "message": {"type": "string", "description": "发言内容"}
                        },
                        "required": ["message"],
                        "additionalProperties": False,
                    },
                    "risk_level": "write",
                }
            ],
        }

    observer = observer_module.PeerBotObserver(
        registry=registry,
        plugin_config=cfg,
        call_ai_api=call_ai,
    )

    async def run():
        assert observer.enqueue_protocol_event(_ProtocolRequestEvent()) is True
        classification = SimpleNamespace(bot_status="approved", source_kind="peer_bot_reply")
        assert observer.enqueue_protocol_event(
            _ProtocolBotReplyEvent(), classification=classification
        ) is True
        await asyncio.sleep(0.01)

    asyncio.run(run())
    commands = registry.get_group("415442985")["commands"]
    assert len(commands) == 1
    command = next(iter(commands.values()))
    assert command["status"] == "approved"
    assert command["auto_approved"] is True
    assert command["evidence_count"] == 1
    assert observer.snapshot_stats()["protocol_auto_approved"] == 1
