from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class CapabilityState(StrEnum):
    AVAILABLE = "available"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"
    DISABLED = "disabled"


@dataclass(frozen=True)
class ProtocolIdentity:
    self_id: str
    implementation: str
    app_name: str = ""
    app_version: str = ""
    protocol_version: str = ""
    checked_at: float = 0.0


@dataclass(frozen=True)
class CapabilityRecord:
    name: str
    state: CapabilityState
    reason_code: str
    selected_path: str = ""
    fallback_path: str = ""
    evidence: str = ""
    checked_at: float = 0.0
    expires_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "state": self.state.value,
            "reason_code": self.reason_code,
            "selected_path": self.selected_path,
            "fallback_path": self.fallback_path,
            "evidence": self.evidence,
            "checked_at": self.checked_at,
            "expires_at": self.expires_at,
        }


@dataclass(frozen=True)
class CapabilityMatrix:
    identity: ProtocolIdentity
    capabilities: dict[str, CapabilityRecord]

    def get(self, name: str) -> CapabilityRecord:
        normalized = str(name or "")
        return self.capabilities.get(
            normalized,
            CapabilityRecord(normalized, CapabilityState.UNKNOWN, "not_declared"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "identity": {
                "self_id": self.identity.self_id,
                "implementation": self.identity.implementation,
                "app_name": self.identity.app_name,
                "app_version": self.identity.app_version,
                "protocol_version": self.identity.protocol_version,
                "checked_at": self.identity.checked_at,
            },
            "capabilities": {
                name: record.to_dict()
                for name, record in sorted(self.capabilities.items())
            },
        }


@dataclass(frozen=True)
class ProtocolResult:
    status: str
    code: str
    data: Any = None
    selected_path: str = ""
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "succeeded"


@dataclass
class _PathHealth:
    state: CapabilityState
    reason_code: str
    expires_at: float


_IDENTITY_TTL_SECONDS = 5 * 60
_TRANSIENT_COOLDOWN_SECONDS = 60
_UNAVAILABLE_COOLDOWN_SECONDS = 30 * 60
_KNOWN_IMPLEMENTATIONS = {"napcat", "llonebot", "lagrange", "gocq", "unknown"}

_STANDARD_CAPABILITIES: dict[str, str] = {
    "account.info.read": "get_login_info",
    "group.info.read": "get_group_info",
    "group.member.read": "get_group_member_info",
    "message.recall": "delete_msg",
    "qzone.cookie_export": "get_cookies",
}


def _version_tuple(value: str) -> tuple[int, ...]:
    parts: list[int] = []
    for item in str(value or "").strip().lower().lstrip("v").split("."):
        digits = "".join(char for char in item if char.isdigit())
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def _version_at_least(value: str, minimum: str) -> bool:
    current = _version_tuple(value)
    target = _version_tuple(minimum)
    if not current:
        return False
    width = max(len(current), len(target))
    return current + (0,) * (width - len(current)) >= target + (0,) * (width - len(target))


def _implementation_from_app_name(value: Any) -> str:
    app = str(value or "").strip().lower()
    if "napcat" in app:
        return "napcat"
    if "llonebot" in app or "llbot" in app or "luckylillia" in app:
        return "llonebot"
    if "lagrange" in app:
        return "lagrange"
    if "go-cqhttp" in app or "gocq" in app:
        return "gocq"
    return "unknown"


def _extension_mode(plugin_config: Any) -> str:
    mode = str(
        getattr(plugin_config, "personification_protocol_extensions", "auto")
        if plugin_config is not None
        else "auto"
    ).strip().lower()
    if mode == "none" or mode in _KNOWN_IMPLEMENTATIONS:
        return mode
    return "auto"


def _exception_code(exc: BaseException) -> tuple[CapabilityState, str, float]:
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return CapabilityState.DEGRADED, "timeout", _TRANSIENT_COOLDOWN_SECONDS
    name = type(exc).__name__.lower()
    if any(marker in name for marker in ("network", "connect", "timeout", "transport")):
        return CapabilityState.DEGRADED, "network_error", _TRANSIENT_COOLDOWN_SECONDS
    retcode = getattr(exc, "retcode", None)
    status_code = getattr(exc, "status_code", None)
    if retcode in {404, 1404} or status_code == 404:
        return CapabilityState.UNAVAILABLE, "action_not_found", _UNAVAILABLE_COOLDOWN_SECONDS
    text = str(exc or "").strip().lower()
    if any(marker in text for marker in ("not supported", "unsupported", "action not found", "api not found")):
        return CapabilityState.UNAVAILABLE, "action_not_found", _UNAVAILABLE_COOLDOWN_SECONDS
    return CapabilityState.UNKNOWN, "action_failed", _TRANSIENT_COOLDOWN_SECONDS


class ProtocolAdapter:
    def __init__(self, bot: Any, plugin_config: Any = None, logger: Any = None) -> None:
        self.bot = bot
        self.plugin_config = plugin_config
        self.logger = logger
        self._identity: ProtocolIdentity | None = None
        self._identity_expires_at = 0.0
        self._path_health: dict[str, _PathHealth] = {}
        self._capability_observations: dict[str, CapabilityRecord] = {}

    @property
    def self_id(self) -> str:
        return str(getattr(self.bot, "self_id", "") or "")

    async def _call_api(self, action: str, **params: Any) -> Any:
        method = getattr(self.bot, action, None)
        if callable(method):
            return await method(**params)
        caller = getattr(self.bot, "call_api", None)
        if not callable(caller):
            raise AttributeError(action)
        return await caller(action, **params)

    async def identity(self, *, refresh: bool = False) -> ProtocolIdentity:
        mode = _extension_mode(self.plugin_config)
        now = time.time()
        if not refresh and self._identity is not None and now < self._identity_expires_at:
            return self._identity
        if mode == "none":
            identity = ProtocolIdentity(
                self_id=self.self_id,
                implementation="unknown",
                app_name="extensions-disabled",
                checked_at=now,
            )
        elif mode in _KNOWN_IMPLEMENTATIONS - {"unknown"}:
            identity = ProtocolIdentity(
                self_id=self.self_id,
                implementation=mode,
                app_name=f"forced:{mode}",
                checked_at=now,
            )
        else:
            info: dict[str, Any] = {}
            try:
                raw = await self._call_api("get_version_info")
                info = dict(raw) if isinstance(raw, dict) else {}
            except Exception as exc:
                if self.logger is not None:
                    self.logger.debug(f"[protocol_adapter] get_version_info failed: {type(exc).__name__}")
            identity = ProtocolIdentity(
                self_id=self.self_id,
                implementation=_implementation_from_app_name(info.get("app_name")),
                app_name=str(info.get("app_name", "") or ""),
                app_version=str(info.get("app_version", "") or ""),
                protocol_version=str(info.get("protocol_version", "") or ""),
                checked_at=now,
            )
        changed = self._identity is not None and (
            self._identity.implementation,
            self._identity.app_name,
            self._identity.app_version,
            self._identity.protocol_version,
        ) != (
            identity.implementation,
            identity.app_name,
            identity.app_version,
            identity.protocol_version,
        )
        self._identity = identity
        self._identity_expires_at = now + _IDENTITY_TTL_SECONDS
        if changed:
            self._path_health.clear()
            self._capability_observations.clear()
        return identity

    async def matrix(self, *, refresh: bool = False) -> CapabilityMatrix:
        identity = await self.identity(refresh=refresh)
        now = time.time()
        records: dict[str, CapabilityRecord] = {
            name: CapabilityRecord(
                name=name,
                state=CapabilityState.AVAILABLE,
                reason_code="onebot_v11_standard",
                selected_path=path,
                evidence="standard",
                checked_at=identity.checked_at,
            )
            for name, path in _STANDARD_CAPABILITIES.items()
        }
        records.update(self._extension_capabilities(identity))
        for name, observed in list(self._capability_observations.items()):
            if observed.expires_at and observed.expires_at <= now:
                self._capability_observations.pop(name, None)
                continue
            records[name] = observed
        return CapabilityMatrix(identity=identity, capabilities=records)

    def _extension_capabilities(self, identity: ProtocolIdentity) -> dict[str, CapabilityRecord]:
        now = identity.checked_at or time.time()
        mode = _extension_mode(self.plugin_config)

        def record(
            name: str,
            state: CapabilityState,
            path: str = "",
            *,
            fallback: str = "",
            reason: str = "implementation_contract",
        ) -> CapabilityRecord:
            if mode == "none":
                return CapabilityRecord(name, CapabilityState.DISABLED, "extensions_disabled", checked_at=now)
            return CapabilityRecord(
                name=name,
                state=state,
                reason_code=reason,
                selected_path=path,
                fallback_path=fallback,
                evidence="implementation",
                checked_at=now,
            )

        impl = identity.implementation
        unknown = CapabilityState.UNKNOWN
        unavailable = CapabilityState.UNAVAILABLE
        records = {
            "message.reaction": record("message.reaction", unavailable, reason="implementation_unsupported"),
            "message.poke_group": record("message.poke_group", unavailable, reason="implementation_unsupported"),
            "message.poke_private": record("message.poke_private", unavailable, reason="implementation_unsupported"),
            "message.input_status": record("message.input_status", unavailable, reason="implementation_unsupported"),
            "expression.favorite": record("expression.favorite", unknown, reason="implementation_unknown"),
            "expression.recommended": record("expression.recommended", unavailable, reason="implementation_unsupported"),
            "group.announcement.read": record("group.announcement.read", unknown, reason="implementation_unknown"),
            "group.announcement.delete": record("group.announcement.delete", unknown, reason="implementation_unknown"),
            "account.signature.write": record("account.signature.write", unknown, reason="implementation_unknown"),
            "file.upload_group": record("file.upload_group", unavailable, reason="implementation_unsupported"),
            "file.upload_private": record("file.upload_private", unavailable, reason="implementation_unsupported"),
        }
        if impl == "napcat":
            records.update(
                {
                    "message.reaction": record("message.reaction", CapabilityState.AVAILABLE, "set_msg_emoji_like"),
                    "message.poke_group": record("message.poke_group", CapabilityState.AVAILABLE, "group_poke", fallback="send_poke"),
                    "message.poke_private": record("message.poke_private", CapabilityState.AVAILABLE, "friend_poke", fallback="send_poke"),
                    "message.input_status": record("message.input_status", CapabilityState.AVAILABLE, "set_input_status"),
                    "expression.favorite": record("expression.favorite", CapabilityState.AVAILABLE, "fetch_custom_face"),
                    "group.announcement.read": record("group.announcement.read", CapabilityState.AVAILABLE, "_get_group_notice"),
                    "group.announcement.delete": record("group.announcement.delete", CapabilityState.AVAILABLE, "_del_group_notice"),
                    "account.signature.write": record("account.signature.write", CapabilityState.AVAILABLE, "set_self_longnick"),
                    "file.upload_group": record("file.upload_group", CapabilityState.AVAILABLE, "upload_group_file"),
                    "file.upload_private": record("file.upload_private", CapabilityState.AVAILABLE, "upload_private_file"),
                }
            )
        elif impl == "llonebot":
            send_poke_path = "send_poke" if _version_at_least(identity.app_version, "7.11.3") else ""
            typing_state = CapabilityState.AVAILABLE if _version_at_least(identity.app_version, "7.12.3") else CapabilityState.UNKNOWN
            recommended_state = CapabilityState.AVAILABLE if _version_at_least(identity.app_version, "5.5.0") else CapabilityState.UNKNOWN
            records.update(
                {
                    "message.reaction": record("message.reaction", CapabilityState.AVAILABLE, "set_msg_emoji_like"),
                    "message.poke_group": record("message.poke_group", CapabilityState.AVAILABLE, "group_poke", fallback=send_poke_path),
                    "message.poke_private": record("message.poke_private", CapabilityState.AVAILABLE, "friend_poke", fallback=send_poke_path),
                    "message.input_status": record("message.input_status", typing_state, "set_input_status", reason="version_contract"),
                    "expression.favorite": record("expression.favorite", CapabilityState.AVAILABLE, "fetch_custom_face"),
                    "expression.recommended": record("expression.recommended", recommended_state, "get_recommend_face", reason="version_contract"),
                    "group.announcement.read": record("group.announcement.read", CapabilityState.AVAILABLE, "_get_group_notice"),
                    "group.announcement.delete": record("group.announcement.delete", CapabilityState.AVAILABLE, "_delete_group_notice"),
                    "account.signature.write": record("account.signature.write", CapabilityState.AVAILABLE, "set_qq_profile"),
                    "file.upload_group": record("file.upload_group", CapabilityState.AVAILABLE, "upload_group_file"),
                    "file.upload_private": record("file.upload_private", CapabilityState.AVAILABLE, "upload_private_file"),
                }
            )
        elif impl == "lagrange":
            records.update(
                {
                    "message.reaction": record("message.reaction", CapabilityState.AVAILABLE, "set_group_reaction"),
                    "message.poke_group": record("message.poke_group", CapabilityState.AVAILABLE, "group_poke"),
                    "message.poke_private": record("message.poke_private", CapabilityState.AVAILABLE, "friend_poke"),
                }
            )
        elif impl == "unknown":
            for name in records:
                records[name] = record(name, unknown, reason="implementation_unknown")
        return records

    def _path_available(self, action: str) -> bool:
        health = self._path_health.get(action)
        if health is None:
            return True
        if health.expires_at <= time.time():
            self._path_health.pop(action, None)
            return True
        return health.state not in {CapabilityState.DEGRADED, CapabilityState.UNAVAILABLE}

    async def _attempt(
        self,
        action: str,
        *,
        respect_path_health: bool = True,
        **params: Any,
    ) -> ProtocolResult:
        if not action or (respect_path_health and not self._path_available(action)):
            health = self._path_health.get(action)
            return ProtocolResult(
                status=(
                    "degraded"
                    if health is not None and health.state == CapabilityState.DEGRADED
                    else "unavailable"
                ),
                code=health.reason_code if health is not None else "path_unavailable",
                selected_path=action,
            )
        try:
            data = await self._call_api(action, **params)
        except Exception as exc:
            state, code, ttl = _exception_code(exc)
            self._path_health[action] = _PathHealth(state, code, time.time() + ttl)
            status = (
                "degraded"
                if state == CapabilityState.DEGRADED
                else "unavailable"
                if state == CapabilityState.UNAVAILABLE
                else "definite_failure"
            )
            return ProtocolResult(status, code, selected_path=action, detail=type(exc).__name__)
        self._path_health.pop(action, None)
        return ProtocolResult("succeeded", "ok", data=data, selected_path=action)

    async def _capability(self, name: str) -> CapabilityRecord:
        return (await self.matrix()).get(name)

    def _observe(self, capability_name: str, result: ProtocolResult) -> ProtocolResult:
        state = {
            "succeeded": CapabilityState.AVAILABLE,
            "degraded": CapabilityState.DEGRADED,
            "unavailable": CapabilityState.UNAVAILABLE,
        }.get(result.status, CapabilityState.UNKNOWN)
        ttl = (
            _IDENTITY_TTL_SECONDS
            if state == CapabilityState.AVAILABLE
            else _UNAVAILABLE_COOLDOWN_SECONDS
            if state == CapabilityState.UNAVAILABLE
            else _TRANSIENT_COOLDOWN_SECONDS
        )
        now = time.time()
        self._capability_observations[capability_name] = CapabilityRecord(
            name=capability_name,
            state=state,
            reason_code=result.code,
            selected_path=result.selected_path,
            evidence="observed_operation",
            checked_at=now,
            expires_at=now + ttl,
        )
        return result

    async def emoji_react(self, *, message_id: int, face_id: int, group_id: str = "") -> ProtocolResult:
        capability = await self._capability("message.reaction")
        if capability.state not in {CapabilityState.AVAILABLE, CapabilityState.DEGRADED}:
            return ProtocolResult("unavailable", capability.reason_code)
        identity = await self.identity()
        if identity.implementation == "lagrange":
            if not str(group_id or "").isdigit():
                return ProtocolResult("definite_failure", "group_id_required")
            return self._observe("message.reaction", await self._attempt(
                "set_group_reaction",
                group_id=int(group_id),
                message_id=int(message_id),
                code=str(int(face_id)),
                is_add=True,
            ))
        return self._observe("message.reaction", await self._attempt(
            "set_msg_emoji_like",
            message_id=int(message_id),
            emoji_id=int(face_id),
            set=True,
        ))

    async def poke(self, *, user_id: int, group_id: str = "") -> ProtocolResult:
        capability_name = "message.poke_group" if str(group_id or "") else "message.poke_private"
        capability = await self._capability(capability_name)
        if capability.state not in {CapabilityState.AVAILABLE, CapabilityState.DEGRADED}:
            return ProtocolResult("unavailable", capability.reason_code)
        params = {"user_id": int(user_id)}
        if str(group_id or ""):
            params["group_id"] = int(group_id)
        primary = await self._attempt(capability.selected_path, **params)
        if primary.ok or not capability.fallback_path:
            return self._observe(capability_name, primary)
        return self._observe(capability_name, await self._attempt(capability.fallback_path, **params))

    async def set_typing(self, *, user_id: int) -> ProtocolResult:
        capability = await self._capability("message.input_status")
        if capability.state != CapabilityState.AVAILABLE:
            return ProtocolResult("unavailable", capability.reason_code)
        return self._observe(
            "message.input_status",
            await self._attempt(capability.selected_path, user_id=int(user_id), event_type=1),
        )

    async def recall_message(self, *, message_id: int | str) -> ProtocolResult:
        normalized_message_id: int | None = None
        if isinstance(message_id, int) and not isinstance(message_id, bool):
            normalized_message_id = message_id
        elif isinstance(message_id, str):
            digits = message_id[1:] if message_id.startswith("-") else message_id
            if digits and digits.isascii() and digits.isdecimal():
                normalized_message_id = int(message_id, 10)
        if (
            normalized_message_id is None
            or normalized_message_id == 0
            or normalized_message_id < -(2**31)
            or normalized_message_id > 2**31 - 1
        ):
            return self._observe(
                "message.recall",
                ProtocolResult("definite_failure", "invalid_message_id"),
            )
        return self._observe(
            "message.recall",
            await self._attempt(
                "delete_msg",
                respect_path_health=False,
                message_id=normalized_message_id,
            ),
        )

    async def export_cookies(self, *, domain: str) -> ProtocolResult:
        return self._observe(
            "qzone.cookie_export",
            await self._attempt("get_cookies", domain=str(domain or "")),
        )


@dataclass
class ProtocolAdapterRegistry:
    _items: dict[int, tuple[Any, str, ProtocolAdapter]] = field(default_factory=dict)

    def get(self, bot: Any, plugin_config: Any = None, logger: Any = None) -> ProtocolAdapter:
        key = id(bot)
        mode = _extension_mode(plugin_config)
        cached = self._items.get(key)
        if cached is not None and cached[0] is bot and cached[1] == mode:
            cached[2].plugin_config = plugin_config
            cached[2].logger = logger
            return cached[2]
        adapter = ProtocolAdapter(bot, plugin_config=plugin_config, logger=logger)
        self._items[key] = (bot, mode, adapter)
        return adapter

    def reset(self) -> None:
        self._items.clear()


_REGISTRY = ProtocolAdapterRegistry()


def get_protocol_adapter(bot: Any, plugin_config: Any = None, logger: Any = None) -> ProtocolAdapter:
    return _REGISTRY.get(bot, plugin_config=plugin_config, logger=logger)


def reset_protocol_adapters() -> None:
    _REGISTRY.reset()


__all__ = [
    "CapabilityMatrix",
    "CapabilityRecord",
    "CapabilityState",
    "ProtocolAdapter",
    "ProtocolAdapterRegistry",
    "ProtocolIdentity",
    "ProtocolResult",
    "get_protocol_adapter",
    "reset_protocol_adapters",
]
