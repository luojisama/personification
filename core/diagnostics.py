"""WebUI 功能体检：聚合各模块的配置/就绪状态，供图形化诊断页使用。

每个检查返回统一结构：key/label/status/detail/hint。
status 取值：
- ok       绿：配置正常、功能就绪
- warn     黄：能用但有影响行为的配置（如没有任何已启用的群 → 群里不说话）
- error    红：配置错误会导致功能不可用（如启用了 TTS 但缺 API Key）
- disabled 灰：功能被主动关闭
- info     中性信息

所有检查均 never-raise；单个检查异常会被收敛为 error 项，不影响整体。
"""

from __future__ import annotations

import time
from typing import Any

_OK = "ok"
_WARN = "warn"
_ERROR = "error"
_DISABLED = "disabled"
_INFO = "info"

_SEVERITY = {_ERROR: 3, _WARN: 2, _OK: 1, _INFO: 0, _DISABLED: 0}


def _check(key: str, label: str, status: str, detail: str = "", hint: str = "") -> dict[str, Any]:
    return {"key": key, "label": label, "status": status, "detail": detail, "hint": hint}


def _get(cfg: Any, name: str, default: Any = None) -> Any:
    return getattr(cfg, name, default)


def _truthy_str(cfg: Any, name: str) -> bool:
    return bool(str(_get(cfg, name, "") or "").strip())


def _safe(fn) -> dict[str, Any]:
    """执行单个检查闭包，异常收敛为 error 项。"""
    try:
        return fn()
    except Exception as exc:  # pragma: no cover - 防御
        return _check("internal_error", "检查异常", _ERROR, detail=str(exc)[:200])


# ──────────────────────── 各类检查 ────────────────────────

def _core_checks(cfg: Any, superusers: set[str]) -> list[dict[str, Any]]:
    checks = []
    enabled = bool(_get(cfg, "personification_global_enabled", True))
    checks.append(_check(
        "global_enabled", "插件总开关", _OK if enabled else _WARN,
        detail="已开启" if enabled else "已关闭：插件不会响应任何消息",
        hint="" if enabled else "在「配置中心 → 核心开关」开启 personification_global_enabled",
    ))
    n = len([s for s in (superusers or set()) if str(s).strip()])
    checks.append(_check(
        "superusers", "管理员 (SUPERUSERS)", _OK if n else _ERROR,
        detail=f"已配置 {n} 个管理员" if n else "未配置：无人能登录 WebUI / 收验证码",
        hint="" if n else "在 .env.prod 配置 SUPERUSERS=[\"你的QQ\"]",
    ))
    return checks


def _model_checks(cfg: Any, bundle: Any, logger: Any) -> list[dict[str, Any]]:
    checks = []
    from .ai_routes import list_primary_providers

    providers = list_primary_providers(cfg, logger) or []
    if providers:
        names = ", ".join(
            str(p.get("name") or f"{p.get('api_type')}:{p.get('model')}") for p in providers[:6]
        )
        checks.append(_check(
            "api_pools", "模型 Provider 池", _OK,
            detail=f"已配置 {len(providers)} 个 provider：{names}",
            hint="可在「模型测试 → 测试全部 provider」验证各家连通性",
        ))
    else:
        checks.append(_check(
            "api_pools", "模型 Provider 池", _ERROR,
            detail="未配置任何 provider，主回复模型不可用",
            hint="在「配置中心 → 模型路由」的 API Provider 池添加至少一个 provider",
        ))
    # 主回复 tool_caller 是否就绪
    caller = None
    if bundle is not None:
        deps = getattr(bundle, "reply_processor_deps", None)
        inner = getattr(deps, "runtime", None) if deps is not None else None
        caller = getattr(inner, "agent_tool_caller", None) if inner is not None else None
    checks.append(_check(
        "tool_caller", "Agent 调用链路", _OK if caller is not None else _WARN,
        detail="就绪" if caller is not None else "未就绪（Agent 流水线可能未启用）",
    ))
    # 全局兜底
    if bool(_get(cfg, "personification_fallback_enabled", True)) and _truthy_str(cfg, "personification_fallback_model"):
        checks.append(_check("fallback", "全局兜底模型", _OK, detail=str(_get(cfg, "personification_fallback_model", ""))))
    else:
        checks.append(_check("fallback", "全局兜底模型", _INFO, detail="未配置（provider 池全挂时无救援）"))
    return checks


