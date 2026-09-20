import ast
import asyncio
import inspect
from pathlib import Path
from types import SimpleNamespace

from ._loader import load_personification_module

chat_matchers = load_personification_module("plugin.personification.handlers.chat_matchers")
reply_matchers = load_personification_module("plugin.personification.handlers.reply_matchers")
handle_record_message_event = load_personification_module(
    "plugin.personification.handlers.record_message_handler"
).handle_record_message_event


class _Matcher:
    def __init__(self) -> None:
        self.handler = None

    def handle(self):  # noqa: ANN201
        def decorate(handler):  # noqa: ANN001, ANN202
            self.handler = handler
            return handler

        return decorate


def test_record_matcher_forwards_private_profile_callback(monkeypatch) -> None:  # noqa: ANN001
    registered: list[tuple[object, dict[str, object]]] = []
    matchers = [_Matcher(), _Matcher()]

    def fake_on_message(*_args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        matcher = matchers.pop(0)
        registered.append((matcher, kwargs))
        return matcher

    monkeypatch.setattr(chat_matchers, "on_message", fake_on_message)
    monkeypatch.setattr(chat_matchers, "Rule", lambda predicate: predicate)
    private_profile_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def private_profile_callback(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        private_profile_calls.append((args, kwargs))

    chat_matchers.register_chat_matchers(
        record_msg_rule=lambda _event: True,
        sticker_chat_rule=lambda _event: False,
        handle_record_message_event=handle_record_message_event,
        resolve_record_message=lambda *_args, **_kwargs: ("", False),
        record_group_msg=object(),
        logger=object(),
        create_background_task=lambda _group_id: None,
        create_summary_task=None,
        handle_sticker_chat_event=object(),
        get_group_config=lambda _group_id: {},
        sticker_path="",
        plugin_config=object(),
        message_segment_cls=object(),
        handle_reply=object(),
        create_private_profile_task=private_profile_callback,
    )

    record_matcher, record_options = registered[0]
    assert record_options["priority"] == 999
    assert record_matcher.handler is not None
    event = SimpleNamespace(
        user_id="42",
        platform="onebot",
        self_id="bot-1",
        message_id="message-9",
        message=SimpleNamespace(extract_plain_text=lambda: "private hello"),
    )
    asyncio.run(record_matcher.handler(SimpleNamespace(), event))
    assert private_profile_calls == [
        (("42",), {"platform": "onebot", "bot_id": "bot-1", "source_id": "message-9", "text": "private hello"})
    ]


def test_composition_registration_keywords_bind_to_chat_and_reply_signatures() -> None:
    root = Path(__file__).resolve().parents[1]
    composition = ast.parse(
        (root / "handlers" / "setup" / "composition.py").read_text(encoding="utf-8")
    )
    registration_functions = {
        "register_chat_matchers": chat_matchers.register_chat_matchers,
        "register_reply_matchers": reply_matchers.register_reply_matchers,
    }
    calls = {
        node.func.id: node
        for node in ast.walk(composition)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in registration_functions
    }
    assert calls.keys() == registration_functions.keys()
    for name, function in registration_functions.items():
        keyword_values = {
            keyword.arg: object()
            for keyword in calls[name].keywords
            if keyword.arg is not None
        }
        inspect.signature(function).bind_partial(**keyword_values)
