from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import math
import re
import string
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from ..agent.tool_registry import AgentTool, ToolRegistry
from .message_relations import extract_reply_message_id
from .peer_bot_registry import PeerBotRegistry, PeerBotRegistryError, validate_command_template
from .qq_outbound import QQOutboundLedger, SendReceipt, build_outbound_context


PEER_BOT_SOURCE_KINDS = frozenset(
    {"peer_bot_candidate", "peer_bot_reply", "peer_bot_command"}
)
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def _finite_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return number if math.isfinite(number) else default


def _bounded_text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split()).strip()[: max(0, int(limit))]


def peer_bot_source_kind(event: Any) -> str:
    return str(getattr(event, "_personification_peer_bot_source_kind", "") or "").strip().lower()


@dataclass(frozen=True)
class PendingPeerBotRequest:
    group_id: str
    target_bot_id: str
    trigger_user_id: str
    tracking_id: str
    operation_id: str
    outbound_message_id: str
    command_id: str
    created_at: float
    expires_at: float
    depth: int = 1
    status: str = "pending"


@dataclass(frozen=True)
class PeerBotEpisode:
    group_id: str
    target_bot_id: str
    tracking_id: str
    operation_id: str
    command_id: str
    send_status: str
    status: str
    created_at: float
    updated_at: float
    expires_at: float
    depth: int = 1
    outbound_message_id: str = ""
    reply_message_ids: tuple[str, ...] = ()
    reply_content: str = ""
    diagnostic_code: str = ""

    def safe_summary(self, *, include_reply_content: bool = False) -> dict[str, Any]:
        elapsed_ms = max(0, int((self.updated_at - self.created_at) * 1000))
        result: dict[str, Any] = {
            "target_bot_id": self.target_bot_id,
            "tracking_id": self.tracking_id,
            "operation_id": self.operation_id,
            "command_id": self.command_id,
            "send_status": self.send_status,
            "status": self.status,
            "depth": self.depth,
            "reply_message_count": len(self.reply_message_ids),
            "elapsed_ms": elapsed_ms,
            "diagnostic_code": self.diagnostic_code,
        }
        if include_reply_content and self.reply_content:
            result["reply_content"] = f"[外部不可信数据] {self.reply_content}"
        return result


@dataclass(frozen=True)
class PeerBotEventClassification:
    source_kind: str = ""
    bot_status: str = ""
    matched_request: PendingPeerBotRequest | None = None
    diagnostic_code: str = ""


