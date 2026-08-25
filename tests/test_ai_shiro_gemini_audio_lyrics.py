from __future__ import annotations

import base64
import io
import importlib.util
import json
from pathlib import Path
import sys

import pytest


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "test_ai_shiro_gemini_audio_lyrics.py"
SPEC = importlib.util.spec_from_file_location("test_ai_shiro_gemini_audio_lyrics_script", SCRIPT_PATH)
assert SPEC and SPEC.loader
audio_lyrics = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = audio_lyrics
SPEC.loader.exec_module(audio_lyrics)


def test_ffmpeg_command_is_audio_only(tmp_path: Path) -> None:
    command = audio_lyrics.build_ffmpeg_audio_command(
        "ffmpeg", tmp_path / "source.mp4", tmp_path / "audio.mp3", 64
    )
    assert command[command.index("-map") + 1] == "0:a:0?"
    assert "-vn" in command
    assert "0:v" not in command
    assert "-frames:v" not in command
    assert "image2" not in command
    assert command[-1].endswith("audio.mp3")


def test_ffprobe_command_selects_only_the_first_audio_stream(tmp_path: Path) -> None:
    command = audio_lyrics.build_ffprobe_audio_command("ffprobe", tmp_path / "source.mp4")
    assert command[command.index("-select_streams") + 1] == "a:0"
    assert "v:0" not in command
    assert "show_frames" not in command


def test_parse_lyrics_response_accepts_fenced_json() -> None:
    payload = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {
                            "text": "```json\n{\"lyrics\":[\"一\",\"二\",\"三\",\"四\"]}\n```"
                        }
                    ]
                }
            }
        ]
    }
    assert audio_lyrics.parse_lyrics_response(json.dumps(payload).encode()) == ("一", "二", "三", "四")


@pytest.mark.parametrize(
    "raw",
    [
        '{"lyrics":["一","二","三"]}',
        '{"lyrics":["一","二","三",""]}',
        "不是 JSON",
    ],
)
def test_parse_lyrics_response_rejects_invalid_shape(raw: str) -> None:
    with pytest.raises(audio_lyrics.AudioLyricsError, match="lyrics_response_invalid"):
        audio_lyrics.parse_lyrics_text(raw)


def test_score_tolerates_only_whitespace_and_japanese_punctuation() -> None:
    actual = (
        "かじかんだ心、震える眼差し 世界で",
        "僕は 独りぼっちだった",
        "散ることしか知らない春は。",
        "舞い落ち、冷たく あしらう",
    )
    scores = audio_lyrics.score_lyrics(actual)
    assert all(item["passed"] for item in scores)
    assert all(item["similarity"] == 1.0 for item in scores)


def test_score_does_not_pass_wrong_lyrics() -> None:
    scores = audio_lyrics.score_lyrics(("全然違う歌詞", *audio_lyrics.AGY_REFERENCE_LYRICS[1:]))
    assert scores[0]["similarity"] < 1.0
    assert scores[0]["passed"] is False


def test_missing_key_fails_without_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AI_SHIRO_API_KEY", raising=False)
    with pytest.raises(audio_lyrics.AudioLyricsError, match="api_key_missing"):
        audio_lyrics.read_api_key("AI_SHIRO_API_KEY", no_prompt=True)


def test_payload_is_audio_only_and_excludes_reference_lyrics(tmp_path: Path) -> None:
    audio = tmp_path / "audio.mp3"
    audio.write_bytes(b"audio-only")
    payload = audio_lyrics.build_payload(audio)
    parts = payload["contents"][0]["parts"]
    assert [set(part) for part in parts] == [{"text"}, {"inlineData"}]
    assert parts[1]["inlineData"]["mimeType"] == "audio/mpeg"
    assert base64.b64decode(parts[1]["inlineData"]["data"]) == b"audio-only"
    rendered = json.dumps(payload, ensure_ascii=False)
    assert all(line not in rendered for line in audio_lyrics.AGY_REFERENCE_LYRICS)
    assert "video" not in rendered.lower()
    assert "image" not in rendered.lower()


