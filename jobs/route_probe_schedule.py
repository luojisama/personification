"""Scheduling adapter for safe daily route probes.

It deliberately receives route enumeration and probe execution callbacks from
runtime assembly: this module cannot obtain a Bot or invoke any QQ action.
"""
from __future__ import annotations

from datetime import datetime
import asyncio
from zoneinfo import ZoneInfo
from typing import Any, Awaitable, Callable, Iterable

from ..core.route_probe_service import ProbeResult, RouteProbeService

ProbeTarget = tuple[str, str, Callable[[], Awaitable[ProbeResult]]]

def due_day_key(now: datetime, *, timezone_name: str, hour: int, minute: int) -> str:
    local = now.astimezone(ZoneInfo(timezone_name))
    from datetime import timedelta
    if (local.hour, local.minute) < (int(hour), int(minute)):
        local = local - timedelta(days=1)
    return local.date().isoformat()


def schedule_daily_route_probes(
    *, scheduler: Any, service: RouteProbeService,
    targets: Callable[[], Iterable[ProbeTarget]], enabled: bool | Callable[[], bool],
    timezone_name: str = "Asia/Shanghai", probe_version: str = "v1", hour: int = 3, minute: int = 30,
    route_budget_seconds: float = 480.0,
) -> Callable[[], Awaitable[None]] | None:
    """Register one coalescing 03:30 job; missed runs coalesce rather than replay."""
    job_id = "personification_daily_route_probes"
    try:
        scheduler.remove_job(job_id)
    except Exception:
        pass
    if not callable(enabled) and not enabled:
        return None

    async def run() -> None:
        if not (enabled() if callable(enabled) else enabled):
            return
        # Retention belongs to the shared service so scheduled and manual
        # operations use the same configured retention policy.
        prune = getattr(service, "prune_history", None)
        if callable(prune):
            prune()
        else:  # compatibility with lightweight/older test doubles
            service.store.prune()
        day = due_day_key(datetime.now(ZoneInfo(timezone_name)), timezone_name=timezone_name, hour=hour, minute=minute)
        if not service.store.claim_due_day(day, status="scheduled"):
            return
        grouped: dict[str, list[tuple[str, Callable[[], Awaitable[ProbeResult]]]]] = {}
        for fingerprint, capability, runner in targets():
            bucket = grouped.setdefault(str(fingerprint), [])
            if not any(existing[0] == str(capability) for existing in bucket):
                bucket.append((str(capability), runner))
        for fingerprint, entries in grouped.items():
            deadline = asyncio.get_running_loop().time() + max(1.0, float(route_budget_seconds))
            for capability, runner in entries:
                if asyncio.get_running_loop().time() >= deadline:
                    break
                operation = await service.queue(route_fingerprint=fingerprint, capability=capability, runner=runner, source="daily", probe_version=probe_version, day_key=day)
                remaining = max(0.0, deadline - asyncio.get_running_loop().time())
                try:
                    await asyncio.wait_for(service.wait(operation["operation_id"]), timeout=remaining)
                except asyncio.TimeoutError:
                    await service.cancel(operation["operation_id"])

    scheduler.add_job(
        run, "cron", id=job_id, hour=max(0, min(23, int(hour))), minute=max(0, min(59, int(minute))), timezone=ZoneInfo(timezone_name),
        jitter=90, coalesce=True, misfire_grace_time=60 * 60, replace_existing=True,
    )
    return run
