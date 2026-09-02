from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest

from ._loader import load_personification_module


registry_module = load_personification_module("plugin.personification.core.peer_bot_registry")
awareness_module = load_personification_module("plugin.personification.core.peer_awareness")


class _Store:
    def __init__(self) -> None:
        self.namespaces: dict[str, object] = {}

    def load_sync(self, name: str):
        return copy.deepcopy(self.namespaces.get(name, {}))

    def mutate_sync(self, name: str, mutator):
        current = copy.deepcopy(self.namespaces.get(name, {}))
        updated = mutator(current)
        self.namespaces[name] = copy.deepcopy(updated)
        return copy.deepcopy(updated)


def _registry(*, max_chars: int = 500):
    return registry_module.PeerBotRegistry(
        store=_Store(),
        plugin_config=SimpleNamespace(personification_peer_bot_max_command_chars=max_chars),
    )


def test_registry_defaults_are_closed_and_depth_is_fixed() -> None:
    registry = _registry()
    group = registry.get_group("415442985")
    assert group["enabled"] is False
    assert group["bots"] == {}
    assert group["commands"] == {}
    assert group["policies"] == {
        "max_calls_per_turn": 1,
        "cooldown_seconds": 10.0,
        "pending_ttl_seconds": 30.0,
        "max_chain_depth": 1,
        "auto_learn_approved_commands": False,
    }
    with pytest.raises(registry_module.PeerBotRegistryError, match="max_calls_per_turn"):
        registry.set_settings("415442985", max_calls_per_turn=2)
    with pytest.raises(registry_module.PeerBotRegistryError, match="max_chain_depth"):
        registry.set_settings("415442985", max_chain_depth=2)


def test_candidate_never_overwrites_manual_approval_or_rejection() -> None:
    registry = _registry()
    first = registry.observe_candidate_bot(
        "g1",
        user_id="10001",
        nickname="Usagi",
        confidence=0.82,
        evidence_tags=["fixed_format", "not_allowed"],
    )
    assert first["status"] == "candidate"
    assert first["evidence_tags"] == ["fixed_format"]

    approved = registry.set_bot_status("g1", user_id="10001", action="approve")
    assert approved and approved["status"] == "approved"
    assert approved["manual_override"] is True

    observed_again = registry.observe_candidate_bot(
        "g1",
        user_id="10001",
        nickname="new snapshot",
        confidence=0.99,
        evidence_tags=["periodic_activity"],
    )
    assert observed_again["status"] == "approved"
    assert observed_again["source"] == "manual"
    assert observed_again["evidence_tags"] == ["fixed_format", "periodic_activity"]

    rejected = registry.set_bot_status("g1", user_id="20002", action="reject")
    assert rejected and rejected["status"] == "rejected"
    assert registry.observe_candidate_bot(
        "g1", user_id="20002", confidence=1.0, evidence_tags=["onebot_role"]
    )["status"] == "rejected"


def test_full_command_templates_are_structural_and_strict() -> None:
    validated = registry_module.validate_command_template(
        ".mc say {message}",
        parameter_schema={
            "type": "object",
            "properties": {"message": {"type": "string", "maxLength": 120}},
            "required": ["message"],
            "additionalProperties": False,
        },
    )
    assert validated.command_head == ".mc say"
    assert validated.placeholders == ("message",)
    assert validated.parameter_schema["properties"]["message"]["maxLength"] == 120
    assert registry_module.validate_command_template("/抽卡").placeholders == ()


