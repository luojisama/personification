from __future__ import annotations

import asyncio
import base64
import io
from pathlib import Path
from types import SimpleNamespace
from PIL import Image

from ._loader import load_personification_module


tools = load_personification_module("plugin.personification.core.qq_expression_tools")
executor_mod = load_personification_module("plugin.personification.agent.action_executor")
preparation = load_personification_module("plugin.personification.core.expression_preparation")


class _Bot:
    def __init__(self): self.sent = []
    async def send(self, _event, message):
        self.sent.append(message)
        return {"message_id": str(len(self.sent))}


class _Logger:
    def warning(self, *_a, **_k): pass


def _png(path: Path):
    Image.new("RGB", (8, 8), "pink").save(path, "PNG")


def test_action_executor_local_sticker_final_gate(tmp_path, monkeypatch):
    image = tmp_path / "ok.png"; _png(image)
    outside = tmp_path.parent / "escape.png"; _png(outside)
    cfg = SimpleNamespace(personification_sticker_path=str(tmp_path), personification_expression_enabled=True, personification_local_expression_enabled=True)
    bot = _Bot()
    executor = executor_mod.ActionExecutor(bot, SimpleNamespace(get_plaintext=lambda: "语境"), cfg, _Logger(), core_persona="核心人格", expression_review_caller=(lambda *_a, **_k: None), runtime=SimpleNamespace())
    media = load_personification_module("plugin.personification.core.media_understanding")
    async def deny(**_kw): return '{"allow":false}', "fake"
    monkeypatch.setattr(media, "analyze_images_with_route_or_fallback", deny)
    assert "未通过" in asyncio.run(executor.execute("send_sticker", {"path": str(image)}))
    assert bot.sent == []
    cfg.personification_expression_enabled = False
    assert "拦截" in asyncio.run(executor.execute("send_sticker", {"path": str(image)}))
    assert bot.sent == []
    cfg.personification_expression_enabled = True
    asyncio.run(executor.execute("send_sticker", {"path": str(outside)}))
    assert bot.sent == []
    async def allow(**kwargs):
        assert kwargs["image_refs"][0].startswith("data:image/png;base64,")
        return '{"allow":true}', "fake"
    monkeypatch.setattr(media, "analyze_images_with_route_or_fallback", allow)
    asyncio.run(executor.execute("send_sticker", {"path": str(image)}))
    assert len(bot.sent) == 1 and executor.last_delivery_confirmed
    async def disable_while_reviewing(**kwargs):
        executor.runtime.plugin_config = SimpleNamespace(personification_expression_enabled=False)
        return '{"allow":true}', "fake"
    monkeypatch.setattr(media, "analyze_images_with_route_or_fallback", disable_while_reviewing)
    asyncio.run(executor.execute("send_sticker", {"path": str(image)}))
    assert len(bot.sent) == 1


def test_local_expression_final_gate_rechecks_group_switch_and_rejects_invalid_verdict(tmp_path, monkeypatch):
    image = tmp_path / "ok.png"; _png(image)
    config = SimpleNamespace(
        personification_sticker_path=str(tmp_path),
        personification_expression_enabled=True,
        personification_local_expression_enabled=True,
    )
    runtime = SimpleNamespace(plugin_config=config)
    media = load_personification_module("plugin.personification.core.media_understanding")

    async def allow(**_kwargs):
        return '{"allow":true}', "fixture"

    monkeypatch.setattr(media, "analyze_images_with_route_or_fallback", allow)
    monkeypatch.setattr(preparation, "_group_sticker_enabled", lambda _group_id: False)
    assert asyncio.run(preparation.prepare_local_expression(
        path=image, config=config, core_persona="温和人格", runtime=runtime, context="群聊", group_id="20001",
    )) is None

    monkeypatch.setattr(preparation, "_group_sticker_enabled", lambda _group_id: True)
    async def malformed(**_kwargs):
        return "allow", "fixture"
    monkeypatch.setattr(media, "analyze_images_with_route_or_fallback", malformed)
    assert asyncio.run(preparation.prepare_local_expression(
        path=image, config=config, core_persona="温和人格", runtime=runtime, context="群聊", group_id="20001",
    )) is None


def test_expression_semantic_review_uses_downloaded_visual_evidence(monkeypatch):
    downloaded = []
    async def fake_download(url, **_kwargs):
        downloaded.append(url)
        output = io.BytesIO()
        Image.new("RGB", (8, 8), "pink").save(output, "PNG")
        return safe.DownloadedImage(
            content=output.getvalue(),
            content_type="image/png", final_url=url,
        )
    async def fake_visual(**kwargs):
        assert kwargs["image_refs"][0].startswith("data:image/png;base64,")
        return '{"allow":true}', "fake"
    safe = load_personification_module("plugin.personification.core.safe_image_download")
    media = load_personification_module("plugin.personification.core.media_understanding")
    monkeypatch.setattr(safe, "download_public_image", fake_download)
    monkeypatch.setattr(media, "analyze_images_with_route_or_fallback", fake_visual)
    executor = SimpleNamespace(core_persona="管理员核心人格", expression_review_caller=(lambda *_a, **_k: None), runtime=SimpleNamespace())
    assert asyncio.run(tools.expression_semantic_review(executor=executor, url="https://example.test/a.png", source="qq_recommended"))
    assert downloaded == ["https://example.test/a.png"]


def test_expression_semantic_review_missing_persona_or_invalid_url_never_downloads(monkeypatch):
    calls = []
    async def fake_download(*_args, **_kwargs): calls.append(1)
    safe = load_personification_module("plugin.personification.core.safe_image_download")
    monkeypatch.setattr(safe, "download_public_image", fake_download)
    assert not asyncio.run(tools.expression_semantic_review(executor=SimpleNamespace(), url="https://example.test/a.png", source="qq_favorite"))
    assert not asyncio.run(tools.expression_semantic_review(executor=SimpleNamespace(core_persona="x", expression_review_caller=(lambda: None)), url="file:///x", source="qq_favorite"))
    assert calls == []
