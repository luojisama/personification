"""Packaged, deterministic media samples for explicit admin diagnostics.

The expected observations are deliberately server-only.  Public catalog
metadata and prompts never contain those answers or local resource names.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


_SAMPLE_ROOT = Path(__file__).with_name("diagnostic_media")


@dataclass(frozen=True, slots=True)
class DiagnosticMediaSample:
    sample_id: str
    kind: str
    capability: str
    mime_type: str
    suffix: str
    relative_name: str
    size_bytes: int
    sha256: str

    @property
    def path(self) -> Path:
        return _SAMPLE_ROOT / self.relative_name

    def public_metadata(self) -> dict[str, Any]:
        return {
            "sample_id": self.sample_id,
            "kind": self.kind,
            "mime_type": self.mime_type,
            "size_bytes": self.size_bytes,
            "integrity": "sha256_size_mime",
            "description": "服务器内置的确定性无版权媒体样例，用于内容理解验真。",
        }


_SAMPLES: dict[str, DiagnosticMediaSample] = {
    "audio-ascending-v1": DiagnosticMediaSample(
        sample_id="audio-ascending-v1",
        kind="audio",
        capability="audio_input",
        mime_type="audio/wav",
        suffix=".wav",
        relative_name="audio-ascending-v1.wav",
        size_bytes=46_764,
        sha256="2ed58c5a38002440b094ce29e01e72aed86c21c020ae8e03940beae2b72f064d",
    ),
    "video-rgb-v1": DiagnosticMediaSample(
        sample_id="video-rgb-v1",
        kind="video",
        capability="video_input",
        mime_type="video/mp4",
        suffix=".mp4",
        relative_name="video-rgb-v1.mp4",
        size_bytes=4_142,
        sha256="ad5408f638f4a9a32e54127a0501cfd8171f676dd3c95b577198609f9871ae71",
    ),
}

_DEFAULT_BY_CAPABILITY = {
    "audio_input": "audio-ascending-v1",
    "video_input": "video-rgb-v1",
}

_PROBE_PROMPTS = {
    "audio": (
        "请仅依据随请求提供的音频返回一个严格 JSON 对象，不要附加解释。"
        '格式为 {"segment_count":整数,"pitch_trend":"ascending|descending|flat|mixed"}。'
        "统计可清楚分开的声音片段，并判断整体音高走势。"
    ),
    "video": (
        "请仅依据随请求提供的视频返回一个严格 JSON 对象，不要附加解释。"
        '格式为 {"scene_count":整数,"colors":[小写英文颜色名],"shapes":[小写英文形状名]}。'
        "按时间顺序列出每个主要场景的背景色和中央白色图形。"
    ),
}

_EXPECTED = {
    "audio-ascending-v1": {
        "segment_count": 3,
        "pitch_trend": "ascending",
    },
    "video-rgb-v1": {
        "scene_count": 3,
        "colors": ["red", "green", "blue"],
        "shapes": ["circle", "square", "triangle"],
    },
}


def get_diagnostic_media_sample(capability: str, sample_id: str = "") -> DiagnosticMediaSample | None:
    normalized_capability = str(capability or "").strip().lower()
    resolved_id = str(sample_id or "").strip() or _DEFAULT_BY_CAPABILITY.get(normalized_capability, "")
    sample = _SAMPLES.get(resolved_id)
    if sample is None or sample.capability != normalized_capability:
        return None
    return sample


def validate_diagnostic_media_sample(sample: DiagnosticMediaSample) -> tuple[bool, str]:
    try:
        stat = sample.path.stat()
        if not sample.path.is_file() or stat.st_size != sample.size_bytes:
            return False, "builtin_sample_integrity_failed"
        payload = sample.path.read_bytes()
    except OSError:
        return False, "builtin_sample_integrity_failed"
    if hashlib.sha256(payload).hexdigest() != sample.sha256:
        return False, "builtin_sample_integrity_failed"
    if sample.kind == "audio":
        magic_ok = len(payload) >= 12 and payload[:4] == b"RIFF" and payload[8:12] == b"WAVE"
        size_ok = sample.size_bytes <= 256 * 1024
        metadata_ok = sample.mime_type == "audio/wav" and sample.suffix == ".wav"
    else:
        magic_ok = len(payload) >= 8 and payload[4:8] == b"ftyp"
        size_ok = sample.size_bytes <= 128 * 1024
        metadata_ok = sample.mime_type == "video/mp4" and sample.suffix == ".mp4"
    return (True, "builtin_sample_integrity_verified") if magic_ok and size_ok and metadata_ok else (
        False,
        "builtin_sample_integrity_failed",
    )


def diagnostic_media_prompt(sample: DiagnosticMediaSample) -> str:
    return _PROBE_PROMPTS[sample.kind]


def _json_object(raw: Any) -> dict[str, Any] | None:
    text = str(raw or "").strip()
    if not text or len(text) > 2_000:
        return None
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 3 and lines[-1].strip() == "```":
            text = "\n".join(lines[1:-1]).strip()
            if text.casefold().startswith("json\n"):
                text = text[5:].strip()
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def score_diagnostic_media_response(sample: DiagnosticMediaSample, raw: Any) -> bool:
    parsed = _json_object(raw)
    expected = _EXPECTED[sample.sample_id]
    if parsed is None or set(parsed) != set(expected):
        return False
    if sample.kind == "audio":
        return (
            type(parsed.get("segment_count")) is int
            and parsed.get("segment_count") == expected["segment_count"]
            and str(parsed.get("pitch_trend") or "").strip().lower() == expected["pitch_trend"]
        )
    colors = parsed.get("colors")
    shapes = parsed.get("shapes")
    return (
        type(parsed.get("scene_count")) is int
        and parsed.get("scene_count") == expected["scene_count"]
        and isinstance(colors, list)
        and [str(item).strip().lower() for item in colors] == expected["colors"]
        and isinstance(shapes, list)
        and [str(item).strip().lower() for item in shapes] == expected["shapes"]
    )


def score_custom_media_transport_response(raw: Any) -> bool | None:
    """Parse the upload probe's deliberately narrow transport acknowledgement.

    ``True`` proves only that the selected provider accepted and decoded the
    submitted media.  It is not semantic-understanding evidence.  ``False`` is
    an explicit rejection; malformed or conversational output stays
    inconclusive as ``None``.
    """

    parsed = _json_object(raw)
    if parsed is None or set(parsed) != {"media_input_accepted"}:
        return None
    accepted = parsed.get("media_input_accepted")
    return accepted if type(accepted) is bool else None


def diagnostic_media_catalog_metadata(capability: str) -> dict[str, Any]:
    sample = get_diagnostic_media_sample(capability)
    return sample.public_metadata() if sample is not None else {}


__all__ = [
    "DiagnosticMediaSample",
    "diagnostic_media_catalog_metadata",
    "diagnostic_media_prompt",
    "get_diagnostic_media_sample",
    "score_custom_media_transport_response",
    "score_diagnostic_media_response",
    "validate_diagnostic_media_sample",
]
