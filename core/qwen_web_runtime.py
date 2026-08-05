from __future__ import annotations

import asyncio
import re
import shutil
import time
from collections import deque
from contextlib import suppress
from pathlib import Path
from typing import Any, Iterable

from ..native_mcp.social_research.browser import AuthSession, BrowserPool


QWEN_WEB_PLATFORM = "qwen_web"
QWEN_WEB_HOME = "https://www.qianwen.com/"
QWEN_WEB_PAGE_CONTRACT = "qianwen_cn_v1"

_QWEN_ALLOWED_HOSTS = (
    "qianwen.com",
    "alibaba.com",
    "taobao.com",
    "alicdn.com",
    "alipay.com",
)
_QWEN_LOGIN_TRIGGERS = (
    'button:has-text("登录")',
    '[role="button"]:has-text("登录")',
    'text="登录"',
)
_QWEN_QR_SELECTORS = (
    'canvas[aria-label*="二维码"]',
    'img[alt*="二维码"]',
    '[class*="qrcode"] canvas',
    '[class*="qrcode"] img',
)
_QWEN_UPLOAD_TRIGGERS = (
    'button:has-text("音视频速读")',
    '[role="button"]:has-text("音视频速读")',
    'button:has-text("附件")',
    '[role="button"]:has-text("附件")',
    'button[aria-label*="附件"]',
    'button[aria-label*="上传"]',
)
_QWEN_COMPOSERS = (
    'textarea[placeholder]',
    'textarea',
    '[contenteditable="true"][role="textbox"]',
    '[contenteditable="true"]',
)
_QWEN_SEND_BUTTONS = (
    'button:has-text("发送")',
    'button[aria-label*="发送"]',
    '[role="button"][aria-label*="发送"]',
)
_QWEN_ASSISTANT_MESSAGES = (
    '[data-message-author-role="assistant"]',
    '[data-role="assistant"]',
    '[data-testid*="assistant"]',
    '[class*="message"][class*="assistant"]',
)
_QWEN_STOP_BUTTONS = (
    'button:has-text("停止生成")',
    'button[aria-label*="停止"]',
)
_LOGIN_MARKERS = (
    "登录后使用",
    "请先登录",
    "扫码登录",
    "手机号登录",
)
_MANUAL_VERIFICATION_MARKERS = (
    "人机验证",
    "安全验证",
    "请完成验证",
    "拖动滑块",
    "验证码",
    "captcha",
)
_NETWORK_RISK_MARKERS = (
    "网络安全风险",
    "网络环境存在风险",
    "网络环境可能存在风险",
    "当前网络环境存在风险",
    "当前网络环境可能存在安全风险",
    "访问过于频繁",
    "操作过于频繁",
    "请求过于频繁",
    "账号存在风险",
    "risk control",
    "risk_control",
)
_UPLOAD_ERROR_MARKERS = (
    "上传失败",
    "文件过大",
    "不支持该格式",
    "文件格式不支持",
)
_MEDIA_TOKEN_RE = re.compile(r"^job_[0-9a-f]{32}$")
_AUTOMATIC_MIN_INTERVAL_SECONDS = 30.0
_AUTOMATIC_WINDOW_SECONDS = 10 * 60.0
_AUTOMATIC_WINDOW_LIMIT = 6
_NETWORK_RISK_COOLDOWN_SECONDS = 15 * 60.0


