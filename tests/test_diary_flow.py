from __future__ import annotations

import asyncio
import json

from ._loader import load_personification_module


diary_flow = load_personification_module("plugin.personification.flows.diary_flow")
simple_loop = load_personification_module(
    "plugin.personification.agent.runtime.simple_loop"
)


class _Resp:
    def __init__(self, content: str, tool_calls=None) -> None:
        self.content = content
        self.tool_calls = tool_calls or []


class _ToolCall:
    def __init__(self, call_id: str, name: str, arguments: dict) -> None:
        self.id = call_id
        self.name = name
        self.arguments = arguments


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


def test_run_tool_loop_text_executes_tool_then_returns_content(monkeypatch) -> None:  # noqa: ANN001
    """工具循环：首轮返回 tool_calls 执行工具，次轮返回最终文本。"""

    class _Caller:
        def __init__(self) -> None:
            self.rounds = 0
            self.builtin_search_seen: list = []

        async def chat_with_tools(self, messages, tools, use_builtin_search):  # noqa: ANN001
            self.builtin_search_seen.append(use_builtin_search)
            self.rounds += 1
            if self.rounds == 1:
                return _Resp("", [_ToolCall("c1", "web_search", {"query": "天气"})])
            return _Resp(json.dumps({"content": "今天有点冷", "image_prompt": ""}, ensure_ascii=False))

        def build_tool_result_message(self, call_id, name, result):  # noqa: ANN001
            return {"role": "tool", "tool_call_id": call_id, "name": name, "content": result}

    class _Registry:
        def get(self, _name):  # noqa: ANN001
            return object()

        def openai_schemas(self):  # noqa: ANN201
            return [{"type": "function", "function": {"name": "web_search", "parameters": {}}}]

    executed: list = []

    async def _fake_exec(*, registry, tool_name, tool_args, rewritten_query, user_images, logger):  # noqa: ANN001
        executed.append(tool_name)
        return tool_args, "tianqi 5 度"

    monkeypatch.setattr(simple_loop, "_execute_tool_with_retries", _fake_exec)
    monkeypatch.setattr(
        simple_loop,
        "select_tool_schemas",
        lambda registry, *, has_images, chat_intent="": registry.openai_schemas(),
    )

    caller = _Caller()
    messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "写说说"}]
    out = asyncio.run(
        simple_loop.run_tool_loop_text(
            messages,
            registry=_Registry(),
            tool_caller=caller,
            logger=_Logger(),
            max_steps=4,
            use_builtin_search=True,
        )
    )

    assert json.loads(out)["content"] == "今天有点冷"
    assert caller.rounds == 2
    assert executed == ["web_search"]
    assert caller.builtin_search_seen[0] is True
    # 工具结果被回填进消息历史
    assert any(m.get("role") == "tool" for m in messages)


def test_build_qzone_post_rewrites_ooc_search_talk() -> None:
    """生成内容若漏出"根据搜索结果"等搜索腔，应被去 AI 腔重写。"""

    class _Caller:
        async def chat_with_tools(self, messages, tools, use_builtin_search):  # noqa: ANN001
            assert tools == []  # rewrite 走空工具
            return _Resp("外面风有点大")

        def build_tool_result_message(self, *_a):  # noqa: ANN001
            return {}

    result = asyncio.run(
        diary_flow._build_qzone_post_with_optional_image(
            content="根据搜索结果，今天天气不错",
            image_prompt="",
            tool_caller=_Caller(),
            logger=_Logger(),
            recent_posts=[],
            persona_system="你是某角色",
        )
    )

    assert result == "外面风有点大"


def test_build_qzone_post_drops_when_ooc_rewrite_fails() -> None:
    """去 AI 腔重写失败（仍是搜索腔）则丢弃该条，宁缺勿发。"""

    class _Caller:
        async def chat_with_tools(self, messages, tools, use_builtin_search):  # noqa: ANN001
            return _Resp("我查了一下资料发现")  # 仍是 OOC

        def build_tool_result_message(self, *_a):  # noqa: ANN001
            return {}

    result = asyncio.run(
        diary_flow._build_qzone_post_with_optional_image(
            content="根据搜索结果，今天天气不错",
            image_prompt="",
            tool_caller=_Caller(),
            logger=_Logger(),
            recent_posts=[],
            persona_system="x",
        )
    )

    assert result == ""


def test_review_qzone_post_rewrites_net_slang_tic() -> None:
    """『也太……了吧』等营业感叹腔会被改写成平铺直叙。"""

    class _Caller:
        async def chat_with_tools(self, messages, tools, use_builtin_search):  # noqa: ANN001
            assert tools == []
            return _Resp("上班还能摸鱼打两局游戏")

        def build_tool_result_message(self, *_a):  # noqa: ANN001
            return {}

    result = asyncio.run(
        diary_flow._review_qzone_post(
            "上班能打游戏也太爽了吧",
            tool_caller=_Caller(),
            persona_system="你是某角色",
            logger=_Logger(),
        )
    )

    assert result == "上班还能摸鱼打两局游戏"
    assert not diary_flow._NET_SLANG_TIC_RE.search(result)


def test_review_qzone_post_keeps_clean_text_untouched() -> None:
    """不含营业感叹腔/搜索腔的正文不调用改写，原样返回。"""

    class _Caller:
        async def chat_with_tools(self, messages, tools, use_builtin_search):  # noqa: ANN001
            raise AssertionError("clean text should not be rewritten")

        def build_tool_result_message(self, *_a):  # noqa: ANN001
            return {}

    result = asyncio.run(
        diary_flow._review_qzone_post(
            "下午三点的阳光有点刺眼",
            tool_caller=_Caller(),
            persona_system="x",
            logger=_Logger(),
        )
    )

    assert result == "下午三点的阳光有点刺眼"
