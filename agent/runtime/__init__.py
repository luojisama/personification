"""Lazy compatibility exports for Agent runtime entry points.

Importing this package is part of the planner/review dependency path.  Eagerly
importing ``runner`` here used to pull reply quality back into response review
while that module was only half initialized.  Keep the established public
entry points, but load the heavyweight loop only when it is actually used.
"""
from __future__ import annotations

import importlib
from typing import Any


__all__ = ["AgentResult", "run_agent"]


def __getattr__(name: str) -> Any:
    if name == "AgentResult":
        from .final_synthesis import AgentResult

        return AgentResult
    # Explicit imports of historic runner attributes remain compatible without
    # making wildcard imports a new public API surface.
    if not name.startswith("__"):
        runner = importlib.import_module(f"{__name__}.runner")

        try:
            return getattr(runner, name)
        except AttributeError:
            pass
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
