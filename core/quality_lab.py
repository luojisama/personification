"""Persistent blind comparisons backed by the existing isolated pipeline runner.

The service never imports a reply processor in the web process. Every candidate
gets a fresh child process and data directory; it cannot touch production state.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import itertools
import json
from pathlib import Path
import secrets
import sqlite3
import sys
from typing import Any

from .sensitive_data import sanitize_object, sanitize_text

DIMENSIONS = ("naturalness", "persona", "context", "relationship", "factuality")
VARIANTS = ("A", "B", "C")
_TERMINAL = {"completed", "failed", "cancelled", "interrupted"}


class QualityLabError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _id() -> str:
    return secrets.token_hex(12)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def validate_case(value: Any) -> dict[str, Any]:
    """Reject capabilities/paths rather than accidentally expanding fixtures."""
    if not isinstance(value, dict) or len(_json(value)) > 100_000:
        raise QualityLabError("案例格式无效或超过大小限制")
    allowed = {"surface", "pipeline", "persona", "events", "seed", "seed_memory", "current_time", "bot_id"}
    if set(value) - allowed:
        raise QualityLabError("案例包含不支持的字段；禁止导入运行配置或工具执行信息")
    if value.get("surface", "group") not in {"group", "private"} or value.get("pipeline", "normal") not in {"normal", "yaml"}:
        raise QualityLabError("案例会话类型无效")
    if not isinstance(value.get("persona"), (str, dict)) or not value["persona"]:
        raise QualityLabError("请提供冻结的人格快照")
    events = value.get("events")
    if not isinstance(events, list) or not 1 <= len(events) <= 50:
        raise QualityLabError("每个案例需要 1 至 50 条消息")
    event_keys = {"kind", "user_id", "sender", "text", "message_id", "group_id", "bot_id"}
    for event in events:
        if not isinstance(event, dict) or set(event) - event_keys or event.get("kind", "message") not in {"message", "mention"}:
            raise QualityLabError("案例只支持文本消息；媒体和外部工具案例请使用离线评测入口")
        if not isinstance(event.get("text", ""), str):
            raise QualityLabError("消息正文必须是文本")
    def contains_secret(item: Any) -> bool:
        if isinstance(item, dict):
            return any(contains_secret(v) for v in item.values())
        if isinstance(item, list):
            return any(contains_secret(v) for v in item)
        return isinstance(item, str) and sanitize_text(item) != item
    if contains_secret(value):
        raise QualityLabError("案例包含疑似凭据，请脱敏后重新导入")
    return deepcopy(value)


class QualityLab:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / "quality.sqlite3"
        self._tasks: dict[str, asyncio.Task] = {}
        self._processes: dict[str, Any] = {}
        with self._db() as db:
            db.execute("CREATE TABLE IF NOT EXISTS quality_items (kind TEXT, id TEXT, payload TEXT NOT NULL, PRIMARY KEY(kind,id))")
        # Never resume or replay model calls after an uncertain interruption.
        for job in self._list("job"):
            if job["status"] not in _TERMINAL:
                job.update(status="interrupted", error="服务重启，已停止实验；未自动重放请求")
                self._save("job", job)

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.db_path)
        try:
            with db:
                yield db
        finally:
            db.close()

    def _save(self, kind: str, value: dict[str, Any]) -> None:
        with self._db() as db:
            db.execute("INSERT OR REPLACE INTO quality_items VALUES (?,?,?)", (kind, value["id"], _json(value)))
        if kind == "job":
            from .runtime_events import publish_runtime_event
            publish_runtime_event("quality.job.updated", payload={"job_id": value["id"], "status": value["status"], "completed": value.get("progress", {}).get("completed", 0)})

    def _get(self, kind: str, item_id: str) -> dict[str, Any]:
        with self._db() as db:
            row = db.execute("SELECT payload FROM quality_items WHERE kind=? AND id=?", (kind, item_id)).fetchone()
        if row is None:
            raise QualityLabError("记录不存在")
        return json.loads(row[0])

    def _list(self, kind: str) -> list[dict[str, Any]]:
        with self._db() as db:
            rows = db.execute("SELECT payload FROM quality_items WHERE kind=? ORDER BY rowid DESC LIMIT 500", (kind,)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def add_case(self, payload: dict[str, Any]) -> dict[str, Any]:
        if payload.get("redact_confirmed") is not True:
            raise QualityLabError("请先检查并确认案例已经脱敏")
        split = payload.get("split", "development")
        if split not in {"development", "holdout"}:
            raise QualityLabError("案例分组无效")
        case = validate_case(payload.get("case"))
        item = {"id": _id(), "title": sanitize_text(str(payload.get("title", "未命名案例")))[:120], "split": split, "case": case, "created_at": _now()}
        self._save("case", item)
        return item

    def providers(self, plugin_config: Any, *, private: bool = False) -> list[dict[str, Any]]:
        result = []
        pools = getattr(plugin_config, "personification_api_pools", []) or []
        if isinstance(pools, str):
            try:
                pools = json.loads(pools)
            except ValueError:
                return []
        if not isinstance(pools, list):
            return []
        if not pools and getattr(plugin_config, "personification_api_url", ""):
            pools = [{"api_type": getattr(plugin_config, "personification_api_type", "openai"), "api_url": getattr(plugin_config, "personification_api_url", ""), "api_key": getattr(plugin_config, "personification_api_key", ""), "model": getattr(plugin_config, "personification_model", ""), "thinking_mode": getattr(plugin_config, "personification_thinking_mode", "none"), "gemini_auth_mode": getattr(plugin_config, "personification_gemini_auth_mode", "auto")}]
        for index, raw in enumerate(pools):
            if not isinstance(raw, dict) and not hasattr(raw, "model_dump"):
                continue
            route = raw.model_dump() if hasattr(raw, "model_dump") else dict(raw)
            api_type = str(route.get("api_type", route.get("type", ""))).lower()
            if not route.get("enabled", True) or api_type not in {"gemini", "gemini_official", "openai"}:
                continue
            model = str(route.get("model", route.get("model_id", "")))
            endpoint = str(route.get("api_url", route.get("base_url", "")))
            # Google auto negotiation/native SDK may issue uncounted HTTP calls.
            # Keep a single audited dispatch until those routes have a wire meter.
            if not model or not endpoint or (api_type != "openai" and ("googleapis.com" in endpoint.lower() or route.get("gemini_auth_mode") != "bearer")):
                continue
            entry = {"id": str(index), "model": model, "api_type": "openai" if api_type == "openai" else "gemini"}
            if private:
                entry["route"] = {**route, "api_type": entry["api_type"], "api_url": endpoint, "model": model}
            result.append(entry)
        return result

    def snapshot(self, config: Any) -> dict[str, Any]:
        return {"cases": self._list("case"), "jobs": [self.job(j["id"]) for j in self._list("job")], "providers": self.providers(config), "dimensions": list(DIMENSIONS)}

    def preview(self, payload: dict[str, Any], config: Any) -> dict[str, Any]:
        case_ids = payload.get("case_ids")
        if not isinstance(case_ids, list) or not 1 <= len(case_ids) <= 50 or any(not isinstance(v, str) for v in case_ids) or len(set(case_ids)) != len(case_ids):
            raise QualityLabError("请选择 1 至 50 个不同案例")
        cases = [self._get("case", value) for value in case_ids]
        budget = payload.get("request_budget")
        if isinstance(budget, bool) or not isinstance(budget, int) or not 1 <= budget <= 1500:
            raise QualityLabError("请显式设置 1 至 1500 的本次请求预算")
        provider = next((p for p in self.providers(config) if p["id"] == str(payload.get("provider_id"))), None)
        if not provider:
            raise QualityLabError("当前 Provider 不支持隔离评测；需配置 OpenAI 或固定 bearer 的 Gemini 自定义接口")
        return {"model": provider["model"], "request_budget": budget, "candidate_count": len(cases) * 3, "variants": list(VARIANTS), "warnings": ["包含规划、生成、记忆判断和审阅请求；预算不足会停止，不能据未完成结果宣告提升。", "保留集仅供最终验证，不用于调参。", "仅重放显式导入的快照；不会读取生产聊天或触发真实外发。"]}

    async def start(self, payload: dict[str, Any], config: Any) -> dict[str, Any]:
        if payload.get("confirmed") is not True:
            raise QualityLabError("请确认模型和请求预算后运行")
        preview = self.preview(payload, config)
        if any(not task.done() for task in self._tasks.values()):
            raise QualityLabError("已有实验运行中，请等待结束或先停止")
        provider = next(p for p in self.providers(config, private=True) if p["id"] == str(payload["provider_id"]))
        # Read only the existing behavioral allowlist; never export provider config.
        from ..scripts.quality_eval.runner import BEHAVIOR_KEYS
        behavior = {key: getattr(config, key) for key in BEHAVIOR_KEYS if hasattr(config, key)}
        job_id = _id()
        cases = [self._get("case", value) for value in payload["case_ids"]]
        job = {"id": job_id, "status": "running", "model": preview["model"], "request_budget": preview["request_budget"], "case_ids": list(payload["case_ids"]), "created_at": _now(), "error": "", "results": [], "ballots": [], "scores": [], "progress": {"completed": 0, "total": preview["candidate_count"]}, "snapshot_sha256": hashlib.sha256(_json({"cases": cases, "behavior": behavior}).encode()).hexdigest()}
        self._save("job", job)
        self._tasks[job_id] = asyncio.create_task(self._run(job, cases, provider["route"], behavior))
        return self.job(job_id)

    async def _candidate(self, job: dict, case: dict, variant: str, route: dict, behavior: dict) -> dict:
        root = self.root / job["id"]
        folder = root / (case["id"] + "-" + variant)
        folder.mkdir(parents=True, exist_ok=True)
        worker = Path(__file__).resolve().parents[1] / "scripts" / "quality_eval" / "web_worker.py"
        payload = {"variant": variant, "case": {**case["case"], "id": case["id"]}, "config": {"execution_mode": "real", "runtime_path": "pipeline", "frozen_route": route, "behavior_source": "server", "behavior_config": deepcopy(behavior), "isolated_db_path": str(folder / "state"), "budget_db": str(root / "budget.sqlite3"), "call_limit": job["request_budget"]}}
        proc = await asyncio.create_subprocess_exec(sys.executable, str(worker), cwd=str(folder), stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        self._processes[job["id"]] = proc
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(_json(payload).encode("utf-8")), timeout=1200)
            if proc.returncode or len(stdout) > 2_000_000:
                return {"status": "failed", "error": "候选运行失败", "reply": ""}
            result = json.loads(stdout)
            return sanitize_object(result)
        finally:
            if proc.returncode is None:
                proc.kill()
                await proc.wait()
            self._processes.pop(job["id"], None)

    async def _run(self, job: dict, cases: list[dict], route: dict, behavior: dict) -> None:
        try:
            # Shuffle execution order as well as display order to reduce drift.
            for case in cases:
                order = list(VARIANTS)
                secrets.SystemRandom().shuffle(order)
                candidates = []
                for variant in order:
                    result = await self._candidate(job, case, variant, route, behavior)
                    row = {"id": _id(), "case_id": case["id"], "variant": variant, **result}
                    job["results"].append(row)
                    candidates.append(row)
                    job["progress"]["completed"] += 1
                    self._save("job", job)
                    if result.get("status") == "budget_exhausted":
                        raise QualityLabError("请求预算已耗尽，结果不完整")
                # Three pairwise ballots permit tie / both-bad without ambiguity.
                for pair in itertools.combinations(candidates, 2):
                    shuffled = list(pair)
                    secrets.SystemRandom().shuffle(shuffled)
                    job["ballots"].append({"id": _id(), "case_id": case["id"], "candidates": [{"id": r["id"], "text": r.get("reply", ""), "status": r.get("status", "failed")} for r in shuffled]})
                self._save("job", job)
            job["status"] = "completed"
        except asyncio.CancelledError:
            job.update(status="cancelled", error="实验已停止，未重放未知结果")
        except Exception:
            job.update(status="failed", error="实验未完成；请检查预算、配置和候选状态")
        finally:
            job["finished_at"] = _now()
            self._save("job", job)

    def job(self, job_id: str) -> dict[str, Any]:
        job = self._get("job", job_id)
        return {key: value for key, value in job.items() if key not in {"results", "scores"}} | {"scored_ballot_ids": [s["ballot_id"] for s in job["scores"]]}

    def score(self, job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        job = self._get("job", job_id)
        if job.get("revealed_at"):
            raise QualityLabError("实验已经揭盲，不能修改盲评记录")
        if job["status"] not in _TERMINAL:
            raise QualityLabError("请等待实验结束后评分")
        ballot = next((b for b in job["ballots"] if b["id"] == payload.get("ballot_id")), None)
        if not ballot or not isinstance(payload.get("winner"), str) or payload.get("winner") not in {"tie", "both_bad", *(c["id"] for c in ballot["candidates"])}:
            raise QualityLabError("评分候选无效")
        dimensions = payload.get("dimensions", {})
        if not isinstance(dimensions, dict) or set(dimensions) != set(DIMENSIONS) or any(type(v) is not int or not 1 <= v <= 5 for v in dimensions.values()):
            raise QualityLabError("请对全部五个维度给出 1 至 5 分")
        score = {"ballot_id": ballot["id"], "winner": payload["winner"], "dimensions": dimensions, "reason": sanitize_text(str(payload.get("reason", "")))[:2000], "created_at": _now()}
        job["scores"] = [s for s in job["scores"] if s["ballot_id"] != ballot["id"]] + [score]
        self._save("job", job)
        return self.job(job_id)

    def report(self, job_id: str) -> dict[str, Any]:
        job = self._get("job", job_id)
        if job["status"] not in _TERMINAL or len(job["scores"]) < len(job["ballots"]):
            raise QualityLabError("完成所有盲评后才能揭盲")
        if not job.get("revealed_at"):
            job["revealed_at"] = _now()
            self._save("job", job)
        job["variants"] = {"A": "当前完整链路", "B": "精简表达限制", "C": "精简表达限制与长度软目标"}
        job["metrics"] = {"wire_calls": sum(int(r.get("usage", {}).get("wire_calls", 0)) for r in job["results"] if isinstance(r.get("usage"), dict)), "elapsed_ms": sum(int(r.get("elapsed_ms", 0)) for r in job["results"]), "failed_candidates": sum(r.get("status") not in {"ok", "passed", "success", "completed", "no_reply"} for r in job["results"])}
        # Persisted reservations survive a killed worker / lost IPC result.
        # They are an upper bound on HTTP calls, not proof of provider billing.
        budget_path = self.root / job_id / "budget.sqlite3"
        if budget_path.is_file():
            db = sqlite3.connect(budget_path)
            try:
                row = db.execute("SELECT reserved FROM quality_eval_budget WHERE id=1").fetchone()
                job["metrics"]["reserved_calls"] = int(row[0]) if row else None
            finally:
                db.close()
        by_variant = {}
        for variant in VARIANTS:
            rows = [r for r in job["results"] if r["variant"] == variant]
            turns = [turn for r in rows for turn in (r.get("turns") or [])]
            replies = [str(turn.get("reply", "")) for turn in turns if turn.get("reply")]
            token_counts = [usage.get("total_tokens") for r in rows for usage in (r.get("usage") or {}).get("responses", []) if isinstance(usage, dict)]
            by_variant[variant] = {
                "candidate_count": len(rows),
                "mean_elapsed_ms": round(sum(int(r.get("elapsed_ms", 0)) for r in rows) / len(rows)) if rows else None,
                "reported_tokens": sum(v for v in token_counts if isinstance(v, int)) if any(isinstance(v, int) for v in token_counts) else None,
                "truncated_turns": sum(bool(t.get("truncated")) for t in turns),
                "length_observed_turns": sum(bool(t.get("length_observations")) for t in turns),
                "unique_reply_ratio": len(set(replies)) / len(replies) if replies else None,
                "repeated_opening_count": len(replies) - len({r[:6] for r in replies}),
            }
        job["metrics"]["by_variant"] = by_variant
        job["release_gate"] = "requires_human_review_and_holdout"
        return job

    async def cancel(self, job_id: str) -> dict[str, Any]:
        task = self._tasks.get(job_id)
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                job = self._get("job", job_id)
                job.update(status="cancelled", error="实验已停止，未重放未知结果")
                self._save("job", job)
        return self.job(job_id)

    async def close(self) -> None:
        for task in self._tasks.values():
            if not task.done():
                task.cancel()
        await asyncio.gather(*self._tasks.values(), return_exceptions=True)
