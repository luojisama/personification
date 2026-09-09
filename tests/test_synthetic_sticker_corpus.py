from __future__ import annotations
import json
from pathlib import Path
from PIL import Image
from ._loader import load_personification_module

replay = load_personification_module("plugin.personification.scripts.replay_sticker_vision_api")

def test_generated_synthetic_corpus_has_static_and_ordered_dynamic_cases(tmp_path, monkeypatch) -> None:
    import sys
    generator = load_personification_module("plugin.personification.scripts.generate_synthetic_sticker_corpus")
    root = tmp_path / "synthetic"
    monkeypatch.setattr(sys, "argv", ["generate", "--output", str(root)])
    assert generator.main() == 0
    manifest = root / "manifest.json"
    rows=replay.cases(manifest)
    assert len(rows)==50
    assert {row["stratum"] for row in rows} == {
        "greeting", "irony", "comfort", "refusal", "closing", "agreement",
        "surprise", "apology", "uncertain_ocr", "celebration",
    }
    assert all(sum(item["stratum"]==name for item in rows)==5 for name in {row["stratum"] for row in rows})
    assert sum(row["kind"]=="static" for row in rows)==20
    dynamic=[row for row in rows if row["kind"]=="dynamic"]
    assert len(dynamic)==30
    for row in dynamic:
        with Image.open(root/row["image"]) as image: assert image.n_frames==3
        ref,detail=replay.image_url(manifest,row["image"])
        assert ref.startswith("data:image/png;base64,")
        assert detail["transport"]=="gif_contact_sheet" and detail["sampled_frames"]==[0,1,2]
        assert row["expected"]["objective"]["motion_direction"] in {"left","right","up","down"}


def test_v2_score_uses_visible_ocr_motion_and_schema_not_subjective_intent() -> None:
    expected={"objective":{"ocr_any_of":["不 行"],"motion_any_of":["向左","左"],"clarity":"clear","required_output_fields":["ocr_text","subject_action","visual_confidence"]}}
    actual={"ocr_text":"不 行","subject_action":"图形向左移动","visual_confidence":0.8,"social_intent":"任意模型解释"}
    assert replay.score(expected,actual)=={"ocr_visible_text":1.0,"gif_motion_direction":1.0,"schema_fields":1.0}


def test_v2_unclear_ocr_requires_uncertainty_not_an_exact_guess() -> None:
    expected={"objective":{"ocr_any_of":[],"clarity":"uncertain","required_output_fields":["ocr_text","description","visual_confidence"]}}
    actual={"ocr_text":"","description":"文字模糊看不清","visual_confidence":0.4}
    assert replay.score(expected,actual)["ocr_visible_text"] is None
    assert replay.score(expected,actual)["uncertainty_preserved"] == 1.0
