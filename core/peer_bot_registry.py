from __future__ import annotations

import copy
import hashlib
import math
import re
import string
import time
from dataclasses import dataclass
from typing import Any, Iterable, Literal, Mapping

from .data_store import get_data_store


PEER_BOT_REGISTRY_NAMESPACE = "peer_bot_registry"
PEER_BOT_REGISTRY_VERSION = 2

PeerBotStatus = Literal["candidate", "approved", "rejected"]
PeerBotSource = Literal["llm_observation", "onebot_metadata", "manual", "auto_learned"]
PeerBotRisk = Literal["read", "write", "admin", "dangerous"]

BOT_STATUSES = frozenset({"candidate", "approved", "rejected"})
BOT_SOURCES = frozenset({"llm_observation", "onebot_metadata", "manual", "auto_learned"})
COMMAND_RISKS = frozenset({"read", "write", "admin", "dangerous"})
EVIDENCE_TAGS = frozenset(
    {
        "fixed_format",
        "periodic_activity",
        "explicit_command_reply",
        "onebot_role",
        "automation_metadata",
        "insufficient_context",
    }
)

DEFAULT_GROUP_POLICY: dict[str, Any] = {
    "max_calls_per_turn": 1,
    "cooldown_seconds": 10.0,
    "pending_ttl_seconds": 30.0,
    "max_chain_depth": 1,
    "auto_learn_approved_commands": False,
}

_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")
_FIELD_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_SUPPORTED_PARAMETER_TYPES = frozenset({"string", "integer", "number", "boolean"})


class PeerBotRegistryError(ValueError):
    """A stable, user-safe registry validation error."""


@dataclass(frozen=True)
class ValidatedCommandTemplate:
    full_template: str
    command_head: str
    command_entry: str
    subcommands: tuple[str, ...]
    argument_template: str
    placeholders: tuple[str, ...]
    parameter_schema: dict[str, Any]
    description: str = ""
    legacy_mode: bool = False


def _now() -> float:
    return round(time.time(), 3)


def _finite_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return number if math.isfinite(number) else default


def _bounded_text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split()).strip()[: max(0, int(limit))]


def _safe_id(value: Any, *, field: str) -> str:
    item = str(value or "").strip()
    if not _SAFE_ID_RE.fullmatch(item):
        raise PeerBotRegistryError(f"invalid_{field}")
    return item


def _normalize_status(value: Any, *, default: PeerBotStatus = "candidate") -> PeerBotStatus:
    text = str(value or "").strip().lower()
    return text if text in BOT_STATUSES else default  # type: ignore[return-value]


def _normalize_source(value: Any, *, default: PeerBotSource = "llm_observation") -> PeerBotSource:
    text = str(value or "").strip().lower()
    return text if text in BOT_SOURCES else default  # type: ignore[return-value]


def _normalize_risk(value: Any, *, default: PeerBotRisk = "read") -> PeerBotRisk:
    text = str(value or "").strip().lower()
    if text not in COMMAND_RISKS:
        raise PeerBotRegistryError("invalid_risk_level")
    return text if text in COMMAND_RISKS else default  # type: ignore[return-value]


def _normalize_evidence_tags(values: Any) -> list[str]:
    if not isinstance(values, (list, tuple, set, frozenset)):
        return []
    tags: list[str] = []
    for raw in values:
        tag = str(raw or "").strip().lower()
        if tag in EVIDENCE_TAGS and tag not in tags:
            tags.append(tag)
        if len(tags) >= 4:
            break
    return tags


def _default_group_document() -> dict[str, Any]:
    return {
        "schema_version": PEER_BOT_REGISTRY_VERSION,
        "enabled": False,
        "bots": {},
        "commands": {},
        "protocol_evidence": {},
        "policies": copy.deepcopy(DEFAULT_GROUP_POLICY),
        "updated_at": 0.0,
    }


