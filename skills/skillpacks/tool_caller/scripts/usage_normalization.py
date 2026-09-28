from __future__ import annotations

from typing import Any

_USAGE_PROMPT_KEYS = ("prompt_tokens", "input_tokens", "promptTokenCount", "promptTokens")
_USAGE_COMPLETION_KEYS = ("completion_tokens", "output_tokens", "candidatesTokenCount", "completionTokens", "outputTokens")
_USAGE_TOTAL_KEYS = ("total_tokens", "totalTokenCount", "totalTokens")
_USAGE_CONTAINER_KEYS = ("usage", "usageMetadata", "usage_metadata")


def _read_usage_value(source: Any, keys: tuple[str, ...]) -> int:
    """从 dict / pydantic 对象任一支持的键名读 token 计数。"""
    if isinstance(source, dict):
        for key in keys:
            if key in source and source[key] is not None:
                try:
                    return int(source[key])
                except (TypeError, ValueError):
                    return 0
        return 0
    for key in keys:
        value = getattr(source, key, None)
        if value is not None:
            try:
                return int(value)
            except (TypeError, ValueError):
                return 0
    return 0


def _read_optional_usage_value(source: Any, keys: tuple[str, ...]) -> int | None:
    """Read a provider counter without conflating absence with an explicit zero."""
    for key in keys:
        if isinstance(source, dict):
            if key not in source or source[key] is None:
                continue
            value = source[key]
        else:
            value = getattr(source, key, None)
            if value is None:
                continue
        if isinstance(value, bool) or not isinstance(value, (int, str)):
            return None
        if isinstance(value, str) and not value.strip().isdigit():
            return None
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return None
        return parsed if parsed >= 0 else None
    return None


def _read_usage_child(source: Any, keys: tuple[str, ...]) -> Any:
    for key in keys:
        if isinstance(source, dict):
            if key in source and source[key] is not None:
                return source[key]
        else:
            value = getattr(source, key, None)
            if value is not None:
                return value
    return None


def _extract_cache_usage(usage_obj: Any) -> dict[str, Any]:
    """Normalize only cache counters explicitly reported by the provider.

    A missing field stays missing so downstream telemetry cannot turn an
    unsupported response shape into a false cache miss.  An explicit zero is
    retained because it is a provider-confirmed miss for that response.
    """

    openai_details = _read_usage_child(
        usage_obj,
        ("prompt_tokens_details", "input_tokens_details", "promptTokensDetails", "inputTokensDetails"),
    )
    openai_read = _read_optional_usage_value(openai_details, ("cached_tokens", "cachedTokens"))
    if openai_read is not None:
        return {
            "cache_provider": "openai",
            "cache_read_input_tokens": openai_read,
        }

    anthropic_read = _read_optional_usage_value(
        usage_obj, ("cache_read_input_tokens", "cacheReadInputTokens")
    )
    anthropic_create = _read_optional_usage_value(
        usage_obj, ("cache_creation_input_tokens", "cacheCreationInputTokens")
    )
    if anthropic_read is not None or anthropic_create is not None:
        result: dict[str, Any] = {"cache_provider": "anthropic"}
        if anthropic_read is not None:
            result["cache_read_input_tokens"] = anthropic_read
        if anthropic_create is not None:
            result["cache_creation_input_tokens"] = anthropic_create
        creation = _read_usage_child(usage_obj, ("cache_creation",))
        for source_key, target_key in (
            ("ephemeral_5m_input_tokens", "cache_creation_5m_input_tokens"),
            ("ephemeral_1h_input_tokens", "cache_creation_1h_input_tokens"),
        ):
            count = _read_optional_usage_value(creation, (source_key,))
            if count is not None:
                result[target_key] = count
        return result

    gemini_read = _read_optional_usage_value(
        usage_obj, ("cachedContentTokenCount", "cached_content_token_count")
    )
    if gemini_read is not None:
        return {
            "cache_provider": "gemini",
            "cache_read_input_tokens": gemini_read,
        }
    return {}


def _extract_usage(response: Any) -> dict:
    """从任意 LLM provider 响应提取 token 用量。

    兼容三种字段命名族：
      - OpenAI:    prompt_tokens / completion_tokens / total_tokens
      - Anthropic: input_tokens / output_tokens
      - Gemini:    promptTokenCount / candidatesTokenCount / totalTokenCount

    兼容三种容器键：response.usage / response.usageMetadata / response.usage_metadata
    （chat.completions、Responses、generateContent 三套 API 各用一种）

    response 可以是 dict、Pydantic 对象、嵌套结构。无法定位时返回 {}。
    """
    try:
        usage_obj: Any = None
        for container_key in _USAGE_CONTAINER_KEYS:
            if isinstance(response, dict):
                if container_key in response and response[container_key] is not None:
                    usage_obj = response[container_key]
                    break
            else:
                candidate = getattr(response, container_key, None)
                if candidate is not None:
                    usage_obj = candidate
                    break
        if usage_obj is None:
            return {}
        prompt = _read_usage_value(usage_obj, _USAGE_PROMPT_KEYS)
        completion = _read_usage_value(usage_obj, _USAGE_COMPLETION_KEYS)
        total = _read_usage_value(usage_obj, _USAGE_TOTAL_KEYS) or (prompt + completion)
        cache_usage = _extract_cache_usage(usage_obj)
        if prompt == 0 and completion == 0 and total == 0 and not cache_usage:
            return {}
        result = {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": total,
        }
        # Keep legacy numeric fields for telemetry, but never price a missing or
        # malformed required counter as a provider-confirmed zero.
        if (_read_optional_usage_value(usage_obj, _USAGE_PROMPT_KEYS) is None
                or _read_optional_usage_value(usage_obj, _USAGE_COMPLETION_KEYS) is None):
            result["usage_complete"] = False
        result.update(cache_usage)
        return result
    except (TypeError, ValueError, OverflowError):
        return {}
