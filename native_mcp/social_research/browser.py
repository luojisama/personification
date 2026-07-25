from __future__ import annotations

import asyncio
import base64
import os
import shutil
import stat
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .models import PLATFORMS


def _restrict_private_directory(path: Path) -> None:
    if os.name == "nt":
        creation_flags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0) or 0)
        identity = subprocess.run(
            ["whoami"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=creation_flags,
        ).stdout.strip()
        if not identity:
            raise RuntimeError("profile_permission_hardening_failed")
        result = subprocess.run(
            [
                "icacls",
                str(path),
                "/inheritance:r",
                "/grant:r",
                f"{identity}:(OI)(CI)F",
            ],
            check=False,
            capture_output=True,
            timeout=10,
            creationflags=creation_flags,
        )
        if result.returncode != 0:
            raise RuntimeError("profile_permission_hardening_failed")
        return
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    except OSError as exc:
        raise RuntimeError("profile_permission_hardening_failed") from exc


@dataclass
class AuthSession:
    session_id: str
    platform: str
    owner: str
    created_at: float = field(default_factory=time.time)
    expires_at: float = field(default_factory=lambda: time.time() + 300)
    status: str = "starting"
    qr_png: bytes = b""
    error_code: str = ""


class BrowserPool:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.profiles_root = (self.root / "profiles").resolve()
        self.profiles_root.mkdir(parents=True, exist_ok=True)
        _restrict_private_directory(self.profiles_root)
        self._playwright: Any = None
        self._contexts: dict[str, Any] = {}
        self._context_headless: dict[str, bool] = {}
        self._locks = {platform: asyncio.Lock() for platform in PLATFORMS}
        self._auth: dict[str, AuthSession] = {}

    async def _ensure_runtime(self) -> None:
        if self._playwright is not None:
            return
        try:
            from playwright.async_api import async_playwright
        except Exception as exc:
            raise RuntimeError("playwright_unavailable") from exc
        self._playwright = await async_playwright().start()

    def profile_dir(self, platform: str) -> Path:
        if platform not in PLATFORMS:
            raise ValueError("unsupported platform")
        return (self.profiles_root / platform).resolve()

    async def context(self, platform: str, *, headless: bool = True) -> Any:
        if platform not in PLATFORMS:
            raise ValueError("unsupported platform")
        async with self._locks[platform]:
            existing = self._contexts.get(platform)
            if existing is not None and self._context_headless.get(platform, True) == bool(headless):
                return existing
            if existing is not None:
                await existing.close()
                self._contexts.pop(platform, None)
                self._context_headless.pop(platform, None)
            await self._ensure_runtime()
            profile = self.profile_dir(platform)
            profile.mkdir(parents=True, exist_ok=True)
            _restrict_private_directory(profile)
            try:
                context = await self._playwright.chromium.launch_persistent_context(
                    str(profile),
                    headless=bool(headless),
                    viewport={"width": 1280, "height": 900},
                    locale="zh-CN",
                    args=["--disable-blink-features=AutomationControlled"],
                )
            except Exception as exc:
                raise RuntimeError("chromium_unavailable") from exc
            self._contexts[platform] = context
            self._context_headless[platform] = bool(headless)
            return context

    async def page(self, platform: str, *, headless: bool = True) -> Any:
        context = await self.context(platform, headless=headless)
        pages = list(context.pages)
        return pages[0] if pages else await context.new_page()

    async def close_platform(self, platform: str) -> None:
        async with self._locks[platform]:
            context = self._contexts.pop(platform, None)
            self._context_headless.pop(platform, None)
            if context is not None:
                await context.close()

    async def close(self) -> None:
        for platform in list(self._contexts):
            await self.close_platform(platform)
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None

    async def cookies(self, platform: str, *, headless: bool | None = None) -> list[dict[str, Any]]:
        resolved_headless = self._context_headless.get(platform, True) if headless is None else bool(headless)
        context = await self.context(platform, headless=resolved_headless)
        return [dict(item) for item in await context.cookies()]

    async def authenticated(self, platform: str, cookie_names: set[str], *, headless: bool | None = None) -> bool:
        names = {str(item.get("name") or "") for item in await self.cookies(platform, headless=headless)}
        return bool(names & cookie_names)

    async def start_auth(
        self,
        platform: str,
        owner: str,
        login_url: str,
        qr_selectors: tuple[str, ...],
        login_trigger_selectors: tuple[str, ...],
    ) -> dict[str, Any]:
        session = AuthSession(session_id=uuid.uuid4().hex, platform=platform, owner=owner)
        self._auth[session.session_id] = session
        try:
            # Login uses the same persistent profile as later read-only requests,
            # but stays visible so official sliders/device confirmation remain possible.
            interactive_window = True
            try:
                page = await self.page(platform, headless=False)
            except RuntimeError:
                # A headless server can still provide the official QR flow. If the
                # platform later asks for a slider/device confirmation, the status
                # clearly reports that a desktop window is required.
                interactive_window = False
                page = await self.page(platform, headless=True)
            await page.goto(login_url, wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_timeout(1200)
            for selector in login_trigger_selectors:
                trigger = page.locator(selector).first
                try:
                    if await trigger.count() and await trigger.is_visible():
                        await trigger.click(timeout=3000)
                        await page.wait_for_timeout(800)
                        break
                except Exception:
                    continue
            qr_png = b""
            for selector in qr_selectors:
                locator = page.locator(selector).first
                try:
                    if await locator.count() and await locator.is_visible():
                        qr_png = await locator.screenshot(type="png")
                        break
                except Exception:
                    continue
            if qr_png:
                session.qr_png = qr_png
                session.status = "waiting_scan"
                if not interactive_window:
                    session.error_code = "interactive_window_unavailable"
            else:
                session.status = "manual_verification_required"
                if not interactive_window:
                    session.error_code = "interactive_window_unavailable"
        except RuntimeError as exc:
            session.status = "error"
            session.error_code = str(exc)
        except Exception:
            session.status = "error"
            session.error_code = "login_page_unavailable"
        return self.public_auth(session)

    def get_auth(self, session_id: str, owner: str) -> AuthSession:
        session = self._auth.get(str(session_id or ""))
        if session is None or session.owner != str(owner or ""):
            raise KeyError("auth_session_not_found")
        if session.expires_at <= time.time() and session.status not in {"success", "cancelled"}:
            session.status = "expired"
            session.qr_png = b""
        return session

    def public_auth(self, session: AuthSession) -> dict[str, Any]:
        return {
            "session_id": session.session_id,
            "platform": session.platform,
            "status": session.status,
            "created_at": session.created_at,
            "expires_at": session.expires_at,
            "qr_available": bool(session.qr_png),
            "error_code": session.error_code,
        }

    def auth_qrcode(self, session_id: str, owner: str) -> dict[str, Any]:
        session = self.get_auth(session_id, owner)
        if not session.qr_png:
            raise KeyError("qrcode_unavailable")
        return {
            **self.public_auth(session),
            "mime_type": "image/png",
            "data_base64": base64.b64encode(session.qr_png).decode("ascii"),
        }

    def cancel_auth(self, session_id: str, owner: str) -> dict[str, Any]:
        session = self.get_auth(session_id, owner)
        session.status = "cancelled"
        session.qr_png = b""
        return self.public_auth(session)

    async def logout(self, platform: str) -> None:
        await self.close_platform(platform)
        profile = self.profile_dir(platform)
        if profile == self.profiles_root or not profile.is_relative_to(self.profiles_root):
            raise RuntimeError("unsafe_profile_path")
        if profile.exists():
            await asyncio.to_thread(shutil.rmtree, profile)
        for session in self._auth.values():
            if session.platform == platform:
                session.status = "cancelled"
                session.qr_png = b""


__all__ = ["AuthSession", "BrowserPool"]
