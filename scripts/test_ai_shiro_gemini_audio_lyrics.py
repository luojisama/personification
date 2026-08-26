"""只用音频测试 ai.shiro.team Gemini 的日文歌词理解。

此脚本的边界刻意严格：它只允许 ffprobe 检查音轨、ffmpeg 抽取首条音轨，
再以 Gemini ``inlineData(audio/mpeg)`` 上传临时音频。它不会抽帧、读取图像、
上传视频容器，也不会把文件名提供给模型。

用法（密钥不会写入命令行）：

    python scripts/test_ai_shiro_gemini_audio_lyrics.py

也可由受控环境注入密钥：

    $env:AI_SHIRO_API_KEY = "..."
    python scripts/test_ai_shiro_gemini_audio_lyrics.py
"""
from __future__ import annotations

import argparse
import base64
import difflib
import getpass
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import HTTPRedirectHandler, Request, build_opener


DEFAULT_API_URL = "https://ai.shiro.team"
DEFAULT_MODEL = "gemini-3.7-flash-high"
DEFAULT_VIDEO_DIRECTORY = Path(r"D:\CloudMusic\MV")
DEFAULT_VIDEO_NAME = "春日影 (MyGO!!!!! ver.) - MyGO!!!!!.mp4"
DEFAULT_TIMEOUT_SECONDS = 180.0
DEFAULT_MAX_AUDIO_BYTES = 20 * 1024 * 1024
DEFAULT_MAX_VIDEO_BYTES = 12 * 1024 * 1024
DEFAULT_WINDOW_SECONDS = 90.0
DEFAULT_AUDIO_BITRATE_KBPS = 128
COMPATIBILITY_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"

# 仅供本地评分，绝不拼接到发给模型的 prompt 或 HTTP payload。
AGY_REFERENCE_LYRICS = (
    "かじかんだ心 震える眼差し 世界で",
    "僕は独りぼっちだった",
    "散ることしか知らない春は",
    "舞い落ち 冷たく あしらう",
)

_JAPANESE_PUNCTUATION = re.compile(r"[\s\u3000\u3001\u3002\u30fb\u30fc\u2010-\u2015\u2018\u2019\u201c\u201d\(\)\[\]{}!！?？,，.．:：;；]+")
_SECRET_TEXT = re.compile(
    r"(?i)(?:sk-[a-z0-9_-]{6,}|bearer\s+[^\s,;]+|x-goog-api-key\s*[:=]\s*[^\s,;]+|(?:[?&](?:key|api_key|token)=)[^&\s,;]+)"
)


class _RejectRedirects(HTTPRedirectHandler):
    """媒体请求不跟随跳转，避免认证头被转交给另一主机。"""

    def redirect_request(self, request, fp, code, msg, headers, newurl):  # noqa: ANN001, ANN201
        return None


_NO_REDIRECT_OPENER = build_opener(_RejectRedirects()).open


class AudioLyricsError(RuntimeError):
    """带稳定诊断码的预期失败。"""

    def __init__(self, diagnostic_code: str) -> None:
        self.diagnostic_code = diagnostic_code
        super().__init__(diagnostic_code)


@dataclass(frozen=True)
class AudioMetadata:
    codec_name: str
    sample_rate: int | None
    channels: int | None
    duration_seconds: float | None
    size_bytes: int


@dataclass(frozen=True)
class GeminiResult:
    lyrics: tuple[str, str, str, str]
    auth_mode: str
    request_count: int
    status_code: int
    request_body_bytes: int


@dataclass(frozen=True)
class HttpResult:
    status_code: int
    body: bytes


@dataclass(frozen=True)
class MediaArtifact:
    input_mode: str
    mime_type: str
    codec_summary: str
    duration_seconds: float | None
    size_bytes: int
    sha256: str


def find_target_video(directory: Path, expected_name: str) -> Path:
    """按精确文件名定位视频；文件名只在本地使用，不会发送给模型。"""

    candidate = directory / expected_name
    if candidate.is_file():
        return candidate
    if not directory.is_dir():
        raise AudioLyricsError("target_directory_missing")
    matches = [path for path in directory.iterdir() if path.is_file() and path.name == expected_name]
    if len(matches) == 1:
        return matches[0]
    raise AudioLyricsError("target_video_not_found")


