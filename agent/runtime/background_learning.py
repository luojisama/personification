"""Lifecycle and diagnostics for optional per-turn active learning."""
from __future__ import annotations

import asyncio
from typing import Any

from ...core.active_learning import run_active_learning
from ...core.generation_fence import register_generation_task
from ...core.runtime_task_supervisor import runtime_task_supervisor


def start_active_learning_task(**kwargs: Any) -> None:
    owner = asyncio.current_task()
    if owner is None:
        raise RuntimeError("active learning requires a running owner task")
    task = runtime_task_supervisor.start_owned(owner, "active_learning", lambda: run_active_learning(**kwargs))
    register_generation_task(task)


__all__ = ["start_active_learning_task"]
