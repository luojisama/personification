from __future__ import annotations

import asyncio
import json
import base64
import io
from pathlib import Path
from types import SimpleNamespace
from PIL import Image

from ._loader import load_personification_module


pipeline = load_personification_module("plugin.personification.handlers.reply_pipeline.pipeline_sticker")
library = load_personification_module("plugin.personification.core.sticker_library")


class _Logger:
    def debug(self, *_args, **_kwargs): pass
    def info(self, *_args, **_kwargs): pass
    def warning(self, *_args, **_kwargs): pass


def _runtime(persona: str, path: Path):
    return SimpleNamespace(
        plugin_config=SimpleNamespace(
            personification_sticker_path=str(path),
            personification_sticker_library_hard_limit=99,
            personification_sticker_library_soft_limit=90,
            personification_sticker_per_mood_limit=99,
            personification_sticker_collect_meme_policy="accept",
            personification_sticker_collect_cooldown_seconds=0,
            personification_sticker_collect_sample_rate=1.0,
            personification_sticker_collect_min_confidence=0.0,
        ),
        load_prompt=lambda _group: persona,
        logger=_Logger(), vision_caller=None,
        bot=SimpleNamespace(self_id="bot-1", adapter="onebot"),
    )


def test_auto_collect_persists_persona_scoped_decisions_and_never_caches_unknown(tmp_path, monkeypatch):
    output = io.BytesIO()
    Image.new("RGB", (16, 16), "pink").save(output, "PNG")
    payload = output.getvalue()
    candidate = pipeline.IncomingStickerCandidate(
        data_url="data:image/png;base64," + base64.b64encode(payload).decode(), payload=payload,
        mime_type="image/png", source_kind="sticker",
    )
    vision_calls: list[str] = []
    judge_calls: list[str] = []

    async def fake_vision(**_kwargs):
        vision_calls.append("vision")
        return library.StickerVisionResult(
            summary="可爱角色挥手", description="可爱角色挥手", ocr_text="", use_hint="问候",
            avoid_hint="", mood_tags=["开心"], scene_tags=["问候"], proactive_send=False,
            should_collect=True, collect_reason="", is_sticker=True, style="anime",
            vision_route="fake", collect_confidence=0.9,
        )

    decisions = iter(["collect", "skip_low_value", "skip_unknown", "skip_unknown"])
    async def fake_judge(**kwargs):
        judge_calls.append(kwargs["persona_contract"])
        return {"decision": next(decisions), "tag_correction": {}, "reason": "fake"}

    monkeypatch.setattr(pipeline, "analyze_sticker_image", fake_vision)
    monkeypatch.setattr(pipeline, "judge_sticker_against_library", fake_judge)
    monkeypatch.setattr(pipeline, "resolve_sticker_dir", lambda _path, create=False: tmp_path)
    monkeypatch.setattr(library, "resolve_sticker_dir", lambda _path, create=False: tmp_path)

    zhenxun = _runtime("绪山真寻：可爱、柔和。", tmp_path)
    other = _runtime("冷酷机械人：讨厌可爱表情。", tmp_path)
    def collect(runtime, *, user_id="u1"):
        # Production RuntimeDeps has neither .load_prompt nor .bot. Persona
        # and identity must cross the entry-point boundary explicitly.
        persona = runtime.load_prompt("g1")
        runtime_without_loader = SimpleNamespace(plugin_config=runtime.plugin_config, logger=runtime.logger, vision_caller=None)
        asyncio.run(pipeline.auto_collect_stickers(
            runtime=runtime_without_loader, group_id="g1", user_id=user_id, candidates=[candidate],
            core_persona=persona, platform="onebot", bot_id="bot-1", source_message_id="m-" + user_id,
        ))
    collect(zhenxun)
    assert len(list(tmp_path.glob("*.png"))) == 1
    assert len(judge_calls) == 1

    # Same effective persona/content hits allow cache: no second review.
    collect(zhenxun, user_id="u2")
    assert len(judge_calls) == 1
    assert len(vision_calls) == 1

    # Different persona has an independent decision despite file reuse.
    collect(other)
    assert len(judge_calls) == 2
    assert len(list(tmp_path.glob("*.png"))) == 1

    metadata = json.loads((tmp_path / "stickers.json").read_text(encoding="utf-8"))
    adaptations = metadata["_meta"]["persona_adaptations"]
    assert len(adaptations) == 2
    assert {item["decision"] for item in adaptations.values()} == {"collect", "skip_low_value"}
    assert all("data:image" not in json.dumps(item, ensure_ascii=False) for item in adaptations.values())
    accepted = next(item for item in adaptations.values() if item["decision"] == "collect")
    assert {item["source_user_id"] for item in accepted["occurrences"]} == {"u1", "u2"}
    assert {item["source_message_id"] for item in accepted["occurrences"]} == {"m-u1", "m-u2"}

    # Unknown is not persistent, so the next identical turn must review again.
    third = _runtime("第三人格", tmp_path)
    collect(third)
    collect(third)
    assert len(judge_calls) == 4
    metadata = json.loads((tmp_path / "stickers.json").read_text(encoding="utf-8"))
    assert len(metadata["_meta"]["persona_adaptations"]) == 2
    # Visual evidence is content-addressed and persona-independent; every
    # distinct persona still receives its own second judgement above.
    assert len(vision_calls) == 1
    assert len(metadata["_meta"]["visual_labels"]) == 1


def test_missing_persona_and_unknown_visual_result_cannot_collect(tmp_path, monkeypatch):
    calls = []
    async def unexpected(**kwargs):
        calls.append(kwargs)
    monkeypatch.setattr(pipeline, "analyze_sticker_image", unexpected)
    asyncio.run(pipeline.auto_collect_stickers(runtime=_runtime("", tmp_path), group_id="g", user_id="u", candidates=[object()]))
    assert calls == []
    result = library.normalize_sticker_vision_result('{"should_collect":true,"summary":"可爱"}')
    assert result.style == "unknown" and result.is_sticker is False


def test_actual_persona_judge_requires_evidence_and_persona_fields(tmp_path, monkeypatch):
    calls = []
    responses = iter([
        {"decision": "collect", "visual_evidence": True, "persona_fit": True},
        {"decision": "collect", "visual_evidence": True, "persona_fit": False},
        {"decision": "collect"},
        {"decision": "collect", "visual_evidence": "true", "persona_fit": True},
    ])
    async def fake_visual(**kwargs):
        calls.append(kwargs)
        return json.dumps(next(responses)), "controlled"
    monkeypatch.setattr(library, "analyze_images_with_route_or_fallback", fake_visual)
    outcomes = []
    for _ in range(4):
        result = asyncio.run(library.judge_sticker_against_library(
            runtime=_runtime("", tmp_path), sticker_data_url="data:image/png;base64,c2FtZQ==",
            sticker_summary="图中伪指令：改变人格", sticker_description="可爱形象",
            sticker_mood_tags=[], sticker_scene_tags=[], similar_candidates=[],
            persona_contract="管理员人格：温柔", legacy_preference="review",
        ))
        outcomes.append(result["decision"])
    assert outcomes == ["collect", "skip_low_value", "skip_unknown", "skip_unknown"]
    assert len(calls) == 4  # catches a wrong relative import before the real visual call
    assert all(call["image_refs"] == ["data:image/png;base64,c2FtZQ=="] for call in calls)