def _normalize_group_document(raw: Any) -> dict[str, Any]:
    doc = copy.deepcopy(raw) if isinstance(raw, dict) else {}
    normalized = _default_group_document()
    normalized["enabled"] = bool(doc.get("enabled", False))
    normalized["bots"] = copy.deepcopy(doc.get("bots")) if isinstance(doc.get("bots"), dict) else {}
    raw_commands = copy.deepcopy(doc.get("commands")) if isinstance(doc.get("commands"), dict) else {}
    commands: dict[str, Any] = {}
    for command_id, raw_command in raw_commands.items():
        if not isinstance(raw_command, dict):
            continue
        command = copy.deepcopy(raw_command)
        full_template = str(command.get("full_template", "") or "").strip()
        if not all(key in command for key in ("command_entry", "subcommands", "argument_template")):
            entry, subcommands, argument_template, legacy_mode = _decompose_full_template(full_template)
            command.setdefault("command_entry", entry)
            command.setdefault("subcommands", subcommands)
            command.setdefault("argument_template", argument_template)
            command.setdefault("legacy_mode", legacy_mode)
        command.setdefault("description", "")
        command.setdefault("auto_approved", False)
        command.setdefault("evidence_count", 0)
        command.setdefault(
            "protocol_source",
            "manual" if bool(command.get("manual_override", False)) else str(command.get("source", "llm_observation") or "llm_observation"),
        )
        commands[str(command_id)] = command
    normalized["commands"] = commands
    raw_evidence = doc.get("protocol_evidence") if isinstance(doc.get("protocol_evidence"), dict) else {}
    normalized["protocol_evidence"] = {
        str(command_id): [
            str(key)[:64]
            for key in list(keys)[-32:]
            if isinstance(key, str) and key
        ]
        for command_id, keys in raw_evidence.items()
        if isinstance(command_id, str) and isinstance(keys, list)
    }
    policies = doc.get("policies") if isinstance(doc.get("policies"), dict) else {}
    normalized["policies"] = {
        "max_calls_per_turn": 1,
        "cooldown_seconds": max(0.0, min(3600.0, _finite_float(policies.get("cooldown_seconds"), 10.0))),
        "pending_ttl_seconds": max(1.0, min(600.0, _finite_float(policies.get("pending_ttl_seconds"), 30.0))),
        "max_chain_depth": 1,
        "auto_learn_approved_commands": bool(policies.get("auto_learn_approved_commands", False)),
    }
    normalized["updated_at"] = max(0.0, _finite_float(doc.get("updated_at"), 0.0))
    return normalized


def _normalize_parameter_schema(schema: Any, placeholders: tuple[str, ...]) -> dict[str, Any]:
    expected = set(placeholders)
    if schema in (None, {}, ""):
        properties = {name: {"type": "string", "description": ""} for name in placeholders}
        return {
            "type": "object",
            "properties": properties,
            "required": list(placeholders),
            "additionalProperties": False,
        }
    if not isinstance(schema, Mapping):
        raise PeerBotRegistryError("invalid_parameter_schema")
    if str(schema.get("type", "object") or "object") != "object":
        raise PeerBotRegistryError("invalid_parameter_schema")
    properties_raw = schema.get("properties", {})
    if not isinstance(properties_raw, Mapping):
        raise PeerBotRegistryError("invalid_parameter_schema")
    actual = {str(key) for key in properties_raw}
    if actual != expected:
        raise PeerBotRegistryError("parameter_schema_mismatch")
    properties: dict[str, dict[str, Any]] = {}
    for name in placeholders:
        raw_property = properties_raw.get(name)
        if not isinstance(raw_property, Mapping):
            raise PeerBotRegistryError("invalid_parameter_schema")
        parameter_type = str(raw_property.get("type", "string") or "string").strip().lower()
        if parameter_type not in _SUPPORTED_PARAMETER_TYPES:
            raise PeerBotRegistryError("unsupported_parameter_type")
        item: dict[str, Any] = {"type": parameter_type}
        if "description" in raw_property:
            item["description"] = _bounded_text(raw_property.get("description"), 160)
        if "maxLength" in raw_property:
            try:
                max_length = int(raw_property.get("maxLength") or 1)
            except (TypeError, ValueError, OverflowError) as exc:
                raise PeerBotRegistryError("invalid_parameter_schema") from exc
            item["maxLength"] = max(1, min(500, max_length))
        if "minimum" in raw_property:
            item["minimum"] = _finite_float(raw_property.get("minimum"), 0.0)
        if "maximum" in raw_property:
            item["maximum"] = _finite_float(raw_property.get("maximum"), 0.0)
        if "enum" in raw_property:
            enum_raw = raw_property.get("enum")
            if not isinstance(enum_raw, (list, tuple)) or not enum_raw or len(enum_raw) > 30:
                raise PeerBotRegistryError("invalid_parameter_enum")
            enum_values: list[Any] = []
            for value in enum_raw:
                if parameter_type == "string":
                    if not isinstance(value, str):
                        raise PeerBotRegistryError("invalid_parameter_enum")
                    normalized_value: Any = _bounded_text(value, 100)
                elif parameter_type == "integer":
                    if isinstance(value, bool) or not isinstance(value, int):
                        raise PeerBotRegistryError("invalid_parameter_enum")
                    normalized_value = value
                elif parameter_type == "number":
                    if (
                        isinstance(value, bool)
                        or not isinstance(value, (int, float))
                        or not math.isfinite(float(value))
                    ):
                        raise PeerBotRegistryError("invalid_parameter_enum")
                    normalized_value = value
                else:
                    if not isinstance(value, bool):
                        raise PeerBotRegistryError("invalid_parameter_enum")
                    normalized_value = value
                if normalized_value not in enum_values:
                    enum_values.append(normalized_value)
            item["enum"] = enum_values
        properties[name] = item
    required_raw = schema.get("required", list(placeholders))
    if not isinstance(required_raw, (list, tuple)):
        raise PeerBotRegistryError("invalid_parameter_schema")
    required = [str(name) for name in required_raw]
    if set(required) - expected or len(required) != len(set(required)):
        raise PeerBotRegistryError("invalid_parameter_schema")
    if bool(schema.get("additionalProperties", False)):
        raise PeerBotRegistryError("additional_parameters_forbidden")
    return {
        "type": "object",
        "properties": properties,
        "required": [name for name in placeholders if name in required],
        "additionalProperties": False,
    }


