from __future__ import annotations

import re
from typing import Any


_HEADER_PATTERN = re.compile(
    r"(?im)\b(authorization|proxy-authorization|cookie|set-cookie)\s*:\s*[^\r\n]*"
)
_KEY_VALUE_PATTERN = re.compile(
    r"(?i)([\"']?(?:api[_-]?key|access[_-]?token|refresh[_-]?token|token|csrf|"
    r"client[_-]?secret|password|passwd|session(?:[_-]?id)?|p_skey|skey)[\"']?\s*[:=]\s*)"
    r"(?:\"[^\"]*\"|'[^']*'|[^\s,;}&]+)"
)
_BEARER_PATTERN = re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/=-]+")
_OPENAI_KEY_PATTERN = re.compile(r"(?i)\b(sk-[a-z0-9_-]{8})[a-z0-9_-]+")
_SENSITIVE_KEY_PATTERN = re.compile(
    r"(?i)(api[_-]?key|key|token|secret|cookie|authorization|csrf|password|passwd|"
    r"session|p_skey|skey)"
)


def sanitize_text(value: Any, *, limit: int = 4000) -> str:
    text = str(value or "")
    if len(text) > limit:
        text = text[: max(0, limit - 12)] + "...<truncated>"
    text = _HEADER_PATTERN.sub(lambda match: f"{match.group(1)}: ***", text)
    text = _KEY_VALUE_PATTERN.sub(lambda match: f"{match.group(1)}***", text)
    text = _BEARER_PATTERN.sub("Bearer ***", text)
    text = _OPENAI_KEY_PATTERN.sub(r"\1***", text)
    return text


def sanitize_object(value: Any, *, depth: int = 0) -> Any:
    if depth > 5:
        return "<nested>"
    if isinstance(value, dict):
        output: dict[str, Any] = {}
        for key, item in list(value.items())[:80]:
            key_text = str(key or "")[:120]
            output[key_text] = "***" if _SENSITIVE_KEY_PATTERN.search(key_text) else sanitize_object(item, depth=depth + 1)
        return output
    if isinstance(value, (list, tuple, set)):
        return [sanitize_object(item, depth=depth + 1) for item in list(value)[:80]]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return sanitize_text(value)


def contains_sensitive_value(value: Any) -> bool:
    text = str(value or "")
    return bool(
        _HEADER_PATTERN.search(text)
        or _KEY_VALUE_PATTERN.search(text)
        or _BEARER_PATTERN.search(text)
    )


__all__ = ["contains_sensitive_value", "sanitize_object", "sanitize_text"]
