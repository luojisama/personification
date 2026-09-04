from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from dataclasses import dataclass, fields
from enum import Enum
from typing import Any, Mapping
from urllib.parse import urlsplit, urlunsplit


CAPABILITY_NAMES = (
    "image_input",
    "audio_input",
    "video_input",
    "reasoning",
    "function_call",
    "native_web_search",
    "external_network_access",
)


class CapabilityState(str, Enum):
    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    UNKNOWN = "unknown"


class VerificationState(str, Enum):
    """Freshness and outcome of the evidence behind a capability state.

    ``state`` deliberately stays a three-state compatibility contract.  This
    secondary field prevents a stale, absent, or inconclusive probe from being
    rendered as a successful capability check.
    """

    VERIFIED = "verified"
    NOT_RUN = "not_run"
    PROBE_UNAVAILABLE = "probe_unavailable"
    INCONCLUSIVE = "inconclusive"
    STALE = "stale"


class CapabilitySource(str, Enum):
    MANUAL = "manual"
    RUNTIME_SUCCESS = "runtime_success"
    PROBE = "probe"
    PROVIDER_CATALOG = "provider_catalog"
    MODEL_CATALOG = "model_catalog"
    HEURISTIC = "heuristic"


class CapabilityObservation(str, Enum):
    SUCCESS = "success"
    EXPLICIT_UNSUPPORTED = "explicit_unsupported"
    PROBE_UNAVAILABLE = "probe_unavailable"
    TIMEOUT = "timeout"
    NETWORK_ERROR = "network_error"
    SERVER_ERROR = "server_error"
    PARSE_ERROR = "parse_error"
    PROVIDER_REJECTED = "provider_rejected"
    EMPTY_RESPONSE = "empty_response"


_SOURCE_PRIORITY = {
    CapabilitySource.MANUAL: 60,
    CapabilitySource.RUNTIME_SUCCESS: 50,
    CapabilitySource.PROBE: 40,
    CapabilitySource.PROVIDER_CATALOG: 40,
    CapabilitySource.MODEL_CATALOG: 30,
    CapabilitySource.HEURISTIC: 20,
}

_DEFAULT_TTL_SECONDS: dict[CapabilitySource, float | None] = {
    CapabilitySource.MANUAL: None,
    CapabilitySource.RUNTIME_SUCCESS: 7 * 24 * 60 * 60,
    CapabilitySource.PROBE: 24 * 60 * 60,
    CapabilitySource.PROVIDER_CATALOG: 24 * 60 * 60,
    CapabilitySource.MODEL_CATALOG: 24 * 60 * 60,
    CapabilitySource.HEURISTIC: 60 * 60,
}

_OBSERVATION_STATE = {
    CapabilityObservation.SUCCESS: CapabilityState.SUPPORTED,
    CapabilityObservation.EXPLICIT_UNSUPPORTED: CapabilityState.UNSUPPORTED,
    CapabilityObservation.PROBE_UNAVAILABLE: CapabilityState.UNKNOWN,
    CapabilityObservation.TIMEOUT: CapabilityState.UNKNOWN,
    CapabilityObservation.NETWORK_ERROR: CapabilityState.UNKNOWN,
    CapabilityObservation.SERVER_ERROR: CapabilityState.UNKNOWN,
    CapabilityObservation.PARSE_ERROR: CapabilityState.UNKNOWN,
    CapabilityObservation.PROVIDER_REJECTED: CapabilityState.UNKNOWN,
    CapabilityObservation.EMPTY_RESPONSE: CapabilityState.UNKNOWN,
}

_OBSERVATION_VERIFICATION_STATE = {
    CapabilityObservation.SUCCESS: VerificationState.VERIFIED,
    CapabilityObservation.EXPLICIT_UNSUPPORTED: VerificationState.VERIFIED,
    CapabilityObservation.PROBE_UNAVAILABLE: VerificationState.PROBE_UNAVAILABLE,
    CapabilityObservation.TIMEOUT: VerificationState.INCONCLUSIVE,
    CapabilityObservation.NETWORK_ERROR: VerificationState.INCONCLUSIVE,
    CapabilityObservation.SERVER_ERROR: VerificationState.INCONCLUSIVE,
    CapabilityObservation.PARSE_ERROR: VerificationState.INCONCLUSIVE,
    CapabilityObservation.PROVIDER_REJECTED: VerificationState.INCONCLUSIVE,
    CapabilityObservation.EMPTY_RESPONSE: VerificationState.INCONCLUSIVE,
}

_DETAIL_CODE_RE = re.compile(r"[^a-z0-9_.:-]+")


def _normalize_atom(value: Any, *, replace_hyphen: bool = False) -> str:
    normalized = str(value or "").strip().lower()
    return normalized.replace("-", "_") if replace_hyphen else normalized


