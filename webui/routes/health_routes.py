from __future__ import annotations

from fastapi import APIRouter, Depends

from ..deps import AdminIdentity, require_admin


def build_health_router(*, runtime) -> APIRouter:
    router = APIRouter(prefix="/api/health", tags=["health"])

    @router.get("/check")
    async def check(_: AdminIdentity = Depends(require_admin)) -> dict:
        from ...core.diagnostics import run_diagnostics

        return await run_diagnostics(
            plugin_config=getattr(runtime, "plugin_config", None),
            bundle=getattr(runtime, "runtime_bundle", None),
            superusers=getattr(runtime, "superusers", set()),
            get_bots=getattr(runtime, "get_bots", None),
            logger=getattr(runtime, "logger", None),
        )

    return router
