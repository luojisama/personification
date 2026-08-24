"""Tests for the cleanup path that prevents thinking-chain XML from leaking to users."""
from __future__ import annotations

import pytest

from ._loader import load_personification_module

context_policy = load_personification_module("plugin.personification.core.context_policy")
yaml_parser = load_personification_module("plugin.personification.flows.yaml_parser")

has_silence_control_marker = context_policy.has_silence_control_marker
strip_response_control_markers = context_policy.strip_response_control_markers
sanitize_history_text = context_policy.sanitize_history_text
visible_output = load_personification_module("plugin.personification.core.visible_output")
parse_yaml_response = yaml_parser.parse_yaml_response


def test_strip_full_status_think_action_block() -> None:
    raw = (
        "<status>心情: \"平静\" 状态: \"旁听中\" 记忆: \"\" 动作: \"\"</status>\n"
        "<think>Step 1: 安全性 OK\nStep 2: 跳过</think>\n"
        "<action></action>\n"
        "<output>\n<message>晚上好呀</message>\n</output>"
    )
    cleaned = strip_response_control_markers(raw)
    assert "心情" not in cleaned, cleaned
    assert "Step 1" not in cleaned, cleaned
    assert "晚上好呀" in cleaned


def test_strip_raw_visible_step_reasoning_block_keeps_final_reply() -> None:
    raw = (
        "轻松\n\n"
        "Step 1: 安全性 - 只是普通闲聊。\n\n"
        "Step 2: 作息 - 关闭，跳过。\n\n"
        "Step 3: 上下文 - 接上一句即可。\n\n"
        "Step 4: 角色语气 - 短句。\n\n"
        "Step 5: 重复检查 - 无。\n\n"
        "轻松"
    )
    cleaned = strip_response_control_markers(raw)
    assert cleaned == "轻松"
    assert "Step 1" not in cleaned


def test_strip_raw_visible_step_reasoning_block_uses_final_label() -> None:
    raw = "步骤 1：先判断\n步骤 2：再检查\n最终回复：在"
    assert strip_response_control_markers(raw) == "在"


def test_strip_orphan_control_bracket() -> None:
    assert strip_response_control_markers("[") == ""
    assert strip_response_control_markers("[\n收到") == "收到"


def test_strip_unclosed_think_block_does_not_leak_inner_text() -> None:
    # 截断/未闭合 <think>，内部"心情..."不应漏出
    raw = "<think>心情: \"困\" 状态: \"在偷玩手机\" 记忆: \"\" 动作: \"打哈欠\"\n"
    cleaned = strip_response_control_markers(raw)
    assert "心情" not in cleaned, cleaned
    assert "状态" not in cleaned, cleaned


def test_strip_keeps_plain_text() -> None:
    raw = "晚上好呀"
    assert strip_response_control_markers(raw) == "晚上好呀"


def test_strip_keeps_inner_message_when_only_message_tag_present() -> None:
    raw = "<output><message>就这样</message></output>"
    cleaned = strip_response_control_markers(raw)
    assert "就这样" in cleaned
    assert "<message>" not in cleaned
    assert "<output>" not in cleaned


def test_strip_drops_silence_marker() -> None:
    raw = "<output><message>[SILENCE]</message></output>"
    assert has_silence_control_marker(raw) is True
    cleaned = strip_response_control_markers(raw)
    assert "[SILENCE]" not in cleaned
    assert cleaned == ""


def test_parse_yaml_response_extracts_multiple_messages() -> None:
    raw = (
        "<status>...</status>"
        "<output>"
        "<message>第一条</message>"
        "<message>第二条</message>"
        "</output>"
    )
    parsed = parse_yaml_response(raw)
    texts = [m["text"] for m in parsed["messages"]]
    assert texts == ["第一条", "第二条"]


def test_parse_yaml_response_falls_back_when_no_output_wrapper() -> None:
    raw = "<status>x</status><message>裸 message</message>"
    parsed = parse_yaml_response(raw)
    texts = [m["text"] for m in parsed["messages"]]
    assert texts == ["裸 message"]


def test_parse_yaml_response_empty_messages_for_plain_text() -> None:
    raw = "晚上好呀"
    parsed = parse_yaml_response(raw)
    assert parsed["messages"] == []


def test_strip_extra_attributes_on_tags() -> None:
    raw = '<status type="auto" id="1">...</status><output><message quote="msg-1">嗨</message></output>'
    cleaned = strip_response_control_markers(raw)
    assert "嗨" in cleaned
    assert "type" not in cleaned
    assert "quote" not in cleaned


def test_strip_plain_state_block_leak_sample() -> None:
    # 用户抓到的真实泄露样本：模型格式漂移，裸文本状态块无 <status> 包裹
    raw = (
        "状态:\n"
        "心情: 嫌弃\n"
        "状态: 在家\n"
        "记忆: AG man喜欢乱赖人\n"
        "动作:\n"
    )
    cleaned = strip_response_control_markers(raw)
    assert cleaned == ""
    assert "心情" not in cleaned
    assert "记忆" not in cleaned


def test_strip_plain_state_block_inside_reply_keeps_reply() -> None:
    raw = (
        "别乱赖我啊\n"
        "心情: \"平静\"\n状态: \"在家\"\n记忆: \"\"\n动作: \"发呆\"\n"
        "你自己看啊"
    )
    cleaned = strip_response_control_markers(raw)
    assert "心情" not in cleaned, cleaned
    assert "别乱赖我啊" in cleaned
    assert "你自己看啊" in cleaned


def test_strip_plain_state_block_quoted_and_fullwidth() -> None:
    raw = '心情: "平静"\n状态: "在家"\n记忆: ""\n动作: "看手机"'
    assert strip_response_control_markers(raw) == ""
    raw_fw = "状态：\n心情：嫌弃\n状态：在家\n记忆：空\n动作："
    assert strip_response_control_markers(raw_fw) == ""


def test_strip_keeps_normal_chat_mentioning_state_keywords() -> None:
    raw = "我现在状态不太好，心情: 有点烦"
    assert strip_response_control_markers(raw) == raw
    only_two = "状态:\n心情: 不错"
    assert strip_response_control_markers(only_two) == only_two.strip()
    with_reply = "刚才在工作，状态还在线\n"  # 只有一行状态键值，保留
    assert strip_response_control_markers(with_reply) == with_reply.strip()


def test_strip_keeps_non_contiguous_normal_state_lines() -> None:
    raw = "心情: 有点烦\n但我还是会把事情做完\n状态: 慢慢恢复\n今天早点睡\n动作: 先去洗澡"
    assert strip_response_control_markers(raw) == raw


@pytest.mark.parametrize(
    "surface",
    ["normal_reply", "yaml_reply", "agent_action_text", "qzone_post", "qzone_comment", "qzone_forward"],
)
def test_shared_visible_output_gate_strips_plain_state_blocks(surface: str) -> None:
    raw = "能看到的回复\n心情: 平静\n状态: 在家\n记忆: 无\n动作: 看手机"
    assert visible_output.guard_visible_text(raw, surface=surface) == "能看到的回复"


def test_history_gate_strips_plain_state_blocks() -> None:
    raw = "会写入历史的回复\n心情: 平静\n状态: 在家\n记忆: 无\n动作: 看手机"
    assert sanitize_history_text(raw) == "会写入历史的回复"
