"""Framework-neutral route capability probe targets and execution.

No Bot object is accepted here.  Every caller is constructed from exactly one
configured provider and all fallback flags are disabled for media probes.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable

import httpx

from .diagnostic_media_samples import get_diagnostic_media_sample, validate_diagnostic_media_sample, score_custom_media_transport_response
from .route_capabilities import (
    CAPABILITY_NAMES, DEFAULT_ROUTE_CAPABILITY_REGISTRY, CapabilityObservation, RouteKey,
)
from .route_probe_service import ProbeResult

_NOOP = {"type":"function","function":{"name":"personification_capability_noop","description":"No side effects.","parameters":{"type":"object","properties":{},"additionalProperties":False}}}

class _MediaProbeConfig:
    _FALSE = {"personification_gemini_web_enabled", "personification_gemini_web_risk_acknowledged", "personification_mimo_web_asr_enabled", "personification_mimo_web_asr_risk_acknowledged", "personification_fullmodal_provider_enabled", "personification_fallback_enabled", "personification_video_fallback_enabled", "personification_audio_transcription_enabled"}
    def __init__(self, original: Any) -> None: self._original=original
    def __getattr__(self, name: str) -> Any:
        if name in self._FALSE: return False
        if name == "personification_video_route_mode": return "primary"
        if name == "personification_video_storyboard_fallback_enabled": return False
        if name == "personification_video_understanding_enabled": return True
        return getattr(self._original, name)

class _MediaProbeRuntime:
    strict_probe = True
    def __init__(self, runtime: Any, provider: dict[str,Any]) -> None:
        self.plugin_config=_MediaProbeConfig(getattr(runtime,"plugin_config",None)); self.logger=getattr(runtime,"logger",None); self._provider=dict(provider)
    def get_configured_api_providers(self) -> list[dict[str,Any]]: return [dict(self._provider)]


def configured_route_targets(runtime: Any, *, capabilities: Iterable[str] = CAPABILITY_NAMES) -> list[tuple[str, str, Callable[[], Awaitable[ProbeResult]]]]:
    """Enumerate enabled, unique request fingerprints without secrets in DTOs."""
    bundle = getattr(runtime, "runtime_bundle", None)
    getter = getattr(bundle, "get_configured_api_providers", None)
    providers = list(getter() or []) if callable(getter) else []
    result=[]; seen=set()
    for provider in providers:
        if not isinstance(provider, dict) or provider.get("enabled", True) is False:
            continue
        key=RouteKey.from_config(provider=provider.get("name") or "primary",api_type=provider.get("api_type"),api_url=provider.get("api_url"),model=provider.get("model"),media_protocol=provider.get("media_protocol") or "auto")
        DEFAULT_ROUTE_CAPABILITY_REGISTRY.bind_route(str(provider.get("name") or "primary"), key)
        for capability in capabilities:
            marker=(key.fingerprint, capability)
            if marker in seen: continue
            seen.add(marker)
            async def runner(key=key, provider=dict(provider), capability=capability):
                return await run_route_probe(runtime, key, provider, capability)
            result.append((key.fingerprint, capability, runner))
    return result


def _record(key: RouteKey, capability: str, observation: CapabilityObservation, code: str, *, stage: str = "", http_status: int | None = None, input_count: int = 0, transport_verified: bool = False, content_verified: bool = False) -> ProbeResult:
    previous = DEFAULT_ROUTE_CAPABILITY_REGISTRY.get(key, capability)
    if observation not in {CapabilityObservation.SUCCESS, CapabilityObservation.EXPLICIT_UNSUPPORTED} and previous.verification_state.value == "verified":
        # An unavailable attempt is separate from the last verified fact.
        return ProbeResult("unknown", "inconclusive", code, transport_verified, content_verified, stage, http_status, input_count)
    record=DEFAULT_ROUTE_CAPABILITY_REGISTRY.record_observation(key, capability, observation, detail_code=code)
    return ProbeResult(record.state.value, record.verification_state.value, code, transport_verified, content_verified, stage, http_status, input_count)


def _official_reasoning_wire_confirmed(provider: dict[str, Any], caller: Any) -> bool:
    """A correct answer alone is never evidence that reasoning was requested."""
    api_type = str(provider.get("api_type") or "").strip().lower().replace("-", "_")
    # Protocol compatibility is what is being probed. A self-hosted endpoint
    # can implement the same official request shape; hostname is no evidence.
    official = api_type in {"openai", "openai_responses", "anthropic", "anthropic_messages", "gemini", "gemini_official"}
    shape = getattr(caller, "last_probe_request_shape", None)
    if not official or not isinstance(shape, dict):
        return False
    return shape.get("reasoning_requested") is True


async def _run_route_probe(
    runtime: Any, key: RouteKey, provider: dict[str, Any], capability: str,
    *, media_path: Path | None = None, sample_mode: str = "builtin", sample_id: str = "",
) -> ProbeResult:
    """Execute one bounded synthetic probe on one provider, never a fallback."""
    if capability not in CAPABILITY_NAMES:
        return _record(key, capability, CapabilityObservation.PROBE_UNAVAILABLE, "probe_unavailable")
    timeout=max(5.0,min(45.0,float(getattr(getattr(runtime,"plugin_config",None),"personification_route_probe_item_timeout_seconds",45.0) or 45.0)))
    deadline=time.monotonic()+timeout
    stage="provider"
    try:
        from .ai_routes import build_single_provider_caller
        caller=build_single_provider_caller(getattr(runtime,"plugin_config",None), provider, thinking_mode_override="low" if capability == "reasoning" else None)
        if capability == "function_call":
            stage="tool_call"
            response=await asyncio.wait_for(caller.chat_with_tools([{"role":"user","content":"Call only personification_capability_noop."}],[_NOOP],False),timeout)
            calls=list(getattr(response,"tool_calls",None) or [])
            ok=len(calls)==1 and (calls[0].get("name") if isinstance(calls[0],dict) else getattr(calls[0],"name",None)) == "personification_capability_noop"
            if not ok:
                return _record(key, capability, CapabilityObservation.PARSE_ERROR, "function_call_probe_inconclusive", stage="tool_call", input_count=1)
            selected = next(item for item in calls if (item.get("name") if isinstance(item, dict) else getattr(item, "name", None)) == "personification_capability_noop")
            call_id = str(selected.get("id") if isinstance(selected, dict) else getattr(selected, "id", ""))
            receipt = uuid.uuid4().hex
            continuation = [
                {"role": "user", "content": "Call only personification_capability_noop. Then return only the probe_receipt from the tool result."},
                caller.build_assistant_tool_calls_message(response),
                caller.build_tool_result_message(call_id, "personification_capability_noop", '{"probe_receipt":"'+receipt+'"}'),
            ]
            stage="tool_continuation"
            final = await asyncio.wait_for(caller.chat_with_tools(continuation, [_NOOP], False), max(0,deadline-time.monotonic()))
            verified = str(getattr(final, "content", "") or "").strip() == receipt
            return _record(key, capability, CapabilityObservation.SUCCESS if verified else CapabilityObservation.PARSE_ERROR, "function_call_noop_structured_tool_call" if verified else "function_call_probe_inconclusive", stage="tool_continuation", input_count=2, transport_verified=verified, content_verified=verified)
        if capability == "native_web_search":
            response=await asyncio.wait_for(caller.chat_with_tools([{"role":"user","content":"Use native search for a public factual query; answer briefly."}],[],True),timeout)
            ok=bool(str(getattr(response,"content","") or "").strip()) and getattr(response,"used_builtin_search",None) is True
            return _record(key,capability,CapabilityObservation.SUCCESS if ok else CapabilityObservation.PARSE_ERROR,"native_search_readonly_visible_answer" if ok else "native_search_probe_inconclusive")
        if capability == "reasoning":
            stage="reasoning"
            response=await asyncio.wait_for(caller.chat_with_tools([{"role":"user","content":"Output only the decimal result of 2+2."}],[],False),timeout)
            wire = _official_reasoning_wire_confirmed(provider, caller)
            ok=str(getattr(response,"content","") or "").strip() == "4" and wire
            return _record(key,capability,CapabilityObservation.SUCCESS if ok else CapabilityObservation.PARSE_ERROR,"reasoning_minimal_visible_answer" if ok else "reasoning_probe_inconclusive",stage="reasoning",input_count=1,transport_verified=wire,content_verified=ok)
        if capability == "image_input":
            stage="visual"
            from .visual_capabilities import _PROBE_PROMPT, _PROBE_IMAGE_DATA_URL, _probe_response_matches_expected
            from .message_parts import build_user_message_content
            response=await asyncio.wait_for(caller.chat_with_tools([{"role":"user","content":build_user_message_content(text=_PROBE_PROMPT,image_urls=[_PROBE_IMAGE_DATA_URL],image_detail="low")}],[],False),timeout)
            answer=_probe_response_matches_expected(str(getattr(response,"content","") or ""))
            return _record(key,capability,CapabilityObservation.SUCCESS if answer else CapabilityObservation.PARSE_ERROR,"probe_visual_succeeded" if answer else "probe_visual_inconclusive",stage=stage,input_count=1,transport_verified=True,content_verified=answer)
        if capability in {"audio_input","video_input"}:
            stage="media"
            builtin = str(sample_mode or "builtin").strip().lower() == "builtin"
            sample=get_diagnostic_media_sample(capability, sample_id) if builtin else None
            if builtin and (sample is None or not validate_diagnostic_media_sample(sample)[0]):
                return _record(key,capability,CapabilityObservation.PROBE_UNAVAILABLE,"builtin_sample_integrity_failed")
            if builtin:
                media_path = sample.path
            if media_path is None or not Path(media_path).is_file():
                return _record(key, capability, CapabilityObservation.PROBE_UNAVAILABLE, "media_probe_upload_required")
            from .media_provider_adapters import resolve_media_provider_adapter
            adapter=resolve_media_provider_adapter(provider)
            if not (adapter.supports_audio if capability == "audio_input" else adapter.supports_video):
                return _record(key,capability,CapabilityObservation.PROBE_UNAVAILABLE,"media_probe_primary_route_unavailable")
            from .diagnostic_media_samples import diagnostic_media_prompt, score_diagnostic_media_response, diagnostic_media_response_is_json
            from .media_understanding import analyze_audios_with_route_or_fallback, analyze_videos_with_route_or_fallback
            probe_runtime=_MediaProbeRuntime(runtime, provider)
            if capability == "audio_input":
                prompt=diagnostic_media_prompt(sample) if sample is not None else '{"media_input_accepted":true|false}'
                response, _ = await asyncio.wait_for(analyze_audios_with_route_or_fallback(runtime=probe_runtime,prompt=prompt,audio_refs=[str(media_path)]),timeout)
            else:
                prompt=diagnostic_media_prompt(sample) if sample is not None else '{"media_input_accepted":true|false}'
                response, _ = await asyncio.wait_for(analyze_videos_with_route_or_fallback(runtime=probe_runtime,prompt=prompt,video_refs=[str(media_path)]),timeout)
            transport_verified=bool(getattr(probe_runtime,"probe_transport_verified",False))
            if sample is None:
                accepted=score_custom_media_transport_response(response)
                if accepted is True:
                    record=_record(key,capability,CapabilityObservation.PARSE_ERROR,f"{capability}_custom_media_transport_verified")
                    return ProbeResult(record.capability_state,record.verification_state,record.detail_code,transport_verified=True,stage=stage,input_count=1)
                if accepted is False:
                    return _record(key,capability,CapabilityObservation.PARSE_ERROR,f"{capability}_custom_media_transport_rejected")
                return _record(key,capability,CapabilityObservation.PARSE_ERROR,f"{capability}_probe_inconclusive")
            verified=score_diagnostic_media_response(sample,response)
            code = f"{capability}_builtin_content_verified" if verified else f"{capability}_builtin_content_mismatch"
            if not diagnostic_media_response_is_json(response):
                code = "media_response_json_invalid"
            return _record(key,capability,CapabilityObservation.SUCCESS if verified else CapabilityObservation.PARSE_ERROR,code,stage=stage,input_count=1,transport_verified=transport_verified or verified,content_verified=verified)
        return _record(key,capability,CapabilityObservation.PROBE_UNAVAILABLE,"probe_unavailable")
    except (asyncio.TimeoutError, httpx.TimeoutException):
        return _record(key,capability,CapabilityObservation.TIMEOUT,"probe_timeout",stage=stage,input_count=1)
    except Exception as exc:
        # Classify controlled attributes, never copy provider response bodies.
        from .media_understanding import GeminiMediaResponseError
        if isinstance(exc, GeminiMediaResponseError):
            return _record(key, capability, CapabilityObservation.PARSE_ERROR, exc.diagnostic_code,
                           stage=stage, http_status=200, input_count=1)
        if isinstance(exc, ValueError) and str(exc) in {
            "inline_media_request_too_large", "video_file_too_large_for_inline_data",
            "audio_file_too_large_for_inline_data", "gemini_proxy_inline_media_too_large",
        }:
            return _record(key, capability, CapabilityObservation.PROBE_UNAVAILABLE,
                           "media_inline_budget_exceeded", stage=stage, input_count=1)
        status = getattr(exc, "status_code", None)
        if status is None:
            status = getattr(getattr(exc, "response", None), "status_code", None)
        if status in {401, 403}:
            return _record(key, capability, CapabilityObservation.PROVIDER_REJECTED, "probe_auth_rejected",stage=stage,http_status=status,input_count=1)
        if status == 429:
            return _record(key, capability, CapabilityObservation.PROVIDER_REJECTED, "probe_rate_limited",stage=stage,http_status=status,input_count=1)
        if isinstance(status, int) and 500 <= status <= 599:
            return _record(key, capability, CapabilityObservation.SERVER_ERROR, "probe_server_error",stage=stage,http_status=status,input_count=1)
        if isinstance(status, int) and 400 <= status <= 499:
            return _record(key, capability, CapabilityObservation.PROVIDER_REJECTED, "probe_request_rejected",stage=stage,http_status=status,input_count=1)
        if isinstance(exc, httpx.RequestError):
            return _record(key,capability,CapabilityObservation.NETWORK_ERROR,"probe_network_error",stage=stage,input_count=1)
        return _record(key,capability,CapabilityObservation.PARSE_ERROR,"probe_internal_failed",stage=stage,input_count=1)


async def run_route_probe(runtime: Any, key: RouteKey, provider: dict[str,Any], capability: str, **kwargs) -> ProbeResult:
    """One isolated total budget, including tool continuation and media prep."""
    from .llm_context import current_llm_context, set_llm_context, reset_llm_context, LLM_RETRY_POLICY_SINGLE_ATTEMPT
    current=current_llm_context()
    token=set_llm_context(group_id=current.get("group_id",""),user_id=current.get("user_id",""),
        platform=current.get("platform",""),bot_id=current.get("bot_id",""),purpose="capability_probe",
        retry_policy=LLM_RETRY_POLICY_SINGLE_ATTEMPT)
    timeout=max(0.001,min(45.0,float(getattr(getattr(runtime,"plugin_config",None),"personification_route_probe_item_timeout_seconds",45) or 45)))
    try:
        return await asyncio.wait_for(_run_route_probe(runtime,key,provider,capability,**kwargs),timeout)
    except asyncio.TimeoutError:
        return _record(key,capability,CapabilityObservation.TIMEOUT,"probe_timeout",stage="deadline")
    finally:
        reset_llm_context(token)
