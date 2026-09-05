from __future__ import annotations

import asyncio
import base64
import hashlib
import re
import shutil
import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Iterable, Literal

from .media_refs import (
    is_supported_video_filename,
    normalize_audio_ref,
    normalize_video_ref,
)
from .message_relations import extract_reply_message_id
from .paths import get_data_dir
from .safe_media_download import SafeMediaDownloadError, download_public_media_to_path


MediaOrigin = Literal["current", "quoted", "batch", "antecedent"]
MediaReferenceRole = Literal["current", "selected_referent", "address_only", "background"]

_ALLOWED_ORIGINS = {"current", "quoted", "batch", "antecedent"}
_ALLOWED_REFERENCE_ROLES = {"current", "selected_referent", "address_only", "background"}
_ALLOWED_KINDS = {"image", "sticker", "gif", "mface", "video", "audio", "unknown"}
_MEDIA_RESOLUTION_CODES = {
    "onebot_get_file_url",
    "onebot_get_file_local",
    "onebot_audio_get_record_local",
    "onebot_audio_safe_download",
    "onebot_video_safe_download",
    "onebot_private_file_url",
    "onebot_group_file_url",
    "onebot_video_resolve_failed",
    "onebot_audio_download_failed",
    "onebot_video_download_failed",
    "onebot_media_too_large",
    "onebot_media_mime_rejected",
    "onebot_media_budget_exhausted",
    "onebot_media_download_timeout",
}
_VIDEO_DOWNLOAD_MIMES = {
    "video/mp4": ".mp4",
    "video/quicktime": ".mov",
    "video/x-m4v": ".m4v",
    "video/webm": ".webm",
    "video/x-matroska": ".mkv",
    "video/x-msvideo": ".avi",
}
_AUDIO_DOWNLOAD_MIMES = {
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3",
    "audio/mp4": ".m4a",
    "audio/aac": ".aac",
    "audio/ogg": ".ogg",
    "audio/opus": ".opus",
    "audio/flac": ".flac",
    "audio/x-flac": ".flac",
    "audio/amr": ".amr",
}
_DATA_URL_RE = re.compile(r"data:[^\s,;]+;base64,[A-Za-z0-9+/=\r\n]+", re.IGNORECASE)
_SUMMARY_MARKER_RE = re.compile(
    r"\[(?:图片视觉描述|表情包语义|动态表情语义|媒体语义)（系统注入[^）]*）[：:]\s*(.*?)\]",
    re.DOTALL,
)
_LEASE_STATE_KEY = "_personification_turn_media_lease"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _segment_type(segment: Any) -> str:
    if isinstance(segment, dict):
        return _text(segment.get("type")).lower()
    return _text(getattr(segment, "type", "")).lower()


def _segment_data(segment: Any) -> dict[str, Any]:
    if isinstance(segment, dict):
        data = segment.get("data", {})
    else:
        data = getattr(segment, "data", {})
    return dict(data) if isinstance(data, dict) else {}


def _message_segments(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, dict) and "message" in value:
        value = value.get("message")
    if isinstance(value, (str, bytes, bytearray, dict)):
        return []
    try:
        return list(value)
    except TypeError:
        return []


def _object_value(value: Any, name: str) -> Any:
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)


def _sender_user_id(value: Any) -> str:
    sender = _object_value(value, "sender")
    sender_id = _object_value(sender, "user_id") if sender is not None else None
    return _text(sender_id or _object_value(value, "user_id"))


def _message_id(value: Any) -> str:
    return _text(
        _object_value(value, "message_id")
        or _object_value(value, "id")
        or _object_value(value, "messageId")
    )


def _file_id(data: dict[str, Any]) -> str:
    return _text(
        data.get("file_id")
        or data.get("fileId")
        or data.get("file")
        or data.get("id")
        or data.get("emoji_id")
    )


def _media_ref(data: dict[str, Any]) -> str:
    return _text(
        data.get("url")
        or data.get("src")
        or data.get("path")
        or data.get("file")
    )


def _file_name(data: dict[str, Any]) -> str:
    return _text(
        data.get("name")
        or data.get("file_name")
        or data.get("fileName")
        or data.get("path")
        or data.get("url")
        or data.get("file")
    )


def _content_hash(ref: str, file_id: str) -> str:
    raw_ref = _text(ref)
    if raw_ref.lower().startswith("data:") and "," in raw_ref:
        encoded = raw_ref.split(",", 1)[1]
        try:
            payload = base64.b64decode(encoded, validate=False)
        except Exception:
            payload = encoded.encode("utf-8", errors="ignore")
        return hashlib.sha256(payload).hexdigest()
    seed = raw_ref or _text(file_id)
    return hashlib.sha256(seed.encode("utf-8")).hexdigest() if seed else ""


def _kind_for_segment(segment_type: str, data: dict[str, Any]) -> str:
    if segment_type == "mface":
        return "mface"
    if segment_type == "gif":
        return "gif"
    if segment_type == "video":
        return "video"
    if segment_type == "record":
        return "audio"
    if segment_type == "file" and is_supported_video_filename(_file_name(data)):
        return "video"
    if segment_type != "image":
        return "unknown"
    raw_sub_type = data.get("sub_type", data.get("subType", 0))
    try:
        return "sticker" if int(raw_sub_type or 0) == 1 else "image"
    except (TypeError, ValueError):
        return "image"


