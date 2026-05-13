from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from ...core import config_registry, env_writer
from ..deps import AdminIdentity, require_admin
from ..schemas import (
    ConfigEntriesResponse,
    ConfigEntryView,
    ConfigUpdateRequest,
    ConfigUpdateResponse,
)


def _entry_to_view(entry: Any, *, plugin_config: Any) -> ConfigEntryView:
    sources = env_writer.resolve_value_sources(entry.field_name, plugin_config)
    return ConfigEntryView(
        key=entry.key,
        field_name=entry.field_name,
        label=entry.display_name or entry.key,
        description=entry.description or "",
        group=entry.group or "其他",
        kind=entry.kind or "text",
        value_type=entry.value_type,
        required=bool(entry.required),
        secret=bool(entry.secret),
        default=getattr(type(plugin_config), entry.field_name, entry.default),
        current=sources.get("current"),
        active_source=sources.get("active_source", "default"),
        sources={
            "env_file": sources.get("env_file"),
            "env_json": sources.get("env_json"),
            "runtime_config": sources.get("runtime_config"),
            "default": sources.get("default"),
        },
        choices=list(entry.choices or []),
        min_value=entry.min_value,
        max_value=entry.max_value,
        scope=entry.scope,
    )


def build_config_router(*, runtime) -> APIRouter:
    router = APIRouter(prefix="/api/config", tags=["config"])

    @router.get("/entries", response_model=ConfigEntriesResponse)
    async def list_entries(_: AdminIdentity = Depends(require_admin)) -> ConfigEntriesResponse:
        entries = [
            _entry_to_view(entry, plugin_config=runtime.plugin_config)
            for entry in config_registry.get_config_entries("global")
        ]
        # 对 secret 字段返回时遮码，前端不应看到明文
        for view in entries:
            if view.secret:
                if isinstance(view.current, str) and view.current:
                    view.current = "***"
                if isinstance(view.sources.get("env_file"), str) and view.sources["env_file"]:
                    view.sources["env_file"] = "***"
                if isinstance(view.sources.get("env_json"), str) and view.sources["env_json"]:
                    view.sources["env_json"] = "***"
        groups = sorted({view.group for view in entries})
        return ConfigEntriesResponse(entries=entries, groups=groups)

    @router.post("/value", response_model=ConfigUpdateResponse)
    async def update_value(
        payload: ConfigUpdateRequest,
        _: AdminIdentity = Depends(require_admin),
    ) -> ConfigUpdateResponse:
        field_name = payload.field_name.strip()
        if not field_name:
            raise HTTPException(status_code=400, detail="field_name 不能为空")
        entry = None
        for candidate in config_registry.get_config_entries("global"):
            if candidate.field_name == field_name:
                entry = candidate
                break
        if entry is None:
            raise HTTPException(status_code=404, detail=f"未知字段 {field_name}")
        try:
            normalized = entry.normalize_value(payload.value)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"取值非法：{exc}") from exc
        result = env_writer.write_both(field_name, normalized, runtime.plugin_config)
        # 同步当前进程中的 plugin_config 实例（不依赖重启）
        try:
            setattr(runtime.plugin_config, field_name, normalized)
        except Exception as exc:
            result["errors"].append(f"运行时同步失败：{exc}")
        return ConfigUpdateResponse(
            success=not result["errors"],
            errors=list(result["errors"]),
            dotenv_path=result.get("dotenv_path"),
            env_json_path=result.get("env_json_path"),
            new_value=normalized if not entry.secret else "***",
        )

    return router
