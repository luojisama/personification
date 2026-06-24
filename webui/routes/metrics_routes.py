from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from ...core import metrics, token_ledger
from ..deps import AdminIdentity, require_admin

_WINDOW_PATTERN = "^(day|week|month|24h|7d|30d)$"

_PROVIDER_LABELS = {
    "anthropic": "Anthropic / Claude",
    "openai": "OpenAI",
    "gemini": "Gemini/Antigravity CLI",
    "codex": "Codex / ChatGPT",
}

_QUOTA_FIELDS = {
    "anthropic": "personification_quota_anthropic_monthly_tokens",
    "openai": "personification_quota_openai_monthly_tokens",
    "gemini": "personification_quota_gemini_cli_monthly_tokens",
    "codex": "personification_quota_codex_monthly_tokens",
}


def _resolve_limit(plugin_config, provider: str) -> int:
    field = _QUOTA_FIELDS.get(provider, "")
    if not field:
        return 0
    try:
        return max(0, int(getattr(plugin_config, field, 0) or 0))
    except Exception:
        return 0


def _provider_usage(*, plugin_config, window: str) -> list[dict]:
    data = token_ledger.query_provider_summary(window)
    rows = list(data.get("providers", []) or [])
    items = []
    for provider_key in ("anthropic", "openai", "gemini", "codex"):
        entry = next(
            (p for p in rows if p.get("provider") == provider_key),
            {
                "provider": provider_key,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "call_count": 0,
            },
        )
        limit = _resolve_limit(plugin_config, provider_key) if plugin_config else 0
        used = int(entry.get("total_tokens", 0) or 0)
        items.append(
            {
                "provider": provider_key,
                "label": _PROVIDER_LABELS[provider_key],
                "prompt_tokens": int(entry.get("prompt_tokens", 0) or 0),
                "completion_tokens": int(entry.get("completion_tokens", 0) or 0),
                "total_tokens": used,
                "call_count": int(entry.get("call_count", 0) or 0),
                "monthly_limit": limit,
                "usage_ratio": round(used / limit, 4) if limit > 0 else 0.0,
                "unlimited": limit == 0,
            }
        )
    for entry in rows:
        provider = str(entry.get("provider", "") or "unknown")
        if provider in _PROVIDER_LABELS:
            continue
        items.append(
            {
                "provider": provider,
                "label": provider,
                "prompt_tokens": int(entry.get("prompt_tokens", 0) or 0),
                "completion_tokens": int(entry.get("completion_tokens", 0) or 0),
                "total_tokens": int(entry.get("total_tokens", 0) or 0),
                "call_count": int(entry.get("call_count", 0) or 0),
                "monthly_limit": 0,
                "usage_ratio": 0.0,
                "unlimited": True,
            }
        )
    return items


def _billing_summary(summary: dict, provider_usage: list[dict]) -> dict:
    series = []
    for point in summary.get("series", []) or []:
        series.append(
            {
                "bucket": point.get("bucket", ""),
                "label": point.get("label", ""),
                "request_cost": 0.0,
                "credit_deduction": 0.0,
                "requests": int(point.get("call_count", 0) or 0),
                "tokens": int(point.get("total_tokens", 0) or 0),
            }
        )
    limited = [p for p in provider_usage if int(p.get("monthly_limit", 0) or 0) > 0]
    used_tokens = sum(int(p.get("total_tokens", 0) or 0) for p in provider_usage)
    limited_used_tokens = sum(int(p.get("total_tokens", 0) or 0) for p in limited)
    limit_tokens = sum(int(p.get("monthly_limit", 0) or 0) for p in limited)
    return {
        "request_cost": 0.0,
        "credit_deduction": 0.0,
        "cost_configured": False,
        "currency": "USD",
        "series": series,
        "quota": {
            "used_tokens": used_tokens,
            "limited_used_tokens": limited_used_tokens,
            "limit_tokens": limit_tokens,
            "limited_provider_count": len(limited),
            "unlimited": len(limited) == 0,
        },
        "note": "本地 token 账本当前没有模型单价配置，费用字段显示为 $0.00；额度进度按 provider token 月度额度统计。",
    }


def build_metrics_router(*, runtime) -> APIRouter:
    router = APIRouter(prefix="/api/metrics", tags=["metrics"])

    @router.get("/summary")
    async def summary(
        window: str = Query(default="month", pattern=_WINDOW_PATTERN),
        _: AdminIdentity = Depends(require_admin),
    ) -> dict:
        window_key = token_ledger.normalize_window(window)
        data = token_ledger.query_summary(window_key)
        plugin_config = getattr(runtime, "plugin_config", None)
        provider_usage = _provider_usage(plugin_config=plugin_config, window=window_key)
        data["provider_usage"] = provider_usage
        data["billing"] = _billing_summary(data, provider_usage)
        data["total_consumption"] = token_ledger.query_total_consumption()
        return data

    @router.get("/group/{group_id}")
    async def group_detail(
        group_id: str,
        window: str = Query(default="month", pattern=_WINDOW_PATTERN),
        _: AdminIdentity = Depends(require_admin),
    ) -> dict:
        return token_ledger.query_group_detail(group_id, token_ledger.normalize_window(window))

    @router.get("/runtime")
    async def runtime_snapshot(_: AdminIdentity = Depends(require_admin)) -> dict:
        snap = metrics.snapshot_metrics()
        return {
            "counters": list(snap.get("counters", []))[:30],
            "timings": list(snap.get("timings", []))[:30],
        }

    return router
