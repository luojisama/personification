from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from ._loader import load_personification_module

module = load_personification_module("plugin.personification.flows.telegram_proactive")
legacy = load_personification_module("plugin.personification.flows.proactive_flow")
db = load_personification_module("plugin.personification.core.db")
store = load_personification_module("plugin.personification.core.data_store")


class Log:
    def warning(self, *_a, **_k): pass
    def debug(self, *_a, **_k): pass
    def info(self, *_a, **_k): pass


def fixture(tmp_path, monkeypatch, *, delivery="sent", review="小话题", enabled=True):
    config = SimpleNamespace(personification_data_dir=str(tmp_path), personification_group_idle_enabled=True, personification_proactive_enabled=True, personification_proactive_probability=1, personification_group_quiet_hour_start=0, personification_group_quiet_hour_end=0)
    db.init_db_sync(tmp_path)
    store.init_data_store(config, logger=Log())
    now = datetime.now(timezone.utc)
    envelope = module.InteractionEnvelope("telegram", "12", "group", "-100", "", "", topic_id="8")
    room = {"chat_id": "-100", "topic_id": "8", "scope_key": envelope.conversation_namespace, "proactive_enabled": enabled}
    class Connections:
        def settings_for(self, _bot): return {"scope": "all", "persona": "测试角色"}
        def rooms(self, _bot): return [room]
        def canonical_chat(self, _bot, chat): return chat
    class Bot:
        platform = "telegram"
        self_id = "12"
        username = "fixture"
        async def send_message(self, **kwargs):
            sends.append(kwargs)
            if delivery == "unknown" or (delivery == "partial" and len(sends) > 1):
                raise TimeoutError("fixture")
            return SimpleNamespace(message_id=42)
    bot = Bot()
    history = [{"message_id": "real-user-event", "content": "真实话题", "nickname": "群友", "is_bot": False, "source_kind": "user", "time": now.timestamp() - 7200}]
    reads, sends, recorded, sessions, calls = [], [], [], [], []
    proactive_state = {}
    def read(scope, **kwargs):
        reads.append((scope, kwargs))
        return deepcopy(history)
    def save(state):
        proactive_state.clear()
        proactive_state.update(deepcopy(state))
    async def call(messages):
        calls.append(messages)
        return "候选话题"
    async def checked(**kwargs):
        assert kwargs["text"] == "候选话题"
        return review
    monkeypatch.setattr(module, "_review_proactive_text", checked)
    checker = module.build_telegram_idle_checker(plugin_config=config, get_bots=lambda: {"bot": bot}, connections=Connections(), load_prompt=lambda _scope: "默认角色", call_ai_api=call, get_now=lambda: now, load_proactive_state=lambda: deepcopy(proactive_state), save_proactive_state=save, logger=Log(), get_recent_group_msgs=read, record_group_msg=lambda *args, **kwargs: recorded.append((args, kwargs)), append_session_message=lambda *args, **kwargs: sessions.append((args, kwargs)))
    return SimpleNamespace(check=checker, config=config, room=room, history=history, reads=reads, sends=sends, recorded=recorded, sessions=sessions, calls=calls, now=now, envelope=envelope, state=proactive_state)


def test_default_off_never_calls_model_or_transport(tmp_path, monkeypatch):
    f = fixture(tmp_path, monkeypatch, enabled=False)
    try:
        assert asyncio.run(f.check()) == 0
        assert not f.calls and not f.sends
    finally:
        asyncio.run(db.close_db())


def test_confirmed_text_uses_review_topic_receipt_and_scoped_history(tmp_path, monkeypatch):
    f = fixture(tmp_path, monkeypatch)
    try:
        assert asyncio.run(f.check()) == 1
        assert f.sends == [{"chat_id": -100, "message_thread_id": 8, "text": "小话题"}]
        assert all(read[0] == f.envelope.conversation_namespace and read[1]["platform"] == "telegram" and read[1]["bot_id"] == f.envelope.bot_namespace for read in f.reads)
        assert f.recorded[0][1]["source_kind"] == "bot_reply"
        assert f.sessions[0][0][0] == "group_" + f.envelope.conversation_namespace
        assert asyncio.run(f.check()) == 0
        assert len(f.sends) == 1
    finally:
        asyncio.run(db.close_db())


