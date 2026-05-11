from __future__ import annotations

import json
import re
from typing import Any


_GROUP_KNOWLEDGE_PROMPT = (
    "你是群聊知识抽取器。下面是一段群聊对话摘要。"
    "请从中抽取 3-10 个高频话题、专有名词、群内绰号或内部梗。"
    "每个条目一句话说明含义（20字内）。只输出 JSON 数组，不要 markdown。"
    "\n格式：[{\"term\":\"专有名词\",\"definition\":\"一句话说明\"}]"
)


async def build_group_knowledge(
    *,
    tool_caller: Any,
    memory_store: Any,
    group_id: str,
    chat_summary: str,
) -> int:
    if not tool_caller or not memory_store or not chat_summary:
        return 0
    try:
        response = await tool_caller.chat_with_tools(
            messages=[
                {"role": "system", "content": _GROUP_KNOWLEDGE_PROMPT},
                {"role": "user", "content": str(chat_summary)[:3000]},
            ],
            tools=[],
            use_builtin_search=False,
        )
    except Exception:
        return 0

    content = str(getattr(response, "content", "") or "").strip()
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        match = re.search(r"\[[\s\S]*\]", content)
        if not match:
            return 0
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return 0
    if not isinstance(data, list):
        return 0

    saved = 0
    for item in data:
        if not isinstance(item, dict):
            continue
        term = str(item.get("term", "") or "").strip()
        definition = str(item.get("definition", "") or "").strip()
        if not term or len(term) < 2:
            continue
        memory_id = f"gk_{_safe_id(group_id)}_{_safe_id(term)}"
        memory_store.write_memory_item({
            "memory_id": memory_id,
            "memory_type": "group_knowledge",
            "summary": f"{term}: {definition}",
            "term": term,
            "definition": definition,
            "group_id": str(group_id),
            "confidence": 0.7,
            "salience": 0.5,
            "source_kind": "auto_extract",
        })
        saved += 1
    return saved


def query_group_knowledge(
    memory_store: Any,
    group_id: str,
    message_text: str,
    *,
    top_k: int = 8,
) -> list[dict[str, Any]]:
    if not memory_store:
        return []
    entries = memory_store.list_recent_memories(
        group_id=str(group_id),
        memory_type="group_knowledge",
        limit=200,
    )
    matched: list[tuple[int, dict[str, Any]]] = []
    text_lower = str(message_text or "").strip().lower()
    for entry in entries:
        term = str(entry.get("term", "") or "").strip()
        if term and term.lower() in text_lower:
            summary = str(entry.get("summary", "") or "").strip()
            matched.append((len(term), {"term": term, "summary": summary}))
    matched.sort(key=lambda item: item[0], reverse=True)
    return [item[1] for item in matched[:top_k]]


def format_group_knowledge_hint(entries: list[dict[str, Any]]) -> str:
    if not entries:
        return ""
    lines = ["群组已知专有名词/话题："]
    for entry in entries:
        term = entry.get("term", "")
        summary = entry.get("summary", "")
        if term and summary:
            lines.append(f"- {term}: {summary}")
    if len(lines) <= 1:
        return ""
    lines.append("上面这些是群友常聊的，你已知晓。遇到这些词不要再触发 web_search/lookup_web。")
    return "\n".join(lines)


def _safe_id(text: str) -> str:
    safe = re.sub(r"[^\w\u4e00-\u9fff]+", "_", str(text or "").strip().lower())
    return safe.strip("_")[:40] or "unknown"


__all__ = [
    "build_group_knowledge",
    "format_group_knowledge_hint",
    "query_group_knowledge",
]