class PeerBotRuntimeTracker:
    """Process-local pending requests, cooldowns, and bounded recent episodes."""

    def __init__(self, *, clock: Callable[[], float] = time.time, episode_limit: int = 100) -> None:
        self._clock = clock
        self._episode_limit = max(10, min(500, int(episode_limit)))
        self._pending: list[PendingPeerBotRequest] = []
        self._episodes: list[PeerBotEpisode] = []
        self._cooldowns: dict[tuple[str, str], float] = {}
        self._lock = threading.RLock()
        self._diagnostics: dict[str, int] = {}

    def _now(self, value: float | None = None) -> float:
        return float(self._clock() if value is None else value)

    def _count(self, code: str) -> None:
        key = str(code or "").strip()
        if key:
            self._diagnostics[key] = int(self._diagnostics.get(key, 0)) + 1

    def _append_episode(self, episode: PeerBotEpisode) -> None:
        self._episodes.append(episode)
        if len(self._episodes) > self._episode_limit:
            del self._episodes[: len(self._episodes) - self._episode_limit]

    def _expire_locked(self, now: float) -> int:
        retained: list[PendingPeerBotRequest] = []
        expired = 0
        expired_tracking_ids: set[str] = set()
        timeout_episodes: list[PeerBotEpisode] = []
        for pending in self._pending:
            if pending.expires_at > now:
                retained.append(pending)
                continue
            expired += 1
            expired_tracking_ids.add(pending.tracking_id)
            timeout_episodes.append(
                PeerBotEpisode(
                    group_id=pending.group_id,
                    target_bot_id=pending.target_bot_id,
                    tracking_id=pending.tracking_id,
                    operation_id=pending.operation_id,
                    command_id=pending.command_id,
                    send_status="sent",
                    status="timeout",
                    created_at=pending.created_at,
                    updated_at=now,
                    expires_at=pending.expires_at,
                    depth=pending.depth,
                    outbound_message_id=pending.outbound_message_id,
                    diagnostic_code="peer_bot_response_timeout",
                )
            )
            self._count("peer_bot_response_timeout")
        self._pending = retained
        if expired_tracking_ids:
            self._episodes = [
                episode
                for episode in self._episodes
                if not (
                    episode.tracking_id in expired_tracking_ids
                    and episode.status == "pending"
                )
            ]
            for episode in timeout_episodes:
                self._append_episode(episode)
        return expired

    def record_dispatch(
        self,
        *,
        group_id: str,
        target_bot_id: str,
        trigger_user_id: str,
        tracking_id: str,
        operation_id: str,
        command_id: str,
        send_status: str,
        outbound_message_id: str = "",
        ttl_seconds: float = 30.0,
        depth: int = 1,
        diagnostic_code: str = "",
        now: float | None = None,
    ) -> PendingPeerBotRequest | None:
        timestamp = self._now(now)
        ttl = max(1.0, min(600.0, _finite_float(ttl_seconds, 30.0)))
        pending: PendingPeerBotRequest | None = None
        with self._lock:
            self._expire_locked(timestamp)
            self._cooldowns[(group_id, target_bot_id)] = timestamp
            self._count(diagnostic_code)
            if send_status == "sent" and outbound_message_id:
                pending = PendingPeerBotRequest(
                    group_id=group_id,
                    target_bot_id=target_bot_id,
                    trigger_user_id=trigger_user_id,
                    tracking_id=tracking_id,
                    operation_id=operation_id,
                    outbound_message_id=outbound_message_id,
                    command_id=command_id,
                    created_at=timestamp,
                    expires_at=timestamp + ttl,
                    depth=max(1, int(depth)),
                )
                self._pending.append(pending)
                self._append_episode(
                    PeerBotEpisode(
                        group_id=group_id,
                        target_bot_id=target_bot_id,
                        tracking_id=tracking_id,
                        operation_id=operation_id,
                        command_id=command_id,
                        send_status="sent",
                        status="pending",
                        created_at=timestamp,
                        updated_at=timestamp,
                        expires_at=timestamp + ttl,
                        depth=max(1, int(depth)),
                        outbound_message_id=outbound_message_id,
                        diagnostic_code=diagnostic_code or "peer_bot_dispatch_sent",
                    )
                )
            else:
                self._append_episode(
                    PeerBotEpisode(
                        group_id=group_id,
                        target_bot_id=target_bot_id,
                        tracking_id=tracking_id,
                        operation_id=operation_id,
                        command_id=command_id,
                        send_status=send_status,
                        status="failed",
                        created_at=timestamp,
                        updated_at=timestamp,
                        expires_at=timestamp,
                        depth=max(1, int(depth)),
                        diagnostic_code=diagnostic_code,
                    )
                )
        return pending

    def cooldown_remaining(
        self,
        group_id: str,
        target_bot_id: str,
        *,
        cooldown_seconds: float,
        now: float | None = None,
    ) -> float:
        timestamp = self._now(now)
        cooldown = max(0.0, min(3600.0, _finite_float(cooldown_seconds, 10.0)))
        with self._lock:
            last = self._cooldowns.get((group_id, target_bot_id), 0.0)
        return max(0.0, cooldown - max(0.0, timestamp - last))

    def match_reply(
        self,
        *,
        group_id: str,
        target_bot_id: str,
        reply_to_message_id: str = "",
        reply_message_id: str = "",
        reply_content: str = "",
        now: float | None = None,
    ) -> PendingPeerBotRequest | None:
        timestamp = self._now(now)
        reply_to = str(reply_to_message_id or "").strip()
        with self._lock:
            self._expire_locked(timestamp)
            match_index = -1
            if reply_to:
                for index, pending in enumerate(self._pending):
                    if (
                        pending.group_id == group_id
                        and pending.target_bot_id == target_bot_id
                        and pending.outbound_message_id == reply_to
                    ):
                        match_index = index
                        break
            else:
                for index, pending in enumerate(self._pending):
                    if pending.group_id == group_id and pending.target_bot_id == target_bot_id:
                        match_index = index
                        break
            if match_index < 0:
                return None
            pending = self._pending.pop(match_index)
            self._episodes = [
                episode
                for episode in self._episodes
                if not (
                    episode.tracking_id == pending.tracking_id
                    and episode.status == "pending"
                )
            ]
            self._append_episode(
                PeerBotEpisode(
                    group_id=group_id,
                    target_bot_id=target_bot_id,
                    tracking_id=pending.tracking_id,
                    operation_id=pending.operation_id,
                    command_id=pending.command_id,
                    send_status="sent",
                    status="completed",
                    created_at=pending.created_at,
                    updated_at=timestamp,
                    expires_at=pending.expires_at,
                    depth=pending.depth,
                    outbound_message_id=pending.outbound_message_id,
                    reply_message_ids=(str(reply_message_id or "").strip(),)
                    if str(reply_message_id or "").strip()
                    else (),
                    reply_content=_bounded_text(reply_content, 500),
                    diagnostic_code="peer_bot_response_matched",
                )
            )
            self._count("peer_bot_response_matched")
            return pending

    def recent_episodes(self, group_id: str, *, limit: int = 5) -> list[PeerBotEpisode]:
        timestamp = self._now()
        with self._lock:
            self._expire_locked(timestamp)
            items = [episode for episode in self._episodes if episode.group_id == group_id]
            return list(items[-max(1, min(20, int(limit))):])

    def snapshot(self, *, group_id: str = "") -> dict[str, Any]:
        timestamp = self._now()
        with self._lock:
            self._expire_locked(timestamp)
            pending = [item for item in self._pending if not group_id or item.group_id == group_id]
            episodes = [item for item in self._episodes if not group_id or item.group_id == group_id]
            return {
                "pending_count": len(pending),
                "recent_count": len(episodes),
                "cooldown_count": sum(
                    1 for (gid, _target) in self._cooldowns if not group_id or gid == group_id
                ),
                "max_chain_depth": 1,
                "diagnostics": copy.deepcopy(self._diagnostics),
            }

    def reset_loop(self, *, group_id: str) -> dict[str, Any]:
        with self._lock:
            self._pending = [item for item in self._pending if item.group_id != group_id]
            self._cooldowns = {
                key: value for key, value in self._cooldowns.items() if key[0] != group_id
            }
        return self.snapshot(group_id=group_id)


