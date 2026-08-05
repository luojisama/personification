from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from ._loader import load_personification_module


def test_browser_pool_accepts_isolated_qwen_platform(tmp_path: Path) -> None:
    browser_mod = load_personification_module("plugin.personification.native_mcp.social_research.browser")
    pool = browser_mod.BrowserPool(
        tmp_path,
        platforms=("qwen_web",),
        task_name_prefix="qwen-web-browser",
    )
    assert pool.platforms == ("qwen_web",)
    assert pool.profile_dir("qwen_web") == (tmp_path / "profiles" / "qwen_web").resolve()
    with pytest.raises(ValueError, match="unsupported platform"):
        pool.profile_dir("douyin")


def test_qwen_helper_exposes_no_agent_tools_and_does_not_launch_browser(tmp_path: Path) -> None:
    compat = load_personification_module("plugin.personification.skill_runtime.mcp_compat")
    project_root = Path(__file__).resolve().parents[2]
    entrypoint = Path(__file__).resolve().parents[1] / "core" / "qwen_web_entrypoint.py"

    async def run() -> tuple[list[dict], dict]:
        async with compat.McpStdioClient(
            command=sys.executable,
            args=[str(entrypoint)],
            env={**os.environ, "PERSONIFICATION_QWEN_WEB_ROOT": str(tmp_path / "qwen-web")},
            cwd=str(project_root),
            timeout=8,
        ) as client:
            tools = await client.list_tools()
            status = await client.request("personification/qwen-web/status", {})
            return tools, status

    tools, status = asyncio.run(run())
    assert tools == []
    assert status["state"] == "login_required"
    assert status["browser_running"] is False
    assert status["active_job"] is False
    assert status["page_contract_version"] == "qianwen_cn_v1"


def test_qwen_service_disabled_status_never_starts_helper(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    service_mod = load_personification_module("plugin.personification.core.qwen_web_service")
    service = service_mod.QwenWebService(tmp_path)
    config = SimpleNamespace(
        personification_qwen_web_enabled=False,
        personification_qwen_web_risk_acknowledged=False,
    )

    async def forbidden(_config):  # noqa: ANN001, ANN202
        raise AssertionError("disabled status must not start the helper")

    monkeypatch.setattr(service, "_ensure_client", forbidden)
    status = asyncio.run(service.status(config, refresh=True))
    assert status["state"] == "disabled"
    assert status["last_diagnostic_code"] == "qwen_web_disabled"
    assert status["browser_running"] is False


def test_qwen_runtime_detects_network_security_risk_without_interacting(tmp_path: Path) -> None:
    runtime_mod = load_personification_module("plugin.personification.core.qwen_web_runtime")

    class Locator:
        @property
        def first(self):  # noqa: ANN201
            return self

        async def inner_text(self, timeout: int = 0) -> str:
            del timeout
            return "当前网络环境可能存在安全风险，请稍后再试"

    class Page:
        def locator(self, selector: str) -> Locator:
            assert selector == "body"
            return Locator()

    runtime = runtime_mod.QwenWebRuntime(tmp_path)
    state, code = asyncio.run(runtime._page_state(Page()))
    assert state == "manual_verification_required"
    assert code == "qwen_web_network_risk_detected"
    allowed, limited_code = runtime._automatic_allowed()
    assert allowed is False
    assert limited_code == "qwen_web_network_risk_cooldown"


def test_qwen_runtime_rejects_paths_instead_of_media_tokens(tmp_path: Path) -> None:
    runtime_mod = load_personification_module("plugin.personification.core.qwen_web_runtime")
    runtime = runtime_mod.QwenWebRuntime(tmp_path)
    with pytest.raises(ValueError, match="qwen_web_media_token_invalid"):
        runtime._resolve_media_token(str(tmp_path / "video.mp4"))
    with pytest.raises(ValueError, match="qwen_web_media_token_invalid"):
        runtime._resolve_media_token("job_" + "a" * 31 + "../")


def test_qwen_runtime_applies_conservative_local_rate_limit(tmp_path: Path) -> None:
    runtime_mod = load_personification_module("plugin.personification.core.qwen_web_runtime")
    runtime = runtime_mod.QwenWebRuntime(tmp_path)
    runtime._recent_jobs.append(time.time())
    allowed, code = runtime._automatic_allowed()
    assert allowed is False
    assert code == "qwen_web_local_rate_limited"
