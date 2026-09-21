"""Sequential real-model runner. Invoked from a dedicated artifact directory."""
import argparse
import asyncio
import json
import os
import hashlib
import subprocess
from dataclasses import asdict
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

try:  # Script execution on VPS keeps the historical sibling import.
    from runner import CallBudget, describe_behavior_snapshot, invoke_case
except ModuleNotFoundError:  # Package import makes report projection testable.
    from .runner import CallBudget, describe_behavior_snapshot, invoke_case


_REPORT_REDACTED_BEHAVIOR_FIELDS = {
    "personification_system_prompt",
    "personification_core_values_prompt",
}


def project_behavior_for_report(
    effective_fields: dict[str, object], sources: dict[str, str],
) -> dict[str, object]:
    """Keep behavior reproducible in reports without persisting prompt bodies."""
    projected = dict(effective_fields)
    for field in _REPORT_REDACTED_BEHAVIOR_FIELDS:
        value = projected.get(field)
        if not isinstance(value, str):
            continue
        projected[field] = {
            "sha256": hashlib.sha256(value.encode("utf-8")).hexdigest(),
            "length": len(value),
            "source": str(sources.get(field, "defaults")),
        }
    return projected


async def run(args):
    artifact = Path(args.artifact_dir).resolve()
    artifact.mkdir(parents=True, exist_ok=True)
    cases = [json.loads(line) for line in Path(args.corpus).resolve().read_text(encoding="utf-8").splitlines() if line.strip()]
    if args.case:
        cases = [case for case in cases if case["id"] in args.case]
    else:
        cases = [case for case in cases if case["split"] == args.split]
    if not cases:
        raise ValueError("No matching cases")
    if args.stage_calls <= 0:
        raise ValueError("stage-calls must be positive")
    repo = Path(__file__).resolve().parents[2]
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    behavior_snapshot = describe_behavior_snapshot(args.config_path, args.behavior_snapshot) if args.behavior_source == "server" else {"effective_fields": {}, "sources": {}, "credentials_exported": False, "paths_exported": False}
    frozen_behavior = dict(behavior_snapshot["effective_fields"])
    report_behavior = project_behavior_for_report(frozen_behavior, behavior_snapshot["sources"])
    report_behavior_snapshot = dict(behavior_snapshot)
    report_behavior_snapshot["effective_fields"] = report_behavior
    manifest = {"revision": revision, "corpus_sha256": hashlib.sha256(Path(args.corpus).read_bytes()).hexdigest(),
                "runtime_path": args.runtime_path, "model": "gemini-3.8-flash-high",
                "behavior_source": args.behavior_source,
                "behavior_config": report_behavior,
                "behavior_snapshot": report_behavior_snapshot,
                "fixture_overrides": {
                    "isolated_data_dir": True,
                    "outbound_capture_bot": True,
                    "external_tool_registration": "disabled",
                    "remote_embedding": "hash_bow_forced",
                }}
    manifest_path = artifact / "manifest.json"
    if manifest_path.exists() and json.loads(manifest_path.read_text(encoding="utf-8")) != manifest:
        raise ValueError("Artifact directory belongs to a different revision or corpus; use a new directory")
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    budget_path = Path(args.budget_db).resolve()
    config_path = Path(args.config_path).resolve()
    output = artifact / "results.jsonl"
    done = set()
    if output.exists():
        done = {json.loads(line)["case_id"] for line in output.read_text(encoding="utf-8").splitlines() if line.strip()}
    os.chdir(artifact)
    os.environ["XDG_DATA_HOME"] = str(artifact / "data")
    os.environ["XDG_CACHE_HOME"] = str(artifact / "cache")
    budget = CallBudget(budget_path)
    initial = budget.snapshot()["reserved_calls"]
    consecutive_failures = 0
    for case in cases:
        if case["id"] in done:
            continue
        if budget.snapshot()["reserved_calls"] - initial >= args.stage_calls:
            break
        result = await invoke_case(case, {
            "execution_mode": "real", "config_path": str(config_path),
            "runtime_path": args.runtime_path,
            "behavior_source": args.behavior_source,
            "behavior_config": frozen_behavior,
            "behavior_snapshot_path": args.behavior_snapshot,
            "budget_db": str(budget_path), "isolated_db_path": str(artifact / case["id"]),
        })
        row = {"case_id": case["id"], "split": case["split"], "surface": case["surface"],
               "revision": revision, "corpus_sha256": manifest["corpus_sha256"],
               "behavior_config": report_behavior, "case": case, **asdict(result)}
        with output.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
            stream.flush()
        print(json.dumps({"case_id": case["id"], "status": result.status, "budget": budget.snapshot()}), flush=True)
        consecutive_failures = consecutive_failures + 1 if result.status == "failed" else 0
        if consecutive_failures >= 3:
            break
        if result.status == "budget_exhausted" or (result.status in {"failed", "blocked"} and not args.continue_after_failure):
            break


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-path", required=True)
    parser.add_argument("--artifact-dir", required=True)
    parser.add_argument("--budget-db", required=True)
    parser.add_argument("--corpus", default=str(Path(__file__).resolve().parents[2] / "tests/replay_corpus/quality_v1/cases.jsonl"))
    parser.add_argument("--case", action="append")
    parser.add_argument("--split", choices=["dev", "holdout"], default="dev")
    parser.add_argument("--stage-calls", type=int, default=50)
    parser.add_argument("--runtime-path", choices=["pipeline", "agent_fragment"], default="pipeline")
    parser.add_argument("--behavior-source", choices=["defaults", "server"], default="server")
    parser.add_argument("--behavior-snapshot", help="Explicit redacted JSON profile with a behavior object; never a production secret file")
    parser.add_argument("--continue-after-failure", action="store_true", help="Record a failed case and continue to other cases; never retry that case")
    asyncio.run(run(parser.parse_args()))
