from __future__ import annotations

import asyncio
import base64
import binascii
import copy
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from .protocol_adapter import get_protocol_adapter

MAX_IMAGES = 4
MAX_IMAGE_BYTES = 32 * 1024 * 1024

class TimImagePreparationError(ValueError):
    """Fixed safe reason; preparation fails before any visible send."""


def _image_bytes(value: Any, remaining: int) -> bytes | None:
    if isinstance(value, (bytes, bytearray, memoryview)):
        if (value.nbytes if isinstance(value, memoryview) else len(value)) > remaining:
            raise TimImagePreparationError("tim_image_budget_exceeded")
        payload = bytes(value)
    elif isinstance(value, Path):
        payload = _read_file(value, remaining)
    elif isinstance(value, str):
        if value.startswith(("https://", "http://")):
            return None  # Mobile handles bounded public URL retrieval.
        if value.startswith("data:image/"):
            head, separator, encoded = value.partition(",")
            if not separator or head not in {"data:image/png;base64", "data:image/jpeg;base64"}:
                raise TimImagePreparationError("tim_image_format_unsupported")
            payload = _decode(encoded, remaining)
        elif value.startswith("base64://"):
            payload = _decode(value[9:], remaining)
        elif value.startswith("file://"):
            parsed = urlsplit(value)
            if parsed.netloc not in {"", "localhost"} or parsed.query or parsed.fragment:
                raise TimImagePreparationError("tim_image_file_uri_invalid")
            path = unquote(parsed.path)
            if len(path) > 2 and path[0] == "/" and path[2] == ":":
                path = path[1:]
            payload = _read_file(Path(path), remaining)
        else:
            payload = _read_file(Path(value), remaining)
    else:
        raise TimImagePreparationError("tim_image_source_unsupported")
    if not (payload.startswith(b"\x89PNG\r\n\x1a\n") or payload.startswith(b"\xff\xd8\xff")):
        raise TimImagePreparationError("tim_image_format_unsupported")
    return payload


def _read_file(path: Path, remaining: int) -> bytes:
    try:
        resolved = path.resolve(strict=True)
        roots = (Path.cwd().resolve() / "data", Path(tempfile.gettempdir()).resolve())
        if not any(resolved.is_relative_to(root) for root in roots) or not resolved.is_file():
            raise TimImagePreparationError("tim_image_path_not_allowed")
        with resolved.open("rb") as stream:
            payload = stream.read(remaining + 1)
    except (OSError, ValueError):
        raise TimImagePreparationError("tim_image_file_unavailable") from None
    if len(payload) > remaining:
        raise TimImagePreparationError("tim_image_budget_exceeded")
    return payload


def _decode(encoded: str, remaining: int) -> bytes:
    if len(encoded) > 4 * ((remaining + 2) // 3):
        raise TimImagePreparationError("tim_image_budget_exceeded")
    try:
        payload = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        raise TimImagePreparationError("tim_image_base64_invalid") from None
    if len(payload) > remaining:
        raise TimImagePreparationError("tim_image_budget_exceeded")
    return payload


def _prepare(message: Any) -> Any:
    from nonebot.adapters.onebot.v11 import Message, MessageSegment
    if isinstance(message, str):
        # Plain visible text must never be upgraded to executable CQ image segments.
        return message
    single = isinstance(message, MessageSegment)
    segments = [message] if single else list(message)
    output = []
    remaining = MAX_IMAGE_BYTES
    count = 0
    for segment in segments:
        if isinstance(segment, dict):
            kind, data = segment.get("type"), dict(segment.get("data") or {})
        else:
            kind, data = segment.type, dict(segment.data)
        if kind == "image":
            count += 1
            if count > MAX_IMAGES:
                raise TimImagePreparationError("tim_image_count_exceeded")
            if data.get("type") is None:
                data["type"] = "normal"
            payload = _image_bytes(data.get("file"), remaining)
            if payload is not None:
                remaining -= len(payload)
                data["file"] = "base64://" + base64.b64encode(payload).decode("ascii")
        output.append(MessageSegment(kind, copy.deepcopy(data)))
    return output[0] if single else Message(output)


async def prepare_tim_message(bot: Any, message: Any, plugin_config: Any = None) -> Any:
    """Prepare only images destined for an identified TIM bridge, without mutating input."""
    # Avoid a discovery call for text-only sends.
    if isinstance(message, str):
        return message
    segments = [message] if hasattr(message, "type") else list(message)
    if not any((segment.get("type") if isinstance(segment, dict) else getattr(segment, "type", "")) == "image" for segment in segments):
        return message
    identity = await get_protocol_adapter(bot, plugin_config).identity()
    if identity.app_name.strip().lower() != "tim onebot bridge":
        return message
    return await asyncio.to_thread(_prepare, message)


def tim_send_options(original: Any, prepared: Any) -> dict[str, Any]:
    """Only a successful TIM image copy changes its API wait budget."""
    return {"_timeout": 95} if prepared is not original else {}
