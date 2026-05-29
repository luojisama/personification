from __future__ import annotations

import json
from typing import Any, Optional

import httpx

# 复用现有基础设施，避免重复造轮子：
# - resource_collector：免 key 多搜索引擎抓取 + 工具函数（_strip_tags / search_web）
# - wiki_search：维基百科 / 萌娘百科 / Fandom 条目查询
# 采用绝对导入（与 parallel_research 一致的可靠写法）；builtin skillpack 经合成包名加载，
# 3 级相对导入不可靠，故用 plugin.personification.* 绝对路径。
from plugin.personification.skills.skillpacks.resource_collector.scripts import impl as rc_impl
from plugin.personification.skills.skillpacks.wiki_search.scripts import impl as wiki_impl


_STEAM_STORE_SEARCH = "https://store.steampowered.com/api/storesearch/"
_STEAM_NEWS_API = "https://api.steampowered.com/ISteamNews/GetNewsForApp/v2/"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

# 各 aspect 拼进社区搜索的关键词。
_ASPECT_SEARCH_KEYWORDS = {
    "update": "更新公告 新版本 版本更新 patch notes",
    "guide": "攻略 图文攻略 流程 通关",
    "story": "剧情 设定 世界观 结局解析",
    "tips": "技巧 进阶技巧 小技巧 注意事项 tips",
}

_ASPECT_LABEL = {
    "update": "更新/补丁公告",
    "guide": "攻略",
    "story": "剧情/设定",
    "tips": "技巧",
}

# 攻略/技巧定向搜索时优先关注的社区站点（用于排序加权，不做硬过滤）。
_DEFAULT_COMMUNITY_SITES = [
    "gamersky.com",
    "3dmgame.com",
    "bbs.mihoyo.com",
    "miyoushe.com",
    "bilibili.com",
    "tieba.baidu.com",
    "reddit.com",
    "gamefaqs.gamespot.com",
    "ign.com",
    "fextralife.com",
]


def _resolve_logger(logger: Any) -> Any:
    return rc_impl._get_logger(logger)


def _normalize_aspect(aspect: str) -> str:
    value = str(aspect or "").strip().lower()
    if value in _ASPECT_SEARCH_KEYWORDS:
        return value
    # 一些常见同义词兜底
    alias = {
        "patch": "update",
        "patchnotes": "update",
        "news": "update",
        "walkthrough": "guide",
        "guides": "guide",
        "plot": "story",
        "lore": "story",
        "tip": "tips",
        "trick": "tips",
        "tricks": "tips",
    }
    return alias.get(value, "guide")


def _community_sites(plugin_config: Any) -> list[str]:
    raw = getattr(plugin_config, "personification_game_info_community_sites", None)
    sites: list[str] = []
    if isinstance(raw, (list, tuple)):
        sites = [str(s).strip() for s in raw if str(s).strip()]
    elif isinstance(raw, str) and raw.strip():
        sites = [s.strip() for s in raw.replace("，", ",").split(",") if s.strip()]
    return sites or list(_DEFAULT_COMMUNITY_SITES)


def _timeout(plugin_config: Any) -> float:
    try:
        return float(getattr(plugin_config, "personification_game_info_timeout", 15.0) or 15.0)
    except (TypeError, ValueError):
        return 15.0


async def _resolve_steam_appid(
    name: str,
    *,
    http_client: httpx.AsyncClient,
    logger: Any,
    timeout: float,
) -> tuple[Optional[int], str]:
    """用 Steam store 搜索把游戏名解析为 appid（免 key）。找不到返回 (None, "")。"""
    term = str(name or "").strip()
    if not term:
        return None, ""
    try:
        resp = await http_client.get(
            _STEAM_STORE_SEARCH,
            params={"term": term, "cc": "us", "l": "schinese"},
            headers=_HEADERS,
            timeout=timeout,
        )
        data = resp.json()
    except Exception as exc:
        logger.debug(f"[game_info] steam storesearch failed: {exc}")
        return None, ""
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list) or not items:
        return None, ""
    first = items[0]
    if not isinstance(first, dict):
        return None, ""
    appid = first.get("id")
    try:
        appid_int = int(appid)
    except (TypeError, ValueError):
        return None, ""
    return appid_int, str(first.get("name", term) or term)


