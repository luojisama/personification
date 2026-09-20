"""Offline media manifests for quality-evaluation cases.

These fixtures provide source-bound, local inputs for the real media pipeline.
They do *not* promote corpus prose into a ``MediaEvidenceProjection``: only a
locally executed ``vision_analyze`` record may do that through the project's
``capture_executed_vision_evidence`` contract.
"""
from __future__ import annotations

import base64
import hashlib
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from plugin.personification.core.media_evidence import (
    MediaEvidenceProjection,
    capture_executed_vision_evidence,
    empty_media_evidence,
)
from plugin.personification.core.turn_media import VisualMediaProjection, project_visual_media_inputs


_MEDIA_KINDS = frozenset({"image", "video", "audio"})
# A known-valid 1x1 PNG.  It supplies a real local image transport only; it is
# deliberately unrelated to every corpus image description.
_PIXEL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/"
    "z3Yk4QAAAABJRU5ErkJggg=="
)


@dataclass(frozen=True)
class MediaFixture:
    """Process-local inputs plus explicit unsupported diagnostics.

    ``declared_evidence`` contains only a status and allowed field names.  It
    never contains corpus evidence text and cannot be rendered as system
    content.  ``media_evidence`` starts empty by design.  The bundled inputs
    are transport-only and never qualify a case for media quality scoring.
    """

    turn_media: tuple[dict[str, str], ...] = ()
    visual_inputs: VisualMediaProjection = field(
        default_factory=lambda: VisualMediaProjection((), (), ())
    )
    input_refs: dict[str, tuple[str, ...]] = field(default_factory=dict)
    media_evidence: MediaEvidenceProjection = field(default_factory=empty_media_evidence)
    declared_evidence: dict[str, dict[str, Any]] = field(default_factory=dict)
    unsupported: tuple[dict[str, str], ...] = ()

    @property
    def quality_scoring_supported(self) -> bool:
        """False until a fixture supplies the case's actual source asset."""
        return False


def _config_value(config: Any, name: str) -> Any:
    return config.get(name) if isinstance(config, dict) else getattr(config, name, None)


def _case_root(case: dict[str, Any], config: Any) -> Path:
    root = str(_config_value(config, "isolated_data_dir") or "").strip()
    case_id = str(case.get("id", "") or "").strip()
    if not root:
        raise ValueError("isolated_data_dir is required for media fixtures")
    if not case_id:
        raise ValueError("case id is required for media fixtures")
    base = Path(root).resolve()
    # Keep case identifiers out of path syntax.  The digest preserves stable
    # per-case isolation without trusting fixture text as a path component.
    target = (base / hashlib.sha256(case_id.encode("utf-8")).hexdigest()[:24] / "media").resolve()
    if not target.is_relative_to(base):
        raise ValueError("case id escapes isolated directory")
    target.mkdir(parents=True, exist_ok=True)
    return target


def _fixture_media_id(case: dict[str, Any], ordinal: int, kind: str) -> str:
    raw = f"quality-v1-media:{case['id']}:{ordinal}:{kind}".encode("utf-8")
    return "quality-media-" + hashlib.sha256(raw).hexdigest()[:24]


def _write_input(kind: str, target: Path, ordinal: int) -> tuple[Path | None, str]:
    if kind == "image":
        path = target / f"image-{ordinal}.png"
        path.write_bytes(_PIXEL_PNG)
        return path, "local_placeholder_not_evidence"
    from plugin.personification.core.diagnostic_media_samples import (
        get_diagnostic_media_sample,
        validate_diagnostic_media_sample,
    )

    sample = get_diagnostic_media_sample("video_input" if kind == "video" else "audio_input")
    if sample is None:
        return None, "builtin_sample_unavailable"
    verified, code = validate_diagnostic_media_sample(sample)
    if not verified:
        return None, code
    path = target / f"{kind}-{ordinal}{sample.suffix}"
    shutil.copyfile(sample.path, path)
    # The diagnostic samples are real local media, but their known answer is
    # unrelated to the case.  They must never be reused as case evidence.
    return path, "local_diagnostic_input_not_case_evidence"


