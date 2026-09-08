"""Conservative, route-local context budgeting for provider requests.

This module deliberately estimates rather than trusting a model-name heuristic.
It is used immediately before a wire request, so an Agent tool loop cannot grow
past the selected route's context window between its initial prompt and review.
"""
from __future__ import annotations

import copy
import json
import threading
from dataclasses import dataclass
from typing import Any, Iterable, Mapping


class ContextBudgetExceeded(ValueError):
    """The required, non-trimmable request cannot fit the selected route."""

    code = "provider_context_budget_exceeded"
    retryable = False


_CALIBRATION_LOCK = threading.RLock()
_ROUTE_ESTIMATE_MULTIPLIERS: dict[str, float] = {}


@dataclass(frozen=True)
class ContextBudget:
    context_window_tokens: int
    max_input_tokens: int
    max_output_tokens: int
    input_token_limit: int
    safety_margin_tokens: int
    thinking_reserve_tokens: int
    history_tokens: int
    component_tokens: int
    reserve_tokens: int
    source: str = "conservative_fallback"
    estimation_multiplier: float = 1.0

    @property
    def effective_input_limit(self) -> int:
        return min(self.max_input_tokens, self.input_token_limit)

    @classmethod
    def from_route(cls, route: Mapping[str, Any] | None = None) -> "ContextBudget":
        route = route or {}
        def positive(name: str, default: int = 0) -> int:
            try:
                return max(0, int(route.get(name) or default))
            except (TypeError, ValueError):
                return default

        # Unknown routes must be usable but never inherit Gemini's advertised
        # million-token capacity merely from a model-like string.
        window = positive("context_window_tokens", 32_768)
        source = "configured" if positive("context_window_tokens") else "conservative_fallback"
        configured_output = positive("max_output_tokens")
        output = configured_output or min(8_192, max(1_024, window // 4))
        try:
            margin_ratio = float(route.get("context_safety_margin_ratio", 0.05) or 0.05)
        except (TypeError, ValueError):
            margin_ratio = 0.05
        margin_ratio = min(0.25, max(0.01, margin_ratio))
        margin = max(256, int(window * margin_ratio))
        thinking_reserve = positive("thinking_reserve_tokens") or max(512, int(window * 0.05))
        if output + margin + thinking_reserve >= window:
            raise ContextBudgetExceeded(
                "route output, thinking and safety reserves exceed its context window"
            )
        physical_input = max(1, window - output - margin - thinking_reserve)
        configured_max = positive("max_input_tokens")
        configured_limit = positive("input_token_limit")
        # Default input allocation is intentionally only half of the window;
        # remaining tokens stay available for output, thinking, and provider
        # accounting that a rough local estimator cannot observe.
        try:
            input_ratio = float(route.get("context_input_ratio", 0.50) or 0.50)
        except (TypeError, ValueError):
            input_ratio = 0.50
        input_ratio = min(0.90, max(0.05, input_ratio))
        default_input = max(1, int(window * input_ratio))
        hard_input_caps = [physical_input, default_input]
        if configured_max:
            hard_input_caps.append(configured_max)
        if configured_limit:
            hard_input_caps.append(configured_limit)
        input_limit = min(hard_input_caps)
        return cls(
            context_window_tokens=window,
            max_input_tokens=configured_max or default_input,
            max_output_tokens=output,
            input_token_limit=input_limit,
            safety_margin_tokens=margin,
            thinking_reserve_tokens=thinking_reserve,
            history_tokens=int(input_limit * 0.70),
            component_tokens=int(input_limit * 0.20),
            reserve_tokens=input_limit - int(input_limit * 0.70) - int(input_limit * 0.20),
            source=source,
            estimation_multiplier=_bounded_multiplier(route.get("_context_estimate_multiplier", 1.0)),
        )


def _bounded_multiplier(value: Any) -> float:
    try:
        return min(2.0, max(0.75, float(value or 1.0)))
    except (TypeError, ValueError):
        return 1.0


def _media_tokens(value: Mapping[str, Any]) -> int | None:
    """Estimate opaque media from declared modality, never its base64 bytes."""
    keys = {str(key).lower() for key in value}
    mime = str(value.get("mime_type") or value.get("mimeType") or "").lower()
    duration = value.get("duration_seconds", value.get("duration", 0))
    try:
        seconds = max(0.0, float(duration or 0))
    except (TypeError, ValueError):
        seconds = 0.0
    is_video = bool(keys & {"video_file", "video_url"}) or mime.startswith("video/")
    is_audio = bool(keys & {"audio_file", "audio_url", "input_audio"}) or mime.startswith("audio/")
    is_image = bool(keys & {"inline_data", "inlinedata", "image_url", "image_file", "input_image", "image"}) or mime.startswith("image/")
    # A data/inlineData field with any image/audio/video MIME is opaque media;
    # its encoded transport representation must not be charged as prompt text.
    data_value = value.get("data", value.get("inlineData", ""))
    data_uri = isinstance(data_value, str) and data_value.lower().startswith("data:")
    if ("data" in keys or "inlinedata" in keys) and (is_image or is_audio or is_video or data_uri):
        if data_uri and not (is_image or is_audio or is_video):
            prefix = data_value.lower().split(";", 1)[0]
            is_video, is_audio, is_image = prefix.startswith("data:video/"), prefix.startswith("data:audio/"), prefix.startswith("data:image/")
        pass
    elif not (is_image or is_audio or is_video):
        return None
    if is_video:
        return min(96_000, max(1_024, int(seconds * 1_024) if seconds else 4_096))
    if is_audio:
        return min(32_000, max(256, int(seconds * 32) if seconds else 1_024))
    # Detail/pixel metadata is optional.  Unknown native images receive a
    # meaningful conservative floor without treating 1 MiB base64 as 500k text
    # tokens. High detail grows toward the typical vision-image allocation.
    detail = str(value.get("detail") or "").lower()
    width, height = value.get("width", 0), value.get("height", 0)
    try:
        pixels = max(0, int(width or 0) * int(height or 0))
    except (TypeError, ValueError):
        pixels = 0
    pixel_cost = min(16_384, max(512, (pixels + 511) // 512)) if pixels else 1_024
    return max(pixel_cost, 8_192 if detail == "high" else 0)


def estimate_tokens(value: Any) -> int:
    """A privacy-safe, conservative estimator for text, schemas and media."""
    if value is None:
        return 0
    if isinstance(value, str):
        # Four characters/token is optimistic for CJK/JSON; this is deliberately
        # more conservative while still avoiding a tokenizer dependency.
        return max(1, (len(value.encode("utf-8")) + 1) // 2)
    if isinstance(value, (bytes, bytearray)):
        return max(1, len(value) // 2)
    if isinstance(value, Mapping):
        media = _media_tokens(value)
        if media is not None:
            # Include only small structured metadata, never URL/base64/data
            # transport payloads.  MIME/size/dimensions still have a cost.
            metadata = {key: item for key, item in value.items() if str(key).lower() not in {"data", "inline_data", "inlinedata", "image_url", "image_file", "video_file", "video_url", "audio_file", "audio_url"}}
            return media + min(256, sum(estimate_tokens(key) + estimate_tokens(item) for key, item in metadata.items()))
        return sum(estimate_tokens(key) + estimate_tokens(item) for key, item in value.items()) + 8
    if isinstance(value, Iterable) and not isinstance(value, (str, bytes, bytearray)):
        return 4 + sum(estimate_tokens(item) for item in value)
    return estimate_tokens(str(value))


def estimate_request_tokens(messages: list[dict[str, Any]], tools: list[dict[str, Any]], *, multiplier: float = 1.0) -> int:
    raw = sum(estimate_tokens(message) + 8 for message in messages) + estimate_tokens(tools) + 16
    return max(1, int(raw * _bounded_multiplier(multiplier)))


def request_token_categories(messages: list[dict[str, Any]], tools: list[dict[str, Any]], *, multiplier: float = 1.0) -> dict[str, int]:
    """Safe token buckets for observability; values never contain prompt text."""
    system = history = memory = media = 0
    for message in messages or []:
        content = message.get("content") if isinstance(message, Mapping) else ""
        cost = estimate_tokens(content) + 8
        if str(message.get("role") or "") == "system":
            system += cost
        elif isinstance(message, Mapping) and any(str(key).startswith("_context_history") for key in message):
            history += cost
        else:
            history += cost
        if isinstance(content, (Mapping, list)):
            media += _media_cost_in_value(content)
    factor = _bounded_multiplier(multiplier)
    return {
        "system_tokens": int(system * factor),
        "history_tokens": int(history * factor),
        "memory_tokens": int(memory * factor),
        "tools_tokens": int(estimate_tokens(tools) * factor),
        "media_tokens": int(media * factor),
    }


def _media_cost_in_value(value: Any) -> int:
    if isinstance(value, Mapping):
        own = _media_tokens(value) or 0
        return own + sum(_media_cost_in_value(item) for item in value.values()) if not own else own
    if isinstance(value, list):
        return sum(_media_cost_in_value(item) for item in value)
    return 0


def _tool_call_ids(message: Mapping[str, Any]) -> set[str]:
    calls = message.get("tool_calls") or message.get("toolCalls") or []
    if not isinstance(calls, list):
        return set()
    return {str(call.get("id") or call.get("tool_call_id") or "") for call in calls if isinstance(call, Mapping)} - {""}


def _protected_indexes(messages: list[dict[str, Any]]) -> set[int]:
    protected = {i for i, message in enumerate(messages) if str(message.get("role") or "") == "system"}
    user_indexes = [i for i, message in enumerate(messages) if str(message.get("role") or "") == "user"]
    if user_indexes:
        protected.add(user_indexes[-1])
    # The current user request and the tool exchange it caused are required.
    # Older exchanges remain removable as atomic units below.
    current_start = user_indexes[-1] if user_indexes else len(messages)
    for index, message in enumerate(messages):
        ids = _tool_call_ids(message)
        if not ids:
            continue
        if index < current_start:
            continue
        protected.add(index)
        for result_index in range(index + 1, len(messages)):
            candidate = messages[result_index]
            role = str(candidate.get("role") or "")
            if role == "assistant":
                break
            if role == "tool" and str(candidate.get("tool_call_id") or candidate.get("toolCallId") or "") in ids:
                protected.add(result_index)
    return protected


def _removal_indexes(messages: list[dict[str, Any]], index: int) -> set[int]:
    """Remove an old tool exchange as one unit, never leaving orphan results."""
    message = messages[index]
    if str(message.get("role") or "") == "user":
        # Historical turns are a user message plus all model/tool continuation
        # until the next user message.  Dropping that unit avoids retaining an
        # apparently unprompted tool exchange after its originating request.
        removable = {index}
        for following in range(index + 1, len(messages)):
            if str(messages[following].get("role") or "") == "user":
                break
            removable.add(following)
        return removable
    ids = _tool_call_ids(message)
    if ids:
        removable = {index}
        for result_index in range(index + 1, len(messages)):
            candidate = messages[result_index]
            if str(candidate.get("role") or "") == "assistant":
                break
            if str(candidate.get("role") or "") == "tool" and str(candidate.get("tool_call_id") or candidate.get("toolCallId") or "") in ids:
                removable.add(result_index)
        return removable
    if str(message.get("role") or "") == "tool":
        result_id = str(message.get("tool_call_id") or message.get("toolCallId") or "")
        if result_id:
            for prior in range(index - 1, -1, -1):
                ids = _tool_call_ids(messages[prior])
                if result_id in ids:
                    return _removal_indexes(messages, prior)
                if str(messages[prior].get("role") or "") == "user":
                    break
    return {index}


def fit_request_to_budget(
    messages: list[dict[str, Any]], tools: list[dict[str, Any]], budget: ContextBudget,
) -> tuple[list[dict[str, Any]], dict[str, int | str]]:
    """Return a copied, atomically-trimmed request or raise a stable error."""
    kept = copy.deepcopy(list(messages or []))
    _refit_trusted_embedded_history(kept, tools, budget)
    # Private assembly metadata is never part of a provider wire message (and
    # never accepted from user content; only dict keys set by local adapters).
    for message in kept:
        for key in tuple(message):
            if key.startswith("_context_history"):
                message.pop(key, None)
    original = estimate_request_tokens(kept, tools, multiplier=budget.estimation_multiplier)
    limit = budget.effective_input_limit
    protected = _protected_indexes(kept)
    while estimate_request_tokens(kept, tools, multiplier=budget.estimation_multiplier) > limit:
        removable = next((i for i in range(len(kept)) if i not in protected), None)
        if removable is None:
            raise ContextBudgetExceeded(
                f"required request exceeds route input budget ({estimate_request_tokens(kept, tools, multiplier=budget.estimation_multiplier)}>{limit})"
            )
        for index in sorted(_removal_indexes(kept, removable), reverse=True):
            kept.pop(index)
        protected = _protected_indexes(kept)
    return kept, {
        "estimated_input_tokens": estimate_request_tokens(kept, tools, multiplier=budget.estimation_multiplier),
        "original_estimated_input_tokens": original,
        "input_token_limit": limit,
        "context_window_tokens": budget.context_window_tokens,
        "budget_source": budget.source,
    }


def _refit_trusted_embedded_history(
    messages: list[dict[str, Any]], tools: list[dict[str, Any]], budget: ContextBudget,
) -> None:
    """Route-refit a locally tagged YAML history block before it becomes wire text.

    There is intentionally no parsing of delimiter-looking user text.  The
    caller must provide a private structured history plus either an internal
    renderer or one rendered item per structure entry, allowing exact
    replacement of only the locally bound aggregate string.
    """
    for index, message in enumerate(messages):
        history = message.get("_context_history")
        rendered = message.get("_context_history_rendered")
        content = message.get("content")
        text_content = _content_text(content)
        if not isinstance(history, list) or not isinstance(rendered, str) or text_content is None:
            continue
        renderer = message.get("_context_history_renderer")
        items = message.get("_context_history_render_items")
        if not callable(renderer) and (not isinstance(items, list) or len(items) != len(history)):
            continue
        fixed = copy.deepcopy(messages)
        fixed_message = fixed[index]
        fixed_message["content"] = _replace_content_text(content, rendered, "")
        for key in tuple(fixed_message):
            if key.startswith("_context_history"):
                fixed_message.pop(key, None)
        selected, _detail = fit_history_to_budget(
            [item for item in history if isinstance(item, dict)],
            fixed_messages=fixed,
            tools=tools,
            budget=budget,
        )
        if callable(renderer):
            new_rendered = renderer(selected)
        else:
            # ``fit_history_to_budget`` copies entries, so preserve by ordered
            # structural equality rather than trusting content as an index.
            remaining = list(selected)
            rendered_parts: list[str] = []
            for source, text in zip(history, items):
                for choice in list(remaining):
                    if source == choice:
                        rendered_parts.append(str(text))
                        remaining.remove(choice)
                        break
            new_rendered = "".join(rendered_parts)
        if not isinstance(new_rendered, str):
            raise ContextBudgetExceeded("trusted history renderer returned invalid content")
        message["content"] = _replace_content_text(content, rendered, new_rendered)


def _content_text(content: Any) -> str | None:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = [str(part.get("text") or "") for part in content if isinstance(part, Mapping) and part.get("type") == "text"]
        return "".join(texts) if texts else None
    return None


def _replace_content_text(content: Any, old: str, new: str) -> Any:
    if isinstance(content, str):
        return content.replace(old, new, 1)
    copied = copy.deepcopy(content)
    if isinstance(copied, list):
        for part in copied:
            if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str) and old in part["text"]:
                part["text"] = part["text"].replace(old, new, 1)
                return copied
    return copied


def fit_history_to_budget(
    history: list[dict[str, Any]],
    *,
    fixed_messages: list[dict[str, Any]] | None = None,
    tools: list[dict[str, Any]] | None = None,
    budget: ContextBudget,
) -> tuple[list[dict[str, Any]], dict[str, int | str]]:
    """Select structured history *before* an adapter flattens it into a prompt.

    ``fixed_messages`` is the rendered system/current-request component and is
    never modified.  History gets its 70% allocation, further reduced when
    fixed prompt pieces or tool schemas leave less physical request room.
    """
    selected = copy.deepcopy(list(history or []))
    fixed = list(fixed_messages or [])
    tool_schemas = list(tools or [])
    fixed_tokens = estimate_request_tokens(fixed, tool_schemas, multiplier=budget.estimation_multiplier)
    available = budget.effective_input_limit - fixed_tokens
    history_limit = min(budget.history_tokens, available)
    if available < 0:
        raise ContextBudgetExceeded(
            f"fixed request components exceed route input budget ({fixed_tokens}>{budget.effective_input_limit})"
        )
    while estimate_request_tokens(selected, [], multiplier=budget.estimation_multiplier) > history_limit:
        if not selected:
            break
        # History has no current user turn; oldest complete conversational
        # unit is removable.  A leading historical system item is retained
        # only when it fits; otherwise callers receive a clear required-input
        # failure rather than a malformed system-free history.
        if str(selected[0].get("role") or "") == "system":
            if len(selected) == 1:
                raise ContextBudgetExceeded("required history system component exceeds route budget")
            candidate = 1
        else:
            candidate = 0
        for index in sorted(_removal_indexes(selected, candidate), reverse=True):
            selected.pop(index)
    return selected, {
        "history_estimated_tokens": estimate_request_tokens(selected, [], multiplier=budget.estimation_multiplier),
        "history_token_limit": max(0, history_limit),
        "fixed_component_estimated_tokens": fixed_tokens,
        "input_token_limit": budget.effective_input_limit,
        "budget_source": budget.source,
    }


def usage_calibration(
    *, estimated_input_tokens: int,
    usage: Mapping[str, Any] | None,
) -> dict[str, int | str]:
    """Safe per-response estimate calibration for Trace/diagnostics.

    Providers expose different usage shapes.  This function does not retain
    prompt text or mutate a global ratio; callers may record the returned
    category and calibrate policy only from confirmed native prompt usage.
    """
    usage = usage or {}
    observed = usage.get("prompt_tokens", usage.get("input_tokens", 0))
    try:
        observed = max(0, int(observed or 0))
    except (TypeError, ValueError):
        observed = 0
    estimate = max(0, int(estimated_input_tokens or 0))
    if not observed:
        return {"usage_calibration": "unavailable", "estimated_input_tokens": estimate}
    ratio = int((estimate * 100) / max(1, observed))
    category = "close" if 75 <= ratio <= 125 else ("conservative" if ratio > 125 else "underestimated")
    return {
        "usage_calibration": category,
        "estimated_input_tokens": estimate,
        "observed_input_tokens": observed,
        "estimate_percent_of_observed": ratio,
    }


def route_estimation_multiplier(route_key: Any) -> float:
    """Return a process-local bounded multiplier; route keys contain no prompt data."""
    key = str(route_key or "unknown")[:160]
    with _CALIBRATION_LOCK:
        return _ROUTE_ESTIMATE_MULTIPLIERS.get(key, 1.0)


def record_usage_calibration(
    *, route_key: Any, estimated_input_tokens: int, usage: Mapping[str, Any] | None,
) -> dict[str, int | str | float]:
    """Update a bounded EWMA only when native provider input usage is present."""
    result: dict[str, int | str | float] = dict(
        usage_calibration(estimated_input_tokens=estimated_input_tokens, usage=usage)
    )
    observed = result.get("observed_input_tokens")
    if not isinstance(observed, int) or observed <= 0 or estimated_input_tokens <= 0:
        return result
    # Native/estimated shows the adjustment needed for future estimates.  The
    # clamp protects one malformed provider report from radically changing a
    # route. EWMA is local process telemetry; it stores neither messages nor
    # usage identities.
    sample = _bounded_multiplier(observed / max(1, estimated_input_tokens))
    key = str(route_key or "unknown")[:160]
    with _CALIBRATION_LOCK:
        previous = _ROUTE_ESTIMATE_MULTIPLIERS.get(key, 1.0)
        updated = _bounded_multiplier(previous * 0.80 + sample * 0.20)
        _ROUTE_ESTIMATE_MULTIPLIERS[key] = updated
    result["route_estimation_multiplier"] = updated
    return result


def primary_route_budget(plugin_config: Any, logger: Any = None) -> ContextBudget:
    """Resolve the active primary api-pool lazily, without an import cycle."""
    from .provider_router import get_configured_api_providers

    providers = get_configured_api_providers(plugin_config, logger)
    if providers:
        route = dict(providers[0])
        route["_context_estimate_multiplier"] = route_estimation_multiplier(route.get("name") or route.get("model"))
        return ContextBudget.from_route(route)
    return ContextBudget.from_route(
        {
            "context_budget_enabled": getattr(plugin_config, "personification_context_budget_enabled", True),
            "context_input_ratio": getattr(plugin_config, "personification_context_input_ratio", 0.50),
            "context_safety_margin_ratio": getattr(plugin_config, "personification_context_safety_margin_ratio", 0.05),
        }
    )


__all__ = ["ContextBudget", "ContextBudgetExceeded", "estimate_request_tokens", "estimate_tokens", "fit_history_to_budget", "fit_request_to_budget", "primary_route_budget", "record_usage_calibration", "route_estimation_multiplier", "usage_calibration"]
