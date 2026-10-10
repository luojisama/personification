from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

from ._loader import load_personification_module

connections = load_personification_module("plugin.personification.core.telegram_connections")


@pytest.mark.parametrize("status,code,retry", [(409, "receiver_conflict", 0), (429, "rate_limited", 37), (401, "invalid_token", 0)])
def test_official_b20_preserves_safe_http_fault(monkeypatch, status, code, retry):
    import nonebot.adapters
    wheel = Path(os.environ.get("PERSONIFICATION_TELEGRAM_ADAPTERS", "D:/test_artifacts/personification/telegram-20261010/deps/nonebot/adapters"))
    if wheel.is_dir() and str(wheel) not in nonebot.adapters.__path__:
        nonebot.adapters.__path__.append(str(wheel))
    pytest.importorskip("nonebot.adapters.telegram")
    from nonebot.adapters.telegram import Adapter
    from nonebot.config import Config, Env
    from nonebot.drivers import Response, combine_driver
    from nonebot.drivers.fastapi import Driver
    from nonebot.drivers.httpx import Mixin
    requests = []
    async def request(self, setup):
        requests.append(setup)
        return Response(status, content=json.dumps({"ok": False, "error_code": status, "description": "do not expose credentials", "parameters": {"retry_after": retry}}))
    monkeypatch.setattr(Adapter, "request", request)
    driver = combine_driver(Driver, Mixin)(Env(), Config())
    bot = connections.official_bot(connections.validate_settings({"token": "123:fixture_token_000000000000", "scope": "all"}), driver)
    with pytest.raises(connections.ConnectionFault) as exc:
        asyncio.run(bot.get_updates(offset=43, timeout=20))
    assert exc.value.code == code
    assert exc.value.retry_after == retry
    assert len(requests) == 1
    assert "fixture_token" not in str(exc.value)
