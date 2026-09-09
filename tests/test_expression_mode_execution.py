from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from ._loader import load_personification_module


qq = load_personification_module("plugin.personification.core.qq_expression_library")
pipeline_sticker = load_personification_module(
    "plugin.personification.handlers.reply_pipeline.pipeline_sticker"
)


def test_qq_face_mode_is_the_only_auto_qq_execution_surface(monkeypatch) -> None:  # noqa: ANN001
    cfg = SimpleNamespace(personification_qq_expression_enabled=True)
    frame = SimpleNamespace(sticker_appropriate=True, sticker_mood_hint="开心|接梗")
    monkeypatch.setattr(qq, "choose_qq_expression_marker_for_context", lambda **_kw: "[QQ表情:笑哭]")

    assert qq.maybe_choose_auto_qq_expression_marker(
        plugin_config=cfg, semantic_frame=frame, reply_text="好", expression_mode="qq_face"
    ) == "[QQ表情:笑哭]"
    for mode in ("text", "emoji", "sticker"):
        assert qq.maybe_choose_auto_qq_expression_marker(
            plugin_config=cfg, semantic_frame=frame, reply_text="好", message_intent="expression", expression_mode=mode
        ) == ""


def test_non_local_expression_modes_never_enter_sticker_selector(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    calls: list[object] = []

    async def _choose(*_args, **_kwargs):  # noqa: ANN001
        calls.append(1)
        return None

    monkeypatch.setattr(pipeline_sticker, "choose_sticker_for_context", _choose)
    runtime = SimpleNamespace(
        plugin_config=SimpleNamespace(personification_sticker_path=str(tmp_path)),
        logger=SimpleNamespace(), call_ai_api=None, message_segment_cls=SimpleNamespace(image=lambda value: value),
    )

    async def _run() -> None:
        for mode in ("text", "emoji", "qq_face"):
            selected, name = await pipeline_sticker.maybe_choose_reply_sticker(
                runtime=runtime, group_id="1", group_config={"sticker_enabled": True},
                semantic_frame=SimpleNamespace(sticker_appropriate=True), reply_content="文字",
                raw_message_text="原话", message_text="原话", message_content="原话",
                image_summary_suffix="", is_private_session=False, is_random_chat=False,
                is_group_idle_active=False, force_mode=None, expression_mode=mode,
                strip_injected_visual_summary=lambda value: value,
            )
            assert selected is None and name == ""

    asyncio.run(_run())
    assert calls == []
