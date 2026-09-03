from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _registered_call(path: str) -> list[ast.Call]:
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "register_peer_bot_tools"
    ]


def test_normal_and_yaml_register_the_same_peer_bot_tools() -> None:
    normal = _registered_call("handlers/reply_pipeline/pipeline_context.py")
    yaml = _registered_call("handlers/yaml_pipeline/processor.py")
    assert len(normal) == 1
    assert len(yaml) == 1
    required = {
        "bot",
        "event",
        "registry",
        "tracker",
        "plugin_config",
        "qq_outbound_ledger",
        "record_group_msg",
        "turn_state",
        "logger",
    }
    assert {item.arg for item in normal[0].keywords} == required
    assert {item.arg for item in yaml[0].keywords} == required