def test_v2_structured_command_fields_generate_full_template() -> None:
    validated = registry_module.validate_command_template(
        full_template=None,
        command_entry=".mc",
        subcommands=["say"],
        argument_template="{message}",
        description="向 Minecraft 服务器里的玩家发送消息",
        parameter_schema={
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "要发送到服务器的消息",
                    "maxLength": 120,
                }
            },
            "required": ["message"],
            "additionalProperties": False,
        },
    )
    assert validated.full_template == ".mc say {message}"
    assert validated.command_entry == ".mc"
    assert validated.subcommands == ("say",)
    assert validated.argument_template == "{message}"
    assert validated.description == "向 Minecraft 服务器里的玩家发送消息"
    assert validated.legacy_mode is False
    assert (
        validated.parameter_schema["properties"]["message"]["description"]
        == "要发送到服务器的消息"
    )

    no_arguments = registry_module.validate_command_template(
        full_template=None,
        command_entry="/抽卡",
        subcommands=[],
        argument_template="",
    )
    assert no_arguments.full_template == "/抽卡"
    assert no_arguments.placeholders == ()


def test_v2_rejects_conflicting_legacy_and_structured_templates() -> None:
    with pytest.raises(
        registry_module.PeerBotRegistryError,
        match="command_template_structural_mismatch",
    ):
        registry_module.validate_command_template(
            ".mc say {message}",
            command_entry=".mc",
            subcommands=["tell"],
            argument_template="{message}",
        )

    with pytest.raises(registry_module.PeerBotRegistryError, match="subcommands_limit"):
        registry_module.validate_command_template(
            full_template=None,
            command_entry=".mc",
            subcommands=["one", "two", "three"],
        )


def test_v1_registry_migrates_without_changing_full_template() -> None:
    store = _Store()
    store.namespaces[registry_module.PEER_BOT_REGISTRY_NAMESPACE] = {
        "g1": {
            "schema_version": 1,
            "enabled": True,
            "bots": {
                "10001": {
                    "user_id": "10001",
                    "status": "approved",
                    "command_ids": ["legacy-command"],
                }
            },
            "commands": {
                "legacy-command": {
                    "command_id": "legacy-command",
                    "target_bot_id": "10001",
                    "full_template": ".mc say {message}",
                    "command_head": ".mc say",
                    "parameter_schema": {
                        "type": "object",
                        "properties": {"message": {"type": "string"}},
                        "required": ["message"],
                        "additionalProperties": False,
                    },
                    "risk_level": "write",
                    "status": "approved",
                    "source": "manual",
                    "manual_override": True,
                }
            },
        }
    }
    registry = registry_module.PeerBotRegistry(
        store=store,
        plugin_config=SimpleNamespace(personification_peer_bot_max_command_chars=500),
    )
    group = registry.get_group("g1")
    command = group["commands"]["legacy-command"]
    assert group["schema_version"] == 2
    assert command["full_template"] == ".mc say {message}"
    assert command["command_entry"] == ".mc"
    assert command["subcommands"] == ["say"]
    assert command["argument_template"] == "{message}"
    assert command["legacy_mode"] is False
    assert command["auto_approved"] is False

    invalid_templates = [
        ".mc say {message}\n/admin stop",
        "{message}",
        ".mc {message!r}",
        ".mc {message:>10}",
        ".mc {message} {message}",
        ".mc {user.name}",
    ]
    for template in invalid_templates:
        with pytest.raises(registry_module.PeerBotRegistryError):
            registry_module.validate_command_template(template)

    with pytest.raises(registry_module.PeerBotRegistryError, match="mismatch"):
        registry_module.validate_command_template(
            ".mc say {message}",
            parameter_schema={
                "type": "object",
                "properties": {"undeclared": {"type": "string"}},
                "required": [],
                "additionalProperties": False,
            },
        )


def test_commands_remain_candidates_until_two_admin_approvals() -> None:
    registry = _registry()
    registry.observe_candidate_bot("g1", user_id="10001", confidence=0.9)
    command = registry.upsert_command(
        "g1",
        target_bot_id="10001",
        full_template=".mc say {message}",
        parameter_schema=None,
        risk_level="write",
    )
    assert command["status"] == "candidate"
    assert registry.get_approved_command("g1", "10001", command["command_id"]) is None

    registry.set_bot_status("g1", user_id="10001", action="approve")
    assert registry.get_approved_command("g1", "10001", command["command_id"]) is None
    registry.set_command_status(
        "g1",
        target_bot_id="10001",
        command_id=command["command_id"],
        action="approve",
    )
    approved = registry.get_approved_command("g1", "10001", command["command_id"])
    assert approved and approved["risk_level"] == "write"