def test_error_redaction_removes_key_and_authorization_value() -> None:
    source = "HTTP 401 x-goog-api-key: sk-abcdef123456 Authorization: Bearer secret-token"
    redacted = audio_lyrics.redact_sensitive_text(source)
    assert "sk-abcdef123456" not in redacted
    assert "secret-token" not in redacted
    assert "[REDACTED]" in redacted


def test_401_auto_auth_retries_once_with_bearer(tmp_path: Path) -> None:
    audio = tmp_path / "audio.mp3"
    audio.write_bytes(b"audio")
    response = {
        "candidates": [{"content": {"parts": [{"text": '{"lyrics":["一","二","三","四"]}'}]}}]
    }
    headers: list[dict[str, str]] = []

    class _Response:
        status = 200

        def __enter__(self):  # noqa: ANN201
            return self

        def __exit__(self, *_args):  # noqa: ANN001, ANN201
            return False

        def getcode(self) -> int:
            return self.status

        def read(self) -> bytes:
            return json.dumps(response).encode()

    def opener(request, timeout):  # noqa: ANN001, ANN202
        headers.append(dict(request.headers))
        if len(headers) == 1:
            from urllib.error import HTTPError

            raise HTTPError(request.full_url, 401, "Unauthorized", {}, None)
        return _Response()

    result = audio_lyrics.request_gemini_audio(
        api_url="https://ai.shiro.team",
        model="gemini-test",
        api_key="test-key",
        audio_path=audio,
        timeout_seconds=5,
        opener=opener,
    )
    assert result.auth_mode == "bearer"
    assert result.request_count == 2
    assert "X-goog-api-key" in headers[0]
    assert headers[1]["Authorization"] == "Bearer test-key"


def test_explicit_bearer_sends_one_bearer_request_only(tmp_path: Path) -> None:
    audio = tmp_path / "audio.mp3"
    audio.write_bytes(b"audio")
    response = {
        "candidates": [{"content": {"parts": [{"text": '{"lyrics":["一","二","三","四"]}'}]}}]
    }
    headers: list[dict[str, str]] = []

    class _Response:
        status = 200

        def __enter__(self):  # noqa: ANN201
            return self

        def __exit__(self, *_args):  # noqa: ANN001, ANN201
            return False

        def getcode(self) -> int:
            return self.status

        def read(self) -> bytes:
            return json.dumps(response).encode()

    def opener(request, timeout):  # noqa: ANN001, ANN202
        headers.append(dict(request.headers))
        return _Response()

    result = audio_lyrics.request_gemini_audio(
        api_url="https://ai.shiro.team",
        model="gemini-test",
        api_key="test-key",
        audio_path=audio,
        timeout_seconds=5,
        auth_mode="bearer",
        opener=opener,
    )
    assert result.auth_mode == "bearer"
    assert result.request_count == 1
    assert len(headers) == 1
    assert headers[0]["Authorization"] == "Bearer test-key"
    assert "X-goog-api-key" not in headers[0]


def test_auto_403_does_not_retry_with_bearer(tmp_path: Path) -> None:
    audio = tmp_path / "audio.mp3"
    audio.write_bytes(b"audio")
    headers: list[dict[str, str]] = []

    def opener(request, timeout):  # noqa: ANN001, ANN202
        headers.append(dict(request.headers))
        from urllib.error import HTTPError

        raise HTTPError(
            request.full_url,
            403,
            "Forbidden",
            {},
            io.BytesIO(b'{"error":{"message":"not authorized"}}'),
        )

    with pytest.raises(audio_lyrics.AudioLyricsError, match="api_permission_denied"):
        audio_lyrics.request_gemini_audio(
            api_url="https://ai.shiro.team",
            model="gemini-test",
            api_key="test-key",
            audio_path=audio,
            timeout_seconds=5,
            auth_mode="auto",
            opener=opener,
        )
    assert len(headers) == 1
    assert "X-goog-api-key" in headers[0]
    assert "Authorization" not in headers[0]
