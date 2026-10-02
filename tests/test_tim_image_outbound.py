from __future__ import annotations
import asyncio
import base64
from types import SimpleNamespace
import pytest
from nonebot.adapters.onebot.v11 import Message, MessageSegment
from ._loader import load_personification_module
mod = load_personification_module("plugin.personification.core.tim_image_outbound")
PNG = b"\x89PNG\r\n\x1a\nfixture"

def identity(monkeypatch, name="TIM OneBot Bridge"):
    async def value(): return SimpleNamespace(app_name=name)
    monkeypatch.setattr(mod, "get_protocol_adapter", lambda *_: SimpleNamespace(identity=value))

def test_tim_local_image_is_copied_atomically_and_normal_type(monkeypatch, tmp_path):
    identity(monkeypatch)
    monkeypatch.setattr(mod.tempfile, 'gettempdir', lambda: str(tmp_path))
    path=tmp_path / "image.png"; path.write_bytes(PNG)
    message=Message([MessageSegment.text("caption"),MessageSegment("image",{"file":path.as_uri(),"type":None})])
    prepared=asyncio.run(mod.prepare_tim_message(object(),message))
    assert prepared[1].data["file"] == "base64://" + base64.b64encode(PNG).decode()
    assert prepared[1].data["type"] == "normal"
    assert message[1].data["file"] == path.as_uri()
    assert mod.tim_send_options(message,prepared) == {"_timeout":95}

def test_other_adapter_unchanged_and_plain_cq_not_upgraded(monkeypatch):
    identity(monkeypatch,"NapCat")
    message=MessageSegment.image("file:///does/not/exist.png")
    assert asyncio.run(mod.prepare_tim_message(object(),message)) is message
    assert asyncio.run(mod.prepare_tim_message(object(),"[CQ:image,file=secret]")) == "[CQ:image,file=secret]"

def test_invalid_media_rejects_entire_caption_and_budget(monkeypatch):
    identity(monkeypatch)
    message=Message([MessageSegment.text("caption"),MessageSegment.image("base64://AAAA")])
    with pytest.raises(mod.TimImagePreparationError): asyncio.run(mod.prepare_tim_message(object(),message))
    assert message[0].data["text"] == "caption"
    with pytest.raises(mod.TimImagePreparationError): mod._decode("A"*16,1)
    with pytest.raises(mod.TimImagePreparationError): mod._prepare(Message([MessageSegment.image("https://example.org/a.png")]*5))

def test_data_and_bytes_aggregate_limit_and_no_arbitrary_path(monkeypatch, tmp_path):
    identity(monkeypatch)
    for value in (PNG,"data:image/png;base64,"+base64.b64encode(PNG).decode()):
        prepared=asyncio.run(mod.prepare_tim_message(object(),MessageSegment("image",{"file":value})))
        assert prepared.data["file"].startswith("base64://")
    monkeypatch.setattr(mod,"MAX_IMAGE_BYTES",len(PNG))
    with pytest.raises(mod.TimImagePreparationError): mod._prepare(Message([MessageSegment.image("base64://"+base64.b64encode(PNG).decode())]*2))


def test_actual_agent_send_prepares_before_send_and_preserves_caption_failure(monkeypatch):
    identity(monkeypatch)
    executor_mod=load_personification_module("plugin.personification.agent.action_executor")
    sent=[]
    class Bot:
        async def send(self,event,message,**options):
            sent.append((message,options));return {"message_id":123}
    executor=object.__new__(executor_mod.ActionExecutor)
    executor.bot=Bot();executor.event=SimpleNamespace();executor.runtime=None;executor._config=None
    executor.qq_outbound_ledger=None;executor.last_delivery_confirmed=False
    asyncio.run(executor._send(MessageSegment("image",{"file":PNG,"type":None}),surface="fixture"))
    assert sent[0][1] == {"_timeout":95}
    assert sent[0][0].data["type"] == "normal"
    invalid=Message([MessageSegment.text("must not escape alone"),MessageSegment.image("base64://AAAA")])
    with pytest.raises(mod.TimImagePreparationError): asyncio.run(executor._send(invalid,surface="fixture"))
    assert len(sent)==1

