from __future__ import annotations

import asyncio
import importlib
import sys
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from ._loader import load_personification_module


def _load_si_submodule(suffix: str):
    return load_personification_module(
        f"plugin.personification.flows.social_intelligence.{suffix}"
    )


# 预加载 data_store（quota 模块用相对 import 引用），保证 sys.modules 有它
ds_mod = load_personification_module("plugin.personification.core.data_store")
framework = _load_si_submodule("framework")
quota = _load_si_submodule("quota")
gate = _load_si_submodule("gate")


# ========== framework ==========

def test_register_and_list_triggers() -> None:
    framework.clear_social_triggers_for_testing()
    t1 = framework.SocialTrigger(
        name="x", handler=lambda ctx: None, schedule_kind="cron", schedule_args={"hour": 8}
    )
    t2 = framework.SocialTrigger(
        name="y", handler=lambda ctx: None, schedule_kind="interval", schedule_args={"minutes": 30}
    )
    framework.register_social_trigger(t1)
    framework.register_social_trigger(t2)
    names = sorted(t.name for t in framework.list_social_triggers())
    assert names == ["x", "y"]


def test_register_same_name_overwrites() -> None:
    framework.clear_social_triggers_for_testing()
    framework.register_social_trigger(
        framework.SocialTrigger("a", lambda ctx: None, "cron", {"hour": 1})
    )
    framework.register_social_trigger(
        framework.SocialTrigger("a", lambda ctx: None, "cron", {"hour": 9})
    )
    triggers = framework.list_social_triggers()
    assert len(triggers) == 1
    assert triggers[0].schedule_args == {"hour": 9}


# ========== quota ==========

@pytest.fixture(autouse=True)
def _stub_data_store(monkeypatch):
    """用内存 dict 替换 data_store；避免污染真实库。"""
    store_data: dict[str, Any] = {}

    class _Stub:
        def load_sync(self, name):
            return store_data.get(name)

        def save_sync(self, name, data):
            store_data[name] = data

    monkeypatch.setattr(ds_mod, "get_data_store", lambda: _Stub())
    yield


def test_quota_default_zero_means_exceeded() -> None:
    assert quota.is_quota_exceeded(
        "u1", scenario="x", daily_quota_per_user=0, cooldown_seconds=0
    ) is True


def test_quota_empty_user_id_treated_as_exceeded() -> None:
    assert quota.is_quota_exceeded(
        "", scenario="x", daily_quota_per_user=5, cooldown_seconds=0
    ) is True


def test_quota_within_limit_allows() -> None:
    assert quota.is_quota_exceeded(
        "u2", scenario="x", daily_quota_per_user=2, cooldown_seconds=0
    ) is False


def test_quota_mark_sent_then_increments() -> None:
    quota.mark_sent("u3", scenario="x", now=1000.0)
    assert quota.is_quota_exceeded(
        "u3", scenario="x", daily_quota_per_user=1, cooldown_seconds=0, now=1001.0
    ) is True
    assert quota.is_quota_exceeded(
        "u3", scenario="x", daily_quota_per_user=5, cooldown_seconds=0, now=1001.0
    ) is False


def test_quota_cooldown_enforced() -> None:
    quota.mark_sent("u4", scenario="morning", now=10_000.0)
    # 在冷却内
    assert quota.is_quota_exceeded(
        "u4",
        scenario="morning",
        daily_quota_per_user=5,
        cooldown_seconds=3600,
        now=10_100.0,
    ) is True
    # 冷却过后
    assert quota.is_quota_exceeded(
        "u4",
        scenario="morning",
        daily_quota_per_user=5,
        cooldown_seconds=3600,
        now=10_000.0 + 3700.0,
    ) is False


def test_quota_different_scenarios_independent_cooldown() -> None:
    quota.mark_sent("u5", scenario="morning", now=20_000.0)
    # 同用户但 evening scenario 没发过，冷却不应阻塞
    assert quota.is_quota_exceeded(
        "u5",
        scenario="evening",
        daily_quota_per_user=5,
        cooldown_seconds=3600,
        now=20_100.0,
    ) is False


