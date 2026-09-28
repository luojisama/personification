from __future__ import annotations

import traceback
from typing import Any


def log_exception(
    logger: Any,
    context: str,
    exc: Exception,
    *,
    level: str = "warning",
    include_traceback: bool = False,
) -> None:
    if logger is None:
        return
    method = getattr(logger, level, None) or getattr(logger, "warning", None)
    if method is None:
        return
    try:
        method(f"{str(context or 'unexpected error').strip()}: {exc}")
    except Exception:
        return
    if include_traceback:
        debug = getattr(logger, "debug", None)
        if debug is not None:
            try:
                debug(traceback.format_exc())
            except Exception:
                return


__all__ = ["log_exception"]
