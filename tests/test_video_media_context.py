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


def test_vision_result_records_gemini_web_route_fallback(monkeypatch) -> None:  # noqa: ANN001
    async def _video(**kwargs):  # noqa: ANN003, ANN202
        kwargs["route_attempts"].extend(
            [
                {"route": "video_primary", "status": "unsupported", "elapsed_ms": 1, "diagnostic_code": ""},
                {"route": "video_gemini_web", "status": "failed", "elapsed_ms": 2, "diagnostic_code": "gemini_web_network_risk_detected"},
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
    assert route["diagnostic_codes"] == ["gemini_web_route_fallback_used"]


def test_onebot_file_video_reaches_vision_route_only_after_local_materialization(
    monkeypatch,
    tmp_path,
) -> None:  # noqa: ANN001
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
        async def call_api(self, api: str, **kwargs):  # noqa: ANN003, ANN201
            if api == "get_file":
                assert kwargs == {"file": "opaque-qq-file"}
                return {"file": "C:\\napcat-host\\qq-file-video.mp4"}
            assert api == "get_private_file_url"
            assert kwargs == {"file_id": "opaque-qq-file"}
            return {
                "data": {
                    "url": "https://multimedia.nt.qq.com.cn/download/qq-file-video"
                }
            }

    async def _download(_url, destination, **_kwargs):  # noqa: ANN001, ANN003, ANN202
        path = type(tmp_path)(destination)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"qq-video")
        return SimpleNamespace(
            path=path,
            content_type="video/mp4",
            final_url="",
            size=len(b"qq-video"),
        )

    monkeypatch.setattr(turn_media, "download_public_media_to_path", _download)
    lease = asyncio.run(
        turn_media.materialize_onebot_media_refs(
            refs,
            _Bot(),
            SimpleNamespace(
                personification_data_dir=str(tmp_path / "data"),
                personification_video_max_bytes=1024 * 1024,
                personification_video_download_timeout=30,
            ),
            None,
            30,
        )
    )
    resolved = lease.refs
    assert resolved[0].origin == "quoted"
    assert resolved[0].owner_user_id == "sender"
    assert not resolved[0].ref.startswith("http")

    captured: dict[str, object] = {}

    async def _video(**kwargs):  # noqa: ANN003, ANN202
        captured.update(kwargs)
        kwargs["route_attempts"].append(
            {
                "route": "video_gemini_web",
                "status": "ok",
                "elapsed_ms": 1000,
                "diagnostic_code": "",
            }
        )
        return '{"scene_summary":"Gemini已理解文件视频"}', "video_gemini_web"

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

    assert captured["video_refs"] == [resolved[0].ref]
    assert result["scene_summary"] == "Gemini已理解文件视频"
    assert result["analysis_route"] == "video_gemini_web"
    assert result["media_routes"][0]["selected_route"] == "video_gemini_web"
    assert result["media_routes"][0]["source_kind"] == "local"
    assert result["media_routes"][0]["provider_transport"] == "local_file"
    assert result["media_routes"][0]["size_bytes"] == len(b"qq-video")
    runtime_dir = lease.runtime_dir
    lease.cleanup()
    assert runtime_dir is not None and not runtime_dir.exists()


def test_invalid_explicit_video_token_does_not_mask_resolved_current_video(
    monkeypatch,
) -> None:  # noqa: ANN001
    captured: dict[str, object] = {}

    async def _video(**kwargs):  # noqa: ANN003, ANN202
        captured.update(kwargs)
        kwargs["route_attempts"].append(
            {"route": "video_test", "status": "ok", "elapsed_ms": 1, "diagnostic_code": ""}
        )
        return '{"scene_summary":"当前视频已送入视觉路由"}', "video_test"

    monkeypatch.setattr(vision_impl, "analyze_videos_with_route_or_fallback", _video)
    runtime = SimpleNamespace(
        plugin_config=SimpleNamespace(personification_fallback_enabled=False),
        vision_caller=None,
    )
    token = sticker_impl.set_current_image_context(
        [],
        "概括视频",
        ["https://cdn.example/resolved-video.mp4"],
        [],
    )
    try:
        result = json.loads(
            asyncio.run(
                vision_impl.analyze_images(
                    runtime=runtime,
                    query="概括视频",
                    videos=["media_opaque_provenance_token"],
                )
            )
        )
    finally:
        sticker_impl.reset_current_image_context(token)

    assert captured["video_refs"] == [
        "https://cdn.example/resolved-video.mp4",
    ]
    assert result["scene_summary"] == "当前视频已送入视觉路由"


def test_vision_tool_strips_markdown_from_model_evidence(monkeypatch) -> None:  # noqa: ANN001
    async def _video(**kwargs):  # noqa: ANN003, ANN202
        return (
            '{"scene_summary":"## 场景\\n- **楼梯战斗**",'
            '"visual_evidence":["1. **角色拿着弓**"],'
            '"ambiguity_notes":["<think>hidden</think>低"]}',
            "video_primary",
        )

    monkeypatch.setattr(vision_impl, "analyze_videos_with_route_or_fallback", _video)
    runtime = SimpleNamespace(
        plugin_config=SimpleNamespace(personification_fallback_enabled=False),
        vision_caller=None,
    )
    result = json.loads(
        asyncio.run(
            vision_impl.analyze_images(
                runtime=runtime,
                query="描述视频",
                videos=["https://cdn.example/video.mp4"],
            )
        )
    )

    visible = json.dumps(result, ensure_ascii=False)
    assert "##" not in visible
    assert "**" not in visible
    assert "<think>" not in visible
    assert "楼梯战斗" in result["scene_summary"]
