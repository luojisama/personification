from __future__ import annotations
import asyncio
from pathlib import Path
from ._loader import load_personification_module
m=load_personification_module("plugin.personification.core.moderation")
def scope(): return dict(platform="qq",bot_id="b",group_id="g",target_id="u",incident="i")
def ready(ledger, now=1000):
    s=scope(); ledger.event(**s,message_id="e1",occurred_at=now,negative=True,offensive=True); ledger.warning(**s,round_id="r1",message_id="w1",confirmed_at=now,source_message_id="e1"); ledger.event(**s,message_id="e2",occurred_at=now+.5,negative=True,offensive=True); ledger.warning(**s,round_id="r2",message_id="w2",confirmed_at=now+1,source_message_id="e2"); ledger.event(**s,message_id="e3",occurred_at=now+2,negative=True,offensive=True)
def test_two_rounds_new_offense_and_unknown_dedup(tmp_path:Path):
    l=m.ModerationLedger(tmp_path/"m.sqlite3"); ready(l)
    async def run():
        ok=lambda: asyncio.sleep(0,result={"bot_admin":True,"target_manageable":True,"adapter_mute_supported":True})
        a=lambda n: asyncio.sleep(0,result="unknown")
        first=await m.execute_controlled_mute(l,scope=scope(),minutes=1,now=1003,revalidate=ok,adapter_mute=a)
        second=await m.execute_controlled_mute(l,scope=scope(),minutes=1,now=1003,revalidate=ok,adapter_mute=a)
        assert first["status"]=="unknown" and second["code"]=="moderation_duplicate_or_duration_invalid"
    asyncio.run(run())
def test_expired_reconciled_and_duration_fail_closed(tmp_path:Path):
    l=m.ModerationLedger(tmp_path/"m.sqlite3"); ready(l,1); assert not l.eligible(**scope(),now=1+m.WARNING_TTL_SECONDS+2); ready(l,1000); l.reconcile(**scope()); assert not l.eligible(**scope(),now=1003); assert l.reserve(**scope(),minutes=True,now=1003) is None; assert l.reserve(**scope(),minutes=21,now=1003) is None

def test_wrong_target_quote_and_split_warning_do_not_unlock(tmp_path:Path):
    l=m.ModerationLedger(tmp_path/"m.sqlite3"); s=scope()
    l.event(**s,message_id="quote",occurred_at=1,negative=True,offensive=False)
    l.event(**s,message_id="e",occurred_at=1,negative=True,offensive=True)
    l.warning(**s,round_id="same",message_id="part1",confirmed_at=1,source_message_id="e")
    assert not l.warning(**s,round_id="same",message_id="part2",confirmed_at=1.1,source_message_id="e")
    other={**s,"target_id":"other"}; l.event(**other,message_id="oe",occurred_at=1,negative=True,offensive=True); l.warning(**other,round_id="r2",message_id="w",confirmed_at=2,source_message_id="oe"); l.event(**other,message_id="e",occurred_at=3,negative=True,offensive=True)
    assert not l.eligible(**s,now=4) and not l.eligible(**other,now=4)

def test_permission_lost_already_muted_and_restart_never_replay(tmp_path:Path):
    path=tmp_path/"m.sqlite3"; l=m.ModerationLedger(path); ready(l)
    async def run():
        denied=lambda: asyncio.sleep(0,result={"bot_admin":False,"target_manageable":True,"adapter_mute_supported":True})
        muted=lambda: asyncio.sleep(0,result={"bot_admin":True,"target_manageable":True,"adapter_mute_supported":True,"already_muted":True})
        sent=[]
        async def adapter(n): sent.append(n); return "sent"
        assert (await m.execute_controlled_mute(l,scope=scope(),minutes=2,now=1003,revalidate=denied,adapter_mute=adapter))["status"]=="blocked"
        assert (await m.execute_controlled_mute(l,scope=scope(),minutes=2,now=1003,revalidate=muted,adapter_mute=adapter))["status"]=="blocked"
        ok=lambda: asyncio.sleep(0,result={"bot_admin":True,"target_manageable":True,"adapter_mute_supported":True})
        first=await m.execute_controlled_mute(l,scope=scope(),minutes=2,now=1003,revalidate=ok,adapter_mute=lambda n: asyncio.sleep(0,result="unknown"))
        restarted=m.ModerationLedger(path)
        again=await m.execute_controlled_mute(restarted,scope=scope(),minutes=2,now=1003,revalidate=ok,adapter_mute=adapter)
        assert first["status"]=="unknown" and again["status"]=="blocked" and not sent
    asyncio.run(run())