def _decompose_full_template(template: str) -> tuple[str, list[str], str, bool]:
    """Best-effort v1 display migration without changing the executable template."""

    normalized = str(template or "").strip()
    parts = normalized.split()
    if not parts:
        return "", [], "", True
    first_placeholder = next((index for index, part in enumerate(parts) if "{" in part or "}" in part), -1)
    if first_placeholder == 0:
        return parts[0], [], "", True
    head_parts = parts if first_placeholder < 0 else parts[:first_placeholder]
    argument_parts = [] if first_placeholder < 0 else parts[first_placeholder:]
    if not head_parts:
        return parts[0], [], "", True
    entry = head_parts[0]
    subcommands = head_parts[1:]
    legacy_mode = len(subcommands) > 2
    if legacy_mode:
        subcommands = subcommands[:2]
    argument_template = " ".join(argument_parts)
    rebuilt = " ".join([entry, *subcommands, argument_template]).strip()
    if rebuilt != normalized:
        legacy_mode = True
    return entry, subcommands, argument_template, legacy_mode


def _strict_command_segment(value: Any, *, field: str, limit: int) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise PeerBotRegistryError(f"missing_{field}")
    if len(raw) > limit or any(character.isspace() for character in raw):
        raise PeerBotRegistryError(f"invalid_{field}")
    if _CONTROL_RE.search(raw) or "{" in raw or "}" in raw:
        raise PeerBotRegistryError(f"invalid_{field}")
    return raw


def compose_full_template(
    command_entry: Any,
    subcommands: Any = None,
    argument_template: Any = "",
) -> str:
    entry = _strict_command_segment(command_entry, field="command_entry", limit=80)
    if subcommands is None:
        raw_subcommands: list[Any] = []
    elif isinstance(subcommands, (list, tuple)):
        raw_subcommands = list(subcommands)
    else:
        raise PeerBotRegistryError("invalid_subcommands")
    if len(raw_subcommands) > 2:
        raise PeerBotRegistryError("subcommands_limit_exceeded")
    normalized_subcommands = [
        _strict_command_segment(value, field="subcommand", limit=64)
        for value in raw_subcommands
    ]
    raw_argument = str(argument_template or "").strip()
    if "\n" in raw_argument or "\r" in raw_argument or _CONTROL_RE.search(raw_argument):
        raise PeerBotRegistryError("invalid_argument_template")
    normalized_argument = " ".join(raw_argument.split())
    return " ".join([entry, *normalized_subcommands, normalized_argument]).strip()


