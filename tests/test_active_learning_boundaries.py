from __future__ import annotations

import asyncio
import sqlite3
from types import SimpleNamespace

import pytest

from ._loader import load_personification_module

learning = load_personification_module("plugin.personification.core.active_learning")


class Store:
    def __init__(self, *, read_error: Exception | None = None, write_error: Exception | None = None):
        self.read_error = read_error
        self.write_error = write_error
        self.items: list[dict] = []

    def get_memory_item(self, _key):  # noqa: ANN001
        if self.read_error:
            raise self.read_error
        return None

    def write_memory_item(self, item):  # noqa: ANN001
        if self.write_error:
            raise self.write_error
        self.items.append(item)


def _run(store: Store, output: str, *, call_error: Exception | None = None) -> tuple[bool, int]:
    calls = 0

    class Caller:
        async def chat_with_tools(self, **_kwargs):
            nonlocal calls
            calls += 1
            if call_error:
                raise call_error
            return SimpleNamespace(content=output)

    result = asyncio.run(learning.run_active_learning(
        tool_caller=Caller(), memory_store=store, uncertainty_notes=["unknown"], group_id="g",
        research_followup_query="q", plugin_config=SimpleNamespace(personification_active_learning_enabled=True),
    ))
    return result, calls


def test_quota_read_failure_prevents_model_request(caplog) -> None:  # noqa: ANN001
    store = Store(read_error=sqlite3.OperationalError("private path"))
    assert _run(store, '{"learned_fact":"fact","confidence":0.5}')[0] is False
    assert store.items == []
    assert "OperationalError" in caplog.text and "private path" not in caplog.text


@pytest.mark.parametrize("output", ["[]", '"scalar"', "null", "{}"])
def test_non_object_model_result_does_not_write_memory(output: str) -> None:
    store = Store()
    assert _run(store, output)[0] is False
    assert store.items == []


def test_quota_write_failure_prevents_fact_write() -> None:
    store = Store(write_error=sqlite3.OperationalError("quota write unavailable"))
    assert _run(store, '{"learned_fact":"fact","confidence":0.5}')[0] is False
    assert store.items == []


def test_model_internal_typeerror_is_not_silent() -> None:
    with pytest.raises(TypeError, match="internal model wrapper defect"):
        _run(Store(), "", call_error=TypeError("internal model wrapper defect"))
