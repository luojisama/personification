"""Fail-closed incident ledger for the controlled group mute capability."""
from __future__ import annotations
import asyncio, math, sqlite3, time, uuid
from pathlib import Path
from typing import Any, Awaitable, Callable

WARNING_TTL_SECONDS = 30 * 60

class ModerationLedger:
    def __init__(self, path: Path) -> None:
        self.path=Path(path); self.path.parent.mkdir(parents=True,exist_ok=True)
        with self._db() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS moderation_events(platform TEXT,bot_id TEXT,group_id TEXT,target_id TEXT,incident TEXT,message_id TEXT,occurred_at REAL,negative INTEGER,offensive INTEGER,PRIMARY KEY(platform,bot_id,group_id,target_id,incident,message_id));
            CREATE TABLE IF NOT EXISTS moderation_warnings(platform TEXT,bot_id TEXT,group_id TEXT,target_id TEXT,incident TEXT,round_id TEXT,message_id TEXT,confirmed_at REAL,reconciled INTEGER DEFAULT 0,PRIMARY KEY(platform,bot_id,group_id,target_id,incident,round_id));
            CREATE TABLE IF NOT EXISTS moderation_operations(platform TEXT,bot_id TEXT,group_id TEXT,target_id TEXT,incident TEXT,operation_id TEXT UNIQUE,status TEXT,minutes INTEGER,created_at REAL,updated_at REAL,PRIMARY KEY(platform,bot_id,group_id,target_id,incident));
            CREATE TABLE IF NOT EXISTS moderation_incidents(platform TEXT,bot_id TEXT,group_id TEXT,target_id TEXT,incident TEXT PRIMARY KEY, reconciled_at REAL);
            CREATE TABLE IF NOT EXISTS moderation_releases(operation_id TEXT PRIMARY KEY,status TEXT NOT NULL,updated_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS moderation_warning_attempts(platform TEXT,bot_id TEXT,group_id TEXT,target_id TEXT,incident TEXT,source_message_id TEXT,round_id TEXT,status TEXT,PRIMARY KEY(platform,bot_id,group_id,target_id,incident,source_message_id),UNIQUE(platform,bot_id,group_id,target_id,incident,round_id));
            """)
            try: db.execute("ALTER TABLE moderation_warnings ADD COLUMN source_message_id TEXT NOT NULL DEFAULT ''")
            except sqlite3.OperationalError: pass
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS moderation_warning_source_unique ON moderation_warnings(platform,bot_id,group_id,target_id,incident,source_message_id) WHERE source_message_id<>''")
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS moderation_warning_message_unique ON moderation_warnings(platform,bot_id,group_id,target_id,incident,message_id)")
    def get_or_create_active_incident(self, *, platform:str,bot_id:str,group_id:str,target_id:str,now:float)->str|None:
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            # An unresolved write blocks every later incident for this target,
            # including after reconciliation or a process restart.
            if db.execute("SELECT 1 FROM moderation_operations WHERE platform=? AND bot_id=? AND group_id=? AND target_id=? AND status IN ('running','unknown')",(platform,bot_id,group_id,target_id)).fetchone(): return None
            row=db.execute("SELECT incident,reconciled_at FROM moderation_incidents WHERE platform=? AND bot_id=? AND group_id=? AND target_id=? ORDER BY rowid DESC LIMIT 1",(platform,bot_id,group_id,target_id)).fetchone()
            if row and row[1] is None:
                incident=str(row[0]); operation=db.execute("SELECT status FROM moderation_operations WHERE platform=? AND bot_id=? AND group_id=? AND target_id=? AND incident=?",(platform,bot_id,group_id,target_id,incident)).fetchone()
                # Once a warning is confirmed, its first delivery anchors this
                # conflict window.  Later hostile messages must not keep two
                # old warnings alive forever.  Before any warning exists, the
                # first trusted offense is the only available anchor.
                warning_anchor=db.execute("SELECT min(confirmed_at) FROM moderation_warnings WHERE platform=? AND bot_id=? AND group_id=? AND target_id=? AND incident=? AND reconciled=0",(platform,bot_id,group_id,target_id,incident)).fetchone()[0]
                event_anchor=db.execute("SELECT min(occurred_at) FROM moderation_events WHERE platform=? AND bot_id=? AND group_id=? AND target_id=? AND incident=?",(platform,bot_id,group_id,target_id,incident)).fetchone()[0]
                anchor=warning_anchor if warning_anchor is not None else event_anchor
                if not operation and anchor and float(now)-WARNING_TTL_SECONDS<=float(anchor)<=float(now): return incident
            incident=f"incident-{uuid.uuid4().hex}"
            db.execute("INSERT INTO moderation_incidents VALUES(?,?,?,?,?,NULL)",(platform,bot_id,group_id,target_id,incident))
            return incident
    def _db(self):
        db=sqlite3.connect(self.path); db.row_factory=sqlite3.Row; return db
    def event(self, *, platform:str,bot_id:str,group_id:str,target_id:str,incident:str,message_id:str,occurred_at:float,negative:bool,offensive:bool)->bool:
        if not (negative is True and offensive is True and math.isfinite(float(occurred_at)) and all([platform,bot_id,group_id,target_id,incident,message_id])): return False
        with self._db() as db:
            return bool(db.execute("INSERT OR IGNORE INTO moderation_events VALUES(?,?,?,?,?,?,?,?,?)",(platform,bot_id,group_id,target_id,incident,message_id,float(occurred_at),1,1)).rowcount)
    def warning(self, *, platform:str,bot_id:str,group_id:str,target_id:str,incident:str,round_id:str,message_id:str,confirmed_at:float,source_message_id:str="")->bool:
        if not all([platform,bot_id,group_id,target_id,incident,round_id,message_id,source_message_id]): return False
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            if not self._warning_allowed(db,(platform,bot_id,group_id,target_id,incident),source_message_id,round_id,float(confirmed_at)): return False
            inserted=bool(db.execute("INSERT OR IGNORE INTO moderation_warnings(platform,bot_id,group_id,target_id,incident,round_id,message_id,confirmed_at,source_message_id) VALUES(?,?,?,?,?,?,?,?,?)",(platform,bot_id,group_id,target_id,incident,round_id,message_id,float(confirmed_at),source_message_id)).rowcount)
            if inserted:
                db.execute("UPDATE moderation_warning_attempts SET status='sent' WHERE platform=? AND bot_id=? AND group_id=? AND target_id=? AND incident=? AND source_message_id=? AND round_id=? AND status='running'",(platform,bot_id,group_id,target_id,incident,source_message_id,round_id))
            return inserted
    def _warning_allowed(self, db, keys, source, round_id, now):
        if not math.isfinite(now): return False
        if db.execute("SELECT 1 FROM moderation_operations WHERE platform=? AND bot_id=? AND group_id=? AND target_id=? AND incident=?",keys).fetchone(): return False
        if db.execute("SELECT 1 FROM moderation_incidents WHERE platform=? AND bot_id=? AND group_id=? AND target_id=? AND incident=? AND reconciled_at IS NOT NULL",keys).fetchone(): return False
        evidence=db.execute("SELECT occurred_at FROM moderation_events WHERE platform=? AND bot_id=? AND group_id=? AND target_id=? AND incident=? AND message_id=? AND offensive=1 AND negative=1",(*keys,source)).fetchone()
        if not evidence or not now-WARNING_TTL_SECONDS <= float(evidence[0]) <= now: return False
        warnings=db.execute("SELECT confirmed_at,round_id,source_message_id FROM moderation_warnings WHERE platform=? AND bot_id=? AND group_id=? AND target_id=? AND incident=? AND reconciled=0 ORDER BY confirmed_at",keys).fetchall()
        if len(warnings)>=2 or any(row[1]==round_id or row[2]==source for row in warnings): return False
        # Two messages buffered before the first reminder are one interaction;
        # a second reminder requires a later incoming message after delivery.
        return not warnings or now-WARNING_TTL_SECONDS <= float(warnings[-1][0]) < float(evidence[0])
    def reserve_warning(self, *, platform,bot_id,group_id,target_id,incident,source_message_id,round_id,now):
        keys=(platform,bot_id,group_id,target_id,incident)
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            if not source_message_id or not round_id or not self._warning_allowed(db,keys,source_message_id,round_id,float(now)): return False
            return bool(db.execute("INSERT OR IGNORE INTO moderation_warning_attempts VALUES(?,?,?,?,?,?,?,'running')",(*keys,source_message_id,round_id)).rowcount)
    def finish_warning_attempt(self, *, platform:str,bot_id:str,group_id:str,target_id:str,incident:str,source_message_id:str,round_id:str,status:str)->bool:
        """Record a non-confirmed delivery outcome without turning it into a warning.

        ``unknown`` is terminal for the attempt: re-sending could duplicate a
        delivered reminder, so later processing may query/reconcile only.
        """
        if status not in {"sent","unknown","failed"}: return False
        with self._db() as db:
            return bool(db.execute("UPDATE moderation_warning_attempts SET status=? WHERE platform=? AND bot_id=? AND group_id=? AND target_id=? AND incident=? AND source_message_id=? AND round_id=? AND status='running'",(status,platform,bot_id,group_id,target_id,incident,source_message_id,round_id)).rowcount)
    def warning_evidence(self, *, platform,bot_id,group_id,target_id,incident):
        with self._db() as db:
            return [dict(row) for row in db.execute("SELECT round_id,message_id,source_message_id,confirmed_at,reconciled FROM moderation_warnings WHERE platform=? AND bot_id=? AND group_id=? AND target_id=? AND incident=? ORDER BY confirmed_at LIMIT 2",(platform,bot_id,group_id,target_id,incident))]
    def reconcile(self, *, platform:str,bot_id:str,group_id:str,target_id:str,incident:str)->None:
        with self._db() as db:
            db.execute("UPDATE moderation_warnings SET reconciled=1 WHERE platform=? AND bot_id=? AND group_id=? AND target_id=? AND incident=?",(platform,bot_id,group_id,target_id,incident))
            db.execute("INSERT INTO moderation_incidents(platform,bot_id,group_id,target_id,incident,reconciled_at) VALUES(?,?,?,?,?,?) ON CONFLICT(incident) DO UPDATE SET reconciled_at=excluded.reconciled_at",(platform,bot_id,group_id,target_id,incident,time.time()))
    def eligible(self, *, platform:str,bot_id:str,group_id:str,target_id:str,incident:str,now:float)->bool:
        with self._db() as db: return self._eligible_db(db,platform,bot_id,group_id,target_id,incident,now)
    def _eligible_db(self, db:sqlite3.Connection, platform:str,bot_id:str,group_id:str,target_id:str,incident:str,now:float)->bool:
        args=(platform,bot_id,group_id,target_id,incident,now-WARNING_TTL_SECONDS,now)
        if db.execute("SELECT 1 FROM moderation_incidents WHERE platform=? AND bot_id=? AND group_id=? AND target_id=? AND incident=? AND reconciled_at IS NOT NULL",(platform,bot_id,group_id,target_id,incident)).fetchone(): return False
        warnings=db.execute("SELECT confirmed_at FROM moderation_warnings WHERE platform=? AND bot_id=? AND group_id=? AND target_id=? AND incident=? AND reconciled=0 AND confirmed_at>=? AND confirmed_at<=? ORDER BY confirmed_at",args).fetchall()
        if len(warnings)<2: return False
        second=float(warnings[1][0])
        return db.execute("SELECT 1 FROM moderation_events WHERE platform=? AND bot_id=? AND group_id=? AND target_id=? AND incident=? AND occurred_at>? AND occurred_at<=? AND negative=1 AND offensive=1",(platform,bot_id,group_id,target_id,incident,second,now)).fetchone() is not None
    def reserve(self, *, platform:str,bot_id:str,group_id:str,target_id:str,incident:str,minutes:int,now:float)->dict[str,Any]|None:
        if type(minutes) is not int or not 1<=minutes<=20: return None
        created=time.time(); op=f"moderation-{uuid.uuid4().hex}"
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            if not self._eligible_db(db,platform,bot_id,group_id,target_id,incident,float(now)): return None
            inserted=db.execute("INSERT OR IGNORE INTO moderation_operations VALUES(?,?,?,?,?,?,?,?,?,?)",(platform,bot_id,group_id,target_id,incident,op,"running",minutes,created,created)).rowcount
            if not inserted: return None
        return {"operation_id":op,"status":"running","minutes":minutes}
    def finish(self, operation_id:str,status:str)->None:
        if status not in {"sent","failed","unknown"}: raise ValueError(status)
        with self._db() as db: db.execute("UPDATE moderation_operations SET status=?,updated_at=? WHERE operation_id=? AND status='running'",(status,time.time(),operation_id))
    def list_operations(self, page:int=1, page_size:int=20)->dict[str,Any]:
        page=max(1,int(page)); size=min(100,max(1,int(page_size)))
        with self._db() as db:
            total=int(db.execute("SELECT count(*) FROM moderation_operations").fetchone()[0]); rows=db.execute("SELECT platform,bot_id,group_id,target_id,incident,operation_id,status,minutes,created_at,updated_at FROM moderation_operations ORDER BY created_at DESC LIMIT ? OFFSET ?",(size,(page-1)*size)).fetchall()
        return {"items":[dict(row) for row in rows],"page":page,"page_size":size,"total":total,"total_pages":max(1,(total+size-1)//size)}
    def list_incidents(self, page:int=1, page_size:int=20)->dict[str,Any]:
        page=max(1,int(page)); size=min(100,max(1,int(page_size)))
        query="""SELECT platform,bot_id,group_id,target_id,incident,max(confirmed_at) updated_at,count(*) warning_count,group_concat(message_id) warning_message_ids,group_concat(source_message_id) evidence_message_ids FROM moderation_warnings GROUP BY platform,bot_id,group_id,target_id,incident"""
        with self._db() as db:
            total=int(db.execute("SELECT count(*) FROM ("+query+")").fetchone()[0]); rows=db.execute(query+" ORDER BY updated_at DESC LIMIT ? OFFSET ?",(size,(page-1)*size)).fetchall()
            items=[]
            for row in rows:
                data=dict(row); op=db.execute("SELECT operation_id,status,minutes FROM moderation_operations WHERE platform=? AND bot_id=? AND group_id=? AND target_id=? AND incident=?",tuple(data[k] for k in ("platform","bot_id","group_id","target_id","incident"))).fetchone()
                anchor=db.execute("SELECT min(confirmed_at) FROM moderation_warnings WHERE platform=? AND bot_id=? AND group_id=? AND target_id=? AND incident=? AND reconciled=0",tuple(data[k] for k in ("platform","bot_id","group_id","target_id","incident"))).fetchone()[0]
                data.update({"operation_id":str(op[0]) if op else "","status":str(op[1]) if op else "warning_only","minutes":int(op[2]) if op else 0,"warning_message_ids":str(data["warning_message_ids"] or "").split(","),"evidence_message_ids":str(data["evidence_message_ids"] or "").split(","),"expires_at":float(anchor if anchor is not None else data["updated_at"])+WARNING_TTL_SECONDS}); items.append(data)
        return {"items":items,"page":page,"page_size":size,"total":total,"total_pages":max(1,(total+size-1)//size)}
    def get_incident(self, incident:str)->dict[str,Any]|None:
        """Return the safe administrative projection for one incident.

        Message identifiers are deliberately retained for audit navigation, but
        neither message bodies nor model reasoning are exposed to the WebUI.
        """
        incident=str(incident or "")[:128]
        if not incident:
            return None
        with self._db() as db:
            warnings=db.execute(
                "SELECT platform,bot_id,group_id,target_id,incident,round_id,message_id,source_message_id,confirmed_at,reconciled "
                "FROM moderation_warnings WHERE incident=? ORDER BY confirmed_at LIMIT 2", (incident,)
            ).fetchall()
            operation=db.execute(
                "SELECT operation_id,status,minutes,created_at,updated_at FROM moderation_operations WHERE incident=?", (incident,)
            ).fetchone()
        if not warnings and not operation:
            return None
        first=dict(warnings[0]) if warnings else {}
        return {
            "incident": incident,
            "platform": str(first.get("platform", "")),
            "bot_id": str(first.get("bot_id", "")),
            "group_id": str(first.get("group_id", "")),
            "target_id": str(first.get("target_id", "")),
            "warnings": [dict(row) for row in warnings],
            "warning_count": len(warnings),
            "operation": dict(operation) if operation else None,
        }
    def get_operation(self, operation_id:str)->dict[str,Any]|None:
        with self._db() as db:
            row=db.execute("SELECT platform,bot_id,group_id,target_id,incident,operation_id,status,minutes,created_at,updated_at FROM moderation_operations WHERE operation_id=?",(str(operation_id)[:96],)).fetchone()
        return dict(row) if row else None
    def reserve_release(self, operation_id:str)->str:
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row=db.execute("SELECT status,updated_at FROM moderation_releases WHERE operation_id=?",(operation_id,)).fetchone()
            if row:
                state=str(row[0])
                if state == "releasing":
                    if time.time()-float(row[1]) < 120: return "in_progress"
                    db.execute("UPDATE moderation_releases SET status='unknown',updated_at=? WHERE operation_id=?",(time.time(),operation_id))
                    return "unknown"
                # No adapter write occurred for these preflight outcomes, so a
                # later administrator may safely retry after restoring access.
                if state in {"blocked","unavailable"}:
                    db.execute("UPDATE moderation_releases SET status='releasing',updated_at=? WHERE operation_id=?",(time.time(),operation_id))
                    return "releasing"
                return state
            db.execute("INSERT INTO moderation_releases VALUES(?,?,?)",(operation_id,"releasing",time.time())); return "releasing"
    def finish_release(self, operation_id:str,status:str)->None:
        if status not in {"released","unknown","blocked","unavailable"}: raise ValueError(status)
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("UPDATE moderation_releases SET status=?,updated_at=? WHERE operation_id=? AND (status='releasing' OR (status='unknown' AND ?='released'))",(status,time.time(),operation_id,status))
            if status == "released":
                db.execute("UPDATE moderation_operations SET status='released',updated_at=? WHERE operation_id=? AND status IN ('sent','unknown')",(time.time(),operation_id))

async def execute_controlled_mute(ledger:ModerationLedger, *, scope:dict[str,str], minutes:int, now:float, revalidate:Callable[[],Awaitable[dict[str,bool]]], adapter_mute:Callable[[int],Awaitable[str]])->dict[str,Any]:
    keys={name:str(scope.get(name) or "") for name in ("platform","bot_id","group_id","target_id","incident")}
    if not ledger.eligible(**keys,now=now): return {"status":"blocked","code":"moderation_evidence_insufficient"}
    permission=await revalidate()
    allowed = all(permission.get(key) is True for key in ("bot_admin","target_manageable","adapter_mute_supported"))
    if not allowed or permission.get("already_muted") is True:
        return {"status":"blocked","code":"moderation_permission_or_state_blocked"}
    if not ledger.eligible(**keys,now=now): return {"status":"blocked","code":"moderation_evidence_changed"}
    reservation=ledger.reserve(**keys,minutes=minutes,now=now)
    if reservation is None: return {"status":"blocked","code":"moderation_duplicate_or_duration_invalid"}
    try: result=await adapter_mute(minutes)
    except asyncio.CancelledError:
        ledger.finish(reservation["operation_id"],"unknown"); raise
    except Exception: result="unknown"
    status=result if result in {"sent","failed","unknown"} else "unknown"
    ledger.finish(reservation["operation_id"],status)
    return {**reservation,"status":status}
