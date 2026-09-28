from __future__ import annotations

from typing import Any

from ..core.peer_bot_registry import PeerBotRegistryError


def _peer_bot_usage() -> str:
    return (
        "用法：\n"
        "- 拟人 群Bot 列表\n"
        "- 拟人 群Bot 发现\n"
        "- 拟人 群Bot 确认 <QQ号>\n"
        "- 拟人 群Bot 忽略 <QQ号>\n"
        "- 拟人 群Bot 启用|停用\n"
        "- 拟人 群Bot 自动学习 启用|停用\n"
        "- 拟人 群Bot 命令 添加 <QQ号> <read|write|admin|dangerous> <完整命令模板>\n"
        "- 拟人 群Bot 命令 确认 <QQ号> <命令ID>\n"
        "- 拟人 群Bot 命令 删除 <QQ号> <命令ID>\n"
        "- 拟人 群Bot 循环复位"
    )


def _format_peer_bot_list(registry: Any, tracker: Any, group_id: str) -> str:
    group = registry.get_group(group_id)
    bots = registry.list_group_bots(group_id)
    lines = [
        f"本群 Peer Bot：{'已启用' if group.get('enabled') else '已停用'}",
        (
            "策略：单回合 1 次 / 深度 1 / "
            f"冷却 {group.get('policies', {}).get('cooldown_seconds', 10)} 秒 / "
            f"等待 {group.get('policies', {}).get('pending_ttl_seconds', 30)} 秒"
        ),
    ]
    if not bots:
        lines.append("暂无 Bot 候选或管理员配置。")
    commands = group.get("commands", {})
    for bot in bots:
        lines.append(
            f"- {bot.get('nickname') or '未命名'} ({bot.get('user_id')}): "
            f"{bot.get('status')} / 置信度 {float(bot.get('confidence', 0) or 0):.2f} / "
            f"来源 {bot.get('source', 'unknown')}"
        )
        for command_id in bot.get("command_ids", []):
            command = commands.get(command_id)
            if not isinstance(command, dict):
                continue
            lines.append(
                f"  · {command_id}: {command.get('full_template')} "
                f"[{command.get('risk_level')}/{command.get('status')}]"
            )
    if tracker is not None:
        snapshot = tracker.snapshot(group_id=group_id)
        lines.append(f"进程内等待：{snapshot.get('pending_count', 0)}；冷却项：{snapshot.get('cooldown_count', 0)}")
    lines.append("候选不会自动获得调用权限；admin/dangerous 模板即使确认也不会被 Agent 调用。")
    return "\n".join(lines)


async def handle_peer_bot_command(
    bundle: Any,
    *,
    group_id: str,
    tokens: list[str],
) -> str:
    registry = getattr(bundle, "peer_bot_registry", None)
    observer = getattr(bundle, "peer_bot_observer", None)
    tracker = getattr(bundle, "peer_bot_tracker", None)
    if registry is None:
        return "Peer Bot 注册表当前不可用。"
    action = str(tokens[0] if tokens else "列表").strip().lower()
    try:
        if action in {"列表", "list"}:
            return _format_peer_bot_list(registry, tracker, group_id)
        if action in {"发现", "discover"}:
            if observer is None:
                return "Peer Bot 观察器当前不可用。"
            results = await observer.flush_group(group_id)
            return (
                f"已评估本群 {len(results)} 个已缓冲观察微批；只会产生待确认候选。\n"
                + _format_peer_bot_list(registry, tracker, group_id)
            )
        if action in {"启用", "enable"}:
            registry.set_settings(group_id, enabled=True)
            return "已启用本群 Peer Bot 协作；仍只允许管理员确认过的 Bot 和命令。"
        if action in {"停用", "disable"}:
            registry.set_settings(group_id, enabled=False)
            return "已停用本群 Peer Bot 协作。"
        if action in {"自动学习", "auto-learn", "autolearn"}:
            if len(tokens) != 2:
                return _peer_bot_usage()
            mode = str(tokens[1]).strip().lower()
            if mode not in {"启用", "enable", "on", "停用", "disable", "off"}:
                return _peer_bot_usage()
            enabled = mode in {"启用", "enable", "on"}
            registry.set_settings(group_id, auto_learn_approved_commands=enabled)
            if enabled:
                return "已启用本群 Peer Bot 协议自动学习；仅批准 Bot 的高置信 read/write 新协议可自动启用。"
            return "已停用本群 Peer Bot 协议自动学习；已批准命令保持不变。"
        if action in {"循环复位", "reset-loop", "reset"}:
            if tracker is None:
                return "Peer Bot 循环保护当前不可用。"
            snapshot = tracker.reset_loop(group_id=group_id)
            return f"已复位本群进程内循环保护；pending={snapshot.get('pending_count', 0)}，不会自动重发。"
        if action in {"确认", "approve", "忽略", "reject"}:
            if len(tokens) != 2 or not str(tokens[1]).isdigit():
                return _peer_bot_usage()
            normalized_action = "approve" if action in {"确认", "approve"} else "reject"
            bot_state = registry.set_bot_status(
                group_id,
                user_id=str(tokens[1]),
                action=normalized_action,
            )
            return f"已将 {tokens[1]} 标记为 {bot_state.get('status') if bot_state else 'candidate'}。"
        if action not in {"命令", "command", "commands"} or len(tokens) < 2:
            return _peer_bot_usage()

        command_action = str(tokens[1]).strip().lower()
        if command_action in {"添加", "add"}:
            if len(tokens) < 5 or not str(tokens[2]).isdigit():
                return _peer_bot_usage()
            risk_level = str(tokens[3]).strip().lower()
            full_template = " ".join(tokens[4:]).strip()
            command = registry.upsert_command(
                group_id,
                target_bot_id=str(tokens[2]),
                full_template=full_template,
                parameter_schema=None,
                risk_level=risk_level,
                status="candidate",
                source="manual",
                manual_override=True,
            )
            return (
                f"已添加待确认命令 {command.get('command_id')}：{command.get('full_template')} "
                f"[{command.get('risk_level')}]。请再执行“拟人 群Bot 命令 确认 {tokens[2]} {command.get('command_id')}”。"
            )
        if command_action in {"确认", "approve", "删除", "delete", "remove"}:
            if len(tokens) != 4 or not str(tokens[2]).isdigit():
                return _peer_bot_usage()
            if command_action in {"确认", "approve"}:
                command = registry.set_command_status(
                    group_id,
                    target_bot_id=str(tokens[2]),
                    command_id=str(tokens[3]),
                    action="approve",
                )
                return f"命令 {command.get('command_id')} 已确认；风险等级 {command.get('risk_level')}。"
            deleted = registry.delete_command(
                group_id,
                target_bot_id=str(tokens[2]),
                command_id=str(tokens[3]),
            )
            return "命令已删除。" if deleted else "目标命令不存在，无需删除。"
        return _peer_bot_usage()
    except PeerBotRegistryError as exc:
        return f"Peer Bot 管理失败：{str(exc)}"
    except Exception as exc:  # noqa: BLE001 - administrator command isolation
        logger = getattr(bundle, "logger", None)
        if logger is not None:
            try:
                logger.warning(f"拟人插件：Peer Bot 管理命令异常: {type(exc).__name__}")
            except Exception:  # noqa: BLE001 - logging failure cannot expose private command details
                return "Peer Bot 管理失败：peer_bot_admin_operation_failed"
        return "Peer Bot 管理失败：peer_bot_admin_operation_failed"
