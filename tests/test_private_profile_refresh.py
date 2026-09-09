from __future__ import annotations
import asyncio
import hashlib
import json
import time
from types import SimpleNamespace
from ._loader import load_personification_module

mod=load_personification_module("plugin.personification.core.private_profile_refresh")
db=load_personification_module("plugin.personification.core.db")
memory_store=load_personification_module("plugin.personification.core.memory_store")
profile_service=load_personification_module("plugin.personification.core.profile_service")
scoped_service=load_personification_module("plugin.personification.core.scoped_profile_service")
session_store=load_personification_module("plugin.personification.core.session_store")
class Store:
 def __init__(self):self.data={}
 def load_sync(self,k):return self.data.get(k,{})
 def save_sync(self,k,v):self.data[k]=v
class Memory:
 def __init__(self):self.generation=1
 def get_profile_generation(self):return self.generation
class Service:
 def __init__(self):self.profile_service=SimpleNamespace(memory_store=Memory());self.doc={};self.calls=[]
 def get_scoped_document_v3(self,**_):return self.doc or None
 def put_scoped_document_v3(self,**kw):self.calls.append(kw);self.doc={"document":kw["document"],"revision":1}
class Caller:
 async def chat_with_tools(self,**_):return SimpleNamespace(content=json.dumps([{"key":"content_pref","value":"tea","confidence":.9,"source_ids":["2"]}]))

def test_private_refresh_excludes_assistant_and_persists_only_scope(monkeypatch):
 store=Store();service=Service(); now=[1000.0]
 monkeypatch.setattr(mod,"get_data_store",lambda:store)
 monkeypatch.setattr(mod,"get_recent_session_candidates",lambda *a,**k:[{"id":1,"role":"assistant","content":"bot","timestamp":1},{"id":2,"role":"user","content":"tea","timestamp":2}])
 async def run():
  refresh=mod.PrivateProfileRefresh(service,Caller(),None,threshold=1,quiet_seconds=1,cooldown=0,clock=lambda:now[0])
  refresh.observe_private_message("u",platform="qq",bot_id="b",source_id="msg",text="tea")
  await refresh.refresh("qq","b","u")
  assert service.calls and service.calls[0]["document"]["claims"][0]["evidence_refs"][0]["row_id"]==2
  raw=store.data["private_profile_refresh_v1"]
  assert "tea" not in json.dumps(raw) and "msg" not in json.dumps(raw)
  assert service.calls[0]["document"]["claims"][0]["source"]=="evidence_derived"
  assert service.calls[0]["document"]["private_source_scope"]=="private"
 asyncio.run(run())

def test_private_refresh_budget_cooldown_generation_and_cancel(monkeypatch):
 store=Store();service=Service(); now=[90000.0]; calls=[]
 monkeypatch.setattr(mod,"get_data_store",lambda:store)
 monkeypatch.setattr(mod,"get_recent_session_candidates",lambda *a,**k:[{"id":2,"role":"user","content":"tea"}])
 class Slow:
  async def chat_with_tools(self,**_):calls.append(1);return SimpleNamespace(content="[]")
 async def run():
  r=mod.PrivateProfileRefresh(service,Slow(),None,threshold=1,cooldown=600,daily_budget=1,clock=lambda:now[0]);r.observe_private_message("u",platform="qq",bot_id="b",source_id="x",text="t")
  await r.refresh("qq","b","u");await r.refresh("qq","b","u")
  assert len(calls)==1
  assert await r.cancel_user_tasks("u")==1
 asyncio.run(run())

def test_private_refresh_restart_and_generation_failure_keep_watermark(monkeypatch):
 store=Store();service=Service(); now=[1000.0]
 monkeypatch.setattr(mod,"get_data_store",lambda:store)
 monkeypatch.setattr(mod,"get_recent_session_candidates",lambda *a,**k:[{"id":7,"role":"user","content":"tea","timestamp":7}])
 class Reject(Service):
  def put_scoped_document_v3(self,**kw): raise RuntimeError("generation changed")
 async def run():
  r=mod.PrivateProfileRefresh(Reject(),Caller(),None,threshold=20,cooldown=0,clock=lambda:now[0])
  r.state={r._key("qq","b","u"):{"count":20,"watermark":0,"last_run":0,"due_at":0}}
  r._save()
  await r.refresh("qq","b","u")
  assert r.state[r._key("qq","b","u")]["watermark"]==0
  restarted=mod.PrivateProfileRefresh(service,Caller(),None,cooldown=0,clock=lambda:now[0])
  assert restarted.resume_pending()==1
  await restarted.refresh("qq","b","u")
  assert service.calls and service.calls[-1]["document"]["private_target_user_id"]=="u"
  await restarted.close()
 asyncio.run(run())

