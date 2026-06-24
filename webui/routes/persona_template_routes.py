from __future__ import annotations

import asyncio
import json
import re
import time
from datetime import datetime
from typing import Any

import httpx
from fastapi import APIRouter, Body, Depends, HTTPException, Request

from ...core import webui_audit_log
from ..deps import AdminIdentity, get_client_ip, require_admin


_SOURCE_SUMMARY_LIMIT = 1400
_SOURCE_CORPUS_LIMIT = 18000
_RESEARCH_TIMEOUT_SECONDS = 90
_SYNTHESIS_TIMEOUT_SECONDS = 120


def _clip_text(value: Any, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "..."


def _runtime_bundle(runtime: Any) -> Any | None:
    return getattr(runtime, "runtime_bundle", None)


def _main_ai_caller(runtime: Any) -> Any | None:
    bundle = _runtime_bundle(runtime)
    if bundle is None:
        return None
    caller = getattr(bundle, "call_ai_api", None)
    if callable(caller):
        return caller
    deps = getattr(bundle, "reply_processor_deps", None)
    runtime_inner = getattr(deps, "runtime", None) if deps is not None else None
    caller = getattr(runtime_inner, "call_ai_api", None) if runtime_inner is not None else None
    return caller if callable(caller) else None


async def _call_main_model(
    caller: Any,
    messages: list[dict[str, Any]],
    *,
    temperature: float = 0.2,
    use_builtin_search: bool = False,
    timeout: int = _RESEARCH_TIMEOUT_SECONDS,
) -> str:
    async def _invoke() -> Any:
        try:
            return await caller(
                messages,
                temperature=temperature,
                use_builtin_search=use_builtin_search,
            )
        except TypeError:
            try:
                return await caller(messages, None, None, temperature)
            except TypeError:
                return await caller(messages)

    raw = await asyncio.wait_for(_invoke(), timeout=timeout)
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw.strip()
    content = getattr(raw, "content", None)
    if content is not None:
        return str(content or "").strip()
    return str(raw or "").strip()


def _source_key(source: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(source.get("source", "") or ""),
        str(source.get("title", "") or ""),
        str(source.get("url", "") or ""),
    )


async def _gather_wiki_sources(
    *,
    work_title: str,
    character_name: str,
    plugin_config: Any,
    logger: Any,
) -> list[dict[str, Any]]:
    try:
        from ...skills.skillpacks.wiki_search.scripts.impl import wiki_lookup_candidates
        from ...skills.skillpacks.wiki_search.scripts.main import resolve_wiki_runtime_config
    except Exception:
        return []

    wiki_enabled, _fandom_enabled, extra_fandom_wikis = resolve_wiki_runtime_config(plugin_config)
    if not wiki_enabled:
        return []
    queries = [
        f"{work_title} {character_name}",
        f"{character_name} {work_title} 角色",
        f"{work_title} {character_name} 人物设定 口癖 关系",
    ]
    sources: list[dict[str, Any]] = []
    async with httpx.AsyncClient(follow_redirects=True) as client:
        for query in queries:
            try:
                payload = await asyncio.wait_for(
                    wiki_lookup_candidates(
                        query,
                        extra_fandom_wikis=extra_fandom_wikis,
                        http_client=client,
                        logger=logger,
                    ),
                    timeout=18,
                )
            except Exception:
                continue
            for item in payload.get("top_candidates", []) or []:
                if not isinstance(item, dict):
                    continue
                sources.append(
                    {
                        "kind": "wiki",
                        "query": query,
                        "source": str(item.get("source", "") or "Wiki"),
                        "title": str(item.get("title", "") or ""),
                        "url": str(item.get("url", "") or ""),
                        "summary": _clip_text(item.get("summary", ""), _SOURCE_SUMMARY_LIMIT),
                        "confidence": item.get("confidence", 0),
                    }
                )
    return sources


async def _gather_web_sources(
    *,
    work_title: str,
    character_name: str,
    logger: Any,
) -> list[dict[str, Any]]:
    try:
        from ...core.web_grounding import do_web_search
    except Exception:
        return []

    queries = [
        f"{work_title} {character_name} 官方 角色介绍",
        f"{work_title} {character_name} 设定 立绘 台词 萌点",
    ]
    sources: list[dict[str, Any]] = []
    for query in queries:
        try:
            result = await asyncio.wait_for(
                do_web_search(
                    query,
                    context_hint="为 WebUI 自动构建角色人设模板，优先官方资料、百科、角色资料页和设定集信息。",
                    get_now=lambda: datetime.now(),
                    logger=logger,
                ),
                timeout=24,
            )
        except Exception as exc:
            sources.append(
                {
                    "kind": "web_search_error",
                    "query": query,
                    "source": "联网搜索",
                    "title": query,
                    "url": "",
                    "summary": f"联网搜索失败：{_clip_text(exc, 300)}",
                    "confidence": 0,
                }
            )
            continue
        text = _clip_text(result, _SOURCE_SUMMARY_LIMIT)
        if text:
            sources.append(
                {
                    "kind": "web_search",
                    "query": query,
                    "source": "联网搜索",
                    "title": query,
                    "url": "",
                    "summary": text,
                    "confidence": 0.5,
                }
            )
    return sources


async def _gather_persona_sources(
    *,
    runtime: Any,
    work_title: str,
    character_name: str,
) -> list[dict[str, Any]]:
    plugin_config = getattr(runtime, "plugin_config", None)
    logger = getattr(runtime, "logger", None)
    wiki_sources, web_sources = await asyncio.gather(
        _gather_wiki_sources(
            work_title=work_title,
            character_name=character_name,
            plugin_config=plugin_config,
            logger=logger,
        ),
        _gather_web_sources(
            work_title=work_title,
            character_name=character_name,
            logger=logger,
        ),
    )
    seen: set[tuple[str, str, str]] = set()
    merged: list[dict[str, Any]] = []
    for source in [*wiki_sources, *web_sources]:
        key = _source_key(source)
        if key in seen:
            continue
        seen.add(key)
        merged.append(source)
    return merged[:12]


def _source_corpus(sources: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for index, source in enumerate(sources, start=1):
        title = source.get("title") or source.get("query") or f"资料 {index}"
        lines.append(
            "\n".join(
                [
                    f"[S{index}] 来源：{source.get('source') or '未知来源'}",
                    f"标题：{title}",
                    f"链接：{source.get('url') or '无直接链接'}",
                    f"查询：{source.get('query') or ''}",
                    f"摘要：{source.get('summary') or ''}",
                ]
            )
        )
    corpus = "\n\n".join(lines)
    return corpus[:_SOURCE_CORPUS_LIMIT]


def _extract_json_object(text: str) -> dict[str, Any]:
    raw = str(text or "").strip()
    if not raw:
        return {}
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, flags=re.S)
    if fenced:
        raw = fenced.group(1)
    else:
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            raw = raw[start : end + 1]
    try:
        parsed = json.loads(raw)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


async def _run_subagent(
    *,
    caller: Any,
    agent_name: str,
    focus: str,
    work_title: str,
    character_name: str,
    source_text: str,
) -> dict[str, Any]:
    system = (
        "你是拟人插件 WebUI 的只读资料研究子agent。"
        "只能基于给出的资料和你能通过主模型内置搜索查到的可靠信息做交叉验证。"
        "不要编造设定；不确定就写入 conflicts 或 unknowns。"
        "输出 JSON，不要输出寒暄。"
    )
    user = f"""
任务：为《{work_title}》的角色「{character_name}」构建人设模板前置研究。
你的研究重点：{focus}

资料：
{source_text or "（资料抓取为空，可以使用主模型内置搜索补充，但必须标注不确定性。）"}

请输出 JSON：
{{
  "agent": "{agent_name}",
  "focus": "{focus}",
  "facts": ["可验证事实，含来源编号如 S1"],
  "personality": ["性格/行为模式"],
  "visual_references": ["立绘、服装、外观、表情、代表性物件"],
  "relations": ["角色关系"],
  "catchphrases": ["口癖/常用表达"],
  "moe_points": ["萌点/记忆点"],
  "story_setting": ["剧情定位/背景"],
  "conflicts": ["互相冲突或来源不足之处"],
  "unknowns": ["仍缺资料的点"]
}}
""".strip()
    text = await _call_main_model(
        caller,
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=0.1,
        use_builtin_search=True,
        timeout=_RESEARCH_TIMEOUT_SECONDS,
    )
    parsed = _extract_json_object(text)
    return {
        "name": agent_name,
        "focus": focus,
        "report": parsed if parsed else {"raw": text},
        "raw": text,
    }


async def _synthesize_template(
    *,
    caller: Any,
    work_title: str,
    character_name: str,
    source_text: str,
    subagents: list[dict[str, Any]],
) -> str:
    reports = json.dumps(
        [
            {
                "name": item.get("name"),
                "focus": item.get("focus"),
                "report": item.get("report"),
            }
            for item in subagents
        ],
        ensure_ascii=False,
        indent=2,
    )
    system = (
        "你是 NoneBot 拟人插件的人设模板构建器，必须使用主模型做最终合成。"
        "目标是生成可直接放入插件 system prompt/YAML 人设中的中文模板。"
        "保留角色核心，不要把 bot 写成客服或助手；口吻应像群友。"
        "不要用关键词规则或触发词设计对话语义。"
    )
    user = f"""
请基于资料和三个子agent交叉验证报告，为《{work_title}》的「{character_name}」生成插件内可用的人设模板。

硬性要求：
- 输出中文。
- 包含基础身份、性格、说话方式、口癖、视觉/立绘参考、角色关系、萌点、剧情定位、禁忌与不确定项。
- 模板要适合作为“白咲真寻机”这类群聊拟人 bot 的角色底座：自然、有边界、有群友感。
- 不要写“我是 AI/助手/客服”。
- 对证据不足的设定标注“待确认”，不要编造成事实。
- 最后附“资料冲突与缺口”小节。

资料：
{source_text or "（资料抓取为空）"}

三个子agent报告：
{reports}

请直接输出模板正文，不要输出 JSON。
""".strip()
    return await _call_main_model(
        caller,
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=0.25,
        use_builtin_search=False,
        timeout=_SYNTHESIS_TIMEOUT_SECONDS,
    )


def build_persona_template_router(*, runtime) -> APIRouter:
    router = APIRouter(prefix="/api/persona-template", tags=["persona-template"])

    @router.post("/build")
    async def build_template(
        request: Request,
        body: dict = Body(default_factory=dict),
        admin: AdminIdentity = Depends(require_admin),
    ) -> dict:
        work_title = str(body.get("work_title", "") or "").strip()
        character_name = str(body.get("character_name", "") or "").strip()
        if not work_title or not character_name:
            raise HTTPException(status_code=400, detail="作品名和角色名都不能为空")
        if len(work_title) > 120 or len(character_name) > 80:
            raise HTTPException(status_code=400, detail="作品名或角色名过长")
        caller = _main_ai_caller(runtime)
        if caller is None:
            raise HTTPException(status_code=503, detail="主模型调用器未就绪")

        started = time.time()
        sources = await _gather_persona_sources(
            runtime=runtime,
            work_title=work_title,
            character_name=character_name,
        )
        source_text = _source_corpus(sources)
        focus_items = [
            ("基础设定子agent", "官方身份、百科摘要、基础人设、剧情定位、资料可信度"),
            ("性格台词子agent", "性格模式、口癖、说话节奏、萌点、群聊可迁移表达"),
            ("关系视觉子agent", "角色关系、立绘/服装/视觉锚点、冲突资料与缺口"),
        ]
        subagents = await asyncio.gather(
            *[
                _run_subagent(
                    caller=caller,
                    agent_name=name,
                    focus=focus,
                    work_title=work_title,
                    character_name=character_name,
                    source_text=source_text,
                )
                for name, focus in focus_items
            ]
        )
        template = await _synthesize_template(
            caller=caller,
            work_title=work_title,
            character_name=character_name,
            source_text=source_text,
            subagents=list(subagents),
        )
        conflicts: list[str] = []
        for item in subagents:
            report = item.get("report") if isinstance(item, dict) else {}
            raw_conflicts = report.get("conflicts", []) if isinstance(report, dict) else []
            if isinstance(raw_conflicts, list):
                conflicts.extend(str(x) for x in raw_conflicts if str(x).strip())
        webui_audit_log.record(
            action="persona_template_build",
            qq=admin.qq,
            device_id=admin.device_id,
            target=f"{work_title}/{character_name}",
            ip_hash=get_client_ip(request),
            detail={"source_count": len(sources), "subagent_count": len(subagents)},
            outcome="ok",
        )
        return {
            "success": True,
            "work_title": work_title,
            "character_name": character_name,
            "model_role": "configured_main",
            "duration_ms": int((time.time() - started) * 1000),
            "sources": sources,
            "subagents": list(subagents),
            "conflicts": conflicts[:20],
            "template": template,
        }

    return router