def resolve_binary(name: str, configured: str = "") -> str:
    candidate = str(configured or "").strip() or shutil.which(name)
    if candidate:
        return candidate
    raise AudioLyricsError(f"{name}_unavailable")


def build_ffprobe_audio_command(ffprobe: str, source: Path) -> list[str]:
    return [
        ffprobe,
        "-v",
        "error",
        "-select_streams",
        "a:0",
        "-show_entries",
        "stream=codec_name,sample_rate,channels,duration",
        "-show_entries",
        "format=duration",
        "-of",
        "json",
        "--",
        str(source),
    ]


def build_ffmpeg_audio_command(
    ffmpeg: str,
    source: Path,
    destination: Path,
    bitrate_kbps: int,
    window_seconds: float | None = None,
) -> list[str]:
    """只映射首条音轨，``-vn`` 强制丢弃任何视频流。"""

    return [
        ffmpeg,
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(source),
        *( ["-t", str(float(window_seconds))] if window_seconds and window_seconds > 0 else [] ),
        "-map",
        "0:a:0?",
        "-vn",
        "-c:a",
        "libmp3lame",
        "-b:a",
        f"{int(bitrate_kbps)}k",
        "-ac",
        "1",
        "-ar",
        "44100",
        str(destination),
    ]


def build_ffmpeg_video_command(
    ffmpeg: str,
    source: Path,
    destination: Path,
    window_seconds: float = DEFAULT_WINDOW_SECONDS,
) -> list[str]:
    """从相同开头窗口转码 MP4，保留画面与原音轨的内容。"""

    return [
        ffmpeg,
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(source),
        "-t",
        str(float(window_seconds)),
        "-map",
        "0:v:0?",
        "-map",
        "0:a:0?",
        # NapCat 附带的 ffmpeg 不保证编译 libx264，mpeg4 更可移植。
        "-c:v",
        "mpeg4",
        "-q:v",
        "6",
        "-maxrate",
        "500k",
        "-bufsize",
        "1000k",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "64k",
        "-ac",
        "1",
        "-ar",
        "44100",
        "-movflags",
        "+faststart",
        str(destination),
    ]


