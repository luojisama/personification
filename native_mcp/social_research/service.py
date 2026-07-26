from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

from .adapters import SPECS, PlatformAdapter, build_adapters
from .browser import BrowserPool
from .cache import PacketCache
from .covers import CoverRegistry
from .models import (
    ContentPacket,
    DEFAULT_PLATFORM_CONFIG,
    PLATFORMS,
    QUALITY_MODES,
    apply_quality_filter,
    clean_text,
    validate_platforms,
)


_SAFE_OPERATION_CODES = {
    "chromium_unavailable",
    "login_page_unavailable",
    "login_required",
    "manual_verification_required",
    "manual_browser_start_failed",
    "official_window_closed",
    "platform_disabled",
    "playwright_unavailable",
    "platform_request_failed",
    "platform_timeout",
    "risk_controlled",
    "qr_expired",
    "qr_refresh_failed",
    "system_browser_unavailable",
}


def _safe_operation_code(exc: BaseException) -> str:
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return "platform_timeout"
    value = str(exc).strip()
    return value if value in _SAFE_OPERATION_CODES else "platform_request_failed"


class SocialResearchService:
    def __init__(self, root: str | Path | None = None) -> None:
        resolved = Path(root or os.environ.get("PERSONIFICATION_SOCIAL_DATA_DIR") or "data/personification/mcp/social_platform")
        self.root = resolved.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.browsers = BrowserPool(self.root)
        self.adapters = build_adapters(self.browsers)
        self.cache = PacketCache(self.root / "cache")
        self.covers = CoverRegistry(self.root)
        self.config_path = self.root / "config.json"
        self._config_lock = asyncio.Lock()
        self._config = self._load_config()

    def _load_config(self) -> dict[str, dict[str, Any]]:
        try:
            raw = json.loads(self.config_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            raw = {}
        result: dict[str, dict[str, Any]] = {}
        for platform in PLATFORMS:
            current = raw.get(platform) if isinstance(raw, dict) and isinstance(raw.get(platform), dict) else {}
            result[platform] = {**DEFAULT_PLATFORM_CONFIG, **current, "enabled": bool(current.get("enabled", False))}
        return result

    async def configure(self, params: dict[str, Any]) -> dict[str, Any]:
        platform = str(params.get("platform") or "")
        if platform not in PLATFORMS:
            raise ValueError("unsupported platform")
        enabled = params.get("enabled")
        if type(enabled) is not bool:
            raise ValueError("enabled must be a JSON boolean")
        supplied = params.get("config") if isinstance(params.get("config"), dict) else {}
        allowed = set(DEFAULT_PLATFORM_CONFIG)
        unknown = set(supplied) - allowed
        if unknown:
            raise ValueError("platform config contains unsupported fields")
        config = {**self._config[platform], **supplied, "enabled": enabled}
        mode = str(config.get("quality_mode") or "")
        if mode not in QUALITY_MODES:
            raise ValueError("quality_mode is invalid")
        config["marketing_threshold"] = min(1.0, max(0.0, float(config.get("marketing_threshold", 0.75))))
        for key in ("min_play_count", "min_comment_count", "min_reply_count", "max_results", "comment_limit", "danmaku_limit", "cache_ttl_seconds", "request_timeout_seconds"):
            config[key] = max(0, int(config.get(key, DEFAULT_PLATFORM_CONFIG[key]) or 0))
        config["max_results"] = min(50, max(1, config["max_results"]))
        config["comment_limit"] = min(200, config["comment_limit"])
        config["danmaku_limit"] = min(500, config["danmaku_limit"])
        config["cache_ttl_seconds"] = min(86400, max(60, config["cache_ttl_seconds"]))
        config["request_timeout_seconds"] = min(60, max(3, config["request_timeout_seconds"]))
        async with self._config_lock:
            self._config[platform] = config
            temporary = self.config_path.with_suffix(".tmp")
            temporary.write_text(json.dumps(self._config, ensure_ascii=False), encoding="utf-8")
            os.replace(temporary, self.config_path)
        return await self.status()

    async def _platform_status(self, platform: str) -> dict[str, Any]:
        adapter = self.adapters[platform]
        config = self._config[platform]
        authenticated = False
        error_code = ""
        if config.get("enabled"):
            try:
                authenticated = await adapter.authenticated()
            except RuntimeError as exc:
                error_code = _safe_operation_code(exc)
        state = "disabled" if not config.get("enabled") else "ready" if authenticated else "login_required"
        if error_code:
            state = "unavailable"
        return {
            "platform": platform,
            "enabled": bool(config.get("enabled")),
            "authenticated": authenticated,
            "state": state,
            "error_code": error_code,
            "capabilities": adapter.capabilities(),
            "config": dict(config),
        }

    async def status(self) -> dict[str, Any]:
        results = await asyncio.gather(*(self._platform_status(platform) for platform in PLATFORMS))
        return {"schema_version": 1, "platforms": {item["platform"]: item for item in results}}

    async def health(self) -> dict[str, Any]:
        status = await self.status()
        return {
            **status,
            "ok": any(item["state"] == "ready" for item in status["platforms"].values()),
            "chromium_required": True,
            "install_command": "python -m playwright install chromium",
        }

    async def auth_start(self, params: dict[str, Any]) -> dict[str, Any]:
        platform = str(params.get("platform") or "")
        owner = clean_text(params.get("owner"), 200)
        mode = str(params.get("mode") or "embedded_qr")
        if platform not in PLATFORMS or not owner or mode not in {"embedded_qr", "manual_browser"}:
            raise ValueError("platform, owner and a valid auth mode are required")
        return await self.adapters[platform].start_auth(owner, mode=mode)

    async def auth_status(self, params: dict[str, Any]) -> dict[str, Any]:
        owner = clean_text(params.get("owner"), 200)
        session = self.browsers.get_auth(str(params.get("session_id") or ""), owner)
        if session.login_mode == "manual_browser" and session.status != "expired":
            if self.browsers.manual_browser_running(session):
                return self.browsers.public_auth(session)
            session.official_window_open = False
            if session.verification_kind == "official_browser_login":
                await asyncio.sleep(0.5)
            try:
                authenticated = await self.adapters[session.platform].authenticated(interactive=False)
            except Exception:
                authenticated = False
            if authenticated:
                session.status = "success"
                session.error_code = ""
                session.verification_kind = ""
                await self.browsers.close_platform(session.platform)
            else:
                session.status = "manual_verification_required"
                session.verification_kind = "manual_login_incomplete"
            return self.browsers.public_auth(session)
        if session.status in {
            "starting", "waiting_scan", "manual_verification_required", "risk_controlled", "qr_expired"
        }:
            try:
                authenticated = await self.adapters[session.platform].authenticated(interactive=True)
            except Exception:
                authenticated = False
            if authenticated:
                session.status = "success"
                session.qr_png = b""
                session.error_code = ""
                session.verification_kind = ""
                session.official_window_open = False
                await self.browsers.close_platform(session.platform)
            else:
                await self.browsers.refresh_auth(session)
        elif session.status == "expired":
            await self.browsers.expire_auth(session)
        return self.browsers.public_auth(session)

    async def auth_qrcode(self, params: dict[str, Any]) -> dict[str, Any]:
        return self.browsers.auth_qrcode(
            str(params.get("session_id") or ""),
            clean_text(params.get("owner"), 200),
        )

    async def auth_cancel(self, params: dict[str, Any]) -> dict[str, Any]:
        result = await self.browsers.cancel_auth(
            str(params.get("session_id") or ""),
            clean_text(params.get("owner"), 200),
        )
        await self.browsers.close_platform(str(result.get("platform") or ""))
        return result

    async def auth_logout(self, params: dict[str, Any]) -> dict[str, Any]:
        platform = str(params.get("platform") or "")
        if platform not in PLATFORMS:
            raise ValueError("unsupported platform")
        await self.browsers.logout(platform)
        return {"platform": platform, "state": "login_required", "authenticated": False}

    async def _ready_adapter(self, platform: str) -> tuple[PlatformAdapter, dict[str, Any]]:
        config = self._config[platform]
        if not config.get("enabled"):
            raise RuntimeError("platform_disabled")
        adapter = self.adapters[platform]
        if not await adapter.authenticated():
            raise RuntimeError("login_required")
        return adapter, config

    async def _search_platform(self, platform: str, query: str, limit: int, quality_mode: str) -> tuple[list[dict[str, Any]], dict[str, Any], int]:
        started = time.monotonic()
        adapter, base_config = await self._ready_adapter(platform)
        config = {**base_config, "quality_mode": quality_mode}
        rows = await asyncio.wait_for(
            adapter.search(query, limit=limit, timeout_seconds=float(config["request_timeout_seconds"])),
            timeout=float(config["request_timeout_seconds"]) + 2,
        )
        filtered = [apply_quality_filter(item, config) for item in rows]
        retained = [item for item in filtered if item["retained"]]
        for item in retained:
            item["cover_ref"] = self.covers.register(platform, str(item.get("cover_ref") or ""))
        return retained, {"state": "ready", "elapsed_ms": int((time.monotonic() - started) * 1000)}, len(filtered) - len(retained)

    async def search(self, params: dict[str, Any]) -> dict[str, Any]:
        query = clean_text(params.get("query"), 200)
        if not query:
            raise ValueError("query is required")
        platforms = validate_platforms(params.get("platforms"))
        raw_content_types = params.get("content_types")
        if raw_content_types is None:
            content_types = ["video", "article", "post"]
        elif isinstance(raw_content_types, list):
            content_types = list(dict.fromkeys(str(item) for item in raw_content_types))
            if not content_types or any(item not in {"video", "article", "post"} for item in content_types):
                raise ValueError("content_types is invalid")
        else:
            raise ValueError("content_types must be an array")
        limit = min(50, max(1, int(params.get("limit", 12) or 12)))
        quality_mode = str(params.get("quality_mode") or "balanced")
        if quality_mode not in QUALITY_MODES:
            raise ValueError("quality_mode is invalid")
        cache_key = json.dumps(
            ["search", "opaque_cover_v1", query, platforms, content_types, limit, quality_mode, self._config],
            ensure_ascii=False,
            sort_keys=True,
        )
        cached = self.cache.get(cache_key)
        if cached is not None:
            cached["cache_hit"] = True
            return cached
        results = await asyncio.gather(
            *(self._search_platform(platform, query, limit, quality_mode) for platform in platforms),
            return_exceptions=True,
        )
        packet = ContentPacket(ttl_seconds=min(self._config[p]["cache_ttl_seconds"] for p in platforms))
        for platform, result in zip(platforms, results):
            if isinstance(result, BaseException):
                code = _safe_operation_code(result)
                packet.platform_statuses[platform] = {"state": code, "error_code": code}
                packet.partial = True
                continue
            items, status, filtered_count = result
            retained_types = [item for item in items if str(item.get("content_type") or "") in content_types]
            packet.items.extend(retained_types)
            packet.platform_statuses[platform] = status
            packet.filtered_counts[platform] = filtered_count + len(items) - len(retained_types)
        packet.items.sort(key=lambda item: float(item.get("quality_score", 0) or 0), reverse=True)
        packet.items = packet.items[: limit * len(platforms)]
        value = packet.to_dict()
        self.cache.set(cache_key, value, packet.ttl_seconds)
        return value

    async def read(self, params: dict[str, Any]) -> dict[str, Any]:
        platform = str(params.get("platform") or "")
        if platform not in PLATFORMS:
            raise ValueError("unsupported platform")
        adapter, config = await self._ready_adapter(platform)
        include = params.get("include") if isinstance(params.get("include"), list) else ["caption", "comments", "replies", "danmaku"]
        include = [str(item) for item in include]
        comment_limit = min(200, max(0, int(params.get("comment_limit", config["comment_limit"]) or 0)))
        danmaku_limit = min(500, max(0, int(params.get("danmaku_limit", config["danmaku_limit"]) or 0)))
        content_id = clean_text(params.get("content_id"), 300)
        url = clean_text(params.get("url"), 1000)
        cache_key = json.dumps(
            ["read", "opaque_cover_v1", platform, content_id, url, include, comment_limit, danmaku_limit],
            ensure_ascii=False,
            sort_keys=True,
        )
        cached = self.cache.get(cache_key)
        if cached is not None:
            cached["cache_hit"] = True
            return cached
        raw = await asyncio.wait_for(
            adapter.read(
                content_id=content_id,
                url=url,
                include=include,
                comment_limit=comment_limit,
                danmaku_limit=danmaku_limit,
                timeout_seconds=float(config["request_timeout_seconds"]),
            ),
            timeout=float(config["request_timeout_seconds"]) + 3,
        )
        item = apply_quality_filter(raw, config)
        item["cover_ref"] = self.covers.register(platform, str(item.get("cover_ref") or ""))
        packet = ContentPacket(
            items=[item] if item["retained"] or str(config.get("quality_mode")) == "ranking_only" else [],
            platform_statuses={platform: {"state": "ready"}},
            filtered_counts={platform: int(not item["retained"])},
            ttl_seconds=int(config["cache_ttl_seconds"]),
        )
        value = packet.to_dict()
        self.cache.set(cache_key, value, packet.ttl_seconds)
        return value

    async def research(self, params: dict[str, Any]) -> dict[str, Any]:
        term = clean_text(params.get("term"), 100)
        context = clean_text(params.get("context"), 1000)
        game = clean_text(params.get("game"), 100)
        depth = str(params.get("depth") or "auto")
        if not term or not context or depth not in {"auto", "deep"}:
            raise ValueError("term, context and a valid depth are required")
        query = " ".join(value for value in (game, term) if value)
        if depth == "deep":
            platforms = list(PLATFORMS)
        else:
            video = next((name for name in ("bilibili", "douyin") if self._config[name].get("enabled")), None)
            community = next((name for name in ("xiaoheihe", "tieba") if self._config[name].get("enabled")), None)
            platforms = [name for name in (video, community) if name]
            if not platforms:
                platforms = ["bilibili", "xiaoheihe"]
        search_packet = await self.search(
            {"query": query, "platforms": platforms, "limit": 6, "quality_mode": "balanced"}
        )
        candidates = list(search_packet.get("items") or [])[: (12 if depth == "deep" else 6)]
        read_results = await asyncio.gather(
            *(
                self.read(
                    {
                        "platform": item["platform"],
                        "url": item["canonical_url"],
                        "include": ["caption", "comments", "replies", "danmaku"],
                        "comment_limit": 30,
                        "danmaku_limit": 80,
                    }
                )
                for item in candidates
            ),
            return_exceptions=True,
        )
        packet = ContentPacket(
            platform_statuses=dict(search_packet.get("platform_statuses") or {}),
            partial=bool(search_packet.get("partial")),
            warnings=list(search_packet.get("warnings") or []),
            filtered_counts=dict(search_packet.get("filtered_counts") or {}),
        )
        for result in read_results:
            if isinstance(result, BaseException):
                packet.partial = True
                continue
            packet.items.extend(list(result.get("items") or []))
        if not packet.items:
            packet.items = candidates
        packet.items = packet.items[: (12 if depth == "deep" else 6)]
        return packet.to_dict()

    async def close(self) -> None:
        await self.browsers.close()

    def cover_resolve(self, params: dict[str, Any]) -> dict[str, Any]:
        return self.covers.resolve(clean_text(params.get("cover_ref"), 100))


__all__ = ["SocialResearchService"]