class PeerBotCoordinator:
    def __init__(
        self,
        *,
        registry: PeerBotRegistry,
        tracker: PeerBotRuntimeTracker,
        plugin_config: Any,
        logger: Any = None,
    ) -> None:
        self.registry = registry
        self.tracker = tracker
        self.plugin_config = plugin_config
        self.logger = logger

    def classify_event(self, event: Any) -> PeerBotEventClassification:
        cached = getattr(event, "_personification_peer_bot_classification", None)
        if isinstance(cached, PeerBotEventClassification):
            return cached
        group_id = str(getattr(event, "group_id", "") or "").strip()
        user_id = str(getattr(event, "user_id", "") or "").strip()
        if not group_id or not user_id:
            result = PeerBotEventClassification()
            setattr(event, "_personification_peer_bot_classification", result)
            return result
        group_enabled = False
        try:
            group = self.registry.get_group(group_id)
            group_enabled = bool(group.get("enabled"))
            bot = group.get("bots", {}).get(user_id)
        except Exception:
            bot = None
        status = str(bot.get("status", "") or "").strip().lower() if isinstance(bot, dict) else ""
        matched: PendingPeerBotRequest | None = None
        diagnostic = ""
        source_kind = ""
        matching_enabled = bool(
            getattr(self.plugin_config, "personification_peer_bot_enabled", False)
        ) and group_enabled
        if status == "approved":
            try:
                text = _bounded_text(event.get_plaintext(), 500)
            except Exception:
                text = ""
            if matching_enabled:
                matched = self.tracker.match_reply(
                    group_id=group_id,
                    target_bot_id=user_id,
                    reply_to_message_id=extract_reply_message_id(event),
                    reply_message_id=str(getattr(event, "message_id", "") or "").strip(),
                    reply_content=text,
                )
            source_kind = "peer_bot_reply"
            diagnostic = (
                "peer_bot_response_matched"
                if matched is not None
                else ("peer_bot_approved" if matching_enabled else "peer_bot_matching_disabled")
            )
        elif status == "candidate":
            source_kind = "peer_bot_candidate"
            diagnostic = "peer_bot_candidate"
        result = PeerBotEventClassification(
            source_kind=source_kind,
            bot_status=status,
            matched_request=matched,
            diagnostic_code=diagnostic,
        )
        setattr(event, "_personification_peer_bot_classification", result)
        setattr(event, "_personification_peer_bot_source_kind", source_kind)
        if matched is not None:
            setattr(event, "_personification_peer_bot_tracking_id", matched.tracking_id)
            setattr(event, "_personification_peer_bot_operation_id", matched.operation_id)
            setattr(event, "_personification_peer_bot_command_id", matched.command_id)
        return result


