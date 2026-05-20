from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from ._loader import load_personification_module

wf = load_personification_module("plugin.personification.core.web_fetch")


# ============ SSRF / URL 验证 ============

def test_rejects_non_http_scheme() -> None:
    for url in ("file:///etc/passwd", "ftp://example.com/x", "javascript:alert(1)", "data:text/html,xx"):
        with pytest.raises(wf.WebFetchError, match="scheme"):
            asyncio.run(wf.fetch_web_page(url))


def test_rejects_empty_or_no_host() -> None:
    with pytest.raises(wf.WebFetchError):
        asyncio.run(wf.fetch_web_page(""))
    with pytest.raises(wf.WebFetchError, match="scheme"):
        asyncio.run(wf.fetch_web_page("not a url"))


def test_rejects_loopback_ip(monkeypatch) -> None:
    # 直接传 IP 字面值；getaddrinfo 会返回 127.0.0.1，is_loopback 命中
    with pytest.raises(wf.WebFetchError, match="内网|本地|拒绝"):
        asyncio.run(wf.fetch_web_page("http://127.0.0.1/admin"))


def test_rejects_private_ip() -> None:
    with pytest.raises(wf.WebFetchError, match="内网|本地|拒绝"):
        asyncio.run(wf.fetch_web_page("http://192.168.1.1/"))


def test_rejects_domain_resolving_to_loopback(monkeypatch) -> None:
    # mock socket.getaddrinfo 返回 127.0.0.1，模拟域名解析到内网
    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [(2, 1, 6, "", ("127.0.0.1", 0))]
    monkeypatch.setattr(wf.socket, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(wf.WebFetchError, match="内网|本地"):
        asyncio.run(wf.fetch_web_page("http://example.com/"))


def test_rejects_blocked_domain(monkeypatch) -> None:
    # 黑名单优先于 DNS 检查，无需 mock 网络
    with pytest.raises(wf.WebFetchError, match="黑名单"):
        asyncio.run(wf.fetch_web_page("http://example.com/", blocked_domains=["example.com"]))


def test_blocked_domain_matches_subdomain(monkeypatch) -> None:
    with pytest.raises(wf.WebFetchError, match="黑名单"):
        asyncio.run(wf.fetch_web_page("http://api.example.com/", blocked_domains=["example.com"]))


# ============ 正文抽取 ============

class _FakeResp:
    def __init__(self, *, text: str, status: int = 200, content_type: str = "text/html; charset=utf-8") -> None:
        self.text = text
        self.content = text.encode("utf-8")
        self.status_code = status
        self.headers = {"content-type": content_type}
        self.url = "http://example.com/article"


class _FakeClient:
    def __init__(self, *args, **kwargs) -> None:
        self._resp = kwargs.pop("_resp_override", None)
    async def __aenter__(self) -> "_FakeClient":
        return self
    async def __aexit__(self, *a) -> None:
        return None
    async def get(self, url: str) -> _FakeResp:
        if self._resp:
            return self._resp
        return _FakeResp(text="<html><head><title>T</title></head><body>x</body></html>")


def _make_safe_dns(monkeypatch) -> None:
    def fake_getaddrinfo(host, port, *args, **kwargs):
        # 返回一个公网 IP（8.8.8.8）让 SSRF 检查通过
        return [(2, 1, 6, "", ("8.8.8.8", 0))]
    monkeypatch.setattr(wf.socket, "getaddrinfo", fake_getaddrinfo)


def test_extracts_title_and_main_text(monkeypatch) -> None:
    _make_safe_dns(monkeypatch)
    html = """<html><head><title>测试标题</title></head>
    <body>
      <nav>导航不要</nav>
      <header>头部不要</header>
      <article>
        <p>这是正文第一段</p>
        <p>这是正文第二段</p>
      </article>
      <footer>底部不要</footer>
    </body></html>"""
    fake_resp = _FakeResp(text=html)
    def make_client(*args, **kwargs):
        return _FakeClient(_resp_override=fake_resp)
    monkeypatch.setattr(wf.httpx, "AsyncClient", make_client)
    result = asyncio.run(wf.fetch_web_page("http://example.com/"))
    assert result["status_code"] == 200
    assert result["title"] == "测试标题"
    assert "正文第一段" in result["text"]
    assert "正文第二段" in result["text"]
    assert "导航不要" not in result["text"]
    assert "头部不要" not in result["text"]
    assert "底部不要" not in result["text"]


def test_truncates_long_text(monkeypatch) -> None:
    _make_safe_dns(monkeypatch)
    body = "字" * 5000
    html = f"<html><body><article>{body}</article></body></html>"
    fake_resp = _FakeResp(text=html)
    def make_client(*args, **kwargs):
        return _FakeClient(_resp_override=fake_resp)
    monkeypatch.setattr(wf.httpx, "AsyncClient", make_client)
    result = asyncio.run(wf.fetch_web_page("http://example.com/", max_chars=200))
    assert result["char_count"] <= 201  # 200 + 一个截断符号
    assert result["text"].endswith("…")


def test_non_html_content_type_returned_raw(monkeypatch) -> None:
    _make_safe_dns(monkeypatch)
    fake_resp = _FakeResp(text='{"key":"value"}', content_type="application/json")
    def make_client(*args, **kwargs):
        return _FakeClient(_resp_override=fake_resp)
    monkeypatch.setattr(wf.httpx, "AsyncClient", make_client)
    result = asyncio.run(wf.fetch_web_page("http://example.com/api"))
    assert result["title"] == ""
    assert '"key":"value"' in result["text"]


def test_falls_back_when_no_article_or_main(monkeypatch) -> None:
    _make_safe_dns(monkeypatch)
    html = "<html><head><title>T</title></head><body><div>普通 div 内容</div></body></html>"
    fake_resp = _FakeResp(text=html)
    def make_client(*args, **kwargs):
        return _FakeClient(_resp_override=fake_resp)
    monkeypatch.setattr(wf.httpx, "AsyncClient", make_client)
    result = asyncio.run(wf.fetch_web_page("http://example.com/"))
    assert "普通 div 内容" in result["text"]
