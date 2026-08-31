from __future__ import annotations

import hashlib
import hmac
import json
import os
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Awaitable, Callable
from urllib.parse import urlsplit, urlunsplit

import httpx

from .sensitive_data import sanitize_text


GEMINI_AUTH_AUTO = "auto"
GEMINI_AUTH_HEADER = "x-goog-api-key"
GEMINI_AUTH_BEARER = "bearer"
GEMINI_AUTH_QUERY_LEGACY = "query_legacy"

_AUTH_CACHE_MAX_SIZE = 128
_AUTH_CACHE_TTL_SECONDS = 12 * 60 * 60
_AUTH_CACHE_HMAC_KEY = os.urandom(32)
_AUTH_CACHE: "OrderedDict[str, tuple[str, float]]" = OrderedDict()
_AUTH_CACHE_LOCK = threading.RLock()
_ERROR_DIAGNOSTIC_BODY_LIMIT = 64 * 1024

_UPSTREAM_STATUS_ALLOWLIST = {
    "ABORTED",
    "ALREADY_EXISTS",
    "CANCELLED",
    "DATA_LOSS",
    "DEADLINE_EXCEEDED",
    "FAILED_PRECONDITION",
    "INTERNAL",
    "INVALID_ARGUMENT",
    "NOT_FOUND",
    "OUT_OF_RANGE",
    "PERMISSION_DENIED",
    "RESOURCE_EXHAUSTED",
    "UNAUTHENTICATED",
    "UNAVAILABLE",
    "UNIMPLEMENTED",
    "UNKNOWN",
}
_UPSTREAM_REASON_ALLOWLIST = {
    "API_KEY_INVALID",
    "ACCESS_TOKEN_EXPIRED",
    "BILLING_DISABLED",
    "MODEL_NOT_FOUND",
    "RATE_LIMIT_EXCEEDED",
    "SERVICE_DISABLED",
}
_UPSTREAM_DETAIL_CODES = {
    "context_limit_exceeded",
    "function_response_count_mismatch",
    "function_response_mismatch",
    "function_response_order_invalid",
    "invalid_argument",
    "request_too_large",
    "thought_signature_invalid",
    "tool_schema_rejected",
    "upstream_rejected",
}


@dataclass(frozen=True, slots=True)
class GeminiAuthPayload:
    mode: str
    headers: dict[str, str]
    params: dict[str, str]


@dataclass(frozen=True, slots=True)
class GeminiAuthResult:
    response: Any
    mode: str
    request_count: int


def _gemini_error_detail_code(message: Any, status: str) -> str:
    """Classify a Gemini error without retaining the provider's raw message."""

    lowered = str(message or "").strip().lower()
    if "thought signature" in lowered or "thoughtsignature" in lowered:
        return "thought_signature_invalid"
    if "function response" in lowered:
        if any(marker in lowered for marker in ("immediately after", "must follow", "preceding function call")):
            return "function_response_order_invalid"
        if any(marker in lowered for marker in ("number of", "same number", "count", "missing response")):
            return "function_response_count_mismatch"
        if any(marker in lowered for marker in ("does not match", "mismatch", "call id", "function call id", "name")):
            return "function_response_mismatch"
    if any(marker in lowered for marker in ("request too large", "payload too large", "request size")):
        return "request_too_large"
    if any(marker in lowered for marker in ("context length", "context window", "token limit")):
        return "context_limit_exceeded"
    if "schema" in lowered and any(marker in lowered for marker in ("invalid", "unsupported", "reject")):
        return "tool_schema_rejected"
    if status == "INVALID_ARGUMENT":
        return "invalid_argument"
    return "upstream_rejected"


def _gemini_error_diagnostics(response: Any) -> dict[str, str]:
    try:
        raw_body = bytes(getattr(response, "content", b"") or b"")
        if not raw_body or len(raw_body) > _ERROR_DIAGNOSTIC_BODY_LIMIT:
            payload = {}
        else:
            payload = json.loads(raw_body)
    except Exception:
        payload = {}
    error = payload.get("error") if isinstance(payload, dict) else {}
    error = error if isinstance(error, dict) else {}
    raw_status = str(error.get("status") or "").strip().upper()
    status = raw_status if raw_status in _UPSTREAM_STATUS_ALLOWLIST else ""
    reason = ""
    details = error.get("details")
    if isinstance(details, list):
        for item in details[:8]:
            if not isinstance(item, dict):
                continue
            candidate = str(item.get("reason") or "").strip().upper()
            if candidate in _UPSTREAM_REASON_ALLOWLIST:
                reason = candidate
                break
    detail_code = _gemini_error_detail_code(error.get("message"), status)
    return {
        "upstream_status": status,
        "upstream_reason": reason,
        "upstream_detail_code": detail_code if detail_code in _UPSTREAM_DETAIL_CODES else "upstream_rejected",
    }


