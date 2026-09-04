from __future__ import annotations

import asyncio
import copy
import hashlib
import ipaddress
import json
import math
import socket
import threading
import time
from datetime import datetime
from typing import Any, Callable
from urllib.parse import urlsplit, urlunsplit

import httpx


_UPSTREAM_USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
_CACHE_TTL_SECONDS = 60.0
_FORCE_COOLDOWN_SECONDS = 30.0
_CACHE_LOCK = threading.RLock()
_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_LAST_REQUEST_AT: dict[str, float] = {}
_VALID_STATUSES = {
    "available",
    "not_configured",
    "auth_failed",
    "upstream_failed",
    "unsupported",
    "stale",
}


def _route_fingerprint(provider: dict[str, Any], endpoint: str) -> str:
    management_key = str(provider.get("subscription_management_key", "") or "").strip()
    management_key_fingerprint = (
        hashlib.sha256(management_key.encode("utf-8")).hexdigest()
        if management_key
        else "missing"
    )
    material = "\x1f".join(
        (
            str(provider.get("name", "") or ""),
            str(provider.get("api_type", "") or ""),
            str(provider.get("model", "") or ""),
            endpoint,
            str(provider.get("subscription_auth_index", "") or ""),
            management_key_fingerprint,
        )
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]


def _normalize_management_endpoint(value: Any) -> tuple[str, str]:
    raw = str(value or "").strip()
    if not raw or len(raw) > 2048:
        return "", "subscription_management_url_missing"
    if any(ord(char) <= 32 or ord(char) == 127 for char in raw):
        return "", "subscription_management_url_invalid"
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except (TypeError, ValueError):
        return "", "subscription_management_url_invalid"
    scheme = parsed.scheme.lower()
    host = str(parsed.hostname or "").strip().lower()
    if scheme not in {"http", "https"} or not host:
        return "", "subscription_management_url_invalid"
    if parsed.username is not None or parsed.password is not None:
        return "", "subscription_management_url_userinfo_forbidden"
    if parsed.query or parsed.fragment:
        return "", "subscription_management_url_components_forbidden"
    if port is not None and not (1 <= port <= 65535):
        return "", "subscription_management_url_port_invalid"
    if scheme == "http" and host not in {"localhost", "127.0.0.1", "::1"}:
        return "", "subscription_management_url_https_required"
    path = (parsed.path or "").rstrip("/")
    if path.endswith("/v0/management/api-call"):
        endpoint_path = path
    elif path.endswith("/v0/management"):
        endpoint_path = f"{path}/api-call"
    else:
        endpoint_path = f"{path}/v0/management/api-call"
    netloc = parsed.netloc
    return urlunsplit((scheme, netloc, endpoint_path, "", "")), ""


def _resolve_host_addresses(host: str, port: int) -> list[str]:
    return sorted(
        {
            str(item[4][0])
            for item in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
            if item and len(item) > 4 and item[4]
        }
    )


async def _validate_resolved_endpoint(endpoint: str) -> tuple[bool, str]:
    parsed = urlsplit(endpoint)
    host = str(parsed.hostname or "").lower()
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        addresses = await asyncio.to_thread(_resolve_host_addresses, host, port)
    except Exception:
        return False, "subscription_management_dns_failed"
    if not addresses:
        return False, "subscription_management_dns_failed"
    for value in addresses:
        try:
            address = ipaddress.ip_address(value)
        except ValueError:
            return False, "subscription_management_address_invalid"
        if address.is_loopback:
            if host not in {"localhost", "127.0.0.1", "::1"}:
                return False, "subscription_management_loopback_alias_forbidden"
            continue
        if (
            address.is_private
            or address.is_link_local
            or address.is_multicast
            or address.is_reserved
            or address.is_unspecified
        ):
            return False, "subscription_management_private_address_forbidden"
    return True, ""


