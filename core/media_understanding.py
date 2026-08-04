from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import mimetypes
from pathlib import Path
from typing import Any, Sequence

import httpx

from .ai_routes import resolve_video_fallback_provider
from .audio_transcription import transcribe_audio_file
from .gemini_transport import raise_for_gemini_status, request_with_gemini_auth
from .image_input import is_image_input_unsupported_error, provider_supports_vision
from .sensitive_data import sanitize_text
from .media_refs import normalize_video_ref
from .message_parts import build_user_message_content
from .model_router import MODEL_ROLE_STICKER, get_model_override_for_role
from .llm_context import use_single_attempt_retry_policy
from .visual_capabilities import VISUAL_ROUTE_AGENT, error_indicates_vision_unavailable, provider_supports_video
from .video_understanding import prepare_video_storyboard


def build_tool_caller(config: Any) -> Any:
    from ..skills.skillpacks.tool_caller.scripts.impl import build_tool_caller

    return build_tool_caller(config)


def _build_tool_caller(config: Any) -> Any:
    return build_tool_caller(config)


_VIDEO_INLINE_MAX_BYTES = 20 * 1024 * 1024
_GEMINI_DEFAULT_MODEL = "gemini-2.0-flash"
_GENERIC_REFUSAL_TEXTS = {
    "i can't discuss that.",
    "i cant discuss that.",
    "i cannot discuss that.",
    "i'm sorry, but i can't discuss that.",
    "抱歉，我不能讨论这个。",
    "抱歉，我无法讨论这个。",
}


def _normalize_media_api_type(api_type: str) -> str:
    value = str(api_type or "").strip().lower().replace("-", "_")
    if value in {"gemini", "gemini_official"}:
        return "gemini_official"
    if value in {"gemini_cli", "geminicli", "antigravity_cli", "antigravity", "agy", "agy_cli"}:
        return "antigravity_cli"
    if value in {"openai_codex", "codex"}:
        return "openai_codex"
    if value in {"claude_code", "claudecode", "claude_cli"}:
        return "claude_code"
    if value == "anthropic":
        return "anthropic"
    return "openai"


def normalize_video_route_mode(value: Any) -> str:
    normalized = str(value or "auto").strip().lower().replace("-", "_")
    aliases = {"direct": "native", "frames": "storyboard", "frame": "storyboard"}
    normalized = aliases.get(normalized, normalized)
    return normalized if normalized in {"auto", "native", "hybrid", "storyboard"} else "auto"


def _is_provider_usable(provider: dict[str, Any]) -> bool:
    api_type = _normalize_media_api_type(str(provider.get("api_type", "") or "openai"))
    model = str(provider.get("model", "") or "").strip()
    if not model:
        return False
    if api_type in {"openai_codex", "gemini_cli", "antigravity_cli", "claude_code"}:
        return True
    return bool(str(provider.get("api_key", "") or "").strip())


def _primary_provider_candidates(runtime: Any) -> list[dict[str, Any]]:
    providers: list[dict[str, Any]] = []
    getter = getattr(runtime, "get_configured_api_providers", None)
    if callable(getter):
        try:
            providers = [dict(item) for item in list(getter() or []) if isinstance(item, dict)]
        except Exception:
            providers = []
    if providers:
        return _apply_sticker_model_override(runtime, providers)

    primary = get_primary_provider_config(runtime)
    if _is_provider_usable(primary):
        payload = dict(primary)
        payload.setdefault("name", "legacy_primary")
        return _apply_sticker_model_override(runtime, [payload])
    return []


