"""Offline coverage for grade-run artifact identity and completeness reporting."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


def _load_grade_run():
    path = Path(__file__).resolve().parents[1] / "scripts" / "quality_eval" / "grade_run.py"
    spec = importlib.util.spec_from_file_location("personification_grade_run", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


grade_run = _load_grade_run()


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def test_grade_manifest_allows_same_input_resume_and_rejects_seed_or_content_change(tmp_path: Path) -> None:
    baseline, candidate = tmp_path / "baseline.jsonl", tmp_path / "candidate.jsonl"
    corpus = tmp_path / "cases.jsonl"
    baseline.write_text('{"case_id":"a"}\n', encoding="utf-8")
    candidate.write_text('{"case_id":"a"}\n', encoding="utf-8")
    corpus.write_text('{"id":"a","split":"dev"}\n', encoding="utf-8")
    artifact = tmp_path / "artifact"
    artifact.mkdir()
    manifest = grade_run.build_grade_manifest(
        baseline=baseline, candidate=candidate, corpus=corpus, split="dev", seed=7,
    )

    path = grade_run.ensure_grade_manifest(artifact, manifest)
    assert grade_run.ensure_grade_manifest(artifact, manifest) == path
    assert json.loads(path.read_text(encoding="utf-8"))["seed"] == 7

    changed_seed = grade_run.build_grade_manifest(
        baseline=baseline, candidate=candidate, corpus=corpus, split="dev", seed=8,
    )
    try:
        grade_run.ensure_grade_manifest(artifact, changed_seed)
    except ValueError as exc:
        assert "baseline, candidate, corpus, split, or seed" in str(exc)
    else:
        raise AssertionError("changed seed must not reuse grades.jsonl")

    candidate.write_text('{"case_id":"a","revision":"other"}\n', encoding="utf-8")
    changed_input = grade_run.build_grade_manifest(
        baseline=baseline, candidate=candidate, corpus=corpus, split="dev", seed=7,
    )
    try:
        grade_run.ensure_grade_manifest(artifact, changed_input)
    except ValueError:
        pass
    else:
        raise AssertionError("changed candidate content must not reuse grades.jsonl")

    changed_split = grade_run.build_grade_manifest(
        baseline=baseline, candidate=candidate, corpus=corpus, split="holdout", seed=7,
    )
    try:
        grade_run.ensure_grade_manifest(artifact, changed_split)
    except ValueError:
        pass
    else:
        raise AssertionError("changed split must not reuse grades.jsonl")

    corpus.write_text('{"id":"a","split":"dev","changed":true}\n', encoding="utf-8")
    changed_corpus = grade_run.build_grade_manifest(
        baseline=baseline, candidate=candidate, corpus=corpus, split="dev", seed=7,
    )
    try:
        grade_run.ensure_grade_manifest(artifact, changed_corpus)
    except ValueError:
        pass
    else:
        raise AssertionError("changed corpus content must not reuse grades.jsonl")


def test_grade_manifest_refuses_legacy_grades_without_identity(tmp_path: Path) -> None:
    artifact = tmp_path / "legacy"
    artifact.mkdir()
    (artifact / "grades.jsonl").write_text('{"case_id":"a"}\n', encoding="utf-8")
    try:
        grade_run.ensure_grade_manifest(artifact, {"schema_version": 1, "seed": 1})
    except ValueError as exc:
        assert "without a grade manifest" in str(exc)
    else:
        raise AssertionError("legacy grades cannot acquire an unverifiable manifest")


def test_grade_report_uses_explicit_split_manifest_and_exposes_unpaired_missing(tmp_path: Path) -> None:
    corpus = tmp_path / "cases.jsonl"
    _write_jsonl(corpus, [
        {"id": "dev-a", "split": "dev", "surface": "group", "category": "chat"},
        {"id": "hold-b", "split": "holdout", "surface": "private", "category": "chat"},
    ])
    expected = grade_run.load_expected_cases(corpus, "dev")
    assert [case["id"] for case in expected] == ["dev-a"]
    artifact = tmp_path / "artifact"
    artifact.mkdir()
    report = grade_run.write_grade_report(
        artifact=artifact,
        rows=[],
        expected_cases=expected,
        pairs={"pairs": [], "unpaired": [{"case_id": "dev-a", "status": "missing_output"}], "invalid_rows": {}},
    )

    assert report["expected_case_ids_verified"] is True
    assert report["missing_expected_case_ids"] == ["dev-a"]
    assert report["unpaired_case_ids"] == ["dev-a"]
    assert json.loads((artifact / "grade_report.json").read_text(encoding="utf-8"))["graded"] == 0


def test_pair_selection_cannot_grade_another_split_or_same_id_old_corpus_case() -> None:
    expected_holdout = {"id": "hold-b", "split": "holdout", "prompt": "current corpus"}
    selected = grade_run.select_pairs_for_expected(
        {
            "pairs": [
                {"case_id": "dev-a", "case": {"id": "dev-a", "split": "dev"}},
                {"case_id": "hold-b", "case": {"id": "hold-b", "split": "holdout", "prompt": "old corpus"}},
            ],
            "unpaired": [{"case_id": "dev-a"}, {"case_id": "hold-b"}],
            "invalid_rows": {},
        },
        [expected_holdout],
    )
    assert selected["pairs"] == []
    assert selected["unpaired"] == [
        {"case_id": "hold-b"}, {"case_id": "hold-b", "status": "corpus_case_mismatch"},
    ]


def test_expected_cases_reject_empty_requested_split(tmp_path: Path) -> None:
    corpus = tmp_path / "cases.jsonl"
    _write_jsonl(corpus, [{"id": "a", "split": "dev"}])
    try:
        grade_run.load_expected_cases(corpus, "holdout")
    except ValueError as exc:
        assert "holdout" in str(exc)
    else:
        raise AssertionError("empty split must not be reported as a complete run")
