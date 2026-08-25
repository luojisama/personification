from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter
from fastapi.routing import APIRoute

from .audit_routes import build_audit_router
from .auth_routes import build_auth_router
from .config_routes import build_config_router
from .data_transfer_routes import build_data_transfer_router
from .group_routes import build_group_router
from .log_routes import build_log_router
from .mcp_routes import build_mcp_router
from .memory_routes import build_memory_router
from .outbound_routes import build_outbound_router
from .persona_routes import build_persona_router
from .persona_template_routes import build_persona_template_router
from .plugin_knowledge_routes import build_plugin_knowledge_router
from .qq_routes import build_qq_router
from .qzone_routes import build_qzone_router
from .skill_routes import build_skill_router
from .sticker_routes import build_sticker_router
from .test_routes import build_test_router
from .tool_creator_routes import build_tool_creator_router
from .user_policy_routes import build_user_policy_router


def _clone_routes(
    target: APIRouter,
    source: APIRouter,
    *,
    rewrite: Callable[[str], str | None],
) -> None:
    """Expose the same authenticated handler under a typed v2 namespace.

    The endpoint object, request validation, CSRF dependency, audit behavior and
    response model are shared with the compatibility route.  No HTTP proxy and
    no duplicate business implementation is introduced.
    """

    for route in source.routes:
        if not isinstance(route, APIRoute):
            continue
        destination = rewrite(str(route.path))
        if not destination:
            continue
        target.add_api_route(
            destination,
            route.endpoint,
            methods=sorted(route.methods or {"GET"}),
            response_model=route.response_model,
            status_code=route.status_code,
            tags=["v2-business"],
            summary=route.summary,
            description=route.description,
            response_description=route.response_description,
            responses=route.responses,
            deprecated=False,
            name=f"v2_{route.name}",
            response_class=route.response_class,
        )


def _prefix(source_prefix: str, destination_prefix: str, *, skip_root: bool = False) -> Callable[[str], str | None]:
    def rewrite(path: str) -> str | None:
        if not path.startswith(source_prefix):
            return None
        suffix = path[len(source_prefix) :]
        if skip_root and suffix in {"", "/"}:
            return None
        return f"{destination_prefix}{suffix}"

    return rewrite


def _device_path(path: str) -> str | None:
    prefix = "/api/auth"
    if not path.startswith(prefix):
        return None
    suffix = path[len(prefix) :]
    if suffix == "/logout" or suffix.startswith(("/devices", "/pending-devices", "/trusted-devices")):
        return f"/api/v2/device-management{suffix}"
    return None


def build_v2_business_router(*, runtime: Any) -> APIRouter:
    router = APIRouter(tags=["v2-business"])
    specs: tuple[tuple[APIRouter, Callable[[str], str | None]], ...] = (
        (build_persona_router(runtime=runtime), _prefix("/api/personas", "/api/v2/persona-details", skip_root=True)),
        (build_group_router(runtime=runtime), _prefix("/api/groups", "/api/v2/group-management", skip_root=True)),
        (build_memory_router(runtime=runtime), _prefix("/api/memory", "/api/v2/memory")),
        (build_sticker_router(runtime=runtime), _prefix("/api/stickers", "/api/v2/sticker-management", skip_root=True)),
        (build_skill_router(runtime=runtime), _prefix("/api/skills", "/api/v2/skill-management", skip_root=True)),
        (build_mcp_router(runtime=runtime), _prefix("/api/mcp", "/api/v2/mcp-management")),
        (build_tool_creator_router(runtime=runtime), _prefix("/api/tool-creator", "/api/v2/tool-creator")),
        (build_plugin_knowledge_router(runtime=runtime), _prefix("/api/plugin-knowledge", "/api/v2/plugin-knowledge-management", skip_root=True)),
        (build_persona_template_router(runtime=runtime), _prefix("/api/persona-template", "/api/v2/persona-builder")),
        (build_config_router(runtime=runtime), _prefix("/api/config", "/api/v2/config-tools")),
        (build_test_router(runtime=runtime), _prefix("/api/test", "/api/v2/model-tests")),
        (build_qzone_router(runtime=runtime), _prefix("/api/qzone", "/api/v2/qzone-management")),
        (build_user_policy_router(runtime=runtime), _prefix("/api/user-policy", "/api/v2/user-policies")),
        (build_outbound_router(runtime=runtime), _prefix("/api/outbound", "/api/v2/outbound")),
        (build_data_transfer_router(runtime=runtime), _prefix("/api/data-transfer", "/api/v2/data-transfer")),
        (build_audit_router(runtime=runtime), _prefix("/api/audit", "/api/v2/audit")),
        (build_log_router(runtime=runtime), _prefix("/api/logs", "/api/v2/log-management")),
        (build_qq_router(runtime=runtime), _prefix("/api/qq", "/api/v2/qq-management")),
        (build_auth_router(runtime=runtime), _device_path),
    )
    for source, rewrite in specs:
        _clone_routes(router, source, rewrite=rewrite)
    return router


__all__ = ["build_v2_business_router"]
