from __future__ import annotations

"""Safe, untrusted merged-forward projection for a single reply turn."""

import asyncio
import base64
import hashlib
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from .shared_content import normalize_merged_forward
from .safe_image_download import SafeImageDownloadError, download_public_image
from .sticker_library import validated_expression_image
from .turn_media import TurnMediaRef, coerce_turn_media


def _nodes_in_order(nodes: Any):
    for node in tuple(nodes or ()):
        yield node
        yield from _nodes_in_order(getattr(node, "children", ()) or ())


def _public_image_url(value: Any) -> str:
    candidate = str(value or "").strip()
    parsed = urlparse(candidate)
    return candidate if parsed.scheme.lower() in {"http", "https"} and bool(parsed.netloc) else ""


def _remaining_seconds(response_deadline: float | None) -> float | None:
    if not isinstance(response_deadline, (int, float)):
        return None
    return float(response_deadline) - asyncio.get_running_loop().time()


async def _download_forward_image(
    url: str,
    *,
    response_deadline: float | None,
) -> str:
    """Return a validated, frozen image transport without depending on handlers.

    The core downloader pins public destinations and the second validation
    verifies actual image bytes, MIME and pixel bounds before a data URL is
    permitted into a provider request.
    """
    remaining = _remaining_seconds(response_deadline)
    timeout = min(10.0, remaining) if remaining is not None else 10.0
    if timeout <= 0:
        return ""
    try:
        downloaded = await asyncio.wait_for(
            download_public_image(
                url,
                headers={"Accept": "image/jpeg,image/png,image/webp,image/gif"},
                timeout=timeout,
                connect_timeout=min(4.0, timeout),
                max_bytes=8 * 1024 * 1024,
                allowed_mimes={"image/jpeg", "image/png", "image/webp", "image/gif"},
                max_redirects=4,
            ),
            timeout=timeout,
        )
        return validated_expression_image(
            bytes(downloaded.content),
            str(downloaded.content_type or "").strip().lower(),
        )
    except asyncio.CancelledError:
        raise
    except (asyncio.TimeoutError, SafeImageDownloadError, ValueError):
        return ""
    except Exception:
        return ""


@dataclass(frozen=True)
class ForwardContext:
    text: str
    media: tuple[TurnMediaRef, ...]
    truncated: bool = False


def merge_forward_media(
    primary: Any,
    occurrence_source: Any,
) -> list[TurnMediaRef]:
    """Retain forward occurrences beside an independently selected manifest.

    Follow-up selection controls normal historical/quoted activation.  A
    safely materialized forward belongs to the triggering turn instead, so it
    must be appended to both the active refs and review manifest.  Stable
    media ids make the operation idempotent across normal → YAML handoff.
    """
    merged = list(coerce_turn_media(primary))
    seen_ids = {item.media_id for item in merged if item.media_id}
    for item in coerce_turn_media(occurrence_source):
        if item.origin != "forward":
            continue
        if item.media_id and item.media_id in seen_ids:
            continue
        merged.append(item)
        if item.media_id:
            seen_ids.add(item.media_id)
    return merged


