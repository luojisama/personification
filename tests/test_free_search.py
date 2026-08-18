from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from ._loader import load_personification_module

fs = load_personification_module("plugin.personification.core.free_search")


class _FakeResponse:
    def __init__(self, status_code: int = 200, *, json_data: Any = None, text: str = ""):
        self.status_code = status_code
        self._json = json_data
        self.text = text
        self.headers = {"content-type": "application/json"}

    def json(self) -> Any:
        if self._json is None:
            raise ValueError("no json")
        return self._json


class _FakeClient:
    def __init__(self, *, route_handler):
        self._route = route_handler

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url: str, *, params=None, headers=None):
        return self._route("GET", url, params or {})

    async def head(self, url: str, *, headers=None):
        return self._route("HEAD", url, {})

    async def post(self, url: str, *, json=None, headers=None):
        return self._route("POST", url, json or {})


# ──────────── SearchResult shape ────────────

def test_search_result_dataclass_basics() -> None:
    r = fs.SearchResult(
        title="X", url="https://a.com/p", domain="a.com",
        snippet="hello", source="wikipedia.zh", score=0.7,
    )
    assert r.title == "X"
    assert r.domain == "a.com"
    assert r.source == "wikipedia.zh"
    assert r.extras == {}


def test_clip_snippet_truncates() -> None:
    long = "あ" * 500
    out = fs._clip_snippet(long, max_chars=100)
    assert len(out) <= 101  # 100 + 省略号
    assert out.endswith("…")


def test_clip_snippet_normalizes_whitespace() -> None:
    out = fs._clip_snippet("  hello   world\n\n  ")
    assert out == "hello world"


def test_domain_of_strips_www() -> None:
    assert fs._domain_of("https://www.example.com/x") == "example.com"
    assert fs._domain_of("https://en.wikipedia.org/wiki/X") == "en.wikipedia.org"
    assert fs._domain_of("not a url") == ""


# ──────────── Wikipedia ────────────

def test_wikipedia_search_returns_structured(monkeypatch) -> None:
    search_resp = {
        "query": {
            "search": [
                {"title": "Megalodon", "snippet": "An <span>extinct</span> shark"},
                {"title": "Carcharocles", "snippet": "Genus of sharks"},
            ]
        }
    }
    extract_resp = {
        "query": {
            "pages": [
                {"title": "Megalodon", "extract": "Megalodon is an extinct species of shark."},
            ]
        }
    }
    calls = {"n": 0}

    def route(method, url, params):
        calls["n"] += 1
        if params.get("list") == "search":
            return _FakeResponse(200, json_data=search_resp)
        if params.get("prop") == "extracts":
            return _FakeResponse(200, json_data=extract_resp)
        return _FakeResponse(404)

    monkeypatch.setattr(fs.httpx, "AsyncClient", lambda **kw: _FakeClient(route_handler=route))
    results = asyncio.run(fs.wikipedia_search("megalodon", lang="en", max_results=5))
    assert len(results) == 2
    assert results[0].title == "Megalodon"
    assert "extinct species of shark" in results[0].snippet
    assert results[0].url.startswith("https://en.wikipedia.org/wiki/")
    assert results[0].source == "wikipedia.en"
    assert results[0].score > results[1].score


def test_wikipedia_search_zh_to_en_fallback(monkeypatch) -> None:
    """zh 命中 0 条时自动追一次 en。"""
    counts = {"zh": 0, "en": 0}
    en_data = {"query": {"search": [{"title": "X", "snippet": "x"}]}}
    extract_data = {"query": {"pages": [{"title": "X", "extract": "X extract"}]}}

    def route(method, url, params):
        if "zh.wikipedia.org" in url:
            counts["zh"] += 1
            return _FakeResponse(200, json_data={"query": {"search": []}})
        counts["en"] += 1
        if params.get("prop") == "extracts":
            return _FakeResponse(200, json_data=extract_data)
        return _FakeResponse(200, json_data=en_data)

    monkeypatch.setattr(fs.httpx, "AsyncClient", lambda **kw: _FakeClient(route_handler=route))
    results = asyncio.run(fs.wikipedia_search("nonexistent zh topic"))
    assert counts["zh"] >= 1
    assert counts["en"] >= 1
    assert len(results) == 1
    assert results[0].source == "wikipedia.en"


