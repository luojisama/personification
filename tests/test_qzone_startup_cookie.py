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


def test_startup_cookie_refresh_filters_telegram_before_bot_id_deduplication() -> None:
    from .test_telegram_bridge import bot as telegram_bot

    native, telegram_calls = telegram_bot()
    first_qq = SimpleNamespace(self_id=native.self_id)
    second_qq = SimpleNamespace(self_id="456")
    seen = []
    async def update_qzone_cookie(bot, *, connected_bot_ids):
        seen.append((bot, connected_bot_ids))
        return True, "ok"
    ok = asyncio.run(qzone_startup.refresh_qzone_cookie_on_available_bot(
        enabled=True,
        get_bots=lambda: {"telegram": native, "qq": first_qq, "qq-other": second_qq},
        update_qzone_cookie=update_qzone_cookie, logger=_Logger(), wait_seconds=0,
    ))
    assert ok is True
    assert seen == [(first_qq, ("123", "456")), (second_qq, ("123", "456"))]
    assert telegram_calls == []


def test_startup_cookie_refresh_skips_telegram_only() -> None:
    from .test_telegram_bridge import bot as telegram_bot

    native, telegram_calls = telegram_bot()
    seen = []
    async def update_qzone_cookie(bot, **kwargs):
        seen.append(bot)
        return True, "unexpected"
    ok = asyncio.run(qzone_startup.refresh_qzone_cookie_on_available_bot(
        enabled=True, get_bots=lambda: {"telegram": native},
        update_qzone_cookie=update_qzone_cookie, logger=_Logger(), wait_seconds=0,
    ))
    assert ok is False
    assert seen == []
    assert telegram_calls == []


def test_startup_cookie_refresh_waits_for_qq_when_only_telegram_is_connected() -> None:
    from .test_telegram_bridge import bot as telegram_bot

    native, telegram_calls = telegram_bot()
    qq_bot = SimpleNamespace(self_id="456")
    snapshots = iter(({"telegram": native}, {"telegram": native, "qq": qq_bot}))
    waits, seen = [], []
    async def sleep(delay):
        waits.append(delay)
    async def update_qzone_cookie(bot, *, connected_bot_ids):
        seen.append((bot, connected_bot_ids))
        return True, "ok"
    ok = asyncio.run(qzone_startup.refresh_qzone_cookie_on_available_bot(
        enabled=True, get_bots=lambda: next(snapshots), update_qzone_cookie=update_qzone_cookie,
        logger=_Logger(), wait_seconds=5, poll_interval=.25, sleep=sleep,
    ))
    assert ok is True
    assert waits == [.25]
    assert seen == [(qq_bot, ("456",))]
    assert telegram_calls == []
