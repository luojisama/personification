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
    memories = await store.arecall_memories(
        query=str(query or ""),
        scope=str(scope or "auto"),
        **recall_kwargs,
        mode=str(mode or "auto"),
    )
    from plugin.personification.core.services.agent_runtime import (
        active_linked_tool_evidence, collect_linked_tool_evidence,
    )

    own_scope = {key: value for key, value in recall_kwargs.items() if key != "context_type"}
    memories = [{**item, "source": "current_user_memory", "source_scope": own_scope} for item in memories]
    shared = await collect_linked_tool_evidence(
        runtime, request_scope, query=str(query or ""), scope=str(scope or "auto"),
        mode=str(mode or "auto"), limit=32,
    )
    background_intelligence = getattr(runtime, "background_intelligence", None)
    if background_intelligence is not None:
        # Reinforcement stays with the current actor's existing memory lane.
        background_intelligence.schedule_recall_reinforcement(
            [str(item.get("memory_id", "") or "") for item in memories]
        )
    # Recheck after every asynchronous source read and local scheduling callback.
    memories.extend(await active_linked_tool_evidence(runtime, shared))
    memories.sort(key=lambda item: -float(item.get("score", 0) or 0))
    memories = memories[:32]
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
            "私聊也查询已验证绑定账号的私有记忆和经历，逐条标明原平台来源；群聊不共享绑定侧内容。"
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
