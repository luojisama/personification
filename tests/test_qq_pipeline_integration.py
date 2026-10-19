"""Actual buffered QQ -> shared processor -> helper, with no external I/O."""
import asyncio
import importlib.util
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace as NS
import nonebot.adapters
import pytest
from nonebot.adapters.onebot.v11 import Message, MessageSegment, MessageEvent, GroupMessageEvent, PrivateMessageEvent
from ._loader import load_personification_module

satori=Path(os.environ.get('PERSONIFICATION_SATORI_ADAPTERS',r'D:/test_artifacts/personification/implementation0906/satori-deps/nonebot/adapters'))
qq=Path(r'D:/test_artifacts/qq-official-implementation/adapter_deps/nonebot/adapters')
for root in (satori,qq):
    if root.is_dir() and str(root) not in nonebot.adapters.__path__: nonebot.adapters.__path__.append(str(root))
pytest.importorskip('nonebot.adapters.satori.event')
qq_events=pytest.importorskip('nonebot.adapters.qq.event')
from .test_satori_pipeline_integration import _deps, processor, reply_buffer
bridge=load_personification_module('plugin.personification.handlers.qq_reply_bridge')

@pytest.mark.parametrize('group',[False,True])
def test_qq_real_pipeline_text_to_confirmed_helper_receipt(monkeypatch,group):
    helper_path=Path(r'D:/test_artifacts/qq-official-implementation/qq_output.py')
    if not helper_path.is_file(): pytest.skip('optional local QQ helper missing')
    spec=importlib.util.spec_from_file_location('qq_output',helper_path)
    helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)
    if not hasattr(helper,'send_parts_output'): pytest.skip('ordered helper missing')
    import sys
    monkeypatch.setitem(sys.modules,'qq_output',helper)
    class Store:
        def mutate_sync(self,key,callback): callback({})
    monkeypatch.setattr(bridge,'get_data_store',lambda:Store())
    observed=[]
    deps,config,logger,wire,images,logs,errors=_deps(monkeypatch,observed)
    timestamp=datetime.now(timezone.utc).isoformat()
    payload={'id':'incoming-qq','content':'hello','timestamp':timestamp}
    if group:
        payload.update(group_id='raw-group',group_openid='000group',author={'id':'raw-author','bot':False,'member_openid':'000member','member_role':'member'})
        event=qq_events.GroupAtMessageCreateEvent.model_validate(payload)
    else:
        payload['author']={'id':'raw-author','user_openid':'000private'}
        event=qq_events.C2CMessageCreateEvent.model_validate(payload)
    sent=[];projected=[]
    class Bot:
        self_id='app-test'
        adapter=NS(get_name=lambda:'QQ')
        async def send(self,*,event,message):
            assert event is original
            event._reply_seq+=1
            sent.append(message)
            return NS(id='confirmed-'+str(len(sent)))
    original=event
    async def rule(e,state):
        projected.append(e)
        return True
    done=asyncio.Event()
    async def process(bot,event,state,current_deps):
        try: await processor.process_response_logic(bot,event,state,current_deps)
        finally: done.set()
    async def timer(*args,**kwargs):
        kwargs['delay']=0
        await reply_buffer.run_buffer_timer(*args,**kwargs)
    async def run():
        await bridge.dispatch_qq_reply(Bot(),event,{},personification_rule=rule,
            process_response_logic=process,reply_processor_deps=deps,msg_buffer={},
            message_cls=Message,message_segment_cls=MessageSegment,message_event_cls=MessageEvent,
            group_message_event_cls=GroupMessageEvent,private_message_event_cls=PrivateMessageEvent,
            poke_event_cls=deps.types.poke_event_cls,logger=logger,plugin_config=config,run_buffer_timer=timer)
        await asyncio.wait_for(done.wait(),timeout=3)
    asyncio.run(run())
    assert observed and sent and not errors,(logs,errors)
    assert '看到了' in str(sent[-1]) and '[CQ:' not in str(sent[-1])
    assert projected[0].platform=='qq_official'
    assert projected[0].user_id.startswith('p11:qq_official')
    assert projected[0].sender.nickname==projected[0].sender.card==''
    assert event._reply_seq>=1
    serialized=json.dumps(observed,ensure_ascii=False)
    assert 'qq_official' in serialized
    assert 'raw-author' not in serialized
