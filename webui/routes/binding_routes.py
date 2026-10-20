"""Manual binding review uses the existing administrator and CSRF boundary."""
import os
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from ..deps import require_admin, AdminIdentity


class ReviewBody(BaseModel):
    approve: bool
    note: str = Field(min_length=1, max_length=500)


def build_binding_router():
    router = APIRouter(tags=['binding-review'], dependencies=[Depends(require_admin)])

    def service():
        path = os.environ.get('PERSONIFICATION_BINDING_DB', '')
        app = os.environ.get('PERSONIFICATION_BINDING_LEGACY_APP', '')
        if not path or not app:
            raise HTTPException(503, '人工绑定服务尚未配置')
        from shared_accounts import SharedAccounts
        from manual_binding import ManualBinding
        return ManualBinding(SharedAccounts(path), app)

    @router.get('/binding-review', response_class=HTMLResponse)
    async def page():
        return HTMLResponse(Path(__file__).with_name('binding_review.html').read_text(encoding='utf-8'),
                            headers={'Cache-Control':'no-store'})

    @router.get('/api/binding-requests')
    async def requests():
        return {'items':service().list(), 'ready':os.environ.get('PERSONIFICATION_BINDING_READY') == 'true'}

    @router.post('/api/binding-requests/{request_id}/review')
    async def review(request_id: str, body: ReviewBody, admin: AdminIdentity = Depends(require_admin)):
        from shared_accounts import Conflict
        try:
            return service().review(request_id, approve=body.approve, reviewer=admin.qq,
                                    note=body.note, ready=os.environ.get('PERSONIFICATION_BINDING_READY') == 'true')
        except (Conflict, ValueError) as exc:
            raise HTTPException(409, str(exc)) from exc
    return router
