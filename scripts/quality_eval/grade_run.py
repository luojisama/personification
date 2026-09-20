"""Grade saved pairs using the same server-local route and global call budget."""
import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.quality_eval.grading import grade_pair, prepare_pairs
from scripts.quality_eval.runner import BudgetedCaller, CallBudget, _bootstrap_runtime, _load_fixed_gemini_route


async def run(args):
    pairs = prepare_pairs(args.baseline, args.candidate)
    artifact = Path(args.artifact_dir).resolve()
    artifact.mkdir(parents=True, exist_ok=True)
    route = _load_fixed_gemini_route(str(Path(args.config_path).resolve()))
    budget = CallBudget(Path(args.budget_db).resolve())
    os.chdir(artifact)
    os.environ["XDG_DATA_HOME"] = str(artifact / "data")
    os.environ["XDG_CACHE_HOME"] = str(artifact / "cache")
    _bootstrap_runtime()
    from plugin.personification.core.db import close_db, init_db_sync
    from plugin.personification.skills.skillpacks.tool_caller.scripts.impl import GeminiToolCaller
    await close_db()
    init_db_sync(artifact)
    caller = BudgetedCaller(GeminiToolCaller(api_key=str(route.get("api_key", "")),
        base_url=route["api_url"], model=route["model"], auth_mode="bearer", streaming_mode="off"), budget)
    output = artifact / "grades.jsonl"
    done = {json.loads(line)["case_id"] for line in output.read_text(encoding="utf-8").splitlines()} if output.exists() else set()
    (artifact / "pairing.json").write_text(json.dumps({key: value for key, value in pairs.items() if key != "pairs"}, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        for index, pair in enumerate(pairs["pairs"]):
            if pair["case_id"] in done:
                continue
            if budget.snapshot()["remaining"] < 2:
                break
            before = budget.snapshot()["reserved_calls"]
            usage_offset = len(caller.usages)
            result = await grade_pair(pair["case"], pair["baseline"], pair["candidate"], caller=caller, seed=args.seed + index)
            row = {"case_id": pair["case_id"], "surface": pair["case"].get("surface"),
                "category": pair["case"].get("category"), "split": pair["case"].get("split"),
                "baseline_revision": pair["baseline"]["revision"], "candidate_revision": pair["candidate"]["revision"],
                **result, "usage": {"wire_calls": budget.snapshot()["reserved_calls"] - before,
                    "responses": caller.usages[usage_offset:]}}
            with output.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(row, ensure_ascii=False) + "\n")
                stream.flush()
            print(json.dumps({"case_id": pair["case_id"], "status": result["status"], "budget": budget.snapshot()}), flush=True)
            if caller.failure_types or caller.exhausted:
                break
    finally:
        await close_db()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("baseline", "candidate", "artifact-dir", "config-path", "budget-db"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--seed", type=int, default=20260920)
    asyncio.run(run(parser.parse_args()))
