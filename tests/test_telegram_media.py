from __future__ import annotations

import asyncio
import hashlib
import io
import logging
from types import SimpleNamespace

import httpx
import pytest
from PIL import Image

from ._loader import load_personification_module

media = load_personification_module("plugin.personification.core.telegram_media")
interaction = load_personification_module("plugin.personification.core.interaction_adapter")
turn = load_personification_module("plugin.personification.core.turn_media")
TOKEN = "123456:isolated_test_token"


def test_shared_diagnostics_redact_telegram_url_before_truncation():
    sanitizer = load_personification_module("plugin.personification.core.sensitive_data")
    text = "https://api.telegram.org/file/bot" + TOKEN + "/photos/a.png"
    assert TOKEN not in sanitizer.sanitize_text(text)
    assert "123456:" not in sanitizer.sanitize_text(text, limit=45)
    assert sanitizer.contains_sensitive_value(text)


def test_transport_child_logger_redacts_token(caplog):
    media.protect_transport_logs()
    with caplog.at_level(logging.DEBUG, logger="httpcore.http11"):
        logging.getLogger("httpcore.http11").debug("request %s", "/bot" + TOKEN + "/getUpdates")
    assert TOKEN not in caplog.text
    assert "[REDACTED]" in caplog.text


def envelope(kind="image"):
    item = turn.TurnMediaRef("m", "tgfile:remote-id", "current", "user", "1", kind)
    return interaction.InteractionEnvelope("telegram", "123456", "private", "7", "user", "1", ordered_media=(item,))


def fixture(monkeypatch, *, path="photos/a.png", size=1, payload=b"audio", status=200):
    requests, options = [], []
    async def get_file(**kwargs):
        assert kwargs == {"file_id": "remote-id"}
        return SimpleNamespace(file_path=path, file_size=size)
    bot = SimpleNamespace(get_file=get_file, bot_config=SimpleNamespace(token=TOKEN, api_server="https://tg.invalid"), adapter=SimpleNamespace(adapter_config=SimpleNamespace(proxy=None)))
    def handle(request):
        requests.append(request)
        return httpx.Response(status, content=payload, headers={"location": "https://other.invalid/stolen"} if status == 302 else {})
    real_client = httpx.AsyncClient
    def client(**kwargs):
        options.append(kwargs)
        return real_client(**kwargs, transport=httpx.MockTransport(handle))
    monkeypatch.setattr(httpx, "AsyncClient", client)
    return bot, requests, options


def test_real_png_is_inline_without_credential_url(monkeypatch, tmp_path, caplog):
    data = io.BytesIO()
    Image.new("RGB", (2, 3), "red").save(data, format="PNG")
    bot, requests, options = fixture(monkeypatch, payload=data.getvalue())
    with caplog.at_level(logging.INFO, logger="httpx"):
        result = asyncio.run(media.materialize(bot, envelope(), tmp_path))
    item = result.ordered_media[0]
    assert item.ref.startswith("data:image/png;base64,")
    assert item.content_hash == hashlib.sha256(data.getvalue()).hexdigest()
    assert item.resolution_code == "telegram_downloaded"
    assert TOKEN not in repr(result) + caplog.text
    assert len(requests) == 1
    assert options[0]["follow_redirects"] is False
    assert options[0]["trust_env"] is False


@pytest.mark.parametrize("kind,path", [("audio", "voice/a.ogg"), ("video", "videos/a.mp4")])
def test_audio_video_use_hashed_local_cache(monkeypatch, tmp_path, kind, path):
    bot, _, _ = fixture(monkeypatch, path=path)
    item = asyncio.run(media.materialize(bot, envelope(kind), tmp_path)).ordered_media[0]
    from pathlib import Path
    file = Path(item.ref)
    assert file.parent == tmp_path
    assert file.read_bytes() == b"audio"
    assert file.name.startswith("tg-" + hashlib.sha256(b"audio").hexdigest())
    assert TOKEN not in repr(item)


@pytest.mark.parametrize("path", ["../secret.ogg", "/secret.ogg", "C:/secret.ogg", "a\\b.ogg", "a.ogg?token=x", "a.ogg#x", ""])
def test_malicious_remote_paths_never_download(monkeypatch, tmp_path, path):
    bot, requests, _ = fixture(monkeypatch, path=path)
    item = asyncio.run(media.materialize(bot, envelope("audio"), tmp_path)).ordered_media[0]
    assert item.resolution_code == "telegram_download_failed"
    assert requests == []


