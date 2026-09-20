"""Grade saved pairs using the same server-local route and global call budget."""
import argparse
import asyncio
import hashlib
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.quality_eval.grading import grade_pair, prepare_pairs, report_summary
from scripts.quality_eval.runner import BudgetedCaller, CallBudget, _bootstrap_runtime, _load_fixed_gemini_route


_DEFAULT_CORPUS = Path(__file__).resolve().parents[2] / "tests/replay_corpus/quality_v1/cases.jsonl"


def _sha256_file(path: str | Path) -> str:
    return hashlib.sha256(Path(path).resolve().read_bytes()).hexdigest()


def build_grade_manifest(
    *, baseline: str | Path, candidate: str | Path, corpus: str | Path, split: str, seed: int,
) -> dict[str, object]:
    """Identify immutable grading inputs before reusing an artifact directory."""
    return {
        "schema_version": 1,
        "baseline_sha256": _sha256_file(baseline),
        "candidate_sha256": _sha256_file(candidate),
        "corpus_sha256": _sha256_file(corpus),
        "split": str(split),
        "seed": int(seed),
    }


def ensure_grade_manifest(artifact: str | Path, manifest: dict[str, object]) -> Path:
    path = Path(artifact) / "grade_manifest.json"
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        if existing != manifest:
            raise ValueError("Artifact directory belongs to different baseline, candidate, corpus, split, or seed; use a new directory")
    else:
        if (Path(artifact) / "grades.jsonl").exists():
            raise ValueError("Artifact directory has grades.jsonl without a grade manifest; use a new directory")
        path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def load_expected_cases(corpus: str | Path, split: str) -> list[dict[str, object]]:
    cases = [json.loads(line) for line in Path(corpus).resolve().read_text(encoding="utf-8").splitlines() if line.strip()]
    selected = [case for case in cases if str(case.get("split", "")) == split]
    if not selected:
        raise ValueError(f"No {split!r} cases in corpus")
    return selected


def read_grade_rows(path: str | Path) -> list[dict[str, object]]:
    output = Path(path)
    if not output.exists():
        return []
    return [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines() if line.strip()]


def select_pairs_for_expected(pairs: dict[str, object], expected_cases: list[dict[str, object]]) -> dict[str, object]:
    """Keep one requested corpus split from affecting the other split's report."""
    expected_by_id = {
        str(case.get("id", case.get("case_id", "")) or ""): case for case in expected_cases
    }
    selected_pairs: list[dict[str, object]] = []
    rejected = list(pairs.get("unpaired", []))
    for pair in list(pairs.get("pairs", [])):
        case_id = str(pair.get("case_id", "") or "")
        expected = expected_by_id.get(case_id)
        if expected is None:
            continue
        if pair.get("case") != expected:
            rejected.append({"case_id": case_id, "status": "corpus_case_mismatch"})
            continue
        selected_pairs.append(pair)
    return {
        **pairs,
        "pairs": selected_pairs,
        "unpaired": [
            item for item in rejected
            if str(item.get("case_id", "") or "") in expected_by_id
        ],
    }


def write_grade_report(*, artifact: str | Path, rows: list[dict[str, object]], expected_cases: list[dict[str, object]], pairs: dict[str, object]) -> dict[str, object]:
    report = report_summary(rows, expected_cases=expected_cases)
    report["pairing"] = {key: value for key, value in pairs.items() if key != "pairs"}
    report["unpaired_case_ids"] = sorted(
        str(item.get("case_id", "")) for item in list(pairs.get("unpaired", [])) if str(item.get("case_id", ""))
    )
    report["rejected_case_ids"] = sorted(
        str(item.get("case_id", "")) for item in list(pairs.get("rejected", [])) if str(item.get("case_id", ""))
    )
    Path(artifact, "grade_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


async def run(args):
    pairs = prepare_pairs(args.baseline, args.candidate)
    artifact = Path(args.artifact_dir).resolve()
    artifact.mkdir(parents=True, exist_ok=True)
    expected_cases = load_expected_cases(args.corpus, args.split)
    manifest = build_grade_manifest(
        baseline=args.baseline, candidate=args.candidate, corpus=args.corpus, split=args.split, seed=args.seed,
    )
    ensure_grade_manifest(artifact, manifest)
    pairs = select_pairs_for_expected(pairs, expected_cases)
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
    done = {str(row.get("case_id", "")) for row in read_grade_rows(output)}
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
        write_grade_report(
            artifact=artifact, rows=read_grade_rows(output), expected_cases=expected_cases, pairs=pairs,
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("baseline", "candidate", "artifact-dir", "config-path", "budget-db"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--seed", type=int, default=20260920)
    parser.add_argument("--corpus", default=str(_DEFAULT_CORPUS))
    parser.add_argument("--split", choices=["dev", "holdout"], default="dev")
    asyncio.run(run(parser.parse_args()))
