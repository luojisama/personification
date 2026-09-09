"""Locally bound memory provider for text-only Agent surfaces."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from .memory_context import prepare_memory_context, render_history


def build_surface_memory_provider(*, config: Any, store: Any, caller: Any, policy: Any = None, profiles: Any = None):
    async def prepare(*, messages: list[dict], scope: dict, surface: str) -> dict | str:
        if not all(str(scope.get(key) or "") for key in ("platform", "bot_id")):
            return ""
        user_id, group_id = str(scope.get("user_id") or ""), str(scope.get("group_id") or "")
        if not user_id and not group_id:
            return ""
        history = []
        if group_id:
            from ..utils import get_recent_group_msgs
            from .history_config import effective_history_days, effective_history_message_limit
            history = await asyncio.to_thread(get_recent_group_msgs, group_id,
                limit=effective_history_message_limit(config, private=False)[0],
                expire_hours=(effective_history_days(config, private=False)[0] or 0)*24,
                platform=scope["platform"], bot_id=scope["bot_id"])
            history = [{**m, "role": "assistant" if str(m.get("user_id") or "") == scope["bot_id"] else "user", "timestamp": m.get("time", m.get("timestamp", 0))} for m in history]
        # QZone is a separate public surface. Never read private chat merely
        # because its author is also a known private correspondent.
        qzone = "qzone" in surface
        if user_id and policy is not None and user_id != scope["bot_id"]:
            auth = await policy.current_authorization(user_id)
            if not auth.allow_context_read:
                return ""
        if not group_id and not qzone:
            from .session_store import get_recent_session_candidates, build_private_session_id
            from .history_config import effective_history_days, effective_history_message_limit
            history = await asyncio.to_thread(get_recent_session_candidates, build_private_session_id(user_id),
                limit=effective_history_message_limit(config, private=True)[0],
                days=effective_history_days(config, private=True)[0] or 0,
                platform=scope["platform"], bot_id=scope["bot_id"])
            history = [{**m, "user_id": str(m.get("user_id") or (scope["bot_id"] if m.get("role") == "assistant" else user_id))} for m in history]
        if policy is not None:
            history, _ = await policy.filter_context_messages(history, bot_self_id=scope["bot_id"])
        else:
            history = []  # No policy binding cannot authorize new historical sources.
        query = [dict(m) for m in messages if m.get("role") == "user"][-1:]
        from .context_budget import fit_history_to_budget, primary_route_budget
        history, _ = fit_history_to_budget(history, fixed_messages=messages, budget=primary_route_budget(config))
        runtime = SimpleNamespace(plugin_config=config, memory_store=store, lite_tool_caller=caller, scoped_profile_service=profiles)
        from .llm_context import set_llm_context, reset_llm_context
        token = set_llm_context(**{k: str(scope.get(k) or "") for k in ("platform", "bot_id", "user_id", "group_id")}, purpose=surface)
        try:
            prepared = await prepare_memory_context(runtime=runtime, event=SimpleNamespace(user_id=user_id, group_id=group_id),
                bot=SimpleNamespace(self_id=scope["bot_id"]), messages=history + query,
                surface=surface, refresh=False)
            if qzone:
                prepared.states = []
                prepared.memories = [m for m in prepared.memories if m.get("permission_type") == "public_preference"
                                     and m.get("visibility") == "public"]
            historical = prepared.history[:-len(query)] if query else prepared.history
            rendered = render_history(historical, prepared.timezone)
            return {"role": "user", "content": rendered + "\n" + prepared.render(),
                    "_context_history": historical, "_context_history_rendered": rendered,
                    "_context_history_renderer": lambda selected: render_history(selected, prepared.timezone)}
        finally:
            reset_llm_context(token)
    return prepare
