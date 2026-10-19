import ast
import asyncio
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace as NS

from ._loader import load_personification_module

qq = load_personification_module("plugin.personification.core.qq_interaction")
notify = load_personification_module("plugin.personification.core.notify")
jobs = load_personification_module("plugin.personification.jobs.periodic_jobs")


def official_bot():
    base = type("Bot", (), {"__module__": "nonebot.adapters.qq.bot"})
    class CustomBot(base):
        def __getattr__(self, name):
            raise AssertionError(f"official QQ must not receive {name}")
    return CustomBot()


def test_official_subclasses_skip_admin_push_but_onebot_still_sends():
    calls = []
    async def call_api(*args, **kwargs):
        calls.append((args, kwargs))
    bot = official_bot()
    assert qq.native_qq(bot)
    assert not qq.native_qq(NS())
    assert not asyncio.run(notify.send_to_admin(lambda: {"official": bot}, "123", "hello"))
    assert asyncio.run(notify.send_to_admin(lambda: {"official": bot, "onebot": NS(call_api=call_api)}, "123", "hello"))
    assert len(calls) == 1


def test_daily_report_skips_official_bot_and_preserves_onebot():
    calls = []
    async def get_group_info(**kwargs):
        calls.append("group")
        return {"group_name": "test"}
    async def send_private_msg(**kwargs):
        calls.append("send")
    now = datetime(2026, 10, 19)
    result = asyncio.run(jobs.run_daily_group_fav_report(
        sign_in_available=True,
        load_data=lambda: {"group_123": {"last_update": "2026-10-19", "daily_fav_count": 1}},
        get_now=lambda: now,
        get_bots=lambda: {"official": official_bot(), "onebot": NS(get_group_info=get_group_info, send_private_msg=send_private_msg)},
        superusers=["123"], logger=NS(info=lambda *a: None, error=lambda *a: None),
    ))
    assert result == 1
    assert calls == ["group", "send"]


def test_restored_user_task_skips_official_before_any_bot_api():
    # Extract the real nested caller without importing the plugin or connecting bots.
    tree = ast.parse((Path(__file__).parents[1] / "__init__.py").read_text(encoding="utf-8"))
    restore = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "_restore_user_tasks")
    caller = next(n for n in restore.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "_bot_caller")
    calls = []
    async def get_friend_list():
        calls.append("friends")
        return [{"user_id": 123}]
    async def send_private_msg(**kwargs):
        calls.append("send")
    scope = dict(native_qq=qq.native_qq, native_telegram=lambda b: False,
        guard_scheduled_user_task_message=lambda task, message, **kw: message,
        get_bots=lambda: {"official": official_bot(), "onebot": NS(get_friend_list=get_friend_list, send_private_msg=send_private_msg)},
        logger=NS(warning=lambda *a: None), qq_outbound_ledger=None)
    exec(compile(ast.Module(body=[caller], type_ignores=[]), "isolated_user_task", "exec"), scope)
    task = {"user_id": "123", "message": "hello"}
    asyncio.run(scope["_bot_caller"](task))
    assert task["last_status"] == "sent"
    assert calls == ["friends", "send"]
