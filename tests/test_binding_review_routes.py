from fastapi import FastAPI
from fastapi.testclient import TestClient
from types import SimpleNamespace
from ._loader import load_personification_module

m=load_personification_module('plugin.personification.webui.routes.binding_routes')

def test_binding_routes_require_login():
    app=FastAPI();app.include_router(m.build_binding_router())
    client=TestClient(app)
    assert client.get('/binding-review').status_code==401
    assert client.get('/api/binding-requests').status_code==401
    assert client.post('/api/binding-requests/test/review',json={'approve':True,'note':'verified'}).status_code==401

def test_admin_page_and_unconfigured_service(monkeypatch):
    monkeypatch.delenv('PERSONIFICATION_BINDING_DB',raising=False)
    app=FastAPI();app.include_router(m.build_binding_router())
    app.dependency_overrides[m.require_admin]=lambda:SimpleNamespace(qq='admin')
    client=TestClient(app)
    page=client.get('/binding-review')
    assert page.status_code==200
    assert '申报 QQ 号未经验证' in page.text
    assert 'textContent' in page.text and 'innerHTML' not in page.text
    assert client.get('/api/binding-requests').status_code==503