def normalize_safe_visual_summary(value: Any, *, limit: int = 500) -> str:
    text = _text(value)
    if not text:
        return ""
    match = _SUMMARY_MARKER_RE.search(text)
    if match:
        text = _text(match.group(1))
    text = _DATA_URL_RE.sub("[media-data-omitted]", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[: max(0, int(limit))]


@dataclass(frozen=True)
class TurnMediaRef:
    media_id: str
    ref: str
    origin: MediaOrigin
    owner_user_id: str
    message_id: str
    kind: str
    content_hash: str = ""
    file_id: str = ""
    safe_summary: str = ""
    confidence: float = 0.0
    summary_scope: str = ""
    group_id: str = ""
    resolution_code: str = ""
    reference_role: MediaReferenceRole = "current"

    def to_dict(self) -> dict[str, Any]:
        ref = _text(self.ref)
        return {
            "media_id": _text(self.media_id),
            "ref": "" if ref.lower().startswith("data:") else ref,
            "origin": self.origin if self.origin in _ALLOWED_ORIGINS else "current",
            "owner_user_id": _text(self.owner_user_id),
            "message_id": _text(self.message_id),
            "kind": self.kind if self.kind in _ALLOWED_KINDS else "unknown",
            "content_hash": _text(self.content_hash),
            "file_id": _text(self.file_id),
            "group_id": _text(self.group_id),
            "resolution_code": _text(self.resolution_code),
            "reference_role": (
                self.reference_role
                if self.reference_role in _ALLOWED_REFERENCE_ROLES
                else "current"
            ),
            "safe_summary": normalize_safe_visual_summary(self.safe_summary),
            "confidence": max(0.0, min(1.0, float(self.confidence or 0.0))),
            "summary_scope": _text(self.summary_scope),
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> TurnMediaRef | None:
        if not isinstance(value, dict):
            return None
        origin = _text(value.get("origin")).lower()
        if origin not in _ALLOWED_ORIGINS:
            origin = "current"
        reference_role = _text(value.get("reference_role")).lower()
        if reference_role not in _ALLOWED_REFERENCE_ROLES:
            reference_role = (
                "current" if origin == "current" else "address_only" if origin == "quoted" else "background"
            )
        kind = _text(value.get("kind")).lower()
        if kind not in _ALLOWED_KINDS:
            kind = "unknown"
        try:
            confidence = max(0.0, min(1.0, float(value.get("confidence", 0.0) or 0.0)))
        except (TypeError, ValueError):
            confidence = 0.0
        file_id = _text(value.get("file_id"))
        ref = _text(value.get("ref"))
        content_hash = _text(value.get("content_hash")) or _content_hash(ref, file_id)
        media_id = _text(value.get("media_id"))
        if not media_id:
            media_id = _build_media_id(
                owner_user_id=_text(value.get("owner_user_id")),
                message_id=_text(value.get("message_id")),
                origin=origin,
                kind=kind,
                file_id=file_id,
                content_hash=content_hash,
            )
        return cls(
            media_id=media_id,
            ref=ref,
            origin=origin,  # type: ignore[arg-type]
            owner_user_id=_text(value.get("owner_user_id")),
            message_id=_text(value.get("message_id")),
            kind=kind,
            content_hash=content_hash,
            file_id=file_id,
            group_id=_text(value.get("group_id")),
            resolution_code=_text(value.get("resolution_code")),
            reference_role=reference_role,  # type: ignore[arg-type]
            safe_summary=normalize_safe_visual_summary(value.get("safe_summary")),
            confidence=confidence,
            summary_scope=_text(value.get("summary_scope")),
        )


@dataclass(frozen=True)
class VisualMediaProjection:
    """Process-local visual input chosen from explicit turn-media provenance.

    ``media`` deliberately keeps every selected occurrence (and therefore its
    owner/message/role).  ``transport_refs`` is merely the de-duplicated list
    of payloads handed to a model.  Do not serialize this object: it can hold
    temporary data URLs and OneBot transport URLs.
    """

    media: tuple[TurnMediaRef, ...]
    transport_refs: tuple[str, ...]
    # ``media_id -> transport`` bindings are intentionally explicit.  They
    # let per-media vision summaries retain ownership even when several
    # occurrences share a data URL after download/materialization.
    occurrence_transport_refs: tuple[tuple[str, str], ...]


_VISUAL_KINDS = frozenset({"image", "sticker", "gif", "mface"})
_VISUAL_INPUT_ROLES = frozenset({"current", "selected_referent"})


def project_visual_media_inputs(
    values: Iterable[TurnMediaRef | dict[str, Any]] | None,
    *,
    image_refs: Iterable[Any] | None = None,
    transport_aliases: dict[str, str] | None = None,
) -> VisualMediaProjection:
    """Select visual media and de-duplicate only its process-local transport.

    A OneBot media occurrence has stable provenance in ``media_id`` while a
    downloader may materialize it as a data URL.  ``transport_aliases`` is an
    explicit, process-local ``original-ref -> materialized-ref`` binding made
    at conversion time.  It is intentionally not inferred from URL text or
    list position.  Selected occurrences remain separate even if they share a
    transport payload; only the provider input is de-duplicated.
    """

    aliases = {
        _text(original): _text(materialized)
        for original, materialized in dict(transport_aliases or {}).items()
        if _text(original) and _text(materialized)
    }
    originals_by_materialized: dict[str, set[str]] = {}
    for original, materialized in aliases.items():
        originals_by_materialized.setdefault(materialized, set()).add(original)
    all_visual_media = tuple(
        item for item in coerce_turn_media(values) if item.kind in _VISUAL_KINDS
    )
    media = tuple(
        item
        for item in all_visual_media
        if item.kind in _VISUAL_KINDS and item.reference_role in _VISUAL_INPUT_ROLES
    )
    allowed_original_refs = {_text(item.ref) for item in media if _text(item.ref)}
    has_visual_manifest = bool(all_visual_media)
    transport_refs: list[str] = []
    occurrence_transport_refs: list[tuple[str, str]] = []
    seen_transport: set[str] = set()

    def _append_transport(value: Any) -> None:
        ref = _text(value)
        if not ref:
            return
        # Values are compared only after an explicit alias lookup.  Never use
        # URL syntax as evidence that two media occurrences are the same.
        transport = aliases.get(ref, ref)
        # At this point equality means the exact payload transport selected by
        # an explicit alias (or an unchanged literal transport), never a URL
        # similarity heuristic.  Occurrence provenance remains in ``media``.
        if transport in seen_transport:
            return
        seen_transport.add(transport)
        transport_refs.append(transport)

    # Materialized current input is preferred over its raw OneBot ref.  This
    # prevents an original URL and its data URL from being sent twice while
    # keeping selected historical refs available when no materialization exists.
    for value in image_refs or []:
        raw_transport = _text(value)
        original_refs = originals_by_materialized.get(raw_transport, {raw_transport})
        if has_visual_manifest and not any(ref in allowed_original_refs for ref in original_refs):
            # A quoted/history transport may have been materialized while the
            # semantic resolver deliberately left that occurrence address-only.
            continue
        _append_transport(raw_transport)
    for item in media:
        raw_ref = _text(item.ref)
        transport = aliases.get(raw_ref, raw_ref)
        if item.media_id and transport:
            occurrence_transport_refs.append((item.media_id, transport))
        _append_transport(raw_ref)
    return VisualMediaProjection(
        media=media,
        transport_refs=tuple(transport_refs),
        occurrence_transport_refs=tuple(occurrence_transport_refs),
    )


@dataclass
class ResolvedTurnMediaLease:
    """Process-local media refs plus an idempotent cleanup boundary.

    ``refs`` may contain controlled absolute paths and therefore must never be
    persisted as Trace or history data.  ``summary`` is deliberately limited
    to low-cardinality enums, counts, and byte sizes.
    """

    refs: list[TurnMediaRef]
    summary: dict[str, Any]
    runtime_dir: Path | None = None
    _cleaned: bool = False

    def cleanup(self) -> None:
        if self._cleaned:
            return
        self._cleaned = True
        runtime_dir = self.runtime_dir
        self.runtime_dir = None
        if runtime_dir is not None:
            shutil.rmtree(runtime_dir, ignore_errors=True)


def register_turn_media_lease(
    holder: dict[str, Any],
    lease: ResolvedTurnMediaLease,
) -> None:
    previous = holder.get(_LEASE_STATE_KEY)
    if previous is not lease and isinstance(previous, ResolvedTurnMediaLease):
        previous.cleanup()
    holder[_LEASE_STATE_KEY] = lease


def cleanup_turn_media_lease(holder: dict[str, Any] | None) -> None:
    if not isinstance(holder, dict):
        return
    lease = holder.pop(_LEASE_STATE_KEY, None)
    if isinstance(lease, ResolvedTurnMediaLease):
        lease.cleanup()


@dataclass(frozen=True)
class MediaAvailability:
    """Bounded structural facts about media attached to the current turn.

    The object deliberately contains counts only.  It is safe to give to the
    semantic/planning model and avoids making chat semantics depend on file
    names, URLs, local paths, or user-authored descriptions.
    """

    image_count: int = 0
    video_count: int = 0
    audio_count: int = 0
    usable_image_count: int = 0
    usable_video_count: int = 0
    usable_audio_count: int = 0
    media_only_turn: bool = False

    @property
    def has_media(self) -> bool:
        return bool(self.image_count or self.video_count or self.audio_count)

    @property
    def has_usable_media(self) -> bool:
        return bool(
            self.usable_image_count
            or self.usable_video_count
            or self.usable_audio_count
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "image_count": max(0, int(self.image_count)),
            "video_count": max(0, int(self.video_count)),
            "audio_count": max(0, int(self.audio_count)),
            "usable_image_count": max(0, int(self.usable_image_count)),
            "usable_video_count": max(0, int(self.usable_video_count)),
            "usable_audio_count": max(0, int(self.usable_audio_count)),
            "media_only_turn": bool(self.media_only_turn),
        }


def _build_media_id(
    *,
    owner_user_id: str,
    message_id: str,
    origin: str,
    kind: str,
    file_id: str,
    content_hash: str,
) -> str:
    seed = "\0".join(
        (owner_user_id, message_id, origin, kind, file_id, content_hash)
    )
    return f"media_{hashlib.sha256(seed.encode('utf-8')).hexdigest()[:20]}"


def extract_media_from_message(
    message: Any,
    *,
    origin: MediaOrigin,
    owner_user_id: str,
    message_id: str,
    group_id: str = "",
) -> list[TurnMediaRef]:
    refs: list[TurnMediaRef] = []
    for segment in _message_segments(message):
        segment_type = _segment_type(segment)
        if segment_type not in {"image", "mface", "gif", "video", "record", "file"}:
            continue
        data = _segment_data(segment)
        if segment_type == "file" and not is_supported_video_filename(_file_name(data)):
            continue
        ref = _media_ref(data)
        file_id = _file_id(data)
        if not ref and not file_id:
            continue
        kind = _kind_for_segment(segment_type, data)
        content_hash = _content_hash(ref, file_id)
        refs.append(
            TurnMediaRef(
                media_id=_build_media_id(
                    owner_user_id=_text(owner_user_id),
                    message_id=_text(message_id),
                    origin=origin,
                    kind=kind,
                    file_id=file_id,
                    content_hash=content_hash,
                ),
                ref=ref,
                origin=origin,
                owner_user_id=_text(owner_user_id),
                message_id=_text(message_id),
                kind=kind,
                content_hash=content_hash,
                file_id=file_id,
                group_id=_text(group_id),
                reference_role="current" if origin == "current" else "address_only" if origin == "quoted" else "background",
            )
        )
    return refs


def extract_turn_media_from_event(
    event: Any,
    *,
    current_origin: MediaOrigin = "current",
    include_quoted: bool = True,
) -> list[TurnMediaRef]:
    refs = extract_media_from_message(
        _object_value(event, "message"),
        origin=current_origin,
        owner_user_id=_sender_user_id(event),
        message_id=_message_id(event),
        group_id=_text(_object_value(event, "group_id")),
    )
    if not include_quoted:
        return refs
    quoted = _object_value(event, "reply") or _object_value(event, "quoted") or _object_value(event, "quote")
    if quoted:
        refs.extend(
            extract_media_from_message(
                _object_value(quoted, "message"),
                origin="quoted",
                owner_user_id=_sender_user_id(quoted),
                message_id=_message_id(quoted),
                group_id=_text(
                    _object_value(quoted, "group_id")
                    or _object_value(event, "group_id")
                ),
            )
        )
    return refs


def _onebot_message_payload(payload: Any) -> Any:
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, dict) and data.get("message") is not None:
            return data
    return payload


async def resolve_onebot_quoted_media_refs(
    event: Any,
    bot: Any,
    *,
    timeout_seconds: float = 15.0,
) -> list[TurnMediaRef]:
    """Hydrate quoted media when the adapter only exposes a reply message id.

    NapCat may omit file/video segments from ``event.reply.message`` even though
    the visible QQ quote points at that file.  Fetching the quoted message keeps
    the exact message/owner provenance and avoids guessing from chat text.
    """

    refs = extract_turn_media_from_event(event, current_origin="current")
    if any(item.origin == "quoted" for item in refs):
        return refs
    quoted = (
        _object_value(event, "reply")
        or _object_value(event, "quoted")
        or _object_value(event, "quote")
    )
    quoted_segments = _message_segments(_object_value(quoted, "message")) if quoted else []
    if any(
        _segment_type(segment) == "text"
        and _text(_segment_data(segment).get("text"))
        for segment in quoted_segments
    ):
        return refs
    reply_message_id = extract_reply_message_id(event)
    if not reply_message_id:
        return refs
    request_message_id: str | int = reply_message_id
    if reply_message_id.isdigit():
        try:
            request_message_id = int(reply_message_id)
        except (TypeError, ValueError, OverflowError):
            request_message_id = reply_message_id
    try:
        get_msg = getattr(bot, "get_msg", None)
        if callable(get_msg):
            request = get_msg(message_id=request_message_id)
        else:
            call_api = getattr(bot, "call_api", None)
            if not callable(call_api):
                return refs
            request = call_api("get_msg", message_id=request_message_id)
        payload = _onebot_message_payload(
            await asyncio.wait_for(
                request,
                timeout=max(1.0, float(timeout_seconds or 15.0)),
            )
        )
    except Exception:
        return refs
    quoted_refs = extract_media_from_message(
        _object_value(payload, "message"),
        origin="quoted",
        owner_user_id=_sender_user_id(payload),
        message_id=_message_id(payload) or reply_message_id,
        group_id=_text(
            _object_value(payload, "group_id")
            or _object_value(event, "group_id")
        ),
    )
    return coerce_turn_media([*refs, *quoted_refs])


def serialize_turn_media(values: Iterable[TurnMediaRef | dict[str, Any]]) -> list[dict[str, Any]]:
    serialized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for value in values or []:
        item = value if isinstance(value, TurnMediaRef) else TurnMediaRef.from_dict(value)
        if item is None or item.media_id in seen:
            continue
        seen.add(item.media_id)
        serialized.append(item.to_dict())
    return serialized


def coerce_turn_media(values: Iterable[TurnMediaRef | dict[str, Any]] | None) -> list[TurnMediaRef]:
    refs: list[TurnMediaRef] = []
    seen: set[str] = set()
    for value in values or []:
        item = value if isinstance(value, TurnMediaRef) else TurnMediaRef.from_dict(value)
        if item is None or item.media_id in seen:
            continue
        seen.add(item.media_id)
        refs.append(item)
    return refs


def coerce_media_availability(
    value: MediaAvailability | dict[str, Any] | None,
    *,
    has_images: bool = False,
) -> MediaAvailability:
    if isinstance(value, MediaAvailability):
        base = value
    elif isinstance(value, dict):
        def _count(name: str) -> int:
            try:
                return max(0, int(value.get(name, 0) or 0))
            except (TypeError, ValueError):
                return 0

        base = MediaAvailability(
            image_count=_count("image_count"),
            video_count=_count("video_count"),
            audio_count=_count("audio_count"),
            usable_image_count=_count("usable_image_count"),
            usable_video_count=_count("usable_video_count"),
            usable_audio_count=_count("usable_audio_count"),
            media_only_turn=bool(value.get("media_only_turn", False)),
        )
    else:
        base = MediaAvailability()
    if not has_images or base.image_count > 0:
        return base
    return replace(
        base,
        image_count=1,
        usable_image_count=max(1, base.usable_image_count),
    )


def build_media_availability(
    values: Iterable[TurnMediaRef | dict[str, Any]] | None,
    *,
    image_refs: Iterable[Any] | None = None,
    text: Any = "",
) -> MediaAvailability:
    refs = coerce_turn_media(values)
    image_keys: set[str] = set()
    usable_image_keys: set[str] = set()
    video_refs: list[TurnMediaRef] = []
    audio_refs: list[TurnMediaRef] = []
    def _potentially_usable(item: TurnMediaRef) -> bool:
        resolution_code = _text(item.resolution_code)
        if resolution_code in {
            "onebot_video_resolve_failed",
            "onebot_audio_download_failed",
            "onebot_video_download_failed",
            "onebot_media_too_large",
            "onebot_media_mime_rejected",
            "onebot_media_budget_exhausted",
            "onebot_media_download_timeout",
        } or resolution_code.endswith("_failed"):
            return False
        return bool(_text(item.ref) or _text(item.file_id))

    for item in refs:
        if item.kind in {"image", "sticker", "gif", "mface"}:
            key = item.media_id or item.content_hash or item.ref or item.file_id
            if key:
                image_keys.add(key)
                if _potentially_usable(item):
                    usable_image_keys.add(key)
        elif item.kind == "video":
            video_refs.append(item)
        elif item.kind == "audio":
            audio_refs.append(item)
    # ``image_refs`` are transports, not independent occurrences.  When a
    # visual manifest exists, its occurrence identities own the count even if
    # a downloader materialized the same bytes as a data URL.  Only legacy
    # callers with no visual manifest may contribute transport-only images.
    has_visual_manifest = bool(image_keys)
    for raw in ([] if has_visual_manifest else (image_refs or [])):
        value = _text(raw)
        if value:
            key = hashlib.sha256(value.encode("utf-8", errors="ignore")).hexdigest()
            image_keys.add(key)
            usable_image_keys.add(key)

    normalized_text = re.sub(r"\s+", " ", _text(text)).strip()
    return MediaAvailability(
        image_count=len(image_keys),
        video_count=len(video_refs),
        audio_count=len(audio_refs),
        usable_image_count=len(usable_image_keys),
        usable_video_count=sum(_potentially_usable(item) for item in video_refs),
        usable_audio_count=sum(_potentially_usable(item) for item in audio_refs),
        media_only_turn=bool((image_keys or video_refs or audio_refs) and not normalized_text),
    )


def media_from_batched_events(values: Iterable[dict[str, Any]] | None) -> list[TurnMediaRef]:
    refs: list[TurnMediaRef] = []
    for value in values or []:
        if isinstance(value, dict):
            refs.extend(coerce_turn_media(value.get("media") or []))
    return coerce_turn_media(refs)


async def resolve_onebot_audio_refs(
    values: Iterable[TurnMediaRef | dict[str, Any]] | None,
    bot: Any,
) -> list[TurnMediaRef]:
    """Resolve opaque OneBot record tokens only when a reply needs media tools."""

    refs = coerce_turn_media(values)
    resolved: list[TurnMediaRef] = []
    for item in refs:
        if item.kind != "audio":
            resolved.append(item)
            continue
        raw_ref = _text(item.ref)
        token = _text(item.file_id or raw_ref)
        if not token:
            resolved.append(item)
            continue
        try:
            local, remote, code = await _onebot_media_candidates(
                item,
                bot,
                response_deadline=None,
                timeout_seconds=180.0,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            local, remote, code = "", "", ""
        candidate = local or remote
        resolved.append(
            replace(item, ref=candidate, resolution_code=code)
            if candidate
            else item
        )
    return resolved


def _onebot_payload_value(payload: Any, *names: str) -> str:
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, dict):
            nested = _onebot_payload_value(data, *names)
            if nested:
                return nested
        for name in names:
            candidate = _text(payload.get(name))
            if candidate:
                return candidate
        return ""
    for name in names:
        candidate = _text(getattr(payload, name, None))
        if candidate:
            return candidate
    return _text(payload) if isinstance(payload, (str, Path)) else ""


def _normalize_local_media_candidate(value: Any, *, kind: str) -> str:
    raw = _text(value)
    if not raw or raw.startswith(("http://", "https://")):
        return ""
    normalized, problem = (
        normalize_audio_ref(raw) if kind == "audio" else normalize_video_ref(raw)
    )
    if not normalized or problem or normalized.startswith(("http://", "https://")):
        return ""
    return normalized


def _normalize_remote_media_candidate(value: Any) -> str:
    raw = _text(value)
    return raw if raw.startswith("https://") else ""


def _remaining_seconds(response_deadline: float | None) -> float | None:
    if not isinstance(response_deadline, (int, float)):
        return None
    return float(response_deadline) - asyncio.get_running_loop().time()


def _bounded_media_timeout(
    *,
    configured_timeout: float,
    response_deadline: float | None,
) -> float:
    timeout = max(0.0, float(configured_timeout or 0.0))
    remaining = _remaining_seconds(response_deadline)
    if remaining is not None:
        timeout = min(timeout, max(0.0, remaining))
    return timeout


async def _onebot_media_candidates(
    item: TurnMediaRef,
    bot: Any,
    *,
    response_deadline: float | None,
    timeout_seconds: float,
) -> tuple[str, str, str]:
    """Return ``(local_path, remote_url, materialization_code)`` safely."""

    raw_ref = _text(item.ref)
    token = _text(item.file_id or ("" if raw_ref.startswith("https://") else raw_ref))
    fallback_url = _normalize_remote_media_candidate(raw_ref)
    existing_local = _normalize_local_media_candidate(raw_ref, kind=item.kind)
    if existing_local and not token:
        return existing_local, "", "existing_local"
    if not token:
        return "", fallback_url, ""

    loop = asyncio.get_running_loop()
    operation_deadline = loop.time() + max(0.0, float(timeout_seconds or 0.0))
    if isinstance(response_deadline, (int, float)):
        operation_deadline = min(operation_deadline, float(response_deadline))
    timeout = _bounded_media_timeout(
        configured_timeout=timeout_seconds,
        response_deadline=operation_deadline,
    )
    if timeout <= 0:
        return "", "", "onebot_media_budget_exhausted"

    if item.kind == "audio":
        try:
            payload = await _call_onebot_file_api(
                bot,
                "get_record",
                timeout_seconds=timeout,
                file=token,
                out_format="wav",
            )
            local = _normalize_local_media_candidate(
                _onebot_payload_value(payload, "file", "path"),
                kind="audio",
            )
            if local:
                return local, "", "onebot_audio_get_record_local"
            payload_url = _normalize_remote_media_candidate(
                _onebot_payload_value(payload, "url", "file")
            )
            if existing_local:
                return existing_local, "", "existing_local"
            return "", payload_url or fallback_url, ""
        except asyncio.CancelledError:
            raise
        except Exception:
            if existing_local:
                return existing_local, "", "existing_local"
            return "", fallback_url, ""

    try:
        payload = await _call_onebot_file_api(
            bot,
            "get_file",
            timeout_seconds=timeout,
            file=token,
        )
        # A NapCat path may belong to another host.  It is accepted only when
        # it resolves to a readable local file in this process.
        local = _normalize_local_media_candidate(
            _onebot_payload_value(payload, "file", "path"),
            kind="video",
        )
        if local:
            return local, "", "onebot_get_file_local"
        payload_url = _normalize_remote_media_candidate(
            _onebot_payload_value(payload, "url")
        )
        if payload_url:
            return "", payload_url, ""
    except asyncio.CancelledError:
        raise
    except Exception:
        if existing_local:
            return existing_local, "", "existing_local"

    fallback_api = "get_group_file_url" if _text(item.group_id) else "get_private_file_url"
    fallback_kwargs: dict[str, Any] = {"file_id": token}
    if fallback_api == "get_group_file_url":
        fallback_kwargs["group_id"] = item.group_id
    timeout = _bounded_media_timeout(
        configured_timeout=timeout_seconds,
        response_deadline=operation_deadline,
    )
    if timeout <= 0:
        return "", "", "onebot_media_budget_exhausted"
    try:
        payload = await _call_onebot_file_api(
            bot,
            fallback_api,
            timeout_seconds=timeout,
            **fallback_kwargs,
        )
        payload_url = _normalize_remote_media_candidate(
            _onebot_payload_value(payload, "url")
        )
        return "", payload_url or fallback_url, ""
    except asyncio.CancelledError:
        raise
    except Exception:
        if existing_local:
            return existing_local, "", "existing_local"
        return "", fallback_url, ""


def _download_failure_code(kind: str, exc: BaseException) -> str:
    message = str(exc or "").lower()
    if "too large" in message or "size limit" in message or "exceeded" in message:
        return "onebot_media_too_large"
    if "mime" in message or "media type" in message:
        return "onebot_media_mime_rejected"
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)) or "timeout" in message:
        return "onebot_media_download_timeout"
    return f"onebot_{kind}_download_failed"


