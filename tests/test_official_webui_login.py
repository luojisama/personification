import asyncio
import re
import sqlite3
from types import SimpleNamespace

import pytest
from ._loader import load_personification_module
from .test_webui_smoke import _build_client, _runtime_context

m = load_personification_module('plugin.personification.core.official_webui_login')
handler = load_personification_module('plugin.personification.handlers.official_login')


@pytest.fixture
def service(tmp_path, monkeypatch):
    path=tmp_path/'binding.sqlite'
    with sqlite3.connect(path) as c:
        c.executescript('''CREATE TABLE identities(platform,app,subject,account);
          CREATE TABLE binding_requests(app,subject,legacy_app,qq,status);
          INSERT INTO identities VALUES('onebot','3204586647','10001','a');
          INSERT INTO identities VALUES('qq_official','102210360','openid','a');
          INSERT INTO binding_requests VALUES('102210360','openid','3204586647','10001','approved');''')
    monkeypatch.setenv('PERSONIFICATION_BINDING_DB',str(path))
    monkeypatch.setenv('PERSONIFICATION_BINDING_LEGACY_APP','3204586647')
    return m.configured_service()


def test_real_http_browser_bound_one_time_and_no_push(service,_runtime_context):
    client=_build_client(_runtime_context)
    result=client.post('/personification/api/auth/login',json={'qq':'10001','delivery':'official'})
    assert result.status_code==200,result.text
    assert not result.json()['sent']
    assert not _runtime_context.sent
    ticket=re.search(r'/登录 ([\w-]+)',result.json()['message']).group(1)
    with pytest.raises(ValueError): service.claim(ticket,'wrong-app','openid')
    with pytest.raises(ValueError): service.claim(ticket,'102210360','other')
    code=service.claim(ticket,'102210360','openid')
    with pytest.raises(ValueError): service.claim(ticket,'102210360','openid')
    other=_build_client(_runtime_context)
    assert other.post('/personification/api/auth/verify',json={'qq':'10001','code':code}).status_code==403
    assert client.post('/personification/api/auth/verify',json={'qq':'10001','code':code}).status_code==200
    assert client.get('/personification/api/auth/me').status_code==200
    assert client.post('/personification/api/auth/verify',json={'qq':'10001','code':code}).status_code==403


def test_admin_gate_and_unbound(service,_runtime_context):
    client=_build_client(_runtime_context)
    assert client.post('/personification/api/auth/login',json={'qq':'99999','delivery':'official'}).status_code==403
    with service.tx() as c: c.execute("UPDATE binding_requests SET status='pending'")
    assert client.post('/personification/api/auth/login',json={'qq':'10001','delivery':'official'}).status_code==409


def test_revoked_admin_cannot_verify(service,_runtime_context):
    client=_build_client(_runtime_context)
    result=client.post('/personification/api/auth/login',json={'qq':'10001','delivery':'official'})
    ticket=re.search(r'/登录 ([\w-]+)',result.json()['message']).group(1)
    code=service.claim(ticket,'102210360','openid')
    _runtime_context.app_module.get_runtime_context().superusers.clear()
    assert client.post('/personification/api/auth/verify',json={'qq':'10001','code':code}).status_code==403


def test_expiry_attempts_and_unlink(service):
    ticket=service.create('10001','browser')
    code=service.claim(ticket,'102210360','openid')
    wrong='000000' if code!='000000' else '111111'
    for _ in range(5): assert not service.consume('10001',wrong,'browser')
    assert not service.consume('10001',code,'browser')
    ticket=service.create('10001','browser')
    service.clock=lambda: 10**12
    with pytest.raises(ValueError): service.claim(ticket,'102210360','openid')


def test_unlink_after_delivery_and_no_plaintext_storage(service):
    ticket=service.create('10001','browser')
    code=service.claim(ticket,'102210360','openid')
    with service.tx() as c:
        row=c.execute('SELECT ticket,browser,code FROM official_web_login').fetchone()
        assert row==(m.digest(ticket),m.digest('browser'),m.digest(m.digest(ticket)+code))
        c.execute("DELETE FROM identities WHERE platform='qq_official'")
    assert not service.consume('10001',code,'browser')


def test_failed_delivery_invalidates_code(service,monkeypatch):
    ticket=service.create('10001','browser')
    monkeypatch.setattr(handler,'configured_service',lambda:service)
    event=SimpleNamespace(get_plaintext=lambda:'/登录 '+ticket,author=SimpleNamespace(user_openid='openid'))
    async def fail(*args): raise RuntimeError('transport unavailable')
    with pytest.raises(RuntimeError):
        asyncio.run(handler.deliver_login(SimpleNamespace(self_id='102210360'),event,fail))
    with service.tx() as c: assert c.execute('SELECT COUNT(*) FROM official_web_login').fetchone()[0]==0
