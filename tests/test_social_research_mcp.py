from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from ._loader import load_personification_module


def test_social_research_server_exposes_only_read_tools_and_control_is_not_listed(tmp_path: Path) -> None:
    compat = load_personification_module("plugin.personification.skill_runtime.mcp_compat")
    project_root = Path(__file__).resolve().parents[2]

    async def run():
        async with compat.McpStdioClient(
            command=sys.executable,
            args=[str(Path(__file__).resolve().parents[1] / "native_mcp" / "social_research" / "entrypoint.py")],
            env={**os.environ, "PERSONIFICATION_SOCIAL_DATA_DIR": str(tmp_path / "social")},
            cwd=str(project_root),
            timeout=8,
        ) as client:
            tools = await client.list_tools()
            status = await client.request("personification/builtin/status", {})
            return client.protocol_version, tools, status

    version, tools, status = asyncio.run(run())
    assert version == "2025-11-25"
    assert {tool["name"] for tool in tools} == {
        "social_content_search",
        "social_content_read",
        "research_game_slang",
    }
    assert all(tool["annotations"]["readOnlyHint"] is True for tool in tools)
    assert not any("auth" in tool["name"] or "configure" in tool["name"] for tool in tools)
    assert set(status["platforms"]) == {"bilibili", "douyin", "tieba", "xiaoheihe"}
    assert all(item["state"] == "disabled" for item in status["platforms"].values())


def test_balanced_filter_requires_both_video_thresholds_and_filters_marketing() -> None:
    models = load_personification_module("plugin.personification.native_mcp.social_research.models")
    config = dict(models.DEFAULT_PLATFORM_CONFIG)
    high_comments = models.apply_quality_filter(
        {
            "content_type": "video",
            "title": "小众攻略",
            "caption_or_body": "正常讨论",
            "stats": {"play_count": 100, "comment_count": 20},
        },
        config,
    )
    assert high_comments["retained"] is True

    double_low = models.apply_quality_filter(
        {
            "content_type": "video",
            "title": "小众攻略",
            "caption_or_body": "正常讨论",
            "stats": {"play_count": 100, "comment_count": 1},
        },
        config,
    )
    assert double_low["retained"] is False
    assert double_low["filtered_reason"] == "low_video_engagement"

    marketing = models.apply_quality_filter(
        {
            "content_type": "video",
            "title": "低价代练优惠下单",
            "caption_or_body": "加微信 abcdef 私信购买 https://example.test",
            "commercial_label": True,
            "stats": {"play_count": 100000, "comment_count": 100},
        },
        config,
    )
    assert marketing["retained"] is False
    assert marketing["filtered_reason"] == "marketing_risk"
    assert marketing["marketing_reasons"]


def test_social_search_returns_partial_packet_without_leaking_adapter_error(tmp_path: Path) -> None:
    service_mod = load_personification_module("plugin.personification.native_mcp.social_research.service")

    class FakeAdapter:
        async def authenticated(self):
            return True

        async def search(self, query, *, limit, timeout_seconds):
            assert query == "刘涛"
            return [
                {
                    "platform": "bilibili",
                    "content_type": "video",
                    "content_id": "BV1",
                    "canonical_url": "https://www.bilibili.com/video/BV1",
                    "title": "刘涛是什么意思",
                    "caption_or_body": "玩家讨论",
                    "cover_ref": "https://i0.hdslb.com/test.jpg",
                    "author": {"display_name": "", "fingerprint": ""},
                    "published_at": 0,
                    "stats": {"play_count": 10000, "comment_count": 20},
                    "discussion": [],
                    "content_fingerprint": "x",
                }
            ]

    class FailingAdapter:
        async def authenticated(self):
            raise RuntimeError("private-cookie-value")

    async def run():
        service = service_mod.SocialResearchService(tmp_path)
        service._config["bilibili"]["enabled"] = True
        service._config["douyin"]["enabled"] = True
        service.adapters["bilibili"] = FakeAdapter()
        service.adapters["douyin"] = FailingAdapter()
        try:
            return await service.search(
                {"query": "刘涛", "platforms": ["bilibili", "douyin"], "limit": 5, "quality_mode": "balanced"}
            )
        finally:
            await service.close()

    packet = asyncio.run(run())
    assert packet["trust"] == "untrusted_data_only"
    assert packet["partial"] is True
    assert packet["items"][0]["content_id"] == "BV1"
    serialized = json.dumps(packet, ensure_ascii=False)
    assert "private-cookie-value" not in serialized
    assert packet["platform_statuses"]["douyin"]["error_code"] == "platform_request_failed"


