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
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


DEFAULT_API_URL = "https://ai.shiro.team"
DEFAULT_MODEL = "gemini-3.7-flash-high"
DEFAULT_VIDEO_DIRECTORY = Path(r"D:\CloudMusic\MV")
DEFAULT_VIDEO_NAME = "春日影 (MyGO!!!!! ver.) - MyGO!!!!!.mp4"
DEFAULT_TIMEOUT_SECONDS = 120.0
DEFAULT_MAX_AUDIO_BYTES = 20 * 1024 * 1024

# 仅供本地评分，绝不拼接到发给模型的 prompt 或 HTTP payload。
AGY_REFERENCE_LYRICS = (
    "かじかんだ心 震える眼差し 世界で",
    "僕は独りぼっちだった",
    "散ることしか知らない春は",
    "舞い落ち 冷たく あしらう",
)

_JAPANESE_PUNCTUATION = re.compile(r"[\s\u3000\u3001\u3002\u30fb\u30fc\u2010-\u2015\u2018\u2019\u201c\u201d\(\)\[\]{}!！?？,，.．:：;；]+")
_SECRET_TEXT = re.compile(
    r"(?i)(?:sk-[a-z0-9_-]{6,}|bearer\s+[^\s,;]+|x-goog-api-key\s*[:=]\s*[^\s,;]+)"
)


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


@dataclass(frozen=True)
class HttpResult:
    status_code: int
    body: bytes


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


def build_ffmpeg_audio_command(ffmpeg: str, source: Path, destination: Path, bitrate_kbps: int) -> list[str]:
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
) -> None:
    _run_checked(
        build_ffmpeg_audio_command(ffmpeg, source, destination, bitrate_kbps),
        diagnostic_code="audio_extract_failed",
    )
    if not destination.is_file() or destination.stat().st_size <= 0:
        raise AudioLyricsError("audio_extract_empty")
    if destination.stat().st_size > max_audio_bytes:
        raise AudioLyricsError("audio_payload_too_large")


def build_prompt() -> str:
    return (
        "你将收到一段仅含音频的数据。请只根据你实际听到的音频复述歌曲开头前四句日文歌词。"
        "不得依据文件名、画面、封面、上下文提示或外部知识；听不清的字请如实使用空字符串，不要猜测。"
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


def build_payload(audio_path: Path) -> dict[str, Any]:
    return {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {"text": build_prompt()},
                    {
                        "inlineData": {
                            "mimeType": "audio/mpeg",
                            "data": base64.b64encode(audio_path.read_bytes()).decode("ascii"),
                        }
                    },
                ],
            }
        ]
    }


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


def request_gemini_audio(
    *,
    api_url: str,
    model: str,
    api_key: str,
    audio_path: Path,
    timeout_seconds: float,
    auth_mode: str = "auto",
    opener: Callable[..., Any] = urlopen,
) -> GeminiResult:
    if not str(api_key or "").strip():
        raise AudioLyricsError("api_key_missing")
    endpoint = build_generate_content_endpoint(api_url, model)
    body = json.dumps(build_payload(audio_path), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    normalized_auth = str(auth_mode or "auto").strip().lower().replace("-", "_")
    if normalized_auth not in {"auto", "header", "bearer"}:
        raise AudioLyricsError("auth_mode_invalid")
    modes = ["header"] if normalized_auth in {"auto", "header"} else ["bearer"]
    if normalized_auth == "auto":
        modes.append("bearer")
    for count, current_mode in enumerate(modes, start=1):
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if current_mode == "header":
            headers["x-goog-api-key"] = api_key
        else:
            headers["Authorization"] = f"Bearer {api_key}"
        response = _read_http_response(
            Request(endpoint, data=body, headers=headers, method="POST"),
            timeout_seconds,
            opener,
        )
        if 200 <= response.status_code < 300:
            return GeminiResult(parse_lyrics_response(response.body), current_mode, count)
        if response.status_code != 401 or normalized_auth != "auto" or current_mode == "bearer":
            raise AudioLyricsError(_response_diagnostic_code(response.status_code))
    raise AudioLyricsError("api_auth_failed")


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
    api_url: str,
    model: str,
    metadata: AudioMetadata,
    elapsed_seconds: float,
    result: GeminiResult,
) -> bool:
    scores = score_lyrics(result.lyrics)
    passed = all(bool(item["passed"]) for item in scores)
    print("[音频歌词测试报告]")
    print(f"接口: {api_url}")
    print(f"模型: {model}")
    print(f"音频格式: audio/mpeg（编码={metadata.codec_name}，采样率={metadata.sample_rate or '未知'} Hz，声道={metadata.channels or '未知'}）")
    print(f"音频大小: {metadata.size_bytes} bytes")
    print(f"音频时长: {_format_seconds(metadata.duration_seconds)}")
    print(f"请求耗时: {elapsed_seconds:.3f} 秒")
    print(f"认证协商: {result.auth_mode}（请求次数={result.request_count}）")
    print("音频隔离: PASS（上传 parts 仅包含 text 与 inlineData(audio/mpeg)，未上传视频、图片或视频帧）")
    for item in scores:
        status = "PASS" if item["passed"] else "FAIL"
        print(f"第 {item['line']} 句 [{status}] 相似度={item['similarity']:.4f}: {item['model']}")
    print(f"总结果: {'PASS' if passed else 'FAIL'}")
    print(f"诊断码: {'audio_lyrics_pass' if passed else 'audio_lyrics_mismatch'}")
    return passed


