from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from ._loader import load_personification_module

notify = load_personification_module("plugin.personification.core.notify")


@pytest.mark.parametrize("telegram_first", [True, False])
def test_admin_notification_selects_qq_from_mixed_bots(telegram_first):
    from .test_telegram_bridge import bot as telegram_bot

    native, telegram_calls = telegram_bot()
    qq_calls = []
    async def call_api(api, **kwargs):
        qq_calls.append((api, kwargs))
        return {"message_id": 42}
    qq_bot = SimpleNamespace(self_id=native.self_id, call_api=call_api)
    items = [("telegram", native), ("qq", qq_bot)]
    bots = dict(items if telegram_first else reversed(items))

    assert asyncio.run(notify.send_to_admin(lambda: bots, "10001", "启动通知")) is True
    assert qq_calls == [("send_private_msg", {"user_id": 10001, "message": "启动通知"})]
    assert telegram_calls == []


def test_admin_notification_skips_telegram_only():
    from .test_telegram_bridge import bot as telegram_bot

    native, telegram_calls = telegram_bot()
    assert asyncio.run(notify.send_to_admin(lambda: {"telegram": native}, "10001", "启动通知")) is False
    assert telegram_calls == []


def test_startup_admin_notification_keeps_deduplication_with_mixed_bots():
    from .test_telegram_bridge import bot as telegram_bot

    native, telegram_calls = telegram_bot()
    qq_calls = []
    async def call_api(api, **kwargs):
        qq_calls.append((api, kwargs))
        return {"message_id": 42}
    qq_bot = SimpleNamespace(self_id="123", call_api=call_api)
    sent = asyncio.run(notify.startup_notify_admins(
        get_bots=lambda: {"telegram": native, "qq": qq_bot},
        superusers=["10001"], plugin_admins=["10001", "10002"], message="启动通知",
    ))
    assert sent == 2
    assert [payload["user_id"] for api, payload in qq_calls if api == "send_private_msg"] == [10001, 10002]
    assert telegram_calls == []