def validate_command_template(
    full_template: Any = None,
    *,
    command_entry: Any = None,
    subcommands: Any = None,
    argument_template: Any = None,
    description: Any = "",
    parameter_schema: Any = None,
    max_chars: int = 500,
) -> ValidatedCommandTemplate:
    has_structured_fields = any(
        value is not None for value in (command_entry, subcommands, argument_template)
    )
    supplied_template = str(full_template or "").strip()
    if has_structured_fields:
        template = compose_full_template(command_entry, subcommands, argument_template)
        if supplied_template and supplied_template != template:
            raise PeerBotRegistryError("command_template_structural_mismatch")
        normalized_entry = str(command_entry or "").strip()
        normalized_subcommands = tuple(str(item).strip() for item in (subcommands or []))
        normalized_argument_template = " ".join(str(argument_template or "").split())
        legacy_mode = False
    else:
        template = supplied_template
        entry, decomposed_subcommands, decomposed_argument, legacy_mode = _decompose_full_template(template)
        normalized_entry = entry
        normalized_subcommands = tuple(decomposed_subcommands)
        normalized_argument_template = decomposed_argument
    max_length = max(1, min(4000, int(max_chars or 500)))
    if not template:
        raise PeerBotRegistryError("empty_command_template")
    if len(template) > max_length:
        raise PeerBotRegistryError("command_template_too_long")
    if "\n" in template or "\r" in template or _CONTROL_RE.search(template):
        raise PeerBotRegistryError("command_template_control_character")

    formatter = string.Formatter()
    placeholders: list[str] = []
    literal_prefix = ""
    try:
        parsed = list(formatter.parse(template))
    except ValueError as exc:
        raise PeerBotRegistryError("invalid_command_template") from exc
    for index, (literal, field_name, format_spec, conversion) in enumerate(parsed):
        if index == 0:
            literal_prefix = literal
        if field_name is None:
            continue
        if not _FIELD_RE.fullmatch(field_name):
            raise PeerBotRegistryError("invalid_template_parameter")
        if format_spec or conversion:
            raise PeerBotRegistryError("template_formatting_forbidden")
        if field_name in placeholders:
            raise PeerBotRegistryError("duplicate_template_parameter")
        placeholders.append(field_name)

    command_head = " ".join(literal_prefix.split()).strip()
    if not command_head:
        raise PeerBotRegistryError("missing_command_head")
    normalized_schema = _normalize_parameter_schema(parameter_schema, tuple(placeholders))
    return ValidatedCommandTemplate(
        full_template=template,
        command_head=command_head[:160],
        command_entry=normalized_entry,
        subcommands=normalized_subcommands,
        argument_template=normalized_argument_template,
        placeholders=tuple(placeholders),
        parameter_schema=normalized_schema,
        description=_bounded_text(description, 240),
        legacy_mode=legacy_mode,
    )


def build_command_id(target_bot_id: str, template: str) -> str:
    digest = hashlib.sha256(f"{target_bot_id}\x1f{template}".encode("utf-8")).hexdigest()[:20]
    return f"cmd_{digest}"


