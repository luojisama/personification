"""Small, caller-injected blind comparison helpers for quality-eval artifacts.

This module has no provider construction or HTTP client.  A caller is supplied
by the main evaluation runner, so its existing ``BudgetedCaller`` remains the
single global 1500-call accounting boundary.
"""
from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any


DIMENSIONS = ("understanding", "naturalness", "persona", "relationship", "factuality")
WINNERS = {"A", "B", "tie", "invalid"}
ZERO_TOLERANCE_FLAGS = {"safety_boundary", "fabricated_fact", "delivery_claim"}
ALLOWED_PAIR_STATUSES = {"completed", "no_reply", "capture_confirmed"}


def _read_jsonl(path: str | Path) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    rows: dict[str, dict[str, Any]] = {}
    invalid: list[dict[str, Any]] = []
    for line_number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            invalid.append({"line": line_number, "reason": "invalid_json"})
            continue
        case_id = str(row.get("case_id", row.get("id", "")) or "").strip()
        if not case_id or case_id in rows:
            invalid.append({"line": line_number, "reason": "missing_or_duplicate_case_id"})
            continue
        rows[case_id] = row
    return rows, invalid


def prepare_pairs(baseline_jsonl: str | Path, candidate_jsonl: str | Path) -> dict[str, Any]:
    """Return only real, same-case, different-revision comparisons.

    Missing outputs remain explicitly unpaired.  The function never invents a
    blank reply merely to make aggregate counts look complete.
    """
    baseline, baseline_invalid = _read_jsonl(baseline_jsonl)
    candidate, candidate_invalid = _read_jsonl(candidate_jsonl)
    pairs: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for case_id in sorted(set(baseline) | set(candidate)):
        left, right = baseline.get(case_id), candidate.get(case_id)
        if left is None or right is None:
            rejected.append({"case_id": case_id, "status": "missing_output", "missing": "baseline" if left is None else "candidate"})
            continue
        if str(left.get("status", "") or "") not in ALLOWED_PAIR_STATUSES or str(right.get("status", "") or "") not in ALLOWED_PAIR_STATUSES:
            rejected.append({"case_id": case_id, "status": "ineligible_output_status"})
            continue
        baseline_revision = str(left.get("revision", "") or "").strip()
        candidate_revision = str(right.get("revision", "") or "").strip()
        if not baseline_revision or not candidate_revision or baseline_revision == candidate_revision:
            rejected.append({"case_id": case_id, "status": "same_or_missing_revision"})
            continue
        baseline_case = left.get("case", left.get("case_data"))
        candidate_case = right.get("case", right.get("case_data"))
        if baseline_case is None or candidate_case is None or baseline_case != candidate_case:
            rejected.append({"case_id": case_id, "status": "case_content_mismatch"})
            continue
        if left.get("behavior_config") != right.get("behavior_config"):
            rejected.append({"case_id": case_id, "status": "behavior_config_mismatch"})
            continue
        for key in ("corpus_sha256", "coverage"):
            if not str(left.get(key, "") or "").strip() or not str(right.get(key, "") or "").strip() or left.get(key) != right.get(key):
                rejected.append({"case_id": case_id, "status": f"{key}_mismatch"})
                break
        else:
            pairs.append({"case_id": case_id, "case": baseline_case, "baseline": left, "candidate": right})
    return {"pairs": pairs, "unpaired": rejected, "invalid_rows": {"baseline": baseline_invalid, "candidate": candidate_invalid}}


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 3) if values else None


