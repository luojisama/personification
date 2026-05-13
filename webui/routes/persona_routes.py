from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from ..deps import AdminIdentity, require_admin


def _profile_service(runtime) -> Any | None:
    bundle = getattr(runtime, "runtime_bundle", None)
    if bundle is None:
        return None
    return getattr(bundle, "profile_service", None)


def build_persona_router(*, runtime) -> APIRouter:
    router = APIRouter(prefix="/api/personas", tags=["personas"])

    @router.get("")
    async def list_core(_: AdminIdentity = Depends(require_admin)) -> dict:
        svc = _profile_service(runtime)
        if svc is None:
            return {"profiles": [], "available": False}
        profiles = svc.list_core_profiles()
        return {
            "profiles": [
                {
                    "user_id": p["user_id"],
                    "snippet": (p["profile_text"] or "")[:140],
                    "updated_at": p.get("updated_at", 0),
                    "source": p.get("source", ""),
                }
                for p in profiles
            ],
            "available": True,
        }

    @router.get("/{user_id}")
    async def detail(user_id: str, _: AdminIdentity = Depends(require_admin)) -> dict:
        svc = _profile_service(runtime)
        if svc is None:
            raise HTTPException(status_code=503, detail="profile_service 未就绪")
        core = svc.get_core_profile(user_id)
        groups = svc.list_groups()
        local_profiles: list[dict[str, Any]] = []
        for gid in groups:
            entry = svc.get_local_profile(group_id=gid, user_id=user_id)
            if entry is None:
                continue
            local_profiles.append(
                {
                    "group_id": gid,
                    "profile_text": entry.profile_text,
                    "profile_json": entry.profile_json,
                    "updated_at": entry.updated_at,
                }
            )
        return {
            "user_id": user_id,
            "core_profile": {
                "profile_text": core.profile_text if core else "",
                "profile_json": core.profile_json if core else {},
                "updated_at": core.updated_at if core else 0,
            } if core else None,
            "local_profiles": local_profiles,
            "prompt_block": svc.build_prompt_block(user_id=user_id, group_id=""),
        }

    return router
