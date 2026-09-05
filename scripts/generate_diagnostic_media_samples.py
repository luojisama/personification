"""Generate the deterministic, copyright-free WebUI media probe samples.

This developer script is not used at runtime.  It requires ``imageio-ffmpeg``
only to locate a local ffmpeg binary while generating the packaged MP4.
"""

from __future__ import annotations

import hashlib
import math
import struct
import subprocess
import wave
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "core" / "diagnostic_media"


def _write_audio(path: Path) -> None:
    sample_rate = 16_000
    amplitude = 0.42
    tones = (330.0, 440.0, 660.0)
    tone_seconds = 0.42
    gap_seconds = 0.10
    frames: list[bytes] = []
    for tone_index, frequency in enumerate(tones):
        for index in range(round(sample_rate * tone_seconds)):
            fade = min(1.0, index / 160, (sample_rate * tone_seconds - index) / 160)
            value = int(32767 * amplitude * fade * math.sin(2 * math.pi * frequency * index / sample_rate))
            frames.append(struct.pack("<h", value))
        if tone_index != len(tones) - 1:
            frames.extend([b"\x00\x00"] * round(sample_rate * gap_seconds))
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(b"".join(frames))


def _scene_frame(width: int, height: int, color: tuple[int, int, int], shape: str) -> bytes:
    pixels = bytearray(color * (width * height))
    white = (255, 255, 255)

    def set_pixel(x: int, y: int) -> None:
        if 0 <= x < width and 0 <= y < height:
            offset = (y * width + x) * 3
            pixels[offset : offset + 3] = bytes(white)

    center_x, center_y = width // 2, height // 2
    if shape == "circle":
        radius = min(width, height) // 5
        for y in range(center_y - radius, center_y + radius + 1):
            for x in range(center_x - radius, center_x + radius + 1):
                if (x - center_x) ** 2 + (y - center_y) ** 2 <= radius**2:
                    set_pixel(x, y)
    elif shape == "square":
        half = min(width, height) // 5
        for y in range(center_y - half, center_y + half):
            for x in range(center_x - half, center_x + half):
                set_pixel(x, y)
    else:
        half = min(width, height) // 4
        for row in range(half * 2):
            span = max(1, row // 2)
            y = center_y - half + row
            for x in range(center_x - span, center_x + span + 1):
                set_pixel(x, y)
    return bytes(pixels)


def _write_video(path: Path) -> None:
    import imageio_ffmpeg

    width = height = 96
    fps = 12
    scenes = (
        ((220, 34, 50), "circle"),
        ((28, 168, 92), "square"),
        ((35, 92, 210), "triangle"),
    )
    command = [
        imageio_ffmpeg.get_ffmpeg_exe(),
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-s",
        f"{width}x{height}",
        "-r",
        str(fps),
        "-i",
        "pipe:0",
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "veryslow",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        "-map_metadata",
        "-1",
        "-metadata",
        "creation_time=1970-01-01T00:00:00Z",
        "-threads",
        "1",
        "-y",
        str(path),
    ]
    process = subprocess.Popen(command, stdin=subprocess.PIPE)
    assert process.stdin is not None
    try:
        for color, shape in scenes:
            frame = _scene_frame(width, height, color, shape)
            for _ in range(fps):
                process.stdin.write(frame)
    finally:
        process.stdin.close()
    if process.wait() != 0:
        raise RuntimeError("ffmpeg failed to generate the diagnostic MP4")


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    audio = OUTPUT / "audio-ascending-v1.wav"
    video = OUTPUT / "video-rgb-v1.mp4"
    _write_audio(audio)
    _write_video(video)
    for path in (audio, video):
        payload = path.read_bytes()
        print(f"{path.relative_to(ROOT)} size={len(payload)} sha256={hashlib.sha256(payload).hexdigest()}")


if __name__ == "__main__":
    main()
