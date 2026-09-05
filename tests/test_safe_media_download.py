from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from ._loader import load_personification_module


safe_media_download = load_personification_module("plugin.personification.core.safe_media_download")


class _CancellingResponse:
    status_code = 200
    headers = {"content-type": "video/mp4", "content-length": "8"}

    def __init__(self) -> None:
        self.closed = False

    async def aiter_bytes(self):  # noqa: ANN201
        yield b"part"
        raise asyncio.CancelledError

    async def aclose(self) -> None:
        self.closed = True


class _Client:
    def __init__(self, response: _CancellingResponse, **_kwargs) -> None:  # noqa: ANN003
        self.response = response

    async def __aenter__(self):  # noqa: ANN201
        return self

    async def __aexit__(self, *_args) -> None:  # noqa: ANN002
        return None

    def build_request(self, *_args, **_kwargs):  # noqa: ANN002, ANN003
        return type("Request", (), {"extensions": {}})()

    async def send(self, *_args, **_kwargs):  # noqa: ANN002, ANN003, ANN201
        return self.response


def test_cancelled_media_download_removes_only_its_part_file(tmp_path: Path) -> None:
    destination = tmp_path / "already-there.mp4"
    destination.write_bytes(b"prior-content")
    response = _CancellingResponse()

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            safe_media_download.download_public_media_to_path(
                "https://8.8.8.8/file.mp4",
                destination,
                max_bytes=64,
                allowed_mimes={"video/mp4"},
                client_factory=lambda **kwargs: _Client(response, **kwargs),
            )
        )

    assert destination.read_bytes() == b"prior-content"
    assert not list(tmp_path.glob("*.part"))
    assert response.closed is True
