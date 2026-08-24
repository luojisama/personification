from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from ..deps import AdminIdentity, require_admin
from ..v2_services import build_agent_runtime_snapshot


def build_agent_status_router(*, runtime: Any) -> APIRouter:
    router = APIRouter(prefix="/api/agent-status", tags=["agent-status"])

    @router.get("")
    async def status(_: AdminIdentity = Depends(require_admin)) -> dict[str, Any]:
        snapshot = await build_agent_runtime_snapshot(runtime)
        outcomes: dict[str, int] = {}
        for trace in snapshot["recent_traces"]:
            outcome = str(trace.get("outcome") or "")
            if outcome and outcome != "unknown":
                outcomes[outcome] = outcomes.get(outcome, 0) + 1
        overall = (
            "offline"
            if not snapshot["running"]
            else "degraded"
            if not snapshot["enabled"] or snapshot["stale_turns"]
            else "online"
        )
        return {
            "overall": overall,
            "updated_at": snapshot["generated_at"],
            "bots": {
                "connected": len(snapshot["connected_bots"]),
                "ids": [item["bot_id"] for item in snapshot["connected_bots"]],
            },
            "agent_enabled": snapshot["enabled"],
            "running": snapshot["active_turns"],
            "stale": snapshot["stale_turns"],
            "outcomes": outcomes,
            "inner_state": snapshot["inner_state"],
            "metrics": {
                "event_loop_p95_ms": snapshot["event_loop_p95_ms"],
                "turn_p95_ms": snapshot["turn_p95_ms"],
                "rss_bytes": snapshot["rss_bytes"],
                "background_failures": snapshot["background_failures"],
            },
            "recent": snapshot["recent_traces"],
        }

    return router


__all__ = ["build_agent_status_router"]
