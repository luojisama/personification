from __future__ import annotations

import asyncio
import json

from ._loader import load_personification_module


diary_flow = load_personification_module("plugin.personification.flows.diary_flow")


class _Logger:
    def __init__(self) -> None:
        self.infos: list[str] = []

    def info(self, message: str) -> None:
        self.infos.append(str(message))

    def warning(self, _message: str) -> None:
        return None

    def debug(self, _message: str) -> None:
        return None

    def error(self, _message: str) -> None:
        return None


class _Store:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def load_sync(self, _name: str):  # noqa: ANN201
        return self.payload


class _Bot:
    async def get_group_list(self):  # noqa: ANN201
        return []


def test_qzone_post_similarity_rejects_repeated_daily_motifs() -> None:
    recent = [
        "明天先带点疲惫，靠炸鸡排和摸鱼慢慢回血。",
        "明天大概会先累一下，但先想去吃块炸鸡排回血。",
    ]

    assert diary_flow._is_too_similar_to_recent_qzone_post(
        "明天先靠炸鸡排和摸鱼给自己回血一下",
        recent,
    )
    assert not diary_flow._is_too_similar_to_recent_qzone_post(
        "刚看到新番截图，突然想把桌面也收拾一下",
        recent,
    )


def test_generate_ai_diary_injects_recent_posts_and_enables_builtin_search(monkeypatch) -> None:  # noqa: ANN001
    logger = _Logger()
    monkeypatch.setattr(
        diary_flow,
        "get_data_store",
        lambda: _Store({"recent_contents": ["明天先靠炸鸡排和摸鱼慢慢回血"]}),
    )
    calls: list[dict] = []

    async def _call_ai(messages, **kwargs):  # noqa: ANN001
        calls.append({"messages": messages, "kwargs": kwargs})
        return json.dumps({"content": "新游戏更新看着有点想摸一下", "image_prompt": ""}, ensure_ascii=False)

    result = asyncio.run(
        diary_flow.generate_ai_diary(
            _Bot(),
            load_prompt=lambda: "你是绪山真寻。",
            call_ai_api=_call_ai,
            logger=logger,
        )
    )

    assert result == "新游戏更新看着有点想摸一下"
    assert calls[0]["kwargs"]["use_builtin_search"] is True
    prompt = calls[0]["messages"][1]["content"]
    assert "明天先靠炸鸡排和摸鱼慢慢回血" in prompt
    assert "游戏、动漫、轻新闻" in prompt


def test_generate_ai_diary_skips_too_similar_recent_post(monkeypatch) -> None:  # noqa: ANN001
    logger = _Logger()
    monkeypatch.setattr(
        diary_flow,
        "get_data_store",
        lambda: _Store({"recent_contents": ["明天先靠炸鸡排和摸鱼慢慢回血"]}),
    )

    async def _call_ai(_messages, **_kwargs):  # noqa: ANN001
        return json.dumps({"content": "明天先靠炸鸡排和摸鱼给自己回血一下", "image_prompt": ""}, ensure_ascii=False)

    result = asyncio.run(
        diary_flow.generate_ai_diary(
            _Bot(),
            load_prompt=lambda: "你是绪山真寻。",
            call_ai_api=_call_ai,
            logger=logger,
        )
    )

    assert result == ""
    assert any("repeats recent content" in item for item in logger.infos)
