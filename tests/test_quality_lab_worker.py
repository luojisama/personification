"""Exercise actual WebUI IPC and the full pipeline against a loopback model."""
from __future__ import annotations

import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
from types import SimpleNamespace

from ._loader import load_personification_module

lab_mod = load_personification_module("plugin.personification.core.quality_lab")


def test_worker_freezes_route_budgets_requests_and_only_captures_outbound(tmp_path, monkeypatch):
    # The subprocess inherits environment; loopback fixtures must bypass any
    # developer proxy without inspecting its address or credentials.
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(request)
            text = json.dumps(request, ensure_ascii=False)
            if "候选回复" in text or "待核验证据" in text:
                answer = '{"action":"accept","text":"","flags":[],"persona_verdict":"consistent"}'
            elif "reply_action" in text and "ambiguity_level" in text:
                answer = '{"reply_action":"reply","speech_act":"answer","memory_need":"none","research_need":"none","vision_need":"none","output_mode":"chat_short","tool_intent":[],"ambiguity_level":"low","message_target":"bot","confidence":0.9}'
            else:
                answer = "<output><message>隔离模型测试回复</message></output>"
            response = {"candidates": [{"content": {"parts": [{"text": answer}]}}], "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 8, "totalTokenCount": 18}}
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(response).encode())
        def log_message(self, *_args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    lab = lab_mod.QualityLab(tmp_path)
    route = {"api_type": "gemini", "api_url": f"http://127.0.0.1:{server.server_port}", "api_key": "fixture-only", "model": "fixture-gemini", "gemini_auth_mode": "bearer"}
    job = {"id": "isolated-test", "request_budget": 15}
    case = {"id": "case", "case": {"surface": "private", "persona": "你是测试人格，简洁自然地交流", "events": [{"kind": "message", "text": "你好", "user_id": "fixture"}]}}
    try:
        result = asyncio.run(lab._candidate(job, case, "C", route, {}))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    assert result["status"] == "completed", {k:v for k,v in result.items() if k in {"status","error","error_code","reason","reason_code","failure_code","delivery","usage"}}
    assert result["delivery"] == "capture_confirmed"
    assert result["confirmed_history"] > 0
    assert result["usage"]["wire_calls"] == len(requests)
    assert len(requests) <= 15
    assert any("软目标" in json.dumps(request, ensure_ascii=False) for request in requests)
    assert "fixture-only" not in json.dumps(result)
    assert not any(path.name == "env.json" for path in tmp_path.rglob("*"))