def _parameter_value(name: str, schema: Mapping[str, Any], value: Any) -> str:
    parameter_type = str(schema.get("type", "string") or "string")
    if parameter_type == "string":
        if not isinstance(value, str):
            raise PeerBotRegistryError(f"argument_type_mismatch:{name}")
        normalized: Any = value
        max_length = max(1, min(500, int(schema.get("maxLength", 500) or 500)))
        if len(normalized) > max_length:
            raise PeerBotRegistryError(f"argument_too_long:{name}")
    elif parameter_type == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            raise PeerBotRegistryError(f"argument_type_mismatch:{name}")
        normalized = value
    elif parameter_type == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            raise PeerBotRegistryError(f"argument_type_mismatch:{name}")
        normalized = value
    elif parameter_type == "boolean":
        if not isinstance(value, bool):
            raise PeerBotRegistryError(f"argument_type_mismatch:{name}")
        normalized = "true" if value else "false"
    else:
        raise PeerBotRegistryError(f"unsupported_argument_type:{name}")
    if "enum" in schema and normalized not in schema.get("enum", []):
        raise PeerBotRegistryError(f"argument_enum_mismatch:{name}")
    if parameter_type in {"integer", "number"}:
        numeric = float(normalized)
        if "minimum" in schema and numeric < float(schema["minimum"]):
            raise PeerBotRegistryError(f"argument_below_minimum:{name}")
        if "maximum" in schema and numeric > float(schema["maximum"]):
            raise PeerBotRegistryError(f"argument_above_maximum:{name}")
    rendered = str(normalized)
    if "\n" in rendered or "\r" in rendered or _CONTROL_RE.search(rendered):
        raise PeerBotRegistryError(f"argument_control_character:{name}")
    return rendered


def _parse_full_command_value(name: str, schema: Mapping[str, Any], value: str) -> Any:
    parameter_type = str(schema.get("type", "string") or "string")
    if parameter_type == "integer":
        if not re.fullmatch(r"-?\d+", value):
            raise PeerBotRegistryError(f"argument_type_mismatch:{name}")
        return int(value)
    if parameter_type == "number":
        try:
            parsed = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise PeerBotRegistryError(f"argument_type_mismatch:{name}") from exc
        if not math.isfinite(parsed):
            raise PeerBotRegistryError(f"argument_type_mismatch:{name}")
        return parsed
    if parameter_type == "boolean":
        if value not in {"true", "false"}:
            raise PeerBotRegistryError(f"argument_type_mismatch:{name}")
        return value == "true"
    return value


