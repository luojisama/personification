from __future__ import annotations

from typing import Any, Mapping, MutableMapping


def apply_agent_result_completion_state(
    *,
    state: MutableMapping[str, Any],
    agent_result: Any,
    default_citation_mode: str = "none",
) -> None:
    """Project one Agent result into the shared visible-delivery contract.

    Normal and YAML reply flows must expose the same final completion state.
    Keep the projection here so a new quality context cannot silently become a
    success in only one flow.
    """

    social_coverage = dict(getattr(agent_result, "social_coverage", {}) or {})
    state["agent_social_evidence"] = [
        dict(item)
        for item in list(getattr(agent_result, "social_evidence", []) or [])[:10]
        if isinstance(item, dict)
    ]
    state["agent_social_coverage"] = dict(social_coverage)
    state["agent_evidence_delivery_required"] = bool(
        getattr(agent_result, "evidence_delivery_required", False)
    )
    state["agent_evidence_delivery_status"] = str(
        getattr(agent_result, "evidence_delivery_status", "not_required") or "not_required"
    )
    state["agent_evidence_recovered"] = bool(
        getattr(agent_result, "evidence_recovered", False)
    )
    # Video fact delivery is separate from social links.  Never let a successful
    # tool call turn an ungrounded visible answer into an overall ``ok`` turn.
    state["agent_media_only"] = bool(getattr(agent_result, "media_only", False))
    state["agent_media_grounding"] = str(
        getattr(agent_result, "media_grounding", "not_required") or "not_required"
    )
    state["agent_available_evidence_fields"] = max(
        0, int(getattr(agent_result, "available_evidence_fields", 0) or 0)
    )
    state["agent_grounded_evidence_fields"] = max(
        0, int(getattr(agent_result, "grounded_evidence_fields", 0) or 0)
    )
    state["agent_grounded_anchor_count"] = max(
        0, int(getattr(agent_result, "grounded_anchor_count", 0) or 0)
    )
    state["agent_media_recovery_method"] = str(
        getattr(agent_result, "media_recovery_method", "not_needed") or "not_needed"
    )
    state["agent_media_delivery"] = str(
        getattr(agent_result, "media_delivery", "not_required") or "not_required"
    )
    state["agent_citation_mode"] = str(
        getattr(agent_result, "citation_mode", default_citation_mode)
        or default_citation_mode
        or "none"
    )
    state["agent_social_coverage_status"] = str(
        social_coverage.get("coverage_status", "") or ""
    )
    state["agent_social_tool_execution"] = (
        "partial"
        if bool(social_coverage.get("partial", False))
        else "ok"
        if bool(getattr(agent_result, "social_evidence", None))
        else "not_used"
    )
    evidence_unavailable = (
        str(getattr(agent_result, "quality_context", "") or "")
        == "evidence_unavailable"
    )
    state["agent_evidence_unavailable"] = evidence_unavailable
    tool_calls_made = bool(getattr(agent_result, "tool_calls_made", False))
    state["agent_tool_calls"] = tool_calls_made
    structured_media_evidence_seen = bool(
        int(getattr(agent_result, "available_evidence_fields", 0) or 0) > 0
        and str(getattr(agent_result, "media_delivery", "not_required") or "not_required")
        == "incomplete"
    )
    # A vision tool may have successfully produced safe structured fields even
    # if the final fact-delivery gate cannot form a sendable answer. Preserve
    # that operational fact, while ``media_delivery=incomplete`` still makes
    # the overall completion partial below.
    if tool_calls_made and structured_media_evidence_seen:
        tool_execution = "ok"
    elif evidence_unavailable:
        tool_execution = "empty"
    else:
        tool_execution = "ok" if tool_calls_made else "not_used"
    state["agent_tool_execution"] = tool_execution


def resolve_sent_reply_completion(
    *,
    state: Mapping[str, Any] | None,
    visible_text: str,
    delivery_partial: bool = False,
    delivery_unknown: bool = False,
) -> dict[str, Any]:
    """Resolve the final Trace outcome after a reply dispatch path finishes.

    Tool execution, evidence delivery and outbound confirmation are deliberately
    independent. A tool returning data cannot by itself make the whole turn
    successful.
    """

    values = state or {}
    evidence_required = bool(values.get("agent_evidence_delivery_required", False))
    evidence_delivery = str(
        values.get("agent_evidence_delivery_status", "not_required") or "not_required"
    )
    citation_mode = str(values.get("agent_citation_mode", "none") or "none")
    evidence_recovered = bool(values.get("agent_evidence_recovered", False))
    media_delivery = str(values.get("agent_media_delivery", "not_required") or "not_required")
    coverage_status = str(values.get("agent_social_coverage_status", "") or "")
    social_tool_execution = str(
        values.get("agent_social_tool_execution", "not_used") or "not_used"
    )
    general_tool_execution = str(
        values.get("agent_tool_execution", "not_used") or "not_used"
    )
    tool_execution = (
        social_tool_execution
        if social_tool_execution != "not_used"
        else general_tool_execution
    )
    evidence_unavailable = bool(
        values.get("agent_evidence_unavailable", False)
        or values.get("media_reference_unavailable", False)
    )
    if evidence_unavailable and evidence_delivery == "not_required":
        evidence_delivery = "incomplete"
    outbound_delivery = (
        "unconfirmed" if delivery_unknown else "partial" if delivery_partial else "confirmed"
    )
    visible_nonempty = bool(str(visible_text or "").strip())

    if delivery_unknown or not visible_nonempty:
        outcome = "failed"
        diagnosis_code = "outbound_send_failed"
    elif delivery_partial:
        outcome = "partial"
        diagnosis_code = "outbound_send_failed"
    elif evidence_required and evidence_delivery not in {"met", "recovered"}:
        outcome = "partial"
        diagnosis_code = "evidence_delivery_incomplete"
    elif evidence_unavailable:
        outcome = "partial"
        diagnosis_code = "evidence_delivery_incomplete"
    elif media_delivery == "incomplete":
        outcome = "partial"
        diagnosis_code = "evidence_delivery_incomplete"
    elif evidence_recovered or evidence_delivery == "recovered":
        outcome = "partial"
        diagnosis_code = "visible_output_recovered"
    elif coverage_status == "degraded" or tool_execution == "partial":
        outcome = "partial"
        diagnosis_code = "social_coverage_degraded"
    else:
        outcome = "ok"
        diagnosis_code = "ok"

    return {
        "outcome": outcome,
        "diagnosis_code": diagnosis_code,
        "tool_execution": tool_execution,
        "evidence_delivery": evidence_delivery,
        "outbound_delivery": outbound_delivery,
        "coverage_status": coverage_status,
        "evidence_required": evidence_required,
        "citation_mode": citation_mode,
        "evidence_recovered": evidence_recovered,
        "evidence_unavailable": evidence_unavailable,
        "media_delivery": media_delivery,
    }


__all__ = ["apply_agent_result_completion_state", "resolve_sent_reply_completion"]
