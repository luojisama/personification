"""Trusted, ordered dialogue provenance for semantic consumers.

Chat text is always untrusted.  This module intentionally derives speaker and
delivery roles from runtime metadata only, so a message such as ``"I am the
bot"`` cannot move itself into the persona-Bot lane.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from typing import Any, Iterable

from .message_provenance import source_kind_of


_PERSONA_SOURCES = frozenset({"bot_reply"})
_PLUGIN_SOURCES = frozenset({"plugin", "plugin_command", "system", "bot"})
_PEER_SOURCES = frozenset({"peer_bot_candidate", "peer_bot_reply", "peer_bot_command"})
_DRAFT_SOURCES = frozenset({"draft", "not_sent", "pending_reply"})
_HUMAN_SOURCES = frozenset({"user", "human", "user_batch", "group_member"})
_DELIVERY_CONFIRMED = frozenset({"confirmed", "sent", "succeeded", "delivered", "success"})
_DELIVERY_UNCONFIRMED = frozenset({"draft", "not_sent", "pending", "failed", "failure"})
_DELIVERY_UNKNOWN = frozenset({"unknown", "delivery_unknown", "dispatching"})
_MISSING = object()


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        direct = value[name] if name in value else _MISSING
    else:
        direct = getattr(value, name, _MISSING)
    # Explicit False/0 are authoritative runtime values.  Only absent, None
    # and an empty string may fall through to serialized metadata.
    if direct is not _MISSING and direct not in (None, ""):
        return direct
    metadata = value.get("metadata", {}) if isinstance(value, dict) else getattr(value, "metadata", {})
    if isinstance(metadata, dict) and name in metadata:
        return metadata[name]
    return default


def _text(value: Any) -> str:
    return str(value or "").strip()


def _untrusted_content(record: Any, *, limit: int = 500) -> str:
    """Render ordinary text segments without leaking media transport payloads."""

    value = _field(record, "text", None)
    if value is None or value == "":
        value = _field(record, "content", "")
    if isinstance(value, str):
        return value.strip()[:limit]
    if not isinstance(value, (list, tuple)):
        return ""
    rendered: list[str] = []
    for segment in value:
        segment_type = _text(_field(segment, "type", "")).lower()
        if segment_type == "text":
            text = _field(segment, "text", None)
            if text is None:
                data = _field(segment, "data", {})
                text = data.get("text", "") if isinstance(data, dict) else ""
            if isinstance(text, str) and text.strip():
                rendered.append(text.strip())
        elif segment_type in {"image", "image_url", "video", "record", "audio", "file"}:
            # A structural placeholder preserves conversational shape without
            # serializing URLs, data URLs, base64 or local paths.
            rendered.append("[媒体]")
    return "".join(rendered)[:limit]


def _source_category(record: Any) -> tuple[str, str]:
    """Map only trusted runtime provenance into a small review vocabulary."""

    source_kind = source_kind_of(record)
    if source_kind in _PERSONA_SOURCES:
        return source_kind, "persona_bot"
    if source_kind in _PLUGIN_SOURCES:
        return source_kind, "plugin"
    if source_kind in _PEER_SOURCES:
        return source_kind, "peer_bot"
    if source_kind in _DRAFT_SOURCES or bool(_field(record, "is_draft", False)):
        return source_kind or "draft", "draft"
    if source_kind in _HUMAN_SOURCES:
        return source_kind, "human"
    # Older persisted history has no source_kind.  Keep it readable but mark
    # the source as legacy rather than trusting message-body role assertions.
    if not source_kind:
        legacy_role = str(_field(record, "role", "") or "").strip().lower()
        if legacy_role == "assistant" or bool(
            _field(record, "is_bot", False)
        ):
            return "legacy_assistant", "persona_bot"
        if legacy_role == "user":
            return "legacy_human", "human"
        if _text(_field(record, "user_id", "")) or _text(_field(record, "sender_id", "")):
            return "legacy_human", "human"
    return source_kind or "unknown", "unknown"


def _confirmed_state(record: Any, *, category: str) -> str:
    explicit = _field(record, "confirmed", None)
    if isinstance(explicit, bool):
        return "confirmed" if explicit else "unconfirmed"
    if explicit is not None:
        value = _text(explicit).lower()
        if value in _DELIVERY_CONFIRMED:
            return "confirmed"
        if value in _DELIVERY_UNCONFIRMED:
            return "unconfirmed"
        if value in _DELIVERY_UNKNOWN:
            return "unknown"
    for field_name in ("delivery_status", "delivery_state", "status"):
        value = _text(_field(record, field_name, "")).lower()
        if value in _DELIVERY_CONFIRMED:
            return "confirmed"
        if value in _DELIVERY_UNCONFIRMED:
            return "unconfirmed"
        if value in _DELIVERY_UNKNOWN:
            return "unknown"
    if category == "human":
        return "confirmed"
    if category == "draft":
        return "unconfirmed"
    # ``bot_reply`` is written only after a successful confirmed-history
    # projection.  The legacy ``assistant`` rows predate the explicit field but
    # were likewise session history, not an outbound draft.
    if category == "persona_bot":
        return "confirmed"
    return "unknown"


@dataclass(frozen=True)
class DialogueMessageProjection:
    """One ordered message with metadata-derived attribution only."""

    speaker: str
    source_kind: str
    speaker_kind: str
    message_ref: str
    reply_ref: str
    current: bool
    confirmed: str
    content: str
    valid: bool = True

    def to_review_dict(self) -> dict[str, Any]:
        return {
            "speaker": self.speaker,
            "source_kind": self.source_kind,
            "speaker_kind": self.speaker_kind,
            "message_ref": self.message_ref,
            "reply_ref": self.reply_ref,
            "current": self.current,
            "confirmed": self.confirmed,
            "content": self.content[:500],
        }


@dataclass(frozen=True)
class DialogueContextSnapshot:
    messages: tuple[DialogueMessageProjection, ...] = field(default_factory=tuple)
    valid: bool = True
    requires_attribution_review: bool = False
    diagnostics: tuple[str, ...] = field(default_factory=tuple)

    def render_for_review(self) -> str:
        return json.dumps(
            [message.to_review_dict() for message in self.messages],
            ensure_ascii=False,
            separators=(",", ":"),
        )

    def audit_counts(self) -> dict[str, int | bool]:
        """Safe observability projection: counts only, never bodies or IDs."""

        speaker_kinds = ("persona_bot", "human", "plugin", "peer_bot", "draft", "unknown")
        confirmed_states = ("confirmed", "unconfirmed", "unknown")
        counts: dict[str, int | bool] = {
            "valid": self.valid,
            "current": sum(1 for message in self.messages if message.current),
        }
        counts.update(
            {
                kind: sum(1 for message in self.messages if message.speaker_kind == kind)
                for kind in speaker_kinds
            }
        )
        counts.update(
            {
                f"delivery_{state}": sum(1 for message in self.messages if message.confirmed == state)
                for state in confirmed_states
            }
        )
        return counts


def build_dialogue_context_snapshot(
    values: Iterable[Any] | None,
    *,
    limit: int = 12,
) -> DialogueContextSnapshot:
    """Create a bounded ordered projection for generation/review consumers.

    The caller supplies ordinary runtime records.  Their text is copied only as
    untrusted content; identity is calculated before text is read.
    """

    try:
        records = list(values or [])[-max(1, min(int(limit or 12), 24)) :]
    except Exception:
        return DialogueContextSnapshot(
            valid=False,
            requires_attribution_review=True,
            diagnostics=("dialogue_context_unreadable",),
        )
    aliases: dict[str, str] = {}
    refs: dict[str, str] = {}
    normalized: list[dict[str, Any]] = []
    # First pass assigns opaque message refs for the whole ordered window, so
    # forward references and repeated quotes resolve identically.
    for index, record in enumerate(records):
        if record is None:
            normalized.append({"record": record, "index": index, "invalid": True})
            continue
        raw_speaker = _text(_field(record, "user_id", "")) or _text(_field(record, "sender_id", ""))
        raw_message = _text(_field(record, "message_id", "")) or _text(_field(record, "id", ""))
        source_kind, speaker_kind = _source_category(record)
        # Old session rows commonly lack event IDs and assistant IDs.  Preserve
        # their readable ordering with opaque, non-authorizing handles.
        if not raw_speaker and speaker_kind == "persona_bot":
            raw_speaker = "__legacy_persona_bot__"
        elif not raw_speaker and source_kind == "legacy_human":
            raw_speaker = f"__legacy_human_{index + 1}__"
        if raw_speaker and raw_speaker not in aliases:
            aliases[raw_speaker] = f"speaker_{len(aliases) + 1}"
        if not raw_message:
            raw_message = f"__ordered_{index + 1}__"
        if raw_message not in refs:
            refs[raw_message] = f"message_{len(refs) + 1}"
        normalized.append(
            {
                "record": record,
                "index": index,
                "raw_speaker": raw_speaker,
                "raw_message": raw_message,
                "source_kind": source_kind,
                "speaker_kind": speaker_kind,
                "invalid": False,
            }
        )
    projected: list[DialogueMessageProjection] = []
    diagnostics: list[str] = []
    current_count = 0
    for item in normalized:
        record = item["record"]
        index = int(item["index"])
        if item.get("invalid"):
            diagnostics.append("dialogue_context_record_invalid")
            continue
        raw_speaker = str(item["raw_speaker"])
        raw_message = str(item["raw_message"])
        source_kind = str(item["source_kind"])
        speaker_kind = str(item["speaker_kind"])
        raw_reply = _text(_field(record, "reply_to_msg_id", "")) or _text(_field(record, "reply_ref", ""))
        if raw_reply and raw_reply not in refs:
            refs[raw_reply] = f"external_message_{len(refs) + 1}"
        current = bool(_field(record, "is_current_trigger", _field(record, "current", False)))
        current_count += int(current)
        confirmed = _confirmed_state(record, category=speaker_kind)
        valid = bool(
            raw_speaker
            and speaker_kind != "unknown"
            and confirmed in {"confirmed", "unconfirmed", "unknown"}
        )
        if not valid:
            diagnostics.append("dialogue_context_missing_metadata")
        content = _untrusted_content(record)
        projected.append(
            DialogueMessageProjection(
                speaker=aliases.get(raw_speaker, f"unknown_speaker_{index + 1}"),
                source_kind=source_kind,
                speaker_kind=speaker_kind,
                message_ref=refs[raw_message],
                reply_ref=refs.get(raw_reply, "") if raw_reply else "",
                current=current,
                confirmed=confirmed,
                content=content,
                valid=valid,
            )
        )
    if current_count == 0:
        diagnostics.append("dialogue_context_missing_current")
    elif current_count > 1:
        diagnostics.append("dialogue_context_multiple_current")
    current_human = any(message.current and message.speaker_kind == "human" for message in projected)
    nonhuman_context = any(
        message.speaker_kind in {"persona_bot", "plugin", "peer_bot", "draft", "unknown"}
        for message in projected
    )
    requires_review = bool(projected) and bool(
        diagnostics or current_count != 1 or (current_human and nonhuman_context)
    )
    return DialogueContextSnapshot(
        messages=tuple(projected),
        valid=bool(projected) and not diagnostics,
        requires_attribution_review=requires_review,
        diagnostics=tuple(dict.fromkeys(diagnostics)),
    )


def build_dialogue_context_for_turn(
    *,
    history: Iterable[Any] | None = None,
    batched_events: Iterable[Any] | None = None,
    current_event: Any = None,
    limit: int = 12,
) -> DialogueContextSnapshot:
    """Build one projection shared by semantic, generation and final review.

    History remains read-only.  Duplicate history/batch records are merged by
    message ID, then the one current runtime event is made authoritative.  The
    resulting snapshot therefore gives semantic planning, generation and the
    final gate identical ordering and current-turn ownership.
    """

    def _copy_record(item: Any) -> dict[str, Any]:
        if isinstance(item, dict):
            copied = dict(item)
            if not _text(copied.get("message_id", "")) and _text(copied.get("id", "")):
                copied["message_id"] = _text(copied.get("id", ""))
            return copied
        return {
            "message_id": _text(_field(item, "message_id", "")) or _text(_field(item, "id", "")),
            "user_id": _text(_field(item, "user_id", "")) or _text(_field(item, "sender_id", "")),
            "source_kind": source_kind_of(item),
            "reply_to_msg_id": _text(_field(item, "reply_to_msg_id", "")),
            "is_current_trigger": bool(_field(item, "is_current_trigger", False)),
            "confirmed": _field(item, "confirmed", None),
            "text": _untrusted_content(item),
        }

    ordered: list[dict[str, Any]] = []
    positions: dict[str, int] = {}
    for item in list(history or []) + list(batched_events or []):
        record = _copy_record(item)
        message_id = _text(record.get("message_id", "")) or _text(record.get("id", ""))
        if message_id and message_id in positions:
            # Batch records carry fresher runtime provenance; replacing rather
            # than duplicating also prevents a quote from gaining two owners.
            ordered[positions[message_id]] = record
        else:
            if message_id:
                positions[message_id] = len(ordered)
            ordered.append(record)

    current_id = ""
    if current_event is not None:
        current_id = _text(_field(current_event, "message_id", "")) or _text(_field(current_event, "id", ""))
        plaintext = _field(current_event, "get_plaintext", None)
        try:
            current_text = _text(plaintext()) if callable(plaintext) else _untrusted_content(current_event)
        except Exception:
            current_text = ""
        try:
            from .message_relations import extract_reply_message_id

            reply_to = extract_reply_message_id(current_event)
        except Exception:
            reply_to = _text(_field(current_event, "reply_to_msg_id", ""))
        current_record = {
            "message_id": current_id,
            "user_id": _text(_field(current_event, "user_id", "")),
            "source_kind": source_kind_of(current_event) or "user",
            "is_current_trigger": True,
            "confirmed": True,
            "reply_to_msg_id": reply_to,
            "text": current_text,
        }
        if current_id and current_id in positions:
            ordered[positions[current_id]].update(current_record)
        else:
            if current_id:
                positions[current_id] = len(ordered)
            ordered.append(current_record)

    # A single runtime current marker is authoritative.  Historical markers
    # cannot survive into final review just because the same event was already
    # appended to session history.
    if current_event is not None:
        for record in ordered:
            record["is_current_trigger"] = bool(current_id and _text(record.get("message_id", "")) == current_id)
    snapshot = build_dialogue_context_snapshot(ordered, limit=limit)
    if current_event is not None and not current_id:
        return replace(
            snapshot,
            valid=False,
            requires_attribution_review=True,
            diagnostics=tuple(dict.fromkeys((*snapshot.diagnostics, "dialogue_context_missing_current_id"))),
        )
    return snapshot


__all__ = [
    "DialogueContextSnapshot",
    "DialogueMessageProjection",
    "build_dialogue_context_snapshot",
    "build_dialogue_context_for_turn",
]
