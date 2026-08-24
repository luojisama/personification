from __future__ import annotations

from types import SimpleNamespace

from personification.core.command_runtime_context import (
    build_command_runtime_context,
    ensure_runtime_command_prefix,
    has_runtime_command_prefix,
    render_command_runtime_prompt,
)


def test_slash_prefix_and_runtime_prompt() -> None:
    config = SimpleNamespace(command_start={"/"}, command_sep={"."})
    context = build_command_runtime_context(config)

    assert context.prefixes == ("/",)
    assert context.example() == "/天气 北京"
    assert has_runtime_command_prefix(" /天气 北京", config) is True
    assert "受信任配置" in render_command_runtime_prompt(config)


def test_bang_prefix_rewrites_stale_slash_example() -> None:
    config = SimpleNamespace(command_start={"!"}, command_sep={"."})

    assert ensure_runtime_command_prefix("/天气 北京", config) == "!天气 北京"
    assert ensure_runtime_command_prefix("!天气 北京", config) == "!天气 北京"
    assert has_runtime_command_prefix("/天气 北京", config) is False


def test_empty_prefix_does_not_mark_every_message_as_command() -> None:
    config = SimpleNamespace(command_start={""}, command_sep={"."})

    assert build_command_runtime_context(config).allows_empty_prefix is True
    assert ensure_runtime_command_prefix("/天气 北京", config) == "天气 北京"
    assert has_runtime_command_prefix("普通聊天", config) is False


def test_multiple_prefixes_are_deterministic() -> None:
    config = SimpleNamespace(command_start={"!!", "!", "/"}, command_sep={"::", "."})
    context = build_command_runtime_context(config)

    assert context.prefixes == ("!", "/", "!!")
    assert context.separators == (".", "::")
    assert ensure_runtime_command_prefix("天气 北京", config) == "!天气 北京"


def test_runtime_changes_are_read_without_cache() -> None:
    config = SimpleNamespace(command_start={"/"}, command_sep={"."})
    assert build_command_runtime_context(config).preferred_prefix == "/"
    config.command_start = {"!"}
    assert build_command_runtime_context(config).preferred_prefix == "!"
