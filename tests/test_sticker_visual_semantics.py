from __future__ import annotations

import json

import pytest

from ._loader import load_personification_module


library = load_personification_module("plugin.personification.core.sticker_library")
curator = load_personification_module("plugin.personification.core.sticker_curator")
qq = load_personification_module("plugin.personification.core.qq_expression_library")


# A deterministic 50-case synthetic corpus.  It tests the schema boundary and
# selection facts without pretending that local fixtures prove real vision API
# quality; production visual validation remains a separate acceptance layer.
CASES = [(f"主体{i}挥手", mood, scene) for i, (mood, scene) in enumerate(
    [("开心", "打招呼"), ("无语", "吐槽"), ("惊讶", "表达惊讶"), ("害羞", "撒娇"), ("赞同", "表达赞同")] * 10,
    start=1,
)]


@pytest.mark.parametrize(("action", "mood", "scene"), CASES)
def test_synthetic_visual_semantics_preserve_explicit_labels(action: str, mood: str, scene: str) -> None:
    raw = json.dumps({
        "summary": action,
        "description": f"{action}并带有文字",
        "ocr_text": "收到",
        "subject_action": action,
        "animation_progression": "第1帧抬手，第3帧挥手",
        "literal_emotion": mood,
        "social_intent": scene,
        "suitable_contexts": [scene],
        "unsuitable_contexts": ["严肃告别"],
        "visual_confidence": 0.82,
        "mood_tags": [mood], "scene_tags": [scene],
        "is_sticker": True, "style": "anime", "should_collect": True,
        "collect_confidence": 0.82,
    }, ensure_ascii=False)
    parsed = library.normalize_sticker_vision_result(raw)
    assert parsed.subject_action == action
    assert parsed.animation_progression.startswith("第1帧")
    assert parsed.mood_tags == [mood]
    assert parsed.scene_tags == [scene]
    assert parsed.visual_confidence == 0.82


def test_visual_cache_key_changes_with_model_or_prompt_version() -> None:
    digest = "a" * 64
    assert library.visual_label_cache_key(digest, "vision-a", "v1") != library.visual_label_cache_key(digest, "vision-b", "v1")
    assert library.visual_label_cache_key(digest, "vision-a", "v1") != library.visual_label_cache_key(digest, "vision-a", "v2")


def test_text_only_curator_keeps_low_confidence_asset_and_requests_visual_relabel(tmp_path) -> None:  # noqa: ANN001
    (tmp_path / "unclear.png").write_bytes(b"not-used-by-curator")
    metadata = {"unclear.png": {"description": "模糊", "visual_confidence": 0.2, "mood_tags": [], "scene_tags": []}}
    result = curator.CurationResult()
    curator._apply_curation_action(
        {"file_name": "unclear.png", "action": "remove", "target_file": "", "new_tags": {}, "reason": "文本猜测"},
        sticker_dir=tmp_path, metadata=metadata, result=result, log_target=[],
    )
    assert (tmp_path / "unclear.png").exists()
    assert metadata["unclear.png"]["needs_visual_relabel"] is True
    assert result.remove_count == 0


def test_native_auto_expression_requires_structured_expression_intent() -> None:
    cfg = type("Cfg", (), {"personification_qq_expression_enabled": True, "personification_qq_expression_probability": 1.0})()
    frame = type("Frame", (), {"sticker_appropriate": True})()
    assert qq.maybe_choose_auto_qq_expression_marker(plugin_config=cfg, semantic_frame=frame, reply_text="好", message_intent="banter") == ""
