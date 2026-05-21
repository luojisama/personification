from __future__ import annotations

import asyncio
import json
import pathlib
from typing import Any
from unittest.mock import MagicMock, patch

from ._loader import load_personification_module

impl = load_personification_module(
    "plugin.personification.skills.skillpacks.tool_caller.scripts.impl"
)


# ====== 纯函数测试 ======

def test_endpoint_uses_cloudcode_pa() -> None:
    """endpoint 必须是 cloudcode-pa（非不存在的 antigravity-pa）。"""
    endpoint = impl._ANTIGRAVITY_CLI_GENERATE_ENDPOINT
    assert endpoint == "https://cloudcode-pa.googleapis.com/v1internal:generateContent"
    assert "antigravity-pa" not in endpoint


def test_user_agent_format() -> None:
    ua = impl._antigravity_user_agent()
    assert ua.startswith("antigravity/1.15.8 ")
    rest = ua.split(" ", 1)[1]
    assert "/" in rest
    os_part, arch_part = rest.split("/", 1)
    assert os_part in {"windows", "linux", "macos"}
    assert arch_part in {"amd64", "arm64"}, f"unexpected arch token: {arch_part!r}"


def test_client_platform_returns_canonical() -> None:
    p = impl._antigravity_client_platform()
    assert p in {"WINDOWS", "MACOS", "LINUX"}


def test_model_candidates_user_model_first() -> None:
    cs = impl._antigravity_cli_model_candidates("gemini-3.5-flash")
    assert cs[0] == "gemini-3.5-flash"
    assert "gemini-2.5-flash" in cs
    assert "gemini-3-pro-high" in cs
    assert cs.count("gemini-3.5-flash") == 1


def test_model_candidates_auto_expands() -> None:
    cs = impl._antigravity_cli_model_candidates("auto-gemini-3")
    assert cs[0] == "gemini-3.5-flash"
    assert "gemini-3-pro-low" in cs


def test_model_candidates_unknown_user_model_keeps_first_with_fallback() -> None:
    cs = impl._antigravity_cli_model_candidates("some-future-model-x")
    assert cs[0] == "some-future-model-x"
    assert "gemini-3.5-flash" in cs


# ====== 协议端到端测试（mock httpx） ======

def _run_chat(model: str = "gemini-3.5-flash") -> dict[str, Any]:
    """构造一个 AntigravityCliToolCaller，mock 凭证/project/httpx，捕获 POST 详情。"""
    caller = impl.AntigravityCliToolCaller(
        model=model,
        auth_path="",
        project="fake-project-id",
        thinking_mode="none",
        timeout=30.0,
    )
    captured: dict[str, Any] = {}

    class _CapturingClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> "_CapturingClient":
            return self

        async def __aexit__(self, *args: Any) -> None:
            return None

        async def post(self, url: str, *, json: dict, headers: dict) -> Any:
            captured["url"] = url
            captured["json"] = json
            captured["headers"] = headers
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            resp.json = lambda: {
                "response": {
                    "candidates": [
                        {"content": {"parts": [{"text": "ok"}]}}
                    ]
                }
            }
            resp.status_code = 200
            return resp

    async def fake_get_access_token(*, force_refresh: bool = False):
        return ("fake-token", pathlib.Path("/tmp/fake_auth.json"))

    with patch.object(caller, "_get_access_token", new=fake_get_access_token), \
         patch.object(impl.httpx, "AsyncClient", _CapturingClient):
        result = asyncio.run(
            caller.chat_with_tools(
                messages=[{"role": "user", "content": "hi"}],
                tools=[],
                use_builtin_search=False,
            )
        )

    captured["result"] = result
    return captured


def test_envelope_includes_user_agent_and_request_id() -> None:
    c = _run_chat()
    body = c["json"]
    assert body["model"] == "gemini-3.5-flash"
    assert body["project"] == "fake-project-id"
    assert body["userAgent"] == "antigravity"
    assert "requestId" in body
    rid = body["requestId"]
    assert isinstance(rid, str) and len(rid) >= 16, f"requestId 不合理: {rid!r}"


def test_post_url_is_cloudcode_not_antigravity_pa() -> None:
    c = _run_chat()
    assert c["url"] == "https://cloudcode-pa.googleapis.com/v1internal:generateContent"
    assert "antigravity-pa" not in c["url"]


def test_headers_include_client_metadata_and_x_goog() -> None:
    c = _run_chat()
    h = c["headers"]
    assert h["Authorization"] == "Bearer fake-token"
    assert h["Content-Type"] == "application/json"
    assert h["X-Goog-Api-Client"] == "google-cloud-sdk vscode_cloudshelleditor/0.1"
    assert h["User-Agent"].startswith("antigravity/")
    cm = json.loads(h["Client-Metadata"])
    assert cm["ideType"] == "ANTIGRAVITY"
    assert cm["pluginType"] == "GEMINI"
    assert cm["platform"] in {"WINDOWS", "MACOS", "LINUX"}


def test_response_unwrapped_correctly() -> None:
    c = _run_chat()
    assert c["result"].content == "ok"