@pytest.mark.parametrize("surface", ["normal_reply", "yaml_reply"])
def test_actual_shared_reply_dispatch_prepares_ledger_and_confirms_receipt(monkeypatch, tmp_path, surface):
    identity(monkeypatch)
    pipeline=load_personification_module("plugin.personification.handlers.reply_pipeline.pipeline_context")
    db=load_personification_module("plugin.personification.core.db")
    outbound=load_personification_module("plugin.personification.core.qq_outbound")
    ledger=outbound.QQOutboundLedger(db.init_db_sync(tmp_path),content_hmac_key=b"k"*32)
    observed=[]
    original_dispatch=ledger.dispatch
    async def dispatch(context,content,send,**kwargs):
        observed.append(("ledger",content));return await original_dispatch(context,content,send,**kwargs)
    monkeypatch.setattr(ledger,"dispatch",dispatch)
    class Bot:
        self_id="123456"
        async def send(self,event,payload,**options):
            observed.append(("send",payload,options));return {"status":"ok","retcode":0,"data":{"message_id":123}}
    original=Message([MessageSegment.text("reviewed caption"),MessageSegment("image",{"file":PNG,"type":None})])
    receipt=asyncio.run(pipeline.dispatch_reply_part(bot=Bot(),event=SimpleNamespace(group_id=654321,user_id=1,message_id=2),payload=original,ledger=ledger,surface=surface,on_delivery_started=lambda: observed.append(("started",))))
    assert receipt.status == "sent"
    assert str(receipt.message_id) == "123"
    assert observed[0] == ("started",)
    prepared=observed[1][1]
    assert prepared is observed[2][1]
    assert prepared[1].data["file"].startswith("base64://")
    assert prepared[1].data["type"] == "normal"
    assert observed[2][2] == {"_timeout":95}
    assert original[1].data["file"] == PNG

@pytest.mark.parametrize("surface", ["normal_reply", "yaml_reply"])
def test_shared_reply_invalid_image_never_enters_ledger_or_sends_caption(monkeypatch, surface):
    identity(monkeypatch)
    pipeline=load_personification_module("plugin.personification.handlers.reply_pipeline.pipeline_context")
    calls=[]
    class Ledger:
        async def dispatch(self,*args,**kwargs):calls.append("ledger");raise AssertionError("must not dispatch")
    class Bot:
        self_id="123456"
        async def send(self,*args,**kwargs):calls.append("send");return {"message_id":123}
    payload=Message([MessageSegment.text("caption must not escape"),MessageSegment.image("base64://AAAA")])
    with pytest.raises(mod.TimImagePreparationError):
        asyncio.run(pipeline.dispatch_reply_part(bot=Bot(),event=SimpleNamespace(group_id=654321,user_id=1),payload=payload,ledger=Ledger(),surface=surface,on_delivery_started=lambda: calls.append("started")))
    assert calls == []


def test_memoryview_budget_counts_bytes_not_elements(monkeypatch):
    payload=memoryview(bytearray(PNG+b"\0"*((4-len(PNG)%4)%4))).cast("I")
    assert len(payload)<payload.nbytes
    with pytest.raises(mod.TimImagePreparationError): mod._image_bytes(payload,len(payload))


@pytest.mark.parametrize("mode", ["none", "napcat"])
def test_disabled_and_forced_identity_preserve_media_without_discovery(mode):
    class Bot:
        self_id = "123456"
        async def call_api(self, *args, **kwargs):
            raise AssertionError("explicit identity must not trigger discovery")
    original = MessageSegment.image("file:///not/an/allowed/file.png")
    config = SimpleNamespace(personification_protocol_extensions=mode)
    assert asyncio.run(mod.prepare_tim_message(Bot(), original, config)) is original


def test_shared_reply_passes_current_protocol_configuration(monkeypatch):
    pipeline = load_personification_module("plugin.personification.handlers.reply_pipeline.pipeline_context")
    config = SimpleNamespace(personification_protocol_extensions="none")
    observed = []
    async def prepare(bot, message, plugin_config):
        observed.append(plugin_config)
        return message
    monkeypatch.setattr(mod, "prepare_tim_message", prepare)
    class Bot:
        async def send(self, *args, **kwargs):
            return {"message_id": 123}
    asyncio.run(pipeline.dispatch_reply_part(bot=Bot(), event=SimpleNamespace(), payload=MessageSegment.image("file:///fixture.png"), ledger=None, surface="normal_reply", plugin_config=config))
    assert observed == [config]