def _db_checks(cfg: Any) -> list[dict[str, Any]]:
    checks = []
    try:
        from .db import connect_sync

        with connect_sync() as conn:
            conn.execute("SELECT 1").fetchone()
        checks.append(_check("db", "主数据库", _OK, detail="连接正常"))
    except Exception as exc:
        checks.append(_check("db", "主数据库", _ERROR, detail=f"连接失败：{exc}"[:200]))
    return checks


def _memory_checks(cfg: Any, bundle: Any) -> list[dict[str, Any]]:
    checks = []
    enabled = bool(_get(cfg, "personification_memory_enabled", True))
    if not enabled:
        checks.append(_check("memory", "记忆系统", _DISABLED, detail="已关闭"))
        return checks
    store = getattr(bundle, "memory_store", None) if bundle is not None else None
    checks.append(_check(
        "memory", "记忆系统", _OK if store is not None else _WARN,
        detail="已启用且就绪" if store is not None else "已启用但 memory_store 未就绪",
    ))
    if bool(_get(cfg, "personification_real_embedding_enabled", False)):
        provider = str(_get(cfg, "personification_embedding_provider", "") or "")
        checks.append(_check("embedding", "向量检索", _OK, detail=f"已启用，provider={provider or '默认'}"))
    else:
        checks.append(_check("embedding", "向量检索", _INFO, detail="使用本地 hash_bow（未启用真实 embedding）"))
    return checks


def _persona_checks(cfg: Any, bundle: Any) -> list[dict[str, Any]]:
    checks = []
    if not bool(_get(cfg, "personification_persona_enabled", True)):
        checks.append(_check("persona", "用户画像", _DISABLED, detail="已关闭"))
        return checks
    store = getattr(bundle, "persona_store", None) if bundle is not None else None
    checks.append(_check(
        "persona", "用户画像", _OK if store is not None else _WARN,
        detail="已启用且就绪" if store is not None else "已启用但 persona_store 未就绪",
    ))
    return checks


def _group_checks(cfg: Any) -> list[dict[str, Any]]:
    checks = []
    from ..utils import is_group_whitelisted, load_group_configs, load_whitelist

    config_wl = [str(g) for g in (_get(cfg, "personification_whitelist", []) or [])]
    dynamic_wl = [str(g) for g in (load_whitelist() or [])]
    gconfigs = load_group_configs() or {}
    if not isinstance(gconfigs, dict):
        gconfigs = {}
    all_ids = set(config_wl) | set(dynamic_wl) | {str(k) for k in gconfigs}
    enabled = [g for g in all_ids if is_group_whitelisted(g, config_wl)]
    if enabled:
        checks.append(_check(
            "group_whitelist", "群聊白名单", _OK,
            detail=f"{len(enabled)} 个群已启用拟人回复",
        ))
    else:
        checks.append(_check(
            "group_whitelist", "群聊白名单", _WARN,
            detail="没有任何已启用的群：bot 在所有群里都不会主动说话",
            hint="在「群开关」页打开目标群，或配置 personification_whitelist",
        ))
    prob = float(_get(cfg, "personification_probability", 0.0) or 0.0)
    checks.append(_check(
        "probability", "随机插话概率", _OK if prob > 0 else _WARN,
        detail=f"probability={prob}",
        hint="" if prob > 0 else "概率为 0：除被 @/点名外，群里几乎不会主动接话",
    ))
    return checks


def _sticker_checks(cfg: Any) -> list[dict[str, Any]]:
    checks = []
    try:
        from .sticker_cache import get_sticker_files

        files = get_sticker_files(_get(cfg, "personification_sticker_path", None)) or []
        n = len(files)
        checks.append(_check(
            "stickers", "表情包库", _OK if n else _WARN,
            detail=f"{n} 张表情可用" if n else "目录为空或不存在",
            hint="" if n else "在「表情包」页上传，或检查 personification_sticker_path",
        ))
    except Exception as exc:
        checks.append(_check("stickers", "表情包库", _ERROR, detail=str(exc)[:160]))
    if bool(_get(cfg, "personification_labeler_enabled", True)):
        has_key = _truthy_str(cfg, "personification_labeler_api_key")
        checks.append(_check(
            "labeler", "表情视觉打标", _OK if has_key else _INFO,
            detail="独立打标模型已配置" if has_key else "未配独立打标模型，回退主模型",
        ))
    return checks