def _finite_percent(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(number):
        return None
    return round(max(0.0, min(100.0, number)), 2)


def _reset_timestamp(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        number = 0.0
    if math.isfinite(number) and number > 0:
        return number
    text = str(value or "").strip()
    if not text or len(text) > 64:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _window_type(seconds: int) -> str:
    if abs(seconds - 5 * 60 * 60) <= 60:
        return "five_hour"
    if abs(seconds - 7 * 24 * 60 * 60) <= 60:
        return "weekly"
    return "other"


def _parse_usage_windows(payload: Any) -> list[dict[str, Any]]:
    windows: list[dict[str, Any]] = []
    visited: set[int] = set()

    def _walk(value: Any) -> None:
        if isinstance(value, dict):
            if id(value) in visited:
                return
            visited.add(id(value))
            try:
                seconds = int(value.get("limit_window_seconds") or 0)
            except (TypeError, ValueError, OverflowError):
                seconds = 0
            if 0 < seconds <= 31 * 24 * 60 * 60:
                used = _finite_percent(
                    value.get("used_percent", value.get("used_percentage"))
                )
                remaining = _finite_percent(
                    value.get("remaining_percent", value.get("remaining_percentage"))
                )
                if used is None and remaining is not None:
                    used = round(100.0 - remaining, 2)
                if remaining is None and used is not None:
                    remaining = round(100.0 - used, 2)
                if used is not None and remaining is not None:
                    windows.append(
                        {
                            "window_type": _window_type(seconds),
                            "limit_window_seconds": seconds,
                            "used_percent": used,
                            "remaining_percent": remaining,
                            "reset_at": _reset_timestamp(
                                value.get("reset_at", value.get("reset_time"))
                            ),
                        }
                    )
            for nested in value.values():
                _walk(nested)
        elif isinstance(value, list):
            for nested in value[:64]:
                _walk(nested)

    _walk(payload)
    unique: dict[tuple[str, int], dict[str, Any]] = {}
    for window in windows:
        unique[(window["window_type"], window["limit_window_seconds"])] = window
    return sorted(
        unique.values(),
        key=lambda item: (item["limit_window_seconds"], item["window_type"]),
    )


def _snapshot(
    provider: dict[str, Any],
    *,
    fingerprint: str,
    status: str,
    code: str,
    checked_at: float,
    windows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    normalized_status = status if status in _VALID_STATUSES else "upstream_failed"
    return {
        "route_name": str(provider.get("name", "") or "subscription_route")[:80],
        "route_fingerprint": fingerprint,
        "status": normalized_status,
        "diagnostic_code": str(code or normalized_status)[:96],
        "checked_at": checked_at,
        "source": "codex_wham_proxy",
        "windows": list(windows or []),
    }


async def _fetch_route_snapshot(
    provider: dict[str, Any],
    *,
    now: float,
    client_factory: Callable[..., Any] = httpx.AsyncClient,
) -> tuple[str, dict[str, Any]]:
    endpoint, endpoint_error = _normalize_management_endpoint(
        provider.get("subscription_management_url")
    )
    fingerprint = _route_fingerprint(provider, endpoint)
    auth_index = str(provider.get("subscription_auth_index", "") or "").strip()
    management_key = str(provider.get("subscription_management_key", "") or "").strip()
    if endpoint_error or not auth_index or not management_key:
        return fingerprint, _snapshot(
            provider,
            fingerprint=fingerprint,
            status="not_configured",
            code=endpoint_error or "subscription_credentials_incomplete",
            checked_at=now,
        )
    resolved, resolve_error = await _validate_resolved_endpoint(endpoint)
    if not resolved:
        return fingerprint, _snapshot(
            provider,
            fingerprint=fingerprint,
            status="unsupported",
            code=resolve_error,
            checked_at=now,
        )
    request_payload = {
        "auth_index": auth_index[:128],
        "method": "GET",
        "url": _UPSTREAM_USAGE_URL,
        "header": {"Authorization": "Bearer $TOKEN$"},
        "data": "",
    }
    try:
        async with client_factory(
            timeout=12.0,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            response = await client.post(
                endpoint,
                headers={"Authorization": f"Bearer {management_key}"},
                json=request_payload,
            )
    except Exception:
        return fingerprint, _snapshot(
            provider,
            fingerprint=fingerprint,
            status="upstream_failed",
            code="subscription_management_request_failed",
            checked_at=now,
        )
    if response.status_code in {401, 403}:
        status, code = "auth_failed", "subscription_management_auth_failed"
    elif 300 <= response.status_code < 400:
        status, code = "upstream_failed", "subscription_management_redirect_rejected"
    elif response.status_code != 200:
        status, code = "upstream_failed", "subscription_management_http_failed"
    else:
        status, code = "available", "subscription_quota_available"
    if status != "available":
        return fingerprint, _snapshot(
            provider,
            fingerprint=fingerprint,
            status=status,
            code=code,
            checked_at=now,
        )
    content_length = response.headers.get("content-length")
    try:
        declared_size = int(content_length or 0)
    except (TypeError, ValueError, OverflowError):
        declared_size = 0
    if declared_size > 1024 * 1024 or len(response.content) > 1024 * 1024:
        return fingerprint, _snapshot(
            provider,
            fingerprint=fingerprint,
            status="unsupported",
            code="subscription_management_response_too_large",
            checked_at=now,
        )
    try:
        envelope = response.json()
    except Exception:
        envelope = None
    if not isinstance(envelope, dict):
        return fingerprint, _snapshot(
            provider,
            fingerprint=fingerprint,
            status="unsupported",
            code="subscription_management_response_invalid",
            checked_at=now,
        )
    try:
        upstream_status = int(envelope.get("status_code") or 0)
    except (TypeError, ValueError, OverflowError):
        upstream_status = 0
    if upstream_status in {401, 403}:
        return fingerprint, _snapshot(
            provider,
            fingerprint=fingerprint,
            status="auth_failed",
            code="subscription_upstream_auth_failed",
            checked_at=now,
        )
    if upstream_status != 200:
        return fingerprint, _snapshot(
            provider,
            fingerprint=fingerprint,
            status="upstream_failed",
            code="subscription_upstream_http_failed",
            checked_at=now,
        )
    body = envelope.get("body")
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except json.JSONDecodeError:
            body = None
    windows = _parse_usage_windows(body)
    if not windows:
        return fingerprint, _snapshot(
            provider,
            fingerprint=fingerprint,
            status="unsupported",
            code="subscription_usage_schema_unsupported",
            checked_at=now,
        )
    return fingerprint, _snapshot(
        provider,
        fingerprint=fingerprint,
        status="available",
        code="subscription_quota_available",
        checked_at=now,
        windows=windows,
    )


async def query_subscription_quotas(
    plugin_config: Any,
    *,
    force: bool = False,
    now: float | None = None,
    client_factory: Callable[..., Any] = httpx.AsyncClient,
) -> dict[str, Any]:
    checked_at = float(now if now is not None else time.time())
    raw = getattr(plugin_config, "personification_api_pools", None)
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            raw = []
    providers = [
        dict(item)
        for item in (raw if isinstance(raw, list) else [])
        if isinstance(item, dict)
        and bool(item.get("enabled", True))
        and str(item.get("account_mode", "api_key") or "api_key").strip().lower()
        == "subscription_proxy"
        and str(item.get("subscription_quota_kind", "none") or "none").strip().lower()
        == "codex_wham_proxy"
    ]
    items: list[dict[str, Any]] = []
    for provider in providers:
        endpoint, _ = _normalize_management_endpoint(provider.get("subscription_management_url"))
        fingerprint = _route_fingerprint(provider, endpoint)
        with _CACHE_LOCK:
            cached = _CACHE.get(fingerprint)
            last_request_at = _LAST_REQUEST_AT.get(fingerprint, 0.0)
        cache_age = checked_at - cached[0] if cached else float("inf")
        if cached and cache_age <= _CACHE_TTL_SECONDS and (
            not force or checked_at - last_request_at < _FORCE_COOLDOWN_SECONDS
        ):
            item = copy.deepcopy(cached[1])
            item["cached"] = True
            if force and checked_at - last_request_at < _FORCE_COOLDOWN_SECONDS:
                item["refresh_diagnostic_code"] = "subscription_force_refresh_cooldown"
            items.append(item)
            continue
        fingerprint, fresh = await _fetch_route_snapshot(
            provider,
            now=checked_at,
            client_factory=client_factory,
        )
        with _CACHE_LOCK:
            _LAST_REQUEST_AT[fingerprint] = checked_at
        cached_snapshot = cached[1] if cached else {}
        if (
            fresh["status"] not in {"available", "not_configured"}
            and cached
            and cached_snapshot.get("status") in {"available", "stale"}
            and bool(cached_snapshot.get("windows"))
        ):
            stale = copy.deepcopy(cached[1])
            stale.update(
                {
                    "status": "stale",
                    "diagnostic_code": fresh["diagnostic_code"],
                    "checked_at": checked_at,
                    "stale_since": cached[0],
                    "cached": True,
                }
            )
            items.append(stale)
            continue
        fresh["cached"] = False
        with _CACHE_LOCK:
            _CACHE[fingerprint] = (checked_at, copy.deepcopy(fresh))
        items.append(fresh)
    return {
        "items": items,
        "checked_at": checked_at,
        "cache_ttl_seconds": int(_CACHE_TTL_SECONDS),
        "force_cooldown_seconds": int(_FORCE_COOLDOWN_SECONDS),
        "diagnostic_code": (
            "subscription_quota_routes_available" if items else "subscription_quota_not_configured"
        ),
    }


def reset_subscription_quota_cache_for_testing() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()
        _LAST_REQUEST_AT.clear()


__all__ = [
    "query_subscription_quotas",
    "reset_subscription_quota_cache_for_testing",
]
