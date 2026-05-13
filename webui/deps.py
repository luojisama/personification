from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request

from ..core import webui_auth_store
from .schemas import AdminIdentity


_COOKIE_NAME = "personification_webui_token"


def get_client_ip(request: Request) -> str:
    """取 X-Forwarded-For 头第一段，否则取 client.host。"""
    forwarded = request.headers.get("x-forwarded-for", "") or ""
    if forwarded:
        first = forwarded.split(",")[0].strip()
        if first:
            return first
    client = request.client
    return getattr(client, "host", "") or ""


def get_user_agent(request: Request) -> str:
    return request.headers.get("user-agent", "")[:512]


def require_admin(request: Request) -> AdminIdentity:
    """FastAPI Depends：要求合法 device token，返回管理员身份。"""
    token = request.cookies.get(_COOKIE_NAME, "")
    if not token:
        raise HTTPException(status_code=401, detail="未登录")
    ua = get_user_agent(request)
    record = webui_auth_store.lookup_device(token, ua=ua)
    if not record:
        raise HTTPException(status_code=401, detail="设备令牌无效或已被撤销")
    import hashlib

    return AdminIdentity(
        qq=str(record.get("qq", "")),
        device_id=hashlib.sha256(token.encode("utf-8")).hexdigest(),
        label=str(record.get("label", "")),
    )


def get_cookie_name() -> str:
    return _COOKIE_NAME