def render_approved_command(
    command: Mapping[str, Any],
    *,
    arguments: Any = None,
    full_command: Any = None,
    max_chars: int = 500,
) -> str:
    validated = validate_command_template(
        command.get("full_template", ""),
        parameter_schema=command.get("parameter_schema", {}),
        max_chars=max_chars,
    )
    schema = validated.parameter_schema
    properties = schema.get("properties", {})
    required = set(schema.get("required", []))
    provided = arguments if isinstance(arguments, dict) else {}
    if arguments not in (None, {}) and not isinstance(arguments, dict):
        raise PeerBotRegistryError("arguments_must_be_object")
    unknown = set(provided) - set(properties)
    if unknown:
        raise PeerBotRegistryError("undeclared_arguments")
    if required - set(provided) and full_command in (None, ""):
        raise PeerBotRegistryError("required_arguments_missing")
    rendered_arguments = {
        name: _parameter_value(name, properties[name], value)
        for name, value in provided.items()
    }

    expected = ""
    if set(validated.placeholders) <= set(rendered_arguments):
        expected = validated.full_template.format_map(rendered_arguments)
    supplied_full = str(full_command or "").strip()
    if supplied_full:
        if len(supplied_full) > max_chars:
            raise PeerBotRegistryError("full_command_too_long")
        if "\n" in supplied_full or "\r" in supplied_full or _CONTROL_RE.search(supplied_full):
            raise PeerBotRegistryError("full_command_control_character")
        if expected:
            if supplied_full != expected:
                raise PeerBotRegistryError("full_command_template_mismatch")
            final = supplied_full
        elif not validated.placeholders:
            if supplied_full != validated.full_template:
                raise PeerBotRegistryError("full_command_template_mismatch")
            final = supplied_full
        else:
            pattern_parts: list[str] = []
            for literal, field_name, _format_spec, _conversion in string.Formatter().parse(
                validated.full_template
            ):
                pattern_parts.append(re.escape(literal))
                if field_name is not None:
                    pattern_parts.append(f"(?P<{field_name}>.+?)")
            matched = re.fullmatch("".join(pattern_parts), supplied_full)
            if matched is None:
                raise PeerBotRegistryError("full_command_template_mismatch")
            parsed_arguments = {
                name: _parameter_value(
                    name,
                    properties[name],
                    _parse_full_command_value(name, properties[name], value),
                )
                for name, value in matched.groupdict().items()
            }
            if any(parsed_arguments.get(name) != value for name, value in rendered_arguments.items()):
                raise PeerBotRegistryError("full_command_arguments_mismatch")
            if required - set(parsed_arguments):
                raise PeerBotRegistryError("required_arguments_missing")
            rebuilt = validated.full_template.format_map(parsed_arguments)
            if rebuilt != supplied_full:
                raise PeerBotRegistryError("full_command_template_mismatch")
            final = supplied_full
    else:
        if not expected:
            raise PeerBotRegistryError("required_arguments_missing")
        final = expected
    if len(final) > max_chars:
        raise PeerBotRegistryError("command_too_long")
    return final