def safe_upstream_diagnostics(error: BaseException) -> dict[str, str]:
    """Return only allowlisted provider diagnostics from an exception chain."""

    current: BaseException | None = error
    seen: set[int] = set()
    result = {"upstream_status": "", "upstream_reason": "", "upstream_detail_code": ""}
    while current is not None and id(current) not in seen and len(seen) < 6:
        seen.add(id(current))
        status = str(getattr(current, "upstream_status", "") or "").strip().upper()
        reason = str(getattr(current, "upstream_reason", "") or "").strip().upper()
        detail_code = str(getattr(current, "upstream_detail_code", "") or "").strip().lower()
        if not result["upstream_status"] and status in _UPSTREAM_STATUS_ALLOWLIST:
            result["upstream_status"] = status
        if not result["upstream_reason"] and reason in _UPSTREAM_REASON_ALLOWLIST:
            result["upstream_reason"] = reason
        if not result["upstream_detail_code"] and detail_code in _UPSTREAM_DETAIL_CODES:
            result["upstream_detail_code"] = detail_code
        current = current.__cause__ or current.__context__
    return result


def normalize_gemini_auth_mode(value: Any) -> str:
    normalized = str(value or "").strip().lower().replace("-", "_")
    aliases = {
        "": GEMINI_AUTH_AUTO,
        "default": GEMINI_AUTH_AUTO,
        "auto": GEMINI_AUTH_AUTO,
        "x_goog_api_key": GEMINI_AUTH_HEADER,
        "x_google_api_key": GEMINI_AUTH_HEADER,
        "header": GEMINI_AUTH_HEADER,
        "google": GEMINI_AUTH_HEADER,
        "bearer": GEMINI_AUTH_BEARER,
        "authorization": GEMINI_AUTH_BEARER,
        "query": GEMINI_AUTH_QUERY_LEGACY,
        "query_legacy": GEMINI_AUTH_QUERY_LEGACY,
    }
    return aliases.get(normalized, GEMINI_AUTH_AUTO)


def is_google_gemini_endpoint(url: str) -> bool:
    try:
        hostname = str(urlsplit(str(url or "").strip()).hostname or "").lower()
    except Exception:
        return False
    return hostname == "generativelanguage.googleapis.com" or hostname.endswith(
        ".generativelanguage.googleapis.com"
    )


def gemini_auth_payload(api_key: str, mode: str) -> GeminiAuthPayload:
    selected = normalize_gemini_auth_mode(mode)
    if selected == GEMINI_AUTH_AUTO:
        selected = GEMINI_AUTH_HEADER
    key = str(api_key or "").strip()
    headers: dict[str, str] = {}
    params: dict[str, str] = {}
    if key:
        if selected == GEMINI_AUTH_BEARER:
            headers["Authorization"] = f"Bearer {key}"
        elif selected == GEMINI_AUTH_QUERY_LEGACY:
            params["key"] = key
        else:
            headers["x-goog-api-key"] = key
    return GeminiAuthPayload(mode=selected, headers=headers, params=params)


