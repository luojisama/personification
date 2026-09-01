from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_peer_bot_observer_reload_keeps_plain_ai_caller_contract() -> None:
    tree = ast.parse((ROOT / "core" / "runtime_builder.py").read_text(encoding="utf-8"))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "set_call_ai_api"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "peer_bot_observer"
    ]
    assert len(calls) == 1
    assert len(calls[0].args) == 1
    assert isinstance(calls[0].args[0], ast.Name)
    assert calls[0].args[0].id == "lite_call_ai_api"
