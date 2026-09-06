from __future__ import annotations
import time
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Query
from ...core.moderation import ModerationLedger
from ...core.paths import get_data_dir
from ..deps import AdminIdentity, require_admin

def build_moderation_router(*, runtime: Any) -> APIRouter:
    router=APIRouter(prefix="/api/v2")
    def ledger()->ModerationLedger: return ModerationLedger(get_data_dir(runtime.plugin_config)/"moderation.sqlite3")
    @router.get("/moderation/status")
    async def status(_:AdminIdentity=Depends(require_admin))->dict[str,Any]:
        return {"enabled":bool(getattr(runtime.plugin_config,"personification_controlled_moderation_enabled",False)),"authorized_groups":[str(x) for x in (getattr(runtime.plugin_config,"personification_controlled_moderation_authorized_groups",[]) or [])]}
    @router.get("/moderation/incidents")
    async def incidents(page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=100),_:AdminIdentity=Depends(require_admin))->dict[str,Any]: return ledger().list_incidents(page,page_size)
    @router.get("/moderation/incidents/{incident_id}")
    async def detail(incident_id:str,_:AdminIdentity=Depends(require_admin))->dict[str,Any]:
        # New callers use the incident identifier.  Keep operation identifiers
        # readable during the WebUI transition without accepting client scope.
        row=ledger().get_incident(incident_id)
        if row is not None:
            return row
        operation=ledger().get_operation(incident_id)
        if operation is None:
            raise HTTPException(404,detail={"code":"moderation_incident_not_found","message":"未找到禁言事件。"})
        return operation
    @router.post("/moderation/operations/{operation_id}/release")
    async def release(operation_id:str,_:AdminIdentity=Depends(require_admin))->dict[str,Any]:
        row=ledger().get_operation(operation_id)
        if row is None: raise HTTPException(404,detail={"code":"moderation_operation_not_found","message":"未找到禁言操作。"})
        if str(row.get("status")) == "released":
            return {"ok":True,"code":"moderation_release_released","operation_id":row["operation_id"]}
        if str(row.get("status")) not in {"sent","unknown"}:
            return {"ok":False,"code":"moderation_release_not_releasable","operation_id":row["operation_id"]}
        state=ledger().reserve_release(row["operation_id"])
        if state not in {"releasing","unknown"}: return {"ok":state=="released","code":f"moderation_release_{state}","operation_id":row["operation_id"]}
        bot=(getattr(runtime,"get_bots",lambda:{})() or {}).get(str(row["bot_id"]))
        if bot is None:
            ledger().finish_release(row["operation_id"],"unavailable")
            return {"ok":False,"code":"moderation_release_bot_unavailable","operation_id":row["operation_id"]}
        try:
            mine=await bot.get_group_member_info(group_id=int(row["group_id"]),user_id=int(row["bot_id"]))
            target=await bot.get_group_member_info(group_id=int(row["group_id"]),user_id=int(row["target_id"]))
        except Exception:
            ledger().finish_release(row["operation_id"],"unavailable")
            return {"ok":False,"code":"moderation_release_member_query_unavailable","operation_id":row["operation_id"]}
        try:
            # Unknown/incomplete member snapshots fail closed before any API
            # write.  This is an unattempted operation, not delivery unknown.
            permitted=(isinstance(mine,dict) and isinstance(target,dict)
                and str(mine.get("role") or "") in {"owner","admin"}
                and str(target.get("role") or "") == "member")
            if not permitted:
                ledger().finish_release(row["operation_id"],"blocked")
                return {"ok":False,"code":"moderation_release_permission_blocked","operation_id":row["operation_id"]}
            if "shut_up_timestamp" not in target:
                if state != "unknown": ledger().finish_release(row["operation_id"],"unavailable")
                return {"ok":False,"code":"moderation_release_target_state_unavailable","operation_id":row["operation_id"]}
            if float(target["shut_up_timestamp"]) <= time.time():
                ledger().finish_release(row["operation_id"],"released")
                return {"ok":True,"code":"moderation_release_already_resolved","operation_id":row["operation_id"]}
        except (TypeError, ValueError):
            ledger().finish_release(row["operation_id"],"blocked")
            return {"ok":False,"code":"moderation_release_target_state_invalid","operation_id":row["operation_id"]}
        if state == "unknown":
            return {"ok":False,"code":"moderation_release_unknown","operation_id":row["operation_id"]}
        try:
            result=await bot.call_api("set_group_ban",group_id=int(row["group_id"]),user_id=int(row["target_id"]),duration=0)
            status="released" if result is None or (isinstance(result,dict) and result.get("status") not in {"failed","error"} and (result.get("status") in {"ok","success"} or "retcode" in result and result["retcode"] in {0,"0"})) else "unknown"
        except Exception:
            # Only an exception after the dedicated duration=0 call is a
            # delivery-unknown result.  It is never automatically replayed.
            status="unknown"
        ledger().finish_release(row["operation_id"],status)
        return {"ok":status=="released","code":f"moderation_release_{status}","operation_id":row["operation_id"]}
    return router
