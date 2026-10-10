from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from fastapi.responses import JSONResponse

from ...core.telegram_connections import ConnectionFault, get_connections
from ..deps import require_admin, require_https_or_loopback


MESSAGES = {
    "adapter_missing": "请预装 nonebot-adapter-telegram==0.1.0b20 后重启一次服务。",
    "http_driver_missing": "请安装 HTTP 客户端驱动，并配置 ~fastapi+~httpx 后重启服务。",
    "invalid_token": "Token 无效，请核对 BotFather 提供的值。",
    "receiver_conflict": "该 Bot 已有接收方，请先停止原连接。",
    "webhook_conflict": "该 Bot 已设置 Webhook，请明确迁移后再启用。",
    "native_read_only": "此连接由 NoneBot 配置管理，请在原配置中修改。",
    "stop_before_edit": "请先停止连接再修改。",
}


class SecretSafeRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()
        async def safe(request: Request):
            try:
                return await handler(request)
            except RequestValidationError:
                return JSONResponse(status_code=422, content={"detail": {"code": "invalid_request", "message": "请求格式无效，请检查填写内容。"}})
        return safe


def build_connection_router(*, runtime: Any) -> APIRouter:
    router = APIRouter(prefix="/api/v2/connections", tags=["connections"], dependencies=[Depends(require_admin)], route_class=SecretSafeRoute)

    def service():
        return get_connections(runtime.plugin_config)

    async def run(operation):
        try:
            return await operation
        except ConnectionFault as exc:
            raise HTTPException(404 if exc.code == "not_found" else 409 if "conflict" in exc.code else 400,
                                {"code": exc.code, "message": MESSAGES.get(exc.code, "连接操作未完成，请检查配置与网络。")}) from None
        except Exception:
            raise HTTPException(503, {"code": "connection_unavailable", "message": "连接服务暂不可用，请检查适配器与网络配置。"}) from None

    def run_sync(operation):
        try:
            return operation()
        except ConnectionFault as exc:
            raise HTTPException(404 if exc.code == "not_found" else 409 if "conflict" in exc.code else 400,
                                {"code": exc.code, "message": MESSAGES.get(exc.code, "连接操作未完成，请检查配置与网络。")}) from None
        except Exception:
            raise HTTPException(503, {"code": "connection_unavailable", "message": "连接服务暂不可用，请检查适配器与网络配置。"}) from None

    @router.get("")
    async def listing():
        return {"items": run_sync(lambda: service().list())}

    @router.post("/probe")
    async def probe(request: Request, body: dict[str, Any] = Body(...)):
        require_https_or_loopback(request, code="secure_transport_required", message="请通过 HTTPS 或本机连接提交 Token。")
        return {"connection": await run(service().probe(body))}

    @router.post("")
    async def create(request: Request, body: dict[str, Any] = Body(...)):
        require_https_or_loopback(request, code="secure_transport_required", message="请通过 HTTPS 或本机连接提交 Token。")
        return {"connection": await run(service().save(body))}

    @router.patch("/{ident}")
    async def update(ident: str, request: Request, body: dict[str, Any] = Body(...)):
        require_https_or_loopback(request, code="secure_transport_required", message="请通过 HTTPS 或本机连接修改连接。")
        return {"connection": await run(service().save(body, ident))}

    @router.delete("/{ident}")
    async def delete(ident: str):
        await run(service().delete(ident))
        return {"ok": True}

    @router.post("/{ident}/start")
    async def start(ident: str, body: dict[str, Any] = Body(default={})):
        return {"connection": await run(service().start(ident, migrate_webhook=body.get("migrate_webhook") is True))}

    @router.post("/{ident}/stop")
    async def stop(ident: str):
        await run(service().stop(ident))
        return {"connection": run_sync(lambda: service().status(ident))}

    @router.post("/{ident}/check")
    async def check(ident: str):
        manager = service()
        run_sync(manager.load)
        if ident.startswith("native:"):
            bot = manager.native().get(ident[7:])
            if bot is None:
                raise HTTPException(404, "连接不存在。")
            await run(bot.get_me())
            manager.observed(str(bot.self_id), credentials="verified")
        else:
            row = manager.records.get(ident)
            if row is None:
                raise HTTPException(404, "连接不存在。")
            result = await run(manager.probe(row))
            manager.observed(result["bot_id"], credentials="verified")
        return {"connection": run_sync(lambda: manager.status(ident))}

    @router.get("/{ident}/status")
    async def status(ident: str):
        return {"connection": run_sync(lambda: service().status(ident))}

    @router.post("/{ident}/moderation")
    async def moderation(ident: str, body: dict[str, Any] = Body(...)):
        from ...core.telegram_actions import moderate
        manager = service()
        bot = manager.native().get(ident[7:]) if ident.startswith("native:") else manager.bots.get(ident)
        if bot is None:
            raise HTTPException(409, "Bot 未连接。")
        result = await run(manager.send_guarded(bot, lambda: moderate(bot, str(body.get("action", "")), str(body.get("chat_id", "")), target_id=str(body.get("target_id", "")), message_id=str(body.get("message_id", "")), until_date=body.get("until_date"))))
        return {"state": result.state, "code": result.code, "message_id": result.message_id}

    @router.get("/{ident}/rooms")
    async def rooms(ident: str):
        manager = service()
        bot_id = run_sync(lambda: manager.status(ident))["bot_id"]
        return {"items": run_sync(lambda: manager.rooms(bot_id))}

    @router.patch("/{ident}/rooms")
    async def room_update(ident: str, body: dict[str, Any] = Body(...)):
        manager = service()
        bot_id = run_sync(lambda: manager.status(ident))["bot_id"]
        return {"room": run_sync(lambda: manager.update_room(bot_id, body))}

    return router
