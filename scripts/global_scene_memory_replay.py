#!/usr/bin/env python
"""Offline, deterministic replay for global memory/social safety contracts.

It intentionally does not call a provider or OneBot. ``--api-command`` is an
explicit opt-in hook for a separately authorized runner; its JSONL metrics can
be merged but never turns this script into an outbound sender.
"""
from __future__ import annotations

import argparse, importlib.util, json, math, random, statistics, sys, threading, time, types
from contextlib import contextmanager
from types import SimpleNamespace
from collections import Counter
from pathlib import Path
from typing import Any

REQUIRED = {"id", "kind", "platform", "bot_id", "scope", "event_ids", "expect", "facts"}

def load(path: Path) -> list[dict[str, Any]]:
    rows=[]
    for n,line in enumerate(path.read_text(encoding="utf-8").splitlines(),1):
        if not line.strip(): continue
        item=json.loads(line)
        if not isinstance(item,dict) or not REQUIRED <= set(item): raise ValueError(f"{path}:{n}: invalid record")
        if not isinstance(item["event_ids"],list) or not all(isinstance(x,str) and x for x in item["event_ids"]): raise ValueError(f"{path}:{n}: event_ids")
        if not isinstance(item["facts"],list) or not item["facts"] or not all(isinstance(x,dict) and isinstance(x.get("id"),str) and isinstance(x.get("text"),str) and isinstance(x.get("timestamp"),(int,float)) for x in item["facts"]): raise ValueError(f"{path}:{n}: explicit facts")
        rows.append(item)
    return rows

class _Store:
    """In-memory replacement for DataStore; mutate_sync keeps the real CAS path."""
    def __init__(self): self.data={}; self.lock=threading.Lock()
    def mutate_sync(self,name,mutator):
        with self.lock: self.data[name]=mutator(self.data.get(name,{})); return self.data[name]

def social_module():
    root=Path(__file__).resolve().parent.parent; name="plugin.personification.core.social_decision"
    if name in sys.modules: return sys.modules[name]
    for package,path in (("plugin",root.parent.parent),("plugin.personification",root),("plugin.personification.core",root/"core")):
        m=types.ModuleType(package); m.__path__=[str(path)]; sys.modules.setdefault(package,m)
    spec=importlib.util.spec_from_file_location(name,root/"core"/"social_decision.py")
    module=importlib.util.module_from_spec(spec); sys.modules[name]=module; assert spec and spec.loader; spec.loader.exec_module(module); return module

def module(name: str):
    root=Path(__file__).resolve().parent.parent
    if name in sys.modules: return sys.modules[name]
    social_module()  # establishes namespace packages
    spec=importlib.util.spec_from_file_location(name,root.joinpath(*name.split(".")[2:]).with_suffix(".py"))
    m=importlib.util.module_from_spec(spec); sys.modules[name]=m; assert spec and spec.loader; spec.loader.exec_module(m); return m

