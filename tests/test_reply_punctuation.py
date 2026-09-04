from __future__ import annotations

from ._loader import load_personification_module


reply_punctuation = load_personification_module("plugin.personification.core.reply_punctuation")


def test_strip_common_keeps_punctuation_only_micro_replies() -> None:
    for text in ("？", "？？", "！", "！！", "……"):
        assert reply_punctuation.apply_terminal_punctuation_policy(text) == text


def test_strip_common_keeps_existing_ordinary_text_behavior() -> None:
    assert reply_punctuation.apply_terminal_punctuation_policy("你好！") == "你好"
    assert reply_punctuation.apply_terminal_punctuation_policy("你好？") == "你好？"
    assert reply_punctuation.apply_terminal_punctuation_policy("（你好！）") == "（你好！）"