def _tts_checks(cfg: Any) -> list[dict[str, Any]]:
    if not bool(_get(cfg, "personification_tts_enabled", False)):
        return [_check("tts", "TTS 语音", _DISABLED, detail="已关闭")]
    has_key = _truthy_str(cfg, "personification_tts_api_key")
    has_url = _truthy_str(cfg, "personification_tts_api_url")
    if has_key and has_url:
        return [_check("tts", "TTS 语音", _OK, detail="已启用，API 已配置")]
    missing = "、".join(x for x, ok in (("api_key", has_key), ("api_url", has_url)) if not ok)
    return [_check("tts", "TTS 语音", _ERROR, detail=f"已启用但缺少 {missing}", hint="在「配置中心 → TTS 语音」补全")]


def _qzone_checks(cfg: Any) -> list[dict[str, Any]]:
    if not bool(_get(cfg, "personification_qzone_enabled", False)):
        return [_check("qzone", "QQ 空间", _DISABLED, detail="已关闭")]
    has_cookie = _truthy_str(cfg, "personification_qzone_cookie")
    if has_cookie:
        return [_check("qzone", "QQ 空间", _OK, detail="已启用，Cookie 已配置")]
    return [_check("qzone", "QQ 空间", _ERROR, detail="已启用但缺少 Cookie（说说/点赞会静默失败）",
                   hint="在「配置中心 → QQ 空间」填入 personification_qzone_cookie")]


def _search_checks(cfg: Any) -> list[dict[str, Any]]:
    tool = bool(_get(cfg, "personification_tool_web_search_enabled", True))
    builtin = bool(_get(cfg, "personification_model_builtin_search_enabled", True))
    status = _OK if (tool or builtin) else _DISABLED
    detail = "、".join(x for x, ok in (("外部搜索", tool), ("模型内置搜索", builtin)) if ok) or "均关闭"
    return [_check("web_search", "联网搜索", status, detail=detail)]


def _skill_checks(cfg: Any, bundle: Any) -> list[dict[str, Any]]:
    checks = []
    registry = getattr(bundle, "tool_registry", None) if bundle is not None else None
    if registry is not None:
        try:
            n = len(list(registry.all()))
        except Exception:
            n = 0
        checks.append(_check("tools", "已加载工具", _OK if n else _WARN, detail=f"{n} 个工具就绪"))
    else:
        checks.append(_check("tools", "已加载工具", _WARN, detail="工具注册表未就绪"))
    if bool(_get(cfg, "personification_use_skillpacks", False)):
        checks.append(_check("skillpacks", "Skillpacks", _OK, detail="已启用标准 skillpack 加载"))
    return checks


def _social_checks(cfg: Any) -> list[dict[str, Any]]:
    checks = []
    si = bool(_get(cfg, "personification_social_intelligence_enabled", False))
    checks.append(_check("social", "主动社交框架", _OK if si else _DISABLED,
                         detail="已启用" if si else "已关闭"))
    pa = bool(_get(cfg, "personification_proactive_enabled", True))
    checks.append(_check("proactive", "主动私聊", _OK if pa else _DISABLED,
                         detail="已启用" if pa else "已关闭"))
    return checks


def _persona_prompt_checks(cfg: Any) -> list[dict[str, Any]]:
    try:
        from .prompt_loader import _resolve_candidate_path

        path = str(_get(cfg, "personification_prompt_path", "") or _get(cfg, "personification_system_path", "") or "").strip().strip('"').strip("'")
        if path:
            try:
                p = _resolve_candidate_path(path)
                ok = p.is_file()
            except OSError:
                ok = False
            if ok:
                return [_check("persona_prompt", "人设文件", _OK, detail=f"已加载：{path}")]
            return [_check("persona_prompt", "人设文件", _ERROR, detail=f"配置的路径不存在：{path}",
                           hint="检查 prompt_path / system_path，或改用内联 system_prompt")]
        sp = str(_get(cfg, "personification_system_prompt", "") or "").strip()
        if sp:
            return [_check("persona_prompt", "人设设定", _OK, detail=f"使用内联 system_prompt（{len(sp)} 字）")]
        return [_check("persona_prompt", "人设设定", _WARN, detail="未配置人设，使用内置默认提示词")]
    except Exception as exc:
        return [_check("persona_prompt", "人设设定", _ERROR, detail=str(exc)[:160])]