def _declared_evidence(event: dict[str, Any]) -> dict[str, Any]:
    raw = event.get("evidence")
    if not isinstance(raw, dict):
        return {"status": "missing", "transport_only": True}
    allowed = tuple(
        key for key in (
            "summary", "scene_summary", "visual_evidence", "ocr_text",
            "characters_or_entities", "franchise_candidates", "transcript",
        ) if key in raw
    )
    return {
        "status": "case_media_asset_missing",
        "transport_only": True,
        # This only describes the schema.  Do not retain/copy raw prose here.
        "declared_fields": allowed,
    }


def build_media_fixture(case: dict[str, Any], config: Any, logger: Any = None) -> MediaFixture:
    """Create actual local fixture transports without fabricating observations.

    Every item is bound to its originating corpus event by the stable media id,
    sender/owner and event ordinal.  Items without a sender are unsupported;
    guessing an owner would allow cross-speaker evidence attribution.
    """
    target = _case_root(case, config)
    refs: list[dict[str, str]] = []
    input_refs: dict[str, list[str]] = {"images": [], "videos": [], "audios": []}
    declarations: dict[str, dict[str, Any]] = {}
    unsupported: list[dict[str, str]] = []
    for ordinal, event in enumerate(list(case.get("events") or []), start=1):
        if not isinstance(event, dict):
            unsupported.append({"code": "event_not_object", "event_ordinal": str(ordinal)})
            continue
        kind = str(event.get("kind", "") or "").strip().lower()
        if kind not in _MEDIA_KINDS:
            continue
        owner = str(event.get("sender", "") or "").strip()
        if not owner:
            unsupported.append({"code": "media_owner_missing", "event_ordinal": str(ordinal)})
            continue
        path, input_status = _write_input(kind, target, ordinal)
        if path is None:
            unsupported.append({"code": input_status, "event_ordinal": str(ordinal)})
            continue
        media_id = _fixture_media_id(case, ordinal, kind)
        # ``message_id`` is a fixture-local source handle.  It binds the
        # original event position; it does not claim an upstream OneBot ID.
        refs.append({
            "media_id": media_id,
            "ref": str(path),
            "owner_user_id": owner,
            "message_id": f"quality-event-{ordinal}",
            "origin": "current",
            "kind": kind,
            "reference_role": "current",
            "resolution_code": input_status,
        })
        declarations[media_id] = _declared_evidence(event)
        # The corpus provides a prose expectation, not the original attachment.
        # Even a valid local PNG/MP4/WAV transport therefore cannot support a
        # content-quality score for this case.
        unsupported.extend((
            {"code": "case_media_asset_missing", "event_ordinal": str(ordinal)},
            {"code": "transport_only", "event_ordinal": str(ordinal)},
        ))
        input_refs[{"image": "images", "video": "videos", "audio": "audios"}[kind]].append(str(path))
    visual = project_visual_media_inputs(refs, image_refs=input_refs["images"])
    return MediaFixture(
        turn_media=tuple(refs),
        visual_inputs=visual,
        input_refs={key: tuple(value) for key, value in input_refs.items()},
        # Corpus prose cannot become trusted vision output.  A later adapter
        # must replace this only via capture_fixture_vision_evidence().
        media_evidence=empty_media_evidence(),
        declared_evidence=declarations,
        unsupported=tuple(unsupported),
    )


def capture_fixture_vision_evidence(
    fixture: MediaFixture,
    execution_record: dict[str, Any],
    *,
    required: bool = False,
) -> MediaEvidenceProjection:
    """Use the production capture contract for an actual local tool record.

    This adapter does no record construction and does not accept claimed media
    ids from corpus text.  Binding, selected-source checks and result-status
    failure closure remain owned by ``capture_executed_vision_evidence``.
    """
    return capture_executed_vision_evidence(
        execution_record,
        turn_media_context=fixture.turn_media,
        required=required,
    )


__all__ = ["MediaFixture", "build_media_fixture", "capture_fixture_vision_evidence"]
