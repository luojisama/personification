from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from ._loader import load_personification_module


sticker_impl = load_personification_module(
    "plugin.personification.skills.skillpacks.sticker_tool.scripts.impl"
)
vision_impl = load_personification_module(
    "plugin.personification.skills.skillpacks.vision_analyze.scripts.impl"
)
turn_media = load_personification_module("plugin.personification.core.turn_media")


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


def test_onebot_file_video_reaches_qwen_web_after_lazy_resolution(
    monkeypatch,
    tmp_path: Path,
) -> None:  # noqa: ANN001
    video_path = tmp_path / "qq-file-video.mp4"
    video_path.write_bytes(b"video")
    refs = turn_media.extract_media_from_message(
        [
            SimpleNamespace(
                type="file",
                data={"file": "opaque-qq-file", "name": "qq-file-video.mp4"},
            )
        ],
        origin="quoted",
        owner_user_id="sender",
        message_id="quoted-message",
    )

    class _Bot:
        async def get_file(self, **kwargs):  # noqa: ANN003, ANN201
            assert kwargs == {"file": "opaque-qq-file"}
            return {"file": str(video_path), "file_name": video_path.name}

    resolved = asyncio.run(turn_media.resolve_onebot_media_refs(refs, _Bot()))
    assert resolved[0].origin == "quoted"
    assert resolved[0].owner_user_id == "sender"

    captured: dict[str, object] = {}

    async def _video(**kwargs):  # noqa: ANN003, ANN202
        captured.update(kwargs)
        kwargs["route_attempts"].append(
            {
                "route": "video_qwen_web",
                "status": "ok",
                "elapsed_ms": 1000,
                "diagnostic_code": "",
            }
        )
        return '{"scene_summary":"千问已理解文件视频"}', "video_qwen_web"

    monkeypatch.setattr(vision_impl, "analyze_videos_with_route_or_fallback", _video)
    runtime = SimpleNamespace(
        plugin_config=SimpleNamespace(personification_fallback_enabled=False),
        vision_caller=None,
    )
    token = sticker_impl.set_current_image_context(
        [],
        "概括引用的视频文件",
        [resolved[0].ref],
        [],
    )
    try:
        result = json.loads(
            asyncio.run(
                vision_impl.analyze_images(
                    runtime=runtime,
                    query="概括引用的视频文件",
                )
            )
        )
    finally:
        sticker_impl.reset_current_image_context(token)

    assert captured["video_refs"] == [str(video_path.resolve())]
    assert result["scene_summary"] == "千问已理解文件视频"
    assert result["analysis_route"] == "video_qwen_web"
    assert result["media_routes"][0]["selected_route"] == "video_qwen_web"