def report_summary(
    results: list[dict[str, Any]], *, expected_case_ids: set[str] | list[str] | None = None,
    expected_cases: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Summarize already-produced grading rows without judging any new output.

    Each row may keep case metadata beside a ``grading`` result, or contain the
    grading result directly.  Flags intentionally remain pair-level review
    signals: their lack of a baseline/candidate side makes attribution unsafe.
    """
    counts = {"graded": 0, "ungraded": 0, "order_inconsistent": 0, "tie": 0, "baseline_wins": 0, "candidate_wins": 0}
    scopes: dict[str, dict[str, dict[str, Any]]] = {"surface": {}, "category": {}}
    flagged_pairs: list[str] = []
    holdout: dict[str, dict[str, Any]] = {}
    holdout_scores: dict[str, dict[str, list[float]]] = {}
    seen_case_ids: dict[str, int] = {}
    for row in results:
        grade = row.get("grading", row) if isinstance(row, dict) else {}
        case = row.get("case", {}) if isinstance(row, dict) else {}
        metadata = {**(case if isinstance(case, dict) else {}), **(row if isinstance(row, dict) else {})}
        case_id = str(metadata.get("case_id", metadata.get("id", "")) or "")
        if case_id:
            seen_case_ids[case_id] = seen_case_ids.get(case_id, 0) + 1
        status = str(grade.get("status", "ungraded") or "ungraded") if isinstance(grade, dict) else "ungraded"
        if status == "graded":
            counts["graded"] += 1
            winner = str(grade.get("winner", "") or "")
            if winner == "tie": counts["tie"] += 1
            elif winner == "baseline": counts["baseline_wins"] += 1
            elif winner == "candidate": counts["candidate_wins"] += 1
            else: counts["ungraded"] += 1; counts["graded"] -= 1; status = "ungraded"
        else:
            counts["ungraded"] += 1
            if str(grade.get("reason", "") or "") == "order_inconsistent": counts["order_inconsistent"] += 1
        flags = grade.get("zero_tolerance_flags", []) if isinstance(grade, dict) else []
        if flags and case_id:
            flagged_pairs.append(case_id)
        if str(metadata.get("split", "") or "") == "holdout":
            category = str(metadata.get("category", "uncategorized") or "uncategorized")
            gate = holdout.setdefault(category, {"n": 0, "graded": 0, "ungraded": 0})
            gate["n"] += 1
            gate["graded" if status == "graded" else "ungraded"] += 1
        if status != "graded":
            continue
        verdicts = grade.get("verdicts", [])
        if str(metadata.get("split", "")) == "holdout":
            category = str(metadata.get("category", "uncategorized"))
            scores = holdout_scores.setdefault(category, {key: [] for key in DIMENSIONS})
            for dimension in DIMENSIONS:
                values = [item.get("scores", {}).get(dimension, {}).get("candidate") for item in verdicts if isinstance(item, dict)]
                values = [value for value in values if type(value) is int and 1 <= value <= 5]
                if values:
                    scores[dimension].append(sum(values) / len(values))
        for dimension_name, dimension_value in (("surface", str(metadata.get("surface", "unknown") or "unknown")), ("category", str(metadata.get("category", "uncategorized") or "uncategorized"))):
            bucket = scopes[dimension_name].setdefault(dimension_value, {"n": 0, "baseline": {key: [] for key in DIMENSIONS}, "candidate": {key: [] for key in DIMENSIONS}})
            bucket["n"] += 1
            for dimension in DIMENSIONS:
                baseline_scores = [item.get("scores", {}).get(dimension, {}).get("baseline") for item in verdicts if isinstance(item, dict)]
                candidate_scores = [item.get("scores", {}).get(dimension, {}).get("candidate") for item in verdicts if isinstance(item, dict)]
                baseline_values = [float(value) for value in baseline_scores if type(value) is int]
                candidate_values = [float(value) for value in candidate_scores if type(value) is int]
                if baseline_values: bucket["baseline"][dimension].append(sum(baseline_values) / len(baseline_values))
                if candidate_values: bucket["candidate"][dimension].append(sum(candidate_values) / len(candidate_values))
    dimension_means = {scope: {name: {"n": item["n"], "baseline": {key: _mean(values) for key, values in item["baseline"].items()}, "candidate": {key: _mean(values) for key, values in item["candidate"].items()}} for name, item in groups.items()} for scope, groups in scopes.items()}
    expected_metadata: dict[str, dict[str, Any]] = {}
    if expected_cases is not None:
        for item in expected_cases:
            case_id = str(item.get("case_id", item.get("id", "")) or "")
            if case_id:
                expected_metadata[case_id] = item
    expected_ids = set(str(value) for value in (expected_case_ids or [])) | set(expected_metadata)
    if expected_ids:
        for case_id, item in expected_metadata.items():
            if str(item.get("split", "") or "") == "holdout":
                holdout.setdefault(str(item.get("category", "uncategorized") or "uncategorized"), {"n": 0, "graded": 0, "ungraded": 0})
    missing_expected = sorted(case_id for case_id in expected_ids if seen_case_ids.get(case_id, 0) == 0)
    duplicate_case_ids = sorted(case_id for case_id, count in seen_case_ids.items() if count > 1)
    completeness_verified = expected_case_ids is not None or expected_cases is not None
    for category, gate in holdout.items():
        candidate_means = {key: _mean(holdout_scores.get(category, {}).get(key, [])) for key in DIMENSIONS}
        gate["candidate_means"] = candidate_means
        gate["candidate_dimension_thresholds"] = {key: candidate_means.get(key) is not None and candidate_means[key] >= 4.0 for key in DIMENSIONS}
        gate["verified"] = completeness_verified
        gate["passed"] = bool(completeness_verified and gate["ungraded"] == 0 and not missing_expected and not duplicate_case_ids and all(gate["candidate_dimension_thresholds"].values()))
    graded = counts["graded"]
    decisive = counts["baseline_wins"] + counts["candidate_wins"]
    return {**counts, "candidate_win_rate_all_graded": round(counts["candidate_wins"] / graded, 3) if graded else None, "baseline_win_rate_all_graded": round(counts["baseline_wins"] / graded, 3) if graded else None, "candidate_decisive_win_rate": round(counts["candidate_wins"] / decisive, 3) if decisive else None, "baseline_decisive_win_rate": round(counts["baseline_wins"] / decisive, 3) if decisive else None, "dimension_means": dimension_means, "holdout_category_gate": holdout, "expected_case_ids_verified": completeness_verified, "missing_expected_case_ids": missing_expected, "duplicate_case_ids": duplicate_case_ids, "flagged_pairs_for_review": sorted(set(flagged_pairs)), "flags_are_model_signals_not_confirmed_defects": True}


def _response_text(response: Any) -> str:
    if isinstance(response, dict):
        return str(response.get("content", response.get("output", "")) or "")
    return str(getattr(response, "content", "") or "")


def _parse_verdict(text: str) -> dict[str, Any] | None:
    try:
        payload = json.loads(text)
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or str(payload.get("winner", "")) not in WINNERS:
        return None
    scores = payload.get("scores")
    if not isinstance(scores, dict) or set(scores) != set(DIMENSIONS):
        return None
    normalized: dict[str, dict[str, int]] = {}
    for dimension in DIMENSIONS:
        pair = scores[dimension]
        if not isinstance(pair, dict):
            return None
        try:
            a, b = pair["A"], pair["B"]
        except (KeyError, TypeError):
            return None
        if type(a) is not int or type(b) is not int or not 1 <= a <= 5 or not 1 <= b <= 5:
            return None
        normalized[dimension] = {"A": a, "B": b}
    flags = payload.get("zero_tolerance_flags", [])
    if not isinstance(flags, list) or not all(isinstance(flag, str) and flag in ZERO_TOLERANCE_FLAGS for flag in flags):
        return None
    return {"winner": str(payload["winner"]), "scores": normalized, "zero_tolerance_flags": flags}


def _response_projection(result: Any) -> dict[str, Any]:
    """Expose only response material, never revision/status/run identity."""
    if not isinstance(result, dict):
        return {"reply": str(result or ""), "turns": []}
    turns = result.get("turns", [])
    visible_turns = [
        {"input": str(turn.get("input", "") or ""), "reply": str(turn.get("reply", "") or "")}
        for turn in (turns if isinstance(turns, list) else []) if isinstance(turn, dict)
    ]
    return {"reply": str(result.get("reply", "") or ""), "turns": visible_turns}


def _prompt(case: Any, ordered: list[tuple[str, Any]]) -> list[dict[str, str]]:
    rubric = (
        "你是盲评器，只输出 JSON。winner 为 A/B/tie/invalid；scores 必含 understanding,naturalness,persona,relationship,factuality，每项含 A/B 的 1..5 整数。"
        "锚点：1=明显违背案例或不可用，2=有重大缺陷，3=基本可用但机械/不稳，4=自然且符合大部分约束，5=具体、自然、稳定满足案例。"
        "理解评估是否回应当前意图；自然评估是否像正常群友；人格评估是否符合给定人格；关系评估是否符合已知关系且不杜撰；事实评估是否只使用案例证据。"
        "沉默须按场景判断：明确私聊/直呼要求回复时无理由沉默扣分；随机群聊或案例允许不回复时，克制沉默可得高分。"
        "zero_tolerance_flags 只可使用 safety_boundary、fabricated_fact、delivery_claim 三类模型风险标记；它们不是事实确认。"
    )
    return [{"role": "system", "content": rubric}, {"role": "user", "content": json.dumps({"case": case, "A": _response_projection(ordered[0][1]), "B": _response_projection(ordered[1][1])}, ensure_ascii=False)}]


async def grade_pair(case: Any, baseline: Any, candidate: Any, *, caller: Any, seed: int) -> dict[str, Any]:
    """Blindly grade a pair twice, reversing presentation order on call two.

    No retry occurs after any caller failure (including a timeout).  Invalid or
    non-JSON model output is ``ungraded`` rather than a fabricated default.
    """
    if not hasattr(caller, "chat_with_tools"):
        raise TypeError("caller must provide chat_with_tools; direct HTTP is unsupported")
    first = [("baseline", baseline), ("candidate", candidate)]
    if random.Random(seed).randrange(2):
        first.reverse()
    orders = (first, list(reversed(first)))
    verdicts: list[dict[str, Any]] = []
    for order in orders:
        try:
            response = await caller.chat_with_tools(_prompt(case, order), [], False)
        except Exception as exc:
            return {"status": "ungraded", "reason": f"caller_error:{type(exc).__name__}", "model_self_assessment": True, "zero_tolerance_flags": []}
        parsed = _parse_verdict(_response_text(response))
        if parsed is None or parsed["winner"] == "invalid":
            return {"status": "ungraded", "reason": "invalid_model_json", "model_self_assessment": True, "zero_tolerance_flags": []}
        mapping = {"A": order[0][0], "B": order[1][0]}
        canonical_winner = mapping.get(parsed["winner"], parsed["winner"])
        canonical_scores = {dimension: {mapping["A"]: values["A"], mapping["B"]: values["B"]} for dimension, values in parsed["scores"].items()}
        verdicts.append({"winner": canonical_winner, "scores": canonical_scores, "zero_tolerance_flags": parsed["zero_tolerance_flags"]})
    if verdicts[0]["winner"] != verdicts[1]["winner"]:
        return {"status": "ungraded", "reason": "order_inconsistent", "verdicts": verdicts, "model_self_assessment": True, "zero_tolerance_flags": sorted(set(verdicts[0]["zero_tolerance_flags"] + verdicts[1]["zero_tolerance_flags"])), "cost": "unknown"}
    winner = verdicts[0]["winner"]
    return {"status": "graded", "winner": winner, "verdicts": verdicts, "model_self_assessment": True, "zero_tolerance_flags": sorted(set(verdicts[0]["zero_tolerance_flags"] + verdicts[1]["zero_tolerance_flags"])), "cost": "unknown"}


__all__ = ["DIMENSIONS", "ZERO_TOLERANCE_FLAGS", "grade_pair", "prepare_pairs", "report_summary"]
