from __future__ import annotations
import asyncio
import base64
from types import SimpleNamespace
import pytest
from ._loader import load_personification_module

actions = load_personification_module("plugin.personification.core.telegram_actions")

class Bot:
    self_id = "123"
    def __init__(self, fail_at=0):
        self.calls = []
        self.fail_at = fail_at
    def __getattr__(self, method):
        async def call(**kwargs):
            self.calls.append((method, kwargs))
            if self.fail_at == len(self.calls):
                raise TimeoutError("secret must not leak")
            return SimpleNamespace(message_id=len(self.calls))
        return call


def test_long_text_is_lossless_utf16_and_topic_reply_preserved():
    text = "😀" * 4000 + " 继续\n" * 300
    bot = Bot()
    result = asyncio.run(actions.send_parts(bot, "-100123", text, topic_id="9", reply_to_message_id="8"))
    assert result.state == "sent"
    assert "".join(call[1]["text"] for call in bot.calls) == text
    for _, kwargs in bot.calls:
        assert len(kwargs["text"].encode("utf-16-le")) // 2 <= 4096
        assert kwargs["message_thread_id"] == 9
        assert kwargs["reply_parameters"]["message_id"] == 8
    assert len(result.message_ids) == len(bot.calls)


def test_partial_timeout_stops_without_replaying():
    bot = Bot(fail_at=2)
    result = asyncio.run(actions.send_parts(bot, "1", "x" * 10000))
    assert len(bot.calls) == 2
    assert result.message_ids == ("1",)
    assert result.state == "unknown" and result.partial
    assert result.to_receipt().state == "unknown"
    assert "secret" not in repr(result)


@pytest.mark.parametrize("value", ["C:/secret.txt", "file:///etc/passwd", "../token", "http://127.0.0.1/a", "https://[fe80::1]/a", "https://user:pass@example.com/a", "https://api.telegram.org/file/bot123:SECRET/a", "data:image/png;base64,!!!"])
def test_unsafe_media_fails_before_any_part(value):
    bot = Bot()
    result = asyncio.run(actions.send_parts(bot, 1, [{"type": "text", "text": "first"}, {"type": "image", "image_url": {"url": value}}]))
    assert result.state == "failed"
    assert bot.calls == []


def test_each_supported_media_uses_official_method_with_bytes():
    bot = Bot()
    kinds = {"image": "photo", "voice": "voice", "audio": "voice", "video": "video", "document": "document", "sticker": "sticker", "animation": "animation"}
    parts = [{"type": kind, "data": b"safe bytes"} for kind in kinds]
    result = asyncio.run(actions.send_parts(bot, 1, parts))
    assert result.state == "sent"
    assert [call[0] for call in bot.calls] == ["send_" + kind for kind in kinds.values()]
    for (_, kwargs), kind in zip(bot.calls, kinds.values()):
        assert kwargs[kind] == b"safe bytes"


def test_bounded_data_uri_is_decoded():
    value = "data:audio/ogg;base64," + base64.b64encode(b"ogg").decode()
    bot = Bot()
    result = asyncio.run(actions.send_parts(bot, 1, [{"type": "audio", "audio_url": {"url": value}}]))
    assert result.state == "sent"
    assert bot.calls[0][1]["voice"] == b"ogg"


def test_rate_limit_is_definite_rejection_but_never_retried():
    class Fault(Exception):
        code = "rate_limited"
    bot = Bot()
    async def fail(**kwargs):
        bot.calls.append(kwargs)
        raise Fault()
    bot.send_message = fail
    result = asyncio.run(actions.send_parts(bot, 1, "x"))
    assert result.state == "failed" and result.code == "telegram_rate_limited"
    assert len(bot.calls) == 1


class Moderator:
    self_id = "123"
    def __init__(self, *, permission=True, protected=False, fail_unban=False):
        self.permission, self.protected, self.fail_unban = permission, protected, fail_unban
        self.calls = []
    async def get_chat_member(self, **kwargs):
        self.calls.append(("get_chat_member", kwargs))
        if kwargs["user_id"] == 123:
            return {"status": "administrator", "can_restrict_members": self.permission, "can_delete_messages": self.permission, "can_pin_messages": self.permission}
        return {"status": "creator" if self.protected else "member"}
    async def get_chat(self, **kwargs):
        return {"permissions": {"can_send_messages": True, "can_send_photos": False}}
    def __getattr__(self, method):
        async def call(**kwargs):
            self.calls.append((method, kwargs))
            if method == "unban_chat_member" and self.fail_unban:
                raise TimeoutError()
            return True
        return call


@pytest.mark.parametrize("action", ["mute", "unmute", "kick", "delete", "pin"])
def test_moderation_checks_current_bot_permission(action):
    bot = Moderator(permission=False)
    result = asyncio.run(actions.moderate(bot, action, -1, target_id=42, message_id=9))
    assert result.state == "failed" and result.code == "telegram_permission_denied"
    assert len(bot.calls) == 1


def test_mute_cannot_target_admin():
    bot = Moderator(protected=True)
    result = asyncio.run(actions.moderate(bot, "mute", -1, target_id=42))
    assert result.code == "telegram_target_protected"
    assert all(call[0] == "get_chat_member" for call in bot.calls)


def test_unmute_preserves_group_default_permissions():
    bot = Moderator()
    result = asyncio.run(actions.moderate(bot, "unmute", -1, target_id=42))
    assert result.state == "sent"
    method, kwargs = bot.calls[-1]
    assert method == "restrict_chat_member"
    assert kwargs["permissions"]["can_send_messages"]
    assert not kwargs["permissions"]["can_send_photos"]


def test_kick_partial_is_unknown_and_not_repeated():
    bot = Moderator(fail_unban=True)
    result = asyncio.run(actions.moderate(bot, "kick", -1, target_id=42))
    assert result.state == "unknown" and result.code == "telegram_kick_partial"
    assert [name for name, _ in bot.calls].count("ban_chat_member") == 1
