from __future__ import annotations

import re
import time
from typing import Any, Callable
from urllib.parse import urlparse

from ...core.context_policy import strip_response_control_markers
from ...core.evidence_envelope import EvidenceEnvelope
from ...core.metrics import record_counter, record_timing
from ...core.media_refs import normalize_audio_ref
from ...core.reply_text_policy import (
    looks_like_formulaic_reply_tic,
    looks_like_markdown_reply,
    looks_like_question_reply,
    normalize_visible_reply_text,
)
from ...core.response_review import (
    is_agent_reply_ooc,
    resolve_uncertain_visible_reply,
    rewrite_agent_reply_ooc,
)
from ...core.social_surface_renderer import SocialSurfaceRenderer
from ...core.turn_media import coerce_turn_media, summarize_media_resolution
from ...core.visible_output import assess_visible_text
from .final_synthesis import AgentResult


_CONTROL_REPLIES = frozenset({"[NO_REPLY]", "<NO_REPLY>", "[SILENCE]", "<SILENCE>"})
_REVISION_FLAGS = frozenset(
    {"formulaic_tic", "style_risk", "group_visible_question", "evidence_unavailable"}
)


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
            return content
    return ""


def _copy_result_with_quality(
    result: AgentResult,
    *,
    text: str,
    check: dict[str, Any],
) -> AgentResult:
    checks = list(getattr(result, "quality_checks", []) or [])
    checks.append(check)
    quality_context = str(getattr(result, "quality_context", "") or "")
    suppress_reply_recovery = bool(getattr(result, "suppress_reply_recovery", False))
    if quality_context == "evidence_unavailable" and _is_control_reply(text):
        suppress_reply_recovery = True
    return AgentResult(
        text=text,
        pending_actions=list(getattr(result, "pending_actions", []) or []),
        direct_output=bool(getattr(result, "direct_output", False)),
        bypass_length_limits=bool(getattr(result, "bypass_length_limits", False)),
        quality_checks=checks,
        failure_code=str(getattr(result, "failure_code", "") or ""),
        suppress_reply_recovery=suppress_reply_recovery,
        quality_context=quality_context,
        evidence_envelope=getattr(result, "evidence_envelope", None),
        social_evidence=list(getattr(result, "social_evidence", []) or []),
        social_coverage=dict(getattr(result, "social_coverage", {}) or {}),
        evidence_delivery_required=bool(getattr(result, "evidence_delivery_required", False)),
        evidence_delivery_status=str(getattr(result, "evidence_delivery_status", "not_required") or "not_required"),
        evidence_recovered=bool(getattr(result, "evidence_recovered", False)),
        citation_mode=str(getattr(result, "citation_mode", "none") or "none"),
    )


_SOCIAL_PLATFORM_LABELS = {
    "bilibili": "B站",
    "douyin": "抖音",
    "tieba": "贴吧",
    "xiaoheihe": "小黑盒",
}


def _distinct_social_sources(sources: list[dict[str, Any]], *, limit: int = 3) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen_groups: set[str] = set()
    seen_urls: set[str] = set()
    for source in list(sources or []):
        if not isinstance(source, dict):
            continue
        url = str(source.get("canonical_url") or "").strip()
        group_id = str(source.get("source_group_id") or url).strip()
        if not url or url in seen_urls or group_id in seen_groups:
            continue
        seen_urls.add(url)
        seen_groups.add(group_id)
        candidates.append(source)

    def _origin_key(source: dict[str, Any]) -> str:
        explicit = str(source.get("evidence_origin") or "").strip().lower()
        if explicit:
            return explicit
        platform = str(source.get("platform") or "").strip().lower()
        if platform and platform != "web":
            return platform
        url = str(source.get("canonical_url") or "").strip()
        return (urlparse(url).hostname or "").lower().removeprefix("www.")

    # First cover different platforms/domains, then use remaining distinct
    # source groups. This makes a web cross-check visible even when the social
    # packet contains many high-quality results from a single platform.
    selected: list[dict[str, Any]] = []
    selected_urls: set[str] = set()
    seen_origins: set[str] = set()
    for source in candidates:
        origin = _origin_key(source)
        if origin and origin in seen_origins:
            continue
        selected.append(source)
        selected_urls.add(str(source.get("canonical_url") or "").strip())
        if origin:
            seen_origins.add(origin)
        if len(selected) >= limit:
            return selected
    for source in candidates:
        url = str(source.get("canonical_url") or "").strip()
        if url in selected_urls:
            continue
        selected.append(source)
        selected_urls.add(url)
        if len(selected) >= limit:
            break
    return selected


