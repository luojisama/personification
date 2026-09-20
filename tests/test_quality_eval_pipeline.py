from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from scripts.quality_eval.pipeline_adapter import run_full_path_case
from tests._loader import load_personification_module

db = load_personification_module("plugin.personification.core.db")


class _LoopbackCaller:
    """A no-network model double that exposes what the real processors saw."""
    def __init__(self) -> None:
        self.calls: list[list[dict]] = []

    async def chat_with_tools(self, messages, _tools, _search):  # noqa: ANN001
        self.calls.append([*list(messages), {"_tool_count": len(_tools)}])
        system = "\n".join(str(item.get("content", "")) for item in messages if item.get("role") == "system")
        if _tools:
            content = "我在，先把重点说清楚。"
        elif "speech_act" in system:
            content = '{"reply_action":"reply","speech_act":"answer","memory_need":"none","research_need":"none","vision_need":"none","output_mode":"chat_short","tool_intent":[],"ambiguity_level":"low","message_target":"bot","confidence":0.9,"reason":"直接回答"}'
        elif "JSON" in system or "独立复核" in system:
            content = '{"action":"accept","text":"","flags":[],"persona_verdict":"consistent"}'
        else:
            content = "<output><message>我在，先把重点说清楚。</message></output>"
        return SimpleNamespace(content=content, tool_calls=[], finish_reason="stop", raw={})


def _case(surface: str, pipeline: str = "normal") -> dict:
    return {"id": f"{surface}-{pipeline}", "surface": surface, "pipeline": pipeline,
        "events": [{"kind": "mention" if surface == "group" else "message", "sender": "小明", "text": "帮我看看这个问题", "message_id": "m1"}],
        "trusted_persona": "你是自然、不端着的群友。", "seed": {"group_window": [{"message_id": "old", "user_id": "u2", "text": "之前的上下文", "source_kind": "user"}]}}


def test_full_path_private_normal_uses_public_processor_and_persona(tmp_path: Path) -> None:
    caller = _LoopbackCaller()
    db.init_db_sync(tmp_path)
    try:
        result = asyncio.run(run_full_path_case(_case("private"), caller=caller, isolated_dir=str(tmp_path)))
    finally:
        asyncio.run(db.close_db())
    assert result["coverage"] == "normal_public_wrapper+multi_turn"
    assert result["turns"][0]["input"] == "帮我看看这个问题"
    assert result["status"] == "completed"
    assert result["send_attempt_count"] == 1 and result["reply"]
    assert result["turns"][0]["input"] == "帮我看看这个问题"
    assert any("自然、不端着" in str(message) for call in caller.calls for message in call)


def test_full_path_group_yaml_routes_from_normal_and_uses_group_source(tmp_path: Path) -> None:
    caller = _LoopbackCaller()
    db.init_db_sync(tmp_path)
    try:
        result = asyncio.run(run_full_path_case(_case("group", "yaml"), caller=caller, isolated_dir=str(tmp_path)))
    finally:
        asyncio.run(db.close_db())
    assert result["coverage"] == "normal_public_wrapper+yaml_public_wrapper+multi_turn"
    assert result["status"] == "completed" and result["send_attempt_count"] >= 1 and result["reply"]
    assert result["turns"][0]["mode"] == "yaml"
    assert any("之前的上下文" in str(message) for call in caller.calls for message in call)


def test_unknown_receipt_never_confirms_history(tmp_path: Path) -> None:
    caller = _LoopbackCaller(); case = _case("group", "yaml"); case["synthetic_receipt"] = "unknown"
    db.init_db_sync(tmp_path)
    try:
        result = asyncio.run(run_full_path_case(case, caller=caller, isolated_dir=str(tmp_path)))
    finally:
        asyncio.run(db.close_db())
    assert result["confirmed_history"] == 0
    assert result["status"] == "unknown" and result["send_attempt_count"] == 1 and result["generated"]