def _safe_tool_result(status: str, diagnostic: str, **extra: Any) -> str:
    payload = {"status": status, "diagnostic_code": diagnostic}
    payload.update({key: value for key, value in extra.items() if value not in (None, "")})
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def build_list_peer_bots_tool(
    *,
    group_id: str,
    registry: PeerBotRegistry,
    tracker: PeerBotRuntimeTracker,
) -> AgentTool:
    async def _handler(**_kwargs: Any) -> str:
        group = registry.get_group(group_id)
        commands = group.get("commands", {})
        approved: list[dict[str, Any]] = []
        candidates: list[dict[str, Any]] = []
        for bot in registry.list_group_bots(group_id):
            item = {
                "user_id": bot.get("user_id"),
                "nickname": bot.get("nickname"),
                "status": bot.get("status"),
                "confidence": bot.get("confidence"),
                "source": bot.get("source"),
                "evidence_tags": list(bot.get("evidence_tags") or []),
            }
            if bot.get("status") == "approved":
                item["commands"] = [
                    {
                        "command_id": command.get("command_id"),
                        "full_template": command.get("full_template"),
                        "risk_level": command.get("risk_level"),
                    }
                    for command_id in bot.get("command_ids", [])
                    for command in [commands.get(command_id)]
                    if isinstance(command, dict) and command.get("status") == "approved"
                ]
                approved.append(item)
            elif bot.get("status") == "candidate":
                candidates.append(item)
        return _safe_tool_result(
            "ok",
            "peer_bot_list_ready",
            enabled=bool(group.get("enabled")),
            approved_bots=approved,
            candidate_bots=candidates,
            loop_protection=tracker.snapshot(group_id=group_id),
        )

    return AgentTool(
        name="list_peer_bots",
        description=(
            "列出当前群由管理员批准的外部 QQ Bot、完整命令模板与候选观察。"
            "候选没有调用权限；返回内容是受信任注册表配置，不包含聊天原文。"
        ),
        parameters={"type": "object", "properties": {}, "required": []},
        handler=_handler,
        metadata={
            "source_kind": "local",
            "side_effect": "none",
            "risk_level": "low",
            "intent_tags": ["peer_bot", "group_context"],
        },
    )