def _normalize_detail_code(value: Any, *, fallback: str) -> str:
    code = _DETAIL_CODE_RE.sub("_", str(value or "").strip().lower()).strip("_.:-")
    return (code or fallback)[:96]


def _normalized_api_url_for_hash(api_url: Any) -> str:
    raw = str(api_url or "").strip()
    if not raw:
        return "<default>"
    try:
        parsed = urlsplit(raw)
        scheme = parsed.scheme.lower()
        hostname = str(parsed.hostname or "").lower().rstrip(".")
        if not scheme or not hostname:
            raise ValueError("not an absolute URL")
        try:
            port = parsed.port
        except ValueError:
            port = None
        default_port = (scheme == "https" and port == 443) or (scheme == "http" and port == 80)
        rendered_host = f"[{hostname}]" if ":" in hostname and not hostname.startswith("[") else hostname
        netloc = rendered_host if port is None or default_port else f"{rendered_host}:{port}"
        path = parsed.path or ""
        if path != "/":
            path = path.rstrip("/")
        # Query values can affect a deployment route (for example an API version),
        # so they remain part of the one-way hash. Userinfo and fragments never do.
        return urlunsplit((scheme, netloc, path, parsed.query, ""))
    except Exception:
        # Even malformed compatibility endpoints are represented only by a hash.
        return raw.split("#", 1)[0].rstrip("/")


def api_url_fingerprint(api_url: Any) -> str:
    normalized = _normalized_api_url_for_hash(api_url)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]


@dataclass(frozen=True, slots=True)
class RouteKey:
    provider: str
    api_type: str
    api_url_fingerprint: str
    model: str
    media_protocol: str

    @classmethod
    def from_config(
        cls,
        *,
        provider: Any,
        api_type: Any,
        api_url: Any,
        model: Any,
        media_protocol: Any = "auto",
    ) -> "RouteKey":
        return cls(
            provider=_normalize_atom(provider) or "provider",
            api_type=_normalize_atom(api_type, replace_hyphen=True) or "unknown",
            api_url_fingerprint=api_url_fingerprint(api_url),
            model=_normalize_atom(model),
            media_protocol=_normalize_atom(media_protocol, replace_hyphen=True) or "auto",
        )

    @property
    def fingerprint(self) -> str:
        payload = json.dumps(
            self.to_safe_dict(),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]

    def to_safe_dict(self) -> dict[str, str]:
        return {
            "provider": self.provider,
            "api_type": self.api_type,
            "api_url_fingerprint": self.api_url_fingerprint,
            "model": self.model,
            "media_protocol": self.media_protocol,
        }


@dataclass(frozen=True, slots=True)
class RouteCapability:
    state: CapabilityState
    source: CapabilitySource
    checked_at: float | None
    expires_at: float | None
    detail_code: str
    verification_state: VerificationState = VerificationState.NOT_RUN

    def is_expired(self, now: float | None = None) -> bool:
        return self.expires_at is not None and self.expires_at <= (
            time.time() if now is None else float(now)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "verification_state": self.verification_state.value,
            "source": self.source.value,
            "checked_at": self.checked_at,
            "expires_at": self.expires_at,
            "detail_code": self.detail_code,
        }


def _unknown_capability() -> RouteCapability:
    return RouteCapability(
        state=CapabilityState.UNKNOWN,
        source=CapabilitySource.HEURISTIC,
        checked_at=None,
        expires_at=None,
        detail_code="capability_unverified",
        verification_state=VerificationState.NOT_RUN,
    )


def _default_verification_state(
    *,
    state: CapabilityState,
    source: CapabilitySource,
) -> VerificationState:
    if source == CapabilitySource.RUNTIME_SUCCESS:
        return VerificationState.VERIFIED
    if source == CapabilitySource.PROBE:
        return (
            VerificationState.VERIFIED
            if state in {CapabilityState.SUPPORTED, CapabilityState.UNSUPPORTED}
            else VerificationState.INCONCLUSIVE
        )
    return VerificationState.NOT_RUN


def _stale_capability(record: RouteCapability) -> RouteCapability:
    """Keep a safe historical hint while declining to assert current support."""

    return RouteCapability(
        state=CapabilityState.UNKNOWN,
        source=record.source,
        checked_at=record.checked_at,
        expires_at=record.expires_at,
        detail_code=record.detail_code,
        verification_state=VerificationState.STALE,
    )


