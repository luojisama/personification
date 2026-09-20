"""Provider connection/model catalog normalization.

The persisted ``api_pools`` field predates the catalog and represents one
connection plus one model per item.  Keep that wire format readable while
allowing a connection to own several independently budgeted model entries.
This module intentionally contains no HTTP/client code.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable


PURPOSES = ("main", "lite", "persona", "compress", "vision", "labeler")


def _text(value: Any) -> str:
    return str(value or "").strip()


def stable_provider_id(item: dict[str, Any], index: int = 0) -> str:
    """Return a non-secret stable ID for legacy entries.

    Name is deliberately part of the migration identity so two connections to
    the same gateway do not collapse.  New saves retain the generated ID.
    """
    explicit = _text(item.get("provider_id"))
    if explicit:
        return explicit[:96]
    basis = json.dumps(
        {
            "name": _text(item.get("name")),
            "type": _text(item.get("api_type")),
            "url": _text(item.get("api_url")),
            "auth": _text(item.get("auth_path")),
            "project": _text(item.get("project")),
            "index": int(index),
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return "provider_" + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:20]


def _model_entry(value: Any, *, fallback_model: str = "") -> dict[str, Any] | None:
    raw = dict(value) if isinstance(value, dict) else {"model_id": value}
    model_id = _text(raw.get("model_id") or raw.get("id") or raw.get("model") or fallback_model)
    if not model_id:
        return None
    result = {
        "model_id": model_id[:256],
        "display_name": _text(raw.get("display_name") or raw.get("name") or model_id)[:256],
        "enabled": raw.get("enabled", True) is not False,
    }
    for name in (
        "context_window_tokens", "max_input_tokens", "max_output_tokens", "input_token_limit",
    ):
        try:
            result[name] = max(0, int(raw.get(name, 0) or 0))
        except (TypeError, ValueError):
            result[name] = 0
    capabilities = raw.get("capabilities")
    if isinstance(capabilities, dict):
        result["capabilities"] = {str(key)[:64]: bool(val) for key, val in capabilities.items()}
    return result


def normalize_catalog_pool(raw: Any, *, index: int = 0) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    item = dict(raw)
    provider_id = stable_provider_id(item, index)
    models: list[dict[str, Any]] = []
    raw_models = item.get("models")
    if isinstance(raw_models, list):
        for candidate in raw_models:
            parsed = _model_entry(candidate)
            if parsed and parsed["model_id"] not in {m["model_id"] for m in models}:
                models.append(parsed)
    # Old items migrate to one model, preserving their per-route limits.
    if not models:
        parsed = _model_entry(item, fallback_model=_text(item.get("model")))
        if parsed:
            models.append(parsed)
    legacy_model = _text(item.get("model"))
    default_model_id = _text(item.get("default_model_id") or legacy_model)
    known = {model["model_id"] for model in models}
    if default_model_id not in known:
        default_model_id = models[0]["model_id"] if models else ""
    purpose_models = item.get("purpose_models")
    normalized_purposes: dict[str, str] = {}
    if isinstance(purpose_models, dict):
        for purpose, model_id in purpose_models.items():
            key = _text(purpose).lower()
            value = _text(model_id)
            if key in PURPOSES and value in known:
                normalized_purposes[key] = value
    item["provider_id"] = provider_id
    item["models"] = models
    item["default_model_id"] = default_model_id
    item["purpose_models"] = normalized_purposes
    # Keep a legacy mirror for older consumers and configuration views.
    item["model"] = default_model_id
    return item


def normalize_catalog_pools(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(raw):
        normalized = normalize_catalog_pool(item, index=index)
        if normalized is None:
            continue
        provider_id = normalized["provider_id"]
        # Duplicate stable IDs make credential restoration ambiguous.
        if provider_id in seen:
            normalized["provider_id"] = f"{provider_id}_{index + 1}"
        seen.add(normalized["provider_id"])
        result.append(normalized)
    return result


def model_for_purpose(provider: dict[str, Any], purpose: str = "main") -> dict[str, Any] | None:
    normalized = normalize_catalog_pool(provider)
    if normalized is None:
        return None
    requested = _text((normalized.get("purpose_models") or {}).get(_text(purpose).lower()))
    selected = requested or _text(normalized.get("default_model_id"))
    for model in normalized["models"]:
        if model["model_id"] == selected and model.get("enabled", True):
            return dict(model)
    # Disabling the selected model is not authorization to choose another
    # model from this supplier. Existing provider fallback stays separate.
    return None


def expand_catalog_pools(raw: Any, *, purpose: str = "main") -> list[dict[str, Any]]:
    """Turn connections into existing single-model provider route objects."""
    expanded: list[dict[str, Any]] = []
    for index, provider in enumerate(normalize_catalog_pools(raw)):
        if provider.get("enabled", True) is False:
            continue
        model = model_for_purpose(provider, purpose)
        if model is None:
            continue
        route = dict(provider)
        route.update(model)
        route["model"] = model["model_id"]
        route["provider_id"] = provider["provider_id"]
        route["route_purpose"] = _text(purpose).lower() or "main"
        # Preserve legacy route names: health, diagnostics and existing API-pool
        # fallback policy use this key. provider_id/model_id remain explicit for
        # catalog-aware callers.
        route["name"] = _text(provider.get("name")) or f"pool_{index + 1}"
        expanded.append(route)
    return expanded


def normalize_purpose_bindings(value: Any) -> dict[str, dict[str, str]]:
    """Validate the global purpose -> provider/model pair selection."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
    if not isinstance(value, dict):
        return {}
    result: dict[str, dict[str, str]] = {}
    for raw_purpose, raw_binding in value.items():
        purpose = _text(raw_purpose).lower()
        if purpose not in PURPOSES or not isinstance(raw_binding, dict):
            continue
        provider_id = _text(raw_binding.get("provider_id"))
        model_id = _text(raw_binding.get("model_id"))
        if provider_id and model_id:
            result[purpose] = {"provider_id": provider_id, "model_id": model_id}
    return result


def expand_bound_catalog_pools(
    raw: Any,
    *,
    purpose: str = "main",
    bindings: Any = None,
) -> list[dict[str, Any]]:
    """Resolve an explicit pair binding before normal fallback ordering."""
    normalized_purpose = _text(purpose).lower() or "main"
    binding = normalize_purpose_bindings(bindings).get(normalized_purpose)
    if not binding:
        return expand_catalog_pools(raw, purpose=normalized_purpose)
    for index, provider in enumerate(normalize_catalog_pools(raw)):
        if provider.get("provider_id") != binding["provider_id"] or provider.get("enabled", True) is False:
            continue
        for model in provider["models"]:
            if model["model_id"] != binding["model_id"] or model.get("enabled", True) is False:
                continue
            route = dict(provider)
            route.update(model)
            route.update({
                "model": model["model_id"],
                "provider_id": provider["provider_id"],
                "route_purpose": normalized_purpose,
                "name": _text(provider.get("name")) or f"pool_{index + 1}",
                "purpose_binding": True,
            })
            return [route]
    # An invalid explicit binding must fail closed, not silently use another
    # supplier/model after an administrator intentionally selected one.
    return []


def referenced_model_ids(pools: Iterable[dict[str, Any]]) -> set[tuple[str, str]]:
    refs: set[tuple[str, str]] = set()
    for index, item in enumerate(pools):
        normalized = normalize_catalog_pool(item, index=index)
        if normalized is None:
            continue
        provider_id = normalized["provider_id"]
        for model_id in normalized["purpose_models"].values():
            refs.add((provider_id, model_id))
    return refs
