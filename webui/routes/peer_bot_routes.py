from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request

from ...core import webui_audit_log
from ...core.command_runtime_context import has_runtime_command_prefix
from ...core.db import connect_sync
from ...core.operation_diagnostics import detail, diagnostic, step
from ...core.peer_bot_observer import PeerBotObservationPacket
from ...core.peer_bot_registry import PeerBotRegistryError
from ..deps import AdminIdentity, get_client_ip, require_admin


def _component(runtime: Any, name: str) -> Any | None:
    bundle = getattr(runtime, "runtime_bundle", None)
    value = getattr(bundle, name, None) if bundle is not None else None
    return value if value is not None else getattr(runtime, name, None)


def _raise_failure(
    *,
    status_code: int,
    code: str,
    phase: str,
    title: str,
    message: str,
    suggestion: str = "刷新状态后重试。",
    details: tuple = (),
    steps: tuple = (),
) -> None:
    raise HTTPException(
        status_code=status_code,
        detail=diagnostic(
            ok=False,
            code=code,
            phase=phase,
            title=title,
            message=message,
            details=details,
            steps=steps,
            suggestion=suggestion,
            retryable=status_code >= 500,
            partial=False,
            outcome_unknown=False,
        ),
    )


def _success(
    payload: dict[str, Any],
    *,
    code: str,
    title: str,
    message: str,
    audit_ok: bool = True,
) -> dict[str, Any]:
    return {
        **payload,
        **diagnostic(
            ok=True,
            code=code,
            phase="operation_complete",
            title=title,
            message=message,
            steps=(
                step("validate", "校验 Peer Bot 管理请求", "ok", "请求字段与作用域有效。"),
                step("persist", "提交 Peer Bot 状态", "ok", "目标状态已明确返回。"),
                step(
                    "audit",
                    "记录管理员操作",
                    "ok" if audit_ok else "warn",
                    "审计记录已保存。" if audit_ok else "业务结果已确认，但审计记录写入失败。",
                ),
            ),
            warnings=() if audit_ok else ("业务结果已确认，但本次管理员审计记录未能写入。",),
            retryable=False,
            partial=not audit_ok,
            outcome_unknown=False,
        ),
    }


def _audit(
    runtime: Any,
    *,
    request: Request,
    admin: AdminIdentity,
    action: str,
    target: str,
    audit_detail: dict[str, Any],
) -> bool:
    try:
        webui_audit_log.record(
            action=action,
            qq=admin.qq,
            device_id=admin.device_id,
            target=target,
            ip_hash=get_client_ip(request),
            detail=audit_detail,
        )
        return True
    except Exception as exc:
        logger = getattr(runtime, "logger", None)
        if logger is not None:
            logger.warning(
                f"[peer bot operation] audit_failed action={action} "
                f"exception={type(exc).__name__}"
            )
        return False


def _registry_or_503(runtime: Any) -> Any:
    registry = _component(runtime, "peer_bot_registry")
    if registry is None:
        _raise_failure(
            status_code=503,
            code="peer_bot_registry_unavailable",
            phase="preflight",
            title="Peer Bot 注册表未就绪",
            message="当前无法读取或更新群 Peer Bot 配置。",
            suggestion="等待插件运行时完成初始化后重试。",
        )
    return registry