def _run_checked(command: Sequence[str], *, diagnostic_code: str) -> subprocess.CompletedProcess[str]:
    try:
        completed = subprocess.run(
            list(command),
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as exc:
        raise AudioLyricsError(diagnostic_code) from exc
    if completed.returncode != 0:
        raise AudioLyricsError(diagnostic_code)
    return completed


def probe_audio(ffprobe: str, source: Path) -> AudioMetadata:
    completed = _run_checked(
        build_ffprobe_audio_command(ffprobe, source),
        diagnostic_code="audio_probe_failed",
    )
    try:
        payload = json.loads(completed.stdout)
        streams = list(payload.get("streams") or [])
        stream = next((item for item in streams if isinstance(item, dict)), None)
        if stream is None:
            raise ValueError("missing_audio_stream")
        format_info = payload.get("format") if isinstance(payload.get("format"), dict) else {}
        duration_raw = stream.get("duration") or format_info.get("duration")
        duration = float(duration_raw) if duration_raw not in (None, "", "N/A") else None
        sample_rate = int(stream["sample_rate"]) if str(stream.get("sample_rate") or "").isdigit() else None
        channels = int(stream["channels"]) if str(stream.get("channels") or "").isdigit() else None
        codec = str(stream.get("codec_name") or "unknown")
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise AudioLyricsError("audio_probe_invalid") from exc
    return AudioMetadata(codec, sample_rate, channels, duration, source.stat().st_size)


def extract_audio(
    ffmpeg: str,
    source: Path,
    destination: Path,
    *,
    bitrate_kbps: int,
    max_audio_bytes: int,
    window_seconds: float,
) -> None:
    _run_checked(
        build_ffmpeg_audio_command(ffmpeg, source, destination, bitrate_kbps, window_seconds),
        diagnostic_code="audio_extract_failed",
    )
    if not destination.is_file() or destination.stat().st_size <= 0:
        raise AudioLyricsError("audio_extract_empty")
    if destination.stat().st_size > max_audio_bytes:
        raise AudioLyricsError("audio_payload_too_large")


def extract_video(
    ffmpeg: str,
    source: Path,
    destination: Path,
    *,
    max_video_bytes: int,
    window_seconds: float,
) -> None:
    _run_checked(
        build_ffmpeg_video_command(ffmpeg, source, destination, window_seconds),
        diagnostic_code="video_extract_failed",
    )
    if not destination.is_file() or destination.stat().st_size <= 0:
        raise AudioLyricsError("video_extract_empty")
    if destination.stat().st_size > max_video_bytes:
        raise AudioLyricsError("video_payload_too_large")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_audio_artifact(path: Path, metadata: AudioMetadata) -> MediaArtifact:
    return MediaArtifact(
        input_mode="audio",
        mime_type="audio/mp3",
        codec_summary=f"{metadata.codec_name}, {metadata.sample_rate or '未知'} Hz, {metadata.channels or '未知'} 声道",
        duration_seconds=metadata.duration_seconds,
        size_bytes=path.stat().st_size,
        sha256=_sha256(path),
    )


def probe_video_artifact(ffprobe: str, path: Path) -> MediaArtifact:
    command = [
        ffprobe, "-v", "error", "-show_entries",
        "stream=codec_type,codec_name,sample_rate,channels,duration",
        "-show_entries", "format=duration", "-of", "json", "--", str(path),
    ]
    completed = _run_checked(command, diagnostic_code="video_probe_failed")
    try:
        payload = json.loads(completed.stdout)
        streams = [item for item in list(payload.get("streams") or []) if isinstance(item, dict)]
        video = next(item for item in streams if item.get("codec_type") == "video")
        audio = next(item for item in streams if item.get("codec_type") == "audio")
        format_info = payload.get("format") if isinstance(payload.get("format"), dict) else {}
        duration_raw = video.get("duration") or format_info.get("duration")
        duration = float(duration_raw) if duration_raw not in (None, "", "N/A") else None
    except (StopIteration, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise AudioLyricsError("video_probe_invalid") from exc
    return MediaArtifact(
        input_mode="video",
        mime_type="video/mp4",
        codec_summary=(
            f"视频={str(video.get('codec_name') or '未知')}；"
            f"音频={str(audio.get('codec_name') or '未知')}, "
            f"{str(audio.get('sample_rate') or '未知')} Hz, {str(audio.get('channels') or '未知')} 声道"
        ),
        duration_seconds=duration,
        size_bytes=path.stat().st_size,
        sha256=_sha256(path),
    )


def build_prompt(input_mode: str) -> str:
    medium = "仅含音频的数据" if input_mode == "audio" else "包含画面和音轨的视频容器"
    evidence_rule = "请只依据你实际听到的音轨" if input_mode == "audio" else "请只依据收到的媒体内容"
    return (
        f"你将收到一段{medium}。{evidence_rule}复述歌曲开头前四句日文歌词。"
        "不得依据文件名、上下文提示或外部知识；听不清的字请如实使用空字符串，不要猜测。"
        "只返回严格 JSON 对象，格式为 {\"lyrics\":[\"第一句\",\"第二句\",\"第三句\",\"第四句\"]}，"
        "不得输出 Markdown、解释或其他字段。"
    )


def build_generate_content_endpoint(api_url: str, model: str) -> str:
    root = str(api_url or "").strip().rstrip("/")
    if not root.startswith("https://"):
        raise AudioLyricsError("api_url_invalid")
    if root.endswith("/v1beta"):
        return f"{root}/models/{quote(model, safe='._-')}:generateContent"
    return f"{root}/v1beta/models/{quote(model, safe='._-')}:generateContent"


def build_media_payload(media_path: Path, *, input_mode: str) -> dict[str, Any]:
    mime_type = "audio/mp3" if input_mode == "audio" else "video/mp4"
    return {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {
                        "inlineData": {
                            "mimeType": mime_type,
                            "data": base64.b64encode(media_path.read_bytes()).decode("ascii"),
                        }
                    },
                    {"text": build_prompt(input_mode)},
                ],
            }
        ],
        "generationConfig": {"temperature": 0.2},
    }


