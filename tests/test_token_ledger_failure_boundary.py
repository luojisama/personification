from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest

from ._loader import load_personification_module

ledger = load_personification_module("plugin.personification.core.token_ledger")


def _response():
    return SimpleNamespace(model_used="m", usage={"prompt_tokens": 2, "completion_tokens": 1})


def test_ledger_storage_error_reports_type_and_returns_false(monkeypatch, caplog) -> None:  # noqa: ANN001
    def failed(**_kwargs):
        raise sqlite3.OperationalError("private database path")

    monkeypatch.setattr(ledger, "record_llm_call", failed)
    assert ledger.record_response_usage(_response()) is False
    assert "OperationalError" in caplog.text
    assert "private database path" not in caplog.text


def test_ledger_internal_typeerror_propagates(monkeypatch) -> None:  # noqa: ANN001
    def failed(**_kwargs):
        raise TypeError("internal ledger defect")

    monkeypatch.setattr(ledger, "record_llm_call", failed)
    with pytest.raises(TypeError, match="internal ledger defect"):
        ledger.record_response_usage(_response())
