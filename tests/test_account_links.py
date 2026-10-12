"""Verified private account links using synthetic IDs and captured transports."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import logging
import os
from pathlib import Path
from types import SimpleNamespace

import nonebot.adapters
import pytest
from nonebot.adapters.onebot.v11 import Bot as QQBot, GroupMessageEvent, Message, PrivateMessageEvent
from ._loader import load_personification_module

_adapter_root = Path(os.environ.get("PERSONIFICATION_TELEGRAM_ADAPTERS", "D:/test_artifacts/personification/telegram-20261010/deps/nonebot/adapters"))
if _adapter_root.is_dir() and str(_adapter_root) not in nonebot.adapters.__path__:
    nonebot.adapters.__path__.append(str(_adapter_root))
tg_event = pytest.importorskip("nonebot.adapters.telegram.event")
from nonebot.adapters.telegram.bot import Bot as TelegramBot
from nonebot.adapters.telegram.config import BotConfig

links = load_personification_module("plugin.personification.core.account_links")
commands = load_personification_module("plugin.personification.handlers.account_link_commands")


@pytest.fixture
def setup(tmp_path, monkeypatch):
    db = load_personification_module("plugin.personification.core.db")
    store_module = load_personification_module("plugin.personification.core.data_store")
    monkeypatch.setattr(db, "_db_path", tmp_path / "personification.db")
    db.init_db_sync(tmp_path)
    clock = [1700000000.0]
    store = store_module.DataStore(SimpleNamespace(personification_data_dir=str(tmp_path)))
    service = links.AccountLinkService(store, clock=lambda: clock[0])
    return service, store, clock


def challenge(service, *, tg_bot="123", tg_user="42", qq_bot="456", qq_user="42"):
    claim = service.begin(tg_bot_id=tg_bot, tg_user_id=tg_user, qq_bot_id=qq_bot, qq_user_id=qq_user, issue_code=True)
    assert service.mark_delivery(claim.request_id, confirmed=True)
    return claim


def test_confirmation_uses_both_real_ids_and_one_time_hash(setup):
    service, store, clock = setup
    claim = challenge(service)
    assert claim.code not in repr(claim)
    assert claim.code not in json.dumps(store.load_sync(links.NAMESPACE))
    assert "code_hash" not in json.dumps(service.pending_telegram("123", "42"))
    assert service.resolve_telegram("123", "42") is None
    for bot, user in (("999", "42"), ("456", "999")):
        with pytest.raises(links.AccountLinkError, match="invalid_or_expired_code"):
            service.confirm(qq_bot_id=bot, qq_user_id=user, code=claim.code)
    linked = service.confirm(qq_bot_id="456", qq_user_id="42", code=claim.code)
    assert linked.telegram_scope == ("p8:telegramb3:123", "p8:telegramb3:123:u2:42")
    assert linked.qq_user_id == "42" and linked.link_id
    assert links.AccountLinkService(store).resolve_telegram("123", "42") == linked
    assert service.resolve_telegram("999", "42") is None
    assert service.links_for_qq("456", "42") == [linked]
    with pytest.raises(links.AccountLinkError, match="invalid_or_expired_code"):
        service.confirm(qq_bot_id="456", qq_user_id="42", code=claim.code)


def test_expiry_cooldown_attempts_and_revocation_persist(setup):
    service, store, clock = setup
    claim = challenge(service)
    with pytest.raises(links.AccountLinkError, match="rate_limited"):
        challenge(service)
    for _ in range(5):
        with pytest.raises(links.AccountLinkError, match="invalid_or_expired_code"):
            service.confirm(qq_bot_id="456", qq_user_id="42", code="wrong")
    with pytest.raises(links.AccountLinkError, match="rate_limited"):
        service.confirm(qq_bot_id="456", qq_user_id="42", code=claim.code)
    clock[0] += 61
    assert service.revoke_qq("456", "42") == 1
    with pytest.raises(links.AccountLinkError, match="invalid_or_expired_code"):
        service.confirm(qq_bot_id="456", qq_user_id="42", code=claim.code)
    claim = challenge(service)
    clock[0] += links.TTL_SECONDS
    assert service.pending_telegram("123", "42") is None
    with pytest.raises(links.AccountLinkError, match="invalid_or_expired_code"):
        service.confirm(qq_bot_id="456", qq_user_id="42", code=claim.code)


def test_unknown_delivery_and_replaced_request_never_confirm(setup):
    service, store, clock = setup
    first = service.begin(tg_bot_id="123", tg_user_id="42", qq_bot_id="456", qq_user_id="42", issue_code=True)
    assert service.mark_delivery(first.request_id, confirmed=False)
    assert service.pending_telegram("123", "42")["state"] == "delivery_unverified"
    with pytest.raises(links.AccountLinkError, match="invalid_or_expired_code"):
        service.confirm(qq_bot_id="456", qq_user_id="42", code=first.code)
    clock[0] += 61
    second = challenge(service)
    assert not service.mark_delivery(first.request_id, confirmed=True)
    with pytest.raises(links.AccountLinkError, match="invalid_or_expired_code"):
        service.confirm(qq_bot_id="456", qq_user_id="42", code=first.code)
    assert service.confirm(qq_bot_id="456", qq_user_id="42", code=second.code)


def test_atomic_confirmation_and_unlink_rebind_new_fingerprint(setup):
    service, store, clock = setup
    claim = challenge(service)
    def confirm():
        try:
            return service.confirm(qq_bot_id="456", qq_user_id="42", code=claim.code)
        except links.AccountLinkError:
            return None
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: confirm(), range(2)))
    assert sum(item is not None for item in results) == 1
    previous = service.resolve_telegram("123", "42")
    assert service.revoke_telegram("123", "42") == 1
    assert service.links_for_qq("456", "42") == []
    clock[0] += 61
    claim = challenge(service)
    current = service.confirm(qq_bot_id="456", qq_user_id="42", code=claim.code)
    assert previous.link_id != current.link_id


def test_cancel_pending_preserves_verified_link(setup):
    service, store, clock = setup
    claim = challenge(service)
    linked = service.confirm(qq_bot_id="456", qq_user_id="42", code=claim.code)
    # Another TG Bot may be requesting the same QQ owner. Rejecting that
    # request must not revoke the existing verified account.
    claim = challenge(service, tg_bot="789", tg_user="43")
    assert service.cancel_pending_qq("456", "42") == 1
    assert service.resolve_telegram("123", "42") == linked
    assert service.cancel_pending_telegram("123", "42") == 0
    assert service.resolve_telegram("123", "42") == linked
    with pytest.raises(links.AccountLinkError, match="invalid_or_expired_code"):
        service.confirm(qq_bot_id="456", qq_user_id="42", code=claim.code)


def test_waiting_offline_has_no_usable_credential(setup):
    service, store, clock = setup
    claim = service.begin(tg_bot_id="123", tg_user_id="42", qq_user_id="42")
    assert claim.code == ""
    assert service.pending_telegram("123", "42")["state"] == "awaiting_qq"
    assert service.resolve_telegram("123", "42") is None
    assert service.revoke_telegram("123", "42") == 1


def tg(*, text="/bindqq 42", user=42, private=True, **extra):
    return tg_event.Event.parse_event({"update_id": 1, "message": {"message_id": 10, "date": 1700000000,
        "chat": {"id": user if private else -10001, "type": "private" if private else "supergroup"},
        "from": {"id": user, "is_bot": False, "first_name": "Synthetic"}, "text": text, **extra}})


def qq(*, text, user=42, private=True, subtype="friend"):
    value = dict(time=1700000000, self_id=456, post_type="message", message_type="private" if private else "group",
                 sub_type=subtype if private else "normal", user_id=user, message_id=10, message=Message(text),
                 original_message=Message(text), raw_message=text, font=0, sender={"user_id": user, "nickname": "Synthetic"})
    if not private:
        value["group_id"] = 999
    return (PrivateMessageEvent if private else GroupMessageEvent).model_validate(value)


def bots(*, qq_bot_id="456", receipt=None, failure=False, friend=True):
    calls = []
    class Transport:
        config = SimpleNamespace(nickname=set())
        async def _call_api(self, _bot, api, **kwargs):
            calls.append((api, deepcopy(kwargs)))
            if api == "get_friend_list":
                return [{"user_id": 42}] if friend else []
            if failure:
                raise RuntimeError("synthetic transport failure")
            if api == "send_private_msg":
                return receipt if receipt is not None else {"message_id": -10}
            return {"message_id": 99, "date": 1700000000, "chat": {"id": kwargs["chat_id"], "type": "private"}, "text": kwargs.get("text", "")}
    native = TelegramBot(Transport(), "123", config=BotConfig(token="123:isolated-test-token"))
    native.username = "shiro_bot"
    return native, QQBot(Transport(), qq_bot_id), calls


def test_real_typed_two_party_commands_share_no_secret_with_tg(setup):
    service, store, clock = setup
    native, qbot, calls = bots()
    result = asyncio.run(commands.handle_account_link_command(native, tg(), get_bots=lambda: {"456": qbot}, service=service))
    credential_message = next(data["message"] for api, data in calls if api == "send_private_msg")
    code = credential_message.split("/bindtg ")[1].split()[0]
    assert code not in result and code not in json.dumps(store.load_sync(links.NAMESPACE))
    assert service.resolve_telegram("123", "42") is None
    wrong = asyncio.run(commands.handle_account_link_command(qbot, qq(text=f"/bindtg {code}", user=43), get_bots=lambda: {}, service=service))
    assert "核实" in wrong
    result = asyncio.run(commands.handle_account_link_command(qbot, qq(text=f"/bindtg {code}"), get_bots=lambda: {}, service=service))
    assert "已确认绑定" in result
    assert service.resolve_telegram("123", "42")
    for current_bot, event in ((native, tg(text="/bindstatus")), (qbot, qq(text="/bindstatus"))):
        result = asyncio.run(commands.handle_account_link_command(current_bot, event, get_bots=lambda: {}, service=service))
        assert "管理员权限独立" in result
    result = asyncio.run(commands.handle_account_link_command(qbot, qq(text="/unbind"), get_bots=lambda: {}, service=service))
    assert "已解除" in result and service.resolve_telegram("123", "42") is None


@pytest.mark.parametrize("private,extra", [(False, {}), (True, {"forward_date": 1700000000})])
def test_group_forwarded_commands_cannot_issue_credentials(setup, private, extra):
    service, store, clock = setup
    native, qbot, calls = bots()
    result = asyncio.run(commands.handle_account_link_command(native, tg(private=private, **extra), get_bots=lambda: {"456": qbot}, service=service))
    assert result and calls == []
    assert service.pending_telegram("123", "42") is None


@pytest.mark.parametrize("private,subtype", [(False, "friend"), (True, "group")])
def test_qq_group_or_temporary_session_cannot_confirm(setup, private, subtype):
    service, store, clock = setup
    native, qbot, calls = bots()
    claim = challenge(service)
    result = asyncio.run(commands.handle_account_link_command(qbot, qq(text=f"/bindtg {claim.code}", private=private, subtype=subtype), get_bots=lambda: {}, service=service))
    assert "好友私聊" in result and calls == []
    assert service.resolve_telegram("123", "42") is None


def test_offline_ambiguous_or_not_friend_never_link(setup):
    service, store, clock = setup
    native, qbot, calls = bots()
    result = asyncio.run(commands.handle_account_link_command(native, tg(), get_bots=lambda: {}, service=service))
    assert "离线" in result and service.pending_telegram("123", "42")
    clock[0] += 61
    other = bots(qq_bot_id="789")[1]
    result = asyncio.run(commands.handle_account_link_command(native, tg(), get_bots=lambda: {"456": qbot, "789": other}, service=service))
    assert "多个 QQ Bot" in result and calls == []
    native, qbot, calls = bots(friend=False)
    result = asyncio.run(commands.handle_account_link_command(native, tg(), get_bots=lambda: {"456": qbot}, service=service))
    assert "未核实" in result
    assert service.resolve_telegram("123", "42") is None


@pytest.mark.parametrize("receipt,failure", [({}, False), (None, True)])
def test_empty_or_unknown_send_revokes_credential_without_replay(setup, receipt, failure):
    service, store, clock = setup
    native, qbot, calls = bots(receipt=receipt, failure=failure)
    result = asyncio.run(commands.handle_account_link_command(native, tg(), get_bots=lambda: {"456": qbot}, service=service))
    assert "凭据已撤销" in result
    code = next(data["message"] for api, data in calls if api == "send_private_msg").split("/bindtg ")[1].split()[0]
    assert sum(api == "send_private_msg" for api, data in calls) == 1
    with pytest.raises(links.AccountLinkError, match="invalid_or_expired_code"):
        service.confirm(qq_bot_id="456", qq_user_id="42", code=code)


def test_inbound_event_log_masks_credential_including_group_and_other_bot():
    commands.protect_account_command_logs()
    commands.protect_account_command_logs()
    code = "isolated-secret-confirmation-credential"
    for event in (qq(text=f"/bindtg {code}"), qq(text=f"/bindtg {code}", private=False), tg(text=f"/bindtg@other_bot {code}")):
        assert code not in event.get_log_string()
        assert code in commands._text(event)
    native, _, _ = bots()
    assert commands.parse_command(tg(text="/bindqq@shiro_bot 42"), native) == ("bindqq", ["42"])
    assert commands.parse_command(tg(text="/bindqq@other_bot 42"), native) is None


def test_sensitive_qq_send_guard_is_context_local(monkeypatch):
    from nonebot.adapters.onebot.v11 import adapter
    records = []
    monkeypatch.setattr(adapter, "log", lambda *args, **kwargs: records.append((args, kwargs)))
    commands.protect_qq_account_send_logs()
    commands.protect_qq_account_send_logs()
    async def run():
        token = commands._private_qq_send.set(True)
        try:
            adapter.log("DEBUG", "synthetic sensitive API payload")
            async def independent():
                adapter.log("DEBUG", "ordinary concurrent operation")
            # A task created outside the private context remains visible.
            from contextvars import Context
            task = Context().run(asyncio.create_task, independent())
            await task
        finally:
            commands._private_qq_send.reset(token)
        adapter.log("DEBUG", "ordinary later operation")
    asyncio.run(run())
    assert [args[1] for args, kwargs in records] == ["ordinary concurrent operation", "ordinary later operation"]


def test_real_onebot_api_log_and_wire_frame_credential_are_protected(monkeypatch):
    from nonebot.adapters.onebot.v11.adapter import Adapter
    from nonebot.adapters.onebot.v11 import adapter as adapter_module
    records = []
    monkeypatch.setattr(adapter_module, "log", lambda *args, **kwargs: records.append(args))
    commands.protect_qq_account_send_logs()
    transport = object.__new__(Adapter)
    async def sent(_message):
        return None
    async def fetched(_seq, _timeout):
        return {"status": "ok", "retcode": 0, "data": {"message_id": -10}}
    transport.connections = {"456": SimpleNamespace(send=sent)}
    transport._result_store = SimpleNamespace(get_seq=lambda: 1, fetch=fetched)
    transport.driver = SimpleNamespace(config=SimpleNamespace(api_timeout=15))
    async def run():
        token = commands._private_qq_send.set(True)
        try:
            assert await Adapter._call_api(transport, SimpleNamespace(self_id="456"), "send_private_msg", user_id=42, message="/bindtg isolated-private-code-credential") == {"message_id": -10}
        finally:
            commands._private_qq_send.reset(token)
        await Adapter._call_api(transport, SimpleNamespace(self_id="456"), "send_private_msg", user_id=42, message="ordinary")
    asyncio.run(run())
    assert len(records) == 1 and "Calling API" in records[0][1]
    for text in ('wire frame: {"message":"/bindtg isolated-private-code-credential"}', r'wire frame: {"message":"/bindtg\nisolated-private-code-credential"}'):
        record = logging.LogRecord("websockets.server", logging.DEBUG, __file__, 1, text, (), None)
        logger = logging.getLogger("websockets.server")
        assert logger.filter(record)
        assert "isolated-private-code-credential" not in record.getMessage()


def test_matcher_blocks_other_bot_credential_and_edited_events(setup):
    from nonebot.exception import StopPropagation
    from nonebot.message import check_and_run_matcher
    service, store, clock = setup
    native, qbot, calls = bots()
    matchers = commands.register_account_link_commands(plugin_config=SimpleNamespace(), get_bots=lambda: {"456": qbot}, service=service)
    async def run():
        incoming = tg(text="/bindtg@other_bot isolated-secret-confirmation-credential")
        with pytest.raises(StopPropagation):
            await check_and_run_matcher(matchers["account_link_commands"], native, incoming, {})
        # Editing does not invoke the account handler or fall through to the
        # Telegram archive matcher, even if the command names this Bot.
        payload = {"update_id": 2, "edited_message": {"message_id": 10, "date": 1700000000,
            "edit_date": 1700000100, "chat": {"id": 42, "type": "private"},
            "from": {"id": 42, "is_bot": False, "first_name": "Synthetic"}, "text": "/bindqq 42"}}
        with pytest.raises(StopPropagation):
            await check_and_run_matcher(matchers["account_link_edited_guard"], native, tg_event.Event.parse_event(payload), {})
    try:
        asyncio.run(run())
        assert calls == [] and service.resolve_telegram("123", "42") is None
    finally:
        for matcher in matchers.values():
            matcher.destroy()


def test_corrupt_namespace_rejects_without_granting(setup):
    service, store, clock = setup
    store.save_sync(links.NAMESPACE, {"version": 99, "links": []})
    with pytest.raises(links.AccountLinkError, match="store_invalid"):
        service.resolve_telegram("123", "42")