def test_social_search_honors_content_type_filter(tmp_path: Path) -> None:
    service_mod = load_personification_module("plugin.personification.native_mcp.social_research.service")

    class FakeAdapter:
        async def authenticated(self):
            return True

        async def search(self, _query, *, limit, timeout_seconds):
            return [{
                "platform": "bilibili",
                "content_type": "video",
                "content_id": "BV1",
                "canonical_url": "https://www.bilibili.com/video/BV1",
                "title": "视频",
                "caption_or_body": "内容",
                "cover_ref": "",
                "author": {"display_name": "", "fingerprint": ""},
                "published_at": 0,
                "stats": {"play_count": 10000, "comment_count": 20},
                "discussion": [],
                "content_fingerprint": "x",
            }]

    async def run():
        service = service_mod.SocialResearchService(tmp_path)
        service._config["bilibili"]["enabled"] = True
        service.adapters["bilibili"] = FakeAdapter()
        try:
            return await service.search({"query": "测试", "platforms": ["bilibili"], "content_types": ["article"]})
        finally:
            await service.close()

    packet = asyncio.run(run())
    assert packet["items"] == []
    assert packet["filtered_counts"]["bilibili"] == 1


def test_platform_url_validation_rejects_cross_origin_and_credentials(tmp_path: Path) -> None:
    browser_mod = load_personification_module("plugin.personification.native_mcp.social_research.browser")
    adapters_mod = load_personification_module("plugin.personification.native_mcp.social_research.adapters")
    adapter = adapters_mod.PlatformAdapter(adapters_mod.SPECS["bilibili"], browser_mod.BrowserPool(tmp_path))
    assert adapter.validate_url("https://www.bilibili.com/video/BV1/").startswith("https://www.bilibili.com/")
    for value in (
        "https://example.com/video/BV1",
        "http://www.bilibili.com/video/BV1",
        "https://user:pass@www.bilibili.com/video/BV1",
    ):
        try:
            adapter.validate_url(value)
        except ValueError:
            pass
        else:
            raise AssertionError(value)


def test_live_page_state_is_mapped_to_safe_control_code(tmp_path: Path) -> None:
    service_mod = load_personification_module("plugin.personification.native_mcp.social_research.service")
    assert service_mod._safe_operation_code(RuntimeError("manual_verification_required")) == "manual_verification_required"
    assert service_mod._safe_operation_code(RuntimeError("risk_controlled")) == "risk_controlled"
    assert service_mod._safe_operation_code(asyncio.TimeoutError()) == "platform_timeout"


def test_xiaoheihe_uses_current_web_search_route() -> None:
    adapters_mod = load_personification_module("plugin.personification.native_mcp.social_research.adapters")
    assert adapters_mod.SPECS["xiaoheihe"].search_url.startswith("https://www.xiaoheihe.cn/app/search/list?q=")


def test_cover_registry_returns_opaque_reference_and_enforces_platform_hosts(tmp_path: Path) -> None:
    covers_mod = load_personification_module("plugin.personification.native_mcp.social_research.covers")
    registry = covers_mod.CoverRegistry(tmp_path)
    ref = registry.register("bilibili", "https://i0.hdslb.com/bfs/archive/demo.jpg?signed=internal")
    assert ref.startswith("cover_")
    assert "hdslb" not in ref and "signed" not in ref
    resolved = registry.resolve(ref)
    assert resolved["platform"] == "bilibili"
    assert resolved["url"].startswith("https://i0.hdslb.com/")
    assert registry.register("bilibili", "https://example.com/private.jpg") == ""


def test_auth_session_is_bound_to_owner_and_public_shape_excludes_owner(tmp_path: Path) -> None:
    browser_mod = load_personification_module("plugin.personification.native_mcp.social_research.browser")
    pool = browser_mod.BrowserPool(tmp_path)
    session = browser_mod.AuthSession(session_id="session", platform="bilibili", owner="admin:device:bilibili")
    pool._auth[session.session_id] = session
    assert "owner" not in pool.public_auth(session)
    try:
        pool.get_auth("session", "another-device")
    except KeyError:
        pass
    else:
        raise AssertionError("auth session was not bound to owner")


