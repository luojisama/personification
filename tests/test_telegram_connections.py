from __future__ import annotations

import asyncio
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from ._loader import load_personification_module

mod = load_personification_module("plugin.personification.core.telegram_connections")


class FakeBot:
    def __init__(self, settings, driver):
        self.self_id = settings["token"].split(":")[0]
        self.adapter = SimpleNamespace(bot_connect=lambda bot: driver.update({bot.self_id: bot}), bot_disconnect=lambda bot: driver.pop(bot.self_id, None))
        self.webhook = ""
        self.calls = []

    async def get_me(self):
        return SimpleNamespace(id=int(self.self_id), username="test_bot")

    async def get_webhook_info(self):
        return SimpleNamespace(url=self.webhook)

    async def delete_webhook(self, **kwargs):
        self.calls.append(kwargs)
        self.webhook = ""


def service(tmp_path, monkeypatch, factory=FakeBot):
    secrets_module = load_personification_module("plugin.personification.core.qzone_credentials")
    monkeypatch.setattr(secrets_module, "_restrict_secret_file_permissions", lambda p: None)
    bots = {}
    manager = mod.TelegramConnections(tmp_path, get_bots=lambda: bots, driver=lambda: bots, factory=factory)
    return manager


SETTINGS = {"token": "123:" + "a" * 30, "scope": "all"}


def test_private_unicode_does_not_consume_connection_test(tmp_path, monkeypatch):
    import time

    manager = service(tmp_path, monkeypatch)
    manager.records["connection"] = SETTINGS.copy()
    manager.nonces["connection"] = ("test_nonce", time.time() + 600)
    for text in ("你好", "😀", "/start pf_测试", "/start"):
        assert manager.consume_test("123", text) is False
        assert "connection" in manager.nonces
    assert manager.consume_test("123", "/start pf_test_nonce") is True
    assert manager.consume_test("123", "/start pf_test_nonce") is False


def test_secret_store_public_dto_and_duplicate_identity(tmp_path, monkeypatch):
    manager = service(tmp_path, monkeypatch)
    async def run():
        row = await manager.save(SETTINGS)
        assert SETTINGS["token"] not in str(row)
        assert not row["enabled"]
        assert row["status"]["credentials"] == "verified"
        assert row["status"]["delivery"] == "unverified"
        with pytest.raises(mod.ConnectionFault, match="receiver_conflict"):
            await manager.save(SETTINGS)
        restored = mod.TelegramConnections(tmp_path, get_bots=lambda: {}, driver=lambda: None)
        assert restored.list()[0]["bot_id"] == "123"
        assert SETTINGS["token"] not in str(restored.list())
    asyncio.run(run())


def test_webhook_requires_explicit_migration(tmp_path, monkeypatch):
    created = []
    def factory(settings, driver):
        bot = FakeBot(settings, driver)
        bot.webhook = "https://old.test/receiver"
        created.append(bot)
        return bot
    manager = service(tmp_path, monkeypatch, factory)
    async def poll(_ident, _bot):
        await asyncio.sleep(0)
    monkeypatch.setattr(manager, "_poll", poll)
    async def run():
        row = await manager.save(SETTINGS)
        with pytest.raises(mod.ConnectionFault, match="webhook_conflict"):
            await manager.start(row["id"])
        assert not created[-1].calls
        assert manager.status(row["id"])["last_error"] == "webhook_conflict"
        result = await manager.start(row["id"], migrate_webhook=True)
        assert result["state"] == "running"
        assert created[-1].calls == [{"drop_pending_updates": False}]
        assert result["test_url"].startswith("https://t.me/test_bot?start=pf_")
        nonce = manager.nonces[row["id"]][0]
        assert not manager.consume_test("456", "/start pf_" + nonce)
        assert manager.consume_test("123", "/start pf_" + nonce)
        assert not manager.consume_test("123", "/start pf_" + nonce)
        await manager.stop(row["id"])
    asyncio.run(run())


def test_durable_claim_and_chat_alias_are_bot_scoped(tmp_path, monkeypatch):
    manager = service(tmp_path, monkeypatch)
    assert manager.claim("a", "1")
    assert not manager.claim("a", "1")
    assert manager.claim("b", "1")
    assert manager.canonical_chat("a", "-10", "-10010") == "-10"
    assert manager.canonical_chat("a", "-10010") == "-10"
    assert manager.canonical_chat("b", "-10010") == "-10010"


def test_stop_waits_for_handler_does_not_cancel_delivery(tmp_path, monkeypatch):
    manager = service(tmp_path, monkeypatch)
    async def run():
        row = await manager.save(SETTINGS)
        released = asyncio.Event()
        delivered = []
        async def handler():
            await released.wait()
            delivered.append(True)
        manager.tasks[row["id"]] = asyncio.create_task(handler())
        stop = asyncio.create_task(manager.stop(row["id"]))
        await asyncio.sleep(0)
        assert not stop.done()
        released.set()
        await stop
        assert delivered == [True]
    asyncio.run(run())