def build_payload(audio_path: Path) -> dict[str, Any]:
    """保留旧名称，供音频隔离回归测试和外部调用使用。"""

    return build_media_payload(audio_path, input_mode="audio")


def redact_sensitive_text(value: str) -> str:
    return _SECRET_TEXT.sub("[REDACTED]", str(value or ""))


def _read_http_response(request: Request, timeout_seconds: float, opener: Callable[..., Any]) -> HttpResult:
    try:
        with opener(request, timeout=timeout_seconds) as response:
            return HttpResult(int(getattr(response, "status", response.getcode())), response.read())
    except HTTPError as exc:
        try:
            body = exc.read(4096)
        except OSError:
            body = b""
        return HttpResult(int(exc.code), body)
    except (URLError, TimeoutError, OSError) as exc:
        raise AudioLyricsError("api_network_error") from exc


def _response_diagnostic_code(status_code: int) -> str:
    if 300 <= status_code <= 399:
        return "api_redirect_rejected"
    if status_code == 401:
        return "api_auth_failed"
    if status_code == 403:
        return "api_permission_denied"
    if status_code == 404:
        return "api_endpoint_not_found"
    if status_code == 408:
        return "api_timeout"
    if status_code == 413:
        return "api_payload_rejected"
    if status_code == 429:
        return "api_rate_limited"
    if 500 <= status_code <= 599:
        return "api_upstream_error"
    return "api_http_error"


def _request_endpoint(
    *,
    endpoint: str,
    api_key: str,
    auth_mode: str,
    method: str,
    data: bytes | None,
    timeout_seconds: float,
    opener: Callable[..., Any],
) -> HttpResult:
    normalized_auth = str(auth_mode or "auto").strip().lower().replace("-", "_")
    if normalized_auth not in {"auto", "header", "bearer"}:
        raise AudioLyricsError("auth_mode_invalid")
    headers = {"Accept": "application/json", "User-Agent": COMPATIBILITY_USER_AGENT}
    if data is not None:
        headers["Content-Type"] = "application/json"
    if normalized_auth == "header":
        headers["x-goog-api-key"] = api_key
    elif normalized_auth == "bearer":
        headers["Authorization"] = f"Bearer {api_key}"
    return _read_http_response(Request(endpoint, data=data, headers=headers, method=method), timeout_seconds, opener)


