import asyncio
import pytest
from types import SimpleNamespace
from plugin.personification.handlers.event_rules import split_segment_if_long
from plugin.personification.core.message_splitter import (
    split_reply_with_llm,
    _parse_splitter_json_output,
)


def test_split_segment_if_long_no_hard_cut():
    # 测试在标点符号处切分，绝不在词语内部硬切
    text = "而且她们都在开开心心地吃着甜点，我平时也就打打游戏，哪会有这么可爱的朋友……"
    segments = split_segment_if_long(text, max_chars=20)
    # 验证没有孤立的一个字或残破片段
    assert len(segments) >= 2
    for seg in segments:
        assert len(seg) <= 25
        assert seg != "地吃着甜点……"
    # 拼装后核心文字完整
    combined = "".join(segments)
    assert "开开心心地吃着甜点" in combined


def test_parse_splitter_json_output():
    # 正常 JSON 数组
    assert _parse_splitter_json_output('["消息一", "消息二"]') == ["消息一", "消息二"]
    # 包含 Markdown 代码块包裹
    markdown_wrapped = '```json\n["第一条", "第二条"]\n```'
    assert _parse_splitter_json_output(markdown_wrapped) == ["第一条", "第二条"]
    # 包含前后杂质
    noisy = '好的，这是分段：\n["你好啊！", "今天天气真不错~"]\n希望能帮到你'
    assert _parse_splitter_json_output(noisy) == ["你好啊！", "今天天气真不错~"]
    # 异常输入
    assert _parse_splitter_json_output("不是 JSON") is None


def test_split_reply_with_llm_short_text_bypass():
    async def run():
        runtime = SimpleNamespace(
            plugin_config=SimpleNamespace(
                personification_enable_llm_splitter=True,
                personification_splitter_min_chars=35,
                personification_splitter_max_segments=3,
            ),
            logger=None,
            lite_tool_caller=None,
            agent_tool_caller=None,
        )
        short_text = "这是一条很短的回复。"
        res = await split_reply_with_llm(short_text, runtime)
        assert res == [short_text]

    asyncio.run(run())


def test_split_reply_with_llm_disabled_uses_fallback():
    async def run():
        runtime = SimpleNamespace(
            plugin_config=SimpleNamespace(
                personification_enable_llm_splitter=False,
                personification_max_segment_chars=0,
            ),
            logger=None,
        )
        text = "第一段文字\n\n第二段文字"
        res = await split_reply_with_llm(text, runtime)
        assert res == ["第一段文字", "第二段文字"]

    asyncio.run(run())


def test_split_reply_with_llm_success():
    async def run():
        class DummyCaller:
            async def chat_with_tools(self, messages, tools, stream):
                return SimpleNamespace(content='["呜……中、中间那个……虽然长得有点像", "但我才没有那么可爱呢……！"]')

        runtime = SimpleNamespace(
            plugin_config=SimpleNamespace(
                personification_enable_llm_splitter=True,
                personification_splitter_min_chars=20,
                personification_splitter_max_segments=3,
                personification_splitter_provider="",
                personification_splitter_model="",
            ),
            logger=None,
            lite_tool_caller=DummyCaller(),
        )
        long_text = "呜……中、中间那个……虽然长得有点像，但我才没有那么可爱呢……！"
        res = await split_reply_with_llm(long_text, runtime)
        assert len(res) == 2
        assert "长得有点像" in res[0]
        assert "才没有那么可爱" in res[1]

    asyncio.run(run())


def test_split_reply_with_llm_timeout_fallback():
    async def run():
        class HangingCaller:
            async def chat_with_tools(self, messages, tools, stream):
                await asyncio.sleep(10.0)
                return SimpleNamespace(content='["test"]')

        runtime = SimpleNamespace(
            plugin_config=SimpleNamespace(
                personification_enable_llm_splitter=True,
                personification_splitter_min_chars=10,
                personification_splitter_max_segments=3,
                personification_max_segment_chars=0,
                personification_splitter_provider="",
                personification_splitter_model="",
            ),
            logger=None,
            lite_tool_caller=HangingCaller(),
        )
        text = "这是一段很长的文字用来测试超时降级逻辑。超时后应该降级到规则分段。"
        res = await split_reply_with_llm(text, runtime, timeout_seconds=0.1)
        assert len(res) >= 1
        assert "测试超时降级逻辑" in res[0]

    asyncio.run(run())
