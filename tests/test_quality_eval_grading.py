from __future__ import annotations

import asyncio
import json
from pathlib import Path

from scripts.quality_eval.grading import grade_pair, prepare_pairs, report_summary
from scripts.quality_eval.grading_provider import call_api


def _verdict(winner: str, value: int = 4) -> str:
    return json.dumps({"winner": winner, "scores": {key: {"A": value, "B": value} for key in ("understanding", "naturalness", "persona", "relationship", "factuality")}, "zero_tolerance_flags": ["fabricated_fact"]})


class _Caller:
    def __init__(self, values): self.values, self.calls = list(values), []
    async def chat_with_tools(self, messages, *_args):
        self.calls.append(messages)
        value = self.values.pop(0)
        if isinstance(value, BaseException): raise value
        return {"content": value}


def test_grade_pair_maps_reversed_ab_back_to_canonical_candidate() -> None:
    caller = _Caller([_verdict("A"), _verdict("B")])
    result = asyncio.run(grade_pair({"id": "c"}, {"reply": "old"}, {"reply": "new"}, caller=caller, seed=0))
    assert result["status"] == "graded" and result["winner"] == "candidate"
    assert result["model_self_assessment"] is True
    assert result["zero_tolerance_flags"] == ["fabricated_fact"]


def test_grade_pair_keeps_order_disagreement_as_tie() -> None:
    caller = _Caller([_verdict("A"), _verdict("A")])
    result = asyncio.run(grade_pair({}, "old", "new", caller=caller, seed=0))
    assert result["status"] == "ungraded" and result["reason"] == "order_inconsistent"


def test_invalid_json_is_ungraded_without_default_score() -> None:
    caller = _Caller(["not json"])
    result = asyncio.run(grade_pair({}, "old", "new", caller=caller, seed=0))
    assert result["status"] == "ungraded" and result["reason"] == "invalid_model_json"
    assert len(caller.calls) == 1


def test_invalid_winner_and_non_integer_scores_are_ungraded() -> None:
    invalid_winner = _verdict("invalid")
    float_score = json.loads(_verdict("A")); float_score["scores"]["persona"]["A"] = 4.9
    for response in (invalid_winner, json.dumps(float_score)):
        assert asyncio.run(grade_pair({}, "old", "new", caller=_Caller([response]), seed=0))["status"] == "ungraded"


def test_blind_prompt_excludes_revision_and_status_identity() -> None:
    caller = _Caller([_verdict("A"), _verdict("B")])
    asyncio.run(grade_pair({"events": [{"text": "hi"}], "trusted_persona": "自然"}, {"reply": "old", "revision": "secret-base", "status": "completed"}, {"reply": "new", "revision": "secret-candidate", "status": "failed"}, caller=caller, seed=0))
    serialized = json.dumps(caller.calls, ensure_ascii=False)
    assert "secret-base" not in serialized and "secret-candidate" not in serialized and '"status"' not in serialized


def test_timeout_is_not_retried() -> None:
    caller = _Caller([TimeoutError("deadline")])
    result = asyncio.run(grade_pair({}, "old", "new", caller=caller, seed=0))
    assert result["status"] == "ungraded" and result["reason"] == "caller_error:TimeoutError"
    assert len(caller.calls) == 1


def test_prepare_pairs_requires_real_matching_cases_and_revisions(tmp_path) -> None:
    baseline = tmp_path / "base.jsonl"; candidate = tmp_path / "candidate.jsonl"
    base_case = {"events": [{"text": "hi"}], "trusted_persona": "natural"}
    baseline.write_text(json.dumps({"case_id":"same","revision":"one","status":"completed","reply":"a","case":base_case,"corpus_sha256":"c","coverage":"full"}) + '\n' + json.dumps({"case_id":"only-base","revision":"one","status":"completed","case":base_case}) + '\n', encoding="utf-8")
    candidate.write_text(json.dumps({"case_id":"same","revision":"two","status":"completed","reply":"b","case":base_case,"corpus_sha256":"c","coverage":"full"}) + '\n' + json.dumps({"case_id":"only-candidate","revision":"two","status":"completed","case":base_case}) + '\n', encoding="utf-8")
    prepared = prepare_pairs(baseline, candidate)
    assert [pair["case_id"] for pair in prepared["pairs"]] == ["same"]
    assert {row["status"] for row in prepared["unpaired"]} == {"missing_output"}


def test_prepare_pairs_rejects_failed_and_non_equivalent_inputs(tmp_path) -> None:
    left, right = tmp_path / "left.jsonl", tmp_path / "right.jsonl"
    left.write_text('\n'.join(json.dumps(row) for row in (
        {"case_id":"x","revision":"one","status":"failed","case":{"x":1},"corpus_sha256":"a","coverage":"one"},
        {"case_id":"y","revision":"one","status":"completed","case":{"x":1},"corpus_sha256":"a","coverage":"one"},
        {"case_id":"z","revision":"one","status":"completed","case":{"x":1},"corpus_sha256":"a","coverage":"one"},
        {"case_id":"w","revision":"one","status":"completed","case":{"x":1},"corpus_sha256":"a","coverage":"one"},
    )), encoding="utf-8")
    right.write_text('\n'.join(json.dumps(row) for row in (
        {"case_id":"x","revision":"two","status":"completed","case":{"x":1},"corpus_sha256":"a","coverage":"one"},
        {"case_id":"y","revision":"two","status":"completed","case":{"x":2},"corpus_sha256":"a","coverage":"one"},
        {"case_id":"z","revision":"two","status":"completed","case":{"x":1},"corpus_sha256":"b","coverage":"one"},
        {"case_id":"w","revision":"two","status":"completed","case":{"x":1},"corpus_sha256":"a","coverage":"two"},
    )), encoding="utf-8")
    statuses = {row["status"] for row in prepare_pairs(left, right)["unpaired"]}
    assert statuses == {"ineligible_output_status", "case_content_mismatch", "corpus_sha256_mismatch", "coverage_mismatch"}


