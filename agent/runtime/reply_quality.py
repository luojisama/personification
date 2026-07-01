from __future__ import annotations

import time
from typing import Any, Callable

from ...core.context_policy import strip_response_control_markers
from ...core.metrics import record_counter, record_timing
from ...core.reply_text_policy import (
    looks_like_formulaic_reply_tic,
    looks_like_markdown_reply,
    normalize_visible_reply_text,
)
from ...core.response_review import is_agent_reply_ooc, rewrite_agent_reply_ooc
from .final_synthesis import AgentResult


_CONTROL_REPLIES = frozenset({"[NO_REPLY]", "<NO_REPLY>", "[SILENCE]", "<SILENCE>"})


def _is_control_reply(text: str) -> bool:
    return str(text or "").strip() in _CONTROL_REPLIES


def _is_direct_media_reply(text: str) -> bool:
    value = str(text or "").strip()
    return value.startswith("[IMAGE_B64]") and value.endswith("[/IMAGE_B64]")


def _turn_plan_output_mode(turn_plan: Any) -> str:
    return str(getattr(turn_plan, "output_mode", "") or "").strip() or "chat_short"


def _persona_system_from_messages(messages: list[dict[str, Any]]) -> str:
    for message in list(messages or []):
        if not isinstance(message, dict) or message.get("role") != "system":
            continue
        content = str(message.get("content", "") or "").strip()
        if content:
            return content[:1200]
    return ""


def _copy_result_with_quality(
    result: AgentResult,
    *,
    text: str,
    check: dict[str, Any],
) -> AgentResult:
    checks = list(getattr(result, "quality_checks", []) or [])
    checks.append(check)
    return AgentResult(
        text=text,
        pending_actions=list(getattr(result, "pending_actions", []) or []),
        direct_output=bool(getattr(result, "direct_output", False)),
        bypass_length_limits=bool(getattr(result, "bypass_length_limits", False)),
        quality_checks=checks,
    )


def _quality_flags(raw_text: str, visible_text: str) -> list[str]:
    flags: list[str] = []
    if looks_like_markdown_reply(raw_text):
        flags.append("markdown_or_trace")
    if looks_like_formulaic_reply_tic(raw_text):
        flags.append("formulaic_tic")
    if is_agent_reply_ooc(raw_text):
        flags.append("style_risk")
    if visible_text != str(raw_text or "").strip():
        flags.append("normalized")
    if not visible_text:
        flags.append("empty_after_normalize")
    return flags


async def finalize_agent_reply_quality(
    result: AgentResult,
    *,
    tool_caller: Any,
    messages: list[dict[str, Any]],
    turn_plan: Any = None,
    record_trace: Callable[..., None] | None = None,
    logger: Any = None,
    reason: str = "",
) -> AgentResult:
    """Run one final output-style quality pass for Agent text.

    This is deliberately an output hygiene layer: it normalizes visible text,
    detects assistant/OOC/formulaic surface patterns, and optionally asks the
    model for one rewrite. It does not decide user intent, emotion, or whether
    a normal chat turn should be routed to a feature.
    """

    started_at = time.monotonic()
    raw_text = str(getattr(result, "text", "") or "").strip()
    direct_output = bool(getattr(result, "direct_output", False))
    skipped = direct_output or _is_control_reply(raw_text) or _is_direct_media_reply(raw_text)
    if skipped:
        check = {
            "action": "skipped",
            "reason": "direct_or_control",
            "source": str(reason or ""),
            "flags": [],
            "revision_attempted": False,
            "elapsed_ms": int((time.monotonic() - started_at) * 1000),
            "original_chars": len(raw_text),
            "final_chars": len(raw_text),
        }
        if record_trace is not None:
            record_trace(
                key="agent_reply_quality",
                label="Agent 回复质量",
                status="info",
                detail=(
                    f"action=skipped reason=direct_or_control source={reason or '-'} "
                    f"flags=- chars={len(raw_text)}"
                ),
            )
        record_counter("agent.reply_quality_total", action="skipped")
        return _copy_result_with_quality(result, text=raw_text, check=check)

    stripped = strip_response_control_markers(raw_text)
    visible_text = normalize_visible_reply_text(stripped)
    flags = _quality_flags(raw_text, visible_text)
    action = "accept"
    final_text = visible_text or raw_text
    revision_attempted = False

    if flags and tool_caller is not None:
        revision_attempted = True
        rewritten = await rewrite_agent_reply_ooc(
            tool_caller=tool_caller,
            original_text=raw_text,
            persona_system=_persona_system_from_messages(messages),
            timeout=8.0,
            output_mode=_turn_plan_output_mode(turn_plan),
        )
        if rewritten:
            final_text = normalize_visible_reply_text(strip_response_control_markers(rewritten))
            action = "rewritten"

    if not final_text:
        final_text = "[SILENCE]"
        action = "silenced"
    elif flags and is_agent_reply_ooc(final_text):
        final_text = "[SILENCE]"
        action = "silenced"
    elif flags and action != "rewritten":
        action = "normalized" if final_text != raw_text else "accepted_with_flags"

    elapsed_ms = int((time.monotonic() - started_at) * 1000)
    flags_text = ",".join(flags) if flags else "-"
    record_timing("agent.reply_quality_ms", elapsed_ms, action=action)
    record_counter("agent.reply_quality_total", action=action)
    check = {
        "action": action,
        "reason": str(reason or ""),
        "flags": flags,
        "revision_attempted": revision_attempted,
        "elapsed_ms": elapsed_ms,
        "original_chars": len(raw_text),
        "final_chars": len(final_text),
    }
    if record_trace is not None:
        status = "ok" if action in {"accept", "normalized", "skipped"} else "warn"
        record_trace(
            key="agent_reply_quality",
            label="Agent 回复质量",
            status=status,
            detail=(
                f"action={action} source={reason or '-'} flags={flags_text} "
                f"revision={str(revision_attempted).lower()} elapsed_ms={elapsed_ms} "
                f"chars={len(raw_text)}->{len(final_text)}"
            ),
            hint=(
                "命中输出风格风险后已做一次修订或静默；这只处理可见文本风格，不替代对话语义判断"
                if flags
                else ""
            ),
        )
    if logger is not None and flags:
        try:
            logger.debug(f"[agent] reply quality action={action} flags={flags_text}")
        except Exception:
            pass
    return _copy_result_with_quality(result, text=final_text, check=check)


__all__ = [
    "finalize_agent_reply_quality",
]
