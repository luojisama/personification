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
    assert status["page_contract_version"] == "qianwen_cn_v2"


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


def test_qwen_service_disabled_probe_does_not_start_helper(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    service_mod = load_personification_module("plugin.personification.core.qwen_web_service")
    service = service_mod.QwenWebService(tmp_path)
    config = SimpleNamespace(
        personification_qwen_web_enabled=False,
        personification_qwen_web_risk_acknowledged=True,
    )

    async def forbidden(_config):  # noqa: ANN001, ANN202
        raise AssertionError("disabled probe must not start the helper")

    monkeypatch.setattr(service, "_ensure_client", forbidden)
    with pytest.raises(RuntimeError, match="qwen_web_disabled"):
        asyncio.run(service.probe(config))


def test_qwen_logout_remains_available_after_feature_is_disabled(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    service_mod = load_personification_module("plugin.personification.core.qwen_web_service")
    service = service_mod.QwenWebService(tmp_path)
    config = SimpleNamespace(
        personification_qwen_web_enabled=False,
        personification_qwen_web_risk_acknowledged=False,
    )

    class _Client:
        async def request(self, method: str, params: dict) -> dict:  # noqa: ANN001
            assert method == "personification/qwen-web/logout"
            assert params == {}
            return {"state": "login_required", "profile_present": False}

    async def client(_config):  # noqa: ANN001, ANN202
        return _Client()

    monkeypatch.setattr(service, "_ensure_client", client)
    result = asyncio.run(service.logout(config))

    assert result["state"] == "disabled"
    assert result["profile_present"] is False


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


def test_qwen_network_risk_cooldown_blocks_manual_reopen(tmp_path: Path) -> None:
    runtime_mod = load_personification_module("plugin.personification.core.qwen_web_runtime")

    class _Browser:
        async def start_interactive_auth(self, *_args, **_kwargs):  # noqa: ANN002, ANN003, ANN202
            raise AssertionError("risk cooldown must not reopen the official page")

    runtime = runtime_mod.QwenWebRuntime(tmp_path)
    runtime.browser = _Browser()
    runtime._risk_blocked_until = time.time() + 600

    result = asyncio.run(runtime.auth_start("admin-owner"))

    assert result["status"] == "risk_controlled"
    assert result["error_code"] == "qwen_web_network_risk_cooldown"
    assert result["interactive_available"] is False


def test_qwen_runtime_forwards_auth_input_to_shared_interactive_action(tmp_path: Path) -> None:
    runtime_mod = load_personification_module("plugin.personification.core.qwen_web_runtime")
    captured: list[tuple[str, str, dict]] = []

    class _Browser:
        async def interactive_action(
            self,
            session_id: str,
            owner: str,
            action: dict,
        ) -> dict:
            captured.append((session_id, owner, dict(action)))
            return {"action_applied": True, "interactive_pointer_active": False}

    runtime = runtime_mod.QwenWebRuntime(tmp_path)
    runtime.browser = _Browser()
    action = {
        "type": "pointer_start",
        "gesture_id": "gesture_test",
        "seq": 0,
        "x": 320,
        "y": 180,
    }

    result = asyncio.run(runtime.auth_input("session-1", "admin-owner", action))

    assert result["action_applied"] is True
    assert captured == [("session-1", "admin-owner", action)]


def test_qwen_probe_reuses_recent_result_without_page_access(tmp_path: Path) -> None:
    runtime_mod = load_personification_module("plugin.personification.core.qwen_web_runtime")

    class _Browser:
        _auth = {}

        def runtime_status(self):  # noqa: ANN201
            return {"open_contexts": [], "activity": {}, "diagnostics": []}

        def profile_dir(self, _platform):  # noqa: ANN001, ANN201
            return tmp_path / "missing-profile"

        async def page(self, *_args, **_kwargs):  # noqa: ANN002, ANN003, ANN202
            raise AssertionError("recent probe must not touch the consumer site")

    runtime = runtime_mod.QwenWebRuntime(tmp_path)
    runtime.browser = _Browser()
    runtime._last_probe_at = time.time()
    runtime._state = "ready"

    result = asyncio.run(runtime.probe())

    assert result["state"] == "ready"
    assert result["last_probe_at"] > 0


def test_qwen_upload_stops_immediately_when_page_reports_network_risk(tmp_path: Path) -> None:
    runtime_mod = load_personification_module("plugin.personification.core.qwen_web_runtime")
    media = tmp_path / "video.mp4"
    media.write_bytes(b"video")

    class _Locator:
        def __init__(self, selector: str) -> None:
            self.selector = selector
            self.first = self

        async def is_visible(self, timeout: int = 0) -> bool:
            del timeout
            return self.selector == 'input[type="file"]'

        async def set_input_files(self, value: str, timeout: int = 0) -> None:
            del timeout
            assert value == str(media)

        async def inner_text(self, timeout: int = 0) -> str:
            del timeout
            assert self.selector == "body"
            return "当前网络环境可能存在安全风险"

    class _Page:
        def locator(self, selector: str) -> _Locator:
            return _Locator(selector)

        async def wait_for_timeout(self, _milliseconds: int) -> None:
            return None

    runtime = runtime_mod.QwenWebRuntime(tmp_path)

    with pytest.raises(RuntimeError, match="qwen_web_network_risk_detected"):
        asyncio.run(
            runtime._upload_media(
                _Page(),
                media,
                kind="video",
                timeout_seconds=30,
                upload=_Locator('input[type="file"]'),
            )
        )
    assert runtime._risk_blocked_until > time.time()


def test_qwen_logged_out_shell_is_not_mistaken_for_ready(tmp_path: Path) -> None:
    runtime_mod = load_personification_module("plugin.personification.core.qwen_web_runtime")

    class _Locator:
        def __init__(self, *, visible: bool = False, text: str = "") -> None:
            self.first = self
            self.visible = visible
            self.text = text

        async def is_visible(self, timeout: int = 0) -> bool:
            del timeout
            return self.visible

        async def inner_text(self, timeout: int = 0) -> str:
            del timeout
            return self.text

    class _Page:
        def locator(self, selector: str) -> _Locator:
            if selector == "body":
                return _Locator(text="你好，我是千问 向千问提问 音视频速读")
            return _Locator(
                visible=selector
                in {
                    'button:text-is("登录")',
                    '[contenteditable="true"][role="textbox"]',
                    'button[aria-label="音视频速读"]',
                }
            )

    runtime = runtime_mod.QwenWebRuntime(tmp_path)
    state, code = asyncio.run(runtime._page_state(_Page()))

    assert state == "login_required"
    assert code == "qwen_web_login_required"


def test_qwen_ready_requires_composer_and_real_media_entry(tmp_path: Path) -> None:
    runtime_mod = load_personification_module("plugin.personification.core.qwen_web_runtime")

    class _Locator:
        def __init__(self, *, visible: bool = False, text: str = "") -> None:
            self.first = self
            self.visible = visible
            self.text = text

        async def is_visible(self, timeout: int = 0) -> bool:
            del timeout
            return self.visible

        async def inner_text(self, timeout: int = 0) -> str:
            del timeout
            return self.text

    class _Page:
        def locator(self, selector: str) -> _Locator:
            if selector == "body":
                return _Locator(text="你好，我是千问")
            return _Locator(
                visible=selector
                in {
                    '[contenteditable="true"][role="textbox"]',
                    'button[aria-label="音视频速读"]',
                }
            )

    runtime = runtime_mod.QwenWebRuntime(tmp_path)
    state, code = asyncio.run(runtime._page_state(_Page()))

    assert state == "ready"
    assert code == ""


def test_qwen_media_upload_uses_dedicated_media_entry_and_hidden_input(tmp_path: Path) -> None:
    runtime_mod = load_personification_module("plugin.personification.core.qwen_web_runtime")
    clicks: list[str] = []

    class _Locator:
        def __init__(self, page: "_Page", selector: str) -> None:
            self.page = page
            self.selector = selector
            self.first = self

        async def count(self) -> int:
            return int(self.page.opened and self.selector.startswith('input[type="file"]'))

        async def is_visible(self, timeout: int = 0) -> bool:
            del timeout
            return self.selector == 'button[aria-label="音视频速读"]'

        async def get_attribute(self, name: str) -> str | None:
            return "video/*" if name == "accept" else None

        async def click(self, timeout: int = 0) -> None:
            del timeout
            clicks.append(self.selector)
            self.page.opened = True

        async def inner_text(self, timeout: int = 0) -> str:
            del timeout
            return "你好，我是千问"

    class _Page:
        opened = False

        def locator(self, selector: str) -> _Locator:
            return _Locator(self, selector)

        async def wait_for_timeout(self, _milliseconds: int) -> None:
            return None

    runtime = runtime_mod.QwenWebRuntime(tmp_path)
    upload = asyncio.run(runtime._open_media_upload(_Page(), "video"))

    assert upload.selector.startswith('input[type="file"]')
    assert clicks == ['button[aria-label="音视频速读"]']


def test_qwen_assistant_snapshot_reads_only_latest_assistant_container(tmp_path: Path) -> None:
    runtime_mod = load_personification_module("plugin.personification.core.qwen_web_runtime")

    class _Message:
        async def inner_text(self, timeout: int = 0) -> str:
            del timeout
            return "本次任务的最新助手结果"

    class _Messages:
        async def count(self) -> int:
            return 3

        def nth(self, index: int) -> _Message:
            assert index == 2
            return _Message()

    class _Page:
        def locator(self, selector: str):  # noqa: ANN201
            assert selector == '[data-message-author-role="assistant"]'
            return _Messages()

    runtime = runtime_mod.QwenWebRuntime(tmp_path)
    count, text = asyncio.run(runtime._assistant_snapshot(_Page()))

    assert count == 3
    assert text == "本次任务的最新助手结果"


def test_qwen_service_allows_only_one_active_and_one_waiting_job(tmp_path: Path) -> None:
    service_mod = load_personification_module("plugin.personification.core.qwen_web_service")
    service = service_mod.QwenWebService(tmp_path)

    async def run() -> None:
        release = asyncio.Event()
        entered = asyncio.Event()

        async def first() -> None:
            async with service._admit():
                entered.set()
                await release.wait()

        first_task = asyncio.create_task(first())
        await entered.wait()

        async def second() -> None:
            async with service._admit():
                return None

        second_task = asyncio.create_task(second())
        for _ in range(20):
            if service._waiting == 1:
                break
            await asyncio.sleep(0)
        assert service._active == 1
        assert service._waiting == 1
        with pytest.raises(RuntimeError, match="qwen_web_busy"):
            async with service._admit():
                pass
        release.set()
        await asyncio.gather(first_task, second_task)
        assert service._active == 0
        assert service._waiting == 0

    asyncio.run(run())