def test_llm_observation_cannot_modify_a_manually_approved_command() -> None:
    registry = _registry()
    registry.set_bot_status("g1", user_id="10001", action="approve")
    command = registry.upsert_command(
        "g1",
        target_bot_id="10001",
        full_template=".mc say {message}",
        parameter_schema={
            "type": "object",
            "properties": {"message": {"type": "string", "maxLength": 40}},
            "required": ["message"],
            "additionalProperties": False,
        },
        risk_level="write",
        status="approved",
        source="manual",
        manual_override=True,
    )
    observed = registry.upsert_command(
        "g1",
        target_bot_id="10001",
        full_template=".mc say {message}",
        parameter_schema={
            "type": "object",
            "properties": {"message": {"type": "string", "maxLength": 500}},
            "required": [],
            "additionalProperties": False,
        },
        risk_level="read",
        status="candidate",
        source="llm_observation",
        manual_override=False,
    )
    assert observed == command
    assert observed["parameter_schema"]["properties"]["message"]["maxLength"] == 40
    assert observed["parameter_schema"]["required"] == ["message"]
    assert observed["risk_level"] == "write"
    assert observed["status"] == "approved"


def test_protocol_learning_exact_and_fifo_thresholds_are_atomic() -> None:
    registry = _registry()
    registry.set_settings("g1", enabled=True, auto_learn_approved_commands=True)
    registry.set_bot_status("g1", user_id="536596616", action="approve", nickname="Usagi")

    exact = registry.observe_protocol_command(
        "g1",
        target_bot_id="536596616",
        full_template=".mc say {message}",
        description="向 Minecraft 服务器里的玩家发送消息",
        parameter_schema={
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "发言内容", "maxLength": 120}
            },
            "required": ["message"],
            "additionalProperties": False,
        },
        risk_level="write",
        confidence=0.90,
        correlation_kind="exact_reply",
        auto_approve_confidence=0.90,
        fifo_evidence_count=2,
    )
    assert exact["diagnostic_code"] == "peer_bot_protocol_auto_approved"
    assert exact["command"]["status"] == "approved"
    assert exact["command"]["auto_approved"] is True
    assert exact["command"]["evidence_count"] == 1

    first = registry.observe_protocol_command(
        "g1",
        target_bot_id="536596616",
        full_template="/抽卡",
        description="进行一次抽卡",
        parameter_schema=None,
        risk_level="read",
        confidence=0.92,
        correlation_kind="fifo",
        auto_approve_confidence=0.90,
        fifo_evidence_count=2,
        episode_key="fifo-episode-1",
    )
    assert first["diagnostic_code"] == "peer_bot_protocol_candidate"
    assert first["command"]["status"] == "candidate"
    assert first["command"]["evidence_count"] == 1
    duplicate_first = registry.observe_protocol_command(
        "g1",
        target_bot_id="536596616",
        full_template="/抽卡",
        description="进行一次抽卡",
        parameter_schema=None,
        risk_level="read",
        confidence=0.99,
        correlation_kind="fifo",
        auto_approve_confidence=0.90,
        fifo_evidence_count=2,
        episode_key="fifo-episode-1",
    )
    assert duplicate_first["command"]["evidence_count"] == 1
    assert duplicate_first["command"]["status"] == "candidate"
    second = registry.observe_protocol_command(
        "g1",
        target_bot_id="536596616",
        full_template="/抽卡",
        description="进行一次抽卡",
        parameter_schema=None,
        risk_level="read",
        confidence=0.92,
        correlation_kind="fifo",
        auto_approve_confidence=0.90,
        fifo_evidence_count=2,
        episode_key="fifo-episode-2",
    )
    assert second["diagnostic_code"] == "peer_bot_protocol_auto_approved"
    assert second["command"]["evidence_count"] == 2