def _safe_social_source_line(source: dict[str, Any]) -> str:
    platform = str(source.get("platform") or "").strip().lower()
    url = str(source.get("canonical_url") or "").strip()
    if platform == "web":
        origin = str(source.get("evidence_origin") or "").strip()
        label = origin.removeprefix("web:") or (urlparse(url).hostname or "网页来源")
    else:
        label = _SOCIAL_PLATFORM_LABELS.get(platform, platform or "社交平台")
    title = re.sub(r"\s+", " ", str(source.get("title") or "")).strip()[:120]
    if title:
        decision = assess_visible_text(
            title,
            allow_control=False,
            allow_direct_media=False,
            enforce_role_integrity=True,
        )
        if decision.allowed:
            return f"{title}（{label}）：{url}"
    return f"{label}：{url}"


_SOCIAL_URL_RE = re.compile(
    r"https://(?:www\.)?(?:bilibili\.com/video/[^\s)]+|xiaoheihe\.cn/app/bbs/link/\d+|"
    r"douyin\.com/(?:video|note)/\d+|tieba\.baidu\.com/p/\d+)",
    re.IGNORECASE,
)


def _citation_mode(result: AgentResult, explicit: str | None = None) -> str:
    mode = str(
        explicit if explicit is not None else getattr(result, "citation_mode", "none") or "none"
    ).strip()
    return mode if mode in {"none", "urls_on_request"} else "none"


def _validated_social_url(value: Any) -> str:
    url = str(value or "").strip().rstrip(".,;，。！？）")
    if not url or not _SOCIAL_URL_RE.fullmatch(url):
        return ""
    return url


def _validated_source_url(source: dict[str, Any]) -> str:
    if str(source.get("platform") or "").strip().lower() == "web":
        # Web research supports were already bound to URL + quote by the evidence
        # synthesizer; reuse its strict HTTPS/public-host validator here.
        from .evidence import _validated_web_evidence_url

        return _validated_web_evidence_url(source.get("canonical_url"))
    return _validated_social_url(source.get("canonical_url"))


def _strip_hidden_social_citations(text: str, sources: list[dict[str, Any]]) -> str:
    """Remove source-list lines while retaining the model's natural answer."""

    source_titles = {
        re.sub(r"\s+", " ", str(item.get("title") or "")).strip()
        for item in sources
        if isinstance(item, dict) and str(item.get("title") or "").strip()
    }
    source_urls = {
        url for item in sources if isinstance(item, dict) if (url := _validated_source_url(item))
    }
    kept: list[str] = []
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if not line:
            if kept and kept[-1] != "":
                kept.append("")
            continue
        has_url = bool(_SOCIAL_URL_RE.search(line)) or any(url in line for url in source_urls)
        source_label = bool(re.match(r"^(?:来源|查到的来源|参考来源|出处)\s*[:：]", line))
        matched_title = next((title for title in source_titles if title and title in line), "")
        if has_url or source_label or (
            matched_title
            and (
                line == matched_title
                or "：" in line
                or ":" in line
                or line.startswith(f"{matched_title}（")
                or line.startswith(f"{matched_title}(")
            )
        ):
            continue
        kept.append(raw_line.rstrip())
    return "\n".join(kept).strip()