def test_promptfoo_provider_reads_artifact_without_caller(tmp_path) -> None:
    artifact = tmp_path / "grading.jsonl"; artifact.write_text('{"case_id":"c","status":"graded","winner":"candidate"}\n', encoding="utf-8")
    output = asyncio.run(call_api("", {"config": {"grading_artifact": str(artifact)}}, {"vars": {"case_id": "c"}}))
    assert json.loads(output["output"])["winner"] == "candidate"
    assert output["metadata"]["offline_artifact"] is True


def test_prepare_pairs_rejects_unknown_and_missing_coverage(tmp_path) -> None:
    left, right = tmp_path / "left.jsonl", tmp_path / "right.jsonl"
    left.write_text('{"case_id":"x","revision":"a","status":"unknown","case":{},"corpus_sha256":"h","coverage":"c"}\n{"case_id":"y","revision":"a","status":"completed","case":{},"corpus_sha256":"h","coverage":""}', encoding="utf-8")
    right.write_text('{"case_id":"x","revision":"b","status":"completed","case":{},"corpus_sha256":"h","coverage":"c"}\n{"case_id":"y","revision":"b","status":"completed","case":{},"corpus_sha256":"h","coverage":"c"}', encoding="utf-8")
    assert {row["status"] for row in prepare_pairs(left, right)["unpaired"]} == {"ineligible_output_status", "coverage_mismatch"}


def test_report_summary_counts_failures_and_holdout_coverage_without_flag_attribution() -> None:
    verdict = {"scores": {key: {"baseline": 3, "candidate": 4} for key in ("understanding", "naturalness", "persona", "relationship", "factuality")}}
    rows = [
        {"case_id":"a", "surface":"group", "category":"dialogue", "split":"holdout", "grading":{"status":"graded", "winner":"candidate", "verdicts":[verdict], "zero_tolerance_flags":["fabricated_fact"]}},
        {"case_id":"b", "surface":"private", "category":"dialogue", "split":"holdout", "grading":{"status":"graded", "winner":"tie", "verdicts":[verdict]}},
        {"case_id":"c", "surface":"group", "category":"dialogue", "split":"holdout", "grading":{"status":"ungraded", "reason":"order_inconsistent"}},
    ]
    report = report_summary(rows, expected_case_ids={"a", "b", "c"})
    assert report["graded"] == 2 and report["ungraded"] == 1 and report["order_inconsistent"] == 1
    assert report["tie"] == 1 and report["candidate_wins"] == 1
    assert report["candidate_win_rate_all_graded"] == 0.5 and report["candidate_decisive_win_rate"] == 1.0
    assert report["dimension_means"]["surface"]["group"]["candidate"]["persona"] == 4.0
    assert report["holdout_category_gate"]["dialogue"]["n"] == 3
    assert report["holdout_category_gate"]["dialogue"]["passed"] is False
    assert all(report["holdout_category_gate"]["dialogue"]["candidate_dimension_thresholds"].values())
    assert report["flagged_pairs_for_review"] == ["a"]
    assert report["flags_are_model_signals_not_confirmed_defects"] is True


def test_holdout_gate_needs_expected_manifest_and_reports_missing_duplicates() -> None:
    verdict = {"scores": {key: {"baseline": 3, "candidate": 4} for key in ("understanding", "naturalness", "persona", "relationship", "factuality")}}
    row = {"case_id":"a", "surface":"group", "category":"dialogue", "split":"holdout", "grading":{"status":"graded", "winner":"candidate", "verdicts":[verdict]}}
    unverified = report_summary([row])
    assert unverified["expected_case_ids_verified"] is False
    assert unverified["holdout_category_gate"]["dialogue"]["passed"] is False
    verified = report_summary([row, row], expected_cases=[{"id":"a", "split":"holdout", "category":"dialogue"}, {"id":"b", "split":"holdout", "category":"dialogue"}])
    assert verified["missing_expected_case_ids"] == ["b"]
    assert verified["duplicate_case_ids"] == ["a"]
    assert verified["holdout_category_gate"]["dialogue"]["passed"] is False


def test_dev_scores_cannot_hide_holdout_regression():
    dimensions = ("understanding", "naturalness", "persona", "relationship", "factuality")
    def row(case_id, split, score):
        return {"case_id": case_id, "split": split, "category": "dialogue", "status": "graded", "winner": "candidate",
                "verdicts": [{"scores": {key: {"baseline": 3, "candidate": score} for key in dimensions}}]}
    report = report_summary([row("dev", "dev", 5), row("held", "holdout", 3)], expected_case_ids={"dev", "held"})
    assert report["dimension_means"]["category"]["dialogue"]["candidate"]["persona"] == 4
    assert report["holdout_category_gate"]["dialogue"]["candidate_means"]["persona"] == 3
    assert not report["holdout_category_gate"]["dialogue"]["passed"]


def test_blind_projection_excludes_internal_turn_metadata():
    from scripts.quality_eval.grading import _response_projection
    assert _response_projection({"revision": "candidate", "reply": "回答", "turns": [
        {"input": "提问", "reply": "回答", "mode": "yaml", "trace": "internal", "delivery_unknown": False},
    ]}) == {"reply": "回答", "turns": [{"input": "提问", "reply": "回答"}]}