async def build_forward_context(
    bot: Any,
    event: Any,
    *,
    logger: Any,
    forwarder_user_id: str,
    outer_message_id: str,
    group_id: str = "",
    response_deadline: float | None = None,
    http_client: Any = None,
    max_nodes: int = 20,
    max_images: int = 8,
) -> ForwardContext:
    """Expand forward nodes as untrusted text and controlled visual evidence.

    The outer sender owns every occurrence.  Nested-node sender fields are
    display-only untrusted evidence and must never become chat identity or Bot
    provenance.  Only public HTTP(S) image refs are fetched; local paths,
    opaque OneBot ids, audio and video remain explicitly unknown.
    """
    try:
        segments = list(getattr(event, "message", None) or [])
    except TypeError:
        segments = []
    forward_segments = [seg for seg in segments if str(getattr(seg, "type", "") or "").lower() == "forward"]
    if not forward_segments:
        return ForwardContext("", ())

    rendered: list[str] = []
    media: list[TurnMediaRef] = []
    reached_limit = False
    image_count = 0
    media_attempts = 0
    node_count = 0
    if len(forward_segments) > max_nodes:
        reached_limit = True
    for outer_index, segment in enumerate(forward_segments[:max_nodes]):
        data = getattr(segment, "data", {}) or {}
        forward_id = str(data.get("id", "") or "").strip() if isinstance(data, dict) else ""
        if not forward_id:
            continue
        try:
            remaining = _remaining_seconds(response_deadline)
            if remaining is not None and remaining <= 0:
                rendered.append("[不可信转发记录：内容不可用]")
                break
            request = (
                bot.get_forward_msg(message_id=forward_id)
                if hasattr(bot, "get_forward_msg")
                else bot.call_api("get_forward_msg", message_id=forward_id)
            )
            payload = await asyncio.wait_for(request, timeout=min(10.0, remaining) if remaining is not None else 10.0)
        except asyncio.CancelledError:
            raise
        except Exception:
            rendered.append("[不可信转发记录：内容不可用]")
            continue
        raw_nodes = payload.get("messages", []) if isinstance(payload, dict) else []
        if not isinstance(raw_nodes, list):
            raw_nodes = [payload] if isinstance(payload, dict) else []
        if len(raw_nodes) > max_nodes:
            reached_limit = True
        bundle = normalize_merged_forward(raw_nodes[:max_nodes])
        for node in _nodes_in_order(bundle.nodes):
            if node_count >= max_nodes:
                reached_limit = True
                break
            node_count += 1
            sender = getattr(node, "sender", None)
            author = str(getattr(sender, "nickname", "") or getattr(sender, "user_id", "") or "未知发送者").strip()
            node_ref = str(getattr(node, "node_id", "") or f"{outer_index}:{node_count}").strip()
            text = str(getattr(node, "text", "") or "").strip()
            node_media = tuple(getattr(node, "media_refs", ()) or ())
            if node_media:
                # A forwarded segment is never visual evidence merely because
                # it carries a media-looking field.  The marker deliberately
                # remains unknown until the individual public image has been
                # safely downloaded and projected below.  This also covers
                # opaque audio/video and local-file attempts.
                text = (text + " " if text else "") + "[转发媒体：尚未理解]"
            if text:
                rendered.append(f"[不可信转发记录 #{node_count} 来源={node_ref} 原节点作者（不可信）={author}] {text}")
            for node_media_index, media_ref in enumerate(node_media):
                # Every media candidate consumes one bounded attempt even when
                # it is a local path, opaque token, unsupported kind, failed
                # download, or invalid image.  An attacker cannot turn a
                # stream of failures into unlimited public fetches.
                if media_attempts >= max_images:
                    reached_limit = True
                    break
                media_attempts += 1
                if str(getattr(media_ref, "kind", "") or "").lower() != "image":
                    continue
                remote = _public_image_url(getattr(media_ref, "ref", ""))
                if not remote:
                    continue
                try:
                    data_url = await _download_forward_image(
                        remote,
                        response_deadline=response_deadline,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception:
                    data_url = ""
                if not data_url:
                    continue
                image_count += 1
                try:
                    payload = data_url.split(",", 1)[1].encode("ascii")
                except (IndexError, UnicodeEncodeError):
                    continue
                # Hash frozen payload bytes indirectly only after validation;
                # this value never records the source URL.
                try:
                    digest = hashlib.sha256(base64.b64decode(payload, validate=True)).hexdigest()
                except (ValueError, UnicodeEncodeError):
                    continue
                # The occurrence identity is structural rather than URL
                # derived: the outer triggering message, forward segment,
                # normalized nested-node path and media position survive even
                # when a user forwards the exact same image twice.
                path = "/".join(str(part) for part in getattr(node, "source_path", ()) or ()) or node_ref
                occurrence_id = f"{outer_message_id}:{outer_index}:{path}:{node_media_index}"
                media.append(TurnMediaRef(
                    media_id=f"forward:{occurrence_id}:{digest[:16]}",
                    ref=data_url,
                    origin="forward",
                    owner_user_id=str(forwarder_user_id or ""),
                    message_id=str(outer_message_id or ""),
                    kind="image",
                    content_hash=digest,
                    file_id=f"forward:{occurrence_id}",
                    group_id=str(group_id or ""),
                    resolution_code="forward_image_safe_download",
                    reference_role="current",
                ))
                rendered.append(
                    f"[转发媒体视觉输入 来源={node_ref} 可信归属=本次转发者:{forwarder_user_id or '当前转发者'} "
                    f"media_id=forward:{occurrence_id}:{digest[:16]}：已安全提供给视觉模型，待模型判断]"
                )
        if bundle.truncated:
            reached_limit = True
    if reached_limit:
        rendered.append("[不可信转发记录：内容已按安全上限截断]")
    return ForwardContext("\n".join(rendered), tuple(media), reached_limit)


__all__ = ["ForwardContext", "build_forward_context", "merge_forward_media"]
