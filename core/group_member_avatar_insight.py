from __future__ import annotations

import asyncio
import base64
import json
import math
from inspect import isawaitable
from typing import Any, Mapping, Sequence

from .avatar_candidate import sanitize_image
from .media_understanding import analyze_images_with_route_or_fallback
from .protocol_adapter import get_protocol_adapter
from .safe_image_download import download_public_image
from .user_avatar_insight import (
    AVATAR_ALLOWED_MIMES,
    AVATAR_MAX_BYTES,
    AVATAR_VISION_TIMEOUT_SECONDS,
    _AVATAR_PROMPT,
    _parse_avatar_response,
    build_avatar_evidence_envelope,
    get_user_avatar_state,
)
from .user_profile_meta import qq_avatar_url
from .visual_capabilities import VISUAL_ROUTE_VISION


AVATAR_ASSET_MATCH_RESULTS = frozenset(
    {"exact_asset_match", "possible_asset_match", "uncertain"}
)


def _reply_runtime(runtime: Any) -> Any:
    holder = getattr(runtime, "runtime_bundle", None) or runtime
    deps = getattr(holder, "reply_processor_deps", None)
    inner = getattr(deps, "runtime", None) if deps is not None else None
    return inner or holder


def _numeric_id(value: Any) -> str:
    text = str(value or "").strip()
    return text if text.isascii() and text.isdecimal() and int(text) > 0 else ""


def _candidate_map(candidates: Sequence[Mapping[str, Any]] | None) -> dict[str, str]:
    result: dict[str, str] = {}
    seen_users: set[str] = set()
    for item in list(candidates or [])[:6]:
        if not isinstance(item, Mapping):
            continue
        user_id = _numeric_id(item.get("user_id"))
        label = " ".join(str(item.get("label", "") or "").split())[:48]
        if not user_id or not label or label.isdigit() or user_id in seen_users or label in result:
            continue
        seen_users.add(user_id)
        result[label] = user_id
    return result


async def _policy_allows(policy_authorizer: Any, user_id: str) -> bool:
    if policy_authorizer is None:
        return True
    try:
        authorization = policy_authorizer(user_id)
        if isawaitable(authorization):
            authorization = await authorization
    except Exception:
        return False
    return not bool(getattr(authorization, "blocked", True)) and bool(
        getattr(authorization, "allow_context_read", False)
    )


def build_group_member_avatar_insight_tool(
    *,
    runtime: Any,
    bot: Any,
    event: Any,
    candidates: Sequence[Mapping[str, Any]],
    policy_authorizer: Any = None,
) -> Any | None:
    from ..agent.tool_registry import AgentTool

    runtime = _reply_runtime(runtime)
    group_id = _numeric_id(getattr(event, "group_id", ""))
    by_label = _candidate_map(candidates)
    if bot is None or not group_id or not by_label:
        return None
    labels = list(by_label)
    used = False

    async def handler(member_label: str = "", **extra: Any) -> str:
        nonlocal used
        label = " ".join(str(member_label or "").split())
        user_id = by_label.get(label, "")
        if extra or not user_id or used:
            return build_avatar_evidence_envelope().to_json()
        used = True
        if not await _policy_allows(policy_authorizer, user_id):
            return build_avatar_evidence_envelope().to_json()

        adapter = get_protocol_adapter(
            bot,
            plugin_config=getattr(runtime, "plugin_config", None),
            logger=getattr(runtime, "logger", None),
        )
        membership = await adapter.get_group_member_info(
            group_id=group_id,
            user_id=user_id,
        )
        if not membership.ok:
            return build_avatar_evidence_envelope().to_json()

        profile_service = getattr(runtime, "profile_service", None)
        if profile_service is not None:
            state = get_user_avatar_state(
                profile_service,
                user_id,
                include_hash=False,
                include_hash_short=False,
            )
            if state.get("insight"):
                return build_avatar_evidence_envelope(state["insight"]).to_json()

        try:
            downloaded = await download_public_image(
                qq_avatar_url(user_id),
                max_bytes=AVATAR_MAX_BYTES,
                allowed_mimes=set(AVATAR_ALLOWED_MIMES),
            )
            sanitized, mime, _width, _height, _suffix = sanitize_image(
                bytes(downloaded.content),
                str(downloaded.content_type or ""),
            )
            data_url = f"data:{mime};base64,{base64.b64encode(sanitized).decode('ascii')}"
            raw, route = await asyncio.wait_for(
                analyze_images_with_route_or_fallback(
                    runtime=runtime,
                    prompt=_AVATAR_PROMPT,
                    image_refs=[data_url],
                    route_name=VISUAL_ROUTE_VISION,
                    image_detail="low",
                ),
                timeout=AVATAR_VISION_TIMEOUT_SECONDS,
            )
            if route == "vision_unavailable" or not str(raw or "").strip():
                raise ValueError("avatar vision unavailable")
            insight = _parse_avatar_response(raw)
        except Exception:
            return build_avatar_evidence_envelope().to_json()
        finally:
            if "data_url" in locals():
                del data_url

        if not await _policy_allows(policy_authorizer, user_id):
            return build_avatar_evidence_envelope().to_json()
        return build_avatar_evidence_envelope(insight).to_json()

    return AgentTool(
        name="inspect_group_member_avatar",
        description=(
            "按需查看当前群内一个候选成员头像中可观察到的画面描述和可能的 ACG 角色/作品候选。"
            "仅在当前对话确实要求看某位群成员头像时调用，每轮最多一次；参数只能选择 schema 给出的 label。"
            "画中人物或角色不等于该群成员本人，不得据此推断真人身份、现实关系、性格或经历。"
        ),
        parameters={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "member_label": {"type": "string", "enum": labels},
            },
            "required": ["member_label"],
        },
        handler=handler,
        local=True,
        metadata={
            "intent_tags": ["avatar", "lookup", "group_context"],
            "evidence_kind": "visual_summary",
            "requires_network": True,
            "requires_image": False,
            "latency_class": "slow",
            "risk_level": "low",
            "read_only": True,
            "side_effect": "none",
            "final_behavior": "constrained_persona_output",
            "retryable": True,
            "source_kind": "first_party_runtime",
        },
        per_session_quota=1,
    )


