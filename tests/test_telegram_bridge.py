"""Telegram b20 typed events through the real admission and reply buffer.

Only the Telegram transport/provider boundary is fake; archive state is isolated.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
import os
from pathlib import Path
from types import SimpleNamespace

import nonebot.adapters
import pytest
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageEvent, MessageSegment, PrivateMessageEvent
from ._loader import load_personification_module

_adapter_root = Path(os.environ.get("PERSONIFICATION_TELEGRAM_ADAPTERS", "D:/test_artifacts/personification/telegram-20261010/deps/nonebot/adapters"))
if _adapter_root.is_dir() and str(_adapter_root) not in nonebot.adapters.__path__:
    nonebot.adapters.__path__.append(str(_adapter_root))
tg_event = pytest.importorskip("nonebot.adapters.telegram.event")
from nonebot.adapters.telegram.bot import Bot as TelegramBot
from nonebot.adapters.telegram.config import BotConfig

bridge = load_personification_module("plugin.personification.handlers.telegram_reply_bridge")
interaction = load_personification_module("plugin.personification.core.telegram_interaction")
connections = load_personification_module("plugin.personification.core.telegram_connections")
rules = load_personification_module("plugin.personification.handlers.event_rules")
buffer = load_personification_module("plugin.personification.handlers.reply_buffer")
processor = load_personification_module("plugin.personification.handlers.reply_pipeline.processor")
config_module = load_personification_module("plugin.personification.config")


def event(*, update=1, mid=10, private=False, text="hello", topic=None, edited=False, **extra):
    message = {"message_id": mid, "date": 1700000000, "chat": {"id": 42 if private else -10001, "type": "private" if private else "supergroup", "title": "isolated"}, "from": {"id": 42, "is_bot": False, "first_name": "Alice"}, "text": text, **extra}
    if topic is not None:
        message.update(is_topic_message=True, message_thread_id=topic)
    if edited:
        message["edit_date"] = 1700000100
    return tg_event.Event.parse_event({"update_id": update, "edited_message" if edited else "message": deepcopy(message)})


def bot():
    calls = []
    class Transport:
        config = SimpleNamespace(nickname={"Shiro"})
        async def _call_api(self, _bot, api, **kwargs):
            calls.append((api, kwargs))
            return {"message_id": 99, "date": 1700000000, "chat": {"id": kwargs["chat_id"], "type": "private"}, "text": kwargs.get("text", "")}
    result = TelegramBot(Transport(), "123", config=BotConfig(token="123:isolated-test-token"))
    result.username = "shiro_bot"
    return result, calls


@pytest.fixture
def setup(tmp_path, monkeypatch):
    db = load_personification_module("plugin.personification.core.db")
    paths = load_personification_module("plugin.personification.core.paths")
    store = load_personification_module("plugin.personification.core.data_store")
    monkeypatch.setattr(db, "_db_path", tmp_path / "personification.db")
    monkeypatch.setattr(paths, "get_data_dir", lambda _cfg=None: tmp_path)
    db.init_db_sync(tmp_path)
    cfg = config_module.Config(personification_data_dir=str(tmp_path), personification_schedule_global=False, personification_agent_enabled=False)
    session_store = load_personification_module("plugin.personification.core.session_store")
    # Other suites exercise compression with process-global configuration. Bind
    # this real store to this fixture's database and restore all state on exit.
    monkeypatch.setattr(session_store, "connect_sync", db.connect_sync)
    monkeypatch.setattr(session_store, "_plugin_config", cfg)
    monkeypatch.setattr(session_store, "_compress_tool_caller", None)
    for name in ("_compress_tasks", "_compress_failures", "_compress_retry_tasks"):
        monkeypatch.setattr(session_store, name, {})
    store.init_data_store(cfg)
    utils = load_personification_module("plugin.personification.utils")
    monkeypatch.setattr(utils, "_upsert_chat_meta", lambda *_a, **_k: None)
    monkeypatch.setattr(rules, "is_group_muted", lambda *_: False)
    service = connections.TelegramConnections(tmp_path / "telegram", get_bots=lambda: {}, driver=lambda: None)
    monkeypatch.setattr(bridge, "get_connections", lambda _cfg: service)
    logger = SimpleNamespace(debug=lambda *_: None, info=lambda *_: None, warning=lambda *_: None, error=lambda *_: None)
    return cfg, service, logger, db


def wiring(setup, processed, *, deps=None):
    cfg, service, logger, db = setup
    if deps is None:
        deps = SimpleNamespace(types=processor.TypeDeps(object, MessageEvent, GroupMessageEvent, PrivateMessageEvent, Message), runtime=SimpleNamespace(user_policy_gate=None))
    async def rule(ev, state):
        return await rules.personification_rule(ev, state, sign_in_available=False, get_user_data=lambda _: {}, user_blacklist={}, logger=logger,
            group_event_cls=GroupMessageEvent, private_event_cls=PrivateMessageEvent, is_group_whitelisted=lambda *_: False,
            plugin_whitelist=[], load_prompt=lambda _: {}, load_proactive_state=lambda: {}, is_rest_time=lambda **_: True,
            probability=0, group_chat_follow_probability=0, looks_like_private_command=lambda _: False)
    async def core(bot, ev, state, current_deps):
        processed.append((ev, dict(state), current_deps))
    async def timer(*args, **kwargs):
        kwargs["delay"] = 0
        await buffer.run_buffer_timer(*args, **kwargs)
    return dict(personification_rule=rule, process_response_logic=core, reply_processor_deps=deps, msg_buffer={},
        message_cls=Message, message_segment_cls=MessageSegment, message_event_cls=MessageEvent,
        group_message_event_cls=GroupMessageEvent, private_message_event_cls=PrivateMessageEvent,
        poke_event_cls=object, logger=logger, plugin_config=cfg, run_buffer_timer=timer)


async def drain(processed, count):
    async def wait():
        while len(processed) < count:
            await asyncio.sleep(.005)
    await asyncio.wait_for(wait(), 2)


def test_native_check_tome_does_not_destroy_mentions_or_media():
    native, _ = bot()
    incoming = event(text="@shiro_bot hello", entities=[{"type": "mention", "offset": 0, "length": 10}], photo=[{"file_id": "photo-large", "file_unique_id": "unique", "width": 32, "height": 32}])
    native._check_tome(incoming)
    assert incoming.is_tome()
    assert bridge._directed(native, incoming)
    envelope = interaction.TelegramInteractionAdapter().normalize(incoming, bot_id="123")
    assert [media.ref for media in envelope.ordered_media] == ["tgfile:photo-large"]
    assert "hello" in envelope.text


def test_nickname_substring_is_not_a_direct_mention():
    native, _ = bot()
    incoming = event(text="Shiro今天的天气")
    native._check_tome(incoming)
    assert incoming.is_tome()  # Upstream nickname heuristic is deliberately broader.
    assert not bridge._directed(native, incoming)


def test_real_private_event_reaches_real_rule_and_buffer(setup):
    native, _ = bot()
    processed = []
    kwargs = wiring(setup, processed)
    async def run():
        await bridge.dispatch_telegram_reply(native, event(private=True), {}, **kwargs)
        await drain(processed, 1)
    asyncio.run(run())
    ev, state, _ = processed[0]
    assert state["attention_admitted"] is True
    assert ev.platform == "telegram"
    assert ev.user_id == "p8:telegramb3:123:u2:42"
    assert ev.get_plaintext() == "hello"


def test_group_mentions_reply_and_topics_are_separate(setup):
    native, _ = bot()
    processed = []
    kwargs = wiring(setup, processed)
    decisions = []
    actual_rule = kwargs["personification_rule"]
    async def observed_rule(ev, state):
        admitted = await actual_rule(ev, state)
        decisions.append((ev.message_id, ev.to_me, admitted))
        return admitted
    kwargs["personification_rule"] = observed_rule
    mention = event(update=10, topic=1, text="😀 @shiro_bot hi", entities=[{"type": "mention", "offset": 3, "length": 10}])
    reply = event(update=11, topic=2, reply_to_message={"message_id": 9, "date": 1700000000, "chat": {"id": -10001, "type": "supergroup"}, "from": {"id": 123, "is_bot": True, "first_name": "Bot"}, "text": "hi"})
    async def run():
        await bridge.dispatch_telegram_reply(native, mention, {}, **kwargs)
        await bridge.dispatch_telegram_reply(native, reply, {}, **kwargs)
        assert [entry[2] for entry in decisions] == [True, True], decisions
        await drain(processed, 2)
    asyncio.run(run())
    assert len({ev.group_id for ev, _, _ in processed}) == 2
    assert len({ev.message_id for ev, _, _ in processed}) == 2
    assert all(ev.to_me for ev, _, _ in processed)
    assert processed[1][0].reply_to_message_id.endswith(":m1:9")


def test_ambient_archives_edits_update_without_replies_and_duplicate_is_skipped(setup):
    native, _ = bot()
    processed = []
    kwargs = wiring(setup, processed)
    async def run():
        await bridge.dispatch_telegram_reply(native, event(text="old"), {}, **kwargs)
        await bridge.dispatch_telegram_reply(native, event(text="old"), {}, **kwargs)
        await bridge.dispatch_telegram_reply(native, event(update=2, text="edited", edited=True), {}, **kwargs)
    asyncio.run(run())
    assert not processed
    with setup[3].connect_sync() as db:
        rows = db.execute("SELECT content FROM group_messages WHERE platform='telegram'").fetchall()
    assert [row[0] for row in rows] == ["edited"]


def test_album_combines_typed_media_once_and_update_deduplicates(setup, monkeypatch):
    native, _ = bot()
    processed, materialized = [], []
    kwargs = wiring(setup, processed)
    async def materialize(_bot, envelope, _root):
        materialized.append(envelope)
        return envelope
    monkeypatch.setattr(bridge, "materialize", materialize)
    photo = [{"file_id": "p1", "file_unique_id": "u1", "width": 8, "height": 8}]
    first = event(update=3, private=True, text="", photo=photo, media_group_id="album")
    second = event(update=4, mid=11, private=True, text="", photo=[{**photo[0], "file_id": "p2"}], media_group_id="album")
    async def run():
        await asyncio.gather(bridge.dispatch_telegram_reply(native, first, {}, **kwargs), bridge.dispatch_telegram_reply(native, second, {}, **kwargs))
        await drain(processed, 1)
        await bridge.dispatch_telegram_reply(native, second, {}, **kwargs)
    asyncio.run(run())
    assert len(materialized) == 1
    assert [m.ref for m in materialized[0].ordered_media] == ["tgfile:p1", "tgfile:p2"]
    assert len(processed) == 1
    assert len(processed[0][1]["turn_media_context"]) == 2

def test_private_dispatch_reaches_real_processor_provider_review_and_official_send(setup, monkeypatch):
    # Reuse the existing real processor fixture, with only provider/transport fake.
    satori_root = Path("D:/test_artifacts/personification/implementation0906/satori-deps/nonebot/adapters")
    if satori_root.is_dir() and str(satori_root) not in nonebot.adapters.__path__:
        nonebot.adapters.__path__.append(str(satori_root))
    from .test_satori_pipeline_integration import _deps
    observed = []
    deps, config, logger, wire_inputs, images, logs, errors = _deps(monkeypatch, observed)
    config.personification_data_dir = str(setup[1].root.parent)
    native, sends = bot()
    processed = []
    kwargs = wiring((config, setup[1], logger, setup[3]), processed, deps=deps)
    async def real_processor(proxy, ev, state, scoped_deps):
        try:
            await processor.process_response_logic(proxy, ev, state, scoped_deps)
        finally:
            processed.append((ev, dict(state), scoped_deps))
    kwargs["process_response_logic"] = real_processor
    async def run():
        await bridge.dispatch_telegram_reply(native, event(private=True, text="你好", update=50), {}, **kwargs)
        await drain(processed, 1)
    asyncio.run(run())
    assert len(observed) >= 2, (logs, errors, processed)
    assert sends and sends[-1][0] == "send_message", (logs, errors, processed)
    assert sends[-1][1]["chat_id"] == 42
    stored = deps.session.get_session_messages("ignored")
    assert any(row.get("role") == "user" and row.get("message_id", "").endswith(":m2:10") for row in stored)
    assert "看到了" in sends[-1][1]["text"]
    assert all("[CQ:" not in str(kwargs.get("text", "")) for _, kwargs in sends)


def test_edited_event_registered_for_official_edit_message_type():
    registered = bridge.register_telegram_reply_bridge()
    assert "telegram_edit_matcher" in registered
    assert registered["telegram_edit_matcher"].type == "edit_message"
    assert isinstance(event(edited=True), tg_event.EditedMessageEvent)
    assert not isinstance(event(edited=True), tg_event.MessageEvent)
    for matcher in registered.values():
        matcher.destroy()

def test_topic_edit_only_updates_its_scoped_original(setup):
    native, _ = bot()
    processed = []
    kwargs = wiring(setup, processed)
    async def run():
        await bridge.dispatch_telegram_reply(native, event(update=71, topic=1, text="topic one"), {}, **kwargs)
        await bridge.dispatch_telegram_reply(native, event(update=72, topic=2, text="topic two"), {}, **kwargs)
        await bridge.dispatch_telegram_reply(native, event(update=73, topic=1, text="corrected one", edited=True), {}, **kwargs)
    asyncio.run(run())
    with setup[3].connect_sync() as db:
        rows = db.execute("SELECT group_id,content,message_id FROM group_messages WHERE platform='telegram' ORDER BY group_id").fetchall()
    assert len(rows) == 2
    assert {row[1] for row in rows} == {"corrected one", "topic two"}
    assert len({row[2] for row in rows}) == 2
    assert not processed


def test_anonymous_sender_and_bot_namespaces_never_merge():
    incoming = event(sender_chat={"id": -10002, "type": "supergroup", "title": "anonymous"})
    adapter = interaction.TelegramInteractionAdapter()
    first = adapter.normalize(incoming, bot_id="123")
    second = adapter.normalize(incoming, bot_id="456")
    assert first.sender_id == "chat:-10002"
    assert first.sender_namespace != second.sender_namespace
    assert first.conversation_namespace != second.conversation_namespace

def test_album_secondary_edit_updates_source_without_redelivery(setup, monkeypatch):
    native, _ = bot()
    processed = []
    kwargs = wiring(setup, processed)
    async def materialize(_bot, envelope, _root):
        return envelope
    monkeypatch.setattr(bridge, "materialize", materialize)
    photo = [{"file_id": "p1", "file_unique_id": "u1", "width": 8, "height": 8}]
    async def run():
        await asyncio.gather(
            bridge.dispatch_telegram_reply(native, event(update=81, mid=20, text="first", photo=photo, media_group_id="photos"), {}, **kwargs),
            bridge.dispatch_telegram_reply(native, event(update=82, mid=21, text="second", photo=photo, media_group_id="photos"), {}, **kwargs))
        await bridge.dispatch_telegram_reply(native, event(update=83, mid=21, text="second edited", photo=photo, media_group_id="photos", edited=True), {}, **kwargs)
    asyncio.run(run())
    with setup[3].connect_sync() as db:
        rows = db.execute("SELECT content FROM group_messages WHERE platform='telegram' ORDER BY message_id").fetchall()
    assert [row[0] for row in rows] == ["first", "second edited"]
    assert not processed


def test_private_edit_updates_only_scoped_session_original_without_reply(setup):
    import json
    native, _ = bot()
    processed = []
    kwargs = wiring(setup, processed)
    incoming = event(update=91, private=True, text="corrected", edited=True)
    envelope = bridge._pipeline_envelope(interaction.TelegramInteractionAdapter().normalize(incoming, bot_id="123"))
    metadata = {"platform": "telegram", "bot_id": envelope.bot_namespace, "message_id": envelope.message_id, "user_id": envelope.sender_id}
    with setup[3].connect_sync() as db:
        for bot_id in [envelope.bot_namespace, "other_bot"]:
            db.execute("INSERT INTO session_messages(session_id,role,content,timestamp,metadata) VALUES (?,?,?,?,?)", ("private_"+envelope.sender_id, "user", "old", 1, json.dumps({**metadata,"bot_id":bot_id})))
    asyncio.run(bridge.dispatch_telegram_reply(native, incoming, {}, **kwargs))
    with setup[3].connect_sync() as db:
        rows = db.execute("SELECT content FROM session_messages ORDER BY id").fetchall()
    assert [json.loads(row[0]) if row[0].startswith(chr(34)) else row[0] for row in rows] == ["corrected", "old"]
    assert not processed


def test_notice_migration_is_registered_and_bot_scoped(setup):
    native, _ = bot()
    payload = {"update_id": 101, "message": {"message_id": 10, "date": 1700000000, "chat": {"id": -10001, "type": "group", "title": "old"}, "from": {"id": 42, "is_bot": False, "first_name": "Alice"}, "migrate_to_chat_id": -10099}}
    notice = tg_event.Event.parse_event(payload)
    assert notice.get_type() == "notice"
    assert not isinstance(notice, tg_event.MessageEvent)
    asyncio.run(bridge.dispatch_telegram_migration(native, notice, plugin_config=setup[0]))
    service = setup[1]
    assert service.canonical_chat("123", "-10099") == "-10001"
    assert service.canonical_chat("456", "-10099") == "-10099"
    registry = bridge.register_telegram_reply_bridge(plugin_config=setup[0])
    try:
        assert registry["telegram_migration_matcher"].type == "notice"
    finally:
        for matcher in registry.values():
            matcher.destroy()


def test_partial_reactive_send_archives_only_confirmed_prefix(setup):
    native, calls = bot()
    async def transport(_bot, api, **kwargs):
        calls.append((api,kwargs))
        if len(calls) == 2:
            raise TimeoutError()
        return {"message_id": 99, "date": 1700000000, "chat": {"id": kwargs["chat_id"], "type": "private"}, "text": kwargs.get("text", "")}
    native.adapter._call_api = transport
    incoming = event(private=True)
    envelope = bridge._pipeline_envelope(interaction.TelegramInteractionAdapter().normalize(incoming, bot_id="123"))
    proxy = bridge.TelegramBotProxy(native, incoming, envelope, interaction.TelegramInteractionAdapter())
    with pytest.raises(connections.ConnectionFault):
        asyncio.run(proxy.send(incoming, "x"*5000))
    assert len(calls) == 2
    with setup[3].connect_sync() as db:
        rows = db.execute("SELECT role,content FROM session_messages").fetchall()
    import json
    assert [(row[0],json.loads(row[1])) for row in rows] == [("assistant", "x"*4096)]

@pytest.mark.parametrize("split_batches", [False, True], ids=["same-update-batch", "across-update-batches"])
def test_managed_poll_official_handle_event_merges_album_across_batches(setup, monkeypatch, split_batches):
    import importlib
    native, calls = bot()
    manager = setup[1]
    processed, materialized = [], []
    kwargs = wiring(setup, processed)
    async def materialize(_bot, envelope, _root):
        materialized.append(envelope)
        return envelope
    monkeypatch.setattr(bridge, "materialize", materialize)
    async def framework_dispatch(typed_bot, typed_event):
        assert typed_bot is native
        assert isinstance(typed_event, tg_event.PrivateMessageEvent)
        await bridge.dispatch_telegram_reply(typed_bot, typed_event, {}, **kwargs)
    monkeypatch.setattr(importlib.import_module("nonebot.adapters.telegram.bot"), "handle_event", framework_dispatch)
    raw = [{"update_id": 200+index, "message": {"message_id": 30+index, "date": 1700000000,
        "chat": {"id": 42, "type": "private"}, "from": {"id": 42, "is_bot": False, "first_name": "Alice"},
        "media_group_id": "managed-album", "photo": [{"file_id": "photo"+str(index), "file_unique_id": "unique"+str(index), "width": 8, "height": 8}]}}
        for index in range(2)]
    batches = [[raw[0]], [raw[1]]] if split_batches else [raw]
    async def transport(_bot, api, **params):
        assert api == "get_updates"
        calls.append(params)
        if batches:
            return deepcopy(batches.pop(0))
        raise connections.ConnectionFault("receiver_conflict")
    native.adapter._call_api = transport
    disconnected = []
    native.adapter.bot_disconnect = lambda current: disconnected.append(current.self_id)
    manager.bots["managed"] = native
    async def run():
        await manager._poll("managed", native)
        await drain(processed, 1)
    asyncio.run(run())
    assert len(materialized) == 1
    assert len(materialized[0].ordered_media) == 2
    assert len(processed) == 1
    assert disconnected == ["123"]
    with manager.db() as db:
        assert db.execute("SELECT value FROM offsets WHERE bot='123'").fetchone()[0] == 202
    if split_batches:
        assert calls[1]["offset"] == 201


def test_managed_stop_drains_actual_bridge_handler_without_cancelling(setup, monkeypatch):
    import importlib
    native, _ = bot()
    manager = setup[1]
    processed = []
    kwargs = wiring(setup, processed)
    entered, release, polling = asyncio.Event(), asyncio.Event(), asyncio.Event()
    cancellations = []
    async def materialize(_bot, envelope, _root):
        entered.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            cancellations.append(True)
            raise
        return envelope
    monkeypatch.setattr(bridge, "materialize", materialize)
    async def framework_dispatch(typed_bot, typed_event):
        await bridge.dispatch_telegram_reply(typed_bot, typed_event, {}, **kwargs)
    monkeypatch.setattr(importlib.import_module("nonebot.adapters.telegram.bot"), "handle_event", framework_dispatch)
    batch = [{"update_id": 300, "message": {"message_id": 40, "date": 1700000000,
        "chat": {"id": 42, "type": "private"}, "from": {"id": 42, "is_bot": False, "first_name": "Alice"},
        "photo": [{"file_id": "stop-photo", "file_unique_id": "unique", "width": 8, "height": 8}]}}]
    async def transport(_bot, api, **params):
        assert api == "get_updates"
        if batch:
            result = list(batch)
            batch.clear()
            return result
        polling.set()
        await asyncio.Event().wait()
    native.adapter._call_api = transport
    native.adapter.bot_disconnect = lambda _: None
    manager.loaded = True
    manager.records["managed"] = {"token": "123:isolated-token", "scope": "all", "enabled": True}
    manager.bots["managed"] = native
    async def run():
        manager.tasks["managed"] = asyncio.create_task(manager._poll("managed", native))
        await asyncio.wait_for(entered.wait(), 2)
        await asyncio.wait_for(polling.wait(), 2)
        stopping = asyncio.create_task(manager.stop("managed", persist=False))
        await asyncio.sleep(0)
        assert not stopping.done()
        release.set()
        await asyncio.wait_for(stopping, 2)
        await drain(processed, 1)
    asyncio.run(run())
    assert not cancellations
    assert len(processed) == 1

@pytest.mark.parametrize("private", [True, False], ids=["private", "group"])
def test_album_persists_sources_but_current_provider_sees_aggregate(setup, monkeypatch, private):
    import json
    from dataclasses import replace
    satori_root = Path("D:/test_artifacts/personification/implementation0906/satori-deps/nonebot/adapters")
    if str(satori_root) not in nonebot.adapters.__path__:
        nonebot.adapters.__path__.append(str(satori_root))
    from .test_satori_pipeline_integration import _deps
    session_store = load_personification_module("plugin.personification.core.session_store")
    observed = []
    deps, config, logger, _, _, logs, errors = _deps(monkeypatch, observed)
    config.personification_data_dir = str(setup[1].root.parent)
    deps = replace(deps, session=replace(deps.session,
        build_group_session_id=session_store.build_group_session_id,
        build_private_session_id=session_store.build_private_session_id,
        get_session_messages=session_store.get_session_messages,
        append_session_message=session_store.append_session_message))
    native, _ = bot()
    processed = []
    kwargs = wiring((config, setup[1], logger, setup[3]), processed, deps=deps)
    async def real_processor(proxy, ev, state, scoped_deps):
        try:
            await processor.process_response_logic(proxy, ev, state, scoped_deps)
        finally:
            processed.append((ev, dict(state), scoped_deps))
    kwargs["process_response_logic"] = real_processor
    directed = {} if private else {"entities": [{"type": "mention", "offset": 0, "length": 10}]}
    prefix = "" if private else "@shiro_bot "
    async def run():
        await asyncio.gather(
            bridge.dispatch_telegram_reply(native, event(update=401, mid=50, private=private, text=prefix+"first original caption", media_group_id="editable", **directed), {}, **kwargs),
            bridge.dispatch_telegram_reply(native, event(update=402, mid=51, private=private, text="second original caption", media_group_id="editable"), {}, **kwargs))
        await drain(processed, 1)
        await bridge.dispatch_telegram_reply(native, event(update=403, mid=51, private=private, text="second corrected caption", media_group_id="editable", edited=True), {}, **kwargs)
        await bridge.dispatch_telegram_reply(native, event(update=404, mid=50, private=private, text="first corrected caption", media_group_id="editable", edited=True), {}, **kwargs)
    asyncio.run(run())
    assert observed, (logs, errors)
    wire = json.dumps(observed[0], ensure_ascii=False)
    assert "first original caption" in wire and "second original caption" in wire
    with setup[3].connect_sync() as db:
        rows = db.execute("SELECT content,metadata FROM session_messages WHERE role='user'").fetchall()
    assert len(rows) == 2, rows
    assert {json.loads(row[0]) for row in rows} == {"first corrected caption", "second corrected caption"}
    assert len({json.loads(row[1])["message_id"] for row in rows}) == 2
    assert {json.loads(row[1])["bot_id"] for row in rows} == {"p8:telegramb3:123"}
    assert len(processed) == 1

@pytest.mark.parametrize("stopping", [False, True])
def test_start_verification_uses_guard_before_first_reply(setup, stopping):
    import time
    native, calls = bot()
    service = setup[1]
    native.adapter.personification_managed = True
    service.bots["managed"] = native
    service.records["managed"] = {"token": "123:isolated-token", "scope": "all"}
    service.nonces["managed"] = ("one-use", time.time()+60)
    if stopping:
        service.stopping.add("managed")
    processed = []
    async def run():
        await bridge.dispatch_telegram_reply(native, event(private=True, text="/start pf_one-use"), {}, **wiring(setup, processed))
    if stopping:
        with pytest.raises(connections.ConnectionFault, match="connection_stopped"):
            asyncio.run(run())
    else:
        asyncio.run(run())
    assert len(calls) == (0 if stopping else 1)
    assert not processed


def test_native_registration_installs_transport_redaction(monkeypatch):
    media = load_personification_module("plugin.personification.core.telegram_media")
    installed = []
    monkeypatch.setattr(media, "protect_transport_logs", lambda: installed.append(True))
    matchers = bridge.register_telegram_reply_bridge()
    try:
        assert installed == [True]
    finally:
        for matcher in matchers.values():
            matcher.destroy()