def test_delete_and_queued_start_cannot_orphan_polling_bot(tmp_path, monkeypatch):
    manager = service(tmp_path, monkeypatch)
    async def poll(ident, bot):
        try:
            await manager.stop_events[ident].wait()
        finally:
            manager.bots.pop(ident, None)
            bot.adapter.bot_disconnect(bot)
    monkeypatch.setattr(manager, "_poll", poll)
    async def run():
        row = await manager.save(SETTINGS)
        ident = row["id"]
        await manager.start(ident)
        async with manager.lock:
            delete = asyncio.create_task(manager.delete(ident))
            start = asyncio.create_task(manager.start(ident))
            await asyncio.sleep(0)
        results = await asyncio.gather(delete, start, return_exceptions=True)
        assert results[0] is None
        assert isinstance(results[1], mod.ConnectionFault)
        assert results[1].code == "not_found"
        assert ident not in manager.records and ident not in manager.bots
        assert ident not in manager.tasks
        assert not manager.get_bots()
    asyncio.run(run())


def test_official_b20_bot_factory_uses_real_typed_api_without_setup(tmp_path):
    import nonebot
    import nonebot.adapters
    wheel_adapters = Path(os.environ.get("PERSONIFICATION_TELEGRAM_ADAPTERS", "D:/test_artifacts/personification/telegram-20261010/deps/nonebot/adapters"))
    if wheel_adapters.is_dir() and str(wheel_adapters) not in nonebot.adapters.__path__:
        nonebot.adapters.__path__.append(str(wheel_adapters))
    pytest.importorskip("nonebot.adapters.telegram")
    from nonebot.config import Config, Env
    from nonebot.drivers import combine_driver
    from nonebot.drivers.fastapi import Driver
    from nonebot.drivers.httpx import Mixin
    driver = combine_driver(Driver, Mixin)(Env(), Config())
    bot = mod.official_bot(mod.validate_settings(SETTINGS), driver)
    assert bot.self_id == "123"
    assert not bot.adapter.bots
    assert bot.adapter.personification_managed
    from nonebot.adapters.telegram import Bot
    assert isinstance(bot, Bot)


@pytest.mark.parametrize("field,value", [("api_server", "file:///secret"), ("api_server", "https://name:secret@example.test"), ("proxy", "file:///x"), ("scope", "everywhere")])
def test_invalid_config_rejected_without_exposing_secret(field, value):
    with pytest.raises(mod.ConnectionFault) as exc:
        mod.validate_settings({**SETTINGS, field: value})
    assert SETTINGS["token"] not in str(exc.value)


def test_poll_conflict_stops_and_preserves_durable_offset(tmp_path, monkeypatch):
    import nonebot.adapters
    adapters = Path(os.environ.get("PERSONIFICATION_TELEGRAM_ADAPTERS", "D:/test_artifacts/personification/telegram-20261010/deps/nonebot/adapters"))
    if adapters.is_dir() and str(adapters) not in nonebot.adapters.__path__:
        nonebot.adapters.__path__.append(str(adapters))
    pytest.importorskip("nonebot.adapters.telegram")
    from nonebot.adapters.telegram.event import Event
    manager = service(tmp_path, monkeypatch)
    calls = []
    class PollBot(FakeBot):
        async def get_updates(self, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                return [SimpleNamespace(update_id=40, model_dump=lambda **kw: {"invalid": True}), SimpleNamespace(update_id=41, model_dump=lambda **kw: {"valid": True})]
            raise mod.ConnectionFault("receiver_conflict")
        async def handle_event(self, event):
            self.calls.append(event)
    def parse(value):
        if "invalid" in value:
            raise ValueError("unsupported type")
        return value
    monkeypatch.setattr(Event, "parse_event", parse)
    async def run():
        bot = PollBot(SETTINGS, {})
        await manager._poll("fixture", bot)
        assert bot.calls == [{"valid": True}]
        assert calls[1]["offset"] == 42
        assert manager.errors["fixture"] == "receiver_conflict"
        with manager.db() as db:
            assert db.execute("SELECT value FROM offsets WHERE bot='123'").fetchone()[0] == 42
    asyncio.run(run())


def test_stop_waits_for_guarded_send_and_blocks_new_send(tmp_path, monkeypatch):
    manager = service(tmp_path, monkeypatch)
    async def run():
        row = await manager.save(SETTINGS)
        bot = FakeBot(SETTINGS, {})
        bot.adapter.personification_managed = True
        manager.bots[row["id"]] = bot
        release = asyncio.Event()
        async def operation():
            await release.wait()
            return "sent"
        active = asyncio.create_task(manager.send_guarded(bot, operation))
        await asyncio.sleep(0)
        # Mark stopped before draining. The real lifecycle has a poll task.
        manager.tasks[row["id"]] = asyncio.create_task(asyncio.sleep(0))
        stopped = asyncio.create_task(manager.stop(row["id"]))
        await asyncio.sleep(0)
        with pytest.raises(mod.ConnectionFault, match="connection_stopped"):
            await manager.send_guarded(bot, operation)
        assert not stopped.done()
        release.set()
        assert await active == "sent"
        await stopped
    asyncio.run(run())


def test_rooms_require_observed_scope_and_default_shadow(tmp_path, monkeypatch):
    manager = service(tmp_path, monkeypatch)
    with pytest.raises(mod.ConnectionFault, match="room_not_observed"):
        manager.update_room("a", {"chat_id": "-1", "participation": "on"})
    row = manager.room("a", "-1", "2", scope_key="trusted-scope")
    assert row["participation"] == "shadow"
    assert row["proactive_enabled"] is False
    changed = manager.update_room("a", {"chat_id": "-1", "topic_id": "2", "participation": "on", "scope_key": "forged"})
    assert changed["scope_key"] == "trusted-scope"
    assert manager.rooms("b") == []