def test_wikipedia_search_handles_errors(monkeypatch) -> None:
    def route(method, url, params):
        raise RuntimeError("network down")
    monkeypatch.setattr(fs.httpx, "AsyncClient", lambda **kw: _FakeClient(route_handler=route))
    # zh fails → falls back to en, which also fails → []
    results = asyncio.run(fs.wikipedia_search("x", logger=None))
    assert results == []


# ──────────── SearXNG ────────────

def test_searxng_picks_first_alive_instance(monkeypatch) -> None:
    alive = "https://second.example.com"
    probe_log: list[str] = []

    def route(method, url, params):
        if method == "HEAD":
            probe_log.append(url)
            # 第一个 dead，第二个 alive
            if "first" in url:
                raise RuntimeError("connection refused")
            return _FakeResponse(200)
        if method == "GET" and "/search" in url:
            return _FakeResponse(
                200,
                json_data={
                    "results": [
                        {"title": "T1", "url": "https://a.com/x", "content": "C1", "score": 0.9},
                        {"title": "T2", "url": "https://b.com/x", "content": "C2", "score": 0.5},
                    ]
                },
            )
        return _FakeResponse(404)

    monkeypatch.setattr(fs.httpx, "AsyncClient", lambda **kw: _FakeClient(route_handler=route))
    results = asyncio.run(
        fs.searxng_search(
            "query",
            instances=["https://first.example.com", alive],
            max_results=3,
        )
    )
    assert any("first" in u for u in probe_log)
    assert len(results) == 2
    assert results[0].source == "searxng"
    assert results[0].title == "T1"
    assert results[0].score == 0.9


def test_searxng_no_alive_instance_returns_empty(monkeypatch) -> None:
    def route(method, url, params):
        raise RuntimeError("all dead")
    monkeypatch.setattr(fs.httpx, "AsyncClient", lambda **kw: _FakeClient(route_handler=route))
    results = asyncio.run(fs.searxng_search("x", instances=["https://dead.com"]))
    assert results == []


# ──────────── DuckDuckGo ────────────

def test_ddg_instant_returns_results(monkeypatch) -> None:
    def route(method, url, params):
        if "api.duckduckgo.com" in url:
            return _FakeResponse(
                200,
                json_data={
                    "AbstractText": "ABC abstract",
                    "AbstractURL": "https://abc.com/wiki",
                    "Heading": "ABC",
                    "RelatedTopics": [
                        {"Text": "ABC related - explained", "FirstURL": "https://abc.com/r1"},
                    ],
                },
            )
        if "duckduckgo.com/html" in url:
            return _FakeResponse(200, text="<html></html>")
        return _FakeResponse(404)

    monkeypatch.setattr(fs.httpx, "AsyncClient", lambda **kw: _FakeClient(route_handler=route))
    results = asyncio.run(fs.duckduckgo_search("abc"))
    assert len(results) >= 1
    titles = [r.title for r in results]
    assert any("ABC" in t for t in titles)


def test_ddg_html_scrape_parses_results(monkeypatch) -> None:
    html = """
    <div class="result__body">
      <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Farticle">Article Title</a>
      <a class="result__snippet">This is the snippet text for the article.</a>
    </div>
    </div>
    """
    def route(method, url, params):
        if "api.duckduckgo.com" in url:
            return _FakeResponse(200, json_data={})
        if "duckduckgo.com/html" in url:
            return _FakeResponse(200, text=html)
        return _FakeResponse(404)

    monkeypatch.setattr(fs.httpx, "AsyncClient", lambda **kw: _FakeClient(route_handler=route))
    results = asyncio.run(fs.duckduckgo_search("x"))
    assert len(results) >= 1
    r = next((r for r in results if r.source == "duckduckgo.html"), None)
    assert r is not None
    assert r.url == "https://example.com/article"
    assert "snippet" in r.snippet