def finalize_social_evidence_delivery(
    result: AgentResult,
    *,
    sources: list[dict[str, Any]],
    coverage: dict[str, Any] | None = None,
    partial: bool = False,
    warnings: list[str] | None = None,
    record_trace: Callable[..., None] | None = None,
    citation_mode: str | None = None,
) -> AgentResult:
    """Apply the structured citation policy for validated social MCP data."""

    result.social_evidence = list(sources or [])[:10]
    result.social_coverage = {
        **dict(coverage or {}),
        "partial": bool(partial),
        "warnings": list(warnings or [])[:8],
    }
    mode = _citation_mode(result, citation_mode)
    result.citation_mode = mode
    result.evidence_delivery_required = bool(
        result.evidence_delivery_required
        or result.social_evidence
        or int(result.social_coverage.get("returned_count", 0) or 0) > 0
    )
    if mode == "none":
        result.text = _strip_hidden_social_citations(
            str(getattr(result, "text", "") or ""),
            result.social_evidence,
        )
        result.evidence_delivery_required = False
        result.evidence_delivery_status = "hidden" if result.social_evidence else "not_required"
        if record_trace is not None and result.social_evidence:
            record_trace(
                key="social_source_visibility_hidden",
                label="社交来源默认隐藏",
                status="info",
                detail=f"citation_mode=none sources={len(result.social_evidence)}",
            )
        return result
    if not result.evidence_delivery_required:
        result.evidence_delivery_status = "not_required"
        return result

    if not result.social_evidence:
        result.text = "[NO_REPLY]"
        result.evidence_delivery_status = "failed"
        result.failure_code = "evidence_delivery_incomplete"
        if record_trace is not None:
            record_trace(
                key="agent_evidence_delivery",
                label="Agent 证据交付",
                status="error",
                detail="status=failed diagnostic=evidence_delivery_incomplete links=0",
            )
        return result

    current = str(getattr(result, "text", "") or "").strip()
    valid_urls = [
        _validated_source_url(source)
        for source in result.social_evidence
    ]
    current_visibility = assess_visible_text(current)
    if current_visibility.allowed and any(url and url in current for url in valid_urls):
        result.evidence_delivery_status = "met"
        if record_trace is not None:
            record_trace(
                key="agent_evidence_delivery",
                label="Agent 证据交付",
                status="ok",
                detail=(
                    f"status=met links={sum(1 for url in valid_urls if url and url in current)} "
                    f"groups={int(result.social_coverage.get('source_group_count', 0) or 0)}"
                ),
            )
        return result

    selected = _distinct_social_sources(result.social_evidence, limit=3)
    lines = [
        _validated_source_url(source)
        for source in selected
    ]
    lines = [line for line in lines if line]
    safe_base = current if current_visibility.allowed and current not in _CONTROL_REPLIES else ""
    fallback = "\n".join([safe_base, *lines]).strip()
    fallback_visibility = assess_visible_text(
        fallback,
        allow_control=False,
        allow_direct_media=False,
    )
    if not fallback_visibility.allowed:
        fallback = "\n".join(lines).strip()
        fallback_visibility = assess_visible_text(
            fallback,
            allow_control=False,
            allow_direct_media=False,
        )
    if fallback_visibility.allowed and any(url and url in fallback for url in valid_urls):
        result.text = fallback_visibility.text
        result.evidence_delivery_status = "recovered"
        result.evidence_recovered = True
        result.failure_code = ""
        if record_trace is not None:
            record_trace(
                key="agent_evidence_delivery",
                label="Agent 证据交付",
                status="warn",
                detail=(
                    "status=recovered diagnostic=visible_output_recovered "
                    f"model_visible={str(current_visibility.allowed).lower()} "
                    f"pattern_id={current_visibility.pattern_id or '-'} links={len(selected)}"
                ),
                hint="主回复未保留来源或被可见输出规则拦截，已改用经过校验的结构化来源。",
            )
        return result

    result.text = "[NO_REPLY]"
    result.evidence_delivery_status = "failed"
    result.failure_code = "evidence_delivery_incomplete"
    if record_trace is not None:
        record_trace(
            key="agent_evidence_delivery",
            label="Agent 证据交付",
            status="error",
            detail="status=failed diagnostic=evidence_delivery_incomplete links=0",
        )
    return result


