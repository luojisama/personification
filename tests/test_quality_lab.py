from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from ._loader import load_personification_module

lab_mod = load_personification_module("plugin.personification.core.quality_lab")
variants = load_personification_module("plugin.personification.core.quality_variant")
length = load_personification_module("plugin.personification.core.reply_length_policy")
style = load_personification_module("plugin.personification.core.reply_style_policy")


def _config():
    return SimpleNamespace(personification_api_pools=[{"api_type": "openai", "model": "test-model", "api_url": "http://127.0.0.1:1/v1", "api_key": "private-fixture-key"}])


def _case():
    return {"surface": "private", "persona": "你是测试角色", "events": [{"kind": "message", "text": "测试上下文", "user_id": "fixture-user"}]}


def _add(lab):
    return lab.add_case({"title": "已脱敏", "case": _case(), "redact_confirmed": True})


def test_baseline_is_unchanged_and_context_resets_on_error():
    config = SimpleNamespace(personification_max_output_chars=0)
    assert length.resolve_reply_length_policy(config).max_chars == 60
    baseline = style.build_group_no_question_policy_prompt()
    with pytest.raises(RuntimeError):
        with variants.quality_variant("B"):
            assert "表达偏好" in style.build_group_no_question_policy_prompt()
            assert length.resolve_reply_length_policy(config).max_chars == 60
            raise RuntimeError("test")
    assert style.build_group_no_question_policy_prompt() == baseline


def test_experiment_removes_conflicting_inline_group_style_rules():
    context = load_personification_module("plugin.personification.handlers.reply_pipeline.pipeline_context")
    def prompt():
        return context.build_base_system_prompt(base_prompt="你是测试人格", user_name="测试用户", level_name="普通", combined_attitude="自然", emotion_block="", is_private_session=False, prelude_chunks=[], context_chunks=[], postlude_chunks=[])
    baseline = prompt()
    assert "也不要用问句把话题丢回群里" in baseline
    with variants.quality_variant("B"):
        candidate = prompt()
        assert "也不要用问句把话题丢回群里" not in candidate
        assert "能用一句说完就别说两句" not in candidate
        assert "不要硬猜" in context.build_confidence_style_instruction(.5, is_group=True)
        assert "不得猜测出处" in style.build_empty_evidence_output_policy_prompt()
    assert prompt() == baseline


@pytest.mark.parametrize("surface,direct,target", [("group", False, (20, 80)), ("group", True, (40, 180)), ("private", False, (80, 400))])
def test_soft_target_never_truncates_and_preserves_admin_ceiling(surface, direct, target):
    with variants.quality_variant("C", surface=surface, direct=direct):
        result = length.resolve_reply_length_policy(SimpleNamespace(personification_max_output_chars=0))
        assert result.soft_target == target and result.max_chars == 0
        assert len(length.truncate_reply_text("文" * 500, result.max_chars)) == 500
        assert length.resolve_reply_length_policy(SimpleNamespace(personification_max_output_chars=140)).max_chars == 140


def test_validation_requires_redaction_and_rejects_capability_imports(tmp_path):
    lab = lab_mod.QualityLab(tmp_path)
    with pytest.raises(lab_mod.QualityLabError):
        lab.add_case({"case": _case()})
    with pytest.raises(lab_mod.QualityLabError):
        lab_mod.validate_case({**_case(), "config_path": "production/env.json"})
    case = _case()
    case["events"][0]["text"] = "Authorization: Bearer verysecret"
    with pytest.raises(lab_mod.QualityLabError):
        lab_mod.validate_case(case)
    case = _case()
    case["seed"] = {"session_history": [{"role": "user", "content": "脱敏历史", "session_id": "private_fixture-user"}]}
    assert lab_mod.validate_case(case) == case


def test_preview_has_explicit_budget_and_no_credentials(tmp_path):
    lab = lab_mod.QualityLab(tmp_path)
    item = _add(lab)
    payload = {"case_ids": [item["id"]], "provider_id": "0", "request_budget": 9}
    assert lab.preview(payload, _config())["candidate_count"] == 3
    assert "private-fixture-key" not in json.dumps(lab.snapshot(_config()))
    payload.pop("request_budget")
    with pytest.raises(lab_mod.QualityLabError):
        lab.preview(payload, _config())


def test_blind_pairwise_order_scoring_and_reveal_lock(tmp_path, monkeypatch):
    lab = lab_mod.QualityLab(tmp_path)
    item = _add(lab)
    observed = []
    async def candidate(job, case, variant, route, behavior):
        observed.append((case["id"], variant))
        return {"status": "completed", "reply": "同一候选文本", "usage": {"wire_calls": 2}, "elapsed_ms": 10}
    monkeypatch.setattr(lab, "_candidate", candidate)
    async def run():
        job = await lab.start({"case_ids": [item["id"]], "provider_id": "0", "request_budget": 9, "confirmed": True}, _config())
        await lab._tasks[job["id"]]
        return lab.job(job["id"])
    job = asyncio.run(run())
    assert len(observed) == 3 and len(job["ballots"]) == 3
    assert "results" not in job
    assert all("variant" not in candidate for ballot in job["ballots"] for candidate in ballot["candidates"])
    with pytest.raises(lab_mod.QualityLabError):
        lab.report(job["id"])
    for ballot in job["ballots"]:
        score = {"ballot_id": ballot["id"], "winner": "both_bad", "dimensions": {d: 2 for d in lab_mod.DIMENSIONS}, "reason": "未接住上下文"}
        lab.score(job["id"], score)
    report = lab.report(job["id"])
    assert report["metrics"]["wire_calls"] == 6
    assert report["release_gate"] == "requires_human_review_and_holdout"
    with pytest.raises(lab_mod.QualityLabError):
        lab.score(job["id"], score)


def test_restart_does_not_replay_running_jobs(tmp_path):
    lab = lab_mod.QualityLab(tmp_path)
    lab._save("job", {"id": "job", "status": "running", "scores": [], "results": [], "ballots": []})
    restored = lab_mod.QualityLab(tmp_path)
    assert restored.job("job")["status"] == "interrupted"
    assert not restored._tasks
