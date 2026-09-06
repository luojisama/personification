from __future__ import annotations
import asyncio
import json
import time
from pathlib import Path
from types import SimpleNamespace
from ._loader import load_personification_module
m=load_personification_module("plugin.personification.core.moderation")
t=load_personification_module("plugin.personification.core.moderation_tools")
def s(): return dict(platform="qq",bot_id="b",group_id="1",target_id="2",incident="i")
def test_receipts_then_new_event_allow_one_fake_onebot_ban(tmp_path:Path):
    l=m.ModerationLedger(tmp_path/"m.sqlite3"); x=s()
    l.event(**x,message_id="e1",occurred_at=1,negative=True,offensive=True)
    asyncio.run(t.warn_from_confirmed_receipt(l,scope=x,round_id="r1",warning_message_id="w1",source_message_id="e1",confirmed_at=1))
    l.event(**x,message_id="e2",occurred_at=2,negative=True,offensive=True)
    asyncio.run(t.warn_from_confirmed_receipt(l,scope=x,round_id="r2",warning_message_id="w2",source_message_id="e2",confirmed_at=2))
    l.event(**x,message_id="e3",occurred_at=3,negative=True,offensive=True); seen=[]
    async def check(): return {"bot_admin":True,"target_manageable":True,"adapter_mute_supported":True}
    async def api(name,**kwargs): seen.append((name,kwargs)); return None
    result=asyncio.run(t.onebot_controlled_mute(l,scope=x,minutes=2,now=4,revalidate=check,call_api=api))
    assert result["status"]=="sent" and seen==[("set_group_ban",{"group_id":1,"user_id":2,"duration":120})]

def test_registry_three_turns_two_receipts_then_one_mute(tmp_path:Path):
    from types import SimpleNamespace
    registry=load_personification_module("plugin.personification.agent.tool_registry").ToolRegistry(); ledger=m.ModerationLedger(tmp_path/"x.sqlite3"); x={**s(),"source_message_id":"e1","round_id":"r1","received_at":time.time()}; sent=[]
    async def verify(**kwargs): return {"negative":True,"offensive":True,"continuing":True,"warning_clear":True,"reconciled":False}
    async def receipt(text,**kwargs): sent.append(text); return {"status":"sent","message_id":f"w{len(sent)}"}
    async def check(): return {"bot_admin":True,"target_manageable":True,"adapter_mute_supported":True}
    class Bot:
        async def call_api(self,*a,**k): sent.append(a[0]); return None
    cfg=SimpleNamespace(personification_controlled_moderation_enabled=True,personification_controlled_moderation_authorized_groups=["1"])
    t.register_controlled_moderation_tools(registry,ledger=ledger,scope=x,bot=Bot(),plugin_config=cfg,revalidate=check,semantic_verify=verify,send_warning=receipt)
    tool=registry.get("controlled_group_moderation")
    assert json.loads(asyncio.run(tool.handler(action="warn",warning_text="stop")))["ok"]
    x["source_message_id"]="e2"; x["round_id"]="r2"; x["received_at"]=time.time(); assert json.loads(asyncio.run(tool.handler(action="warn",warning_text="stop")))["ok"]
    x["source_message_id"]="e3"; x["round_id"]="r3"; x["received_at"]=time.time(); assert json.loads(asyncio.run(tool.handler(action="mute",minutes=1)))["ok"] and sent[-1]=="set_group_ban"


def test_real_turn_registration_requires_two_confirmed_warnings_then_later_offense(tmp_path: Path, monkeypatch) -> None:
    """Exercise the actual register→ActionExecutor receipt→tool path across turns."""
    executor_mod = load_personification_module("plugin.personification.agent.action_executor")
    registry_mod = load_personification_module("plugin.personification.agent.tool_registry")
    outbound = load_personification_module("plugin.personification.core.qq_outbound")
    seen_semantic: list[dict] = []
    seen_system: list[str] = []
    bans: list[dict] = []

    class Bot:
        self_id = "90001"
        async def send(self, _event, _message): return {"message_id": f"warn-{len(seen_semantic)}"}
        async def get_group_member_info(self, *, group_id, user_id):
            return {"role": "admin" if int(user_id) == 90001 else "member", "shut_up_timestamp": 0}
        async def call_api(self, name, **kwargs):
            bans.append({"name": name, **kwargs}); return None

    class Ledger:
        async def dispatch(self, context, content, send):
            result = await send()
            return SimpleNamespace(status="sent", message_id=str(result["message_id"]), operation_id=context.operation_id)

    async def caller(messages, **_kwargs):
        # The real semantic verifier receives core persona, trusted ordered
        # attribution, current event and structured emotion in its user JSON.
        payload = json.loads(messages[-1]["content"])
        seen_semantic.append(payload)
        seen_system.append(str(messages[0]["content"]))
        return json.dumps({"negative": True, "offensive": True, "continuing": True, "warning_clear": True, "reconciled": False})

    async def accept_review(_caller, *, candidate_text, **_kwargs):
        return SimpleNamespace(action="accept", text=candidate_text)
    response_review = load_personification_module("plugin.personification.core.response_review")
    monkeypatch.setattr(response_review, "review_response_text", accept_review)

    cfg = SimpleNamespace(
        personification_data_dir=str(tmp_path),
        personification_controlled_moderation_enabled=True,
        personification_controlled_moderation_authorized_groups=["10001"],
    )
    bot = Bot()
    semantic_frame = SimpleNamespace(bot_emotion="受辱而生气", emotion_intensity="high")

    async def one_turn(message_id: str, operation_id: str, action: str, *, warning: str = "请停止继续冒犯，否则会短暂禁言", minutes: int = 1):
        event = SimpleNamespace(group_id="10001", user_id="20002", message_id=message_id, get_plaintext=lambda: "持续的人身冒犯")
        executor = executor_mod.ActionExecutor(
            bot, event, cfg, SimpleNamespace(warning=lambda *_a, **_k: None),
            qq_outbound_ledger=Ledger(), operation_id=operation_id,
            core_persona="管理员设定的克制核心人格", expression_review_caller=caller,
        )
        registry = registry_mod.ToolRegistry()
        t.register_moderation_for_turn(
            registry, executor=executor,
            state={"received_wall_at": time.time()},
            ordered_context="[可信群聊归属] member_1: 冒犯内容；当前对象为 member_1",
            semantic_frame=semantic_frame,
        )
        tool = registry.get("controlled_group_moderation")
        assert tool is not None
        return json.loads(await tool.handler(action=action, warning_text=warning, minutes=minutes))

    first = asyncio.run(one_turn("source-1", "round-1", "warn"))
    assert first == {"ok": True, "code": "moderation_warning_recorded"}
    # A fresh offense after only one confirmed warning is still insufficient.
    time.sleep(0.02)
    early = asyncio.run(one_turn("source-early", "round-early", "mute", minutes=7))
    assert early["ok"] is False and not bans
    time.sleep(0.02)  # second source must occur after the confirmed first warning
    second = asyncio.run(one_turn("source-2", "round-2", "warn"))
    assert second == {"ok": True, "code": "moderation_warning_recorded"}
    time.sleep(0.02)  # third offense must be newer than the second delivery
    muted = asyncio.run(one_turn("source-3", "round-3", "mute", minutes=7))
    assert muted["ok"] is True and muted["code"] == "sent"
    assert bans == [{"name": "set_group_ban", "group_id": 10001, "user_id": 20002, "duration": 420}]
    assert all(item["ordered_dialogue"] and item["current_emotion"] == "受辱而生气" for item in seen_semantic)
    assert all("管理员设定的克制核心人格" in content for content in seen_system)