def _recent_discovery_batches(
    runtime: Any,
    registry: Any,
    group_id: str,
) -> list[list[PeerBotObservationPacket]]:
    """Load a bounded, chronological projection without returning chat text."""

    config = getattr(runtime, "plugin_config", None)
    per_user = max(
        1,
        min(
            32,
            int(getattr(config, "personification_peer_bot_detector_batch_max_messages", 8) or 8),
        ),
    )
    max_users = 8
    try:
        with connect_sync() as conn:
            rows = conn.execute(
                """
                SELECT user_id,nickname,content,message_id,sender_role,
                       reply_to_msg_id,mentioned_ids,is_at_bot,timestamp
                FROM group_messages
                WHERE group_id=? AND user_id<>'' AND content<>'' AND is_bot=0
                ORDER BY timestamp DESC,id DESC
                LIMIT ?
                """,
                (str(group_id), per_user * max_users * 4),
            ).fetchall()
    except Exception:
        return []
    try:
        known_bots = registry.get_group(group_id).get("bots", {})
    except Exception:
        known_bots = {}
    grouped: dict[str, list[Any]] = {}
    for row in rows:
        user_id = str(row["user_id"] or "").strip()
        if not user_id:
            continue
        known = known_bots.get(user_id) if isinstance(known_bots, dict) else None
        if isinstance(known, dict) and known.get("status") in {"approved", "rejected"}:
            continue
        bucket = grouped.setdefault(user_id, [])
        if len(bucket) < per_user:
            bucket.append(row)
        if len(grouped) >= max_users and all(len(items) >= per_user for items in grouped.values()):
            break
    batches: list[list[PeerBotObservationPacket]] = []
    for user_id, user_rows in list(grouped.items())[:max_users]:
        packets: list[PeerBotObservationPacket] = []
        for row in reversed(user_rows):
            try:
                mentioned = json.loads(str(row["mentioned_ids"] or "[]"))
            except (TypeError, ValueError, json.JSONDecodeError):
                mentioned = []
            text = " ".join(str(row["content"] or "").split())[:500]
            if not text:
                continue
            packets.append(
                PeerBotObservationPacket(
                    group_id=str(group_id),
                    user_id=user_id,
                    nickname=" ".join(str(row["nickname"] or user_id).split())[:80],
                    text=text,
                    message_id=str(row["message_id"] or "")[:100],
                    sender_role=str(row["sender_role"] or "member")[:24],
                    reply_to_message_id=str(row["reply_to_msg_id"] or "")[:100],
                    mentioned_user_ids=tuple(
                        str(item)[:80]
                        for item in mentioned[:8]
                        if str(item).strip()
                    ) if isinstance(mentioned, list) else (),
                    is_at_bot=bool(row["is_at_bot"]),
                    has_command_structure=has_runtime_command_prefix(text),
                    created_at=float(row["timestamp"] or 0.0),
                )
            )
        if packets:
            batches.append(packets)
    return batches


def _registry_error(exc: PeerBotRegistryError, *, group_id: str) -> None:
    code = str(exc or "peer_bot_registry_invalid")[:80]
    _raise_failure(
        status_code=400,
        code=code if code.startswith("peer_bot_") else f"peer_bot_{code}",
        phase="request_validation",
        title="Peer Bot 配置未保存",
        message="请求不符合已确认的 Bot、模板或循环保护约束。",
        suggestion="检查 Bot ID、完整模板、参数 schema、风险等级和状态后重试。",
        details=(detail("目标群", group_id), detail("校验码", code, "error")),
        steps=(step("validate", "校验 Peer Bot 管理请求", "error", code),),
    )


