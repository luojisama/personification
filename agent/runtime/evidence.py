from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from .planner import TurnPlan, extract_json_payload, turn_plan_from_semantic_frame


MemoryInjectStyle = Literal["factual", "softened", "drop_due_to_offense_risk", "drop_due_to_stale"]


@dataclass
class EvidenceSynthesis:
    selected_memory_ids: list[str] = field(default_factory=list)
    memory_inject_style: MemoryInjectStyle = "factual"
    tool_evidence_digest: str = ""
    uncertainty_notes: list[str] = field(default_factory=list)
    needs_more_research: bool = False
    research_followup_query: str = ""


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "y", "on"}:
            return True
        if lowered in {"false", "0", "no", "n", "off"}:
            return False
    if value is None:
        return default
    return bool(value)


def _coerce_text_list(value: Any, *, limit: int, item_chars: int) -> list[str]:
    if isinstance(value, str):
        raw_items = [value]
    elif isinstance(value, list):
        raw_items = value
    else:
        raw_items = []
    items: list[str] = []
    for raw in raw_items:
        text = re.sub(r"\s+", " ", str(raw or "")).strip()
        if not text or text in items:
            continue
        items.append(text[:item_chars])
        if len(items) >= limit:
            break
    return items


def _memory_id_set(candidate_memories: list[dict[str, Any]] | None) -> set[str]:
    ids: set[str] = set()
    for item in list(candidate_memories or []):
        if not isinstance(item, dict):
            continue
        memory_id = str(item.get("memory_id", "") or "").strip()
        if memory_id:
            ids.add(memory_id)
    return ids


def parse_evidence_synthesis_payload(
    payload: Any,
    *,
    candidate_memories: list[dict[str, Any]] | None = None,
) -> EvidenceSynthesis | None:
    if not isinstance(payload, dict):
        return None
    allowed_ids = _memory_id_set(candidate_memories)
    selected_ids = _coerce_text_list(payload.get("selected_memory_ids"), limit=12, item_chars=80)
    if allowed_ids:
        selected_ids = [memory_id for memory_id in selected_ids if memory_id in allowed_ids]
    style = str(payload.get("memory_inject_style", "factual") or "factual").strip()
    if style not in {"factual", "softened", "drop_due_to_offense_risk", "drop_due_to_stale"}:
        style = "factual"
    digest = re.sub(r"\s+", " ", str(payload.get("tool_evidence_digest", "") or "")).strip()[:200]
    followup = re.sub(r"\s+", " ", str(payload.get("research_followup_query", "") or "")).strip()[:160]
    needs_more = _coerce_bool(payload.get("needs_more_research"), False)
    if needs_more and not followup:
        needs_more = False
    return EvidenceSynthesis(
        selected_memory_ids=selected_ids,
        memory_inject_style=style,  # type: ignore[arg-type]
        tool_evidence_digest=digest,
        uncertainty_notes=_coerce_text_list(payload.get("uncertainty_notes"), limit=5, item_chars=80),
        needs_more_research=needs_more,
        research_followup_query=followup,
    )


def fallback_evidence_synthesis(
    *,
    candidate_memories: list[dict[str, Any]] | None = None,
    tool_results: list[dict[str, Any]] | None = None,
) -> EvidenceSynthesis:
    selected_ids: list[str] = []
    for item in list(candidate_memories or [])[:3]:
        if not isinstance(item, dict):
            continue
        memory_id = str(item.get("memory_id", "") or "").strip()
        tone_risk = float(item.get("tone_risk", 0) or 0)
        irony_risk = float(item.get("irony_risk", 0) or 0)
        if memory_id and tone_risk < 0.6 and irony_risk < 0.6:
            selected_ids.append(memory_id)
    digest_parts: list[str] = []
    for item in list(tool_results or [])[:3]:
        if not isinstance(item, dict):
            continue
        name = str(item.get("tool_name", "") or item.get("name", "") or "").strip()
        result = re.sub(r"\s+", " ", str(item.get("result", "") or item.get("text", "") or "")).strip()
        if not result:
            continue
        digest_parts.append(f"{name}: {result[:80]}" if name else result[:80])
    return EvidenceSynthesis(
        selected_memory_ids=selected_ids[:12],
        memory_inject_style="factual",
        tool_evidence_digest="；".join(digest_parts)[:200],
        uncertainty_notes=[],
        needs_more_research=False,
        research_followup_query="",
    )


