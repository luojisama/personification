from __future__ import annotations

import json
from typing import Any

from plugin.personification.agent.tool_registry import AgentTool
from plugin.personification.core.memory_store import get_memory_store
from plugin.personification.core.memory_request_scope import MemoryRequestScope, memory_tools_enabled


def _tool_enabled(runtime: Any) -> bool:
    try:
        store = getattr(runtime, "memory_store", None) or get_memory_store()
        return memory_tools_enabled(
            plugin_config=getattr(runtime, "plugin_config", None), memory_store=store
        )
    except Exception:
        return False


async def recall_memory(
    *,
    runtime: Any,
    query: str,
    scope: str = "auto",
    user_id: str = "",
    group_id: str = "",
    mode: str = "auto",
) -> str:
    try:
        store = getattr(runtime, "memory_store", None) or get_memory_store()
    except Exception:
        return json.dumps(
            {"query": str(query or ""), "scope": str(scope or "auto"), "mode": str(mode or "auto"), "memories": []},
            ensure_ascii=False,
        )
    plugin_config = getattr(runtime, "plugin_config", None)
    request_scope = MemoryRequestScope.from_runtime(
        plugin_config=plugin_config, memory_store=store
    )
    if not request_scope.can_recall:
        return json.dumps(
            {"query": str(query or ""), "scope": str(scope or "auto"), "mode": str(mode or "auto"), "memories": []},
            ensure_ascii=False,
        )
    requested_user = str(user_id or "").strip()
    requested_group = str(group_id or "").strip()
    if (requested_user and requested_user != request_scope.user_id) or (
        requested_group and requested_group != request_scope.group_id
    ):
        return json.dumps(
            {"query": str(query or ""), "scope": str(scope or "auto"), "mode": str(mode or "auto"), "memories": [], "note": "请求的记忆范围与当前会话不一致"},
            ensure_ascii=False,
        )
    recall_kwargs = (
        request_scope.group_recall_kwargs()
        if request_scope.context_type == "group"
        else request_scope.actor_recall_kwargs()
    )
    memories = store.recall_memories(
        query=str(query or ""),
        scope=str(scope or "auto"),
        **recall_kwargs,
        mode=str(mode or "auto"),
    )
    background_intelligence = getattr(runtime, "background_intelligence", None)
    if background_intelligence is not None:
        background_intelligence.schedule_recall_reinforcement(
            [str(item.get("memory_id", "") or "") for item in memories]
        )
    payload = {
        "query": str(query or ""),
        "scope": str(scope or "auto"),
        "mode": str(mode or "auto"),
        "memories": memories,
    }
    return json.dumps(payload, ensure_ascii=False)


def build_memory_recall_tool(runtime: Any) -> AgentTool:
    async def _handler(
        query: str,
        scope: str = "auto",
        user_id: str = "",
        group_id: str = "",
        mode: str = "auto",
    ) -> str:
        return await recall_memory(
            runtime=runtime,
            query=query,
            scope=scope,
            user_id=user_id,
            group_id=group_id,
            mode=mode,
        )

    return AgentTool(
        name="memory_recall",
        description=(
            "回忆近期聊过的情节、人物印象、群聊上下文和长期主题。"
            "适合“上次聊到哪了”“你还记得吗”“这个人在这个群里平时怎么聊”这类场景。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "当前问题或回忆目标"},
                "scope": {"type": "string", "description": "auto/recent_episode/person/group/topic/self/future"},
                "mode": {"type": "string", "description": "auto/fast/deep；其中 deep 当前是启发式深度重排，不是完整 LLM 深度策略"},
            },
            "required": ["query"],
        },
        handler=_handler,
        enabled=lambda: _tool_enabled(runtime),
    )
