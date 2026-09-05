from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any, List

from .model_router import MODEL_ROLE_SPLITTER, get_model_override_for_role


_SPLITTER_PROMPT_TEMPLATE = """\
你是即时通讯（QQ/微信）消息分段助手。
请将输入的回复内容切分为符合真人打字习惯的 1 到 {max_segments} 条简短消息。
【严格规则】：
1. 必须完全保留原文本中的每一个字、标点、语气助词，严禁改写、增删、润色或总结内容。
2. 按照自然的语义停顿切分，避免任何一条出现长篇大论。
3. 严格只输出一个标准 JSON 字符串数组，例如：["消息1", "消息2"]。
4. 严禁输出任何 Markdown 标记（如 ```json）、思考过程或解释文字。
"""


def _rule_based_fallback(text: str, max_segment_chars: int = 0) -> List[str]:
    clean = str(text or "").strip()
    if not clean:
        return []
    parts = re.split(r"\n\s*\n+", clean)
    segments = [p.strip() for p in parts if p.strip()]
    if max_segment_chars and max_segment_chars > 0:
        from ..handlers.event_rules import split_segment_if_long

        expanded: List[str] = []
        for seg in segments:
            expanded.extend(split_segment_if_long(seg, max_segment_chars))
        segments = [s.strip() for s in expanded if s.strip()]
    return segments if segments else [clean]


def _parse_splitter_json_output(raw_output: str) -> List[str] | None:
    text = str(raw_output or "").strip()
    if not text:
        return None
    # 尝试剥离外层 markdown 代码块
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        text = text.strip()
    # 查找 JSON array
    match = re.search(r"\[\s*\".*?\"\s*\]", text, re.DOTALL)
    candidate = match.group(0) if match else text
    try:
        parsed = json.loads(candidate)
        if isinstance(parsed, list):
            items = [str(item).strip() for item in parsed if str(item).strip()]
            if items:
                return items
    except Exception:
        pass
    return None


async def split_reply_with_llm(
    text: str,
    runtime: Any,
    *,
    max_segments: int | None = None,
    min_chars: int | None = None,
    timeout_seconds: float = 2.5,
    response_deadline: float | None = None,
) -> List[str]:
    raw_text = str(text or "").strip()
    if not raw_text:
        return []

    plugin_config = getattr(runtime, "plugin_config", None)
    logger = getattr(runtime, "logger", None)
    max_seg_chars = int(getattr(plugin_config, "personification_max_segment_chars", 0) or 0)

    # 1. 开关检查
    enabled = bool(getattr(plugin_config, "personification_enable_llm_splitter", False))
    if not enabled:
        return _rule_based_fallback(raw_text, max_seg_chars)

    effective_min_chars = (
        int(min_chars)
        if min_chars is not None
        else int(getattr(plugin_config, "personification_splitter_min_chars", 35) or 35)
    )
    effective_max_segments = (
        int(max_segments)
        if max_segments is not None
        else int(getattr(plugin_config, "personification_splitter_max_segments", 3) or 3)
    )
    effective_max_segments = max(1, min(effective_max_segments, 6))

    # 2. 短文本快速旁路（0 延迟）
    if len(raw_text) <= effective_min_chars and "\n\n" not in raw_text:
        return [raw_text]

    # 3. 确定 Caller
    caller = None
    override_model = get_model_override_for_role(plugin_config, MODEL_ROLE_SPLITTER)
    configured_model = str(getattr(plugin_config, "personification_splitter_model", "") or "").strip() or override_model
    configured_provider = str(getattr(plugin_config, "personification_splitter_provider", "") or "").strip()

    # 如果配置了独立 provider 或 model，构建对应 caller
    if configured_provider or configured_model:
        try:
            from .service_factory import create_tool_caller

            caller = create_tool_caller(
                plugin_config=plugin_config,
                provider_override=configured_provider or None,
                model_override=configured_model or None,
                role=MODEL_ROLE_SPLITTER,
            )
        except Exception as e:
            if logger:
                logger.debug(f"[message_splitter] 构建独立 caller 失败，回退默认 caller: {e}")
            caller = None

    if caller is None:
        caller = getattr(runtime, "lite_tool_caller", None) or getattr(runtime, "agent_tool_caller", None)

    if caller is None or not hasattr(caller, "chat_with_tools"):
        return _rule_based_fallback(raw_text, max_seg_chars)

    # 4. 构造 Prompt 并发起调用
    messages = [
        {
            "role": "system",
            "content": _SPLITTER_PROMPT_TEMPLATE.format(max_segments=effective_max_segments),
        },
        {
            "role": "user",
            "content": raw_text,
        },
    ]

    # A caller factory may have spent part of the shared turn budget.  Measure
    # again immediately before creating its network coroutine: an expired turn
    # must not begin a new provider request.
    remaining_timeout = float(timeout_seconds)
    if isinstance(response_deadline, (int, float)):
        remaining_timeout = min(remaining_timeout, float(response_deadline) - time.monotonic())
        if remaining_timeout <= 0:
            return _rule_based_fallback(raw_text, max_seg_chars)

    try:
        response = await asyncio.wait_for(
            caller.chat_with_tools(messages, [], False),
            timeout=remaining_timeout,
        )
        content = str(getattr(response, "content", "") or "").strip()
        parsed_segments = _parse_splitter_json_output(content)
        if parsed_segments:
            # The splitter runs after final review.  It may choose bubble
            # boundaries only; even a same-length word substitution is not an
            # approved visible reply.
            combined_parsed = "".join(re.sub(r"\s+", "", s) for s in parsed_segments)
            combined_raw = re.sub(r"\s+", "", raw_text)
            if combined_parsed == combined_raw:
                if len(parsed_segments) <= effective_max_segments:
                    return parsed_segments
                # A count limit cannot justify discarding reviewed text.  Keep
                # the approved order and merge the tail into the last bubble.
                return [
                    *parsed_segments[: effective_max_segments - 1],
                    "".join(parsed_segments[effective_max_segments - 1 :]),
                ]
            elif logger:
                logger.debug("[message_splitter] 模型分段结果内容与原文差异过大，降级规则分段")
    except asyncio.TimeoutError:
        if logger:
            logger.debug(f"[message_splitter] 调用超时 ({timeout_seconds}s)，降级规则分段")
    except Exception as e:
        if logger:
            logger.debug(f"[message_splitter] 调用异常: {e}，降级规则分段")

    return _rule_based_fallback(raw_text, max_seg_chars)


__all__ = [
    "split_reply_with_llm",
]