def test_future_event_tombstone_and_truthy_permission_fail_closed(tmp_path:Path):
    l=m.ModerationLedger(tmp_path/"m.sqlite3"); ready(l)
    l.event(**scope(),message_id="future",occurred_at=9999,negative=True,offensive=True)
    assert l.eligible(**scope(),now=1003)
    l.reconcile(**scope()); assert not l.eligible(**scope(),now=1003)
    l2=m.ModerationLedger(tmp_path/"n.sqlite3"); ready(l2)
    async def run():
        bad=lambda: asyncio.sleep(0,result={"bot_admin":"false","target_manageable":True,"adapter_mute_supported":True})
        result=await m.execute_controlled_mute(l2,scope=scope(),minutes=1,now=1003,revalidate=bad,adapter_mute=lambda _: asyncio.sleep(0,result="sent"))
        assert result["status"]=="blocked"
    asyncio.run(run())

def test_concurrent_reserve_and_reconcile_during_revalidate(tmp_path:Path):
    l=m.ModerationLedger(tmp_path/"m.sqlite3"); ready(l); calls=[]
    async def run():
        gate=asyncio.Event()
        async def check(): await gate.wait(); return {"bot_admin":True,"target_manageable":True,"adapter_mute_supported":True}
        async def adapter(n): calls.append(n); return "sent"
        one=asyncio.create_task(m.execute_controlled_mute(l,scope=scope(),minutes=1,now=1003,revalidate=check,adapter_mute=adapter))
        two=asyncio.create_task(m.execute_controlled_mute(l,scope=scope(),minutes=1,now=1003,revalidate=check,adapter_mute=adapter))
        gate.set(); results=await asyncio.gather(one,two); assert len(calls)==1 and sum(x["status"]=="sent" for x in results)==1
        l2=m.ModerationLedger(tmp_path/"n.sqlite3"); ready(l2); gate2=asyncio.Event()
        async def delayed(): await gate2.wait(); return {"bot_admin":True,"target_manageable":True,"adapter_mute_supported":True}
        task=asyncio.create_task(m.execute_controlled_mute(l2,scope=scope(),minutes=1,now=1003,revalidate=delayed,adapter_mute=adapter)); l2.reconcile(**scope()); gate2.set(); assert (await task)["status"]=="blocked"
    asyncio.run(run())


def test_buffered_messages_do_not_count_as_two_warning_rounds(tmp_path):
    ledger=m.ModerationLedger(tmp_path/"m.sqlite3"); keys=scope()
    ledger.event(**keys,message_id="e1",occurred_at=10,negative=True,offensive=True)
    ledger.event(**keys,message_id="e2",occurred_at=11,negative=True,offensive=True)
    assert ledger.warning(**keys,round_id="r1",message_id="w1",confirmed_at=12,source_message_id="e1")
    assert not ledger.reserve_warning(**keys,source_message_id="e2",round_id="r2",now=13)
    assert not ledger.warning(**keys,round_id="r2",message_id="w2",confirmed_at=13,source_message_id="e2")
    assert not ledger.eligible(**keys,now=14)


def test_unknown_warning_attempt_cannot_replay_or_count(tmp_path):
    ledger=m.ModerationLedger(tmp_path/"m.sqlite3"); keys=scope()
    ledger.event(**keys,message_id="e1",occurred_at=10,negative=True,offensive=True)
    assert ledger.reserve_warning(**keys,source_message_id="e1",round_id="r1",now=11)
    restarted=m.ModerationLedger(tmp_path/"m.sqlite3")
    assert not restarted.reserve_warning(**keys,source_message_id="e1",round_id="r2",now=12)
    assert restarted.warning_evidence(**keys)==[]


def test_completed_penalty_starts_fresh_but_unknown_blocks_even_after_reconcile(tmp_path):
    ledger=m.ModerationLedger(tmp_path/"m.sqlite3")
    target={k:v for k,v in scope().items() if k!='incident'}
    first=ledger.get_or_create_active_incident(**target,now=1000)
    keys={**target,'incident':first}
    for i in range(3):
        ledger.event(**keys,message_id=f'e{i}',occurred_at=1000+i*2,negative=True,offensive=True)
        if i<2: assert ledger.warning(**keys,round_id=f'r{i}',message_id=f'w{i}',confirmed_at=1001+i*2,source_message_id=f'e{i}')
    op=ledger.reserve(**keys,minutes=1,now=1005); ledger.finish(op['operation_id'],'sent')
    second=ledger.get_or_create_active_incident(**target,now=1006)
    assert second and second!=first and not ledger.eligible(**{**target,'incident':second},now=1006)
    with ledger._db() as db: db.execute("UPDATE moderation_operations SET status='unknown'")
    ledger.reconcile(**keys)
    assert ledger.get_or_create_active_incident(**target,now=1007) is None