def test_moderation_blocks_unknown_warning_and_expired_incident_reuses_no_old_rounds(tmp_path: Path) -> None:
    ledger = m.ModerationLedger(tmp_path / "moderation.sqlite3")
    now = time.time()
    old = ledger.get_or_create_active_incident(platform="onebot", bot_id="b", group_id="1", target_id="2", now=now)
    assert old
    assert ledger.event(platform="onebot", bot_id="b", group_id="1", target_id="2", incident=old, message_id="old-source", occurred_at=now, negative=True, offensive=True)
    assert ledger.reserve_warning(platform="onebot", bot_id="b", group_id="1", target_id="2", incident=old, source_message_id="old-source", round_id="r1", now=now)
    # Unknown delivery never becomes confirmed evidence and cannot be replayed.
    assert not ledger.reserve_warning(platform="onebot", bot_id="b", group_id="1", target_id="2", incident=old, source_message_id="old-source", round_id="r1", now=now + 1)
    fresh = ledger.get_or_create_active_incident(platform="onebot", bot_id="b", group_id="1", target_id="2", now=now + m.WARNING_TTL_SECONDS + 1)
    assert fresh and fresh != old


def test_warning_window_is_anchored_to_first_confirmed_delivery_not_latest_offense(tmp_path: Path) -> None:
    ledger = m.ModerationLedger(tmp_path / "moderation.sqlite3")
    base = time.time()
    scope = dict(platform="onebot", bot_id="b", group_id="1", target_id="2")
    incident = ledger.get_or_create_active_incident(**scope, now=base)
    assert incident
    assert ledger.event(**scope, incident=incident, message_id="source-1", occurred_at=base, negative=True, offensive=True)
    assert ledger.warning(**scope, incident=incident, round_id="round-1", message_id="warning-1", source_message_id="source-1", confirmed_at=base)
    # Continued hostile events after the warning do not renew its 30-minute window.
    assert ledger.event(**scope, incident=incident, message_id="source-late", occurred_at=base + m.WARNING_TTL_SECONDS + 5, negative=True, offensive=True)
    replacement = ledger.get_or_create_active_incident(**scope, now=base + m.WARNING_TTL_SECONDS + 5)
    assert replacement and replacement != incident
    listed = ledger.list_incidents()["items"]
    old_item = next(item for item in listed if item["incident"] == incident)
    assert old_item["expires_at"] == base + m.WARNING_TTL_SECONDS


def test_warning_attempt_statuses_are_terminal_and_unknown_still_blocks_new_incident(tmp_path: Path) -> None:
    ledger = m.ModerationLedger(tmp_path / "moderation.sqlite3")
    now = time.time()
    scope = dict(platform="onebot", bot_id="b", group_id="1", target_id="2")
    incident = ledger.get_or_create_active_incident(**scope, now=now)
    assert incident
    assert ledger.event(**scope, incident=incident, message_id="source", occurred_at=now, negative=True, offensive=True)
    assert ledger.reserve_warning(**scope, incident=incident, source_message_id="source", round_id="round", now=now)
    assert ledger.finish_warning_attempt(**scope, incident=incident, source_message_id="source", round_id="round", status="unknown")
    # Unknown delivery cannot be retried or promoted to confirmed evidence.
    assert not ledger.reserve_warning(**scope, incident=incident, source_message_id="source", round_id="round", now=now + 1)
    assert ledger.warning_evidence(**scope, incident=incident) == []
    # An unresolved mute operation remains a cross-incident block even after
    # the warning window has elapsed.
    with ledger._db() as db:
        db.execute("INSERT INTO moderation_operations VALUES(?,?,?,?,?,?,?,?,?,?)", (*scope.values(), incident, "operation", "unknown", 1, now, now))
    assert ledger.get_or_create_active_incident(**scope, now=now + m.WARNING_TTL_SECONDS + 1) is None
