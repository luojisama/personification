from __future__ import annotations

import copy
import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from .data_store import get_data_store


SCHEMA_VERSION = 2
STORE_NAME = "emotion_state_v2"
V1_EMOTION_STORE_NAME = "emotion_state_v1"
V1_INNER_STORE_NAME = "inner_state"

GLOBAL_HALF_LIFE_HOURS = 6.0
RELATION_HALF_LIFE_HOURS = 48.0
ENTRY_TTL_DAYS = 30
APPRAISAL_TEXT_LIMIT = 160
SCOPE_ID_LIMIT = 128

EMOTION_CATEGORIES = (
    "平静",
    "开心",
    "疲惫",
    "困倦",
    "烦躁",
    "低落",
    "期待",
    "紧张",
    "放松",
    "无语",
    "好奇",
)
ACTION_TENDENCIES = ("approach", "avoid", "support", "observe")
EMOTION_SCOPES = ("global", "user", "group")

_NEUTRAL_VAD = {
    "valence": 0.0,
    "arousal": 0.5,
    "dominance": 0.0,
}
_APPRAISAL_FIELDS = ("reason", "goal", "certainty", "controllability")
_PATCH_FIELDS = {
    "scope",
    "scope_id",
    "vad",
    "category",
    "confidence",
    "appraisal",
    "action_tendency",
}
_VAD_FIELDS = frozenset(_NEUTRAL_VAD)

_USER_COMPATIBILITY_FIELDS = (
    "user_attitude",
    "bot_emotion",
    "emotion_intensity",
    "expression_style",
    "tts_style_hint",
    "sticker_mood_hint",
    "last_group_id",
    "last_reply",
)
_GROUP_COMPATIBILITY_FIELDS = (
    "group_climate",
    "bot_social_posture",
    "bot_emotion",
    "emotion_intensity",
    "last_user_id",
)

_EMOTION_PATCH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["scope"],
    "properties": {
        "scope": {"type": "string", "enum": list(EMOTION_SCOPES)},
        "scope_id": {"type": "string", "minLength": 1, "maxLength": SCOPE_ID_LIMIT},
        "vad": {
            "type": "object",
            "additionalProperties": False,
            "minProperties": 1,
            "properties": {
                "valence": {"type": "number", "minimum": -1.0, "maximum": 1.0},
                "arousal": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                "dominance": {"type": "number", "minimum": -1.0, "maximum": 1.0},
            },
        },
        "category": {"type": "string", "enum": list(EMOTION_CATEGORIES)},
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "appraisal": {
            "type": "object",
            "additionalProperties": False,
            "minProperties": 1,
            "properties": {
                field: {"type": "string", "maxLength": APPRAISAL_TEXT_LIMIT}
                for field in _APPRAISAL_FIELDS
            },
        },
        "action_tendency": {"type": "string", "enum": list(ACTION_TENDENCIES)},
    },
    "anyOf": [
        {"required": [field]}
        for field in (
            "vad",
            "category",
            "confidence",
            "appraisal",
            "action_tendency",
        )
    ],
}


class EmotionStateV2Error(ValueError):
    """Base error for invalid v2 state or patch data."""


class EmotionPatchValidationError(EmotionStateV2Error):
    """Raised when a structured emotion patch does not match the contract."""


class UnsupportedEmotionStateVersion(EmotionStateV2Error):
    """Raised instead of silently downgrading a newer persisted schema."""


class EmotionCompatibilityWriteError(RuntimeError):
    """Raised when v2 was written but the v1 rollback view could not be mirrored."""

    def __init__(self, message: str, *, state: dict[str, Any]) -> None:
        super().__init__(message)
        self.state = copy.deepcopy(state)


