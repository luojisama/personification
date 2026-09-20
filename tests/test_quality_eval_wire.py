"""Offline wire-level canary for the isolated quality-evaluation runner.

The child process uses the production GeminiToolCaller and run_agent/final gate,
but its only possible network peer is this test's loopback HTTP server.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


_CHILD = r'''
import asyncio, json, os
from scripts.quality_eval import runner as quality_runner
quality_runner._bootstrap_runtime()
if os.environ.get("QUALITY_WIRE_SHORT_TIMEOUT"):
    from plugin.personification.skills.skillpacks.tool_caller.scripts import impl
    _original_init = impl.GeminiToolCaller.__init__
    def _short_timeout(self, *args, **kwargs):
        _original_init(self, *args, **kwargs)
        self.timeout = 0.05
    impl.GeminiToolCaller.__init__ = _short_timeout
from scripts.quality_eval.runner import invoke_case

root = os.environ["QUALITY_WIRE_ROOT"]
port = os.environ["QUALITY_WIRE_PORT"]
config_path = os.path.join(root, "env.json")
with open(config_path, "w", encoding="utf-8") as f:
    json.dump({"personification_api_pools": [{}, {
        "type": "gemini", "model": "gemini-3.8-flash-high",
        "api_url": f"http://127.0.0.1:{port}/v1beta",
        "api_key": "test-double-only", "gemini_auth_mode": "bearer",
        "streaming_mode": "off"
    }]}, f)

case = {
    "id": "wire-canary", "surface": "private", "trusted_persona": "自然直接",
    "seed_memory": [], "events": [{"kind": "message", "sender": "U", "text": "你好"}],
}
result = asyncio.run(invoke_case(case, {
    "execution_mode": "real", "test_double": True, "config_path": config_path,
    "budget_db": os.path.join(root, "budget.sqlite"),
    "isolated_db_path": os.path.join(root, "isolated-data"),
}))
print(json.dumps({"result": result.__dict__}, ensure_ascii=False))
'''


class _LoopbackGemini:
    def __init__(self, *, status: int = 200, delay_seconds: float = 0.0) -> None:
        self.status = status
        self.delay_seconds = delay_seconds
        self.requests: list[dict[str, Any]] = []

    def handler(self):
        parent = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length", "0"))
                parent.requests.append({"path": self.path, "body": json.loads(self.rfile.read(length) or b"{}")})
                self.send_response(parent.status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                if parent.delay_seconds:
                    import time
                    time.sleep(parent.delay_seconds)
                if parent.status >= 300:
                    self.wfile.write(b'{"error":{"message":"offline fixture failure"}}')
                    return
                rendered_prompt = json.dumps(parent.requests[-1]["body"], ensure_ascii=False)
                is_review = "候选回复" in rendered_prompt or "待核验证据" in rendered_prompt
                payload = {
                    "candidates": [{"content": {"parts": [{"text": (
                        '{"action":"accept","text":"","reason":"fixture","flags":[],"persona_verdict":"consistent"}'
                        if is_review else "你好呀，今天怎么样？"
                    )}]}}],
                    "usageMetadata": {"promptTokenCount": 7, "candidatesTokenCount": 5, "totalTokenCount": 12},
                }
                self.wfile.write(json.dumps(payload).encode("utf-8"))

            def log_message(self, *_args: Any) -> None:
                return

        return Handler


def _run_child(tmp_path: Path, server: ThreadingHTTPServer, *, short_timeout: bool = False) -> dict[str, Any]:
    repo_root = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    for name in list(env):
        if name.startswith("PERSONIFICATION_"):
            env.pop(name)
    env.update({
        "QUALITY_WIRE_ROOT": str(tmp_path),
        "QUALITY_WIRE_PORT": str(server.server_address[1]),
        "PYTHONPATH": str(repo_root),
        "NO_PROXY": "127.0.0.1,localhost",
        "no_proxy": "127.0.0.1,localhost",
        "QUALITY_WIRE_SHORT_TIMEOUT": "1" if short_timeout else "",
        "XDG_DATA_HOME": str(tmp_path / "xdg-data"),
        "XDG_CACHE_HOME": str(tmp_path / "xdg-cache"),
    })
    completed = subprocess.run(
        [sys.executable, "-c", _CHILD], cwd=tmp_path, env=env,
        capture_output=True, text=True, timeout=45, check=False,
    )
    assert completed.returncode == 0, completed.stderr
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    assert lines, completed.stderr
    return json.loads(lines[-1])["result"]


def _with_server(fixture: _LoopbackGemini, tmp_path: Path, *, short_timeout: bool = False) -> tuple[dict[str, Any], _LoopbackGemini]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), fixture.handler())
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        return _run_child(tmp_path, server, short_timeout=short_timeout), fixture
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


def test_offline_gemini_wire_budget_and_final_review_report(tmp_path: Path) -> None:
    result, fixture = _with_server(_LoopbackGemini(), tmp_path)

    assert result["execution_mode"] == "simulated"
    assert result["status"] == "completed"
    assert fixture.requests and all(item["path"].endswith(":generateContent") for item in fixture.requests)
    assert result["usage"]["wire_calls"] == len(fixture.requests)
    assert len(result["usage"]["responses"]) == len(fixture.requests)
    assert result["usage"]["responses"][0]["total_tokens"] == 12
    assert result["turns"] and result["turns"][0]["review_action"] == "accept"
    assert result["reply"] == "你好呀，今天怎么样？"
    assert result["trace"]


def test_offline_gemini_http_failure_never_reports_completed(tmp_path: Path) -> None:
    result, fixture = _with_server(_LoopbackGemini(status=503), tmp_path)

    assert fixture.requests
    assert result["status"] == "failed"
    assert result["status"] != "completed"
    assert result["reply"] == ""
    assert result["usage"]["wire_calls"] == len(fixture.requests)
    assert "http://" not in result["error"]


def test_offline_gemini_timeout_never_reports_completed(tmp_path: Path) -> None:
    result, fixture = _with_server(_LoopbackGemini(delay_seconds=0.2), tmp_path, short_timeout=True)

    assert fixture.requests
    assert result["status"] == "failed"
    assert result["status"] != "completed"
    assert result["reply"] == ""
    assert result["usage"]["wire_calls"] == len(fixture.requests)
    assert "http://" not in result["error"]