def finalize_social_evidence_delivery_boundary(
    visible_text: str,
    *,
    sources: list[dict[str, Any]],
    coverage: dict[str, Any] | None = None,
    evidence_delivery_required: bool = False,
    previous_status: str = "not_required",
    previous_recovered: bool = False,
    record_trace: Callable[..., None] | None = None,
    citation_mode: str | None = None,
) -> AgentResult:
    """Recheck social links after every downstream rewrite and before sending.

    The Agent-level evidence finalizer runs before the normal/YAML reply pipelines.
    Those pipelines may still normalize, review, split, or rewrite the text, so the
    send boundary must enforce the same contract against the text that will really
    be dispatched to QQ.
    """

    boundary_result = AgentResult(
        text=str(visible_text or "").strip(),
        pending_actions=[],
        social_evidence=list(sources or [])[:10],
        social_coverage=dict(coverage or {}),
        evidence_delivery_required=bool(evidence_delivery_required),
        evidence_delivery_status=str(previous_status or "not_required"),
        evidence_recovered=bool(previous_recovered),
        citation_mode=str(citation_mode or "none"),
    )
    boundary_result = finalize_social_evidence_delivery(
        boundary_result,
        sources=list(sources or []),
        coverage=dict(coverage or {}),
        partial=bool(dict(coverage or {}).get("partial", False)),
        warnings=list(dict(coverage or {}).get("warnings") or []),
        citation_mode=citation_mode,
    )
    if record_trace is not None and boundary_result.evidence_delivery_required:
        urls = [
            str(source.get("canonical_url") or "").strip()
            for source in boundary_result.social_evidence
            if isinstance(source, dict)
        ]
        delivered_links = sum(1 for url in urls if url and url in boundary_result.text)
        delivery_status = str(boundary_result.evidence_delivery_status or "failed")
        record_trace(
            key="agent_evidence_delivery_final",
            label="Agent 最终证据交付",
            status="error" if delivery_status == "failed" else "warn"
            if delivery_status == "recovered"
            else "ok",
            detail=(
                f"status={delivery_status} links={delivered_links} "
                f"recovered={str(bool(boundary_result.evidence_recovered)).lower()}"
            ),
            hint=(
                "最终发送边界重新附加了经过校验的社交来源。"
                if delivery_status == "recovered"
                else "最终发送文本已通过社交证据链接契约。"
            ),
        )
    return boundary_result


def _looks_like_group_context(messages: list[dict[str, Any]], turn_plan: Any = None) -> bool:
    for message in list(messages or []):
        if not isinstance(message, dict) or message.get("role") != "system":
            continue
        content = str(message.get("content", "") or "")
        if any(marker in content for marker in ("群聊", "群里", "群友", "群成员")):
            return True
    target = str(getattr(turn_plan, "message_target", "") or "").strip()
    return target in {"broadcast", "someone_else", "uncertain"}


