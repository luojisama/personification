"""Execution policy derived from an already completed, trusted semantic pass."""
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TurnExecutionPolicy:
    route: str = "agent"
    reason: str = "default_agent"

    @property
    def is_context_only(self) -> bool:
        return self.route == "context_only"


def derive_turn_execution_policy(*, semantic_frame: Any = None, turn_plan: Any = None,
                                 intent_decision: Any = None, has_media: bool = False,
                                 enabled: bool = True) -> TurnExecutionPolicy:
    """Only a successful runtime-labelled LLM classification can remove tools."""
    def agent(reason: str) -> TurnExecutionPolicy:
        return TurnExecutionPolicy("agent", reason)
    if not enabled:
        return agent("disabled")
    plan = turn_plan or getattr(semantic_frame, "turn_plan", None)
    inputs = [value for value in (semantic_frame, plan, intent_decision) if value is not None]
    if not inputs or not any(str(getattr(value, "llm_source", "")) in {"primary", "secondary"} for value in inputs):
        return agent("semantic_source_unconfirmed")
    if any(getattr(value, "fallback_reason", "") or str(getattr(value, "reason", "")).startswith("metadata_fallback") for value in inputs):
        return agent("semantic_fallback")
    intents = [getattr(value, "chat_intent") for value in inputs if hasattr(value, "chat_intent")]
    if not intents or any(intent != "banter" for intent in intents) or any(str(getattr(value, "ambiguity_level", "")) != "low" for value in inputs):
        return agent("non_banter_or_ambiguous")
    if plan is None:
        return agent("turn_plan_unconfirmed")
    if any(getattr(value, "recommend_silence", False) or str(getattr(value, "reply_action", "reply")) == "silence" for value in inputs):
        return agent("silence_required")
    if not all(hasattr(plan, field) for field in (
        "research_need", "evidence_policy", "tool_intent", "memory_need", "memory_queries",
        "speech_act", "output_mode", "vision_need", "ambiguity_level",
    )):
        return agent("turn_plan_incomplete")
    if has_media or any(getattr(value, "media_only_turn", False) or str(getattr(value, "vision_need", "none")) != "none" for value in inputs):
        return agent("media_required")
    for value in inputs:
        if str(getattr(value, "research_need", "none")) != "none" or str(getattr(value, "evidence_policy", "none")) != "none":
            return agent("evidence_required")
        if str(getattr(value, "memory_need", "none")) == "deep" or getattr(value, "memory_queries", None):
            return agent("memory_required")
        intents = getattr(value, "tool_intent", None) or []
        if isinstance(intents, str):
            intents = [intents]
        if any(str(item) not in {"", "none"} for item in intents):
            return agent("tool_required")
        if str(getattr(value, "speech_act", "participate")) not in {"participate", "tease"} or str(getattr(value, "output_mode", "chat_short")) != "chat_short":
            return agent("non_chat_action")
        if getattr(value, "future_commitment_candidate", False) or getattr(value, "qzone_continue", False):
            return agent("state_or_social_action")
    return TurnExecutionPolicy("context_only", "confirmed_low_ambiguity_banter")
