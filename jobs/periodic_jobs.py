import calendar
import asyncio
import json
import random
import re
import time
import uuid
from typing import Any, Callable, Dict, Iterable

from ..core.data_store import get_data_store
from ..core.db import connect_sync


_IMAGE_B64_RE = re.compile(r"\[IMAGE_B64\][A-Za-z0-9+/=\r\n]+\[/IMAGE_B64\]")
_QZONE_SOCIAL_SCAN_TIMEOUT_SECONDS = 180.0
_QZONE_INBOUND_TIMEOUT_SECONDS = 90.0


def build_qzone_quota(
    *,
    state: Any,
    now: Any,
    monthly_limit: int,
    min_interval_hours: float,
) -> Dict[str, Any]:
    """计算发空间的月度额度快照，供 agent 自我节奏控制与 WebUI 展示共用。

    state 为 data_store 的 qzone_post_state；若其 period 不是当前月（或缺失），
    本月已发计 0（与 run_proactive 的按月重置一致）。
    """
    period = now.strftime("%Y-%m")
    same_period = isinstance(state, dict) and state.get("period") == period
    used = int((state or {}).get("count", 0) or 0) if same_period else 0
    limit = max(0, int(monthly_limit or 0))
    remaining = max(0, limit - used)
    days_in_month = calendar.monthrange(now.year, now.month)[1]
    day_of_month = int(getattr(now, "day", 1) or 1)
    days_left = max(1, days_in_month - day_of_month + 1)
    last_post_at = float((state or {}).get("last_post_at", 0) or 0)
    min_interval_seconds = max(0.0, float(min_interval_hours or 0)) * 3600
    next_eligible_at = (
        last_post_at + min_interval_seconds if (last_post_at and min_interval_seconds) else 0.0
    )
    return {
        "month": period,
        "used": used,
        "limit": limit,
        "remaining": remaining,
        "days_in_month": days_in_month,
        "day_of_month": day_of_month,
        "days_left": days_left,
        "min_interval_hours": float(min_interval_hours or 0),
        "last_post_at": last_post_at,
        "next_eligible_at": next_eligible_at,
    }


def _compact_qzone_state_content(content: str) -> str:
    return _IMAGE_B64_RE.sub("[配图]", str(content or "")).strip()[:200]


