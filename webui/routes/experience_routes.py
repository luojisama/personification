from __future__ import annotations

from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from ...core.experience_cards import ExperienceScope, ExperienceService, ExperienceConflict
from ..deps import AdminIdentity, require_admin


class ExperienceCreate(BaseModel):
    summary: str = Field(min_length=1, max_length=800)
    source_refs: list[dict[str, Any]] = Field(min_length=1, max_length=30)
    kind: str = "experience"
    confirmation: str = "pending"
    expires_at: float = 0


class ExperienceRevision(BaseModel):
    summary: str = Field(min_length=1, max_length=800)
    expected_revision: int = Field(ge=1)
    confirmation: str | None = None
    expires_at: float | None = None


def _scope(platform: str, bot_id: str, group_id: str = "", user_id: str = "", topic_id: str = "") -> ExperienceScope:
    try:
        return ExperienceScope(platform, bot_id, group_id, user_id, topic_id)
    except ValueError:
        raise HTTPException(422, "请指定平台、Bot 和群或用户作用域。") from None


def build_experience_router(*, runtime) -> APIRouter:
    router = APIRouter(prefix="/api/v2/experiences", tags=["experiences"])

    def selected_scope(platform: str, bot_id: str, group_id: str = "", user_id: str = "", topic_id: str = "") -> ExperienceScope:
        # Accept the visible Telegram IDs used by the UI; preserve internal
        # namespaces for trace links and existing administrative clients.
        if platform == "telegram" and bot_id.isdecimal():
            from ...core.interaction_adapter import InteractionEnvelope
            from ...core.telegram_connections import get_connections
            canonical = get_connections(runtime.plugin_config).canonical_chat(bot_id, group_id) if group_id else ""
            envelope = InteractionEnvelope(platform, bot_id, "group" if group_id else "private", canonical, user_id, "", topic_id=topic_id)
            return _scope(platform, envelope.bot_namespace, envelope.conversation_namespace if group_id else "", envelope.sender_namespace if user_id else "", "")
        return _scope(platform, bot_id, group_id, user_id, topic_id)

    def service(write=False):
        if write and not bool(getattr(runtime.plugin_config, "personification_experience_cards_enabled", False)):
            raise HTTPException(403, "共同经历功能尚未启用。")
        bundle = getattr(runtime, "runtime_bundle", None)
        store = getattr(bundle, "memory_store", None) or getattr(runtime, "memory_store", None)
        if store is None or not store.palace_enabled():
            raise HTTPException(503, "记忆存储尚未就绪。")
        return ExperienceService(store)

    def run(call):
        try:
            return call()
        except ExperienceConflict:
            raise HTTPException(409, "经历已被更新，请刷新后重试。") from None
        except KeyError:
            raise HTTPException(404, "此作用域内没有该经历。") from None
        except ValueError as exc:
            raise HTTPException(422, {"code": str(exc), "message": "请检查内容、来源消息及身份资料有效期。"}) from None

    @router.get("")
    async def listing(scope: ExperienceScope = Depends(selected_scope), limit: int = Query(50, ge=1, le=200), _: AdminIdentity = Depends(require_admin)):
        return {"items": run(lambda: service().list(scope, limit=limit)), "enabled": bool(getattr(runtime.plugin_config, "personification_experience_cards_enabled", False))}

    @router.post("")
    async def create(body: ExperienceCreate, scope: ExperienceScope = Depends(selected_scope), _: AdminIdentity = Depends(require_admin)):
        return run(lambda: service(True).create(scope, **body.model_dump()))

    @router.patch("/{memory_id}")
    async def revise(memory_id: str, body: ExperienceRevision, scope: ExperienceScope = Depends(selected_scope), _: AdminIdentity = Depends(require_admin)):
        return run(lambda: service(True).revise(scope, memory_id, **body.model_dump()))

    @router.delete("/{memory_id}")
    async def forget(memory_id: str, expected_revision: int = Query(..., ge=1), scope: ExperienceScope = Depends(selected_scope), _: AdminIdentity = Depends(require_admin)):
        # Forgetting remains available after disabling recall.
        run(lambda: service().forget(scope, memory_id, expected_revision=expected_revision))
        return {"ok": True}

    return router