# ========== gate ==========

def test_gate_returns_allow_when_no_tool_caller() -> None:
    allow, rewritten, _reason = asyncio.run(
        gate.gate_should_send(
            tool_caller=None, logger=MagicMock(), scenario="x", user_id="u",
            draft="hi", persona_snippet="", now_str=""
        )
    )
    assert allow is True
    assert rewritten is None


def test_gate_default_allow_on_exception() -> None:
    bad_caller = MagicMock()

    async def boom(*args, **kwargs):
        raise RuntimeError("upstream down")

    bad_caller.chat_with_tools = boom
    allow, _, reason = asyncio.run(
        gate.gate_should_send(
            tool_caller=bad_caller, logger=MagicMock(), scenario="x", user_id="u",
            draft="hi", persona_snippet="", now_str=""
        )
    )
    assert allow is True
    assert "failed" in reason


def test_gate_parses_plain_json() -> None:
    fake = MagicMock()

    async def chat(messages, tools, use_builtin_search):
        resp = MagicMock()
        resp.content = '{"allow": false, "reason": "用户睡觉", "rewritten": ""}'
        return resp

    fake.chat_with_tools = chat
    allow, rewritten, reason = asyncio.run(
        gate.gate_should_send(
            tool_caller=fake, logger=MagicMock(), scenario="x", user_id="u",
            draft="hi", persona_snippet="", now_str=""
        )
    )
    assert allow is False
    assert rewritten is None
    assert reason == "用户睡觉"


def test_gate_parses_json_with_markdown_fence() -> None:
    fake = MagicMock()

    async def chat(messages, tools, use_builtin_search):
        resp = MagicMock()
        resp.content = '```json\n{"allow": true, "reason": "ok", "rewritten": "你好啊"}\n```'
        return resp

    fake.chat_with_tools = chat
    allow, rewritten, _reason = asyncio.run(
        gate.gate_should_send(
            tool_caller=fake, logger=MagicMock(), scenario="x", user_id="u",
            draft="嗨", persona_snippet="", now_str=""
        )
    )
    assert allow is True
    assert rewritten == "你好啊"


def test_gate_parses_json_inside_prose() -> None:
    fake = MagicMock()

    async def chat(messages, tools, use_builtin_search):
        resp = MagicMock()
        resp.content = '思考后我觉得：{"allow": true, "reason": "时机ok", "rewritten": ""}'
        return resp

    fake.chat_with_tools = chat
    allow, rewritten, _reason = asyncio.run(
        gate.gate_should_send(
            tool_caller=fake, logger=MagicMock(), scenario="x", user_id="u",
            draft="hi", persona_snippet="", now_str=""
        )
    )
    assert allow is True
    assert rewritten is None


# ========== news_push ==========

news_push = _load_si_submodule("scenarios.news_push")


def test_string_list_parses_list_and_csv_and_empty() -> None:
    assert news_push._string_list(["123", "456"]) == ["123", "456"]
    assert news_push._string_list("11,22, 33") == ["11", "22", "33"]
    assert news_push._string_list("") == []
    assert news_push._string_list(None) == []
    assert news_push._string_list(["", " ", "x"]) == ["x"]


def test_news_push_early_returns_when_master_switch_off() -> None:
    ctx = MagicMock()
    ctx.plugin_config.personification_social_intelligence_enabled = False
    asyncio.run(news_push.news_push_handler(ctx))
    # 总开关关闭：不应触发 bot 调用或 LLM 调用
    ctx.get_bots.assert_not_called()
    ctx.tool_caller.chat_with_tools.assert_not_called()


def test_news_push_early_returns_when_scenario_disabled() -> None:
    ctx = MagicMock()
    ctx.plugin_config.personification_social_intelligence_enabled = True
    ctx.plugin_config.personification_social_news_enabled = False
    asyncio.run(news_push.news_push_handler(ctx))
    ctx.get_bots.assert_not_called()
