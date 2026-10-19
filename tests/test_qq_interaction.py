from types import SimpleNamespace as NS
import asyncio
import pytest
from ._loader import load_personification_module

module = load_personification_module("plugin.personification.core.qq_interaction")
Adapter = module.QQOfficialInteractionAdapter

def group(**changes):
    values=dict(id="msg-001",content="文本",group_openid="000group-A",
        author=NS(id="not-the-openid",member_openid="000member-B",member_role="admin"),
        attachments=[NS(content_type="image/png",url="cdn.example.test/a.png")])
    values.update(changes)
    return NS(**values)

def test_real_group_fields_and_media_ownership():
    result=Adapter().normalize(group(),bot_id="app-A")
    assert result.platform=="qq_official" and result.conversation_kind=="group"
    assert result.conversation_id=="000group-A" and result.sender_id=="000member-B"
    assert result.message_id=="msg-001" and result.text=="文本"
    media=result.ordered_media[0]
    assert media.owner_user_id=="000member-B" and media.message_id=="msg-001"
    assert media.group_id=="000group-A" and media.ref=="https://cdn.example.test/a.png"
    assert media.content_hash=="" and not Adapter().capabilities(None).group_moderation

def test_c2c_string_id_and_reply():
    event=NS(id="m",content="私聊",author=NS(id="other",user_openid="00042"),
        reply=NS(id="previous"),attachments=None)
    result=Adapter().normalize(event,bot_id="app")
    assert result.sender_id==result.conversation_id=="00042"
    assert result.conversation_kind=="private" and result.reply_to_message_id=="previous"

@pytest.mark.parametrize("changes",[{"id":""},{"content":None},{"group_openid":""},
    {"author":NS(id="123")},{"attachments":[NS(content_type="image/png",url=None)]}])
def test_missing_fields_rejected(changes):
    with pytest.raises(ValueError): Adapter().normalize(group(**changes),bot_id="app")

def test_c2c_does_not_fallback_to_author_id():
    with pytest.raises(ValueError):
        Adapter().normalize(NS(id="m",content="",author=NS(id="123")),bot_id="app")

def test_outbound_explicitly_unavailable():
    adapter=Adapter()
    assert not adapter.capabilities(None).text and not adapter.capabilities(None).image
    with pytest.raises(NotImplementedError):
        asyncio.run(adapter.send(None,None,None,"hello"))


def test_ordered_send_and_raw_event(monkeypatch):
    calls=[]
    async def helper(bot,event,parts):
        calls.append((event,parts));return [NS(id=str(i)) for i in range(len(parts))]
    monkeypatch.setattr(module,"_sender",lambda:helper)
    adapter=Adapter();event=group();event._reply_seq=2
    envelope=adapter.normalize(event,bot_id="app")
    parts=[{"type":"text","text":"first"},{"type":"image","data":b"png"},{"type":"text","text":"last"}]
    receipt=asyncio.run(adapter.send(None,event,envelope,parts))
    assert receipt.state=="sent" and calls==[(event,parts)]
    assert event._reply_seq==2 and adapter.capabilities(None).image


def test_invalid_tail_rejected_before_helper(monkeypatch):
    calls=[]
    async def helper(*args): calls.append(args)
    monkeypatch.setattr(module,"_sender",lambda:helper)
    event=group();event._reply_seq=0;adapter=Adapter();env=adapter.normalize(event,bot_id="a")
    with pytest.raises(ValueError):
        asyncio.run(adapter.send(None,event,env,[{"type":"text","text":"ok"},{"type":"image","data":"https://example.com"}]))
    assert not calls


def test_missing_id_unknown_no_retry(monkeypatch):
    calls=[]
    async def helper(*args): calls.append(args);return [NS(id=None)]
    monkeypatch.setattr(module,"_sender",lambda:helper)
    event=group();event._reply_seq=0;adapter=Adapter();env=adapter.normalize(event,bot_id="a")
    assert asyncio.run(adapter.send(None,event,env,"hello")).state=="unknown"
    assert len(calls)==1


def test_partial_failure_propagates(monkeypatch):
    calls=[]
    async def helper(*args): calls.append(args);raise TimeoutError("unknown delivery")
    monkeypatch.setattr(module,"_sender",lambda:helper)
    event=group();event._reply_seq=0;adapter=Adapter();env=adapter.normalize(event,bot_id="a")
    with pytest.raises(TimeoutError): asyncio.run(adapter.send(None,event,env,"hello"))
    assert len(calls)==1
