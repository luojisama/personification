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
