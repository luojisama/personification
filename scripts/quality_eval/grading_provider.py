"""Offline Promptfoo 0.120 Python-provider adapter for grading artifacts.

Promptfoo serializes context as JSON, so it cannot carry the main runner's
Python ``BudgetedCaller``.  Live grading is therefore performed by the runner
with ``grade_pair``; this provider only reads that already-generated JSONL for
Promptfoo report export and never calls a model or HTTP endpoint.
"""
from __future__ import annotations

import json
from typing import Any

async def call_api(prompt: str, options: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    payload = context.get("vars", context)
    config = options.get("config", {}) if isinstance(options, dict) else {}
    artifact = config.get("grading_artifact") or payload.get("grading_artifact")
    case_id = str(payload.get("case_id", "") or "")
    if not artifact or not case_id:
        result = {"status": "ungraded", "reason": "missing_artifact_or_case_id"}
    else:
        result = None
        for line in open(artifact, encoding="utf-8"):
            row = json.loads(line)
            if str(row.get("case_id", "") or "") == case_id:
                result = row
                break
        if result is None:
            result = {"case_id": case_id, "status": "ungraded", "reason": "artifact_case_missing"}
    return {"output": json.dumps(result, ensure_ascii=False), "metadata": {"cost": "unknown", "offline_artifact": True}}