def evidence_synthesizer_enabled(plugin_config: Any) -> bool:
    return bool(getattr(plugin_config, "personification_evidence_synthesizer_enabled", False))


def build_tool_result_record(
    *,
    tool_name: str,
    tool_args: dict[str, Any],
    result: Any,
) -> dict[str, Any]:
    return {
        "tool_name": str(tool_name or "").strip(),
        "args": dict(tool_args or {}),
        "result": str(result or "").strip()[:2400],
    }


def render_evidence_guidance(evidence: EvidenceSynthesis) -> str:
    selected = ", ".join(evidence.selected_memory_ids[:8]) if evidence.selected_memory_ids else "无"
    uncertainty = "；".join(evidence.uncertainty_notes[:4]) if evidence.uncertainty_notes else "无"
    parts = [
        "证据综合器给出的当前可用证据：",
        f"- 选用记忆ID：{selected}",
        f"- 记忆注入方式：{evidence.memory_inject_style}",
        f"- 工具证据摘要：{evidence.tool_evidence_digest or '无'}",
        f"- 不确定点：{uncertainty}",
    ]
    if evidence.needs_more_research and evidence.research_followup_query:
        parts.append(
            "- 后续检索建议："
            f"{evidence.research_followup_query}。如果当前工具还不足以支撑回答，下一步直接调用合适工具；"
            "如果已经足够，就基于现有证据收束回答。"
        )
    else:
        parts.append("- 继续检索：不需要；请基于现有证据直接回答。")
    return "\n".join(parts)


def plan_for_evidence(turn_plan: Any, intent_decision: Any, *, has_images: bool) -> Any:
    if turn_plan is not None:
        return turn_plan
    embedded_plan = getattr(intent_decision, "turn_plan", None)
    if embedded_plan is not None:
        return embedded_plan
    return turn_plan_from_semantic_frame(intent_decision, has_images=has_images)


def _render_turn_plan(plan: TurnPlan | Any) -> str:
    if plan is None:
        return "{}"
    payload = {
        "reply_action": str(getattr(plan, "reply_action", "") or ""),
        "memory_need": str(getattr(plan, "memory_need", "") or ""),
        "research_need": str(getattr(plan, "research_need", "") or ""),
        "vision_need": str(getattr(plan, "vision_need", "") or ""),
        "output_mode": str(getattr(plan, "output_mode", "") or ""),
        "tool_intent": list(getattr(plan, "tool_intent", []) or []),
        "ambiguity_level": str(getattr(plan, "ambiguity_level", "") or ""),
        "session_goal": str(getattr(plan, "session_goal", "") or ""),
    }
    return json.dumps(payload, ensure_ascii=False)


def _render_memories(candidate_memories: list[dict[str, Any]] | None) -> str:
    lines: list[str] = []
    for item in list(candidate_memories or [])[:12]:
        if not isinstance(item, dict):
            continue
        memory_id = str(item.get("memory_id", "") or "").strip()
        summary = re.sub(r"\s+", " ", str(item.get("summary", "") or "")).strip()
        memory_type = str(item.get("memory_type", "") or "").strip()
        zone = str(item.get("palace_zone", "") or "").strip()
        if memory_id and summary:
            lines.append(f"- id={memory_id} type={memory_type or 'unknown'} zone={zone or 'unknown'} summary={summary[:180]}")
    return "\n".join(lines) if lines else "无"


