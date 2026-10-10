"""Transport capability filtering; tool names are protocol identifiers, not intent."""
from __future__ import annotations

from typing import Any

# Existing QQ-bound implementations cannot operate on a Telegram namespace.
_QQ_TOOLS = frozenset({
    "send_qq_face", "send_qq_favorite_expression", "send_qq_recommended_expression",
    "recall_latest_own_output", "list_peer_bots", "invoke_peer_bot", "invoke_plugin",
    "inspect_current_user_avatar", "inspect_group_user_avatar_pair",
    "inspect_group_member_avatar", "match_group_member_avatar_asset",
    "get_current_group_context", "controlled_group_moderation", "poke_user",
    "get_group_info", "get_friend_list", "send_friend_request", "request_friend",
    "add_friend", "list_groups", "get_group_list", "get_group_members",
})


def is_telegram_context(event: Any) -> bool:
    envelope = getattr(event, "_personification_interaction_envelope", None)
    return str(getattr(envelope, "platform", "") or getattr(event, "platform", "")) == "telegram"


def filter_telegram_tools(registry: Any, event: Any) -> Any:
    """Filter the turn-owned clone, never mutate the shared base registry."""
    if not is_telegram_context(event):
        return registry
    remove = set()
    for tool in registry.all():
        name = str(tool.name)
        metadata = tool.metadata or {}
        tags = metadata.get("intent_tags") or []
        platforms = metadata.get("supported_platforms")
        if (
            name in _QQ_TOOLS or "qzone" in name or name.startswith(("qq_", "send_qq_", "peer_bot_"))
            or any(tag in {"peer_bot", "qq", "qzone", "plugin_action"} for tag in tags)
            or (isinstance(platforms, (list, tuple, set)) and "telegram" not in platforms)
        ):
            remove.add(name)
    registry.remove_names(remove)
    return registry