def build_group_member_avatar_asset_match_tool(
    *,
    runtime: Any,
    bot: Any,
    event: Any,
    candidates: Sequence[Mapping[str, Any]],
    turn_media_context: Sequence[Any] | None = None,
    policy_authorizer: Any = None,
) -> Any | None:
    """Compare chat artwork/avatar assets; never perform real-person recognition."""

    from ..agent.tool_registry import AgentTool

    runtime = _reply_runtime(runtime)
    group_id = _numeric_id(getattr(event, "group_id", ""))
    by_label = _candidate_map(candidates)
    media_by_id = {
        str(getattr(item, "media_id", "") or "").strip(): item
        for item in list(turn_media_context or [])[:12]
        if str(getattr(item, "media_id", "") or "").strip()
        and str(getattr(item, "kind", "") or "") in {"image", "sticker", "gif", "mface"}
        and str(getattr(item, "ref", "") or "").strip()
    }
    if bot is None or not group_id or not by_label or not media_by_id:
        return None
    used = False

    def _empty(code: str) -> str:
        return json.dumps(
            {
                "match_result": "uncertain",
                "candidate_user_id": "",
                "confidence": 0.0,
                "diagnostic_code": code,
                "notice": "仅比较聊天图片与 QQ 账号头像的素材相似度，不进行现实人脸身份识别。",
            },
            ensure_ascii=False,
            sort_keys=True,
        )

    async def handler(media_id: str = "", **extra: Any) -> str:
        nonlocal used
        selected = media_by_id.get(str(media_id or "").strip())
        if used or extra or selected is None:
            return _empty("avatar_asset_match_invalid_request")
        used = True
        image_refs = [str(getattr(selected, "ref", "") or "").strip()]
        resolved_labels: list[str] = []
        resolved_ids: list[str] = []
        adapter = get_protocol_adapter(
            bot,
            plugin_config=getattr(runtime, "plugin_config", None),
            logger=getattr(runtime, "logger", None),
        )
        try:
            for label, user_id in list(by_label.items())[:6]:
                if not await _policy_allows(policy_authorizer, user_id):
                    continue
                membership = await adapter.get_group_member_info(
                    group_id=group_id,
                    user_id=user_id,
                )
                if not membership.ok:
                    continue
                downloaded = await download_public_image(
                    qq_avatar_url(user_id),
                    max_bytes=AVATAR_MAX_BYTES,
                    allowed_mimes=set(AVATAR_ALLOWED_MIMES),
                )
                sanitized, mime, _width, _height, _suffix = sanitize_image(
                    bytes(downloaded.content),
                    str(downloaded.content_type or ""),
                )
                image_refs.append(
                    f"data:{mime};base64,{base64.b64encode(sanitized).decode('ascii')}"
                )
                resolved_labels.append(label)
                resolved_ids.append(user_id)
            if not resolved_labels:
                return _empty("avatar_asset_match_no_candidates")
            prompt = (
                "第一张图是群聊媒体，后续图片依次是候选 QQ 账号头像素材。"
                "只比较是否复用了同一张头像素材、同一角色裁切或高度相似的图像资产；"
                "不进行现实人脸身份识别，也不要推断现实人物性别、关系或经历。只输出严格 JSON："
                '{"match_result":"exact_asset_match|possible_asset_match|uncertain",'
                '"candidate_label":"候选标签或空字符串","confidence":0.0}。'
                f"候选顺序与标签：{json.dumps(resolved_labels, ensure_ascii=False)}"
            )
            raw, route = await asyncio.wait_for(
                analyze_images_with_route_or_fallback(
                    runtime=runtime,
                    prompt=prompt,
                    image_refs=image_refs,
                    route_name=VISUAL_ROUTE_VISION,
                    image_detail="low",
                ),
                timeout=AVATAR_VISION_TIMEOUT_SECONDS,
            )
            if route == "vision_unavailable":
                return _empty("avatar_asset_match_vision_unavailable")
            payload = json.loads(str(raw or "").strip())
            if not isinstance(payload, dict) or set(payload) != {
                "match_result",
                "candidate_label",
                "confidence",
            }:
                return _empty("avatar_asset_match_invalid_result")
            result = str(payload.get("match_result", "") or "").strip()
            label = str(payload.get("candidate_label", "") or "").strip()
            confidence = float(payload.get("confidence", 0.0))
            if (
                result not in AVATAR_ASSET_MATCH_RESULTS
                or not math.isfinite(confidence)
                or not 0.0 <= confidence <= 1.0
                or (result != "uncertain" and label not in resolved_labels)
            ):
                return _empty("avatar_asset_match_invalid_result")
            candidate_user_id = (
                resolved_ids[resolved_labels.index(label)]
                if result != "uncertain" and label in resolved_labels
                else ""
            )
            return json.dumps(
                {
                    "match_result": result,
                    "candidate_user_id": candidate_user_id,
                    "confidence": round(confidence, 3),
                    "diagnostic_code": "avatar_asset_match_completed",
                    "notice": "仅比较聊天图片与 QQ 账号头像的素材相似度，不进行现实人脸身份识别；possible 只能表述为可能。",
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        except Exception:
            return _empty("avatar_asset_match_failed")
        finally:
            del image_refs

    return AgentTool(
        name="match_group_member_avatar_asset",
        description=(
            "比较本轮一张聊天图片与当前线程相关群成员的 QQ 账号头像素材。"
            "只返回同素材/可能同素材/不确定的弱证据，不是现实人脸识别；possible 不得说成确定身份。"
        ),
        parameters={
            "type": "object",
            "additionalProperties": False,
            "properties": {"media_id": {"type": "string", "enum": list(media_by_id)}},
            "required": ["media_id"],
        },
        handler=handler,
        local=True,
        metadata={
            "intent_tags": ["avatar", "asset_match", "group_context"],
            "evidence_kind": "weak_visual_asset_match",
            "requires_network": True,
            "requires_image": True,
            "latency_class": "slow",
            "risk_level": "low",
            "read_only": True,
            "side_effect": "none",
            "final_behavior": "constrained_persona_output",
            "retryable": False,
            "source_kind": "first_party_runtime",
        },
        per_session_quota=1,
    )


def register_group_member_avatar_insight_tool(
    registry: Any,
    *,
    runtime: Any,
    bot: Any,
    event: Any,
    candidates: Sequence[Mapping[str, Any]],
    turn_media_context: Sequence[Any] | None = None,
    policy_authorizer: Any = None,
) -> bool:
    if registry is None:
        return False
    tool = build_group_member_avatar_insight_tool(
        runtime=runtime,
        bot=bot,
        event=event,
        candidates=candidates,
        policy_authorizer=policy_authorizer,
    )
    registered = False
    if tool is not None:
        registry.register(tool)
        registered = True
    match_tool = build_group_member_avatar_asset_match_tool(
        runtime=runtime,
        bot=bot,
        event=event,
        candidates=candidates,
        turn_media_context=turn_media_context,
        policy_authorizer=policy_authorizer,
    )
    if match_tool is not None:
        registry.register(match_tool)
        registered = True
    return registered


__all__ = [
    "AVATAR_ASSET_MATCH_RESULTS",
    "build_group_member_avatar_asset_match_tool",
    "build_group_member_avatar_insight_tool",
    "register_group_member_avatar_insight_tool",
]
