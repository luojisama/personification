from __future__ import annotations

import json
import sys


def send(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, separators=(",", ":")) + "\n")
    sys.stdout.flush()


for raw in sys.stdin:
    try:
        message = json.loads(raw)
    except Exception:
        continue
    method = message.get("method")
    request_id = message.get("id")
    if request_id is None:
        continue
    if method == "initialize":
        send({"jsonrpc": "2.0", "id": request_id, "result": {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}}}})
    elif method == "tools/list":
        cursor = str((message.get("params") or {}).get("cursor") or "")
        if not cursor:
            send({"jsonrpc": "2.0", "id": request_id, "result": {"tools": [{"name": "read_demo", "description": "Read demo data", "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}, "annotations": {"readOnlyHint": True}}], "nextCursor": "page-2"}})
        else:
            send({"jsonrpc": "2.0", "id": request_id, "result": {"tools": [{"name": "write_demo", "description": "Write demo data", "inputSchema": {"type": "object", "properties": {}}, "annotations": {"destructiveHint": True}}]}})
    elif method == "tools/call":
        params = message.get("params") or {}
        send({"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": f"called:{params.get('name')}:{json.dumps(params.get('arguments') or {}, sort_keys=True)}"}], "isError": False}})
    else:
        send({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "unknown method"}})