def _quality_flags(
    raw_text: str,
    visible_text: str,
    *,
    is_group: bool = False,
    allow_rhetorical_banter: bool = False,
) -> list[str]:
    flags: list[str] = []
    if looks_like_markdown_reply(raw_text):
        flags.append("markdown_or_trace")
    if looks_like_formulaic_reply_tic(raw_text):
        flags.append("formulaic_tic")
    # Markdown/control wrappers are already handled by ``visible_text`` above;
    # do not send a second LLM rewrite merely because the raw candidate used a
    # presentation wrapper.  Re-check the normalized surface so real OOC
    # phrases (search/source/observer tics) still receive model-led revision.
    if is_agent_reply_ooc(visible_text or raw_text):
        flags.append("style_risk")
    if is_group and looks_like_question_reply(
        visible_text or raw_text,
        allow_exclamatory_rhetorical=allow_rhetorical_banter,
    ):
        flags.append("group_visible_question")
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
    is_group: bool | None = None,
    is_direct_mention: bool = False,
    reply_required: bool = False,
    current_user_text: str = "",
    turn_media_context: list[Any] | None = None,
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
    quality_context = str(getattr(result, "quality_context", "") or "").strip()
    direct_output = bool(getattr(result, "direct_output", False))
    envelope = EvidenceEnvelope.from_value(getattr(result, "evidence_envelope", None))
    if quality_context == "constrained_persona_output" and envelope is not None:
        outcome = await SocialSurfaceRenderer().render_evidence(
            envelope,
            tool_caller=tool_caller,
            persona_system=_persona_system_from_messages(messages),
        )
        final_text = normalize_visible_reply_text(outcome.text) or envelope.natural_fallback
        group_context = _looks_like_group_context(messages, turn_plan) if is_group is None else bool(is_group)
        constraint_flags = ["constrained_evidence"]
        if is_agent_reply_ooc(final_text):
            final_text = envelope.natural_fallback
            constraint_flags.append("style_fallback")
        if group_context and looks_like_question_reply(final_text):
            final_text = envelope.natural_fallback
            constraint_flags.append("question_fallback")
        visibility = assess_visible_text(final_text)
        if not visibility.allowed:
            final_text = envelope.natural_fallback
            constraint_flags.append("visible_output_fallback")
        elapsed_ms = int((time.monotonic() - started_at) * 1000)
        check = {
            "action": outcome.action,
            "reason": str(reason or ""),
            "flags": constraint_flags,
            "revision_attempted": outcome.rewrite_used,
            "elapsed_ms": elapsed_ms,
            "original_chars": len(raw_text),
            "final_chars": len(final_text),
        }
        record_timing("agent.reply_quality_ms", elapsed_ms, action=outcome.action)
        record_counter("agent.reply_quality_total", action=outcome.action)
        if record_trace is not None:
            record_trace(
                key="agent_reply_quality",
                label="Agent 回复质量",
                status="ok" if outcome.action in {"accepted", "rewritten"} else "warn",
                detail=(
                    f"action={outcome.action} source={reason or '-'} "
                    f"flags={','.join(constraint_flags)} revision={str(outcome.rewrite_used).lower()} "
                    f"elapsed_ms={elapsed_ms} chars={len(raw_text)}->{len(final_text)}"
                ),
                hint="头像等受约束证据已完成人设化与事实边界审阅。",
            )
        return _copy_result_with_quality(result, text=final_text, check=check)
    # The model is allowed to use the documented <output><message> wrapper.
    # Remove only the known control blocks first, then assess the exact text that
    # can become visible.  Assessing raw XML here incorrectly classified every
    # well-formed response as ``internal_tag`` before the safe message body could
    # be extracted.
    stripped = strip_response_control_markers(raw_text)
    visible_text = normalize_visible_reply_text(stripped)
    visibility_candidate = visible_text or raw_text
    if quality_context != "evidence_unavailable":
        visibility = assess_visible_text(visibility_candidate)
        if not visibility.allowed:
            check = {
                "action": "silenced",
                "reason": visibility.reason,
                "pattern_id": visibility.pattern_id,
                "summary_hash": visibility.summary_hash,
                "source": str(reason or ""),
                "flags": ["unsafe_visible_output"],
                "revision_attempted": False,
                "elapsed_ms": int((time.monotonic() - started_at) * 1000),
                "original_chars": len(raw_text),
                "final_chars": len("[SILENCE]"),
            }
            record_counter("agent.reply_quality_total", action="silenced")
            if record_trace is not None:
                record_trace(
                    key="agent_reply_quality",
                    label="Agent 回复质量",
                    status="warn",
                    detail=(
                        f"action=silenced reason={visibility.reason} "
                        f"pattern_id={visibility.pattern_id or '-'} chars={len(raw_text)} "
                        f"summary_hash={visibility.summary_hash or '-'} flags=unsafe_visible_output"
                    ),
                )
            return _copy_result_with_quality(result, text="[SILENCE]", check=check)
    skipped = (
        direct_output
        or _is_direct_media_reply(raw_text)
        or (_is_control_reply(raw_text) and quality_context != "evidence_unavailable")
    )
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

    if quality_context == "evidence_unavailable":
        group_context = _looks_like_group_context(messages, turn_plan) if is_group is None else bool(is_group)
        media_resolution = summarize_media_resolution(turn_media_context)
        available_media_parts: list[str] = []
        if int(media_resolution.get("video_usable", 0) or 0) > 0:
            available_media_parts.append(f"可读取视频 {int(media_resolution['video_usable'])} 个")
        usable_audio_count = sum(
            1
            for item in coerce_turn_media(turn_media_context)
            if item.kind == "audio" and normalize_audio_ref(str(item.ref or ""))[0]
        )
        if usable_audio_count > 0:
            available_media_parts.append(f"可读取音频 {usable_audio_count} 个")
        available_media_context = "，".join(available_media_parts)

        async def _call_uncertain_review(review_messages: list[dict[str, Any]]) -> str:
            if tool_caller is None:
                return ""
            response = await tool_caller.chat_with_tools(review_messages, [], False)
            return str(getattr(response, "content", "") or "")

        decision = await resolve_uncertain_visible_reply(
            _call_uncertain_review,
            candidate_text=raw_text,
            raw_message_text=current_user_text,
            persona_system=_persona_system_from_messages(messages),
            turn_plan=turn_plan,
            reply_required=reply_required,
            is_private=not group_context,
            evidence_unavailable=True,
            available_media_context=available_media_context,
            timeout=8.0,
        )
        final_text = "[SILENCE]"
        action = (
            "no_evidence_silenced"
            if decision.reason == "no_evidence_nonrequired"
            else "context_request_rejected"
        )
        if decision.action == "request_context" and decision.text:
            candidate = normalize_visible_reply_text(strip_response_control_markers(decision.text))
            candidate_visibility = assess_visible_text(candidate)
            invalid_group_question = bool(
                group_context
                and looks_like_question_reply(
                    candidate,
                    allow_exclamatory_rhetorical=False,
                )
            )
            if candidate and candidate_visibility.allowed and not invalid_group_question:
                final_text = candidate
                action = "context_request"
        elapsed_ms = int((time.monotonic() - started_at) * 1000)
        flags = list(dict.fromkeys(["evidence_unavailable", *decision.flags]))
        check = {
            "action": action,
            "flags": flags,
            "elapsed_ms": elapsed_ms,
        }
        record_timing("agent.reply_quality_ms", elapsed_ms, action=action)
        record_counter("agent.reply_quality_total", action=action)
        if record_trace is not None:
            record_trace(
                key="agent_reply_quality",
                label="Agent 回复质量",
                status="ok" if action == "context_request" else "warn",
                detail=(
                    f"action={action} flags={','.join(flags)} "
                    f"elapsed_ms={elapsed_ms}"
                ),
                hint="空证据不再包装成可见失败说明；强交互仅允许经过语义复核的具体补充请求。",
            )
        return _copy_result_with_quality(result, text=final_text, check=check)

    group_context = _looks_like_group_context(messages, turn_plan) if is_group is None else bool(is_group)
    speech_act = str(getattr(turn_plan, "speech_act", "") or "").strip()
    allow_rhetorical_banter = bool(
        group_context
        and is_direct_mention
        and speech_act in {"", "participate", "tease"}
    )
    flags = _quality_flags(
        raw_text,
        visible_text,
        is_group=group_context,
        allow_rhetorical_banter=allow_rhetorical_banter,
    )
    if quality_context == "evidence_unavailable":
        flags.append("evidence_unavailable")
    action = "accept"
    final_text = visible_text or raw_text
    revision_attempted = False

    if flags and tool_caller is not None and any(flag in _REVISION_FLAGS for flag in flags):
        if record_trace is not None:
            record_trace(
                key="agent_reply_quality_start",
                label="Agent 回复质量复写开始",
                status="warn",
                detail=(
                    f"flags={','.join(flags)} timeout_ms=8000 "
                    f"chars={len(raw_text)}"
                ),
                hint="候选回复命中可见风格风险，开始一次受限人设改写；仅记录结构化标记，不记录正文。",
            )
        revision_attempted = True
        rewritten = await rewrite_agent_reply_ooc(
            tool_caller=tool_caller,
            original_text=raw_text,
            persona_system=_persona_system_from_messages(messages),
            timeout=8.0,
            output_mode=_turn_plan_output_mode(turn_plan),
            avoid_questions=group_context,
            allow_rhetorical_banter=allow_rhetorical_banter,
            rewrite_reason=quality_context,
        )
        candidate = normalize_visible_reply_text(strip_response_control_markers(rewritten)) if rewritten else ""
        candidate_visibility = assess_visible_text(candidate) if candidate else None
        if candidate and candidate_visibility is not None and candidate_visibility.allowed:
            if group_context and looks_like_question_reply(
                candidate,
                allow_exclamatory_rhetorical=allow_rhetorical_banter,
            ):
                final_text = "[SILENCE]"
                action = "silenced"
            else:
                final_text = candidate
                action = "rewritten"

    if not final_text:
        final_text = "[SILENCE]"
        action = "silenced"
    elif group_context and "group_visible_question" in flags and action != "rewritten":
        final_text = "[SILENCE]"
        action = "silenced"
    elif quality_context == "evidence_unavailable" and action != "rewritten":
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
    "finalize_social_evidence_delivery",
    "finalize_social_evidence_delivery_boundary",
]
