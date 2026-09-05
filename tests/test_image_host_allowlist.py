from __future__ import annotations

import asyncio
import io
import time
from types import SimpleNamespace

import httpx
import pytest

from ._loader import load_personification_module


pipeline_sticker = load_personification_module(
    "plugin.personification.handlers.reply_pipeline.pipeline_sticker"
)
safe_image_download = load_personification_module(
    "plugin.personification.core.safe_image_download"
)


def _png_bytes() -> bytes:
    Image = pytest.importorskip("PIL.Image")
    output = io.BytesIO()
    Image.new("RGB", (2, 2), (20, 30, 40)).save(output, format="PNG")
    return output.getvalue()


def _logger() -> tuple[SimpleNamespace, list[str]]:
    warnings: list[str] = []
    return SimpleNamespace(warning=warnings.append, info=lambda *_args, **_kwargs: None), warnings


def test_sticker_download_delegates_to_shared_pinned_helper(monkeypatch) -> None:  # noqa: ANN001
    captured: dict[str, object] = {}

    async def _download(url: str, **kwargs):  # noqa: ANN001
        captured["url"] = url
        captured.update(kwargs)
        return safe_image_download.DownloadedImage(_png_bytes(), "image/png", url)

    monkeypatch.setattr(pipeline_sticker, "download_public_image", _download)
    logger, warnings = _logger()
    mime, payload, is_gif = asyncio.run(
        pipeline_sticker.download_safe_image_bytes(
            url="https://images.example.test/first.png",
            file_name="first.png",
            http_client=object(),
            logger=logger,
        )
    )

    assert (mime, payload, is_gif) == ("image/png", _png_bytes(), False)
    assert captured["url"] == "https://images.example.test/first.png"
    assert captured["allowed_mimes"] == {"image/jpeg", "image/png", "image/webp", "image/gif"}
    assert captured["max_redirects"] == 4
    assert captured["headers"] == {"Accept": "image/jpeg,image/png,image/webp,image/gif"}
    assert warnings == []


def test_shared_helper_pins_initial_dns_and_rechecks_redirect_target() -> None:
    requests: list[httpx.Request] = []

    async def _resolver(host: str, *_args, **_kwargs):  # noqa: ANN001
        ip = {
            "start.example.test": "93.184.216.34",
            "redirect.example.test": "127.0.0.1",
        }[host]
        return [(2, 1, 6, "", (ip, 443))]

    async def _handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(302, headers={"location": "https://redirect.example.test/private.png"})

    def _client_factory(**kwargs):  # noqa: ANN003
        return httpx.AsyncClient(transport=httpx.MockTransport(_handler), **kwargs)

    with pytest.raises(safe_image_download.SafeImageDownloadError, match="non-public"):
        asyncio.run(
            safe_image_download.download_public_image(
                "https://start.example.test/image.png",
                max_bytes=64,
                allowed_mimes={"image/png"},
                resolver=_resolver,
                client_factory=_client_factory,
            )
        )
    assert [request.url.host for request in requests] == ["93.184.216.34"]
    assert requests[0].headers["host"] == "start.example.test"
    assert requests[0].extensions["sni_hostname"] == "start.example.test"


def test_configured_image_host_allowlist_cannot_bypass_private_resolution() -> None:
    async def _private_resolver(*_args, **_kwargs):  # noqa: ANN002, ANN003
        return [(2, 1, 6, "", ("198.18.0.78", 443))]

    pipeline_sticker.set_image_host_allowlist([".qq.com", ".mycompany.invalid"])
    with pytest.raises(safe_image_download.SafeImageDownloadError, match="non-public"):
        asyncio.run(
            safe_image_download.download_public_image(
                "https://gchat.qpic.cn/protected.png",
                max_bytes=64,
                allowed_mimes={"image/png"},
                resolver=_private_resolver,
            )
        )


def test_sticker_download_rejects_invalid_decoded_content_without_echoing_url(monkeypatch) -> None:  # noqa: ANN001
    async def _download(url: str, **_kwargs):  # noqa: ANN001
        return safe_image_download.DownloadedImage(b"not-a-png", "image/png", url)

    monkeypatch.setattr(pipeline_sticker, "download_public_image", _download)
    logger, warnings = _logger()
    assert asyncio.run(
        pipeline_sticker.download_safe_image_bytes(
            url="https://images.example.test/private-token.png",
            file_name="private-token.png",
            http_client=object(),
            logger=logger,
        )
    ) == (None, None, False)
    assert warnings == ["拟人插件：图片下载或校验未通过，已忽略该媒体。"]
    assert "private-token" not in " ".join(warnings)


def test_sticker_download_rethrows_cancellation_from_shared_helper(monkeypatch) -> None:  # noqa: ANN001
    async def _cancel(*_args, **_kwargs):  # noqa: ANN002, ANN003
        raise asyncio.CancelledError()

    monkeypatch.setattr(pipeline_sticker, "download_public_image", _cancel)
    logger, warnings = _logger()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            pipeline_sticker.download_safe_image_bytes(
                url="https://images.example.test/cancel.png",
                file_name="cancel.png",
                http_client=object(),
                logger=logger,
            )
        )
    assert warnings == []


def test_sticker_download_does_not_start_after_turn_deadline(monkeypatch) -> None:  # noqa: ANN001
    calls = 0

    async def _download(*_args, **_kwargs):  # noqa: ANN002, ANN003
        nonlocal calls
        calls += 1
        raise AssertionError("expired turn must not start a network request")

    monkeypatch.setattr(pipeline_sticker, "download_public_image", _download)
    logger, warnings = _logger()
    assert asyncio.run(
        pipeline_sticker.download_safe_image_bytes(
            url="https://images.example.test/expired.png",
            file_name="expired.png",
            http_client=object(),
            logger=logger,
            response_deadline=time.monotonic() - 0.01,
        )
    ) == (None, None, False)
    assert calls == 0
    assert warnings == []


def test_gif_switch_preserves_placeholder_signal_after_shared_download(monkeypatch) -> None:  # noqa: ANN001
    async def _download(url: str, **_kwargs):  # noqa: ANN001
        return safe_image_download.DownloadedImage(b"not-decoded-because-disabled", "image/gif", url)

    monkeypatch.setattr(pipeline_sticker, "download_public_image", _download)
    logger, _warnings = _logger()
    assert asyncio.run(
        pipeline_sticker.download_safe_image_bytes(
            url="https://images.example.test/animated",
            file_name="animated.gif",
            http_client=object(),
            logger=logger,
            allow_gif=False,
        )
    ) == (None, None, True)
