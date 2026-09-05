from __future__ import annotations

from plugin.personification.core.diagnostic_media_samples import (
    diagnostic_media_catalog_metadata,
    diagnostic_media_prompt,
    get_diagnostic_media_sample,
    score_custom_media_transport_response,
    score_diagnostic_media_response,
    validate_diagnostic_media_sample,
)


def test_packaged_diagnostic_media_assets_have_verified_integrity() -> None:
    audio = get_diagnostic_media_sample("audio_input")
    video = get_diagnostic_media_sample("video_input")

    assert audio is not None and audio.size_bytes <= 256 * 1024
    assert video is not None and video.size_bytes <= 128 * 1024
    assert validate_diagnostic_media_sample(audio) == (True, "builtin_sample_integrity_verified")
    assert validate_diagnostic_media_sample(video) == (True, "builtin_sample_integrity_verified")
    assert diagnostic_media_catalog_metadata("audio_input")["sample_id"] == "audio-ascending-v1"
    assert diagnostic_media_catalog_metadata("video_input")["sample_id"] == "video-rgb-v1"


def test_diagnostic_media_prompts_do_not_disclose_expected_answers_or_paths() -> None:
    for capability in ("audio_input", "video_input"):
        sample = get_diagnostic_media_sample(capability)
        assert sample is not None
        prompt = diagnostic_media_prompt(sample)
        lowered = prompt.casefold()
        assert sample.sample_id.casefold() not in lowered
        assert sample.relative_name.casefold() not in lowered
        assert str(sample.path).casefold() not in lowered
        assert sample.sha256 not in lowered

    audio_prompt = diagnostic_media_prompt(get_diagnostic_media_sample("audio_input"))  # type: ignore[arg-type]
    video_prompt = diagnostic_media_prompt(get_diagnostic_media_sample("video_input"))  # type: ignore[arg-type]
    assert "330" not in audio_prompt and "440" not in audio_prompt and "660" not in audio_prompt
    assert "red" not in video_prompt.casefold()
    assert "green" not in video_prompt.casefold()
    assert "blue" not in video_prompt.casefold()


def test_diagnostic_media_response_requires_exact_content_observation() -> None:
    audio = get_diagnostic_media_sample("audio_input")
    video = get_diagnostic_media_sample("video_input")
    assert audio is not None and video is not None

    assert score_diagnostic_media_response(audio, '{"segment_count":3,"pitch_trend":"ascending"}')
    assert not score_diagnostic_media_response(audio, "已接收")
    assert not score_diagnostic_media_response(audio, '{"segment_count":3,"pitch_trend":"descending"}')
    assert not score_diagnostic_media_response(
        audio,
        '{"segment_count":3,"pitch_trend":"ascending","comment":"ok"}',
    )

    assert score_diagnostic_media_response(
        video,
        '{"scene_count":3,"colors":["red","green","blue"],'
        '"shapes":["circle","square","triangle"]}',
    )
    assert not score_diagnostic_media_response(
        video,
        '{"scene_count":3,"colors":["blue","green","red"],'
        '"shapes":["circle","square","triangle"]}',
    )
    assert not score_diagnostic_media_response(video, "")


def test_unknown_or_cross_kind_sample_is_rejected() -> None:
    assert get_diagnostic_media_sample("audio_input", "video-rgb-v1") is None
    assert get_diagnostic_media_sample("video_input", "missing") is None
    assert diagnostic_media_catalog_metadata("reasoning") == {}


def test_custom_media_transport_response_requires_exact_boolean_contract() -> None:
    assert score_custom_media_transport_response('{"media_input_accepted":true}') is True
    assert score_custom_media_transport_response('{"media_input_accepted":false}') is False
    assert score_custom_media_transport_response("已收到媒体") is None
    assert score_custom_media_transport_response('{"media_input_accepted":"true"}') is None
    assert score_custom_media_transport_response(
        '{"media_input_accepted":true,"comment":"ok"}'
    ) is None