@dataclass(frozen=True)
class EmotionPatch:
    """Validated, partial update selected by the LLM through a structured schema.

    The core never infers a category or action from natural-language keywords.  It
    only accepts exact enum values and mechanically validates/clamps the supplied
    structured values.
    """

    scope: str
    scope_id: str
    vad: dict[str, float] | None = None
    category: str | None = None
    confidence: float | None = None
    appraisal: dict[str, str] | None = None
    action_tendency: str | None = None

    @classmethod
    def json_schema(cls) -> dict[str, Any]:
        return copy.deepcopy(_EMOTION_PATCH_SCHEMA)

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "EmotionPatch":
        if not isinstance(raw, Mapping):
            raise EmotionPatchValidationError("emotion_patch_not_object")

        unknown = sorted(str(key) for key in raw.keys() if key not in _PATCH_FIELDS)
        if unknown:
            raise EmotionPatchValidationError(
                f"emotion_patch_unknown_fields:{','.join(unknown)}"
            )

        scope = str(raw.get("scope", "") or "").strip()
        if scope not in EMOTION_SCOPES:
            raise EmotionPatchValidationError("emotion_patch_invalid_scope")

        scope_id = _normalize_scope_id(raw.get("scope_id", ""))
        if scope == "global":
            if scope_id:
                raise EmotionPatchValidationError("emotion_patch_global_scope_id_forbidden")
        elif not scope_id:
            raise EmotionPatchValidationError("emotion_patch_scope_id_required")

        supplied_fields = {
            key
            for key in (
                "vad",
                "category",
                "confidence",
                "appraisal",
                "action_tendency",
            )
            if key in raw
        }
        if not supplied_fields:
            raise EmotionPatchValidationError("emotion_patch_empty")

        vad: dict[str, float] | None = None
        if "vad" in raw:
            raw_vad = raw.get("vad")
            if not isinstance(raw_vad, Mapping):
                raise EmotionPatchValidationError("emotion_patch_vad_not_object")
            unknown_vad = sorted(str(key) for key in raw_vad.keys() if key not in _VAD_FIELDS)
            if unknown_vad:
                raise EmotionPatchValidationError(
                    f"emotion_patch_unknown_vad_fields:{','.join(unknown_vad)}"
                )
            if not raw_vad:
                raise EmotionPatchValidationError("emotion_patch_vad_empty")
            vad = {}
            for key, value in raw_vad.items():
                vad[str(key)] = _normalize_number(
                    value,
                    minimum=0.0 if key == "arousal" else -1.0,
                    maximum=1.0,
                    field=f"vad.{key}",
                    strict_type=True,
                    error_type=EmotionPatchValidationError,
                )

        category: str | None = None
        if "category" in raw:
            category = _validate_category(
                raw.get("category"),
                error_type=EmotionPatchValidationError,
            )

        confidence: float | None = None
        if "confidence" in raw:
            confidence = _normalize_number(
                raw.get("confidence"),
                minimum=0.0,
                maximum=1.0,
                field="confidence",
                strict_type=True,
                error_type=EmotionPatchValidationError,
            )

        appraisal: dict[str, str] | None = None
        if "appraisal" in raw:
            raw_appraisal = raw.get("appraisal")
            if not isinstance(raw_appraisal, Mapping):
                raise EmotionPatchValidationError("emotion_patch_appraisal_not_object")
            unknown_appraisal = sorted(
                str(key) for key in raw_appraisal.keys() if key not in _APPRAISAL_FIELDS
            )
            if unknown_appraisal:
                raise EmotionPatchValidationError(
                    "emotion_patch_unknown_appraisal_fields:"
                    + ",".join(unknown_appraisal)
                )
            if not raw_appraisal:
                raise EmotionPatchValidationError("emotion_patch_appraisal_empty")
            appraisal = {
                str(key): _normalize_text(
                    value,
                    limit=APPRAISAL_TEXT_LIMIT,
                    strict=True,
                    field=f"appraisal.{key}",
                    error_type=EmotionPatchValidationError,
                )
                for key, value in raw_appraisal.items()
            }

        action_tendency: str | None = None
        if "action_tendency" in raw:
            action_tendency = _validate_action_tendency(
                raw.get("action_tendency"),
                error_type=EmotionPatchValidationError,
            )

        return cls(
            scope=scope,
            scope_id=scope_id,
            vad=vad,
            category=category,
            confidence=confidence,
            appraisal=appraisal,
            action_tendency=action_tendency,
        )


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _coerce_datetime(value: datetime | None) -> datetime:
    current = value or _utc_now()
    if current.tzinfo is None:
        return current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def _format_timestamp(value: datetime) -> str:
    return _coerce_datetime(value).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_timestamp(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(normalized)
    except (TypeError, ValueError):
        try:
            parsed = datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
        except (TypeError, ValueError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _parse_legacy_timestamp(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(normalized)
    except (TypeError, ValueError):
        try:
            parsed = datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
        except (TypeError, ValueError):
            return None
    if parsed.tzinfo is None:
        local_timezone = datetime.now().astimezone().tzinfo or timezone.utc
        parsed = parsed.replace(tzinfo=local_timezone)
    return parsed.astimezone(timezone.utc)


def _normalize_text(
    value: Any,
    *,
    limit: int,
    strict: bool = False,
    field: str = "text",
    error_type: type[EmotionStateV2Error] = EmotionStateV2Error,
) -> str:
    if strict and not isinstance(value, str):
        raise error_type(f"emotion_{field}_not_string")
    text = re.sub(r"\s+", " ", str(value or "").strip())
    return text[: max(0, int(limit))].strip()


def _normalize_scope_id(value: Any) -> str:
    if value in (None, ""):
        return ""
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        raise EmotionPatchValidationError("emotion_patch_scope_id_invalid")
    text = str(value).strip()
    if not text or len(text) > SCOPE_ID_LIMIT:
        raise EmotionPatchValidationError("emotion_patch_scope_id_invalid")
    return text


def _normalize_number(
    value: Any,
    *,
    minimum: float,
    maximum: float,
    field: str,
    default: float | None = None,
    strict_type: bool = False,
    error_type: type[EmotionStateV2Error] = EmotionStateV2Error,
) -> float:
    if isinstance(value, bool) or (
        strict_type and not isinstance(value, (int, float))
    ):
        if default is not None:
            return float(default)
        raise error_type(f"emotion_{field}_not_number")
    try:
        number = float(value)
    except (TypeError, ValueError):
        if default is not None:
            return float(default)
        raise error_type(f"emotion_{field}_not_number")
    if not math.isfinite(number):
        if default is not None:
            return float(default)
        raise error_type(f"emotion_{field}_not_finite")
    return max(float(minimum), min(float(maximum), number))


def _validate_category(
    value: Any,
    *,
    default: str | None = None,
    error_type: type[EmotionStateV2Error] = EmotionStateV2Error,
) -> str:
    if not isinstance(value, str):
        if default is not None:
            return default
        raise error_type("emotion_category_not_string")
    category = value.strip()
    if category not in EMOTION_CATEGORIES:
        if default is not None:
            return default
        raise error_type("emotion_category_invalid")
    return category


def _validate_action_tendency(
    value: Any,
    *,
    default: str | None = None,
    error_type: type[EmotionStateV2Error] = EmotionStateV2Error,
) -> str:
    if not isinstance(value, str):
        if default is not None:
            return default
        raise error_type("emotion_action_tendency_not_string")
    action = value.strip()
    if action not in ACTION_TENDENCIES:
        if default is not None:
            return default
        raise error_type("emotion_action_tendency_invalid")
    return action


def _default_appraisal() -> dict[str, str]:
    return {field: "" for field in _APPRAISAL_FIELDS}


def _default_record(*, updated_at: str = "") -> dict[str, Any]:
    return {
        "vad": dict(_NEUTRAL_VAD),
        "category": "平静",
        "confidence": 0.0,
        "appraisal": _default_appraisal(),
        "action_tendency": "observe",
        "updated_at": str(updated_at or ""),
    }


def default_emotion_state_v2() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "global": {
            **_default_record(),
            "mood": "平静",
            "energy": "正常",
            "pending_thoughts": [],
            "relation_warmth": {},
        },
        "per_user": {},
        "per_group": {},
        "migration": {
            "source": "fresh",
            "migrated_at": "",
            "v1_emotion_present": False,
            "v1_inner_present": False,
        },
        "updated_at": "",
    }


DEFAULT_EMOTION_STATE_V2 = default_emotion_state_v2()


def _normalize_vad(raw: Any) -> dict[str, float]:
    source = raw if isinstance(raw, Mapping) else {}
    return {
        key: _normalize_number(
            source.get(key),
            minimum=0.0 if key == "arousal" else -1.0,
            maximum=1.0,
            field=f"vad.{key}",
            default=neutral,
        )
        for key, neutral in _NEUTRAL_VAD.items()
    }


def _normalize_appraisal(raw: Any) -> dict[str, str]:
    source = raw if isinstance(raw, Mapping) else {}
    return {
        field: _normalize_text(source.get(field, ""), limit=APPRAISAL_TEXT_LIMIT)
        for field in _APPRAISAL_FIELDS
    }


def _copy_jsonish(value: Any, *, fallback: Any) -> Any:
    if isinstance(value, type(fallback)):
        return copy.deepcopy(value)
    return copy.deepcopy(fallback)


def normalize_emotion_record(
    raw: Any,
    *,
    updated_at_fallback: str = "",
) -> dict[str, Any]:
    source = raw if isinstance(raw, Mapping) else {}
    timestamp = str(source.get("updated_at", "") or updated_at_fallback or "").strip()
    return {
        "vad": _normalize_vad(source.get("vad")),
        "category": _validate_category(source.get("category"), default="平静"),
        "confidence": _normalize_number(
            source.get("confidence"),
            minimum=0.0,
            maximum=1.0,
            field="confidence",
            default=0.0,
        ),
        "appraisal": _normalize_appraisal(source.get("appraisal")),
        "action_tendency": _validate_action_tendency(
            source.get("action_tendency"),
            default="observe",
        ),
        "updated_at": timestamp,
    }


def _normalize_global_record(raw: Any, *, updated_at_fallback: str) -> dict[str, Any]:
    source = raw if isinstance(raw, Mapping) else {}
    record = normalize_emotion_record(source, updated_at_fallback=updated_at_fallback)
    mood = _normalize_text(source.get("mood", record["category"]), limit=80)
    record.update(
        {
            "mood": mood or record["category"],
            "energy": _normalize_text(source.get("energy", "正常"), limit=20) or "正常",
            "pending_thoughts": _copy_jsonish(
                source.get("pending_thoughts"),
                fallback=[],
            )[-8:],
            "relation_warmth": _normalize_relation_warmth(
                source.get("relation_warmth")
            ),
        }
    )
    return record


def _normalize_relation_warmth(raw: Any) -> dict[str, float]:
    if not isinstance(raw, Mapping):
        return {}
    normalized: dict[str, float] = {}
    for key, value in raw.items():
        key_text = _normalize_text(key, limit=SCOPE_ID_LIMIT)
        if not key_text:
            continue
        try:
            normalized[key_text] = _normalize_number(
                value,
                minimum=-1.0,
                maximum=1.0,
                field="relation_warmth",
            )
        except EmotionStateV2Error:
            continue
    return normalized


def _normalize_compatibility_fields(
    raw: Any,
    *,
    fields: tuple[str, ...],
) -> dict[str, str]:
    source = raw if isinstance(raw, Mapping) else {}
    normalized: dict[str, str] = {}
    for field in fields:
        limit = 120 if field == "last_reply" else 80
        if field in ("last_group_id", "last_user_id"):
            limit = SCOPE_ID_LIMIT
        normalized[field] = _normalize_text(source.get(field, ""), limit=limit)
    return normalized


def _normalize_scoped_record(
    raw: Any,
    *,
    fields: tuple[str, ...],
    updated_at_fallback: str,
) -> dict[str, Any]:
    record = normalize_emotion_record(raw, updated_at_fallback=updated_at_fallback)
    record.update(_normalize_compatibility_fields(raw, fields=fields))
    return record


def _normalize_migration(raw: Any) -> dict[str, Any]:
    source = raw if isinstance(raw, Mapping) else {}
    migration_source = str(source.get("source", "fresh") or "fresh").strip()
    if migration_source not in ("fresh", "v1"):
        migration_source = "fresh"
    return {
        "source": migration_source,
        "migrated_at": str(source.get("migrated_at", "") or "").strip(),
        "v1_emotion_present": bool(source.get("v1_emotion_present", False)),
        "v1_inner_present": bool(source.get("v1_inner_present", False)),
    }


def _is_expired(updated_at: str, *, now: datetime) -> bool:
    parsed = _parse_timestamp(updated_at)
    if parsed is None:
        return False
    return parsed < now - timedelta(days=ENTRY_TTL_DAYS)


def _is_current_schema(raw: Any) -> bool:
    if not isinstance(raw, Mapping):
        return False
    try:
        return int(raw.get("schema_version")) == SCHEMA_VERSION
    except (TypeError, ValueError):
        return False


def normalize_emotion_state_v2(
    raw: Any,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    current_time = _coerce_datetime(now)
    current_timestamp = _format_timestamp(current_time)
    source = raw if isinstance(raw, Mapping) else {}
    version = source.get("schema_version", SCHEMA_VERSION)
    try:
        version_number = int(version)
    except (TypeError, ValueError):
        version_number = SCHEMA_VERSION
    if version_number > SCHEMA_VERSION:
        raise UnsupportedEmotionStateVersion(
            f"emotion_state_schema_newer:{version_number}"
        )

    root_updated_at = str(source.get("updated_at", "") or "").strip()
    state = default_emotion_state_v2()
    state["global"] = _normalize_global_record(
        source.get("global"),
        updated_at_fallback=root_updated_at or current_timestamp,
    )

    for bucket_name, fields in (
        ("per_user", _USER_COMPATIBILITY_FIELDS),
        ("per_group", _GROUP_COMPATIBILITY_FIELDS),
    ):
        raw_bucket = source.get(bucket_name)
        bucket: dict[str, dict[str, Any]] = {}
        if isinstance(raw_bucket, Mapping):
            for raw_key, raw_entry in raw_bucket.items():
                key = _normalize_text(raw_key, limit=SCOPE_ID_LIMIT)
                if not key or not isinstance(raw_entry, Mapping):
                    continue
                record = _normalize_scoped_record(
                    raw_entry,
                    fields=fields,
                    updated_at_fallback=root_updated_at or current_timestamp,
                )
                if _is_expired(record["updated_at"], now=current_time):
                    continue
                bucket[key] = record
        state[bucket_name] = bucket

    state["migration"] = _normalize_migration(source.get("migration"))
    state["updated_at"] = root_updated_at or state["global"]["updated_at"]
    return state


def _decay_factor(*, elapsed_hours: float, half_life_hours: float) -> float:
    if elapsed_hours <= 0.0:
        return 1.0
    return math.pow(0.5, elapsed_hours / max(0.001, half_life_hours))


def materialize_emotion_record(
    raw: Any,
    *,
    now: datetime | None = None,
    half_life_hours: float,
) -> dict[str, Any]:
    record = normalize_emotion_record(raw)
    current_time = _coerce_datetime(now)
    updated_at = _parse_timestamp(record.get("updated_at"))
    if updated_at is None:
        return record
    elapsed_hours = max(0.0, (current_time - updated_at).total_seconds() / 3600.0)
    factor = _decay_factor(
        elapsed_hours=elapsed_hours,
        half_life_hours=half_life_hours,
    )
    record["vad"] = {
        key: neutral + (record["vad"][key] - neutral) * factor
        for key, neutral in _NEUTRAL_VAD.items()
    }
    record["confidence"] = record["confidence"] * factor
    return record


def _materialize_full_record(
    raw: dict[str, Any],
    *,
    now: datetime,
    half_life_hours: float,
) -> dict[str, Any]:
    materialized = copy.deepcopy(raw)
    decayed = materialize_emotion_record(
        raw,
        now=now,
        half_life_hours=half_life_hours,
    )
    for field in (
        "vad",
        "category",
        "confidence",
        "appraisal",
        "action_tendency",
        "updated_at",
    ):
        materialized[field] = decayed[field]
    return materialized


def materialize_emotion_state_v2(
    raw: Any,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    current_time = _coerce_datetime(now)
    state = normalize_emotion_state_v2(raw, now=current_time)
    state["global"] = _materialize_full_record(
        state["global"],
        now=current_time,
        half_life_hours=GLOBAL_HALF_LIFE_HOURS,
    )
    for bucket_name in ("per_user", "per_group"):
        state[bucket_name] = {
            key: _materialize_full_record(
                entry,
                now=current_time,
                half_life_hours=RELATION_HALF_LIFE_HOURS,
            )
            for key, entry in state[bucket_name].items()
        }
    return state


def _legacy_category(value: Any) -> str:
    """Map only an exact existing label; never infer from natural language."""

    return _validate_category(value, default="平静")


def _legacy_record(
    raw: Any,
    *,
    fields: tuple[str, ...],
    now: datetime,
    fallback_updated_at: str,
) -> dict[str, Any]:
    source = raw if isinstance(raw, Mapping) else {}
    updated_at = str(source.get("updated_at", "") or fallback_updated_at or "").strip()
    parsed_updated_at = _parse_legacy_timestamp(updated_at)
    if parsed_updated_at is None:
        updated_at = _format_timestamp(now)
    else:
        updated_at = _format_timestamp(parsed_updated_at)
    compatibility = _normalize_compatibility_fields(source, fields=fields)
    category_source = compatibility.get("bot_emotion", "")
    return {
        **_default_record(updated_at=updated_at),
        **compatibility,
        "category": _legacy_category(category_source),
    }


def migrate_v1_emotion_state(
    v1_emotion_state: Any,
    v1_inner_state: Any,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build a v2 document without mutating either v1 input.

    Existing labels are retained in compatibility fields.  Only exact members of
    ``EMOTION_CATEGORIES`` become the v2 category; arbitrary free text is not
    interpreted.  All migrated continuous values start neutral with confidence
    zero, so migration does not invent emotional semantics.
    """

    current_time = _coerce_datetime(now)
    current_timestamp = _format_timestamp(current_time)
    emotion_source = v1_emotion_state if isinstance(v1_emotion_state, Mapping) else {}
    inner_source = v1_inner_state if isinstance(v1_inner_state, Mapping) else {}
    emotion_root_updated_at = str(emotion_source.get("updated_at", "") or "").strip()
    inner_updated_at = str(inner_source.get("updated_at", "") or "").strip()
    parsed_inner_updated_at = _parse_legacy_timestamp(inner_updated_at)
    if parsed_inner_updated_at is None:
        inner_updated_at = current_timestamp
    else:
        inner_updated_at = _format_timestamp(parsed_inner_updated_at)

    state = default_emotion_state_v2()
    global_category = _legacy_category(inner_source.get("mood"))
    state["global"] = {
        **_default_record(updated_at=inner_updated_at),
        "category": global_category,
        "mood": _normalize_text(inner_source.get("mood", ""), limit=80)
        or global_category,
        "energy": _normalize_text(inner_source.get("energy", "正常"), limit=20)
        or "正常",
        "pending_thoughts": _copy_jsonish(
            inner_source.get("pending_thoughts"),
            fallback=[],
        )[-8:],
        "relation_warmth": _normalize_relation_warmth(
            inner_source.get("relation_warmth")
        ),
    }

    for bucket_name, fields in (
        ("per_user", _USER_COMPATIBILITY_FIELDS),
        ("per_group", _GROUP_COMPATIBILITY_FIELDS),
    ):
        raw_bucket = emotion_source.get(bucket_name)
        bucket: dict[str, dict[str, Any]] = {}
        if isinstance(raw_bucket, Mapping):
            for raw_key, raw_entry in raw_bucket.items():
                key = _normalize_text(raw_key, limit=SCOPE_ID_LIMIT)
                if not key or not isinstance(raw_entry, Mapping):
                    continue
                record = _legacy_record(
                    raw_entry,
                    fields=fields,
                    now=current_time,
                    fallback_updated_at=emotion_root_updated_at,
                )
                if _is_expired(record["updated_at"], now=current_time):
                    continue
                bucket[key] = record
        state[bucket_name] = bucket

    state["migration"] = {
        "source": "v1",
        "migrated_at": current_timestamp,
        "v1_emotion_present": bool(emotion_source),
        "v1_inner_present": bool(inner_source),
    }
    state["updated_at"] = current_timestamp
    return normalize_emotion_state_v2(state, now=current_time)


def _legacy_fields(record: Mapping[str, Any], fields: tuple[str, ...]) -> dict[str, str]:
    return {field: str(record.get(field, "") or "") for field in fields}


def build_v1_compatibility_view(raw: Any, *, now: datetime | None = None) -> dict[str, Any]:
    """Return the exact legacy namespace shapes needed for one-version rollback."""

    state = normalize_emotion_state_v2(raw, now=now)
    global_record = state["global"]
    return {
        "inner_state": {
            "mood": str(global_record.get("mood") or global_record["category"]),
            "energy": str(global_record.get("energy") or "正常"),
            "pending_thoughts": copy.deepcopy(global_record.get("pending_thoughts", [])),
            "relation_warmth": copy.deepcopy(global_record.get("relation_warmth", {})),
            "updated_at": str(global_record.get("updated_at", "") or state["updated_at"]),
        },
        "emotion_state": {
            "per_user": {
                key: {
                    **_legacy_fields(entry, _USER_COMPATIBILITY_FIELDS),
                    "updated_at": str(entry.get("updated_at", "")),
                }
                for key, entry in state["per_user"].items()
            },
            "per_group": {
                key: {
                    **_legacy_fields(entry, _GROUP_COMPATIBILITY_FIELDS),
                    "updated_at": str(entry.get("updated_at", "")),
                }
                for key, entry in state["per_group"].items()
            },
            "updated_at": str(state.get("updated_at", "")),
        },
    }


def apply_emotion_patch(
    raw: Any,
    patch: EmotionPatch | Mapping[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    validated = patch if isinstance(patch, EmotionPatch) else EmotionPatch.from_mapping(patch)
    current_time = _coerce_datetime(now)
    timestamp = _format_timestamp(current_time)
    state = normalize_emotion_state_v2(raw, now=current_time)

    if validated.scope == "global":
        record = dict(state["global"])
    else:
        bucket_name = "per_user" if validated.scope == "user" else "per_group"
        fields = (
            _USER_COMPATIBILITY_FIELDS
            if validated.scope == "user"
            else _GROUP_COMPATIBILITY_FIELDS
        )
        existing = state[bucket_name].get(validated.scope_id)
        record = (
            dict(existing)
            if isinstance(existing, Mapping)
            else _normalize_scoped_record({}, fields=fields, updated_at_fallback=timestamp)
        )

    if validated.vad is not None:
        vad = dict(record["vad"])
        vad.update(validated.vad)
        record["vad"] = vad
    if validated.category is not None:
        record["category"] = validated.category
        if validated.scope == "global":
            record["mood"] = validated.category
        else:
            record["bot_emotion"] = validated.category
    if validated.confidence is not None:
        record["confidence"] = validated.confidence
    if validated.appraisal is not None:
        appraisal = dict(record["appraisal"])
        appraisal.update(validated.appraisal)
        record["appraisal"] = appraisal
    if validated.action_tendency is not None:
        record["action_tendency"] = validated.action_tendency
    record["updated_at"] = timestamp

    if validated.scope == "global":
        state["global"] = _normalize_global_record(
            record,
            updated_at_fallback=timestamp,
        )
    else:
        bucket_name = "per_user" if validated.scope == "user" else "per_group"
        fields = (
            _USER_COMPATIBILITY_FIELDS
            if validated.scope == "user"
            else _GROUP_COMPATIBILITY_FIELDS
        )
        bucket = dict(state[bucket_name])
        bucket[validated.scope_id] = _normalize_scoped_record(
            record,
            fields=fields,
            updated_at_fallback=timestamp,
        )
        state[bucket_name] = bucket

    state["updated_at"] = timestamp
    return normalize_emotion_state_v2(state, now=current_time)


class EmotionStateV2Service:
    """Persistence, lazy migration and rollback compatibility for emotion v2."""

    def __init__(
        self,
        *,
        store: Any | None = None,
        clock: Callable[[], datetime] | None = None,
        mirror_v1: bool = True,
    ) -> None:
        self._store = store
        self._clock = clock or _utc_now
        self._mirror_v1 = bool(mirror_v1)

    @property
    def store(self) -> Any:
        return self._store if self._store is not None else get_data_store()

    def _now(self) -> datetime:
        return _coerce_datetime(self._clock())

    async def _ensure_raw_state(self, *, now: datetime) -> dict[str, Any]:
        store = self.store
        current = await store.load(STORE_NAME)
        if _is_current_schema(current):
            return normalize_emotion_state_v2(current, now=now)
        if isinstance(current, Mapping):
            raw_version = current.get("schema_version")
            try:
                version_number = int(raw_version) if raw_version is not None else None
            except (TypeError, ValueError):
                version_number = None
            if version_number is not None and version_number > SCHEMA_VERSION:
                raise UnsupportedEmotionStateVersion(
                    f"emotion_state_schema_newer:{raw_version}"
                )

        v1_emotion = await store.load(V1_EMOTION_STORE_NAME)
        v1_inner = await store.load(V1_INNER_STORE_NAME)
        migrated = migrate_v1_emotion_state(
            v1_emotion,
            v1_inner,
            now=now,
        )

        def _initialize(loaded: Any) -> dict[str, Any]:
            if _is_current_schema(loaded):
                return normalize_emotion_state_v2(loaded, now=now)
            return copy.deepcopy(migrated)

        initialized = await store.mutate(STORE_NAME, _initialize)
        return normalize_emotion_state_v2(initialized, now=now)

    async def load(self) -> dict[str, Any]:
        now = self._now()
        raw = await self._ensure_raw_state(now=now)
        return materialize_emotion_state_v2(raw, now=now)

    async def apply_patch(
        self,
        patch: EmotionPatch | Mapping[str, Any],
    ) -> dict[str, Any]:
        validated = patch if isinstance(patch, EmotionPatch) else EmotionPatch.from_mapping(patch)
        now = self._now()
        await self._ensure_raw_state(now=now)

        def _mutate(current: Any) -> dict[str, Any]:
            return apply_emotion_patch(current, validated, now=now)

        updated = await self.store.mutate(STORE_NAME, _mutate)
        normalized = normalize_emotion_state_v2(updated, now=now)
        if self._mirror_v1:
            try:
                await self._mirror_patch_to_v1(validated, normalized)
            except Exception as exc:
                raise EmotionCompatibilityWriteError(
                    "emotion_v1_compatibility_write_failed",
                    state=normalized,
                ) from exc
        return materialize_emotion_state_v2(normalized, now=now)

    async def record_turn(
        self,
        *,
        user_id: str,
        group_id: str = "",
        semantic_frame: Any,
        assistant_text: str = "",
        is_private: bool = False,
        emotion_updates: Any = None,
    ) -> dict[str, Any]:
        """Persist one LLM-structured turn without inferring emotion from keywords.

        Legacy text fields remain available for rollback.  A v2 category is copied
        from ``bot_emotion`` only when it exactly matches the category enum; all
        VAD/appraisal/action updates must arrive through the validated structured
        ``emotion_updates`` payload.  User and group ids are always rebound to the
        current trusted session instead of accepting model-supplied identifiers.
        """

        now = self._now()
        await self._ensure_raw_state(now=now)
        trusted_user_id = _normalize_scope_id(user_id) if str(user_id or "").strip() else ""
        trusted_group_id = (
            ""
            if is_private or not str(group_id or "").strip()
            else _normalize_scope_id(group_id)
        )
        validated_updates = _trusted_turn_patches(
            emotion_updates,
            user_id=trusted_user_id,
            group_id=trusted_group_id,
            is_private=is_private,
        )
        timestamp = _format_timestamp(now)

        def _mutate(current: Any) -> dict[str, Any]:
            state = normalize_emotion_state_v2(current, now=now)
            bot_emotion = _normalize_text(
                getattr(semantic_frame, "bot_emotion", ""),
                limit=80,
            )
            confidence = _normalize_number(
                getattr(semantic_frame, "confidence", 0.0),
                minimum=0.0,
                maximum=1.0,
                field="confidence",
                default=0.0,
            )

            if trusted_user_id:
                existing_user = state["per_user"].get(trusted_user_id, {})
                user_record = _normalize_scoped_record(
                    existing_user,
                    fields=_USER_COMPATIBILITY_FIELDS,
                    updated_at_fallback=timestamp,
                )
                user_record.update(
                    {
                        "user_attitude": _normalize_text(
                            getattr(semantic_frame, "user_attitude", ""), limit=80
                        ),
                        "bot_emotion": bot_emotion,
                        "emotion_intensity": _normalize_text(
                            getattr(semantic_frame, "emotion_intensity", ""), limit=16
                        ),
                        "expression_style": _normalize_text(
                            getattr(semantic_frame, "expression_style", ""), limit=80
                        ),
                        "tts_style_hint": _normalize_text(
                            getattr(semantic_frame, "tts_style_hint", ""), limit=60
                        ),
                        "sticker_mood_hint": _normalize_text(
                            getattr(semantic_frame, "sticker_mood_hint", ""), limit=60
                        ),
                        "last_group_id": trusted_group_id,
                        "last_reply": _normalize_text(assistant_text, limit=120),
                        "updated_at": timestamp,
                    }
                )
                if bot_emotion in EMOTION_CATEGORIES:
                    user_record["category"] = bot_emotion
                    user_record["confidence"] = confidence
                user_bucket = dict(state["per_user"])
                user_bucket[trusted_user_id] = _normalize_scoped_record(
                    user_record,
                    fields=_USER_COMPATIBILITY_FIELDS,
                    updated_at_fallback=timestamp,
                )
                state["per_user"] = user_bucket

            if trusted_group_id:
                existing_group = state["per_group"].get(trusted_group_id, {})
                group_record = _normalize_scoped_record(
                    existing_group,
                    fields=_GROUP_COMPATIBILITY_FIELDS,
                    updated_at_fallback=timestamp,
                )
                group_record.update(
                    {
                        "group_climate": _normalize_text(
                            getattr(semantic_frame, "user_attitude", ""), limit=80
                        ),
                        "bot_social_posture": _normalize_text(
                            getattr(semantic_frame, "expression_style", ""), limit=80
                        ),
                        "bot_emotion": bot_emotion,
                        "emotion_intensity": _normalize_text(
                            getattr(semantic_frame, "emotion_intensity", ""), limit=16
                        ),
                        "last_user_id": trusted_user_id,
                        "updated_at": timestamp,
                    }
                )
                if bot_emotion in EMOTION_CATEGORIES:
                    group_record["category"] = bot_emotion
                    group_record["confidence"] = confidence
                group_bucket = dict(state["per_group"])
                group_bucket[trusted_group_id] = _normalize_scoped_record(
                    group_record,
                    fields=_GROUP_COMPATIBILITY_FIELDS,
                    updated_at_fallback=timestamp,
                )
                state["per_group"] = group_bucket

            state["updated_at"] = timestamp
            for patch in validated_updates:
                state = apply_emotion_patch(state, patch, now=now)
            return normalize_emotion_state_v2(state, now=now)

        updated = await self.store.mutate(STORE_NAME, _mutate)
        return materialize_emotion_state_v2(updated, now=now)

    async def _mirror_patch_to_v1(
        self,
        patch: EmotionPatch,
        state: dict[str, Any],
    ) -> None:
        compatibility = build_v1_compatibility_view(state, now=self._now())
        if patch.scope == "global":
            desired = compatibility["inner_state"]

            def _mirror_inner(current: Any) -> dict[str, Any]:
                merged = dict(current) if isinstance(current, Mapping) else {}
                merged.update(copy.deepcopy(desired))
                return merged

            await self.store.mutate(V1_INNER_STORE_NAME, _mirror_inner)
            return

        bucket_name = "per_user" if patch.scope == "user" else "per_group"
        desired_entry = compatibility["emotion_state"][bucket_name][patch.scope_id]

        def _mirror_emotion(current: Any) -> dict[str, Any]:
            merged = dict(current) if isinstance(current, Mapping) else {}
            bucket = dict(merged.get(bucket_name, {}) or {})
            previous = bucket.get(patch.scope_id)
            entry = dict(previous) if isinstance(previous, Mapping) else {}
            entry.update(copy.deepcopy(desired_entry))
            bucket[patch.scope_id] = entry
            merged[bucket_name] = bucket
            other_bucket = "per_group" if bucket_name == "per_user" else "per_user"
            if not isinstance(merged.get(other_bucket), Mapping):
                merged[other_bucket] = {}
            merged["updated_at"] = state["updated_at"]
            return merged

        await self.store.mutate(V1_EMOTION_STORE_NAME, _mirror_emotion)


def _trusted_turn_patches(
    raw_updates: Any,
    *,
    user_id: str,
    group_id: str,
    is_private: bool,
) -> list[EmotionPatch]:
    if not isinstance(raw_updates, list):
        return []
    trusted: list[EmotionPatch] = []
    seen_scopes: set[str] = set()
    for raw in raw_updates[:3]:
        if not isinstance(raw, Mapping):
            continue
        scope = str(raw.get("scope", "") or "").strip().lower()
        if scope not in EMOTION_SCOPES or scope in seen_scopes:
            continue
        if scope == "user" and not user_id:
            continue
        if scope == "group" and (is_private or not group_id):
            continue
        candidate = dict(raw)
        if scope == "global":
            candidate.pop("scope_id", None)
        elif scope == "user":
            candidate["scope_id"] = user_id
        else:
            candidate["scope_id"] = group_id
        try:
            trusted.append(EmotionPatch.from_mapping(candidate))
        except EmotionPatchValidationError:
            continue
        seen_scopes.add(scope)
    return trusted


async def load_emotion_state_v2(
    data_dir: Path | None = None,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    _ = data_dir
    clock = (lambda: now) if now is not None else None
    return await EmotionStateV2Service(clock=clock).load()


async def update_emotion_state_v2(
    data_dir: Path | None,
    patch: EmotionPatch | Mapping[str, Any],
    *,
    now: datetime | None = None,
    mirror_v1: bool = True,
) -> dict[str, Any]:
    _ = data_dir
    clock = (lambda: now) if now is not None else None
    service = EmotionStateV2Service(clock=clock, mirror_v1=mirror_v1)
    return await service.apply_patch(patch)


async def record_turn_emotion_state_v2(
    data_dir: Path | None,
    *,
    user_id: str,
    group_id: str = "",
    semantic_frame: Any,
    assistant_text: str = "",
    is_private: bool = False,
    emotion_updates: Any = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    _ = data_dir
    clock = (lambda: now) if now is not None else None
    return await EmotionStateV2Service(clock=clock, mirror_v1=False).record_turn(
        user_id=user_id,
        group_id=group_id,
        semantic_frame=semantic_frame,
        assistant_text=assistant_text,
        is_private=is_private,
        emotion_updates=emotion_updates,
    )


__all__ = [
    "ACTION_TENDENCIES",
    "APPRAISAL_TEXT_LIMIT",
    "DEFAULT_EMOTION_STATE_V2",
    "EMOTION_CATEGORIES",
    "EMOTION_SCOPES",
    "ENTRY_TTL_DAYS",
    "EmotionCompatibilityWriteError",
    "EmotionPatch",
    "EmotionPatchValidationError",
    "EmotionStateV2Error",
    "EmotionStateV2Service",
    "GLOBAL_HALF_LIFE_HOURS",
    "RELATION_HALF_LIFE_HOURS",
    "SCHEMA_VERSION",
    "STORE_NAME",
    "UnsupportedEmotionStateVersion",
    "apply_emotion_patch",
    "build_v1_compatibility_view",
    "default_emotion_state_v2",
    "load_emotion_state_v2",
    "materialize_emotion_record",
    "materialize_emotion_state_v2",
    "migrate_v1_emotion_state",
    "normalize_emotion_record",
    "normalize_emotion_state_v2",
    "record_turn_emotion_state_v2",
    "update_emotion_state_v2",
]
