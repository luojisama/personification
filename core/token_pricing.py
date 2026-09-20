from __future__ import annotations

import hashlib
import json
import time
import math
from decimal import Decimal, InvalidOperation, localcontext
from typing import Any, Mapping

from .db import connect_sync

_MILLION = Decimal("1000000")
_PRICE_FIELDS = (
    "input_per_million",
    "output_per_million",
    "cache_read_per_million",
    "cache_create_per_million",
    "cache_create_5m_per_million",
    "cache_create_1h_per_million",
)


def _identity(value: Any, *, limit: int = 240) -> str:
    return str(value or "").strip()[:limit]


def _decimal_text(value: Any, *, required: bool = False) -> str | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise ValueError("price is required")
        return None
    if isinstance(value, bool):
        raise ValueError("price must be a non-negative decimal")
    raw = str(value).strip()
    if len(raw) > 64:
        raise ValueError("price is too large")
    try:
        number = Decimal(raw)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("price must be a non-negative decimal") from exc
    if not number.is_finite() or number < 0:
        raise ValueError("price must be a non-negative decimal")
    if number.adjusted() > 18 or max(0, -number.as_tuple().exponent) > 18:
        raise ValueError("price exceeds supported precision")
    return format(number, "f")


def create_price_version(
    *,
    route_id: str,
    model: str = "",
    currency: str,
    effective_from: float | None = None,
    version_id: str = "",
    **prices: Any,
) -> dict[str, Any]:
    """Create an immutable price version. Empty model is an explicit route default."""
    route_key = _identity(route_id)
    model_key = _identity(model, limit=256)
    currency_key = _identity(currency, limit=16).upper()
    if not route_key or not currency_key:
        raise ValueError("route and currency are required")
    normalized = {field: _decimal_text(prices.get(field)) for field in _PRICE_FIELDS}
    if all(value is None for value in normalized.values()):
        raise ValueError("at least one price is required")
    effective = float(time.time() if effective_from is None else effective_from)
    if not math.isfinite(effective) or effective < 0:
        raise ValueError("effective_from must be non-negative")
    if not version_id:
        canonical = json.dumps(
            {"route": route_key, "model": model_key, "currency": currency_key,
             "effective_from": effective, **normalized},
            sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        )
        version_id = "price_" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]
    version_key = _identity(version_id, limit=96)
    now = time.time()
    with connect_sync() as conn:
        conn.execute(
            f"""INSERT INTO token_price_versions(
                version_id,route,model,currency,{','.join(_PRICE_FIELDS)},effective_from,created_at
            ) VALUES ({','.join('?' for _ in range(12))})""",
            (version_key, route_key, model_key, currency_key,
             *(normalized[field] for field in _PRICE_FIELDS), effective, now),
        )
        conn.commit()
    return get_price_version(version_key) or {}


def get_price_version(version_id: str) -> dict[str, Any] | None:
    with connect_sync() as conn:
        row = conn.execute(
            "SELECT * FROM token_price_versions WHERE version_id=?",
            (_identity(version_id, limit=96),),
        ).fetchone()
    return dict(row) if row is not None else None


def list_price_versions(*, route: str = "", model: str | None = None) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if route:
        clauses.append("route=?")
        params.append(_identity(route))
    if model is not None:
        clauses.append("model=?")
        params.append(_identity(model, limit=256))
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    with connect_sync() as conn:
        rows = conn.execute(
            f"SELECT * FROM token_price_versions{where} ORDER BY effective_from DESC, created_at DESC",
            tuple(params),
        ).fetchall()
    return [dict(row) for row in rows]


def resolve_price_version(*, route: str, model: str, observed_at: float) -> dict[str, Any] | None:
    """Resolve exact route+model first, then an explicitly configured route default."""
    route_key = _identity(route)
    model_key = _identity(model, limit=256)
    if not route_key:
        return None
    with connect_sync() as conn:
        row = conn.execute(
            """SELECT * FROM token_price_versions
               WHERE route=? AND model=? AND effective_from<=?
               ORDER BY effective_from DESC, created_at DESC LIMIT 1""",
            (route_key, model_key, float(observed_at)),
        ).fetchone()
        if row is None and model_key:
            row = conn.execute(
                """SELECT * FROM token_price_versions
                   WHERE route=? AND model='' AND effective_from<=?
                   ORDER BY effective_from DESC, created_at DESC LIMIT 1""",
                (route_key, float(observed_at)),
            ).fetchone()
    return dict(row) if row is not None else None