async def _fetch_steam_news(
    appid: int,
    *,
    http_client: httpx.AsyncClient,
    logger: Any,
    timeout: float,
    count: int = 5,
) -> list[dict[str, Any]]:
    try:
        resp = await http_client.get(
            _STEAM_NEWS_API,
            params={"appid": appid, "count": count, "maxlength": 400, "l": "schinese"},
            headers=_HEADERS,
            timeout=timeout,
        )
        data = resp.json()
    except Exception as exc:
        logger.debug(f"[game_info] steam news failed appid={appid}: {exc}")
        return []
    appnews = data.get("appnews") if isinstance(data, dict) else None
    items = appnews.get("newsitems") if isinstance(appnews, dict) else None
    return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []


def _format_steam_updates(game: str, matched_name: str, news: list[dict[str, Any]]) -> str:
    lines = [f"【{game} 的更新/公告 · Steam 官方】(匹配条目：{matched_name})"]
    for idx, item in enumerate(news[:5], 1):
        title = str(item.get("title", "") or "").strip()
        url = str(item.get("url", "") or "").strip()
        contents = rc_impl._strip_tags(str(item.get("contents", "") or "")).strip()
        feed = str(item.get("feedlabel", "") or "").strip()
        if not title:
            continue
        head = f"{idx}. {title}"
        if feed:
            head += f"（{feed}）"
        lines.append(head)
        if contents:
            lines.append(f"   {contents[:160]}")
        if url:
            lines.append(f"   链接：{url}")
    return "\n".join(lines) if len(lines) > 1 else ""


async def _community_search(
    query: str,
    *,
    http_client: httpx.AsyncClient,
    logger: Any,
    community_sites: list[str],
    limit: int = 6,
) -> list[dict[str, Any]]:
    """复用 resource_collector 的免 key 搜索，按社区站点做软性加权排序。"""
    try:
        raw = await rc_impl.search_web(query, limit, http_client=http_client, logger=logger)
        payload = json.loads(raw)
    except Exception as exc:
        logger.debug(f"[game_info] community search failed: {exc}")
        return []
    results = payload.get("results", []) if isinstance(payload, dict) else []
    if not isinstance(results, list):
        return []

    def _site_rank(item: dict[str, Any]) -> int:
        url = str(item.get("url", "") or "").lower()
        for i, site in enumerate(community_sites):
            if site in url:
                return i
        return len(community_sites) + 1

    results = sorted(
        [r for r in results if isinstance(r, dict)],
        key=_site_rank,
    )
    return results[:limit]


def _format_community(label: str, game: str, results: list[dict[str, Any]]) -> str:
    if not results:
        return ""
    lines = [f"【{game} 的{label} · 社区搜索】"]
    for idx, item in enumerate(results[:6], 1):
        title = str(item.get("title", "") or "").strip()
        url = str(item.get("url", "") or "").strip()
        snippet = str(item.get("snippet", "") or "").strip()
        source = str(item.get("source", "") or "").strip()
        if not title and not url:
            continue
        head = f"{idx}. {title or url}"
        if source:
            head += f" [{source}]"
        lines.append(head)
        if snippet:
            lines.append(f"   {snippet[:160]}")
        if url:
            lines.append(f"   链接：{url}")
    return "\n".join(lines) if len(lines) > 1 else ""


async def _format_story(
    game: str,
    query: str,
    *,
    http_client: httpx.AsyncClient,
    logger: Any,
) -> str:
    """剧情/设定优先走 wiki_lookup（Fandom/萌娘/维基）。"""
    wiki_query = f"{game} {query}".strip() if query else f"{game} 剧情 设定"
    try:
        raw = await wiki_impl.wiki_lookup(wiki_query, http_client=http_client, logger=logger)
    except Exception as exc:
        logger.debug(f"[game_info] wiki_lookup failed: {exc}")
        return ""
    text = str(raw or "").strip()
    if not text or text.startswith("未找到"):
        return ""
    # wiki_lookup 返回 JSON 候选；尽量抽成可读摘要，失败则原样附上。
    try:
        payload = json.loads(text)
    except Exception:
        return f"【{game} 的剧情/设定 · Wiki】\n{text[:600]}"
    candidates = payload.get("top_candidates") if isinstance(payload, dict) else None
    if not isinstance(candidates, list) or not candidates:
        return ""
    lines = [f"【{game} 的剧情/设定 · Wiki】"]
    for idx, cand in enumerate(candidates[:3], 1):
        if not isinstance(cand, dict):
            continue
        title = str(cand.get("title", "") or "").strip()
        summary = str(cand.get("summary", "") or cand.get("extract", "") or "").strip()
        url = str(cand.get("url", "") or "").strip()
        if title:
            lines.append(f"{idx}. {title}")
        if summary:
            lines.append(f"   {summary[:240]}")
        if url:
            lines.append(f"   链接：{url}")
    return "\n".join(lines) if len(lines) > 1 else ""


