from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from ._loader import load_personification_module


sticker_impl = load_personification_module(
    "plugin.personification.skills.skillpacks.sticker_tool.scripts.impl"
)
vision_impl = load_personification_module(
    "plugin.personification.skills.skillpacks.vision_analyze.scripts.impl"
)


def test_current_media_context_carries_and_resets_video_refs() -> None:
    token = sticker_impl.set_current_image_context(
        ["data:image/png;base64,AA=="],
        "看这个",
        ["https://cdn.example/video.mp4"],
        ["C:\\tmp\\voice.wav"],
    )
    try:
        assert sticker_impl.get_current_video_urls() == ["https://cdn.example/video.mp4"]
        assert sticker_impl.get_current_image_urls() == ["data:image/png;base64,AA=="]
        assert sticker_impl.get_current_audio_urls() == ["C:\\tmp\\voice.wav"]
    finally:
        sticker_impl.reset_current_image_context(token)
    assert sticker_impl.get_current_video_urls() == []
    assert sticker_impl.get_current_audio_urls() == []


def test_vision_result_records_qwen_web_route_fallback(monkeypatch) -> None:  # noqa: ANN001
    async def _video(**kwargs):  # noqa: ANN003, ANN202
        kwargs["route_attempts"].extend(
            [
                {"route": "video_primary", "status": "unsupported", "elapsed_ms": 1, "diagnostic_code": ""},
                {"route": "video_qwen_web", "status": "failed", "elapsed_ms": 2, "diagnostic_code": "qwen_web_network_risk_detected"},
                {"route": "video_official_api", "status": "ok", "elapsed_ms": 3, "diagnostic_code": ""},
            ]
        )
        return '{"scene_summary":"正式 API 降级结果"}', "video_qwen_omni"

    monkeypatch.setattr(vision_impl, "analyze_videos_with_route_or_fallback", _video)
    runtime = SimpleNamespace(
        plugin_config=SimpleNamespace(personification_fallback_enabled=False),
        vision_caller=None,
    )

    result = json.loads(
        asyncio.run(
            vision_impl.analyze_images(
                runtime=runtime,
                query="理解视频",
                videos=["https://cdn.example/video.mp4"],
            )
        )
    )

    route = result["media_routes"][0]
    assert route["selected_route"] == "video_qwen_omni"
    assert route["diagnostic_codes"] == ["qwen_web_route_fallback_used"]