def test_browser_pool_switches_between_headless_reads_and_visible_official_login(tmp_path: Path) -> None:
    browser_mod = load_personification_module("plugin.personification.native_mcp.social_research.browser")
    launches: list[bool] = []

    class FakeContext:
        pages = []

        def __init__(self) -> None:
            self.closed = False

        async def close(self) -> None:
            self.closed = True

    class FakeChromium:
        async def launch_persistent_context(self, _profile, *, headless, **_kwargs):  # noqa: ANN001
            launches.append(bool(headless))
            return FakeContext()

    async def run() -> tuple[object, object, object]:
        pool = browser_mod.BrowserPool(tmp_path)
        pool._playwright = type("FakePlaywright", (), {"chromium": FakeChromium()})()
        read_context = await pool.context("bilibili", headless=True)
        reused = await pool.context("bilibili", headless=True)
        login_context = await pool.context("bilibili", headless=False)
        assert read_context.closed is True
        await pool.close_platform("bilibili")
        return read_context, reused, login_context

    read_context, reused, login_context = asyncio.run(run())
    assert read_context is reused
    assert login_context is not read_context
    assert launches == [True, False]


def test_auth_qr_falls_back_to_headless_with_explicit_interactive_warning(tmp_path: Path) -> None:
    browser_mod = load_personification_module("plugin.personification.native_mcp.social_research.browser")
    pool = browser_mod.BrowserPool(tmp_path)
    modes: list[bool] = []

    class FakeLocator:
        first = None

        def __init__(self) -> None:
            self.first = self

        async def count(self) -> int:
            return 1

        async def is_visible(self) -> bool:
            return True

        async def screenshot(self, **_kwargs) -> bytes:
            return b"png"

    class FakePage:
        async def goto(self, *_args, **_kwargs) -> None:
            return None

        async def wait_for_timeout(self, _milliseconds) -> None:  # noqa: ANN001
            return None

        def locator(self, _selector) -> FakeLocator:  # noqa: ANN001
            return FakeLocator()

    async def fake_page(_platform, *, headless=True):  # noqa: ANN001
        modes.append(bool(headless))
        if not headless:
            raise RuntimeError("chromium_unavailable")
        return FakePage()

    pool.page = fake_page
    result = asyncio.run(
        pool.start_auth(
            "bilibili",
            "admin:device:bilibili",
            "https://passport.bilibili.com/login",
            ("qr",),
            (),
        )
    )

    assert modes == [False, True]
    assert result["status"] == "waiting_scan"
    assert result["qr_available"] is True
    assert result["error_code"] == "interactive_window_unavailable"


def test_auth_status_uses_visible_profile_and_closes_window_after_success(tmp_path: Path) -> None:
    browser_mod = load_personification_module("plugin.personification.native_mcp.social_research.browser")
    service_mod = load_personification_module("plugin.personification.native_mcp.social_research.service")
    session = browser_mod.AuthSession(
        session_id="session",
        platform="bilibili",
        owner="admin:device:bilibili",
        status="manual_verification_required",
    )

    class FakeBrowsers:
        closed: list[str] = []

        def get_auth(self, session_id, owner):  # noqa: ANN001
            assert (session_id, owner) == ("session", "admin:device:bilibili")
            return session

        def public_auth(self, value):  # noqa: ANN001
            return {"status": value.status, "platform": value.platform}

        async def close_platform(self, platform):  # noqa: ANN001
            self.closed.append(platform)

    class FakeAdapter:
        async def authenticated(self, *, interactive=None):  # noqa: ANN001
            assert interactive is True
            return True

    async def run() -> tuple[dict, list[str]]:
        service = service_mod.SocialResearchService(tmp_path)
        browsers = FakeBrowsers()
        service.browsers = browsers
        service.adapters["bilibili"] = FakeAdapter()
        result = await service.auth_status(
            {"session_id": "session", "owner": "admin:device:bilibili"}
        )
        return result, browsers.closed

    result, closed = asyncio.run(run())
    assert result["status"] == "success"
    assert closed == ["bilibili"]
