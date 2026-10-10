"""Bounded credential-bearing downloads, separated from model-visible media refs."""
from __future__ import annotations

import base64
import hashlib
import io
import logging
import os
import re
import tempfile
import time
from dataclasses import replace
from pathlib import Path, PurePosixPath
from typing import Any

from .interaction_adapter import InteractionEnvelope


class _TokenURLFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        text = record.getMessage()
        safe = re.sub(r"(/(?:file/)?bot)[0-9]+:[A-Za-z0-9_-]+", r"\1[REDACTED]", text)
        if safe != text:
            record.msg, record.args = safe, ()
        return True


def protect_transport_logs() -> None:
    # Logger filters do not propagate to descendant loggers. Include the
    # transport's actual emitters rather than relying on their parent filter.
    for name in ("httpx", "httpcore", "httpcore.connection", "httpcore.http11", "httpcore.http2", "httpcore.proxy", "httpcore.socks"):
        logger = logging.getLogger(name)
        if not any(isinstance(item, _TokenURLFilter) for item in logger.filters):
            logger.addFilter(_TokenURLFilter())


async def materialize(bot: Any, envelope: InteractionEnvelope, root: Path) -> InteractionEnvelope:
    import httpx
    from PIL import Image

    protect_transport_logs()

    output = []
    total = 0
    root.mkdir(parents=True, exist_ok=True)
    # Only this service's hashed cache assets; never follow local references.
    for old in root.glob("tg-*"):
        if old.is_file() and not old.is_symlink() and old.stat().st_mtime < time.time() - 86400:
            old.unlink(missing_ok=True)
    for media in envelope.ordered_media[:10]:
        try:
            if media.kind not in {"image", "audio", "video"}:
                output.append(replace(media, resolution_code="telegram_media_unsupported"))
                continue
            result = await bot.get_file(file_id=media.ref.removeprefix("tgfile:"))
            path = str(result.file_path or "")
            parts = PurePosixPath(path)
            if not path or parts.is_absolute() or ".." in parts.parts or ":" in path or "\\" in path or "?" in path or "#" in path:
                raise ValueError("invalid remote file path")
            if int(result.file_size or 0) > 20 * 1024 * 1024:
                raise ValueError("too large")
            settings = bot.bot_config
            proxy = getattr(bot.adapter, "personification_proxy", None) or getattr(bot.adapter.adapter_config, "proxy", None)
            url = settings.api_server.rstrip("/") + "/file/bot" + settings.token + "/" + path
            async with httpx.AsyncClient(proxy=proxy, timeout=30, follow_redirects=False, trust_env=False) as client:
                async with client.stream("GET", url) as response:
                    response.raise_for_status()
                    payload = bytearray()
                    async for chunk in response.aiter_bytes():
                        payload.extend(chunk)
                        total += len(chunk)
                        if len(payload) > 20 * 1024 * 1024 or total > 32 * 1024 * 1024:
                            raise ValueError("media budget")
            digest = hashlib.sha256(payload).hexdigest()
            if media.kind == "image":
                with Image.open(io.BytesIO(payload)) as image:
                    if image.width * image.height > 25_000_000:
                        raise ValueError("image dimensions")
                    mime = Image.MIME.get(image.format or "", "")
                    image.verify()
                if mime not in {"image/png", "image/jpeg", "image/webp", "image/gif"}:
                    raise ValueError("image type")
                ref = f"data:{mime};base64," + base64.b64encode(payload).decode("ascii")
            else:
                suffix = parts.suffix.lower()
                if suffix not in {".ogg", ".oga", ".opus", ".mp3", ".wav", ".m4a", ".mp4", ".webm", ".mov"}:
                    raise ValueError("media type")
                destination = root / ("tg-" + digest + suffix)
                with tempfile.NamedTemporaryFile(dir=root, prefix="tg-stage-", delete=False) as stage:
                    temporary = Path(stage.name)
                    stage.write(payload)
                try:
                    os.replace(temporary, destination)
                finally:
                    temporary.unlink(missing_ok=True)
                ref = str(destination.resolve())
            output.append(replace(media, ref=ref, content_hash=digest, resolution_code="telegram_downloaded"))
        except Exception:
            # Never retain exception text containing the URL or bot token.
            output.append(replace(media, resolution_code="telegram_download_failed"))
    return replace(envelope, ordered_media=tuple(output))