def _apply_sticker_model_override(runtime: Any, providers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    plugin_config = getattr(runtime, "plugin_config", None)
    if plugin_config is None:
        return providers
    model_override = get_model_override_for_role(plugin_config, MODEL_ROLE_STICKER)
    if not model_override:
        return providers
    patched: list[dict[str, Any]] = []
    for provider in providers:
        cloned = dict(provider)
        cloned["model"] = model_override
        patched.append(cloned)
    return patched


class _ProviderConfigProxy:
    def __init__(self, original: Any, provider: dict[str, Any]) -> None:
        self._original = original
        self._provider = dict(provider or {})

    def __getattr__(self, name: str) -> Any:
        if name == "personification_api_type":
            return self._provider.get("api_type", "openai")
        if name == "personification_api_url":
            return self._provider.get("api_url", "")
        if name == "personification_api_key":
            return self._provider.get("api_key", "")
        if name == "personification_model":
            return self._provider.get("model", "")
        if name == "personification_gemini_auth_mode":
            return self._provider.get("gemini_auth_mode", "auto")
        if name == "personification_codex_auth_path":
            return self._provider.get("auth_path", "")
        if name == "personification_gemini_cli_auth_path":
            return self._provider.get("auth_path", "")
        if name == "personification_gemini_cli_project":
            return self._provider.get("project", "")
        if name == "personification_antigravity_cli_auth_path":
            return self._provider.get("auth_path", "")
        if name == "personification_antigravity_cli_project":
            return self._provider.get("project", "")
        if name == "personification_claude_code_auth_path":
            return self._provider.get("auth_path", "")
        if name == "personification_thinking_mode":
            return getattr(self._original, name, "none")
        return getattr(self._original, name)


def _invalid_media_text(text: str) -> bool:
    value = str(text or "").strip()
    if not value:
        return True
    return " ".join(value.lower().split()) in _GENERIC_REFUSAL_TEXTS


def _log_warning(runtime: Any, message: str) -> None:
    logger = getattr(runtime, "logger", None)
    if logger is None:
        return
    try:
        logger.warning(message)
    except Exception:
        pass


def get_primary_provider_config(runtime: Any) -> dict[str, str]:
    providers = []
    getter = getattr(runtime, "get_configured_api_providers", None)
    if callable(getter):
        try:
            providers = list(getter() or [])
        except Exception:
            providers = []
    if providers and isinstance(providers[0], dict):
        primary = providers[0]
        return {
            "api_type": str(primary.get("api_type", "") or ""),
            "api_url": str(primary.get("api_url", "") or ""),
            "api_key": str(primary.get("api_key", "") or ""),
            "model": str(primary.get("model", "") or ""),
            "auth_path": str(primary.get("auth_path", "") or ""),
            "project": str(primary.get("project", "") or ""),
        }
    plugin_config = getattr(runtime, "plugin_config", None)
    api_type = str(getattr(plugin_config, "personification_api_type", "") or "")
    normalized_type = _normalize_media_api_type(api_type)
    if normalized_type == "openai_codex":
        auth_path = str(getattr(plugin_config, "personification_codex_auth_path", "") or "")
    elif normalized_type == "gemini_cli":
        auth_path = str(getattr(plugin_config, "personification_gemini_cli_auth_path", "") or "")
    elif normalized_type == "antigravity_cli":
        auth_path = str(getattr(plugin_config, "personification_antigravity_cli_auth_path", "") or "")
    elif normalized_type == "claude_code":
        auth_path = str(getattr(plugin_config, "personification_claude_code_auth_path", "") or "")
    else:
        auth_path = ""
    return {
        "api_type": api_type,
        "api_url": str(getattr(plugin_config, "personification_api_url", "") or ""),
        "api_key": str(getattr(plugin_config, "personification_api_key", "") or ""),
        "model": str(getattr(plugin_config, "personification_model", "") or ""),
        "auth_path": auth_path,
        "project": str(
            getattr(
                plugin_config,
                "personification_antigravity_cli_project"
                if normalized_type == "antigravity_cli"
                else "personification_gemini_cli_project",
                "",
            )
            or ""
        ),
    }


async def _try_primary_image_routes(
    *,
    runtime: Any,
    prompt: str,
    refs: Sequence[str],
    route_name: str,
    image_detail: str,
) -> str:
    plugin_config = getattr(runtime, "plugin_config", None)
    if plugin_config is None:
        return ""
    for provider in _primary_provider_candidates(runtime):
        api_type = str(provider.get("api_type", "") or "")
        model = str(provider.get("model", "") or "")
        provider_name = str(provider.get("name", "") or model or api_type or "primary")
        if not provider_supports_vision(api_type, model, route_name=route_name):
            _log_warning(
                runtime,
                f"[vision] provider={provider_name} is not known to support image input; trying anyway",
            )
        try:
            caller = _build_tool_caller(_ProviderConfigProxy(plugin_config, provider))
            response = await caller.chat_with_tools(
                messages=[
                    {
                        "role": "user",
                        "content": build_user_message_content(
                            text=prompt,
                            image_urls=list(refs),
                            image_detail=image_detail,
                        ),
                    }
                ],
                tools=[],
                use_builtin_search=False,
            )
        except Exception as exc:
            if not (is_image_input_unsupported_error(exc) or error_indicates_vision_unavailable(exc)):
                _log_warning(
                    runtime,
                    f"[vision] primary image route failed provider={provider_name}: {sanitize_text(exc)}",
                )
            continue
        if bool(getattr(response, "vision_unavailable", False)):
            continue
        content = str(getattr(response, "content", "") or "").strip()
        if not _invalid_media_text(content):
            return content
    return ""


async def _try_primary_video_routes(
    *,
    runtime: Any,
    prompt: str,
    refs: Sequence[str],
    route_name: str,
) -> str:
    for provider in _primary_provider_candidates(runtime):
        api_type = str(provider.get("api_type", "") or "")
        model = str(provider.get("model", "") or "")
        provider_name = str(provider.get("name", "") or model or api_type or "primary")
        normalized_type = _normalize_media_api_type(api_type)
        if normalized_type != "gemini_official":
            continue
        if not str(provider.get("api_key", "") or "").strip():
            continue
        if not provider_supports_video(api_type, model, route_name=route_name):
            continue
        try:
            result = await _call_gemini_media(
                api_key=str(provider.get("api_key", "") or ""),
                base_url=str(provider.get("api_url", "") or ""),
                model=model or _GEMINI_DEFAULT_MODEL,
                auth_mode=str(provider.get("gemini_auth_mode", "auto") or "auto"),
                prompt=prompt,
                video_refs=refs,
            )
        except Exception as exc:
            if not error_indicates_vision_unavailable(exc):
                _log_warning(
                    runtime,
                    f"[video] primary route failed provider={provider_name}: {sanitize_text(exc)}",
                )
            continue
        if not _invalid_media_text(result):
            return str(result or "").strip()
    return ""


def primary_route_supports_native_video(
    runtime: Any,
    *,
    route_name: str = VISUAL_ROUTE_AGENT,
) -> bool:
    for provider in _primary_provider_candidates(runtime):
        api_type = str(provider.get("api_type", "") or "")
        model = str(provider.get("model", "") or "")
        if _normalize_media_api_type(api_type) != "gemini_official":
            continue
        if not str(provider.get("api_key", "") or "").strip():
            continue
        if provider_supports_video(api_type, model, route_name=route_name):
            return True
    return False


def get_primary_provider_signature(runtime: Any) -> tuple[str, str]:
    primary = get_primary_provider_config(runtime)
    return primary["api_type"], primary["model"]


def get_primary_image_route_fingerprint(
    runtime: Any,
    *,
    route_name: str = VISUAL_ROUTE_AGENT,
) -> str:
    routes: list[dict[str, str]] = []
    for provider in _primary_provider_candidates(runtime):
        api_key = str(provider.get("api_key", "") or "")
        auth_path = str(provider.get("auth_path", "") or "")
        routes.append(
            {
                "name": str(provider.get("name", "") or ""),
                "api_type": _normalize_media_api_type(str(provider.get("api_type", "") or "")),
                "api_url": str(provider.get("api_url", "") or "").strip().rstrip("/"),
                "model": str(provider.get("model", "") or "").strip(),
                "auth": hashlib.sha256(f"{api_key}\0{auth_path}".encode("utf-8")).hexdigest(),
                "project": str(provider.get("project", "") or "").strip(),
            }
        )
    payload = json.dumps(
        {"route_name": str(route_name or ""), "routes": routes},
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _build_video_fallback_provider_config(runtime: Any) -> dict[str, str] | None:
    plugin_config = getattr(runtime, "plugin_config", None)
    if plugin_config is None:
        return None
    resolution = resolve_video_fallback_provider(plugin_config, getattr(runtime, "logger", None), warn=True)
    if resolution is None:
        return None
    payload = dict(resolution.provider)
    normalized_type = _normalize_media_api_type(str(payload.get("api_type", "") or ""))
    if normalized_type not in {"gemini_official"}:
        return None
    return {
        "api_type": normalized_type,
        "api_url": str(payload.get("api_url", "") or "").strip(),
        "api_key": str(payload.get("api_key", "") or "").strip(),
        "model": str(payload.get("model", "") or "").strip() or _GEMINI_DEFAULT_MODEL,
        "auth_path": str(payload.get("auth_path", "") or "").strip(),
        "gemini_auth_mode": str(payload.get("gemini_auth_mode", "auto") or "auto").strip(),
    }


def _gemini_endpoint(base_url: str, model: str) -> str:
    raw = str(base_url or "").strip().rstrip("/")
    if not raw:
        raw = "https://generativelanguage.googleapis.com"
    lower = raw.lower()
    if lower.endswith("/v1beta"):
        root = raw
    elif lower.endswith("/v1"):
        root = f"{raw[:-3]}/v1beta"
    else:
        root = f"{raw}/v1beta"
    return f"{root}/models/{model}:generateContent"


def _gemini_api_root(base_url: str) -> str:
    raw = str(base_url or "").strip().rstrip("/") or "https://generativelanguage.googleapis.com"
    lower = raw.lower()
    if lower.endswith("/v1beta"):
        return raw
    if lower.endswith("/v1"):
        return f"{raw[:-3]}/v1beta"
    return f"{raw}/v1beta"


def _gemini_headers() -> dict[str, str]:
    return {"Content-Type": "application/json"}


def _gemini_image_part(image_ref: str) -> dict[str, Any]:
    from .image_refs import normalize_image_ref
    from ..skills.skillpacks.tool_caller.scripts.impl import _split_data_url

    normalized_image_url, problem = normalize_image_ref(image_ref)
    if not normalized_image_url:
        raise ValueError(f"invalid_image_ref:{problem or 'unknown'}")
    parsed = _split_data_url(normalized_image_url)
    if parsed:
        mime_type, base64_data = parsed
        return {
            "inlineData": {
                "mimeType": mime_type,
                "data": base64_data,
            }
        }
    return {
        "fileData": {
            "mimeType": "image/*",
            "fileUri": normalized_image_url,
        }
    }


def _gemini_video_part(video_ref: str) -> dict[str, Any]:
    normalized_video_ref, problem = normalize_video_ref(video_ref)
    if not normalized_video_ref:
        raise ValueError(f"invalid_video_ref:{problem or 'unknown'}")
    if normalized_video_ref.startswith(("http://", "https://")):
        mime_type, _ = mimetypes.guess_type(normalized_video_ref)
        return {
            "fileData": {
                "mimeType": mime_type or "video/mp4",
                "fileUri": normalized_video_ref,
            }
        }

    path = Path(normalized_video_ref)
    payload = path.read_bytes()
    if len(payload) > _VIDEO_INLINE_MAX_BYTES:
        raise ValueError("video_file_too_large_for_inline_data")
    mime_type, _ = mimetypes.guess_type(str(path))
    return {
        "inlineData": {
            "mimeType": mime_type or "video/mp4",
            "data": base64.b64encode(payload).decode("ascii"),
        }
    }


async def _upload_gemini_video_file(
    *,
    client: httpx.AsyncClient,
    api_key: str,
    base_url: str,
    auth_mode: str,
    path: Path,
) -> tuple[dict[str, Any], str]:
    size = path.stat().st_size
    mime_type, _ = mimetypes.guess_type(str(path))
    mime = mime_type or "video/mp4"
    upload_endpoint = f"{_gemini_api_root(base_url).rsplit('/v1beta', 1)[0]}/upload/v1beta/files"

    async def _start(auth):  # noqa: ANN001, ANN202
        return await client.post(
            upload_endpoint,
            headers={
                **auth.headers,
                "Content-Type": "application/json",
                "X-Goog-Upload-Protocol": "resumable",
                "X-Goog-Upload-Command": "start",
                "X-Goog-Upload-Header-Content-Length": str(size),
                "X-Goog-Upload-Header-Content-Type": mime,
            },
            params=auth.params,
            json={"file": {"display_name": path.name}},
        )

    start_result = await request_with_gemini_auth(
        endpoint=upload_endpoint,
        api_key=api_key,
        auth_mode=auth_mode,
        send=_start,
        allow_negotiation=not use_single_attempt_retry_policy(),
    )
    start_response = start_result.response
    raise_for_gemini_status(
        start_response,
        auth_mode=start_result.mode,
        request_count=start_result.request_count,
    )
    upload_url = str(start_response.headers.get("x-goog-upload-url") or "").strip()
    if not upload_url:
        raise ValueError("gemini_video_upload_url_missing")
    with path.open("rb") as handle:
        upload_response = await client.post(
            upload_url,
            headers={
                "Content-Length": str(size),
                "X-Goog-Upload-Offset": "0",
                "X-Goog-Upload-Command": "upload, finalize",
            },
            content=handle,
        )
    upload_response.raise_for_status()
    payload = dict(upload_response.json() or {})
    file_info = payload.get("file") if isinstance(payload.get("file"), dict) else payload
    file_name = str(file_info.get("name") or "").strip()
    file_uri = str(file_info.get("uri") or "").strip()
    if not file_name or not file_uri:
        raise ValueError("gemini_video_upload_result_invalid")

    file_endpoint = f"{_gemini_api_root(base_url)}/{file_name}"
    deadline = asyncio.get_running_loop().time() + 90.0
    while str(file_info.get("state") or {}).upper() not in {"ACTIVE", "FAILED"}:
        if asyncio.get_running_loop().time() >= deadline:
            raise TimeoutError("gemini_video_file_processing_timeout")

        async def _poll(auth):  # noqa: ANN001, ANN202
            return await client.get(file_endpoint, headers=auth.headers, params=auth.params)

        poll_result = await request_with_gemini_auth(
            endpoint=file_endpoint,
            api_key=api_key,
            auth_mode=auth_mode,
            send=_poll,
            allow_negotiation=not use_single_attempt_retry_policy(),
        )
        poll_response = poll_result.response
        raise_for_gemini_status(
            poll_response,
            auth_mode=poll_result.mode,
            request_count=poll_result.request_count,
        )
        file_info = dict(poll_response.json() or {})
        await asyncio.sleep(1.0)
    if str(file_info.get("state") or "").upper() != "ACTIVE":
        raise ValueError("gemini_video_file_processing_failed")
    return {"fileData": {"mimeType": mime, "fileUri": file_uri}}, file_name


async def _delete_gemini_file(
    *,
    client: httpx.AsyncClient,
    api_key: str,
    base_url: str,
    auth_mode: str,
    file_name: str,
) -> None:
    endpoint = f"{_gemini_api_root(base_url)}/{str(file_name or '')}"

    async def _delete(auth):  # noqa: ANN001, ANN202
        return await client.delete(endpoint, headers=auth.headers, params=auth.params)

    result = await request_with_gemini_auth(
        endpoint=endpoint,
        api_key=api_key,
        auth_mode=auth_mode,
        send=_delete,
        allow_negotiation=False,
    )
    if result.response.status_code not in {200, 204, 404}:
        raise_for_gemini_status(result.response, auth_mode=result.mode, request_count=result.request_count)


async def _call_gemini_media(
    *,
    api_key: str,
    base_url: str,
    model: str,
    prompt: str,
    image_refs: Sequence[str] = (),
    video_refs: Sequence[str] = (),
    auth_mode: str = "auto",
) -> str:
    parts: list[dict[str, Any]] = [{"text": str(prompt or "").strip() or "请分析这段媒体内容"}]
    for ref in image_refs:
        parts.append(_gemini_image_part(str(ref or "").strip()))
    endpoint = _gemini_endpoint(base_url, model or _GEMINI_DEFAULT_MODEL)
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(90.0, connect=15.0),
        follow_redirects=False,
    ) as client:
        uploaded_files: list[str] = []
        try:
            for ref in video_refs:
                normalized, problem = normalize_video_ref(str(ref or "").strip())
                if not normalized:
                    raise ValueError(f"invalid_video_ref:{problem or 'unknown'}")
                if normalized.startswith(("http://", "https://")) or Path(normalized).stat().st_size <= _VIDEO_INLINE_MAX_BYTES:
                    parts.append(_gemini_video_part(normalized))
                else:
                    part, file_name = await _upload_gemini_video_file(
                        client=client,
                        api_key=api_key,
                        base_url=base_url,
                        auth_mode=auth_mode,
                        path=Path(normalized),
                    )
                    parts.append(part)
                    uploaded_files.append(file_name)
            payload = {"contents": [{"role": "user", "parts": parts}]}

            async def _send(auth):  # noqa: ANN001, ANN202
                return await client.post(
                    endpoint,
                    headers={**_gemini_headers(), **auth.headers},
                    params=auth.params,
                    json=payload,
                )

            auth_result = await request_with_gemini_auth(
                endpoint=endpoint.rsplit("/models/", 1)[0],
                api_key=api_key,
                auth_mode=auth_mode,
                send=_send,
                allow_negotiation=not use_single_attempt_retry_policy(),
            )
            response = auth_result.response
            raise_for_gemini_status(
                response,
                auth_mode=auth_result.mode,
                request_count=auth_result.request_count,
            )
            data = dict(response.json() or {})
        finally:
            for file_name in uploaded_files:
                try:
                    await _delete_gemini_file(
                        client=client,
                        api_key=api_key,
                        base_url=base_url,
                        auth_mode=auth_mode,
                        file_name=file_name,
                    )
                except Exception:
                    pass
    candidates = list((data.get("candidates") or []))
    if not candidates:
        return ""
    content = candidates[0].get("content", {}) if isinstance(candidates[0], dict) else {}
    parts = list(content.get("parts") or []) if isinstance(content, dict) else []
    texts: list[str] = []
    for part in parts:
        if isinstance(part, dict) and part.get("text"):
            texts.append(str(part.get("text", "")))
    return "".join(texts).strip()


async def analyze_images_with_route_or_fallback(
    *,
    runtime: Any,
    prompt: str,
    image_refs: Sequence[str],
    route_name: str = VISUAL_ROUTE_AGENT,
    image_detail: str = "low",
    fallback_vision_caller: Any = None,
) -> tuple[str, str]:
    refs = [str(item or "").strip() for item in image_refs if str(item or "").strip()]
    if not refs:
        return "", "missing_images"

    primary_result = await _try_primary_image_routes(
        runtime=runtime,
        prompt=prompt,
        refs=refs,
        route_name=route_name,
        image_detail=image_detail,
    )
    if primary_result:
        return primary_result, "route_direct"

    fallback = fallback_vision_caller or getattr(runtime, "vision_caller", None)
    if fallback is None:
        return "", "vision_unavailable"
    outputs: list[str] = []
    for ref in refs:
        try:
            output = await fallback.describe(prompt, ref)
        except Exception as exc:
            _log_warning(runtime, f"[vision] fallback image route failed: {sanitize_text(exc)}")
            continue
        if not _invalid_media_text(output):
            outputs.append(str(output or "").strip())
    result = "\n".join(part for part in outputs if str(part or "").strip()).strip()
    return (result, "vision_fallback" if result else "vision_unavailable")


async def analyze_images_with_primary_route_joint_only(
    *,
    runtime: Any,
    prompt: str,
    image_refs: Sequence[str],
    route_name: str = VISUAL_ROUTE_AGENT,
    image_detail: str = "low",
) -> tuple[str, str]:
    """Analyze exactly two images in one primary-route request.

    This API deliberately has no per-image fallback. A joint comparison loses
    its meaning if either image is analyzed in a separate request.
    """
    refs = [str(item or "").strip() for item in image_refs if str(item or "").strip()]
    if len(refs) != 2:
        return "", "missing_joint_images"
    primary_result = await _try_primary_image_routes(
        runtime=runtime,
        prompt=prompt,
        refs=refs,
        route_name=route_name,
        image_detail=image_detail,
    )
    if primary_result:
        return primary_result, "route_direct"
    return "", "joint_vision_unavailable"


async def analyze_videos_with_route_or_fallback(
    *,
    runtime: Any,
    prompt: str,
    video_refs: Sequence[str],
    route_name: str = VISUAL_ROUTE_AGENT,
    context_terms: Sequence[str] = (),
) -> tuple[str, str]:
    refs = [str(item or "").strip() for item in video_refs if str(item or "").strip()]
    if not refs:
        return "", "missing_videos"
    plugin_config = getattr(runtime, "plugin_config", None)
    if plugin_config is None:
        return "", "video_disabled"
    video_enabled = bool(getattr(plugin_config, "personification_video_understanding_enabled", False))
    if not video_enabled and not primary_route_supports_native_video(runtime, route_name=route_name):
        return "", "video_disabled"

    route_mode = normalize_video_route_mode(
        getattr(plugin_config, "personification_video_route_mode", "auto")
    )

    async def _native_result() -> tuple[str, str]:
        primary_result = await _try_primary_video_routes(
            runtime=runtime,
            prompt=prompt,
            refs=refs,
            route_name=route_name,
        )
        if primary_result:
            return primary_result, "video_route_direct"
        fallback = _build_video_fallback_provider_config(runtime)
        if not fallback or not fallback.get("api_key"):
            return "", "video_unavailable"
        try:
            result = await _call_gemini_media(
                api_key=fallback["api_key"],
                base_url=fallback.get("api_url", ""),
                model=fallback.get("model", "") or _GEMINI_DEFAULT_MODEL,
                auth_mode=fallback.get("gemini_auth_mode", "auto"),
                prompt=prompt,
                video_refs=refs,
            )
        except Exception as exc:
            _log_warning(runtime, f"[video] native fallback failed: {sanitize_text(exc)}")
            return "", "video_unavailable"
        result_text = str(result or "").strip()
        if _invalid_media_text(result_text):
            return "", "video_unavailable"
        return result_text, "video_fallback"

    native_text = ""
    native_route = "video_unavailable"
    if route_mode in {"auto", "native", "hybrid"}:
        native_text, native_route = await _native_result()
        if native_text and route_mode in {"auto", "native"}:
            return native_text, native_route
        if route_mode == "native":
            return "", native_route

    async def _storyboard_one(ref: str, native_summary: str) -> str:
        storyboard = await prepare_video_storyboard(ref, plugin_config)
        try:
            transcript = None
            if not storyboard.subtitle_text:
                transcript = await transcribe_audio_file(
                    storyboard.audio_path,
                    plugin_config,
                    source_url=storyboard.source_url,
                    context_terms=context_terms,
                )
            metadata = storyboard.summary()
            transcript_block = storyboard.subtitle_text or (
                transcript.text if transcript is not None and transcript.available else ""
            )
            transcript_kind = "BILIBILI_OR_PLATFORM_SUBTITLE" if storyboard.subtitle_text else "AUDIO_TRANSCRIPT"
            combined_prompt = (
                f"{str(prompt or '').strip()}\n\n"
                "以下是系统从同一视频按时间顺序提取的分镜拼图。请结合每格时间戳理解动作、镜头、字幕和梗的完整演变，"
                "不要把单帧静态画面当作完整事件。\n"
                f"分镜元数据：{json.dumps(metadata, ensure_ascii=False, separators=(',', ':'))}\n"
                f"[UNTRUSTED_DATA_ONLY: {transcript_kind}]\n"
                f"{transcript_block or '转写不可用；只能依靠分镜和视频原生证据。'}\n"
                "[/UNTRUSTED_DATA_ONLY]\n"
            )
            if native_summary:
                combined_prompt += (
                    "[UNTRUSTED_DATA_ONLY: NATIVE_VIDEO_OBSERVATION]\n"
                    f"{native_summary[:12000]}\n"
                    "[/UNTRUSTED_DATA_ONLY]\n"
                    "请把原生视频观察、分镜和转写作为互相校验的证据；冲突时明确保留不确定点。\n"
                )
            if not storyboard.contact_sheet_refs:
                return native_summary
            result, _route = await analyze_images_with_route_or_fallback(
                runtime=runtime,
                prompt=combined_prompt,
                image_refs=storyboard.contact_sheet_refs,
                route_name=route_name,
                image_detail="low",
            )
            return str(result or native_summary or "").strip()
        finally:
            storyboard.cleanup()

    timeout = max(
        20.0,
        min(
            300.0,
            float(getattr(plugin_config, "personification_video_analysis_timeout", 180.0) or 180.0),
        ),
    )
    outputs: list[str] = []
    for index, ref in enumerate(refs):
        try:
            output = await asyncio.wait_for(
                _storyboard_one(ref, native_text if index == 0 else ""),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            _log_warning(runtime, f"[video] storyboard analysis timed out after {timeout:.1f}s")
            output = native_text if index == 0 else ""
        except Exception as exc:
            _log_warning(runtime, f"[video] storyboard analysis failed: {sanitize_text(exc)}")
            output = native_text if index == 0 else ""
        if output:
            outputs.append(output)
    result_text = "\n".join(outputs).strip()
    if not result_text or _invalid_media_text(result_text):
        return "", native_route if native_text else "video_unavailable"
    return result_text, "video_hybrid" if native_text else "video_storyboard"


__all__ = [
    "analyze_images_with_primary_route_joint_only",
    "analyze_images_with_route_or_fallback",
    "analyze_videos_with_route_or_fallback",
    "get_primary_image_route_fingerprint",
    "get_primary_provider_config",
    "get_primary_provider_signature",
    "normalize_video_route_mode",
    "primary_route_supports_native_video",
]