def _cache_key(endpoint: str, api_key: str) -> str:
    endpoint_text = str(endpoint or "").strip().rstrip("/")
    try:
        parsed = urlsplit(endpoint_text)
        hostname = str(parsed.hostname or "").lower()
        rendered_host = f"[{hostname}]" if ":" in hostname and not hostname.startswith("[") else hostname
        port = f":{parsed.port}" if parsed.port else ""
        endpoint_text = urlunsplit(
            (parsed.scheme.lower(), f"{rendered_host}{port}", parsed.path.rstrip("/"), "", "")
        )
    except (TypeError, ValueError):
        pass
    key_digest = hmac.new(
        _AUTH_CACHE_HMAC_KEY,
        str(api_key or "").encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return hashlib.sha256(f"{endpoint_text}\0{key_digest}".encode("utf-8")).hexdigest()


def _cached_mode(endpoint: str, api_key: str) -> str:
    key = _cache_key(endpoint, api_key)
    now = time.monotonic()
    with _AUTH_CACHE_LOCK:
        cached = _AUTH_CACHE.get(key)
        if cached is None:
            return ""
        mode, stored_at = cached
        if now - stored_at > _AUTH_CACHE_TTL_SECONDS:
            _AUTH_CACHE.pop(key, None)
            return ""
        _AUTH_CACHE.move_to_end(key)
        return mode


def _remember_mode(endpoint: str, api_key: str, mode: str) -> None:
    selected = normalize_gemini_auth_mode(mode)
    if selected not in {GEMINI_AUTH_HEADER, GEMINI_AUTH_BEARER}:
        return
    key = _cache_key(endpoint, api_key)
    with _AUTH_CACHE_LOCK:
        _AUTH_CACHE[key] = (selected, time.monotonic())
        _AUTH_CACHE.move_to_end(key)
        while len(_AUTH_CACHE) > _AUTH_CACHE_MAX_SIZE:
            _AUTH_CACHE.popitem(last=False)


def clear_gemini_auth_cache() -> None:
    with _AUTH_CACHE_LOCK:
        _AUTH_CACHE.clear()


async def request_with_gemini_auth(
    *,
    endpoint: str,
    api_key: str,
    auth_mode: str,
    send: Callable[[GeminiAuthPayload], Awaitable[Any]],
    allow_negotiation: bool = True,
) -> GeminiAuthResult:
    configured = normalize_gemini_auth_mode(auth_mode)
    selected = configured
    if configured == GEMINI_AUTH_AUTO:
        selected = _cached_mode(endpoint, api_key) or GEMINI_AUTH_HEADER

    first_payload = gemini_auth_payload(api_key, selected)
    response = await send(first_payload)
    request_count = 1
    status_code = int(getattr(response, "status_code", 0) or 0)
    if 200 <= status_code < 300 and configured == GEMINI_AUTH_AUTO:
        _remember_mode(endpoint, api_key, first_payload.mode)

    if configured == GEMINI_AUTH_AUTO and allow_negotiation and status_code == 401:
        alternate = GEMINI_AUTH_BEARER if first_payload.mode != GEMINI_AUTH_BEARER else GEMINI_AUTH_HEADER
        alternate_payload = gemini_auth_payload(api_key, alternate)
        response = await send(alternate_payload)
        request_count += 1
        status_code = int(getattr(response, "status_code", 0) or 0)
        if 200 <= status_code < 300:
            _remember_mode(endpoint, api_key, alternate_payload.mode)
        selected = alternate_payload.mode
    else:
        selected = first_payload.mode

    return GeminiAuthResult(
        response=response,
        mode=selected,
        request_count=request_count,
    )


def raise_for_gemini_status(
    response: httpx.Response,
    *,
    auth_mode: str = "",
    request_count: int = 1,
) -> None:
    if int(getattr(response, "status_code", 0) or 0) < 300:
        return
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        upstream_diagnostics = _gemini_error_diagnostics(exc.response)
        safe_url = sanitize_text(str(exc.request.url))
        safe_request = httpx.Request(exc.request.method, safe_url)
        safe_headers: dict[str, str] = {}
        if "retry-after" in exc.response.headers:
            safe_headers["Retry-After"] = sanitize_text(exc.response.headers["retry-after"])
        safe_response = httpx.Response(
            exc.response.status_code,
            headers=safe_headers,
            request=safe_request,
        )
        error = httpx.HTTPStatusError(
            f"Gemini upstream returned HTTP {exc.response.status_code} for {safe_url}",
            request=safe_request,
            response=safe_response,
        )
        error.auth_mode = normalize_gemini_auth_mode(auth_mode)
        error.request_count = max(1, int(request_count or 1))
        for key, value in upstream_diagnostics.items():
            setattr(error, key, value)
        raise error from None


__all__ = [
    "GEMINI_AUTH_AUTO",
    "GEMINI_AUTH_BEARER",
    "GEMINI_AUTH_HEADER",
    "GEMINI_AUTH_QUERY_LEGACY",
    "GeminiAuthPayload",
    "GeminiAuthResult",
    "clear_gemini_auth_cache",
    "gemini_auth_payload",
    "is_google_gemini_endpoint",
    "normalize_gemini_auth_mode",
    "raise_for_gemini_status",
    "request_with_gemini_auth",
    "safe_upstream_diagnostics",
]