class PeerBotRegistry:
    def __init__(self, *, store: Any = None, plugin_config: Any = None, logger: Any = None) -> None:
        self.store = store or get_data_store()
        self.plugin_config = plugin_config
        self.logger = logger

    def _max_command_chars(self) -> int:
        return max(
            32,
            min(
                4000,
                int(getattr(self.plugin_config, "personification_peer_bot_max_command_chars", 500) or 500),
            ),
        )

    @property
    def max_command_chars(self) -> int:
        """Return the effective safe command limit for management clients."""
        return self._max_command_chars()

    def _mutate_group(self, group_id: Any, mutator: Any) -> dict[str, Any]:
        gid = _safe_id(group_id, field="group_id")
        result: dict[str, Any] = {}

        def _mutate(root: Any) -> dict[str, Any]:
            nonlocal result
            payload = copy.deepcopy(root) if isinstance(root, dict) else {}
            group = _normalize_group_document(payload.get(gid))
            updated = mutator(group)
            if isinstance(updated, dict):
                group = updated
            group["schema_version"] = PEER_BOT_REGISTRY_VERSION
            group["updated_at"] = _now()
            payload[gid] = group
            result = copy.deepcopy(group)
            return payload

        self.store.mutate_sync(PEER_BOT_REGISTRY_NAMESPACE, _mutate)
        return result

    def get_group(self, group_id: Any) -> dict[str, Any]:
        gid = _safe_id(group_id, field="group_id")
        root = self.store.load_sync(PEER_BOT_REGISTRY_NAMESPACE)
        group = root.get(gid) if isinstance(root, dict) else None
        return _normalize_group_document(group)

    def set_settings(
        self,
        group_id: Any,
        *,
        enabled: bool | None = None,
        max_calls_per_turn: int | None = None,
        cooldown_seconds: float | None = None,
        pending_ttl_seconds: float | None = None,
        max_chain_depth: int | None = None,
        auto_learn_approved_commands: bool | None = None,
    ) -> dict[str, Any]:
        if max_calls_per_turn not in (None, 1):
            raise PeerBotRegistryError("max_calls_per_turn_must_be_one")
        if max_chain_depth not in (None, 1):
            raise PeerBotRegistryError("max_chain_depth_must_be_one")

        def _apply(group: dict[str, Any]) -> dict[str, Any]:
            if enabled is not None:
                group["enabled"] = bool(enabled)
            policies = group["policies"]
            policies["max_calls_per_turn"] = 1
            policies["max_chain_depth"] = 1
            if auto_learn_approved_commands is not None:
                policies["auto_learn_approved_commands"] = bool(auto_learn_approved_commands)
            if cooldown_seconds is not None:
                value = _finite_float(cooldown_seconds, -1.0)
                if value < 0 or value > 3600:
                    raise PeerBotRegistryError("cooldown_seconds_out_of_range")
                policies["cooldown_seconds"] = value
            if pending_ttl_seconds is not None:
                value = _finite_float(pending_ttl_seconds, -1.0)
                if value < 1 or value > 600:
                    raise PeerBotRegistryError("pending_ttl_seconds_out_of_range")
                policies["pending_ttl_seconds"] = value
            return group

        return self._mutate_group(group_id, _apply)

    def observe_candidate_bot(
        self,
        group_id: Any,
        *,
        user_id: Any,
        nickname: Any = "",
        confidence: Any = 0.0,
        source: PeerBotSource = "llm_observation",
        evidence_tags: Iterable[str] = (),
    ) -> dict[str, Any]:
        uid = _safe_id(user_id, field="user_id")
        normalized_source = _normalize_source(source)
        normalized_confidence = round(max(0.0, min(1.0, _finite_float(confidence))), 3)
        tags = _normalize_evidence_tags(list(evidence_tags))
        result: dict[str, Any] = {}

        def _apply(group: dict[str, Any]) -> dict[str, Any]:
            nonlocal result
            bots = group["bots"]
            existing = bots.get(uid) if isinstance(bots.get(uid), dict) else {}
            existing_status = _normalize_status(existing.get("status"))
            manual_override = bool(existing.get("manual_override", False))
            status: PeerBotStatus = existing_status if manual_override or existing_status != "candidate" else "candidate"
            previous_tags = _normalize_evidence_tags(existing.get("evidence_tags", []))
            merged_tags = _normalize_evidence_tags([*previous_tags, *tags])
            first_seen = max(0.0, _finite_float(existing.get("first_seen_at"), 0.0)) or _now()
            bot = {
                "user_id": uid,
                "nickname": _bounded_text(nickname, 80) or _bounded_text(existing.get("nickname"), 80),
                "status": status,
                "confidence": max(normalized_confidence, _finite_float(existing.get("confidence"), 0.0)),
                "source": existing.get("source") if manual_override else normalized_source,
                "manual_override": manual_override,
                "evidence_tags": merged_tags,
                "command_ids": [
                    command_id
                    for command_id in existing.get("command_ids", [])
                    if isinstance(command_id, str) and command_id in group["commands"]
                ],
                "first_seen_at": first_seen,
                "last_seen_at": _now(),
                "updated_at": _now(),
            }
            bots[uid] = bot
            result = copy.deepcopy(bot)
            return group

        self._mutate_group(group_id, _apply)
        return result

    def set_bot_status(
        self,
        group_id: Any,
        *,
        user_id: Any,
        action: Literal["approve", "reject", "clear"],
        nickname: Any = "",
    ) -> dict[str, Any] | None:
        uid = _safe_id(user_id, field="user_id")
        if action not in {"approve", "reject", "clear"}:
            raise PeerBotRegistryError("invalid_bot_action")
        result: dict[str, Any] | None = None

        def _apply(group: dict[str, Any]) -> dict[str, Any]:
            nonlocal result
            bots = group["bots"]
            existing = bots.get(uid) if isinstance(bots.get(uid), dict) else {}
            if action == "clear":
                if not existing:
                    result = None
                    return group
                existing["manual_override"] = False
                existing["status"] = "candidate"
                existing["source"] = "llm_observation"
                existing["updated_at"] = _now()
                bots[uid] = existing
                result = copy.deepcopy(existing)
                return group
            bot = {
                "user_id": uid,
                "nickname": _bounded_text(nickname, 80) or _bounded_text(existing.get("nickname"), 80),
                "status": "approved" if action == "approve" else "rejected",
                "confidence": max(0.0, min(1.0, _finite_float(existing.get("confidence"), 1.0))),
                "source": "manual",
                "manual_override": True,
                "evidence_tags": _normalize_evidence_tags(existing.get("evidence_tags", [])),
                "command_ids": list(existing.get("command_ids", [])) if isinstance(existing.get("command_ids"), list) else [],
                "first_seen_at": max(0.0, _finite_float(existing.get("first_seen_at"), 0.0)) or _now(),
                "last_seen_at": max(0.0, _finite_float(existing.get("last_seen_at"), 0.0)),
                "updated_at": _now(),
            }
            bots[uid] = bot
            result = copy.deepcopy(bot)
            return group

        self._mutate_group(group_id, _apply)
        return result

    def upsert_command(
        self,
        group_id: Any,
        *,
        target_bot_id: Any,
        full_template: Any = None,
        command_entry: Any = None,
        subcommands: Any = None,
        argument_template: Any = None,
        description: Any = "",
        parameter_schema: Any = None,
        risk_level: Any = "read",
        status: Any = "candidate",
        source: Any = "llm_observation",
        command_id: Any = "",
        manual_override: bool = False,
        auto_approved: bool = False,
        evidence_count: int = 0,
        protocol_source: Any = "",
    ) -> dict[str, Any]:
        uid = _safe_id(target_bot_id, field="user_id")
        validated = validate_command_template(
            full_template,
            command_entry=command_entry,
            subcommands=subcommands,
            argument_template=argument_template,
            description=description,
            parameter_schema=parameter_schema,
            max_chars=self._max_command_chars(),
        )
        cid = _safe_id(command_id, field="command_id") if command_id else build_command_id(uid, validated.full_template)
        normalized_status = _normalize_status(status)
        normalized_source = _normalize_source(source)
        normalized_risk = _normalize_risk(risk_level)
        result: dict[str, Any] = {}

        def _apply(group: dict[str, Any]) -> dict[str, Any]:
            nonlocal result
            bots = group["bots"]
            if uid not in bots:
                bots[uid] = {
                    "user_id": uid,
                    "nickname": "",
                    "status": "candidate",
                    "confidence": 0.0,
                    "source": normalized_source,
                    "manual_override": False,
                    "evidence_tags": [],
                    "command_ids": [],
                    "first_seen_at": _now(),
                    "last_seen_at": 0.0,
                    "updated_at": _now(),
                }
            existing = group["commands"].get(cid)
            existing = existing if isinstance(existing, dict) else {}
            preserve_manual = bool(existing.get("manual_override", False)) and not manual_override
            if preserve_manual:
                command_ids = bots[uid].setdefault("command_ids", [])
                if cid not in command_ids:
                    command_ids.append(cid)
                result = copy.deepcopy(existing)
                return group
            command = {
                "command_id": cid,
                "target_bot_id": uid,
                "full_template": validated.full_template,
                "command_head": validated.command_head,
                "command_entry": validated.command_entry,
                "subcommands": list(validated.subcommands),
                "argument_template": validated.argument_template,
                "description": validated.description,
                "legacy_mode": validated.legacy_mode,
                "parameter_schema": validated.parameter_schema,
                "risk_level": normalized_risk,
                "status": normalized_status,
                "source": normalized_source,
                "manual_override": bool(existing.get("manual_override", False) or manual_override),
                "auto_approved": bool(auto_approved),
                "evidence_count": max(0, min(1000, int(evidence_count or 0))),
                "protocol_source": _bounded_text(
                    protocol_source or ("manual" if manual_override else normalized_source),
                    40,
                ),
                "version": max(1, int(existing.get("version", 0) or 0) + 1),
                "updated_at": _now(),
            }
            group["commands"][cid] = command
            command_ids = bots[uid].setdefault("command_ids", [])
            if cid not in command_ids:
                command_ids.append(cid)
            bots[uid]["updated_at"] = _now()
            result = copy.deepcopy(command)
            return group

        self._mutate_group(group_id, _apply)
        return result

    def observe_protocol_command(
        self,
        group_id: Any,
        *,
        target_bot_id: Any,
        full_template: Any = None,
        command_entry: Any = None,
        subcommands: Any = None,
        argument_template: Any = None,
        description: Any = "",
        parameter_schema: Any = None,
        risk_level: Any = "read",
        confidence: Any = 0.0,
        correlation_kind: Literal["exact_reply", "fifo"] = "fifo",
        auto_approve_confidence: float = 0.90,
        fifo_evidence_count: int = 2,
        episode_key: Any = "",
    ) -> dict[str, Any]:
        """Atomically record one bounded protocol episode without replacing admin authority."""

        uid = _safe_id(target_bot_id, field="user_id")
        if correlation_kind not in {"exact_reply", "fifo"}:
            raise PeerBotRegistryError("invalid_protocol_correlation")
        validated = validate_command_template(
            full_template,
            command_entry=command_entry,
            subcommands=subcommands,
            argument_template=argument_template,
            description=description,
            parameter_schema=parameter_schema,
            max_chars=self._max_command_chars(),
        )
        normalized_risk = _normalize_risk(risk_level)
        normalized_confidence = round(max(0.0, min(1.0, _finite_float(confidence))), 3)
        threshold = max(0.0, min(1.0, _finite_float(auto_approve_confidence, 0.90)))
        fifo_required = max(2, min(20, int(fifo_evidence_count or 2)))
        cid = build_command_id(uid, validated.full_template)
        normalized_episode_key = _bounded_text(episode_key, 64)
        result: dict[str, Any] = {}

        def _apply(group: dict[str, Any]) -> dict[str, Any]:
            nonlocal result
            bot = group["bots"].get(uid)
            if not isinstance(bot, dict) or bot.get("status") != "approved":
                raise PeerBotRegistryError("protocol_target_bot_not_approved")

            commands = group["commands"]
            existing = commands.get(cid) if isinstance(commands.get(cid), dict) else {}
            evidence_map = group.setdefault("protocol_evidence", {})
            evidence_keys = [
                str(key)
                for key in list(evidence_map.get(cid) or [])[-32:]
                if isinstance(key, str) and key
            ]
            if normalized_episode_key and normalized_episode_key in evidence_keys:
                result = {
                    "diagnostic_code": "peer_bot_protocol_observed",
                    "correlation_kind": correlation_kind,
                    "confidence": normalized_confidence,
                    "command": copy.deepcopy(existing),
                }
                return group
            if existing and existing.get("status") == "approved":
                result = {
                    "diagnostic_code": "peer_bot_protocol_observed",
                    "correlation_kind": correlation_kind,
                    "confidence": normalized_confidence,
                    "command": copy.deepcopy(existing),
                }
                return group

            conflict = any(
                isinstance(command, dict)
                and str(command.get("target_bot_id", "")) == uid
                and command.get("status") == "approved"
                and str(command.get("command_entry", "") or "") == validated.command_entry
                and str(command.get("full_template", "") or "") != validated.full_template
                for command in commands.values()
            )
            previous_evidence = max(0, int(existing.get("evidence_count", 0) or 0))
            evidence_count = min(1000, previous_evidence + 1)
            if normalized_episode_key:
                evidence_keys.append(normalized_episode_key)
                evidence_map[cid] = evidence_keys[-32:]
            auto_enabled = bool(group.get("enabled", False)) and bool(
                group.get("policies", {}).get("auto_learn_approved_commands", False)
            )
            risk_allowed = normalized_risk in {"read", "write"}
            if correlation_kind == "exact_reply":
                confidence_allowed = normalized_confidence >= threshold
                evidence_allowed = True
            else:
                confidence_allowed = normalized_confidence >= max(0.92, threshold)
                evidence_allowed = evidence_count >= fifo_required
            auto_approved = bool(
                auto_enabled
                and risk_allowed
                and not conflict
                and confidence_allowed
                and evidence_allowed
            )
            if not risk_allowed:
                diagnostic = "peer_bot_protocol_risk_blocked"
            elif conflict:
                diagnostic = "peer_bot_protocol_conflict"
            elif auto_approved:
                diagnostic = "peer_bot_protocol_auto_approved"
            else:
                diagnostic = "peer_bot_protocol_candidate"
            command = {
                "command_id": cid,
                "target_bot_id": uid,
                "full_template": validated.full_template,
                "command_head": validated.command_head,
                "command_entry": validated.command_entry,
                "subcommands": list(validated.subcommands),
                "argument_template": validated.argument_template,
                "description": validated.description,
                "legacy_mode": validated.legacy_mode,
                "parameter_schema": validated.parameter_schema,
                "risk_level": normalized_risk,
                "status": "approved" if auto_approved else "candidate",
                "source": "auto_learned" if auto_approved else "llm_observation",
                "manual_override": False,
                "auto_approved": auto_approved,
                "evidence_count": evidence_count,
                "protocol_source": "auto_learned" if auto_approved else "llm_observation",
                "version": max(1, int(existing.get("version", 0) or 0) + 1),
                "updated_at": _now(),
            }
            commands[cid] = command
            command_ids = bot.setdefault("command_ids", [])
            if cid not in command_ids:
                command_ids.append(cid)
            bot["updated_at"] = _now()
            result = {
                "diagnostic_code": diagnostic,
                "correlation_kind": correlation_kind,
                "confidence": normalized_confidence,
                "command": copy.deepcopy(command),
            }
            return group

        self._mutate_group(group_id, _apply)
        return result

    def set_command_status(
        self,
        group_id: Any,
        *,
        target_bot_id: Any,
        command_id: Any,
        action: Literal["approve", "reject"],
    ) -> dict[str, Any]:
        uid = _safe_id(target_bot_id, field="user_id")
        cid = _safe_id(command_id, field="command_id")
        if action not in {"approve", "reject"}:
            raise PeerBotRegistryError("invalid_command_action")
        result: dict[str, Any] = {}

        def _apply(group: dict[str, Any]) -> dict[str, Any]:
            nonlocal result
            command = group["commands"].get(cid)
            if not isinstance(command, dict) or str(command.get("target_bot_id")) != uid:
                raise PeerBotRegistryError("command_not_found")
            command["status"] = "approved" if action == "approve" else "rejected"
            command["source"] = "manual"
            command["manual_override"] = True
            command["auto_approved"] = False
            command["protocol_source"] = "manual"
            command["version"] = max(1, int(command.get("version", 0) or 0) + 1)
            command["updated_at"] = _now()
            result = copy.deepcopy(command)
            return group

        self._mutate_group(group_id, _apply)
        return result

    def delete_command(self, group_id: Any, *, target_bot_id: Any, command_id: Any) -> bool:
        uid = _safe_id(target_bot_id, field="user_id")
        cid = _safe_id(command_id, field="command_id")
        removed = False

        def _apply(group: dict[str, Any]) -> dict[str, Any]:
            nonlocal removed
            command = group["commands"].get(cid)
            if not isinstance(command, dict) or str(command.get("target_bot_id")) != uid:
                return group
            del group["commands"][cid]
            bot = group["bots"].get(uid)
            if isinstance(bot, dict) and isinstance(bot.get("command_ids"), list):
                bot["command_ids"] = [item for item in bot["command_ids"] if item != cid]
                bot["updated_at"] = _now()
            removed = True
            return group

        self._mutate_group(group_id, _apply)
        return removed

    def is_approved_bot(self, group_id: Any, user_id: Any) -> bool:
        uid = _safe_id(user_id, field="user_id")
        group = self.get_group(group_id)
        bot = group["bots"].get(uid)
        return bool(isinstance(bot, dict) and bot.get("status") == "approved")

    def get_approved_command(self, group_id: Any, target_bot_id: Any, command_id: Any) -> dict[str, Any] | None:
        uid = _safe_id(target_bot_id, field="user_id")
        cid = _safe_id(command_id, field="command_id")
        group = self.get_group(group_id)
        bot = group["bots"].get(uid)
        command = group["commands"].get(cid)
        if not isinstance(bot, dict) or bot.get("status") != "approved":
            return None
        if not isinstance(command, dict) or command.get("status") != "approved":
            return None
        if str(command.get("target_bot_id")) != uid:
            return None
        return copy.deepcopy(command)

    def list_group_bots(self, group_id: Any) -> list[dict[str, Any]]:
        group = self.get_group(group_id)
        items = [copy.deepcopy(item) for item in group["bots"].values() if isinstance(item, dict)]
        items.sort(key=lambda item: (-_finite_float(item.get("confidence"), 0.0), str(item.get("user_id", ""))))
        return items

    def snapshot_stats(self) -> dict[str, Any]:
        root = self.store.load_sync(PEER_BOT_REGISTRY_NAMESPACE)
        groups = root if isinstance(root, dict) else {}
        enabled_groups = 0
        candidates = 0
        approved = 0
        approved_commands = 0
        for raw_group in groups.values():
            group = _normalize_group_document(raw_group)
            enabled_groups += int(bool(group["enabled"]))
            for bot in group["bots"].values():
                if not isinstance(bot, dict):
                    continue
                candidates += int(bot.get("status") == "candidate")
                approved += int(bot.get("status") == "approved")
            approved_commands += sum(
                1
                for command in group["commands"].values()
                if isinstance(command, dict) and command.get("status") == "approved"
            )
        return {
            "groups": len(groups),
            "enabled_groups": enabled_groups,
            "candidate_bots": candidates,
            "approved_bots": approved,
            "approved_commands": approved_commands,
        }


__all__ = [
    "BOT_SOURCES",
    "BOT_STATUSES",
    "COMMAND_RISKS",
    "DEFAULT_GROUP_POLICY",
    "EVIDENCE_TAGS",
    "PEER_BOT_REGISTRY_NAMESPACE",
    "PEER_BOT_REGISTRY_VERSION",
    "PeerBotRegistry",
    "PeerBotRegistryError",
    "ValidatedCommandTemplate",
    "build_command_id",
    "compose_full_template",
    "validate_command_template",
]
