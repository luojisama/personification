"""Background work belongs to its turn and cannot outlive that turn's fence."""

from __future__ import annotations

import asyncio
from unittest.mock import Mock

import pytest

from ._loader import load_personification_module


supervisor_mod = load_personification_module("plugin.personification.core.runtime_task_supervisor")
fence = load_personification_module("plugin.personification.core.generation_fence")


def test_owned_tasks_isolate_owners_and_cancel_only_their_children() -> None:
    async def scenario() -> None:
        supervisor = supervisor_mod.RuntimeTaskSupervisor()
        owner_a_gate = asyncio.Event()
        owner_b_gate = asyncio.Event()
        child_a_gate = asyncio.Event()
        child_b_gate = asyncio.Event()
        owner_a = asyncio.create_task(owner_a_gate.wait())
        owner_b = asyncio.create_task(owner_b_gate.wait())
        child_a = supervisor.start_owned(owner_a, "learn", child_a_gate.wait)
        child_b = supervisor.start_owned(owner_b, "learn", child_b_gate.wait)
        assert child_a is not child_b
        assert supervisor.snapshot()["owned_active"] == 2

        owner_a.cancel()
        await asyncio.gather(owner_a, return_exceptions=True)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert child_a.cancelled()
        assert not child_b.done()
        assert supervisor.snapshot()["owned_active"] == 1

        child_b_gate.set()
        await child_b
        await asyncio.sleep(0)
        owner_b_gate.set()
        await owner_b
        assert supervisor.snapshot()["owned_active"] == 0

    asyncio.run(scenario())


def test_generation_invalidation_stops_late_write_after_ignored_cancellation() -> None:
    async def scenario() -> None:
        supervisor = supervisor_mod.RuntimeTaskSupervisor()
        owner_gate = asyncio.Event()
        owner = asyncio.create_task(owner_gate.wait())
        write_gate = asyncio.Event()
        writes: list[str] = []
        state: dict = {}
        token = fence.bind_generation(state)
        try:
            async def stubborn_work() -> None:
                try:
                    await write_gate.wait()
                except asyncio.CancelledError:
                    # Provider cancellation is advisory; the local fence must
                    # still prevent a late result from committing.
                    pass
                fence.assert_current_generation(state)
                writes.append("late")

            task = supervisor.start_owned(owner, "learning", stubborn_work)
            fence.register_generation_task(task)
            await asyncio.sleep(0)
            assert fence.invalidate_generation(state)
            write_gate.set()
            with pytest.raises(fence.SupersededGeneration):
                await task
            assert writes == []
            await asyncio.sleep(0)
            assert supervisor.snapshot()["owned_active"] == 0
        finally:
            fence.reset_generation(token)
            owner_gate.set()
            await owner

    asyncio.run(scenario())


def test_shutdown_drains_owned_tasks_and_completed_records_do_not_accumulate() -> None:
    async def scenario() -> None:
        supervisor = supervisor_mod.RuntimeTaskSupervisor()
        owner_gate = asyncio.Event()
        owner = asyncio.create_task(owner_gate.wait())
        completed = [supervisor.start_owned(owner, f"quick-{index}", lambda: asyncio.sleep(0)) for index in range(25)]
        await asyncio.gather(*completed)
        await asyncio.sleep(0)
        assert supervisor.snapshot()["owned_active"] == 0
        assert supervisor._owned == {}

        hanging_gate = asyncio.Event()
        hanging = supervisor.start_owned(owner, "hanging", hanging_gate.wait)
        await asyncio.sleep(0)
        await supervisor.shutdown(timeout=0.2)
        assert hanging.cancelled()
        assert supervisor.snapshot()["owned_active"] == 0
        owner_gate.set()
        await owner

    asyncio.run(scenario())


def test_owned_async_failure_is_counted_and_does_not_retain_task(monkeypatch) -> None:  # noqa: ANN001
    counters: list[tuple[str, str]] = []
    monkeypatch.setattr(
        supervisor_mod.metrics, "record_counter",
        lambda name, **labels: counters.append((name, labels.get("task", ""))),
    )

    async def scenario() -> None:
        supervisor = supervisor_mod.RuntimeTaskSupervisor()
        logger = Mock()
        supervisor.configure(logger=logger)
        owner_gate = asyncio.Event()
        owner = asyncio.create_task(owner_gate.wait())

        async def failing() -> None:
            raise RuntimeError("opaque provider detail")

        task = supervisor.start_owned(owner, "learning", failing)
        with pytest.raises(RuntimeError, match="opaque provider detail"):
            await task
        await asyncio.sleep(0)
        assert supervisor.snapshot()["failed_total"] == 1
        assert supervisor.snapshot()["owned_active"] == 0
        assert counters == [("runtime_task_failed_total", "learning")]
        logger.warning.assert_called_once()
        assert "opaque provider detail" not in repr(logger.warning.call_args)
        owner_gate.set()
        await owner

    asyncio.run(scenario())