# ──────────── 萌娘百科 & 必应 ────────────

def test_moegirl_search_returns_structured(monkeypatch) -> None:
    opensearch_resp = [
        "绪山真寻",
        ["绪山真寻", "绪山美寻"],
        ["《别当欧尼酱了！》主人公。", "《别当欧尼酱了！》登场人物。"],
        ["https://zh.moegirl.org.cn/%E7%BB%AA%E5%B1%B1%E7%9C%9F%E5%AF%BB", "https://zh.moegirl.org.cn/%E7%BB%AA%E5%B1%B1%E7%BE%8E%E5%AF%BB"],
    ]

    def route(method, url, params):
        if "moegirl" in url and params.get("action") == "opensearch":
            return _FakeResponse(200, json_data=opensearch_resp)
        return _FakeResponse(404)

    monkeypatch.setattr(fs.httpx, "AsyncClient", lambda **kw: _FakeClient(route_handler=route))
    results = asyncio.run(fs.moegirl_search("绪山真寻"))
    assert len(results) == 2
    assert results[0].title == "绪山真寻"
    assert results[0].domain == "zh.moegirl.org.cn"
    assert results[0].source == "moegirl"
    assert "别当欧尼酱了" in results[0].snippet


def test_bing_search_parses_html(monkeypatch) -> None:
    html = """
    <li class="b_algo">
      <h2><a href="https://baike.baidu.com/item/绪山真寻">绪山真寻_百度百科</a></h2>
      <div class="b_caption"><p>绪山真寻是漫画《别当欧尼酱了！》的主人公。</p></div>
    </li>
    """

    def route(method, url, params):
        if "cn.bing.com/search" in url:
            return _FakeResponse(200, text=html)
        return _FakeResponse(404)

    monkeypatch.setattr(fs.httpx, "AsyncClient", lambda **kw: _FakeClient(route_handler=route))
    results = asyncio.run(fs.bing_search("绪山真寻"))
    assert len(results) == 1
    assert "百度百科" in results[0].title
    assert results[0].source == "bing"
    assert "别当欧尼酱了" in results[0].snippet


# ──────────── free_search 调度 ────────────

def test_free_search_respects_engines_list_and_priority(monkeypatch) -> None:
    called: list[str] = []

    async def fake_moe(q, **kw):
        called.append("moe")
        return [fs.SearchResult(title="Moe", url="https://moe.com", domain="moe.com", snippet="moe", source="moegirl", score=0.95)]

    async def fake_bing(q, **kw):
        called.append("bing")
        return [fs.SearchResult(title="Bing", url="https://bing.com", domain="bing.com", snippet="bing", source="bing", score=0.9)]

    async def fake_wiki(q, **kw):
        called.append("wiki")
        return [fs.SearchResult(title="Wiki", url="https://wiki.com", domain="wiki.com", snippet="wiki", source="wikipedia", score=0.7)]

    monkeypatch.setattr(fs, "moegirl_search", fake_moe)
    monkeypatch.setattr(fs, "bing_search", fake_bing)
    monkeypatch.setattr(fs, "wikipedia_search", fake_wiki)

    out = asyncio.run(fs.free_search("q", engines=["moegirl", "bing"]))
    assert "moe" in called and "bing" in called and "wiki" not in called
    assert len(out) == 2
    assert out[0].source == "moegirl"
    assert out[1].source == "bing"


def test_free_search_empty_engines_uses_default(monkeypatch) -> None:
    async def fake_moe(q, **kw):
        return [fs.SearchResult(title="M", url="https://m.com", domain="m.com", snippet="m", source="moegirl", score=0.9)]

    monkeypatch.setattr(fs, "moegirl_search", fake_moe)
    out = asyncio.run(fs.free_search("q", engines=[]))
    # 默认空或缺省时使用 DEFAULT_FREE_SEARCH_ENGINES，包含 moegirl
    assert len(out) >= 1
    assert out[0].source == "moegirl"


def test_free_search_empty_query_returns_empty() -> None:
    out = asyncio.run(fs.free_search("   ", engines=["wikipedia"]))
    assert out == []