def memory_execute(row: dict[str,Any], artifact: Path) -> tuple[bool,str]:
    """Use real MemoryStore SQLite plus temporal revision/load on an isolated DB."""
    memory=module("plugin.personification.core.memory_store"); temporal=module("plugin.personification.core.temporal_memory")
    db=module("plugin.personification.core.db"); db_path=db.init_db_sync(artifact / row["id"] / "sqlite")
    @contextmanager
    def connect():
        import sqlite3
        conn=sqlite3.connect(db_path); conn.row_factory=sqlite3.Row
        try: yield conn
        finally: conn.close()
    old=temporal.connect_sync; temporal.connect_sync=connect
    try:
        scope_kind, _, scope_subject = str(row["scope"]).partition(":")
        scope={
            "platform":row["platform"], "bot_id":row["bot_id"],
            # QZone records have an owner/actor identity just as direct
            # records do; only their later projection differs.
            "user_id":scope_subject if scope_kind in {"private", "qzone"} else "",
            "group_id":scope_subject if scope_kind == "group" else "",
        }
        facts=row["facts"]
        # Temporal revisions must retain corpus time.  Replacing this with a
        # local counter would make a cross-month/year case meaningless.
        messages=[{"id":f["id"],"role":"user","content":f["text"],"timestamp":float(f["timestamp"])} for f in facts]
        if row["kind"] in {"cancel","correction","postpone"}:
            temporal._commit(scope,[{"statement":facts[0]["text"],"status":"planned","source_ids":[facts[0]["id"]]}],[],messages)
            prior=temporal.load_current_states(scope)
            temporal._commit(scope,[{"state_id":prior[0]["state_id"],"statement":facts[-1]["text"],"status":"cancelled" if row["kind"]=="cancel" else "planned","source_ids":[facts[-1]["id"]]}],prior,messages)
            return bool(temporal.load_current_states(scope) and temporal.load_current_states(scope)[0]["statement"]==facts[-1]["text"]),"correction"
        store=memory.MemoryStore(SimpleNamespace(personification_data_dir=str(artifact / row["id"] / "memory"),personification_memory_retrieval_mode="algorithm_llm",personification_memory_recall_top_k=5,personification_memory_retrieval_days=0))
        expected_id="mem-"+row["id"]
        store.initialize()
        if row["kind"] == "qzone_visibility":
            # QZone shares the Bot/user identity with direct contexts, but its
            # surface projection may expose only public preferences.  Write a
            # tempting private sibling as the negative case and verify that
            # neither private permission nor non-public visibility survives.
            private_id="private-"+row["id"]
            store.write_memory_item({"memory_id":private_id,"summary":facts[0]["text"],"user_id":scope["user_id"],"platform":scope["platform"],"bot_id":scope["bot_id"],"permission_type":"private_fact","visibility":"private"})
            store.write_memory_item({"memory_id":expected_id,"summary":facts[0]["text"],"aliases":list(facts[0].get("aliases",[])),"user_id":scope["user_id"],"platform":scope["platform"],"bot_id":scope["bot_id"],"permission_type":"public_preference","visibility":"public"})
            raw=store.recall_memories(query=row.get("query",facts[0]["text"]),user_id=scope["user_id"],group_id="",platform=scope["platform"],bot_id=scope["bot_id"],limit=8)
            raw_ids={str(item.get("memory_id", "")) for item in raw}
            projected=[item for item in raw if item.get("permission_type")=="public_preference" and item.get("visibility")=="public"]
            projected_ids={str(item.get("memory_id", "")) for item in projected}
            return expected_id in raw_ids and private_id in raw_ids and projected_ids == {expected_id},"qzone_visibility"
        store.write_memory_item({"memory_id":expected_id,"summary":facts[0]["text"],"aliases":list(facts[0].get("aliases",[])),"user_id":scope["user_id"],"group_id":scope["group_id"],"platform":scope["platform"],"bot_id":scope["bot_id"],"permission_type":"private_fact" if scope["user_id"] else "group_fact"})
        if row["kind"] in {"private_isolation","group_isolation","bot_isolation"}:
            own=store.recall_memories(query=row.get("query",facts[0]["text"]),user_id=scope["user_id"],group_id=scope["group_id"],platform=scope["platform"],bot_id=scope["bot_id"])
            own_visible=any(str(item.get("memory_id", ""))==expected_id for item in own)
            other=dict(scope)
            if row["kind"]=="private_isolation":
                other["user_id"]=scope["user_id"]+"__other"
            elif row["kind"]=="group_isolation":
                other["group_id"]=scope["group_id"]+"__other"
            else:
                other["bot_id"]=scope["bot_id"]+"__other"
            found=store.recall_memories(query=row.get("query",facts[0]["text"]),user_id=other["user_id"],group_id=other["group_id"],platform=other["platform"],bot_id=other["bot_id"])
            leaked=any(str(item.get("memory_id", ""))==expected_id for item in found)
            return own_visible and not leaked,"isolation"
        found=store.recall_memories(query=row["query"],user_id=scope["user_id"],group_id=scope["group_id"],platform=scope["platform"],bot_id=scope["bot_id"]); return any(str(item.get("memory_id",""))==expected_id for item in found),"recall"
    finally: temporal.connect_sync=old

def execute(row: dict[str,Any], social: Any, store: _Store, artifact: Path) -> tuple[bool,str]:
    """Exercise parser + atomic claim/settle; expected data is compared only after observation."""
    if row["kind"] in {"recall","short_chinese","private_isolation","group_isolation","bot_isolation","qzone_visibility","cancel","correction","postpone"}:
        return memory_execute(row,artifact)
    import plugin.personification.core.data_store as data_store
    original=data_store.get_data_store; data_store.get_data_store=lambda:store
    try:
        kind=row["kind"]; source=list(row["event_ids"]); target=row["scope"].split(":")[-1]
        if kind=="silent": raw={"send":False,"action":"silent","content":"","motivation":"busy","source_event_ids":source}
        elif kind=="defer": raw={"send":False,"action":"defer","target_id":target,"content":"","motivation":"later","source_event_ids":source,"next_consider_at":float(row["now"])+1}
        else: raw={"send":True,"action":"contact","target_id":target,"content":"synthetic","motivation":"event","source_event_ids":source}
        decision=social.parse_social_decision(json.dumps(raw),allowed_source_event_ids=set(source))
        if decision is None: return False,"parse"
        if kind=="silent": return (not decision.should_send),"silence"
        if kind=="defer": return (not decision.should_send and decision.next_consider_at>float(row["now"])),"defer"
        first=social.claim_social_decision(decision,channel="replay",scope=row["bot_id"]+":"+row["scope"],now=100)
        if not first: return False,"claim"
        if kind in {"unknown_receipt","duplicate_motivation"}:
            social.settle_social_decision(decision,status="unknown",scope=row["bot_id"]+":"+row["scope"])
            rewrite=type(decision)("join",decision.target_id,"rewritten","other",decision.source_event_ids)
            return (not social.claim_social_decision(rewrite,channel="other",scope=row["bot_id"]+":"+row["scope"],now=101)),"unknown" if kind=="unknown_receipt" else "duplicate"
        social.settle_social_decision(decision,status="sent",scope=row["bot_id"]+":"+row["scope"])
        return True,"correction" if kind in {"cancel","correction","postpone"} else "recall"
    finally: data_store.get_data_store=original