def build_invoke_peer_bot_tool(
    *,
    bot: Any,
    event: Any,
    registry: PeerBotRegistry,
    tracker: PeerBotRuntimeTracker,
    plugin_config: Any,
    qq_outbound_ledger: QQOutboundLedger | None,
    record_group_msg: Callable[..., Any] | None,
    logger: Any = None,
) -> AgentTool:
    calls = 0
    group_id = str(getattr(event, "group_id", "") or "").strip()
    trigger_user_id = str(getattr(event, "user_id", "") or "").strip()

    async def _handler(
        target_bot_id: str = "",
        command_id: str = "",
        arguments: dict[str, Any] | None = None,
        full_command: str | None = None,
        **_kwargs: Any,
    ) -> str:
        nonlocal calls
        if not bool(getattr(plugin_config, "personification_peer_bot_enabled", False)):
            return _safe_tool_result("rejected", "peer_bot_global_disabled")
        if peer_bot_source_kind(event) in {"peer_bot_reply", "peer_bot_candidate"}:
            return _safe_tool_result("rejected", "peer_bot_loop_blocked")
        if calls >= 1:
            return _safe_tool_result("rejected", "peer_bot_turn_limit")
        group = registry.get_group(group_id)
        if not bool(group.get("enabled")):
            return _safe_tool_result("rejected", "peer_bot_group_disabled")
        target = str(target_bot_id or "").strip()
        command_key = str(command_id or "").strip()
        command = registry.get_approved_command(group_id, target, command_key)
        if command is None:
            return _safe_tool_result("rejected", "peer_bot_command_unapproved")
        risk_level = str(command.get("risk_level", "") or "").strip().lower()
        if risk_level not in {"read", "write"}:
            return _safe_tool_result("rejected", "peer_bot_command_risk_blocked")
        policies = group.get("policies", {}) if isinstance(group.get("policies"), dict) else {}
        cooldown_seconds = _finite_float(
            policies.get(
                "cooldown_seconds",
                getattr(plugin_config, "personification_peer_bot_cooldown_seconds", 10.0),
            ),
            10.0,
        )
        remaining = tracker.cooldown_remaining(
            group_id,
            target,
            cooldown_seconds=cooldown_seconds,
        )
        if remaining > 0:
            return _safe_tool_result(
                "rejected",
                "peer_bot_cooldown",
                retry_after_ms=max(1, int(remaining * 1000)),
            )
        max_chars = max(
            32,
            min(
                4000,
                int(getattr(plugin_config, "personification_peer_bot_max_command_chars", 500) or 500),
            ),
        )
        try:
            rendered = render_approved_command(
                command,
                arguments=arguments,
                full_command=full_command,
                max_chars=max_chars,
            )
        except PeerBotRegistryError as exc:
            return _safe_tool_result("rejected", str(exc)[:80] or "peer_bot_command_invalid")
        calls += 1
        operation_id = f"peerbot:{uuid.uuid4().hex}"
        tracking_id = "pb_" + hashlib.sha256(operation_id.encode("utf-8")).hexdigest()[:20]
        context = build_outbound_context(
            bot=bot,
            event=event,
            surface="peer_bot_command",
            operation_id=operation_id,
            user_target=target,
        )

        async def _send() -> Any:
            group_target: Any = int(group_id) if group_id.isdigit() else group_id
            return await bot.send_group_msg(group_id=group_target, message=rendered)

        receipt: SendReceipt | None = None
        diagnostic = "peer_bot_dispatch_unknown"
        try:
            if qq_outbound_ledger is None:
                tracker.record_dispatch(
                    group_id=group_id,
                    target_bot_id=target,
                    trigger_user_id=trigger_user_id,
                    tracking_id=tracking_id,
                    operation_id=operation_id,
                    command_id=command_key,
                    send_status="unknown",
                    ttl_seconds=0,
                    depth=1,
                    diagnostic_code="peer_bot_ledger_unavailable",
                )
                return _safe_tool_result(
                    "unknown",
                    "peer_bot_ledger_unavailable",
                    tracking_id=tracking_id,
                    operation_id=operation_id,
                    command_id=command_key,
                    pending=False,
                )
            receipt = await qq_outbound_ledger.dispatch(
                context,
                rendered,
                _send,
                failure_status_resolver=classify_peer_bot_send_failure,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            candidate = getattr(exc, "qq_outbound_receipt", None)
            receipt = candidate if isinstance(candidate, SendReceipt) else None
            if logger is not None:
                try:
                    logger.debug(f"拟人插件：Peer Bot 命令发送未确认: {type(exc).__name__}")
                except Exception:
                    pass
        status = receipt.status if receipt is not None else "unknown"
        diagnostic = {
            "sent": "peer_bot_dispatch_sent",
            "failed": "peer_bot_dispatch_failed",
            "unknown": "peer_bot_dispatch_unknown",
        }.get(status, "peer_bot_dispatch_unknown")
        outbound_message_id = str(receipt.message_id or "").strip() if receipt is not None else ""
        ttl_seconds = _finite_float(
            policies.get(
                "pending_ttl_seconds",
                getattr(plugin_config, "personification_peer_bot_pending_ttl_seconds", 30.0),
            ),
            30.0,
        )
        tracker.record_dispatch(
            group_id=group_id,
            target_bot_id=target,
            trigger_user_id=trigger_user_id,
            tracking_id=tracking_id,
            operation_id=operation_id,
            command_id=command_key,
            send_status=status,
            outbound_message_id=outbound_message_id,
            ttl_seconds=ttl_seconds,
            depth=1,
            diagnostic_code=diagnostic,
        )
        if status == "sent" and record_group_msg is not None:
            try:
                record_group_msg(
                    group_id,
                    "Peer Bot 命令",
                    rendered,
                    is_bot=True,
                    user_id=str(getattr(bot, "self_id", "") or ""),
                    message_id=outbound_message_id,
                    source_kind="peer_bot_command",
                )
            except Exception:
                pass
        return _safe_tool_result(
            status,
            diagnostic,
            tracking_id=tracking_id,
            operation_id=operation_id,
            command_id=command_key,
            pending=status == "sent" and bool(outbound_message_id),
        )

    return AgentTool(
        name="invoke_peer_bot",
        description=(
            "按当前群管理员批准的 command_id 与参数调用独立 QQ Bot。"
            "只允许 read/write 模板、单回合一次；返回仅代表命令发送状态，绝不代表第三方 Bot 已回复。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "target_bot_id": {"type": "string"},
                "command_id": {"type": "string"},
                "arguments": {"type": "object", "additionalProperties": True},
                "full_command": {"type": ["string", "null"]},
            },
            "required": ["target_bot_id", "command_id", "arguments"],
            "additionalProperties": False,
        },
        handler=_handler,
        metadata={
            "source_kind": "local",
            "side_effect": "external",
            "risk_level": "medium",
            "intent_tags": ["peer_bot", "group_action"],
            "retryable": False,
        },
        per_session_quota=1,
    )