def test_declared_size_rejects_before_http(monkeypatch, tmp_path):
    bot, requests, _ = fixture(monkeypatch, size=20 * 1024 * 1024 + 1)
    result = asyncio.run(media.materialize(bot, envelope(), tmp_path))
    assert result.ordered_media[0].resolution_code == "telegram_download_failed"
    assert not requests


def test_actual_size_rejects_even_if_metadata_lies(monkeypatch, tmp_path):
    bot, _, _ = fixture(monkeypatch, path="audio/a.ogg", payload=b"x" * (20 * 1024 * 1024 + 1))
    item = asyncio.run(media.materialize(bot, envelope("audio"), tmp_path)).ordered_media[0]
    assert item.resolution_code == "telegram_download_failed"
    assert not list(tmp_path.iterdir())


def test_redirect_is_not_followed_or_exposed(monkeypatch, tmp_path, caplog):
    bot, requests, _ = fixture(monkeypatch, status=302)
    with caplog.at_level(logging.INFO, logger="httpx"):
        item = asyncio.run(media.materialize(bot, envelope(), tmp_path)).ordered_media[0]
    assert item.resolution_code == "telegram_download_failed"
    assert len(requests) == 1
    assert TOKEN not in repr(item) + caplog.text


def test_cache_symlink_cannot_overwrite_other_file(monkeypatch, tmp_path):
    bot, _, _ = fixture(monkeypatch, path="voice/a.ogg")
    cache = tmp_path / "cache"
    cache.mkdir()
    outside = tmp_path / "protected.ogg"
    outside.write_bytes(b"do not touch")
    link = cache / ("tg-" + hashlib.sha256(b"audio").hexdigest() + ".ogg")
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("host cannot create symbolic links")
    asyncio.run(media.materialize(bot, envelope("audio"), cache))
    assert outside.read_bytes() == b"do not touch"


def test_real_proxy_only_sends_audio_from_tts_cache(monkeypatch, tmp_path):
    bridge = load_personification_module("plugin.personification.handlers.telegram_reply_bridge")
    connections = load_personification_module("plugin.personification.core.telegram_connections")
    from .test_telegram_bridge import BotConfig, TelegramBot
    sent, guarded, observed = [], [], []
    async def send_guarded(native, operation):
        guarded.append(native)
        return await operation()
    service = SimpleNamespace(root=tmp_path / "connections", send_guarded=send_guarded,
        observed=lambda *args, **kwargs: observed.append((args, kwargs)))
    transport = SimpleNamespace(config=SimpleNamespace(nickname=set()), personification_connections=service)
    bot = TelegramBot(transport, "123456", config=BotConfig(token=TOKEN))
    async def send_voice(api, **kwargs):
        assert api == "send_voice"
        sent.append(kwargs)
        return SimpleNamespace(message_id=12)
    # b20 dynamically resolves Bot.send_voice through Bot.call_api.
    monkeypatch.setattr(bot, "call_api", send_voice)
    incoming = SimpleNamespace(chat=SimpleNamespace(id=7), message_id=10, message_thread_id=None)
    proxy = bridge.TelegramBotProxy(bot, incoming, envelope("audio"), object())
    allowed = tmp_path / "tts_cache" / "output.ogg"
    allowed.parent.mkdir()
    allowed.write_bytes(b"generated audio")
    result = asyncio.run(proxy._personification_send_audio_file(None, allowed))
    assert result["message_id"] == "7:m2:12"
    assert sent == [{"chat_id": 7, "reply_parameters": {"message_id": 10, "allow_sending_without_reply": False}, "voice": b"generated audio"}]
    assert guarded == [bot]
    assert observed == [(("123456",), {"delivery": "verified"})]
    forbidden = tmp_path / "credential.ogg"
    forbidden.write_bytes(b"secret")
    with pytest.raises(connections.ConnectionFault):
        asyncio.run(proxy._personification_send_audio_file(None, forbidden))
    assert len(sent) == 1
    assert guarded == [bot]