async def _maybe_synthesize(
    *,
    tool_caller: Any,
    logger: Any,
    game: str,
    aspect_label: str,
    raw_text: str,
) -> str:
    """可选：用 LLM 把多源结果压缩成一段可读摘要；不可用或失败时返回空（由上层兜底原文）。"""
    if tool_caller is None or not raw_text.strip():
        return ""
    system_prompt = (
        "你是游戏资讯整理助手。请把下面来自官方与社区的多源信息整理成一段简洁、可读的中文摘要，"
        "保留关键事实与最有用的链接，去掉重复与噪音，不要编造信息。"
    )
    user_prompt = f"游戏：{game}\n查询方面：{aspect_label}\n\n原始多源信息：\n{raw_text[:3500]}"
    try:
        response = await tool_caller.chat_with_tools(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            [],
            False,
        )
    except Exception as exc:
        logger.debug(f"[game_info] synthesize failed: {exc}")
        return ""
    if getattr(response, "tool_calls", None):
        return ""
    return str(getattr(response, "content", "") or "").strip()


async def game_info(
    game: str,
    aspect: str,
    query: str = "",
    *,
    http_client: httpx.AsyncClient,
    logger: Any = None,
    plugin_config: Any = None,
    tool_caller: Any = None,
) -> str:
    """游戏信息聚合查询主入口。

    aspect: update=更新公告 / guide=攻略 / story=剧情设定 / tips=技巧。
    数据源：Steam 官方公告（update）+ 社区定向搜索 + Wiki（story）。
    """
    logger = _resolve_logger(logger)
    game = str(game or "").strip()
    if not game:
        return "请提供游戏名称。"
    aspect = _normalize_aspect(aspect)
    aspect_label = _ASPECT_LABEL.get(aspect, "攻略")
    query = str(query or "").strip()
    timeout = _timeout(plugin_config)
    community_sites = _community_sites(plugin_config)

    sections: list[str] = []

    if aspect == "story":
        story = await _format_story(game, query, http_client=http_client, logger=logger)
        if story:
            sections.append(story)
        # 剧情也补一条社区搜索，覆盖 wiki 收录不全的小众游戏。
        kw = _ASPECT_SEARCH_KEYWORDS["story"]
        community = await _community_search(
            f"{game} {kw} {query}".strip(),
            http_client=http_client,
            logger=logger,
            community_sites=community_sites,
        )
        block = _format_community(aspect_label, game, community)
        if block:
            sections.append(block)
    else:
        if aspect == "update":
            # 先试 Steam 官方公告。
            appid, matched = await _resolve_steam_appid(
                game, http_client=http_client, logger=logger, timeout=timeout
            )
            if appid is not None:
                news = await _fetch_steam_news(
                    appid, http_client=http_client, logger=logger, timeout=timeout
                )
                steam_block = _format_steam_updates(game, matched, news)
                if steam_block:
                    sections.append(steam_block)
        # 社区定向搜索（攻略/技巧主力；更新作为 Steam 的补充或国服网游的主力）。
        kw = _ASPECT_SEARCH_KEYWORDS[aspect]
        community = await _community_search(
            f"{game} {kw} {query}".strip(),
            http_client=http_client,
            logger=logger,
            community_sites=community_sites,
        )
        block = _format_community(aspect_label, game, community)
        if block:
            sections.append(block)

    if not sections:
        return (
            f"没有查到「{game}」关于{aspect_label}的可靠信息，可能是名称不准确、"
            "暂无相关公告，或当前网络不可达。可以换个更准确的游戏名或具体一点的关键词再试。"
        )

    raw_text = "\n\n".join(sections)
    summary = await _maybe_synthesize(
        tool_caller=tool_caller,
        logger=logger,
        game=game,
        aspect_label=aspect_label,
        raw_text=raw_text,
    )
    if summary:
        return f"{summary}\n\n——\n参考来源：\n{raw_text}"
    return raw_text
