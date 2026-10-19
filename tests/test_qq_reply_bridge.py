import asyncio, base64, io
from pathlib import Path
from types import SimpleNamespace as NS
import pytest
from datetime import datetime, timezone
from nonebot.adapters.onebot.v11 import Message, MessageSegment
from ._loader import load_personification_module
m=load_personification_module("plugin.personification.handlers.qq_reply_bridge")
interaction=load_personification_module("plugin.personification.core.qq_interaction")

def source():
    return NS(id="m1",content="hello",timestamp=datetime.now(timezone.utc).isoformat(),group_openid="group-A",author=NS(member_openid="000member"),_reply_seq=0)
def image():
    from PIL import Image
    b=io.BytesIO();Image.new("RGB",(2,2)).save(b,format="PNG");return b.getvalue()
def test_text_and_image_order():
    raw=image();content=Message('before')+MessageSegment.image(raw)+MessageSegment.text('after')
    result=m._standard_parts(content)
    assert [p['type'] for p in result]==['text','image','text']
    assert result[1]['data']==raw and result[-1]['text']=='after'
@pytest.mark.parametrize('segment',[MessageSegment.image('https://example.com/a.png'),MessageSegment.record('https://example.com/a.wav'),MessageSegment.at('123'),MessageSegment.image('base64://bad!')])
def test_unsupported_rejected_without_silent_drop(segment):
    with pytest.raises(ValueError):m._standard_parts(Message('hello')+segment)
def test_proxy_receipt_and_original_event():
    event=source();bot=NS(self_id='app',adapter=object());calls=[]
    class Adapter(interaction.QQOfficialInteractionAdapter):
        async def send(self,b,e,envelope,parts,**kw):
            calls.append((b,e,envelope,parts));return NS(state='sent',message_id='out1')
    adapter=Adapter();env=adapter.normalize(event,bot_id='app')
    proxy=m.QQBotProxy(bot,event,env,adapter)
    assert proxy.adapter is bot.adapter and proxy.self_id==env.bot_namespace
    assert asyncio.run(proxy.send(None,'hello'))=={'message_id':'out1'}
    assert calls[0][1] is event and calls[0][2].sender_id=='000member'
def test_unknown_and_partial_not_retried():
    event=source();bot=NS(self_id='app',adapter=object());calls=[]
    class Adapter(interaction.QQOfficialInteractionAdapter):
        async def send(self,*args,**kw):calls.append(1);return NS(state='unknown',message_id='')
    a=Adapter();proxy=m.QQBotProxy(bot,event,a.normalize(event,bot_id='app'),a)
    with pytest.raises(RuntimeError):asyncio.run(proxy.send(None,'hello'))
    assert calls==[1]
    async def broken(*args,**kw):calls.append(2);raise TimeoutError('partial')
    a.send=broken
    with pytest.raises(TimeoutError):asyncio.run(proxy.send(None,'hello'))
    assert calls==[1,2]
def test_dispatch_passes_raw_envelope(monkeypatch):
    calls=[]
    async def dispatch(*args,**kw):calls.append((args,kw))
    monkeypatch.setattr(m,'dispatch_satori_reply',dispatch)
    class Store:
        document={}
        def mutate_sync(self,name,fn):
            self.document=fn(self.document)
    store=Store()
    monkeypatch.setattr(m,'get_data_store',lambda:store)
    e=source();b=NS(self_id='app')
    asyncio.run(m.dispatch_qq_reply(b,e,{}))
    assert e.to_me and calls[0][1]['source_envelope'].conversation_id=='group-A'
    assert calls[0][1]['proxy_factory'] is m.QQBotProxy
    asyncio.run(m.dispatch_qq_reply(b,e,{}))
    assert len(calls)==1
    e.id='expired';e.timestamp='2020-01-01T00:00:00+00:00'
    asyncio.run(m.dispatch_qq_reply(b,e,{}))
    assert len(calls)==1


def test_real_adapter_173_group_and_c2c(monkeypatch):
    import nonebot.adapters
    root=Path(r"D:/test_artifacts/qq-official-implementation/adapter_deps/nonebot/adapters")
    if not root.is_dir(): pytest.skip('optional adapter-qq 1.7.3 fixture missing')
    monkeypatch.setattr(nonebot.adapters,'__path__',list(nonebot.adapters.__path__)+[str(root)])
    from nonebot.adapters.qq.event import C2CMessageCreateEvent, GroupAtMessageCreateEvent
    adapter=interaction.QQOfficialInteractionAdapter()
    base={'id':'m','content':'hello','timestamp':'2026-10-19T12:00:00+08:00'}
    private=C2CMessageCreateEvent.model_validate({**base,'author':{'id':'raw','user_openid':'000private'}})
    group=GroupAtMessageCreateEvent.model_validate({**base,'group_id':'rawgroup','group_openid':'openid-group',
        'author':{'id':'raw','bot':False,'member_openid':'000member','member_role':'member'}})
    assert adapter.normalize(private,bot_id='app').conversation_kind=='private'
    assert adapter.normalize(group,bot_id='app').sender_id=='000member'