def pct(values: list[float], q: float) -> float:
    if not values: return 0.0
    return sorted(values)[max(0, math.ceil(len(values)*q)-1)]

def run(rows: list[dict[str,Any]], repeat: int, seed: int, artifact: Path) -> dict[str,Any]:
    rng=random.Random(seed); results=[]; durations=[]
    social=social_module()
    for attempt in range(repeat):
        store=_Store(); attempt_dir=artifact / f"run-{attempt}"; attempt_dir.mkdir(parents=True,exist_ok=True)
        ordered=list(rows); rng.shuffle(ordered)
        for row in ordered:
            start=time.perf_counter(); observed,label=execute(row,social,store,attempt_dir); durations.append((time.perf_counter()-start)*1000)
            expected = (row["expect"].get("isolated", True) if label=="isolation" else row["expect"].get("latest_wins") if label=="correction" else not row["expect"].get("send",True) if label=="silence" else not row["expect"].get("replay",True) if label in {"unknown","duplicate"} else row["expect"].get("next_consider_at",0)>row.get("now",0) if label=="defer" else row["expect"].get("recall",row["expect"].get("stable_event",True)))
            results.append({"id":row["id"],"attempt":attempt,"ok":observed==expected,"observed":observed,"label":label,"token_usage":None,"cost":None})
    labels=Counter(x["label"] for x in results); total=len(results); good=sum(x["ok"] for x in results)
    correction=[x for x in results if x["label"]=="correction"]
    return {"mode":"offline_deterministic","seed":seed,"repeat":repeat,"samples":len(rows),"runs":total,
      "accuracy":good/total if total else 0,"correction_recurrence":sum(not x["ok"] for x in correction)/len(correction) if correction else 0,
      "recall_hit":sum(x["ok"] for x in results if x["label"]=="recall")/max(1,labels["recall"]),
      "token_usage":None,"cost":None,"p50_ms":statistics.median(durations),"p95_ms":pct(durations,.95),"labels":dict(labels),"categories":dict(labels),"results":results}

def main() -> int:
    p=argparse.ArgumentParser(); p.add_argument("--input",default="tests/replay_corpus/global_memory/global_scene_memory.jsonl"); p.add_argument("--output",required=True); p.add_argument("--artifact-dir"); p.add_argument("--baseline"); p.add_argument("--seed",type=int,default=20260908); p.add_argument("--repeat",type=int,default=3); p.add_argument("--blind-export"); p.add_argument("--api-command",help="explicit authorized command only; never used by default")
    a=p.parse_args(); rows=load(Path(a.input)); artifact=Path(a.artifact_dir or Path(a.output).parent / "global_scene_memory_sqlite"); report=run(rows,a.repeat,a.seed,artifact); report["corpus_sha256"]=__import__("hashlib").sha256(Path(a.input).read_bytes()).hexdigest()
    if a.baseline:
        base=json.loads(Path(a.baseline).read_text(encoding="utf-8")); report["baseline_delta"]={k:report.get(k)-base.get(k) for k in ("accuracy","correction_recurrence","recall_hit") if isinstance(base.get(k),(int,float))}
    if a.blind_export:
        Path(a.blind_export).write_text("\n".join(json.dumps({"id":r["id"],"kind":r["kind"],"prompt":r.get("blind_prompt","")},ensure_ascii=False) for r in rows)+"\n",encoding="utf-8")
    if a.api_command:
        report["api"]={"requested":True,"executed":False,"note":"external API execution requires a separately authorized runner"}
    report["limitations"]="Offline deterministic checks validate contracts only; they do not measure naturalness, provider quality, QQ/QZone delivery, token usage, or cost."
    Path(a.output).parent.mkdir(parents=True,exist_ok=True); Path(a.output).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    return 0
if __name__=="__main__": raise SystemExit(main())