async def _protocol_checks(get_bots: Any, cfg: Any, logger: Any) -> list[dict[str, Any]]:
    mode = str(_get(cfg, "personification_protocol_extensions", "auto") or "auto").strip().lower()
    if mode == "none":
        return [_check("protocol", "协议端扩展", _DISABLED, detail="已禁用全部扩展 API（贴表情/拍一拍/输入状态）")]
    try:
        bots = get_bots() if callable(get_bots) else {}
        bot = next(iter(bots.values())) if bots else None
        if bot is None:
            return [_check("protocol", "协议端", _WARN, detail="Bot 未连接，无法识别协议端")]
        from .protocol_capabilities import detect_flavor

        flavor = await detect_flavor(bot, logger)
        if flavor == "gocq":
            return [_check("protocol", "协议端", _WARN, detail="go-cqhttp：不支持贴表情/拍一拍等扩展能力")]
        return [_check("protocol", "协议端", _OK, detail=f"已识别：{flavor}")]
    except Exception as exc:
        return [_check("protocol", "协议端", _WARN, detail=str(exc)[:160])]


def _webui_checks(cfg: Any) -> list[dict[str, Any]]:
    approval = bool(_get(cfg, "personification_webui_require_device_approval", True))
    return [_check(
        "device_approval", "新设备审批", _OK if approval else _WARN,
        detail="已开启：新设备需管理员确认" if approval else "已关闭：任意拿到验证码的设备直接放行",
    )]


async def run_diagnostics(
    *,
    plugin_config: Any,
    bundle: Any = None,
    superusers: set[str] | None = None,
    get_bots: Any = None,
    logger: Any = None,
) -> dict[str, Any]:
    cfg = plugin_config
    su = superusers or set()
    categories: list[dict[str, Any]] = [
        {"name": "核心", "checks": [_safe(lambda: c) for c in _core_checks(cfg, su)]},
        {"name": "模型路由", "checks": _safe_list(lambda: _model_checks(cfg, bundle, logger))},
        {"name": "存储", "checks": _safe_list(lambda: _db_checks(cfg))},
        {"name": "记忆", "checks": _safe_list(lambda: _memory_checks(cfg, bundle))},
        {"name": "用户画像", "checks": _safe_list(lambda: _persona_checks(cfg, bundle))},
        {"name": "群聊", "checks": _safe_list(lambda: _group_checks(cfg))},
        {"name": "表情包", "checks": _safe_list(lambda: _sticker_checks(cfg))},
        {"name": "TTS 语音", "checks": _safe_list(lambda: _tts_checks(cfg))},
        {"name": "QQ 空间", "checks": _safe_list(lambda: _qzone_checks(cfg))},
        {"name": "联网搜索", "checks": _safe_list(lambda: _search_checks(cfg))},
        {"name": "Skill 扩展", "checks": _safe_list(lambda: _skill_checks(cfg, bundle))},
        {"name": "主动社交", "checks": _safe_list(lambda: _social_checks(cfg))},
        {"name": "人设", "checks": _safe_list(lambda: _persona_prompt_checks(cfg))},
        {"name": "协议端", "checks": await _protocol_checks(get_bots, cfg, logger)},
        {"name": "WebUI 安全", "checks": _safe_list(lambda: _webui_checks(cfg))},
    ]

    summary = {_OK: 0, _WARN: 0, _ERROR: 0, _DISABLED: 0, _INFO: 0}
    for cat in categories:
        for chk in cat["checks"]:
            summary[chk.get("status", _INFO)] = summary.get(chk.get("status", _INFO), 0) + 1
    overall = _ERROR if summary[_ERROR] else (_WARN if summary[_WARN] else _OK)
    return {
        "generated_at": time.time(),
        "overall": overall,
        "summary": summary,
        "categories": categories,
    }


def _safe_list(fn) -> list[dict[str, Any]]:
    try:
        return list(fn())
    except Exception as exc:  # pragma: no cover
        return [_check("internal_error", "检查异常", _ERROR, detail=str(exc)[:200])]


__all__ = ["run_diagnostics"]