def _bounded_float(value: Any, default: float, minimum: float, maximum: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return min(maximum, max(minimum, number))


def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return min(maximum, max(minimum, number))


async def _first_visible(page: Any, selectors: Iterable[str]) -> Any | None:
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            if await locator.is_visible(timeout=350):
                return locator
        except Exception:
            continue
    return None


async def _bounded_body_text(page: Any, *, limit: int = 12000) -> str:
    try:
        text = await page.locator("body").inner_text(timeout=2000)
    except Exception:
        return ""
    return str(text or "")[: max(1000, int(limit))].lower()


def _contains_marker(text: str, markers: Iterable[str]) -> bool:
    lowered = str(text or "").lower()
    return any(str(marker or "").lower() in lowered for marker in markers)


class QwenWebRuntime:
    """Own the isolated Qwen consumer-web browser inside the helper process.

    This adapter intentionally uses only normal visible page controls. It never
    replays private endpoints, exports cookies, modifies browser fingerprints or
    attempts to solve/bypass platform verification.
    """

    def __init__(
        self,
        root: Path,
        *,
        idle_timeout_seconds: float = 300.0,
        browser_pool: BrowserPool | None = None,
    ) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.staging_root = (self.root / "staging").resolve()
        self.staging_root.mkdir(parents=True, exist_ok=True)
        self.browser = browser_pool or BrowserPool(
            self.root,
            idle_timeout_seconds=idle_timeout_seconds,
            platforms=(QWEN_WEB_PLATFORM,),
            task_name_prefix="qwen-web-browser",
        )
        self._state = "login_required"
        self._last_diagnostic_code = ""
        self._last_probe_at = 0.0
        self._active_job = False
        self._recent_jobs: deque[float] = deque(maxlen=_AUTOMATIC_WINDOW_LIMIT)
        self._risk_blocked_until = 0.0

    def configure(self, params: dict[str, Any]) -> dict[str, Any]:
        self.browser.set_idle_timeout_seconds(
            _bounded_float(params.get("idle_timeout_seconds"), 300.0, 60.0, 1800.0)
        )
        return self.status()

    def _profile_present(self) -> bool:
        profile = self.browser.profile_dir(QWEN_WEB_PLATFORM)
        if not profile.exists():
            return False
        try:
            return any(profile.iterdir())
        except OSError:
            return False

    def status(self) -> dict[str, Any]:
        runtime = self.browser.runtime_status()
        interactive = next(
            (
                self.browser.public_auth(session)
                for session in self.browser._auth.values()
                if session.platform == QWEN_WEB_PLATFORM
                and session.status not in {"success", "expired", "cancelled", "error"}
            ),
            None,
        )
        return {
            "schema_version": 1,
            "state": "busy" if self._active_job else self._state,
            "profile_present": self._profile_present(),
            "browser_running": QWEN_WEB_PLATFORM in set(runtime.get("open_contexts") or []),
            "active_job": bool(self._active_job),
            "interactive_session": interactive,
            "last_diagnostic_code": self._last_diagnostic_code,
            "last_probe_at": float(self._last_probe_at or 0.0),
            "page_contract_version": QWEN_WEB_PAGE_CONTRACT,
            "risk_cooldown_seconds": max(0, int(self._risk_blocked_until - time.time())),
        }

    async def _page_state(self, page: Any) -> tuple[str, str]:
        body = await _bounded_body_text(page)
        if _contains_marker(body, _NETWORK_RISK_MARKERS):
            self._risk_blocked_until = time.time() + _NETWORK_RISK_COOLDOWN_SECONDS
            return "manual_verification_required", "qwen_web_network_risk_detected"
        if _contains_marker(body, _MANUAL_VERIFICATION_MARKERS):
            return "manual_verification_required", "qwen_web_manual_verification_required"
        if _contains_marker(body, _LOGIN_MARKERS):
            return "login_required", "qwen_web_login_required"
        login_trigger = await _first_visible(page, _QWEN_LOGIN_TRIGGERS)
        composer = await _first_visible(page, _QWEN_COMPOSERS)
        upload_input = await _first_visible(page, ('input[type="file"]',))
        if login_trigger is not None and composer is None and upload_input is None:
            return "login_required", "qwen_web_login_required"
        if composer is not None or upload_input is not None:
            return "ready", ""
        return "dom_changed", "qwen_web_dom_changed"

    async def probe(self) -> dict[str, Any]:
        if time.time() < self._risk_blocked_until:
            self._state = "manual_verification_required"
            self._last_diagnostic_code = "qwen_web_network_risk_cooldown"
            self._last_probe_at = time.time()
            return self.status()
        try:
            async with self.browser.activity(QWEN_WEB_PLATFORM):
                page = await self.browser.page(QWEN_WEB_PLATFORM, headless=True)
                if not str(getattr(page, "url", "") or "").startswith("https://www.qianwen.com/"):
                    await page.goto(QWEN_WEB_HOME, wait_until="domcontentloaded", timeout=20000)
                    await page.wait_for_timeout(500)
                state, code = await self._page_state(page)
        except RuntimeError as exc:
            state, code = "unavailable", str(exc)[:100] or "qwen_web_process_failed"
        except Exception:
            state, code = "unavailable", "qwen_web_process_failed"
        self._state = state
        self._last_diagnostic_code = code
        self._last_probe_at = time.time()
        return self.status()

    async def auth_start(self, owner: str) -> dict[str, Any]:
        self._risk_blocked_until = 0.0
        result = await self.browser.start_interactive_auth(
            QWEN_WEB_PLATFORM,
            str(owner or ""),
            QWEN_WEB_HOME,
            _QWEN_ALLOWED_HOSTS,
            _QWEN_QR_SELECTORS,
            _QWEN_LOGIN_TRIGGERS,
        )
        self._state = "manual_verification_required"
        self._last_diagnostic_code = ""
        return result

    def auth_status(self, session_id: str, owner: str) -> dict[str, Any]:
        return self.browser.public_auth(self.browser.get_auth(session_id, owner))

    async def auth_frame(
        self,
        session_id: str,
        owner: str,
        *,
        after_revision: int = 0,
    ) -> dict[str, Any]:
        return await self.browser.interactive_frame(
            session_id,
            owner,
            after_revision=after_revision,
        )

    async def auth_input(
        self,
        session_id: str,
        owner: str,
        action: dict[str, Any],
    ) -> dict[str, Any]:
        return await self.browser.interactive_input(session_id, owner, action)

    async def auth_finish(self, session_id: str, owner: str) -> dict[str, Any]:
        session = self.browser.get_auth(session_id, owner)
        try:
            page = await self.browser._interactive_page(session)
            state, code = await self._page_state(page)
        except Exception:
            state, code = "unavailable", "qwen_web_process_failed"
        if state == "ready":
            session.status = "success"
            session.verification_kind = ""
            session.error_code = ""
            session.interactive_frame = b""
            session.official_window_open = False
        else:
            session.status = "manual_verification_required" if state == "manual_verification_required" else state
            session.error_code = code
        self._state = state
        self._last_diagnostic_code = code
        self._last_probe_at = time.time()
        return self.browser.public_auth(session)

    async def auth_cancel(self, session_id: str, owner: str) -> dict[str, Any]:
        result = await self.browser.cancel_auth(session_id, owner)
        self._state = "login_required"
        return result

    async def logout(self) -> dict[str, Any]:
        await self.browser.logout(QWEN_WEB_PLATFORM)
        self._state = "login_required"
        self._last_diagnostic_code = ""
        self._risk_blocked_until = 0.0
        return self.status()

    def _resolve_media_token(self, token: str) -> Path:
        normalized = str(token or "").strip().lower()
        if not _MEDIA_TOKEN_RE.fullmatch(normalized):
            raise ValueError("qwen_web_media_token_invalid")
        directory = (self.staging_root / normalized).resolve()
        if not directory.is_relative_to(self.staging_root) or not directory.is_dir():
            raise ValueError("qwen_web_media_token_invalid")
        files = [path.resolve() for path in directory.iterdir() if path.is_file()]
        if len(files) != 1 or not files[0].is_relative_to(directory):
            raise ValueError("qwen_web_media_token_invalid")
        return files[0]

    def _automatic_allowed(self) -> tuple[bool, str]:
        now = time.time()
        if now < self._risk_blocked_until:
            return False, "qwen_web_network_risk_cooldown"
        while self._recent_jobs and now - self._recent_jobs[0] >= _AUTOMATIC_WINDOW_SECONDS:
            self._recent_jobs.popleft()
        if self._recent_jobs and now - self._recent_jobs[-1] < _AUTOMATIC_MIN_INTERVAL_SECONDS:
            return False, "qwen_web_local_rate_limited"
        if len(self._recent_jobs) >= _AUTOMATIC_WINDOW_LIMIT:
            return False, "qwen_web_local_rate_limited"
        return True, ""

    async def _assistant_snapshot(self, page: Any) -> tuple[int, str]:
        for selector in _QWEN_ASSISTANT_MESSAGES:
            try:
                locator = page.locator(selector)
                count = await locator.count()
                if count <= 0:
                    continue
                text = str(await locator.nth(count - 1).inner_text(timeout=1000) or "").strip()
                return count, text
            except Exception:
                continue
        return 0, ""

    async def _upload_media(self, page: Any, media_path: Path) -> None:
        upload = await _first_visible(page, ('input[type="file"]',))
        if upload is None:
            trigger = await _first_visible(page, _QWEN_UPLOAD_TRIGGERS)
            if trigger is None:
                raise RuntimeError("qwen_web_dom_changed")
            await trigger.click(timeout=3000)
            await page.wait_for_timeout(350)
            upload = await _first_visible(page, ('input[type="file"]',))
        if upload is None:
            raise RuntimeError("qwen_web_dom_changed")
        await upload.set_input_files(str(media_path), timeout=10000)
        await page.wait_for_timeout(800)
        body = await _bounded_body_text(page, limit=6000)
        if _contains_marker(body, _UPLOAD_ERROR_MARKERS):
            raise RuntimeError("qwen_web_upload_rejected")

    async def _submit_prompt(self, page: Any, prompt: str) -> None:
        composer = await _first_visible(page, _QWEN_COMPOSERS)
        if composer is None:
            raise RuntimeError("qwen_web_dom_changed")
        tag_name = str(await composer.evaluate("element => element.tagName") or "").lower()
        if tag_name == "textarea":
            await composer.fill(prompt)
        else:
            await composer.click()
            await composer.press("Control+A")
            await composer.press("Backspace")
            await composer.press_sequentially(prompt, delay=1)
        send = await _first_visible(page, _QWEN_SEND_BUTTONS)
        if send is not None:
            await send.click(timeout=3000)
        else:
            await composer.press("Enter")

    async def _wait_for_output(
        self,
        page: Any,
        *,
        baseline_count: int,
        timeout_seconds: float,
        output_max_chars: int,
    ) -> str:
        deadline = time.monotonic() + timeout_seconds
        last = ""
        stable = 0
        saw_result = False
        while time.monotonic() < deadline:
            await asyncio.sleep(0.75)
            body = await _bounded_body_text(page, limit=8000)
            if _contains_marker(body, _NETWORK_RISK_MARKERS):
                self._risk_blocked_until = time.time() + _NETWORK_RISK_COOLDOWN_SECONDS
                raise RuntimeError("qwen_web_network_risk_detected")
            if _contains_marker(body, _MANUAL_VERIFICATION_MARKERS):
                raise RuntimeError("qwen_web_manual_verification_required")
            count, text = await self._assistant_snapshot(page)
            if count > baseline_count and text:
                saw_result = True
                bounded = text[:output_max_chars]
                stable = stable + 1 if bounded == last else 0
                last = bounded
                stop = await _first_visible(page, _QWEN_STOP_BUTTONS)
                if stable >= 2 and stop is None:
                    return last
            elif saw_result:
                stable = 0
        if last:
            return last
        raise TimeoutError("qwen_web_generation_timeout")

    async def analyze(self, params: dict[str, Any]) -> dict[str, Any]:
        allowed, code = self._automatic_allowed()
        if not allowed:
            self._last_diagnostic_code = code
            return self._analysis_failure(code)
        token = str(params.get("media_token") or "")
        kind = str(params.get("kind") or "").strip().lower()
        if kind not in {"video", "audio"}:
            raise ValueError("qwen_web_media_kind_invalid")
        media_path = self._resolve_media_token(token)
        timeout_seconds = _bounded_float(params.get("timeout_seconds"), 120.0, 20.0, 300.0)
        output_max_chars = _bounded_int(params.get("output_max_chars"), 16000, 1000, 50000)
        prompt = str(params.get("prompt") or "").strip()[:4000]
        if not prompt:
            prompt = "请按时间线分析这段音视频，区分直接证据、推断和不确定项。"

        started = time.monotonic()
        self._active_job = True
        self._recent_jobs.append(time.time())
        try:
            async with self.browser.activity(QWEN_WEB_PLATFORM):
                page = await self.browser.page(QWEN_WEB_PLATFORM, headless=True)
                page_url = str(getattr(page, "url", "") or "")
                if not page_url.startswith("https://www.qianwen.com/"):
                    await page.goto(QWEN_WEB_HOME, wait_until="domcontentloaded", timeout=20000)
                    await page.wait_for_timeout(500)
                state, state_code = await self._page_state(page)
                if state != "ready":
                    self._state = state
                    self._last_diagnostic_code = state_code
                    return self._analysis_failure(state_code, started=started)
                baseline_count, _ = await self._assistant_snapshot(page)
                await self._upload_media(page, media_path)
                await self._submit_prompt(page, prompt)
                result = await self._wait_for_output(
                    page,
                    baseline_count=baseline_count,
                    timeout_seconds=timeout_seconds,
                    output_max_chars=output_max_chars,
                )
                if not result.strip():
                    raise RuntimeError("qwen_web_output_empty")
                self._state = "ready"
                self._last_diagnostic_code = ""
                self._last_probe_at = time.time()
                return {
                    "schema_version": 1,
                    "status": "ok",
                    "kind": kind,
                    "text": result,
                    "diagnostic_code": "",
                    "page_contract_version": QWEN_WEB_PAGE_CONTRACT,
                    "elapsed_ms": int((time.monotonic() - started) * 1000),
                }
        except TimeoutError:
            code = "qwen_web_generation_timeout"
        except RuntimeError as exc:
            raw = str(exc or "")
            code = raw if raw.startswith("qwen_web_") else "qwen_web_process_failed"
        except Exception:
            code = "qwen_web_process_failed"
        finally:
            self._active_job = False
        if code in {"qwen_web_network_risk_detected", "qwen_web_manual_verification_required"}:
            self._state = "manual_verification_required"
        elif code == "qwen_web_login_required":
            self._state = "login_required"
        elif code == "qwen_web_dom_changed":
            self._state = "dom_changed"
        self._last_diagnostic_code = code
        return self._analysis_failure(code, started=started)

    @staticmethod
    def _analysis_failure(code: str, *, started: float | None = None) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "status": "failed",
            "text": "",
            "diagnostic_code": str(code or "qwen_web_process_failed"),
            "page_contract_version": QWEN_WEB_PAGE_CONTRACT,
            "elapsed_ms": int((time.monotonic() - started) * 1000) if started is not None else 0,
        }

    async def close(self) -> None:
        await self.browser.close()
        with suppress(Exception):
            for path in self.staging_root.iterdir():
                if path.is_dir() and path.name.startswith("job_"):
                    shutil.rmtree(path, ignore_errors=True)


__all__ = [
    "QWEN_WEB_HOME",
    "QWEN_WEB_PAGE_CONTRACT",
    "QWEN_WEB_PLATFORM",
    "QwenWebRuntime",
]
