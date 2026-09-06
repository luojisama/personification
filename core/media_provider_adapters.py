from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .provider_types import is_removed_provider_type


MEDIA_PROTOCOL_AUTO = "auto"
MEDIA_PROTOCOL_NONE = "none"
MEDIA_PROTOCOL_GEMINI = "gemini_native"
MEDIA_PROTOCOL_ANTIGRAVITY = "antigravity_native"
MEDIA_PROTOCOL_QWEN = "openai_qwen_omni"
MEDIA_PROTOCOL_MIMO = "openai_mimo_v25"
MEDIA_PROTOCOL_OPENAI_GEMINI_INLINE = "openai_gemini_inline"

_KNOWN_PROTOCOLS = {
    MEDIA_PROTOCOL_AUTO,
    MEDIA_PROTOCOL_NONE,
    MEDIA_PROTOCOL_GEMINI,
    MEDIA_PROTOCOL_ANTIGRAVITY,
    MEDIA_PROTOCOL_QWEN,
    MEDIA_PROTOCOL_MIMO,
    MEDIA_PROTOCOL_OPENAI_GEMINI_INLINE,
}


@dataclass(frozen=True)
class MediaProviderAdapter:
    protocol: str
    supports_video: bool
    supports_audio: bool
    local_transport: str
    source: str


def normalize_media_protocol(value: Any) -> str:
    normalized = str(value or MEDIA_PROTOCOL_AUTO).strip().lower().replace("-", "_")
    aliases = {
        "disabled": MEDIA_PROTOCOL_NONE,
        "text_only": MEDIA_PROTOCOL_NONE,
        "gemini": MEDIA_PROTOCOL_GEMINI,
        "gemini_official": MEDIA_PROTOCOL_GEMINI,
        "antigravity": MEDIA_PROTOCOL_ANTIGRAVITY,
        "agy": MEDIA_PROTOCOL_ANTIGRAVITY,
        "agy_native": MEDIA_PROTOCOL_ANTIGRAVITY,
        "qwen": MEDIA_PROTOCOL_QWEN,
        "qwen_omni": MEDIA_PROTOCOL_QWEN,
        "mimo": MEDIA_PROTOCOL_MIMO,
        "mimo_v25": MEDIA_PROTOCOL_MIMO,
        "mimo_v2_5": MEDIA_PROTOCOL_MIMO,
        "gemini_inline": MEDIA_PROTOCOL_OPENAI_GEMINI_INLINE,
        "openai_gemini": MEDIA_PROTOCOL_OPENAI_GEMINI_INLINE,
    }
    normalized = aliases.get(normalized, normalized)
    return normalized if normalized in _KNOWN_PROTOCOLS else MEDIA_PROTOCOL_AUTO


def _adapter(protocol: str, *, source: str) -> MediaProviderAdapter:
    if protocol == MEDIA_PROTOCOL_GEMINI:
        # Native generateContent accepts bounded inlineData.  Files API is an
        # optional large-file feature, not a prerequisite for an explicitly
        # configured Gemini-compatible endpoint.
        return MediaProviderAdapter(protocol, True, True, "inline_data", source)
    if protocol == MEDIA_PROTOCOL_ANTIGRAVITY:
        return MediaProviderAdapter(protocol, True, True, "inlineData_or_fileData", source)
    if protocol in {MEDIA_PROTOCOL_QWEN, MEDIA_PROTOCOL_MIMO}:
        return MediaProviderAdapter(protocol, True, True, "base64_or_url", source)
    if protocol == MEDIA_PROTOCOL_OPENAI_GEMINI_INLINE:
        return MediaProviderAdapter(protocol, True, True, "openai_inline_data", source)
    return MediaProviderAdapter(MEDIA_PROTOCOL_NONE, False, False, "none", source)


def _official_protocol(api_type: str, api_url: str, model: str) -> str:
    api = str(api_type or "").strip().lower().replace("-", "_")
    model_text = str(model or "").strip().lower().replace("_", "-")
    if any(token in model_text for token in ("asr", "tts", "embedding", "embed")):
        return MEDIA_PROTOCOL_NONE
    if model_text.startswith(("qwen3.5-omni-", "qwen3-omni-flash")):
        return MEDIA_PROTOCOL_QWEN
    if model_text == "mimo-v2.5":
        return MEDIA_PROTOCOL_MIMO
    # API type is an explicit administrator-declared wire contract.  It is a
    # candidate for media probing, never proof that a third-party endpoint has
    # implemented the capability.  Do not use a hostname or model spelling as
    # a capability gate.
    if api in {"gemini", "gemini_official"}:
        return MEDIA_PROTOCOL_GEMINI
    return MEDIA_PROTOCOL_NONE


def resolve_media_provider_adapter(provider: Mapping[str, Any] | None) -> MediaProviderAdapter:
    payload = provider or {}
    if is_removed_provider_type(payload.get("api_type")):
        return _adapter(MEDIA_PROTOCOL_NONE, source="provider_type_removed")
    configured = normalize_media_protocol(payload.get("media_protocol", MEDIA_PROTOCOL_AUTO))
    if configured != MEDIA_PROTOCOL_AUTO:
        return _adapter(configured, source="explicit")
    inferred = _official_protocol(
        str(payload.get("api_type", "") or ""),
        str(payload.get("api_url", "") or ""),
        str(payload.get("model", "") or ""),
    )
    return _adapter(
        inferred,
        source="official_preset" if inferred != MEDIA_PROTOCOL_NONE else "unsupported",
    )


__all__ = [
    "MEDIA_PROTOCOL_AUTO",
    "MEDIA_PROTOCOL_ANTIGRAVITY",
    "MEDIA_PROTOCOL_GEMINI",
    "MEDIA_PROTOCOL_MIMO",
    "MEDIA_PROTOCOL_OPENAI_GEMINI_INLINE",
    "MEDIA_PROTOCOL_NONE",
    "MEDIA_PROTOCOL_QWEN",
    "MediaProviderAdapter",
    "normalize_media_protocol",
    "resolve_media_provider_adapter",
]
