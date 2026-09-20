from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Sequence


PromptStability = Literal["stable", "dynamic"]


@dataclass(frozen=True, slots=True)
class PromptSegment:
    """A model-visible prompt block annotated without changing its wire shape."""

    role: str
    content: str
    stability: PromptStability
    source: str


@dataclass(frozen=True, slots=True)
class PromptSegmentReport:
    segment_count: int
    stable_count: int
    dynamic_count: int
    stable_prefix_available: bool
    stable_prefix_contiguous: bool
    first_dynamic_index: int | None
    first_noncontiguous_stable_index: int | None
    preceding_messages: Literal["none", "unknown"]


def validate_prompt_segments(segments: Sequence[PromptSegment]) -> PromptSegmentReport:
    """Validate annotations and report only facts about the supplied segments.

    Existing messages that precede these segments are deliberately reported as
    unknown.  This helper does not infer whether any provider will cache them.
    """

    first_dynamic: int | None = None
    first_late_stable: int | None = None
    stable_count = 0
    for index, segment in enumerate(segments):
        if segment.role not in {"system", "user"}:
            raise ValueError(f"prompt segment {index} must keep role=system or role=user")
        if segment.role == "user" and segment.stability != "dynamic":
            raise ValueError(f"prompt segment {index} with role=user must be dynamic")
        if not isinstance(segment.content, str) or not segment.content:
            raise ValueError(f"prompt segment {index} must have non-empty content")
        if segment.stability not in {"stable", "dynamic"}:
            raise ValueError(f"prompt segment {index} has invalid stability")
        if not segment.source.strip():
            raise ValueError(f"prompt segment {index} must have a source")
        if segment.stability == "dynamic":
            if first_dynamic is None:
                first_dynamic = index
        else:
            stable_count += 1
            if first_dynamic is not None and first_late_stable is None:
                first_late_stable = index

    count = len(segments)
    return PromptSegmentReport(
        segment_count=count,
        stable_count=stable_count,
        dynamic_count=count - stable_count,
        stable_prefix_available=bool(segments and segments[0].stability == "stable"),
        stable_prefix_contiguous=first_late_stable is None,
        first_dynamic_index=first_dynamic,
        first_noncontiguous_stable_index=first_late_stable,
        preceding_messages="none",
    )


def with_preceding_messages(
    report: PromptSegmentReport, *, had_preceding_messages: bool
) -> PromptSegmentReport:
    return PromptSegmentReport(
        segment_count=report.segment_count,
        stable_count=report.stable_count,
        dynamic_count=report.dynamic_count,
        stable_prefix_available=report.stable_prefix_available,
        stable_prefix_contiguous=report.stable_prefix_contiguous,
        first_dynamic_index=report.first_dynamic_index,
        first_noncontiguous_stable_index=report.first_noncontiguous_stable_index,
        preceding_messages="unknown" if had_preceding_messages else "none",
    )


def require_contiguous_stable_prefix(segments: Sequence[PromptSegment]) -> PromptSegmentReport:
    """Enforce the stable-first invariant at a future reordering boundary."""

    report = validate_prompt_segments(segments)
    if not report.stable_prefix_contiguous:
        raise ValueError(
            "stable prompt segments must form one contiguous prefix; "
            f"first late stable segment is {report.first_noncontiguous_stable_index}"
        )
    return report


__all__ = [
    "PromptSegment",
    "PromptSegmentReport",
    "PromptStability",
    "validate_prompt_segments",
    "require_contiguous_stable_prefix",
    "with_preceding_messages",
]
