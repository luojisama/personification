"""Tests for new gemini-cli / claude-code tool caller routing."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pytest

from plugin.personification.core import ai_routes, provider_router
from plugin.personification.skills.skillpacks.tool_caller.scripts import impl as caller_impl
from plugin.personification.skills.skillpacks.image_gen.scripts.main import (
    build_image_gen_nanobanan_tool,
    build_image_gen_tool,
)


@dataclass
class _DummyConfig:
    personification_api_type: str = "openai"
    personification_api_key: str = ""
    personification_api_url: str = ""
    personification_model: str = ""
    personification_thinking_mode: str = "none"
    personification_codex_auth_path: str = ""
    personification_gemini_cli_auth_path: str = ""
    personification_gemini_cli_project: str = ""
    personification_claude_code_auth_path: str = ""
    personification_api_pools: object = None


class _Logger:
    def info(self, *_args, **_kwargs) -> None:
        return None

    def warning(self, *_args, **_kwargs) -> None:
        return None

    def error(self, *_args, **_kwargs) -> None:
        return None


def test_normalize_api_type_gemini_cli_aliases() -> None:
    assert caller_impl._normalize_api_type("gemini_cli") == "gemini_cli"
    assert caller_impl._normalize_api_type("gemini-cli") == "gemini_cli"
    assert caller_impl._normalize_api_type("GEMINICLI") == "gemini_cli"


def test_normalize_api_type_claude_code_aliases() -> None:
    assert caller_impl._normalize_api_type("claude_code") == "claude_code"
    assert caller_impl._normalize_api_type("claude-code") == "claude_code"
    assert caller_impl._normalize_api_type("ClaudeCode") == "claude_code"
    assert caller_impl._normalize_api_type("claude_cli") == "claude_code"


def test_normalize_api_type_keeps_existing_routes() -> None:
    assert caller_impl._normalize_api_type("openai") == "openai"
    assert caller_impl._normalize_api_type("anthropic") == "anthropic"
    assert caller_impl._normalize_api_type("gemini_official") == "gemini_official"
    assert caller_impl._normalize_api_type("openai_codex") == "openai_codex"
    assert caller_impl._normalize_api_type("codex") == "openai_codex"


def test_build_tool_caller_returns_gemini_cli_instance() -> None:
    cfg = _DummyConfig(
        personification_api_type="gemini_cli",
        personification_model="gemini-3.1-pro-preview",
    )
    caller = caller_impl.build_tool_caller(cfg)
    assert isinstance(caller, caller_impl.GeminiCliToolCaller)
    assert caller.model == "gemini-3.1-pro-preview"


def test_build_tool_caller_returns_claude_code_instance() -> None:
    cfg = _DummyConfig(
        personification_api_type="claude_code",
        personification_model="claude-opus-4-7",
    )
    caller = caller_impl.build_tool_caller(cfg)
    assert isinstance(caller, caller_impl.ClaudeCodeToolCaller)
    assert caller.model == "claude-opus-4-7"


def test_provider_router_accepts_cli_legacy_routes_without_api_key() -> None:
    gemini_cfg = _DummyConfig(
        personification_api_type="gemini_cli",
        personification_model="gemini-3.1-pro-preview",
        personification_gemini_cli_auth_path="C:/tmp/gemini.json",
        personification_gemini_cli_project="cloud-project",
    )
    gemini_providers = provider_router.get_configured_api_providers(gemini_cfg, _Logger())
    assert gemini_providers[0]["api_type"] == "gemini_cli"
    assert gemini_providers[0]["auth_path"] == "C:/tmp/gemini.json"
    assert gemini_providers[0]["project"] == "cloud-project"

    claude_cfg = _DummyConfig(
        personification_api_type="claude_code",
        personification_model="claude-opus-4-7",
        personification_claude_code_auth_path="C:/tmp/claude.json",
    )
    claude_providers = provider_router.get_configured_api_providers(claude_cfg, _Logger())
    assert claude_providers[0]["api_type"] == "claude_code"
    assert claude_providers[0]["auth_path"] == "C:/tmp/claude.json"


def test_provider_router_accepts_cli_pool_routes_without_api_key() -> None:
    cfg = _DummyConfig(
        personification_api_pools=(
            '[{"name":"local-gemini","api_type":"gemini_cli","model":"gemini-3.1-pro-preview",'
            '"auth_path":"C:/tmp/gemini.json","project":"cloud-project"},'
            '{"name":"local-claude","api_type":"claude_code","model":"claude-opus-4-7",'
            '"auth_path":"C:/tmp/claude.json"}]'
        )
    )
    providers = provider_router.get_configured_api_providers(cfg, _Logger())
    assert [item["api_type"] for item in providers] == ["gemini_cli", "claude_code"]
    assert providers[0]["project"] == "cloud-project"


def test_routed_config_proxy_passes_cli_auth_fields_to_tool_caller() -> None:
    base = _DummyConfig()
    gemini_proxy = ai_routes._ProviderConfigProxy(
        base,
        {
            "api_type": "gemini_cli",
            "model": "gemini-3.1-pro-preview",
            "auth_path": "C:/tmp/gemini.json",
            "project": "cloud-project",
        },
    )
    gemini_caller = caller_impl.build_tool_caller(gemini_proxy)
    assert isinstance(gemini_caller, caller_impl.GeminiCliToolCaller)
    assert gemini_caller.auth_path_override == "C:/tmp/gemini.json"
    assert gemini_caller.project_override == "cloud-project"

    claude_proxy = ai_routes._ProviderConfigProxy(
        base,
        {
            "api_type": "claude_code",
            "model": "claude-opus-4-7",
            "auth_path": "C:/tmp/claude.json",
        },
    )
    claude_caller = caller_impl.build_tool_caller(claude_proxy)
    assert isinstance(claude_caller, caller_impl.ClaudeCodeToolCaller)
    assert claude_caller.auth_path_override == "C:/tmp/claude.json"


@dataclass
class _DummyRuntime:
    plugin_config: object
    tool_caller: object


@dataclass
class _RuntimeConfig:
    personification_image_gen_enabled: bool = True
    personification_image_gen_model: str = "gpt-image-2"
    personification_image_gen_nanobanan_model: str = "gemini-3-pro-image-preview"
    personification_image_gen_timeout: int = 180


def test_nanobanan_tool_only_on_gemini_cli_route() -> None:
    cfg = _RuntimeConfig()
    gemini_caller = caller_impl.GeminiCliToolCaller(model="gemini-3.1-pro-preview")
    runtime = _DummyRuntime(plugin_config=cfg, tool_caller=gemini_caller)
    nano_tool = build_image_gen_nanobanan_tool(runtime)
    codex_tool = build_image_gen_tool(runtime)
    assert nano_tool is not None and nano_tool.name == "generate_image_nanobanan"
    # Codex 路由的 generate_image 不应该在 gemini-cli caller 上激活
    assert codex_tool is None


def test_nanobanan_tool_disabled_on_openai_route() -> None:
    cfg = _RuntimeConfig()
    openai_caller = caller_impl.OpenAIToolCaller(
        api_key="sk-test",
        base_url="",
        model="gpt-4o-mini",
        thinking_mode="none",
    )
    runtime = _DummyRuntime(plugin_config=cfg, tool_caller=openai_caller)
    assert build_image_gen_nanobanan_tool(runtime) is None
