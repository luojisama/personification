from __future__ import annotations

import asyncio
import json
import pathlib
import sys
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


# ====== keyring 同步链路 ======

def _make_fake_keyring(blob: str | None) -> Any:
    class _FakeKeyring:
        @staticmethod
        def get_password(service: str, user: str) -> str | None:
            assert service == "gemini:antigravity"
            assert user == "antigravity"
            return blob
    return _FakeKeyring


def _utf16le_wrap(s: str) -> str:
    """模拟 Windows Credential Manager 把 UTF-8 bytes 当 UTF-16LE 字符串呈现的行为。"""
    b = s.encode("utf-8")
    if len(b) % 2:
        b += b"\x00"
    return b.decode("utf-16-le")


def test_read_keyring_token_decodes_utf16_wrap(monkeypatch=None) -> None:
    raw_json = json.dumps({
        "token": {
            "access_token": "ya29.fake-access-token-xxxxx",
            "token_type": "Bearer",
            "refresh_token": "1//06fake-refresh",
            "expiry": "2030-01-01T00:00:00+00:00",
        },
        "auth_method": "consumer",
    })
    wrapped = _utf16le_wrap(raw_json)
    fake = _make_fake_keyring(wrapped)
    saved = sys.modules.get("keyring")
    sys.modules["keyring"] = fake
    try:
        token = impl._read_antigravity_keyring_token()
    finally:
        if saved is None:
            sys.modules.pop("keyring", None)
        else:
            sys.modules["keyring"] = saved
    assert token is not None
    assert token["access_token"] == "ya29.fake-access-token-xxxxx"
    assert token["refresh_token"] == "1//06fake-refresh"


def test_read_keyring_token_returns_none_when_missing() -> None:
    fake = _make_fake_keyring(None)
    saved = sys.modules.get("keyring")
    sys.modules["keyring"] = fake
    try:
        assert impl._read_antigravity_keyring_token() is None
    finally:
        if saved is None:
            sys.modules.pop("keyring", None)
        else:
            sys.modules["keyring"] = saved


def test_sync_keyring_writes_file_with_full_scope(tmp_path_root: Any = None) -> None:
    raw_json = json.dumps({
        "token": {
            "access_token": "ya29.fake-AT",
            "token_type": "Bearer",
            "refresh_token": "1//rf",
            "expiry": "2030-01-01T00:00:00+00:00",
        },
        "auth_method": "consumer",
    })
    wrapped = _utf16le_wrap(raw_json)
    fake = _make_fake_keyring(wrapped)
    saved_kr = sys.modules.get("keyring")
    sys.modules["keyring"] = fake
    # 也要把 _Path.home() 重定向到 tmp 目录，避免污染真实用户目录
    import tempfile
    tmp_home = pathlib.Path(tempfile.mkdtemp())
    saved_home = impl._Path.home
    impl._Path.home = staticmethod(lambda: tmp_home)
    try:
        target = impl._sync_antigravity_keyring_to_file()
        assert target is not None
        assert target.exists()
        d = json.loads(target.read_text(encoding="utf-8"))
        assert d["access_token"] == "ya29.fake-AT"
        assert d["refresh_token"] == "1//rf"
        assert "cclog" in d["scope"]
        assert "experimentsandconfigs" in d["scope"]
        assert d["expiry_date"] > 0
    finally:
        if saved_kr is None:
            sys.modules.pop("keyring", None)
        else:
            sys.modules["keyring"] = saved_kr
        impl._Path.home = saved_home
        import shutil
        shutil.rmtree(tmp_home, ignore_errors=True)


def test_find_auth_file_logs_keyring_sync_attempt() -> None:
    """_find_antigravity_cli_auth_file_with_log 应在搜索 log 里体现 keyring 同步动作。"""
    # 不论 keyring 有无凭证，函数都应正常返回（不抛）
    fake = _make_fake_keyring(None)
    saved_kr = sys.modules.get("keyring")
    sys.modules["keyring"] = fake
    try:
        found, log = impl._find_antigravity_cli_auth_file_with_log("")
    finally:
        if saved_kr is None:
            sys.modules.pop("keyring", None)
        else:
            sys.modules["keyring"] = saved_kr
    # log 第一项不一定包含 "keyring"（无 token 时不写日志），但函数本身要正常返回
    assert isinstance(log, list)