def classify_peer_bot_send_failure(exc: BaseException) -> str:
    if isinstance(exc, (asyncio.CancelledError, asyncio.TimeoutError, TimeoutError, ConnectionError)):
        return "unknown"
    name = type(exc).__name__.lower()
    if any(marker in name for marker in ("timeout", "connect", "network", "protocol", "readerror", "writeerror")):
        return "unknown"
    for field in ("retcode", "status_code"):
        value = getattr(exc, field, None)
        try:
            code = int(value)
        except (TypeError, ValueError, OverflowError):
            continue
        if field == "retcode" and code != 0:
            return "failed"
        if field == "status_code" and 400 <= code < 500 and code not in {408, 409, 425, 429}:
            return "failed"
    return "unknown"


def register_peer_bot_tools(
    tool_registry: ToolRegistry,
    *,
    bot: Any,
    event: Any,
    registry: PeerBotRegistry | None,
    tracker: PeerBotRuntimeTracker | None,
    plugin_config: Any,
    qq_outbound_ledger: QQOutboundLedger | None,
    record_group_msg: Callable[..., Any] | None,
    logger: Any = None,
) -> None:
    group_id = str(getattr(event, "group_id", "") or "").strip()
    if not group_id or registry is None or tracker is None:
        return
    if not bool(getattr(plugin_config, "personification_peer_bot_enabled", False)):
        return
    tool_registry.register(
        build_list_peer_bots_tool(group_id=group_id, registry=registry, tracker=tracker)
    )
    try:
        group_enabled = bool(registry.get_group(group_id).get("enabled"))
    except Exception:
        group_enabled = False
    if not group_enabled or peer_bot_source_kind(event) in {"peer_bot_reply", "peer_bot_candidate"}:
        return
    tool_registry.register(
        build_invoke_peer_bot_tool(
            bot=bot,
            event=event,
            registry=registry,
            tracker=tracker,
            plugin_config=plugin_config,
            qq_outbound_ledger=qq_outbound_ledger,
            record_group_msg=record_group_msg,
            logger=logger,
        )
    )


def build_peer_bot_context_episodes(
    *,
    group_id: str,
    registry: PeerBotRegistry | None,
    tracker: PeerBotRuntimeTracker | None,
    limit: int = 3,
) -> list[dict[str, Any]]:
    if not group_id or registry is None or tracker is None:
        return []
    try:
        group = registry.get_group(group_id)
        commands = group.get("commands", {}) if isinstance(group.get("commands"), dict) else {}
        bots = group.get("bots", {}) if isinstance(group.get("bots"), dict) else {}
        episodes = tracker.recent_episodes(group_id, limit=limit)
    except Exception:
        return []
    result: list[dict[str, Any]] = []
    for episode in episodes:
        item = episode.safe_summary(include_reply_content=True)
        command = commands.get(episode.command_id)
        peer_bot = bots.get(episode.target_bot_id)
        item["target_bot_nickname"] = (
            _bounded_text(peer_bot.get("nickname"), 80) if isinstance(peer_bot, dict) else ""
        )
        item["command_template"] = (
            _bounded_text(command.get("full_template"), 200) if isinstance(command, dict) else ""
        )
        result.append(item)
    return result


__all__ = [
    "PEER_BOT_SOURCE_KINDS",
    "PeerBotCoordinator",
    "PeerBotEpisode",
    "PeerBotEventClassification",
    "PeerBotRuntimeTracker",
    "PendingPeerBotRequest",
    "build_invoke_peer_bot_tool",
    "build_list_peer_bots_tool",
    "build_peer_bot_context_episodes",
    "classify_peer_bot_send_failure",
    "peer_bot_source_kind",
    "register_peer_bot_tools",
    "render_approved_command",
]
