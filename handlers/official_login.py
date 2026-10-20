"""Explicit private command; never enters conversational memory or LLMs."""
from ..core.official_webui_login import configured_service


async def deliver_login(bot, event, send):
    parts = event.get_plaintext().strip().split()
    if len(parts) != 2:
        await send(bot,event,'请先在 WebUI 选择“官方 QQ 私聊领取”，再发送页面提供的 /登录 命令。')
        return
    service = None
    try:
        service = configured_service()
        code = service.claim(parts[1],str(bot.self_id),event.author.user_openid)
    except ValueError as exc:
        await send(bot,event,str(exc))
        return
    try:
        await send(bot,event,f'【拟人插件 WebUI】验证码：{code}\n仅用于你刚才发起的登录，网页申请后 5 分钟内有效。请勿转发。')
    except BaseException:
        service.discard(parts[1])
        raise


def register_official_login():
    from nonebot import on_message
    from nonebot.adapters import Bot
    from nonebot.adapters.qq import C2CMessageCreateEvent
    from nonebot.rule import Rule

    original = C2CMessageCreateEvent.get_log_string
    if not getattr(original,'_login_redacted',False):
        def safe_log(self):
            if self.get_plaintext().strip().split(maxsplit=1)[:1] == ['/登录']:
                return '[WebUI login]: private request (payload omitted)'
            return original(self)
        safe_log._login_redacted = True
        C2CMessageCreateEvent.get_log_string = safe_log

    async def rule(event: C2CMessageCreateEvent):
        return event.get_plaintext().strip().split(maxsplit=1)[:1] == ['/登录']

    matcher = on_message(rule=Rule(rule),priority=1,block=True)

    @matcher.handle()
    async def handle(bot: Bot,event: C2CMessageCreateEvent):
        from qq_output import send_output
        await deliver_login(bot,event,send_output)

    return {'official_webui_login':matcher}