@dataclass(frozen=True, slots=True)
class RouteCapabilities:
    image_input: RouteCapability
    audio_input: RouteCapability
    video_input: RouteCapability
    reasoning: RouteCapability
    function_call: RouteCapability
    native_web_search: RouteCapability
    external_network_access: RouteCapability

    @classmethod
    def from_mapping(cls, values: Mapping[str, RouteCapability]) -> "RouteCapabilities":
        return cls(**{name: values.get(name, _unknown_capability()) for name in CAPABILITY_NAMES})

    def to_dict(self) -> dict[str, dict[str, Any]]:
        return {field.name: getattr(self, field.name).to_dict() for field in fields(self)}


class RouteCapabilityRegistry:
    """Thread-safe, route-scoped capability evidence registry.

    Evidence is retained per source so a temporary probe failure cannot erase a
    stronger manual override or a successful real request on the same route.
    The registry stores no API URL or credential, only a one-way route identity.
    """

    def __init__(self, *, clock: Any = time.time) -> None:
        self._clock = clock
        self._lock = threading.RLock()
        self._evidence: dict[
            tuple[RouteKey, str, CapabilitySource], RouteCapability
        ] = {}
        self._route_bindings: dict[str, RouteKey] = {}

    @staticmethod
    def _capability_name(capability: Any) -> str:
        name = str(capability or "").strip().lower()
        if name not in CAPABILITY_NAMES:
            raise ValueError(f"unsupported route capability: {name or '<empty>'}")
        return name

    def bind_route(self, route_name: Any, route_key: RouteKey) -> bool:
        """Bind a logical route and invalidate orphaned evidence after changes.

        Returns True when the route identity changed. Identical route identities
        share runtime-success evidence even if they have different logical names.
        """

        name = _normalize_atom(route_name)
        if not name:
            raise ValueError("route_name is required")
        if not isinstance(route_key, RouteKey):
            raise TypeError("route_key must be a RouteKey")
        with self._lock:
            previous = self._route_bindings.get(name)
            if previous == route_key:
                return False
            self._route_bindings[name] = route_key
            if previous is not None and previous not in self._route_bindings.values():
                self._remove_route_evidence_locked(previous)
            return True

    def configure_route(
        self,
        route_name: Any,
        *,
        provider: Any,
        api_type: Any,
        api_url: Any,
        model: Any,
        media_protocol: Any = "auto",
    ) -> RouteKey:
        key = RouteKey.from_config(
            provider=provider,
            api_type=api_type,
            api_url=api_url,
            model=model,
            media_protocol=media_protocol,
        )
        self.bind_route(route_name, key)
        return key

    def route_key(self, route_name: Any) -> RouteKey | None:
        with self._lock:
            return self._route_bindings.get(_normalize_atom(route_name))

    def record(
        self,
        route_key: RouteKey,
        capability: Any,
        *,
        state: CapabilityState | str,
        source: CapabilitySource | str,
        detail_code: Any = "",
        checked_at: float | None = None,
        expires_at: float | None = None,
        ttl_seconds: float | None = None,
        verification_state: VerificationState | str | None = None,
    ) -> RouteCapability:
        name = self._capability_name(capability)
        resolved_state = state if isinstance(state, CapabilityState) else CapabilityState(str(state))
        resolved_source = source if isinstance(source, CapabilitySource) else CapabilitySource(str(source))
        resolved_verification_state = (
            _default_verification_state(state=resolved_state, source=resolved_source)
            if verification_state is None
            else (
                verification_state
                if isinstance(verification_state, VerificationState)
                else VerificationState(str(verification_state))
            )
        )
        if resolved_source == CapabilitySource.RUNTIME_SUCCESS and resolved_state != CapabilityState.SUPPORTED:
            raise ValueError("runtime_success evidence must be supported")
        now = float(self._clock() if checked_at is None else checked_at)
        if expires_at is not None and ttl_seconds is not None:
            raise ValueError("expires_at and ttl_seconds are mutually exclusive")
        if expires_at is None:
            ttl = _DEFAULT_TTL_SECONDS[resolved_source] if ttl_seconds is None else ttl_seconds
            expiry = None if ttl is None else now + max(0.0, float(ttl))
        else:
            expiry = float(expires_at)
        record = RouteCapability(
            state=resolved_state,
            source=resolved_source,
            checked_at=now,
            expires_at=expiry,
            detail_code=_normalize_detail_code(
                detail_code,
                fallback=f"{resolved_source.value}_{resolved_state.value}",
            ),
            verification_state=resolved_verification_state,
        )
        with self._lock:
            self._evidence[(route_key, name, resolved_source)] = record
        return record

    def record_observation(
        self,
        route_key: RouteKey,
        capability: Any,
        observation: CapabilityObservation | str,
        *,
        source: CapabilitySource | str = CapabilitySource.PROBE,
        detail_code: Any = "",
        checked_at: float | None = None,
        ttl_seconds: float | None = None,
    ) -> RouteCapability:
        resolved_observation = (
            observation
            if isinstance(observation, CapabilityObservation)
            else CapabilityObservation(str(observation))
        )
        resolved_source = source if isinstance(source, CapabilitySource) else CapabilitySource(str(source))
        state = _OBSERVATION_STATE[resolved_observation]
        if resolved_source == CapabilitySource.RUNTIME_SUCCESS and resolved_observation != CapabilityObservation.SUCCESS:
            raise ValueError("runtime_success only accepts a successful observation")
        return self.record(
            route_key,
            capability,
            state=state,
            source=resolved_source,
            detail_code=detail_code or f"capability_{resolved_observation.value}",
            checked_at=checked_at,
            ttl_seconds=ttl_seconds,
            verification_state=_OBSERVATION_VERIFICATION_STATE[resolved_observation],
        )

    def record_runtime_success(
        self,
        route_key: RouteKey,
        capability: Any,
        *,
        detail_code: Any = "runtime_call_succeeded",
        checked_at: float | None = None,
    ) -> RouteCapability:
        return self.record_observation(
            route_key,
            capability,
            CapabilityObservation.SUCCESS,
            source=CapabilitySource.RUNTIME_SUCCESS,
            detail_code=detail_code,
            checked_at=checked_at,
        )

    def record_manual_override(
        self,
        route_key: RouteKey,
        capability: Any,
        state: CapabilityState | str,
        *,
        detail_code: Any = "manual_override",
        checked_at: float | None = None,
    ) -> RouteCapability:
        return self.record(
            route_key,
            capability,
            state=state,
            source=CapabilitySource.MANUAL,
            detail_code=detail_code,
            checked_at=checked_at,
        )

    def get(
        self,
        route_key: RouteKey,
        capability: Any,
        *,
        now: float | None = None,
    ) -> RouteCapability:
        name = self._capability_name(capability)
        current = float(self._clock() if now is None else now)
        candidates: list[RouteCapability] = []
        expired_candidates: list[RouteCapability] = []
        with self._lock:
            for key, record in self._evidence.items():
                if key[0] != route_key or key[1] != name:
                    continue
                if record.is_expired(current):
                    expired_candidates.append(record)
                    continue
                candidates.append(record)
        if not candidates:
            if expired_candidates:
                return _stale_capability(
                    max(
                        expired_candidates,
                        key=lambda item: (
                            _SOURCE_PRIORITY[item.source],
                            float(item.checked_at or 0.0),
                        ),
                    )
                )
            return _unknown_capability()
        return max(
            candidates,
            key=lambda item: (
                _SOURCE_PRIORITY[item.source],
                float(item.checked_at or 0.0),
            ),
        )

    def get_capabilities(
        self,
        route_key: RouteKey,
        *,
        now: float | None = None,
    ) -> RouteCapabilities:
        return RouteCapabilities.from_mapping(
            {name: self.get(route_key, name, now=now) for name in CAPABILITY_NAMES}
        )

    def clear_source(
        self,
        route_key: RouteKey,
        capability: Any,
        source: CapabilitySource | str,
    ) -> bool:
        name = self._capability_name(capability)
        resolved_source = source if isinstance(source, CapabilitySource) else CapabilitySource(str(source))
        with self._lock:
            return self._evidence.pop((route_key, name, resolved_source), None) is not None

    def invalidate_route(self, route_key: RouteKey) -> int:
        with self._lock:
            removed = self._remove_route_evidence_locked(route_key)
            self._route_bindings = {
                name: key for name, key in self._route_bindings.items() if key != route_key
            }
            return removed

    def _remove_route_evidence_locked(self, route_key: RouteKey) -> int:
        targets = [key for key in self._evidence if key[0] == route_key]
        for key in targets:
            self._evidence.pop(key, None)
        return len(targets)

    def clear(self) -> None:
        with self._lock:
            self._evidence.clear()
            self._route_bindings.clear()

    def snapshot(self, *, now: float | None = None) -> list[dict[str, Any]]:
        with self._lock:
            bindings = tuple(sorted(self._route_bindings.items()))
        return [
            {
                "route_name": name,
                "route": key.to_safe_dict(),
                "route_fingerprint": key.fingerprint,
                "capabilities": self.get_capabilities(key, now=now).to_dict(),
            }
            for name, key in bindings
        ]


DEFAULT_ROUTE_CAPABILITY_REGISTRY = RouteCapabilityRegistry()


__all__ = [
    "CAPABILITY_NAMES",
    "CapabilityObservation",
    "CapabilitySource",
    "CapabilityState",
    "VerificationState",
    "DEFAULT_ROUTE_CAPABILITY_REGISTRY",
    "RouteCapabilities",
    "RouteCapability",
    "RouteCapabilityRegistry",
    "RouteKey",
    "api_url_fingerprint",
]