def _render_tool_results(tool_results: list[dict[str, Any]] | None) -> str:
    lines: list[str] = []
    for item in list(tool_results or [])[:8]:
        if not isinstance(item, dict):
            continue
        name = str(item.get("tool_name", "") or item.get("name", "") or "").strip()
        args = item.get("args", {})
        result = re.sub(r"\s+", " ", str(item.get("result", "") or item.get("text", "") or "")).strip()
        lines.append(
            f"- tool={name or 'unknown'} args={json.dumps(args, ensure_ascii=False)[:160]} result={result[:700] or '无'}"
        )
    return "\n".join(lines) if lines else "无"


async def synthesize_evidence_with_llm(
    *,
    tool_caller: Any,
    turn_plan: TurnPlan | Any,
    candidate_memories: list[dict[str, Any]] | None = None,
    tool_results: list[dict[str, Any]] | None = None,
    draft_answer_text: str = "",
    url_summaries: list[str] | None = None,
    group_context: str = "",
    quote_chain: list[dict[str, Any]] | None = None,
) -> EvidenceSynthesis:
    fallback = fallback_evidence_synthesis(
        candidate_memories=candidate_memories,
        tool_results=tool_results,
    )
    if tool_caller is None:
        return fallback
    quote_lines = []
    for item in list(quote_chain or [])[:8]:
        if not isinstance(item, dict):
            continue
        speaker = str(item.get("speaker", "") or item.get("user_id", "") or "").strip()
        content = re.sub(r"\s+", " ", str(item.get("content", "") or "")).strip()
        if content:
            quote_lines.append(f"- {speaker or 'unknown'}: {content[:180]}")
    messages = [
        {
            "role": "system",
            "content": (
                "你是证据综合器。根据 TurnPlan、候选记忆、工具结果、URL 摘要和群聊上下文，"
                "选择哪些记忆适合注入，并把工具证据压缩成最终回复可用的摘要。"
                "只输出严格 JSON，不要 markdown。\n"
                "JSON 结构："
                '{"selected_memory_ids":["..."],'
                '"memory_inject_style":"factual|softened|drop_due_to_offense_risk|drop_due_to_stale",'
                '"tool_evidence_digest":"200字内",'
                '"uncertainty_notes":["..."],'
                '"needs_more_research":false,'
                '"research_followup_query":""}\n'
                "要求：不要选择冒犯风险、过期、明显不相关的记忆。"
                "如果工具结果互相冲突或不足，写 uncertainty_notes；只有确实需要再查时才 needs_more_research=true。"
            ),
        },
        {
            "role": "user",
            "content": (
                f"TurnPlan：{_render_turn_plan(turn_plan)}\n"
                f"候选记忆：\n{_render_memories(candidate_memories)}\n"
                f"工具结果：\n{_render_tool_results(tool_results)}\n"
                f"assistant草稿：{str(draft_answer_text or '').strip()[:600] or '无'}\n"
                f"URL摘要：{json.dumps(_coerce_text_list(url_summaries, limit=5, item_chars=400), ensure_ascii=False)}\n"
                f"群聊上下文：{str(group_context or '').strip()[:900] or '无'}\n"
                f"引用链：\n{chr(10).join(quote_lines) if quote_lines else '无'}"
            ),
        },
    ]
    try:
        response = await tool_caller.chat_with_tools(messages=messages, tools=[], use_builtin_search=False)
    except Exception:
        return fallback
    payload = extract_json_payload(str(getattr(response, "content", "") or ""))
    parsed = parse_evidence_synthesis_payload(payload, candidate_memories=candidate_memories)
    return parsed or fallback


__all__ = [
    "EvidenceSynthesis",
    "build_tool_result_record",
    "evidence_synthesizer_enabled",
    "fallback_evidence_synthesis",
    "plan_for_evidence",
    "parse_evidence_synthesis_payload",
    "render_evidence_guidance",
    "synthesize_evidence_with_llm",
]