def test_unknown_delivery_is_sealed_and_never_recorded(tmp_path, monkeypatch):
    f = fixture(tmp_path, monkeypatch, delivery="unknown")
    try:
        assert asyncio.run(f.check()) == 0
        assert not f.recorded and not f.sessions
        # Drop the ordinary cooldown to prove the durable event reservation
        # itself still prevents replay of the unknown send.
        f.state.clear()
        assert asyncio.run(f.check()) == 0
        assert len(f.sends) == 1
    finally:
        asyncio.run(db.close_db())


def test_review_rejection_stays_silent(tmp_path, monkeypatch):
    f = fixture(tmp_path, monkeypatch, review="")
    try:
        assert asyncio.run(f.check()) == 0
        assert not f.sends and not f.recorded
    finally:
        asyncio.run(db.close_db())


def test_new_message_during_review_discards_idle_draft(tmp_path, monkeypatch):
    f = fixture(tmp_path, monkeypatch)
    async def review(**_kwargs):
        f.history.append({"message_id": "new", "content": "新话题", "is_bot": False, "source_kind": "user", "time": f.now.timestamp()})
        return "已审阅话题"
    monkeypatch.setattr(module, "_review_proactive_text", review)
    try:
        assert asyncio.run(f.check()) == 0
        assert not f.sends
    finally:
        asyncio.run(db.close_db())


def test_legacy_jobs_exclude_telegram_and_scheduler_is_single_instance():
    tg = SimpleNamespace(platform="telegram")
    qq = SimpleNamespace()
    assert legacy._non_telegram_bots({"tg": tg, "qq": qq}) == {"qq": qq}


def test_partial_delivery_records_only_confirmed_segment(tmp_path, monkeypatch):
    f = fixture(tmp_path, monkeypatch, delivery="partial", review="文" * 5000)
    try:
        assert asyncio.run(f.check()) == 0
        assert len(f.sends) == 2 and len(f.recorded) == 1
        assert len(f.recorded[0][0][2]) == 4096
        assert len(f.sessions) == 1
        f.state.clear()
        assert asyncio.run(f.check()) == 0
        assert len(f.sends) == 2
    finally:
        asyncio.run(db.close_db())


def test_disabled_during_review_never_sends(tmp_path, monkeypatch):
    f = fixture(tmp_path, monkeypatch)
    async def review(**_kwargs):
        f.config.personification_global_enabled = False
        return "已审阅话题"
    monkeypatch.setattr(module, "_review_proactive_text", review)
    try:
        assert asyncio.run(f.check()) == 0
        assert not f.sends
    finally:
        asyncio.run(db.close_db())


def test_uses_real_shared_final_reviewer_before_transport(tmp_path, monkeypatch):
    f = fixture(tmp_path, monkeypatch)
    prompts = []
    async def review_call(messages):
        prompts.append(messages)
        return '{"action":"accept","text":"","flags":[],"persona_verdict":"consistent","attribution_verdict":"valid"}'
    async def review(**kwargs):
        kwargs["caller"] = review_call
        return await legacy._review_proactive_text(**kwargs)
    monkeypatch.setattr(module, "_review_proactive_text", review)
    try:
        assert asyncio.run(f.check()) == 1
        assert prompts and f.sends[0]["text"] == "候选话题"
        assert "测试角色" in str(prompts)
    finally:
        asyncio.run(db.close_db())


def test_registration_uses_existing_scheduler_with_single_instance(monkeypatch):
    calls = []
    checker = object()
    monkeypatch.setattr(module, "build_telegram_idle_checker", lambda **_kwargs: checker)
    scheduler = SimpleNamespace(add_job=lambda *args, **kwargs: calls.append((args, kwargs)))
    assert module.register_telegram_proactive_job(scheduler=scheduler, plugin_config=SimpleNamespace(personification_group_idle_check_interval=1)) is checker
    assert calls[0][1]["minutes"] == 5
    assert calls[0][1]["max_instances"] == 1
    assert calls[0][1]["replace_existing"] is True