def test_protocol_learning_never_overwrites_manual_or_auto_approves_high_risk() -> None:
    registry = _registry()
    registry.set_settings("g1", enabled=True, auto_learn_approved_commands=True)
    registry.set_bot_status("g1", user_id="10001", action="approve")
    manual = registry.upsert_command(
        "g1",
        target_bot_id="10001",
        command_entry=".mc",
        subcommands=["say"],
        argument_template="{message}",
        description="管理员定义的用途",
        parameter_schema=None,
        risk_level="write",
        status="approved",
        source="manual",
        manual_override=True,
    )
    observed = registry.observe_protocol_command(
        "g1",
        target_bot_id="10001",
        full_template=".mc say {message}",
        description="模型试图替换用途",
        parameter_schema=None,
        risk_level="read",
        confidence=1.0,
        correlation_kind="exact_reply",
    )
    assert observed["diagnostic_code"] == "peer_bot_protocol_observed"
    assert observed["command"] == manual

    conflict = registry.observe_protocol_command(
        "g1",
        target_bot_id="10001",
        full_template=".mc stop",
        description="冲突协议",
        parameter_schema=None,
        risk_level="write",
        confidence=1.0,
        correlation_kind="exact_reply",
    )
    assert conflict["diagnostic_code"] == "peer_bot_protocol_conflict"
    assert conflict["command"]["status"] == "candidate"

    dangerous = registry.observe_protocol_command(
        "g1",
        target_bot_id="10001",
        full_template="/shutdown",
        description="危险操作",
        parameter_schema=None,
        risk_level="dangerous",
        confidence=1.0,
        correlation_kind="exact_reply",
    )
    assert dangerous["diagnostic_code"] == "peer_bot_protocol_risk_blocked"
    assert dangerous["command"]["status"] == "candidate"


def test_invalid_schema_numbers_use_stable_registry_error() -> None:
    with pytest.raises(registry_module.PeerBotRegistryError, match="invalid_parameter_schema"):
        registry_module.validate_command_template(
            ".mc say {message}",
            parameter_schema={
                "type": "object",
                "properties": {"message": {"type": "string", "maxLength": "many"}},
                "required": ["message"],
                "additionalProperties": False,
            },
        )


def test_parameter_enum_preserves_scalar_types_and_rejects_mismatch() -> None:
    numeric = registry_module.validate_command_template(
        "/roll {count}",
        parameter_schema={
            "type": "object",
            "properties": {"count": {"type": "integer", "enum": [1, 2, 3]}},
            "required": ["count"],
            "additionalProperties": False,
        },
    )
    assert numeric.parameter_schema["properties"]["count"]["enum"] == [1, 2, 3]
    with pytest.raises(registry_module.PeerBotRegistryError, match="invalid_parameter_enum"):
        registry_module.validate_command_template(
            "/roll {count}",
            parameter_schema={
                "type": "object",
                "properties": {"count": {"type": "integer", "enum": ["1"]}},
                "required": ["count"],
                "additionalProperties": False,
            },
        )


def test_legacy_ids_still_silence_but_text_patterns_do_not_decide_identity() -> None:
    legacy = awareness_module.detect_other_bot(user_id="10001", extra_bot_ids=["10001"])
    assert legacy.is_other_bot is True and legacy.suggest_silence is True

    fixed_format = awareness_module.detect_other_bot(
        user_id="20002",
        text="[MC] VikiQAQ fell from a high place",
        extra_bot_ids=[],
    )
    command_style = awareness_module.detect_other_bot(
        user_id="20002",
        text="/签到成功",
        extra_bot_ids=[],
    )
    assert fixed_format.is_other_bot is False
    assert command_style.is_other_bot is False