def parse_lyrics_response(payload: bytes) -> tuple[str, str, str, str]:
    try:
        response = json.loads(payload.decode("utf-8"))
        candidates = list(response.get("candidates") or [])
        parts = list((candidates[0].get("content") or {}).get("parts") or [])
        raw_text = "".join(str(part.get("text") or "") for part in parts if isinstance(part, dict)).strip()
    except (AttributeError, IndexError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AudioLyricsError("api_response_invalid") from exc
    return parse_lyrics_text(raw_text)


def parse_lyrics_text(raw_text: str) -> tuple[str, str, str, str]:
    text = str(raw_text or "").strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.IGNORECASE | re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    if not text.startswith("{"):
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if match:
            text = match.group(0)
    try:
        payload = json.loads(text)
        lyrics = payload.get("lyrics") if isinstance(payload, dict) else None
        if not isinstance(lyrics, list) or len(lyrics) != 4:
            raise ValueError("lyrics_count")
        lines = tuple(str(line).strip() for line in lyrics)
        if any(not line for line in lines):
            raise ValueError("lyrics_empty")
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise AudioLyricsError("lyrics_response_invalid") from exc
    return lines  # type: ignore[return-value]


def request_gemini_media(
    *,
    api_url: str,
    model: str,
    api_key: str,
    media_path: Path,
    input_mode: str,
    timeout_seconds: float,
    auth_mode: str = "auto",
    opener: Callable[..., Any] = _NO_REDIRECT_OPENER,
) -> GeminiResult:
    if not str(api_key or "").strip():
        raise AudioLyricsError("api_key_missing")
    endpoint = build_generate_content_endpoint(api_url, model)
    if input_mode not in {"audio", "video"}:
        raise AudioLyricsError("input_mode_invalid")
    body = json.dumps(
        build_media_payload(media_path, input_mode=input_mode), ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    normalized_auth = str(auth_mode or "auto").strip().lower().replace("-", "_")
    if normalized_auth not in {"auto", "header", "bearer"}:
        raise AudioLyricsError("auth_mode_invalid")
    modes = ["header"] if normalized_auth in {"auto", "header"} else [normalized_auth]
    if normalized_auth == "auto":
        modes.append("bearer")
    for count, current_mode in enumerate(modes, start=1):
        response = _request_endpoint(
            endpoint=endpoint,
            api_key=api_key,
            auth_mode=current_mode,
            method="POST",
            data=body,
            timeout_seconds=timeout_seconds,
            opener=opener,
        )
        if 200 <= response.status_code < 300:
            return GeminiResult(
                parse_lyrics_response(response.body),
                current_mode,
                count,
                response.status_code,
                len(body),
            )
        if response.status_code != 401 or normalized_auth != "auto" or current_mode == "bearer":
            raise AudioLyricsError(_response_diagnostic_code(response.status_code))
    raise AudioLyricsError("api_auth_failed")


def request_gemini_audio(**kwargs: Any) -> GeminiResult:
    """兼容旧调用：严格构造 audio/mpeg payload。"""

    audio_path = Path(kwargs.pop("audio_path"))
    return request_gemini_media(media_path=audio_path, input_mode="audio", **kwargs)


def normalize_lyric(value: str) -> str:
    return _JAPANESE_PUNCTUATION.sub("", str(value or "")).casefold()


def score_lyrics(lines: Sequence[str], expected: Sequence[str] = AGY_REFERENCE_LYRICS) -> list[dict[str, Any]]:
    if len(lines) != 4 or len(expected) != 4:
        raise ValueError("four_lines_required")
    scored: list[dict[str, Any]] = []
    for index, (actual, reference) in enumerate(zip(lines, expected), start=1):
        normalized_actual = normalize_lyric(actual)
        normalized_reference = normalize_lyric(reference)
        similarity = difflib.SequenceMatcher(a=normalized_actual, b=normalized_reference, autojunk=False).ratio()
        scored.append(
            {
                "line": index,
                "model": str(actual),
                "similarity": round(similarity, 4),
                # 只容忍空格和日文标点差异；错词不因模糊相似度而通过。
                "passed": normalized_actual == normalized_reference,
            }
        )
    return scored


def _format_seconds(value: float | None) -> str:
    return "未知" if value is None else f"{value:.3f} 秒"


def print_success_report(
    *,
    input_mode: str,
    api_url: str,
    model: str,
    artifact: MediaArtifact,
    elapsed_seconds: float,
    result: GeminiResult,
) -> bool:
    scores = score_lyrics(result.lyrics)
    passed = all(bool(item["passed"]) for item in scores)
    title = "A：纯音频歌词测试报告" if input_mode == "audio" else "B：视频容器歌词测试报告"
    print(f"[{title}]")
    print(f"接口: {api_url}")
    print(f"模型: {model}")
    print(f"输入模式: {input_mode}")
    print(f"媒体 MIME: {artifact.mime_type}")
    print(f"媒体编码: {artifact.codec_summary}")
    print(f"媒体窗口: {_format_seconds(artifact.duration_seconds)}（从开头截取）")
    print(f"媒体大小: {artifact.size_bytes} bytes")
    print(f"媒体 SHA-256: {artifact.sha256}")
    print(f"HTTP 状态: {result.status_code}")
    print(f"请求体大小: {result.request_body_bytes} bytes")
    print(f"请求耗时: {elapsed_seconds:.3f} 秒")
    print(f"认证协商: {result.auth_mode}（请求次数={result.request_count}）")
    if input_mode == "audio":
        print("媒体边界: PASS（上传 parts 仅含 inlineData(audio/mp3) 与 text，未上传视频、图片或视频帧）")
    else:
        print("媒体边界: video/mp4（允许画面与原音轨；该结果不能作为纯音频理解证明）")
    for item in scores:
        status = "PASS" if item["passed"] else "FAIL"
        print(f"第 {item['line']} 句 [{status}] 相似度={item['similarity']:.4f}: {item['model']}")
    print(f"总结果: {'PASS' if passed else 'FAIL'}")
    print(f"诊断码: {'audio_lyrics_pass' if passed else 'audio_lyrics_mismatch'}")
    return passed


def print_failure_report(
    *,
    input_mode: str,
    api_url: str,
    model: str,
    artifact: MediaArtifact | None,
    elapsed_seconds: float | None,
    diagnostic_code: str,
) -> None:
    """错误报告只保留可审计摘要，绝不回显 HTTP 正文、头部或凭证。"""

    title = "A：纯音频歌词测试报告" if input_mode == "audio" else "B：视频容器歌词测试报告"
    print(f"[{title}]")
    print(f"接口: {api_url}")
    print(f"模型: {model}")
    print(f"输入模式: {input_mode}")
    if artifact is None:
        print("媒体格式: 未生成")
        print("媒体大小: 未生成")
        print("媒体窗口: 未生成")
        print("媒体边界: 未开始上传")
    else:
        print(f"媒体 MIME: {artifact.mime_type}")
        print(f"媒体编码: {artifact.codec_summary}")
        print(f"媒体窗口: {_format_seconds(artifact.duration_seconds)}（从开头截取）")
        print(f"媒体大小: {artifact.size_bytes} bytes")
        print(f"媒体 SHA-256: {artifact.sha256}")
        if input_mode == "audio":
            print("媒体边界: PASS（只允许 inlineData(audio/mpeg)）")
        else:
            print("媒体边界: video/mp4（允许画面与原音轨）")
    if elapsed_seconds is not None:
        print(f"请求耗时: {elapsed_seconds:.3f} 秒")
    print("总结果: FAIL")
    print(f"诊断码: {diagnostic_code}")


def read_api_key(environment_name: str, *, no_prompt: bool) -> str:
    value = os.environ.get(environment_name, "").strip()
    if value:
        return value
    if no_prompt:
        raise AudioLyricsError("api_key_missing")
    return getpass.getpass(f"请输入 {environment_name}（不会回显或写入文件）: ").strip()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="仅上传音频的 ai.shiro.team Gemini 歌词测试")
    parser.add_argument("--video-dir", type=Path, default=DEFAULT_VIDEO_DIRECTORY, help="仅用于本地定位目标视频")
    parser.add_argument("--video-name", default=DEFAULT_VIDEO_NAME, help="精确目标文件名；不会发送给模型")
    parser.add_argument("--api-url", default=DEFAULT_API_URL, help="Gemini API 根地址")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Gemini 模型名")
    parser.add_argument("--api-key-env", default="AI_SHIRO_API_KEY", help="API Key 环境变量名")
    parser.add_argument("--auth-mode", choices=("auto", "header", "bearer"), default="bearer")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS, help="单次 HTTP 超时秒数")
    parser.add_argument("--ffmpeg", default="", help="ffmpeg 可执行文件路径")
    parser.add_argument("--ffprobe", default="", help="ffprobe 可执行文件路径")
    parser.add_argument("--audio-bitrate-kbps", type=int, default=DEFAULT_AUDIO_BITRATE_KBPS, help="临时 MP3 比特率")
    parser.add_argument("--max-audio-bytes", type=int, default=DEFAULT_MAX_AUDIO_BYTES, help="允许上传的临时音频上限")
    parser.add_argument("--max-video-bytes", type=int, default=DEFAULT_MAX_VIDEO_BYTES, help="允许上传的临时 MP4 上限")
    parser.add_argument("--window-seconds", type=float, default=DEFAULT_WINDOW_SECONDS, help="从 MV 开头截取的共同窗口秒数（最大 90）")
    parser.add_argument("--input-mode", choices=("audio", "video", "both"), default="both", help="默认依次执行纯音频 A 与视频容器 B")
    parser.add_argument("--no-prompt", action="store_true", help="密钥缺失时直接失败，适用于自动化")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    # Windows 的非交互式 PowerShell 管道可能仍使用本地代码页；报告固定为 UTF-8。
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass
    args = build_parser().parse_args(argv)
    outcomes: list[int] = []
    try:
        if (
            args.timeout <= 0
            or args.audio_bitrate_kbps <= 0
            or args.max_audio_bytes <= 0
            or args.max_video_bytes <= 0
            or args.window_seconds <= 0
            or args.window_seconds > DEFAULT_WINDOW_SECONDS
        ):
            raise AudioLyricsError("argument_invalid")
        source = find_target_video(args.video_dir, args.video_name)
        ffmpeg = resolve_binary("ffmpeg", args.ffmpeg)
        ffprobe = resolve_binary("ffprobe", args.ffprobe)
        # 先确认源文件有音轨，再索取密钥，避免在本地依赖未就绪时触及凭证。
        probe_audio(ffprobe, source)
        api_key = read_api_key(args.api_key_env, no_prompt=bool(args.no_prompt))
        if not api_key:
            raise AudioLyricsError("api_key_missing")
        with tempfile.TemporaryDirectory(prefix="ai-shiro-gemini-audio-") as temp_dir:
            temp_root = Path(temp_dir)
            for input_mode in (("audio", "video") if args.input_mode == "both" else (args.input_mode,)):
                artifact: MediaArtifact | None = None
                request_started: float | None = None
                try:
                    if input_mode == "audio":
                        media_path = temp_root / "audio_only.mp3"
                        extract_audio(
                            ffmpeg,
                            source,
                            media_path,
                            bitrate_kbps=args.audio_bitrate_kbps,
                            max_audio_bytes=args.max_audio_bytes,
                            window_seconds=None,
                        )
                        artifact = build_audio_artifact(media_path, probe_audio(ffprobe, media_path))
                    else:
                        media_path = temp_root / "video_native.mp4"
                        extract_video(
                            ffmpeg,
                            source,
                            media_path,
                            max_video_bytes=args.max_video_bytes,
                            window_seconds=args.window_seconds,
                        )
                        artifact = probe_video_artifact(ffprobe, media_path)
                    request_started = time.monotonic()
                    result = request_gemini_media(
                        api_url=args.api_url,
                        model=args.model,
                        api_key=api_key,
                        media_path=media_path,
                        input_mode=input_mode,
                        timeout_seconds=args.timeout,
                        auth_mode=args.auth_mode,
                    )
                    passed = print_success_report(
                        input_mode=input_mode,
                        api_url=args.api_url,
                        model=args.model,
                        artifact=artifact,
                        elapsed_seconds=time.monotonic() - request_started,
                        result=result,
                    )
                    outcomes.append(0 if passed else 1)
                except AudioLyricsError as exc:
                    print_failure_report(
                        input_mode=input_mode,
                        api_url=args.api_url,
                        model=args.model,
                        artifact=artifact,
                        elapsed_seconds=(time.monotonic() - request_started) if request_started is not None else None,
                        diagnostic_code=exc.diagnostic_code,
                    )
                    outcomes.append(2)
        return 0 if outcomes and all(outcome == 0 for outcome in outcomes) else 1
    except AudioLyricsError as exc:
        print_failure_report(
            input_mode=args.input_mode if args.input_mode in {"audio", "video"} else "audio",
            api_url=args.api_url,
            model=args.model,
            artifact=None,
            elapsed_seconds=None,
            diagnostic_code=exc.diagnostic_code,
        )
        return 2
    except KeyboardInterrupt:
        print("[音频歌词测试报告]")
        print("总结果: FAIL")
        print("诊断码: interrupted")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