def calculate_cost(usage: Mapping[str, Any], price: Mapping[str, Any]) -> dict[str, Any]:
    """Price mutually-exclusive categories without floating point arithmetic."""
    def token(name: str) -> int | None:
        value = usage.get(name)
        return value if type(value) is int and value >= 0 else None

    input_tokens = token("input_tokens")
    output_tokens = token("output_tokens")
    cache_read = token("cache_read_tokens")
    cache_create = token("cache_create_tokens")
    cache_5m = token("cache_create_5m_tokens")
    cache_1h = token("cache_create_1h_tokens")
    includes_cache = bool(usage.get("input_includes_cache", True))
    provider = _identity(usage.get("provider"), limit=64).lower()
    additive_contract = provider == "anthropic"
    inclusive_contract = provider in {"openai", "gemini", "codex", "official"}

    missing_usage: list[str] = []
    invalid_usage: list[str] = []
    if input_tokens is None:
        missing_usage.append("input_tokens")
    if output_tokens is None:
        missing_usage.append("output_tokens")
    if cache_read is None:
        missing_usage.append("cache_read_tokens")
    cache_creation_known = cache_create is not None or (cache_5m is not None and cache_1h is not None)
    if additive_contract and not cache_creation_known:
        missing_usage.append("cache_creation_tokens")
    if not additive_contract and not inclusive_contract and not cache_creation_known:
        missing_usage.append("cache_creation_tokens")

    read = cache_read or 0
    created_ttl = (cache_5m or 0) + (cache_1h or 0)
    created = cache_create if cache_create is not None else created_ttl
    ordinary_input = input_tokens or 0
    if includes_cache:
        if input_tokens is not None and read + created > input_tokens:
            invalid_usage.append("cache_tokens_exceed_input_tokens")
        ordinary_input = max(0, (input_tokens or 0) - read - created)
    if cache_create is not None and created_ttl > cache_create:
        invalid_usage.append("cache_ttl_tokens_exceed_creation_tokens")

    # OpenAI/Gemini/Codex report cached reads inside input; absent creation
    # fields are not applicable unless the provider explicitly reports them.
    cache_partition_known = cache_read is not None and (cache_creation_known or inclusive_contract)
    categories: list[tuple[str, int, str]] = []
    # Inclusive providers cannot split ordinary input from cache while cache
    # metadata is unknown. Keep only safely priceable subtotals in that case.
    if input_tokens is not None and (not includes_cache or cache_partition_known):
        categories.append(("input", ordinary_input, "input_per_million"))
    if output_tokens is not None:
        categories.append(("output", output_tokens, "output_per_million"))
    cache_values_valid = not invalid_usage
    if cache_read is not None and cache_values_valid:
        categories.append(("cache_read", read, "cache_read_per_million"))
    if cache_5m is not None and cache_values_valid:
        categories.append(("cache_create_5m", cache_5m, "cache_create_5m_per_million"))
    if cache_1h is not None and cache_values_valid:
        categories.append(("cache_create_1h", cache_1h, "cache_create_1h_per_million"))
    remaining_create = max(0, created - created_ttl)
    if cache_create is not None and remaining_create and cache_values_valid:
        categories.append(("cache_create", remaining_create, "cache_create_per_million"))

    total = Decimal("0")
    breakdown: dict[str, str] = {}
    missing: list[str] = []
    for name, count, field in categories:
        if count == 0:
            breakdown[name] = "0"
            continue
        raw_price = price.get(field)
        # TTL prices may explicitly fall back to the generic creation price.
        if raw_price is None and field in {"cache_create_5m_per_million", "cache_create_1h_per_million"}:
            raw_price = price.get("cache_create_per_million")
        if raw_price is None:
            missing.append(field)
            continue
        with localcontext() as context:
            context.prec = 60
            subtotal = Decimal(str(raw_price)) * Decimal(count) / _MILLION
            total += subtotal
        breakdown[name] = format(subtotal, "f")
    return {
        "currency": _identity(price.get("currency"), limit=16).upper(),
        "cost_decimal": format(total, "f"),
        "pricing_complete": not missing and not missing_usage and not invalid_usage,
        "missing_price_fields": sorted(set(missing)),
        "missing_usage_fields": sorted(set(missing_usage)),
        "invalid_usage_fields": sorted(set(invalid_usage)),
        "breakdown": breakdown,
        "ordinary_input_tokens": ordinary_input,
    }


__all__ = [
    "create_price_version", "get_price_version", "list_price_versions",
    "resolve_price_version", "calculate_cost",
]
