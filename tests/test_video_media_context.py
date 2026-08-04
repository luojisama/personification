from __future__ import annotations

from ._loader import load_personification_module


sticker_impl = load_personification_module(
    "plugin.personification.skills.skillpacks.sticker_tool.scripts.impl"
)


def test_current_media_context_carries_and_resets_video_refs() -> None:
    token = sticker_impl.set_current_image_context(
        ["data:image/png;base64,AA=="],
        "看这个",
        ["https://cdn.example/video.mp4"],
    )
    try:
        assert sticker_impl.get_current_video_urls() == ["https://cdn.example/video.mp4"]
        assert sticker_impl.get_current_image_urls() == ["data:image/png;base64,AA=="]
    finally:
        sticker_impl.reset_current_image_context(token)
    assert sticker_impl.get_current_video_urls() == []