async def materialize_onebot_media_refs(
    values: Iterable[TurnMediaRef | dict[str, Any]] | None,
    bot: Any,
    plugin_config: Any,
    response_deadline: float | None,
    video_timeout_seconds: float,
) -> ResolvedTurnMediaLease:
    """Turn transient OneBot audio/video references into controlled files."""

    refs = coerce_turn_media(values)
    resolved: list[TurnMediaRef] = []
    diagnostics: list[dict[str, Any]] = []
    runtime_dir: Path | None = None

    def _ensure_runtime_dir() -> Path:
        nonlocal runtime_dir
        if runtime_dir is None:
            runtime_dir = (
                get_data_dir(plugin_config)
                / "runtime-media"
                / uuid.uuid4().hex
            )
            runtime_dir.mkdir(parents=True, exist_ok=False)
        return runtime_dir

    max_bytes = max(
        1,
        int(getattr(plugin_config, "personification_video_max_bytes", 268435456) or 268435456),
    )
    download_timeout = max(
        0.0,
        float(
            getattr(plugin_config, "personification_video_download_timeout", 90.0)
            or 90.0
        ),
    )
    lease = ResolvedTurnMediaLease(refs=resolved, summary={}, runtime_dir=None)
    try:
        for index, item in enumerate(refs):
            if item.kind not in {"video", "audio"}:
                resolved.append(item)
                continue
            local, remote, code = await _onebot_media_candidates(
                item,
                bot,
                response_deadline=response_deadline,
                timeout_seconds=video_timeout_seconds,
            )
            if local:
                size = None
                try:
                    size = Path(local).stat().st_size
                except OSError:
                    pass
                resolved.append(replace(item, ref=local, resolution_code=code))
                from_onebot = code in {
                    "onebot_get_file_local",
                    "onebot_audio_get_record_local",
                }
                diagnostics.append(
                    {
                        "kind": item.kind,
                        "source_kind": "onebot_local" if from_onebot else "local",
                        "materialization": "onebot_get_file" if from_onebot else "existing_local",
                        "provider_transport": "local_file",
                        "size_bytes": size,
                        "diagnostic_code": code or "media_local_ready",
                    }
                )
                continue
            if code == "onebot_media_budget_exhausted":
                resolved.append(replace(item, ref="", resolution_code=code))
                diagnostics.append(
                    {
                        "kind": item.kind,
                        "source_kind": "onebot_remote",
                        "materialization": "failed",
                        "provider_transport": "none",
                        "size_bytes": None,
                        "diagnostic_code": code,
                    }
                )
                continue
            if not remote:
                failure_code = f"onebot_{item.kind}_download_failed"
                resolved.append(replace(item, ref="", resolution_code=failure_code))
                diagnostics.append(
                    {
                        "kind": item.kind,
                        "source_kind": "onebot_remote",
                        "materialization": "failed",
                        "provider_transport": "none",
                        "size_bytes": None,
                        "diagnostic_code": failure_code,
                    }
                )
                continue

            timeout = _bounded_media_timeout(
                configured_timeout=min(download_timeout, float(video_timeout_seconds or download_timeout)),
                response_deadline=response_deadline,
            )
            if timeout <= 0:
                failure_code = "onebot_media_budget_exhausted"
                resolved.append(replace(item, ref="", resolution_code=failure_code))
                diagnostics.append(
                    {
                        "kind": item.kind,
                        "source_kind": "onebot_remote",
                        "materialization": "failed",
                        "provider_transport": "none",
                        "size_bytes": None,
                        "diagnostic_code": failure_code,
                    }
                )
                continue

            mime_map = _VIDEO_DOWNLOAD_MIMES if item.kind == "video" else _AUDIO_DOWNLOAD_MIMES
            destination = _ensure_runtime_dir() / f"media-{index:02d}.part"
            try:
                downloaded = await asyncio.wait_for(
                    download_public_media_to_path(
                        remote,
                        destination,
                        timeout=timeout,
                        max_bytes=max_bytes,
                        allowed_mimes=set(mime_map),
                    ),
                    timeout=timeout,
                )
                suffix = mime_map[downloaded.content_type]
                final_path = destination.with_suffix(suffix)
                destination.replace(final_path)
                resolution_code = f"onebot_{item.kind}_safe_download"
                resolved.append(
                    replace(item, ref=str(final_path.resolve()), resolution_code=resolution_code)
                )
                diagnostics.append(
                    {
                        "kind": item.kind,
                        "source_kind": "onebot_remote",
                        "materialization": "safe_download",
                        "provider_transport": "local_file",
                        "size_bytes": int(downloaded.size),
                        "diagnostic_code": resolution_code,
                    }
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                destination.unlink(missing_ok=True)
                failure_code = _download_failure_code(item.kind, exc)
                resolved.append(replace(item, ref="", resolution_code=failure_code))
                diagnostics.append(
                    {
                        "kind": item.kind,
                        "source_kind": "onebot_remote",
                        "materialization": "failed",
                        "provider_transport": "none",
                        "size_bytes": None,
                        "diagnostic_code": failure_code,
                    }
                )
        lease.runtime_dir = runtime_dir
        lease.summary = {
            "media": diagnostics[:8],
            "materialized": sum(
                item.get("materialization") in {"existing_local", "onebot_get_file", "safe_download"}
                for item in diagnostics
            ),
            "failed": sum(item.get("materialization") == "failed" for item in diagnostics),
        }
        return lease
    except BaseException:
        lease.runtime_dir = runtime_dir
        lease.cleanup()
        raise


def _normalize_onebot_video_candidate(value: Any) -> tuple[str, str]:
    """Accept an adapter URL or a file that is reachable from this process."""

    raw = _text(value)
    if not raw:
        return "", ""
    normalized, problem = normalize_video_ref(raw)
    if not normalized or problem:
        return "", ""
    if normalized.startswith("http://"):
        return "", ""
    return normalized, "url" if normalized.startswith("https://") else "local"


async def _call_onebot_file_api(
    bot: Any,
    api: str,
    *,
    timeout_seconds: float,
    **kwargs: Any,
) -> Any:
    direct = getattr(bot, api, None)
    if callable(direct):
        request = direct(**kwargs)
    else:
        call_api = getattr(bot, "call_api", None)
        if not callable(call_api):
            raise RuntimeError("onebot_file_api_unavailable")
        request = call_api(api, **kwargs)
    return await asyncio.wait_for(
        request,
        timeout=max(0.001, float(timeout_seconds)),
    )


async def resolve_onebot_video_refs(
    values: Iterable[TurnMediaRef | dict[str, Any]] | None,
    bot: Any,
    *,
    timeout_seconds: float = 180.0,
) -> list[TurnMediaRef]:
    """Resolve opaque OneBot video/file tokens through the adapter on demand."""

    refs = coerce_turn_media(values)
    resolved: list[TurnMediaRef] = []
    for item in refs:
        if item.kind != "video":
            resolved.append(item)
            continue
        raw_ref = _text(item.ref)
        normalized, _problem = normalize_video_ref(raw_ref)
        if normalized and not _text(item.file_id):
            resolved.append(replace(item, ref=normalized) if normalized != raw_ref else item)
            continue
        token = _text(item.file_id or raw_ref)
        if not token:
            resolved.append(replace(item, resolution_code="onebot_video_resolve_failed"))
            continue
        deadline = asyncio.get_running_loop().time() + max(
            1.0,
            float(timeout_seconds or 180.0),
        )
        candidate = ""
        resolution_code = ""
        try:
            payload = await _call_onebot_file_api(
                bot,
                "get_file",
                timeout_seconds=max(0.001, deadline - asyncio.get_running_loop().time()),
                file=token,
            )
            candidate, candidate_kind = _normalize_onebot_video_candidate(
                _onebot_payload_value(payload, "file", "path")
            )
            if candidate:
                resolution_code = (
                    "onebot_get_file_url"
                    if candidate_kind == "url"
                    else "onebot_get_file_local"
                )
            else:
                candidate, candidate_kind = _normalize_onebot_video_candidate(
                    _onebot_payload_value(payload, "url")
                )
                if candidate:
                    resolution_code = "onebot_get_file_url"
        except Exception:
            pass

        if not candidate:
            fallback_api = "get_group_file_url" if _text(item.group_id) else "get_private_file_url"
            fallback_kwargs: dict[str, Any] = {"file_id": token}
            if fallback_api == "get_group_file_url":
                fallback_kwargs["group_id"] = item.group_id
            try:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining > 0:
                    payload = await _call_onebot_file_api(
                        bot,
                        fallback_api,
                        timeout_seconds=remaining,
                        **fallback_kwargs,
                    )
                    candidate, candidate_kind = _normalize_onebot_video_candidate(
                        _onebot_payload_value(payload, "url")
                    )
                    if candidate and candidate_kind == "url":
                        resolution_code = (
                            "onebot_group_file_url"
                            if fallback_api == "get_group_file_url"
                            else "onebot_private_file_url"
                        )
                    else:
                        candidate = ""
            except Exception:
                pass

        if candidate:
            resolved.append(
                replace(
                    item,
                    ref=candidate,
                    resolution_code=resolution_code,
                )
            )
        else:
            resolved.append(
                replace(item, resolution_code="onebot_video_resolve_failed")
            )
    return resolved


def summarize_media_resolution(
    values: Iterable[TurnMediaRef | dict[str, Any]] | None,
) -> dict[str, Any]:
    """Build a compact, non-sensitive media materialization summary for Trace."""

    refs = coerce_turn_media(values)
    video_refs = [item for item in refs if item.kind == "video"]
    usable_videos = 0
    for item in video_refs:
        normalized, problem = normalize_video_ref(_text(item.ref))
        if normalized and not problem:
            usable_videos += 1
    resolution_codes = sorted(
        {
            _text(item.resolution_code)
            for item in refs
            if _text(item.resolution_code) in _MEDIA_RESOLUTION_CODES
        }
    )
    return {
        "videos": len(video_refs),
        "video_usable": usable_videos,
        "video_failed": max(0, len(video_refs) - usable_videos),
        "audios": sum(item.kind == "audio" for item in refs),
        "resolution_codes": resolution_codes[:8],
    }


async def resolve_onebot_media_refs(
    values: Iterable[TurnMediaRef | dict[str, Any]] | None,
    bot: Any,
    *,
    video_timeout_seconds: float = 180.0,
) -> list[TurnMediaRef]:
    """Resolve lazy OneBot audio and video refs while preserving provenance."""

    refs = await resolve_onebot_video_refs(
        values,
        bot,
        timeout_seconds=video_timeout_seconds,
    )
    return await resolve_onebot_audio_refs(refs, bot)


def attach_safe_visual_summary(
    values: Iterable[TurnMediaRef | dict[str, Any]],
    summary: Any,
    *,
    confidence: float = 0.65,
) -> list[TurnMediaRef]:
    refs = coerce_turn_media(values)
    safe_summary = normalize_safe_visual_summary(summary)
    if not safe_summary:
        return refs
    # A turn aggregate is useful background, but assigning the same sentence
    # to every ref fabricates per-media evidence and can swap owners in a
    # multi-speaker batch. Keep it outside individual refs unless the mapping
    # is unambiguous.
    if len(refs) != 1:
        return refs
    return [
        replace(
            item,
            safe_summary=safe_summary,
            confidence=max(0.0, min(1.0, float(confidence or 0.0))),
            summary_scope="single_media",
        )
        for item in refs
    ]


def attach_per_media_visual_summaries(
    values: Iterable[TurnMediaRef | dict[str, Any]],
    summaries: dict[str, Any] | None,
    *,
    confidence: float = 0.65,
) -> list[TurnMediaRef]:
    refs = coerce_turn_media(values)
    normalized = {
        str(media_id): normalize_safe_visual_summary(summary)
        for media_id, summary in dict(summaries or {}).items()
        if str(media_id or "").strip() and normalize_safe_visual_summary(summary)
    }
    if not normalized:
        return refs
    resolved_confidence = max(0.0, min(1.0, float(confidence or 0.0)))
    return [
        replace(
            item,
            safe_summary=normalized[item.media_id],
            confidence=resolved_confidence,
            summary_scope="single_media",
        )
        if item.media_id in normalized
        else item
        for item in refs
    ]


def render_turn_media_grounding(
    values: Iterable[TurnMediaRef | dict[str, Any]] | None,
    *,
    summary: Any = "",
) -> str:
    refs = coerce_turn_media(values)
    safe_summary = normalize_safe_visual_summary(summary)
    if not safe_summary:
        safe_summary = next((item.safe_summary for item in refs if item.safe_summary), "")
    if not refs and not safe_summary:
        return ""
    lines = [
        "## 聊天媒体 provenance 与多模态 grounding（系统事实）",
        "- 每个媒体只归属于下列 owner/message/origin；不要把 batch 或 quoted 媒体归给当前触发者。",
        "- 画中主体只是媒体内容，不是聊天参与者。除非聊天文本或协议事实另有明确证据，不得把画中人物认作发送者、群友或 bot。",
        "- 多人构图、视线、站位、拥挤感或戏剧情绪只说明画面表现，不证明群友在现实中围观、施压、注视或参与该场景。",
    ]
    for item in refs[:12]:
        identity = item.file_id or item.content_hash[:16] or "unknown"
        lines.append(
            f"- {item.media_id}: origin={item.origin}; owner_user_id={item.owner_user_id or 'unknown'}; "
            f"message_id={item.message_id or 'unknown'}; kind={item.kind}; identity={identity}"
        )
        if item.safe_summary and item.summary_scope == "single_media":
            lines.append(
                f"  该媒体的安全视觉摘要（confidence={item.confidence:.2f}）：{item.safe_summary}"
            )
    if safe_summary:
        scope = "single_media" if len(refs) == 1 else "turn_aggregate_do_not_split_by_person"
        summary_confidence = max((item.confidence for item in refs), default=0.65)
        lines.append(
            f"- 安全视觉摘要（scope={scope}，confidence={summary_confidence:.2f}）：{safe_summary}"
        )
    else:
        lines.append("- 安全视觉摘要不可用：只能使用 provenance 和聊天文字，不得补猜画面内容。")
    return "\n".join(lines)


def media_summary_timeout_seconds(
    response_deadline: float | None,
    *,
    now: float,
    maximum: float = 18.0,
    reserve: float = 30.0,
) -> float:
    if response_deadline is None:
        return max(0.0, float(maximum))
    remaining = float(response_deadline) - float(now) - max(0.0, float(reserve))
    return max(0.0, min(float(maximum), remaining))


__all__ = [
    "MediaAvailability",
    "MediaOrigin",
    "MediaReferenceRole",
    "ResolvedTurnMediaLease",
    "TurnMediaRef",
    "VisualMediaProjection",
    "attach_safe_visual_summary",
    "attach_per_media_visual_summaries",
    "build_media_availability",
    "cleanup_turn_media_lease",
    "coerce_media_availability",
    "coerce_turn_media",
    "extract_media_from_message",
    "extract_turn_media_from_event",
    "resolve_onebot_quoted_media_refs",
    "media_from_batched_events",
    "media_summary_timeout_seconds",
    "materialize_onebot_media_refs",
    "normalize_safe_visual_summary",
    "project_visual_media_inputs",
    "render_turn_media_grounding",
    "register_turn_media_lease",
    "resolve_onebot_audio_refs",
    "resolve_onebot_media_refs",
    "resolve_onebot_video_refs",
    "serialize_turn_media",
    "summarize_media_resolution",
]