def _remember_qzone_post(state: dict[str, Any], content: str, *, max_items: int = 12) -> None:
    compact = _compact_qzone_state_content(content)
    if not compact:
        return
    recent_raw = state.get("recent_contents")
    recent = list(recent_raw) if isinstance(recent_raw, list) else []
    normalized: list[str] = []
    seen: set[str] = set()
    for item in recent + [compact]:
        text = str(item.get("content") if isinstance(item, dict) else item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        normalized.append(text)
    state["recent_contents"] = normalized[-max(1, int(max_items)) :]
    state["last_content"] = compact


def record_qzone_post(content: str, *, now: Any, kind: str = "post") -> Dict[str, Any]:
    """发布一条空间内容后更新 qzone_post_state（按月总数 +1、记录去重）。

    供 WebUI 手动「立即发一条」和自动转发复用，保证所有外显空间发布都计入月度额度。
    """
    operation_id = f"legacy-{uuid.uuid4().hex}"
    reserve_qzone_publish(
        operation_id=operation_id,
        now=now,
        monthly_limit=0,
        min_interval_hours=0,
        kind=kind,
        force=True,
    )
    return finalize_qzone_publish(operation_id=operation_id, content=content, now=now, outcome="success")


def _qzone_now_ts(now: Any) -> float:
    try:
        return float(now.timestamp())
    except Exception:
        return time.time()


def _load_qzone_state_from_conn(conn: Any) -> dict[str, Any]:
    row = conn.execute(
        "SELECT value FROM kv_store WHERE namespace=? AND key=?",
        ("qzone_post_state", "__root__"),
    ).fetchone()
    if not row:
        return {}
    try:
        payload = json.loads(row["value"] or "{}")
    except Exception:
        payload = {}
    return payload if isinstance(payload, dict) else {}


def _save_qzone_state_to_conn(conn: Any, state: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO kv_store(namespace, key, value, updated_at)
        VALUES (?, ?, ?, unixepoch('now'))
        ON CONFLICT(namespace, key)
        DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
        """,
        ("qzone_post_state", "__root__", json.dumps(state, ensure_ascii=False)),
    )


def reserve_qzone_publish(
    *,
    operation_id: str,
    now: Any,
    monthly_limit: int,
    min_interval_hours: float,
    kind: str = "post",
    force: bool = False,
    lease_seconds: int = 300,
) -> dict[str, Any]:
    op_id = str(operation_id or "").strip()[:96]
    if not op_id:
        raise ValueError("operation_id is required")
    period = now.strftime("%Y-%m")
    now_ts = _qzone_now_ts(now)
    with connect_sync() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "UPDATE qzone_publish_operations SET status='expired' WHERE status IN ('reserved','unknown') AND expires_at<=?",
            (now_ts,),
        )
        existing = conn.execute(
            "SELECT status, detail FROM qzone_publish_operations WHERE operation_id=?",
            (op_id,),
        ).fetchone()
        if existing is not None:
            existing_status = str(existing["status"])
            if existing_status in {"released", "expired"}:
                conn.execute("DELETE FROM qzone_publish_operations WHERE operation_id=?", (op_id,))
            else:
                conn.commit()
                return {"ok": existing_status == "committed", "duplicate": True, "status": existing_status}
        state = _load_qzone_state_from_conn(conn)
        used = int(state.get("count", 0) or 0) if state.get("period") == period else 0
        active_row = conn.execute(
            "SELECT COUNT(*) AS count, MAX(reserved_at) AS latest FROM qzone_publish_operations WHERE period=? AND status IN ('reserved','unknown')",
            (period,),
        ).fetchone()
        active = int(active_row["count"] or 0)
        latest_reserved = float(active_row["latest"] or 0)
        limit = max(0, int(monthly_limit or 0))
        if not force and used + active >= limit:
            conn.commit()
            return {"ok": False, "status": "quota_blocked", "used": used, "reserved": active, "limit": limit}
        interval_seconds = max(0.0, float(min_interval_hours or 0)) * 3600
        last_effective = max(float(state.get("last_post_at", 0) or 0), latest_reserved)
        if not force and interval_seconds and last_effective and now_ts - last_effective < interval_seconds:
            conn.commit()
            return {"ok": False, "status": "interval_blocked", "next_eligible_at": last_effective + interval_seconds}
        conn.execute(
            """
            INSERT INTO qzone_publish_operations(operation_id, period, kind, status, reserved_at, expires_at, detail)
            VALUES (?, ?, ?, 'reserved', ?, ?, '{}')
            """,
            (op_id, period, str(kind or "post")[:32], now_ts, now_ts + max(30, int(lease_seconds))),
        )
        conn.commit()
    return {"ok": True, "status": "reserved", "operation_id": op_id}


def finalize_qzone_publish(
    *,
    operation_id: str,
    content: str,
    now: Any,
    outcome: str,
    detail: str = "",
) -> dict[str, Any]:
    op_id = str(operation_id or "").strip()[:96]
    normalized = str(outcome or "failed").strip().lower()
    now_ts = _qzone_now_ts(now)
    with connect_sync() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT status, period, kind FROM qzone_publish_operations WHERE operation_id=?",
            (op_id,),
        ).fetchone()
        if row is None:
            conn.rollback()
            raise ValueError("qzone publish operation does not exist")
        if row["status"] == "committed":
            state = _load_qzone_state_from_conn(conn)
            conn.commit()
            return state
        if normalized == "success":
            state = _load_qzone_state_from_conn(conn)
            period = str(row["period"])
            if state.get("period") != period:
                state = {
                    "period": period,
                    "count": 0,
                    "last_post_at": float(state.get("last_post_at", 0) or 0),
                    "last_content": str(state.get("last_content", "") or ""),
                    "recent_contents": list(state.get("recent_contents", [])) if isinstance(state.get("recent_contents"), list) else [],
                }
            state["count"] = int(state.get("count", 0) or 0) + 1
            kind_key = str(row["kind"] or "post").strip().lower()
            if kind_key and kind_key != "post":
                counter_key = f"{kind_key}_count"
                state[counter_key] = int(state.get(counter_key, 0) or 0) + 1
            state["last_post_at"] = now_ts
            _remember_qzone_post(state, content)
            _save_qzone_state_to_conn(conn, state)
            status = "committed"
        else:
            state = _load_qzone_state_from_conn(conn)
            status = "unknown" if normalized == "unknown" else "released"
        conn.execute(
            "UPDATE qzone_publish_operations SET status=?, completed_at=?, detail=? WHERE operation_id=?",
            (status, now_ts, json.dumps({"message": str(detail or "")[:300]}, ensure_ascii=False), op_id),
        )
        conn.commit()
    return state


async def coordinated_qzone_publish(
    *,
    operation_id: str,
    content: str,
    now: Any,
    monthly_limit: int,
    min_interval_hours: float,
    kind: str,
    publish: Callable[[], Any],
    force: bool = False,
) -> dict[str, Any]:
    reservation = await asyncio.to_thread(
        reserve_qzone_publish,
        operation_id=operation_id,
        now=now,
        monthly_limit=monthly_limit,
        min_interval_hours=min_interval_hours,
        kind=kind,
        force=force,
    )
    if reservation.get("duplicate"):
        state = await asyncio.to_thread(get_data_store().load_sync, "qzone_post_state")
        return {**reservation, "success": reservation.get("status") == "committed", "state": state}
    if not reservation.get("ok"):
        return {**reservation, "success": False}
    try:
        result = await publish()
    except (asyncio.TimeoutError, TimeoutError) as exc:
        await asyncio.to_thread(
            finalize_qzone_publish,
            operation_id=operation_id,
            content=content,
            now=now,
            outcome="unknown",
            detail=type(exc).__name__,
        )
        return {"success": False, "status": "outcome_unknown", "message": type(exc).__name__}
    except Exception as exc:
        await asyncio.to_thread(
            finalize_qzone_publish,
            operation_id=operation_id,
            content=content,
            now=now,
            outcome="failed",
            detail=str(exc),
        )
        return {"success": False, "status": "failed", "message": str(exc)}
    success, message = result if isinstance(result, tuple) and len(result) >= 2 else (False, "invalid_publish_result")
    unknown = str(message or "").startswith("outcome_unknown")
    state = await asyncio.to_thread(
        finalize_qzone_publish,
        operation_id=operation_id,
        content=content,
        now=now,
        outcome="success" if success else "unknown" if unknown else "failed",
        detail=str(message or ""),
    )
    return {
        "success": bool(success),
        "status": "committed" if success else "outcome_unknown" if unknown else "failed",
        "message": str(message or ""),
        "state": state,
        "operation_id": operation_id,
    }


async def run_daily_group_fav_report(
    *,
    sign_in_available: bool,
    load_data: Callable[[], Dict[str, Dict[str, Any]]],
    get_now: Callable[[], Any],
    get_bots: Callable[[], Dict[str, Any]],
    superusers: Iterable[str],
    logger: Any,
) -> int:
    """执行每日群好感统计并私聊发送给超级用户。"""
    if not sign_in_available:
        return 0

    try:
        data = load_data()
        today = get_now().strftime("%Y-%m-%d")

        report_lines = []
        total_increase = 0.0

        for user_id, user_data in data.items():
            if not user_id.startswith("group_") or user_id.startswith("group_private_"):
                continue
            if user_data.get("last_update") != today:
                continue

            daily_count = float(user_data.get("daily_fav_count", 0.0))
            if daily_count <= 0:
                continue

            group_id = user_id.replace("group_", "")
            current_fav = float(user_data.get("favorability", 0.0))
            group_name = "未知群聊"

            try:
                bots = get_bots()
                for bot in bots.values():
                    try:
                        group_info = await bot.get_group_info(group_id=int(group_id))
                        group_name = group_info.get("group_name", "未知群聊")
                        break
                    except Exception:
                        continue
            except Exception:
                pass

            report_lines.append(f"群 {group_name}({group_id}): +{daily_count:.2f} (当前: {current_fav:.2f})")
            total_increase += daily_count

        if not report_lines:
            return 0

        summary = (
            f"📊 【每日群聊好感度统计】\n"
            f"日期: {today}\n"
            f"总增长: {total_increase:.2f}\n\n"
            + "\n".join(report_lines)
        )

        bots = get_bots()
        for bot in bots.values():
            for su in superusers:
                try:
                    await bot.send_private_msg(user_id=int(su), message=summary)
                except Exception as e:
                    logger.error(f"发送好感度统计给 {su} 失败: {e}")

        logger.info(f"已发送每日群聊好感度统计，共 {len(report_lines)} 个群聊有变化")
        return len(report_lines)
    except Exception as e:
        logger.error(f"执行每日好感度统计任务出错: {e}")
        return 0


async def run_favorability_maintenance(
    *,
    sign_in_available: bool,
    favorability_service: Any,
    logger: Any,
) -> dict[str, Any]:
    """Run plugin-owned favorability maintenance such as optional decay."""
    if not sign_in_available or favorability_service is None:
        return {"enabled": False, "checked": 0, "decayed": 0, "events": []}
    try:
        result = favorability_service.run_decay_once()
        if result.get("enabled") and int(result.get("decayed", 0) or 0) > 0:
            logger.info(
                "拟人插件：好感度维护完成，"
                f"检查 {int(result.get('checked', 0) or 0)} 个档案，"
                f"衰减 {int(result.get('decayed', 0) or 0)} 个。"
            )
        return result
    except Exception as exc:
        logger.error(f"执行好感度维护任务出错: {exc}")
        return {"enabled": False, "checked": 0, "decayed": 0, "events": [], "error": str(exc)}


async def run_auto_post_diary(
    *,
    qzone_publish_available: bool,
    get_bots: Callable[[], Dict[str, Any]],
    update_qzone_cookie: Callable[..., Any],
    generate_ai_diary: Callable[..., Any],
    publish_qzone_shuo: Callable[..., Any],
    logger: Any,
) -> bool:
    """执行一次自动说说发布。"""
    if not qzone_publish_available:
        logger.warning("拟人插件：当前未启用空间说说发布能力，无法自动发送说说。")
        return False

    bots = get_bots()
    if not bots:
        logger.warning("拟人插件：未找到有效的 Bot 实例，跳过自动说说发布。")
        return False

    bot = list(bots.values())[0]

    logger.info("拟人插件：正在自动更新 Qzone Cookie...")
    try:
        cookie_ok, cookie_msg = await update_qzone_cookie(bot)
    except Exception as e:
        logger.warning(f"拟人插件：Qzone Cookie 更新失败（{e}），将尝试使用旧 Cookie 继续发布。")
    else:
        if cookie_ok:
            logger.info("拟人插件：Qzone Cookie 更新成功。")
        else:
            logger.warning(f"拟人插件：Qzone Cookie 更新失败（{cookie_msg}），将尝试使用旧 Cookie 继续发布。")

    diary_content = await generate_ai_diary(bot)
    if not diary_content:
        return False

    logger.info("拟人插件：正在自动发布空间说说...")
    from ..core.time_ctx import get_configured_now

    published = await coordinated_qzone_publish(
        operation_id=f"auto-diary-{uuid.uuid4().hex}",
        content=diary_content,
        now=get_configured_now(),
        monthly_limit=0,
        min_interval_hours=0,
        kind="post",
        publish=lambda: publish_qzone_shuo(diary_content, bot.self_id),
        force=True,
    )
    if published.get("success"):
        mark_published = getattr(generate_ai_diary, "mark_published", None)
        if callable(mark_published):
            mark_published(diary_content)
        logger.info("拟人插件：空间说说发布成功！")
        return True

    logger.error(f"拟人插件：空间说说发布失败：{published.get('message') or published.get('status')}")
    return False


def _in_qzone_quiet_hour(now_dt: Any, start: int, end: int) -> bool:
    """判断当前小时是否落在 [start, end) 内（支持跨午夜窗口）。
    start==end → 始终返回 False（无静默期）。
    """
    try:
        hour = int(now_dt.hour)
    except Exception:
        return False
    start = max(0, min(23, int(start)))
    end = max(0, min(24, int(end)))
    if start == end:
        return False
    if start < end:
        return start <= hour < end
    # 跨午夜（如 22 → 7）
    return hour >= start or hour < end


async def run_proactive_qzone_post(
    *,
    qzone_publish_available: bool,
    qzone_proactive_enabled: bool,
    qzone_probability: float,
    qzone_monthly_limit: int,
    qzone_min_interval_hours: float,
    get_bots: Callable[[], Dict[str, Any]],
    get_now: Callable[[], Any],
    update_qzone_cookie: Callable[..., Any],
    maybe_generate_qzone_post: Callable[[Any], Any],
    publish_qzone_shuo: Callable[..., Any],
    logger: Any,
    quiet_hour_start: int = 0,
    quiet_hour_end: int = 7,
) -> bool:
    """按内心状态和近期聊天判断，是否主动发一条更日常的空间动态。
    在 [quiet_hour_start, quiet_hour_end) 时间窗口内不触发（避免半夜打扰）。
    """
    if not qzone_publish_available or not qzone_proactive_enabled:
        return False
    bots = get_bots()
    if not bots:
        return False
    bot = list(bots.values())[0]
    now = get_now()
    # 半夜避开：在 quiet_hour 窗口内直接 skip（不消耗 daily quota）
    if _in_qzone_quiet_hour(now, quiet_hour_start, quiet_hour_end):
        logger.debug(
            f"[qzone] skip proactive post in quiet hour {quiet_hour_start}-{quiet_hour_end}"
        )
        return False
    period = now.strftime("%Y-%m")
    now_ts = time.time()

    store = get_data_store()
    state = store.load_sync("qzone_post_state")
    if not isinstance(state, dict):
        state = {}
    # 按月计数：跨月（或旧的按天 state，缺少 period 字段）时 count 归零，
    # last_post_at / recent_contents 照旧 carry over 喂去重。
    if state.get("period") != period:
        state = {
            "period": period,
            "count": 0,
            "last_post_at": float(state.get("last_post_at", 0) or 0),
            "last_content": str(state.get("last_content", "") or ""),
            "recent_contents": list(state.get("recent_contents", []))
            if isinstance(state.get("recent_contents"), list)
            else [],
        }

    if int(state.get("count", 0) or 0) >= max(1, int(qzone_monthly_limit)):
        return False
    min_interval_seconds = max(0.0, float(qzone_min_interval_hours)) * 3600
    last_post_at = float(state.get("last_post_at", 0) or 0)
    if min_interval_seconds and last_post_at and now_ts - last_post_at < min_interval_seconds:
        return False

    try:
        probability = max(0.0, min(1.0, float(qzone_probability)))
    except Exception:
        probability = 0.0
    if probability <= 0.0:
        return False
    if probability < 1.0 and random.random() >= probability:
        logger.debug(f"[qzone] skip proactive post by probability gate p={probability:.2f}")
        return False

    # 把月度额度快照交给 agent，让它自己把控发不发、发的节奏（硬上限仍由上面的 gate 兜底）。
    quota = build_qzone_quota(
        state=state,
        now=now,
        monthly_limit=qzone_monthly_limit,
        min_interval_hours=qzone_min_interval_hours,
    )

    try:
        cookie_ok, cookie_msg = await update_qzone_cookie(bot)
    except Exception as e:
        logger.warning(f"拟人插件：主动说说刷新 Cookie 失败（{e}），尝试使用旧 Cookie。")
    else:
        if not cookie_ok:
            logger.warning(f"拟人插件：主动说说刷新 Cookie 失败（{cookie_msg}），尝试使用旧 Cookie。")

    content = await maybe_generate_qzone_post(bot, quota=quota)
    if not content:
        return False

    published = await coordinated_qzone_publish(
        operation_id=f"proactive-{uuid.uuid4().hex}",
        content=content,
        now=now,
        monthly_limit=qzone_monthly_limit,
        min_interval_hours=qzone_min_interval_hours,
        kind="post",
        publish=lambda: publish_qzone_shuo(content, bot.self_id),
    )
    if not published.get("success"):
        logger.error(f"拟人插件：主动说说发布失败：{published.get('message') or published.get('status')}")
        return False

    mark_published = getattr(maybe_generate_qzone_post, "mark_published", None)
    if callable(mark_published):
        mark_published(content)
    logger.info("拟人插件：已根据当前状态主动发布一条空间说说。")
    return True


async def run_qzone_social_scan(
    *,
    qzone_publish_available: bool,
    qzone_social_enabled: bool,
    get_bots: Callable[[], Dict[str, Any]],
    update_qzone_cookie: Callable[..., Any],
    scan_qzone_social_feeds: Callable[..., Any],
    logger: Any,
    target_user_id: str = "",
    force: bool = False,
) -> dict[str, Any]:
    """Scan friend Qzone feeds and let the LLM decide lightweight interactions."""
    if not qzone_publish_available or (not qzone_social_enabled and not force):
        return {"ok": False, "skipped": True, "last_error": "qzone_social_disabled"}
    bots = get_bots()
    if not bots:
        return {"ok": False, "skipped": True, "last_error": "no_bot"}
    bot = list(bots.values())[0]
    try:
        cookie_ok, cookie_msg = await update_qzone_cookie(bot)
    except Exception as exc:
        logger.warning(f"拟人插件：空间互动刷新 Cookie 失败（{exc}），尝试使用旧 Cookie。")
    else:
        if not cookie_ok:
            logger.warning(f"拟人插件：空间互动刷新 Cookie 失败（{cookie_msg}），尝试使用旧 Cookie。")
    try:
        result = await asyncio.wait_for(
            scan_qzone_social_feeds(
                bot,
                target_user_id=str(target_user_id or ""),
                allow_open_user=bool(force),
            ),
            timeout=_QZONE_SOCIAL_SCAN_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        result = {"ok": False, "status": "timed_out", "last_error": "qzone_social_scan_timed_out"}
    if result.get("skipped"):
        log = logger.warning if int(result.get("running_seconds", 0) or 0) > _QZONE_SOCIAL_SCAN_TIMEOUT_SECONDS else logger.info
        log(
            "拟人插件：空间互动扫描忙碌跳过，"
            f"当前任务 {result.get('busy_by', '')}，已运行 {result.get('running_seconds', 0)} 秒。"
        )
    elif result.get("ok"):
        logger.info(
            "拟人插件：空间互动扫描完成，"
            f"用户 {result.get('scanned_users', 0)}，动态 {result.get('feeds_seen', 0)}，"
            f"点赞 {result.get('liked', 0)}，评论 {result.get('commented', 0)}，"
            f"转发 {result.get('forwarded', 0)}。"
        )
    else:
        logger.warning(f"拟人插件：空间互动扫描跳过或失败：{result.get('last_error')}")
    return result


async def run_qzone_inbound_poll(
    *,
    qzone_publish_available: bool,
    qzone_inbound_enabled: bool,
    get_bots: Callable[[], Dict[str, Any]],
    update_qzone_cookie: Callable[..., Any],
    poll_qzone_inbound_messages: Callable[[Any], Any],
    logger: Any,
    force: bool = False,
) -> dict[str, Any]:
    """Poll comments under the bot's own Qzone feeds for near-realtime replies."""
    if not qzone_publish_available or (not qzone_inbound_enabled and not force):
        return {"ok": False, "skipped": True, "last_error": "qzone_inbound_disabled"}
    bots = get_bots()
    if not bots:
        return {"ok": False, "skipped": True, "last_error": "no_bot"}
    bot = list(bots.values())[0]
    try:
        cookie_ok, cookie_msg = await update_qzone_cookie(bot)
    except Exception as exc:
        logger.warning(f"拟人插件：空间消息轮询刷新 Cookie 失败（{exc}），尝试使用旧 Cookie。")
    else:
        if not cookie_ok:
            logger.warning(f"拟人插件：空间消息轮询刷新 Cookie 失败（{cookie_msg}），尝试使用旧 Cookie。")
    try:
        result = await asyncio.wait_for(
            poll_qzone_inbound_messages(bot),
            timeout=_QZONE_INBOUND_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        result = {"ok": False, "status": "timed_out", "last_error": "qzone_inbound_poll_timed_out"}
    if result.get("skipped"):
        log = logger.warning if int(result.get("running_seconds", 0) or 0) > _QZONE_SOCIAL_SCAN_TIMEOUT_SECONDS else logger.info
        log(
            "拟人插件：空间消息轮询忙碌跳过，"
            f"当前任务 {result.get('busy_by', '')}，已运行 {result.get('running_seconds', 0)} 秒。"
        )
    elif result.get("ok"):
        logger.info(
            "拟人插件：空间消息轮询完成，"
            f"说说 {result.get('feeds_seen', 0)}，留言 {result.get('inbound_comments', 0)}，"
            f"回复 {result.get('replied', 0)}。"
        )
    else:
        logger.warning(f"拟人插件：空间消息轮询跳过或失败：{result.get('last_error')}")
    return result
