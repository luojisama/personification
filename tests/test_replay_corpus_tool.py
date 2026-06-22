"""replay_corpus 工具与样本的 smoke 测试。

- 验证至少存在 cold-start 数量的样本
- 验证 jsonl 解析正常
- 验证 metadata_fallback_turn_plan 接入路径无报错
"""
from __future__ import annotations

import json
from pathlib import Path

from ._loader import load_personification_module

_CORPUS_DIR = Path(__file__).parent / "replay_corpus"


def _load_planner():
    return load_personification_module("plugin.personification.agent.runtime.planner")


def test_replay_corpus_has_min_samples() -> None:
    files = list(_CORPUS_DIR.glob("*.jsonl"))
    assert len(files) >= 3, "至少需要 3 个回放文件（群聊/私聊/QZone）"

    total_lines = 0
    for f in files:
        with open(f, "r", encoding="utf-8") as fp:
            for line in fp:
                if line.strip():
                    total_lines += 1
    assert total_lines >= 15, f"冷启动样本不少于 15 段，当前 {total_lines}"


def test_replay_corpus_jsonl_valid() -> None:
    for f in _CORPUS_DIR.glob("*.jsonl"):
        with open(f, "r", encoding="utf-8") as fp:
            for line_no, raw in enumerate(fp, start=1):
                raw = raw.strip()
                if not raw:
                    continue
                data = json.loads(raw)
                assert isinstance(data, dict), f"{f.name}:{line_no} 不是对象"
                assert "scene" in data, f"{f.name}:{line_no} 缺少 scene"
                assert "expected_frame" in data, f"{f.name}:{line_no} 缺少 expected_frame"
                assert "metadata" in data, f"{f.name}:{line_no} 缺少 metadata"


def test_metadata_fallback_executes() -> None:
    planner = _load_planner()
    plan = planner.metadata_fallback_turn_plan(
        is_group=True,
        is_random_chat=False,
        is_direct_mention=True,
        has_images=False,
        message_target="bot",
    )
    assert plan.reply_action in {"reply", "silence", "ask_clarify"}
    assert plan.output_mode in planner.ALLOWED_OUTPUT_MODES