def _public_group_state(runtime: Any, group_id: str) -> dict[str, Any]:
    registry = _registry_or_503(runtime)
    try:
        group = registry.get_group(group_id)
        bots = registry.list_group_bots(group_id)
    except PeerBotRegistryError as exc:
        _registry_error(exc, group_id=group_id)
    except Exception:
        _raise_failure(
            status_code=500,
            code="peer_bot_registry_read_failed",
            phase="persistence",
            title="Peer Bot 状态读取失败",
            message="注册表未能返回可确认的群级状态。",
        )

    commands = [
        command
        for command in group.get("commands", {}).values()
        if isinstance(command, dict)
    ]
    commands.sort(key=lambda item: (str(item.get("target_bot_id", "")), str(item.get("command_id", ""))))
    candidates = [
        {
            "user_id": bot.get("user_id", ""),
            "nickname": bot.get("nickname", ""),
            "confidence": bot.get("confidence", 0.0),
            "source": bot.get("source", "llm_observation"),
            "evidence_tags": list(bot.get("evidence_tags") or []),
            "reason_code": "peer_bot_candidate",
        }
        for bot in bots
        if bot.get("status") == "candidate"
    ]
    tracker = _component(runtime, "peer_bot_tracker")
    loop_protection = tracker.snapshot(group_id=group_id) if tracker is not None else {
        "pending_count": 0,
        "recent_count": 0,
        "cooldown_count": 0,
        "max_chain_depth": 1,
        "diagnostics": {},
    }
    recent_invocations = (
        [episode.safe_summary(include_reply_content=False) for episode in tracker.recent_episodes(group_id, limit=10)]
        if tracker is not None
        else []
    )
    observer = _component(runtime, "peer_bot_observer")
    observer_state = observer.snapshot_stats() if observer is not None else {
        "enabled": False,
        "pending_messages": 0,
        "pending_users": 0,
    }
    return {
        "group_id": str(group_id),
        "enabled": bool(group.get("enabled", False)),
        "bots": bots,
        "commands": commands,
        "discovery_suggestions": candidates,
        "max_command_chars": registry.max_command_chars,
        "policies": group.get("policies", {}),
        "pending_count": int(loop_protection.get("pending_count", 0) or 0),
        "loop_protection": loop_protection,
        "recent_invocations": recent_invocations,
        "observer": observer_state,
        "updated_at": group.get("updated_at", 0.0),
        "diagnostic_code": "peer_bot_state_ready",
    }


