"""Task-local experiment controls. Production always defaults to baseline A."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar

_variant: ContextVar[str] = ContextVar("quality_variant", default="A")
_surface: ContextVar[str] = ContextVar("quality_surface", default="group")
_direct: ContextVar[bool] = ContextVar("quality_direct", default=False)


def relaxed_style() -> bool:
    return _variant.get() in {"B", "C"}


@contextmanager
def quality_turn(*, surface: str, direct: bool):
    tokens = (_surface.set(surface), _direct.set(direct))
    try:
        yield
    finally:
        _direct.reset(tokens[1])
        _surface.reset(tokens[0])


def soft_length_target() -> tuple[int, int] | None:
    if _variant.get() != "C":
        return None
    if _surface.get() == "private":
        return (80, 400)
    return (40, 180) if _direct.get() else (20, 80)


@contextmanager
def quality_variant(variant: str, *, surface: str = "group", direct: bool = False):
    if variant not in {"A", "B", "C"} or surface not in {"group", "private"}:
        raise ValueError("invalid_quality_variant")
    tokens = (_variant.set(variant), _surface.set(surface), _direct.set(direct))
    try:
        yield
    finally:
        _direct.reset(tokens[2])
        _surface.reset(tokens[1])
        _variant.reset(tokens[0])


def review_style_preference() -> str:
    return (
        "\n表达实验：问句、反问、口癖、接梗和长短均为表达偏好，不能单独作为否决或改写依据。"
        "结合最近多轮表达是否重复及当前语境判断；身份、归属、权限、事实依据与必要安全检查仍是硬约束。"
    ) if relaxed_style() else ""
