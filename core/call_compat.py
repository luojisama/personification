"""Select documented legacy call signatures before executing any work."""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from inspect import signature
from typing import Any


def select_call_shape(
    call: Callable[..., Any],
    variants: Sequence[tuple[tuple[Any, ...], Mapping[str, Any]]],
) -> tuple[tuple[Any, ...], dict[str, Any]]:
    """Bind one supported signature without invoking the callable.

    Opaque callables receive only the canonical first variant. An exception
    raised during execution is never evidence for retrying another signature.
    """
    if not variants:
        raise ValueError("no call variants")
    try:
        sig = signature(call)
    except (TypeError, ValueError):
        args, kwargs = variants[0]
        return args, dict(kwargs)
    binding_error: TypeError | None = None
    for args, kwargs in variants:
        try:
            sig.bind(*args, **kwargs)
        except TypeError as exc:
            if binding_error is None:
                binding_error = exc
            continue
        return args, dict(kwargs)
    assert binding_error is not None
    raise binding_error