def build_peer_bot_group_router(*, runtime: Any) -> APIRouter:
    router = APIRouter(prefix="/{group_id}/peer-bots", tags=["groups", "peer-bots"])

    @router.get("")
    async def get_peer_bots(
        group_id: str,
        _: AdminIdentity = Depends(require_admin),
    ) -> dict[str, Any]:
        return _public_group_state(runtime, group_id)

    @router.put("/settings")
    async def update_settings(
        group_id: str,
        request: Request,
        body: dict[str, Any] = Body(default_factory=dict),
        admin: AdminIdentity = Depends(require_admin),
    ) -> dict[str, Any]:
        registry = _registry_or_503(runtime)
        allowed = {
            "enabled",
            "max_calls_per_turn",
            "cooldown_seconds",
            "pending_ttl_seconds",
            "max_chain_depth",
            "auto_learn_approved_commands",
        }
        if set(body) - allowed:
            _raise_failure(
                status_code=400,
                code="peer_bot_settings_unknown_field",
                phase="request_validation",
                title="Peer Bot 设置未保存",
                message="请求包含未声明的群级策略字段。",
            )
        try:
            updated = registry.set_settings(
                group_id,
                enabled=body.get("enabled") if "enabled" in body else None,
                max_calls_per_turn=body.get("max_calls_per_turn"),
                cooldown_seconds=body.get("cooldown_seconds"),
                pending_ttl_seconds=body.get("pending_ttl_seconds"),
                max_chain_depth=body.get("max_chain_depth"),
                auto_learn_approved_commands=(
                    body.get("auto_learn_approved_commands")
                    if "auto_learn_approved_commands" in body
                    else None
                ),
            )
        except PeerBotRegistryError as exc:
            _registry_error(exc, group_id=group_id)
        except Exception:
            _raise_failure(
                status_code=500,
                code="peer_bot_settings_persist_failed",
                phase="persistence",
                title="Peer Bot 设置保存失败",
                message="群级策略的持久化结果未能确认。",
            )
        audit_ok = _audit(
            runtime,
            request=request,
            admin=admin,
            action="peer_bot_settings_update",
            target=group_id,
            audit_detail={"enabled": updated.get("enabled"), "policies": updated.get("policies", {})},
        )
        return _success(
            {"success": True, "settings": updated},
            code="peer_bot_settings_saved",
            title="Peer Bot 设置已保存",
            message="群开关和循环保护参数已更新。",
            audit_ok=audit_ok,
        )

    @router.put("/{user_id}")
    async def update_bot_status(
        group_id: str,
        user_id: str,
        request: Request,
        body: dict[str, Any] = Body(default_factory=dict),
        admin: AdminIdentity = Depends(require_admin),
    ) -> dict[str, Any]:
        registry = _registry_or_503(runtime)
        if set(body) - {"action", "nickname"}:
            _raise_failure(
                status_code=400,
                code="peer_bot_status_unknown_field",
                phase="request_validation",
                title="Peer Bot 状态未更新",
                message="请求包含未声明的 Bot 状态字段。",
            )
        action = str(body.get("action", "") or "").strip().lower()
        try:
            bot_state = registry.set_bot_status(
                group_id,
                user_id=user_id,
                action=action,
                nickname=body.get("nickname", ""),
            )
        except PeerBotRegistryError as exc:
            _registry_error(exc, group_id=group_id)
        except Exception:
            _raise_failure(
                status_code=500,
                code="peer_bot_status_persist_failed",
                phase="persistence",
                title="Peer Bot 状态更新失败",
                message="管理员覆盖状态的持久化结果未能确认。",
            )
        audit_ok = _audit(
            runtime,
            request=request,
            admin=admin,
            action="peer_bot_status_update",
            target=f"{group_id}:{user_id}",
            audit_detail={"action": action},
        )
        return _success(
            {"success": True, "bot": bot_state},
            code="peer_bot_status_saved",
            title="Peer Bot 状态已更新",
            message="管理员覆盖已提交；候选不会因此自动启用群调用。",
            audit_ok=audit_ok,
        )

    @router.put("/{user_id}/commands/{command_id}")
    async def update_command(
        group_id: str,
        user_id: str,
        command_id: str,
        request: Request,
        body: dict[str, Any] = Body(default_factory=dict),
        admin: AdminIdentity = Depends(require_admin),
    ) -> dict[str, Any]:
        registry = _registry_or_503(runtime)
        allowed = {
            "full_template",
            "command_entry",
            "subcommands",
            "argument_template",
            "description",
            "parameter_schema",
            "risk_level",
            "status",
        }
        if set(body) - allowed:
            _raise_failure(
                status_code=400,
                code="peer_bot_command_unknown_field",
                phase="request_validation",
                title="Peer Bot 命令未保存",
                message="请求包含未声明的命令字段。",
            )
        status = str(body.get("status", "candidate") or "candidate").strip().lower()
        if status not in {"candidate", "approved", "rejected"}:
            _raise_failure(
                status_code=400,
                code="peer_bot_invalid_command_status",
                phase="request_validation",
                title="Peer Bot 命令未保存",
                message="命令状态必须是 candidate、approved 或 rejected。",
            )
        try:
            command = registry.upsert_command(
                group_id,
                target_bot_id=user_id,
                command_id=command_id,
                full_template=body.get("full_template"),
                command_entry=body.get("command_entry"),
                subcommands=body.get("subcommands"),
                argument_template=body.get("argument_template"),
                description=body.get("description", ""),
                parameter_schema=body.get("parameter_schema", {}),
                risk_level=body.get("risk_level", "read"),
                status=status,
                source="manual",
                manual_override=True,
            )
        except PeerBotRegistryError as exc:
            _registry_error(exc, group_id=group_id)
        except Exception:
            _raise_failure(
                status_code=500,
                code="peer_bot_command_persist_failed",
                phase="persistence",
                title="Peer Bot 命令保存失败",
                message="完整模板的持久化结果未能确认。",
            )
        audit_ok = _audit(
            runtime,
            request=request,
            admin=admin,
            action="peer_bot_command_upsert",
            target=f"{group_id}:{user_id}:{command_id}",
            audit_detail={
                "command_id": command_id,
                "risk_level": command.get("risk_level"),
                "status": command.get("status"),
                "version": command.get("version"),
            },
        )
        return _success(
            {"success": True, "command": command},
            code="peer_bot_command_saved",
            title="Peer Bot 命令已保存",
            message="完整命令模板和参数 schema 已通过机械校验。",
            audit_ok=audit_ok,
        )

    @router.delete("/{user_id}/commands/{command_id}")
    async def delete_command(
        group_id: str,
        user_id: str,
        command_id: str,
        request: Request,
        admin: AdminIdentity = Depends(require_admin),
    ) -> dict[str, Any]:
        registry = _registry_or_503(runtime)
        try:
            deleted = registry.delete_command(
                group_id,
                target_bot_id=user_id,
                command_id=command_id,
            )
        except PeerBotRegistryError as exc:
            _registry_error(exc, group_id=group_id)
        except Exception:
            _raise_failure(
                status_code=500,
                code="peer_bot_command_delete_failed",
                phase="persistence",
                title="Peer Bot 命令删除失败",
                message="删除结果未能确认。",
            )
        audit_ok = _audit(
            runtime,
            request=request,
            admin=admin,
            action="peer_bot_command_delete",
            target=f"{group_id}:{user_id}:{command_id}",
            audit_detail={"command_id": command_id, "deleted": deleted},
        )
        return _success(
            {"success": True, "deleted": deleted},
            code="peer_bot_command_deleted" if deleted else "peer_bot_command_delete_noop",
            title="Peer Bot 命令已删除" if deleted else "Peer Bot 命令无需删除",
            message="命令模板已移除。" if deleted else "目标命令模板当前不存在。",
            audit_ok=audit_ok,
        )

    @router.post("/discover")
    async def discover(
        group_id: str,
        request: Request,
        admin: AdminIdentity = Depends(require_admin),
    ) -> dict[str, Any]:
        observer = _component(runtime, "peer_bot_observer")
        if observer is None:
            _raise_failure(
                status_code=503,
                code="peer_bot_observer_unavailable",
                phase="preflight",
                title="Peer Bot 观察器未就绪",
                message="当前无法执行受限候选发现。",
            )
        try:
            results = await observer.flush_group(group_id)
            if not results:
                registry = _registry_or_503(runtime)
                for packets in _recent_discovery_batches(runtime, registry, group_id):
                    results.append(await observer.evaluate_packets(packets))
        except Exception:
            _raise_failure(
                status_code=500,
                code="peer_bot_discovery_failed",
                phase="model_observation",
                title="Peer Bot 候选发现失败",
                message="观察器未能完成已有微批的评估。",
            )
        audit_ok = _audit(
            runtime,
            request=request,
            admin=admin,
            action="peer_bot_discover",
            target=group_id,
            audit_detail={"evaluated_count": len(results)},
        )
        return _success(
            {
                "success": True,
                "evaluated_count": len(results),
                "results": results,
                "state": _public_group_state(runtime, group_id),
            },
            code="peer_bot_discovery_completed",
            title="Peer Bot 候选发现已完成",
            message="只评估了当前群已缓冲的观察微批；结果不会自动获得调用权限。",
            audit_ok=audit_ok,
        )

    @router.post("/reset-loop")
    async def reset_loop(
        group_id: str,
        request: Request,
        admin: AdminIdentity = Depends(require_admin),
    ) -> dict[str, Any]:
        tracker = _component(runtime, "peer_bot_tracker")
        if tracker is None:
            _raise_failure(
                status_code=503,
                code="peer_bot_tracker_unavailable",
                phase="preflight",
                title="Peer Bot 循环保护未就绪",
                message="当前无法显式复位本群的进程内状态。",
            )
        try:
            snapshot = tracker.reset_loop(group_id=group_id)
        except Exception:
            _raise_failure(
                status_code=500,
                code="peer_bot_loop_reset_failed",
                phase="runtime_state",
                title="Peer Bot 循环保护复位失败",
                message="进程内 pending 与 cooldown 状态未能确认清除。",
            )
        audit_ok = _audit(
            runtime,
            request=request,
            admin=admin,
            action="peer_bot_loop_reset",
            target=group_id,
            audit_detail={"pending_count": snapshot.get("pending_count", 0)},
        )
        return _success(
            {"success": True, "loop_protection": snapshot},
            code="peer_bot_loop_reset_completed",
            title="Peer Bot 循环保护已复位",
            message="本群进程内 pending 与 cooldown 已显式清除，不会自动重发。",
            audit_ok=audit_ok,
        )

    return router


__all__ = ["build_peer_bot_group_router"]