def test_private_refresh_keeps_messages_arriving_during_generation(monkeypatch):
 store=Store(); service=Service(); now=[1000.0]
 monkeypatch.setattr(mod,"get_data_store",lambda:store)
 monkeypatch.setattr(mod,"get_recent_session_candidates",lambda *a,**k:[{"id":2,"role":"user","content":"tea","timestamp":2}])
 class Slow:
  def __init__(self): self.started=asyncio.Event(); self.release=asyncio.Event()
  async def chat_with_tools(self,**_):
   self.started.set(); await self.release.wait()
   return SimpleNamespace(content=json.dumps([{"key":"content_pref","value":"tea","confidence":.9,"source_ids":["2"]}]))
 async def run():
  caller=Slow(); r=mod.PrivateProfileRefresh(service,caller,None,threshold=20,cooldown=0,clock=lambda:now[0]); key=r._key("qq","b","u")
  item={"count":1,"watermark":0,"last_run":0,"due_at":0}; r.state={key:item}
  task=asyncio.create_task(r.refresh("qq","b","u")); r.tasks[key]=task; r.running.add(key)
  await asyncio.wait_for(caller.started.wait(),1)
  r.observe_private_message("u",platform="qq",bot_id="b",source_id="new",text="new preference")
  caller.release.set(); await task
  assert r.state[key]["count"] == 1
  await r.cancel_all_tasks()
 asyncio.run(run())

def test_private_refresh_real_sqlite_isolates_bot_and_reloads_sidecar(tmp_path, monkeypatch):
 store=Store(); monkeypatch.setattr(mod,"get_data_store",lambda:store)
 db_path=db.init_db_sync(tmp_path); config=SimpleNamespace(personification_data_dir=str(tmp_path),personification_memory_enabled=True,personification_memory_palace_enabled=False)
 memories=memory_store.MemoryStore(config,logger=None); memories.initialize()
 service=scoped_service.ScopedProfileService(profile_service=profile_service.ProfileService(memories,enabled_getter=lambda:True),tool_caller=None,logger=None,enabled=lambda:True,auto_threshold=1,db_path=db_path)
 with db.connect_sync(db_path) as conn:
  for role,content,meta in [("user","correct bot tea",{"platform":"qq","bot_id":"bot-a"}),("assistant","assistant must not profile",{"platform":"qq","bot_id":"bot-a"}),("user","other bot coffee",{"platform":"qq","bot_id":"bot-b"})]:
   conn.execute("INSERT INTO session_messages(session_id,role,content,is_summary,timestamp,metadata) VALUES(?,?,?,?,?,?)",("private_10001",role,json.dumps(content),0,time.time(),json.dumps(meta)))
  conn.commit()
 candidates=session_store.get_recent_session_candidates("private_10001",limit=4000,days=14,platform="qq",bot_id="bot-a")
 assert [(row["role"], row["content"]) for row in candidates] == [
  ("user", "correct bot tea"), ("assistant", "assistant must not profile")
 ]
 class SqlCaller:
  async def chat_with_tools(self,**kwargs):
   body=kwargs["messages"][1]["content"]
   assert "correct bot tea" in body and "assistant must not profile" not in body and "other bot coffee" not in body
   rows=json.loads(body.split("证据:",1)[1]); return SimpleNamespace(content=json.dumps([{"key":"content_pref","value":"tea","confidence":.9,"source_ids":[rows[0]["source_id"]]}]))
 async def run():
  first=mod.PrivateProfileRefresh(service,SqlCaller(),None,threshold=20,cooldown=0); key=first._key("qq","bot-a","10001")
  first.state={key:{"count":20,"watermark":0,"last_run":0,"due_at":0}}; first._save()
  restarted=mod.PrivateProfileRefresh(service,SqlCaller(),None,cooldown=0)
  assert restarted.resume_pending()==1
  await restarted.tasks[key]
  saved=service.get_scoped_document_v3(platform="qq",bot_id="bot-a",group_id="",user_id="10001")
  assert saved and saved["document"]["claims"][0]["value"]=="tea"
  ref=saved["document"]["claims"][0]["evidence_refs"][0]
  assert ref["message_id"] != "" and ref["content_sha256"]
  assert ref["content_sha256"] == hashlib.sha256("correct bot tea".encode("utf-8")).hexdigest()
  assert service.get_scoped_document_v3(platform="qq",bot_id="bot-b",group_id="",user_id="10001") is None
  await restarted.close()
 asyncio.run(run())
