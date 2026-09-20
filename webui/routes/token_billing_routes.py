"""Authenticated metering and user-defined price versions; no provider requests."""
from __future__ import annotations

import asyncio
import sqlite3
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from ...core import token_ledger, token_pricing, webui_audit_log
from ...core.provider_catalog import normalize_catalog_pool
from ..deps import AdminIdentity, require_admin


class PriceVersionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    route_id: str = Field(min_length=1, max_length=240)
    model: str = Field(default="", max_length=256)
    currency: str = Field(min_length=1, max_length=16)
    effective_from: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    input_per_million: str | None = Field(default=None, max_length=64)
    output_per_million: str | None = Field(default=None, max_length=64)
    cache_read_per_million: str | None = Field(default=None, max_length=64)
    cache_create_per_million: str | None = Field(default=None, max_length=64)
    cache_create_5m_per_million: str | None = Field(default=None, max_length=64)
    cache_create_1h_per_million: str | None = Field(default=None, max_length=64)


class RepriceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version_id: str = Field(min_length=1, max_length=96)
    event_ids: list[str] = Field(min_length=1, max_length=200)


def build_token_billing_router(*, runtime: Any) -> APIRouter:
    router = APIRouter(prefix="/api/v2/metrics", tags=["token-billing"])

    @router.get("/usage")
    async def usage(
        window: Literal["24h", "7d", "30d", "all"] = "30d",
        bot_id: str | None = None, group_id: str | None = None,
        provider: str | None = None, route_id: str | None = None,
        model: str | None = None, purpose: str | None = None,
        _: AdminIdentity = Depends(require_admin),
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            token_ledger.query_usage_insights, window, bot_id=bot_id,
            group_id=group_id, provider=provider, route_id=route_id,
            model=model, purpose=purpose,
        )

    @router.get("/prices")
    async def prices(_: AdminIdentity = Depends(require_admin)) -> dict[str, Any]:
        rows = await asyncio.to_thread(token_pricing.list_price_versions)
        return {"items": [{**row, "route_id": row["route"]} for row in rows]}

    @router.get("/price-routes")
    async def price_routes(_: AdminIdentity = Depends(require_admin)) -> dict[str, Any]:
        pools = getattr(runtime.plugin_config, "personification_api_pools", []) or []
        items = []
        for index, raw in enumerate(pools):
            pool = normalize_catalog_pool(raw, index=index)
            if pool:
                # Explicit projection: never return API URLs, keys or authentication paths.
                items.append({"route_id": pool["provider_id"], "name": pool.get("name", ""),
                              "models": [m["model_id"] for m in pool["models"]]})
        return {"items": items}

    @router.post("/prices", status_code=201)
    async def create_price(body: PriceVersionInput, admin: AdminIdentity = Depends(require_admin)) -> dict[str, Any]:
        try:
            price = await asyncio.to_thread(token_pricing.create_price_version, **body.model_dump())
        except ValueError as exc:
            raise HTTPException(422, detail={"code": "invalid_token_price", "message": str(exc)}) from exc
        except sqlite3.IntegrityError as exc:
            raise HTTPException(409, detail={"code": "price_version_exists"}) from exc
        webui_audit_log.record(action="token_price_create", qq=admin.qq, device_id=admin.device_id,
                              target=price["version_id"], detail={"route": price["route"], "model": price["model"]})
        return {**price, "route_id": price["route"]}

    @router.post("/reprice-preview")
    async def reprice(body: RepriceInput, _: AdminIdentity = Depends(require_admin)) -> dict[str, Any]:
        if any(not item.strip() or len(item) > 96 for item in body.event_ids):
            raise HTTPException(422, detail={"code": "invalid_usage_event_id"})
        try:
            result = await asyncio.to_thread(token_ledger.reprice_usage_preview,
                                             version_id=body.version_id, event_ids=body.event_ids)
            items = result.get("items", [])
            return {**result,
                    "costs": [{"currency": result["currency"], "cost_decimal": result["cost_decimal"],
                               "priced_call_count": len(items)}] if items else [],
                    "unpriced_call_count": max(0, len(set(body.event_ids)) - len(items)),
                    "incomplete_priced_call_count": sum(not item.get("pricing_complete", False) for item in items)}
        except ValueError as exc:
            raise HTTPException(422, detail={"code": "invalid_reprice", "message": str(exc)}) from exc

    return router
