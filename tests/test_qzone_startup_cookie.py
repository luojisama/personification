from __future__ import annotations

import asyncio
from types import SimpleNamespace

from ._loader import load_personification_module


qzone_startup = load_personification_module("plugin.personification.core.qzone_startup")


class _Logger:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    def info(self, message: str) -> None:
        self.messages.append(("info", message))

    def warning(self, message: str) -> None:
        self.messages.append(("warning", message))


def test_startup_cookie_refresh_uses_each_available_bot() -> None:
    bot = SimpleNamespace(self_id="123")
    seen: list[object] = []
    logger = _Logger()

    async def update_qzone_cookie(
        value: object,
        *,
        connected_bot_ids: tuple[str, ...] | None = None,
    ) -> tuple[bool, str]:
        seen.append(value)
        assert connected_bot_ids == ("123",)
        return True, "cookie"

    ok = asyncio.run(
        qzone_startup.refresh_qzone_cookie_on_available_bot(
            enabled=True,
            get_bots=lambda: {"123": bot},
            update_qzone_cookie=update_qzone_cookie,
            logger=logger,
            wait_seconds=0,
        )
    )

    assert ok is True
    assert seen == [bot]
    assert any(level == "info" and "隔离刷新" in message for level, message in logger.messages)


def test_startup_cookie_refresh_skips_without_bot() -> None:
    seen: list[object] = []
    logger = _Logger()

    async def update_qzone_cookie(
        value: object,
        *,
        connected_bot_ids: tuple[str, ...] | None = None,
    ) -> tuple[bool, str]:
        seen.append(value)
        return True, "cookie"

    ok = asyncio.run(
        qzone_startup.refresh_qzone_cookie_on_available_bot(
            enabled=True,
            get_bots=lambda: {},
            update_qzone_cookie=update_qzone_cookie,
            logger=logger,
            wait_seconds=0,
        )
    )

    assert ok is False
    assert seen == []
    assert any(level == "warning" and "未找到有效 Bot 实例" in message for level, message in logger.messages)


def test_startup_cookie_refresh_respects_disabled_flag() -> None:
    seen: list[object] = []
    logger = _Logger()

    async def update_qzone_cookie(
        value: object,
        *,
        connected_bot_ids: tuple[str, ...] | None = None,
    ) -> tuple[bool, str]:
        seen.append(value)
        return True, "cookie"

    ok = asyncio.run(
        qzone_startup.refresh_qzone_cookie_on_available_bot(
            enabled=False,
            get_bots=lambda: {"123": object()},
            update_qzone_cookie=update_qzone_cookie,
            logger=logger,
            wait_seconds=0,
        )
    )

    assert ok is False
    assert seen == []
    assert logger.messages == []


def test_startup_cookie_refresh_covers_every_bot_once() -> None:
    bots = {
        "10001": SimpleNamespace(self_id="10001"),
        "10002": SimpleNamespace(self_id="10002"),
    }
    seen: list[tuple[str, tuple[str, ...]]] = []
    logger = _Logger()

    async def update_qzone_cookie(
        value: object,
        *,
        connected_bot_ids: tuple[str, ...] | None = None,
    ) -> tuple[bool, str]:
        seen.append((str(getattr(value, "self_id")), tuple(connected_bot_ids or ())))
        return True, "ok"

    ok = asyncio.run(
        qzone_startup.refresh_qzone_cookie_on_available_bot(
            enabled=True,
            get_bots=lambda: bots,
            update_qzone_cookie=update_qzone_cookie,
            logger=logger,
            wait_seconds=0,
        )
    )

    assert ok is True
    assert sorted(seen) == [
        ("10001", ("10001", "10002")),
        ("10002", ("10001", "10002")),
    ]