def print_failure_report(
    *,
    api_url: str,
    model: str,
    metadata: AudioMetadata | None,
    elapsed_seconds: float | None,
    diagnostic_code: str,
) -> None:
    """错误报告只保留可审计摘要，绝不回显 HTTP 正文、头部或凭证。"""

    print("[音频歌词测试报告]")
    print(f"接口: {api_url}")
    print(f"模型: {model}")
    if metadata is None:
        print("音频格式: 未生成")
        print("音频大小: 未生成")
        print("音频时长: 未生成")
        print("音频隔离: 未开始上传")
    else:
        print(f"音频格式: audio/mpeg（编码={metadata.codec_name}，采样率={metadata.sample_rate or '未知'} Hz，声道={metadata.channels or '未知'}）")
        print(f"音频大小: {metadata.size_bytes} bytes")
        print(f"音频时长: {_format_seconds(metadata.duration_seconds)}")
        print("音频隔离: PASS（上传内容限定为 inlineData(audio/mpeg)，无视频、图片或视频帧）")
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
    parser.add_argument("--auth-mode", choices=("auto", "header", "bearer"), default="auto")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS, help="单次 HTTP 超时秒数")
    parser.add_argument("--ffmpeg", default="", help="ffmpeg 可执行文件路径")
    parser.add_argument("--ffprobe", default="", help="ffprobe 可执行文件路径")
    parser.add_argument("--audio-bitrate-kbps", type=int, default=64, help="临时 MP3 比特率")
    parser.add_argument("--max-audio-bytes", type=int, default=DEFAULT_MAX_AUDIO_BYTES, help="允许上传的临时音频上限")
    parser.add_argument("--no-prompt", action="store_true", help="密钥缺失时直接失败，适用于自动化")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    # Windows 的非交互式 PowerShell 管道可能仍使用本地代码页；报告固定为 UTF-8。
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass
    args = build_parser().parse_args(argv)
    metadata: AudioMetadata | None = None
    request_started: float | None = None
    try:
        if args.timeout <= 0 or args.audio_bitrate_kbps <= 0 or args.max_audio_bytes <= 0:
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
            audio_path = Path(temp_dir) / "audio_only.mp3"
            extract_audio(
                ffmpeg,
                source,
                audio_path,
                bitrate_kbps=args.audio_bitrate_kbps,
                max_audio_bytes=args.max_audio_bytes,
            )
            metadata = probe_audio(ffprobe, audio_path)
            request_started = time.monotonic()
            result = request_gemini_audio(
                api_url=args.api_url,
                model=args.model,
                api_key=api_key,
                audio_path=audio_path,
                timeout_seconds=args.timeout,
                auth_mode=args.auth_mode,
            )
            passed = print_success_report(
                api_url=args.api_url,
                model=args.model,
                metadata=metadata,
                elapsed_seconds=time.monotonic() - request_started,
                result=result,
            )
        return 0 if passed else 1
    except AudioLyricsError as exc:
        print_failure_report(
            api_url=args.api_url,
            model=args.model,
            metadata=metadata,
            elapsed_seconds=(time.monotonic() - request_started) if request_started is not None else None,
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
