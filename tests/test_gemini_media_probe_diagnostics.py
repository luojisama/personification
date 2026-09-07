from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
import httpx

from ._loader import load_personification_module

media = load_personification_module("plugin.personification.core.media_understanding")
probes = load_personification_module("plugin.personification.core.route_probe_runtime")
service = load_personification_module("plugin.personification.core.route_probe_service")
routes = load_personification_module("plugin.personification.core.ai_routes")
caps = load_personification_module("plugin.personification.core.route_capabilities")


@pytest.mark.parametrize(("payload", "code"), [
    ({}, "gemini_response_no_candidates"),
    ({"candidates": [{"content": {"parts": []}}]}, "gemini_response_no_text"),
    ({"promptFeedback": {"blockReason": "SAFETY"}}, "gemini_response_safety_blocked"),
    ({"candidates": [{"finishReason": "SAFETY"}]}, "gemini_response_safety_blocked"),
    ({"candidates": [{"finishReason": []}]}, "gemini_response_no_text"),
    ([], "gemini_response_json_invalid"),
    ({"candidates": [{"content": {"parts": [{"thought": True, "text": "private reasoning"}]}}]}, "gemini_response_no_text"),
])
def test_response_diagnostics_never_contain_payload(payload, code):
    with pytest.raises(media.GeminiMediaResponseError) as caught:
        media._gemini_media_response_text(payload)
    assert str(caught.value) == code
    assert vars(caught.value) == {"diagnostic_code": code}


def test_response_parser_excludes_thought_parts():
    assert media._gemini_media_response_text({"candidates": [{"content": {"parts": [
        {"thought": True, "text": "private reasoning"}, {"text": '{"scene_count":3}'},
    ]}}]}) == '{"scene_count":3}'


def test_sqlite_boolean_contract_includes_historical_facts(tmp_path):
    store = service.RouteProbeStore(tmp_path / "legacy.sqlite3")
    operation = store.create(route_fingerprint="legacy", capability="video_input")
    store.finish(operation["operation_id"], service.ProbeResult(
        "supported", "verified", "video_input_builtin_content_verified", True, True,
    ))
    legacy = {**store.get(operation["operation_id"]), "transport_verified": 1, "content_verified": 1}
    with store._connect() as db:
        db.execute("UPDATE route_probe_facts SET last_verified_json=?,latest_attempt_json=?", (json.dumps(legacy), json.dumps(legacy)))
        db.execute("DELETE FROM route_probe_operations")
    facts = store.facts("legacy", "video_input")
    assert facts["last_verified"]["content_verified"] is True
    assert facts["latest_attempt"]["transport_verified"] is True
    assert store.all_facts()[0][2]["content_verified"] is True


@pytest.mark.parametrize(("response", "code", "transport"), [
    ('{"scene_count":1,"colors":["red"],"shapes":["circle"]}', "video_input_builtin_content_mismatch", True),
    ("received", "media_response_json_invalid", True),
    ("gemini_response_no_candidates", "gemini_response_no_candidates", False),
    ("gemini_response_safety_blocked", "gemini_response_safety_blocked", False),
    ("timeout", "probe_timeout", False),
    ("oversize", "media_inline_budget_exceeded", False),
])
def test_probe_diagnostic_survives_durable_store(monkeypatch, tmp_path, response, code, transport):
    monkeypatch.setattr(routes, "build_single_provider_caller", lambda *a, **kw: object())
    async def analyze(*, runtime, **kwargs):
        if response == "timeout":
            raise httpx.ReadTimeout("private provider URL must not escape")
        if response == "oversize":
            raise ValueError("video_file_too_large_for_inline_data")
        if response.startswith("gemini_"):
            raise media.GeminiMediaResponseError(response)
        runtime.probe_transport_verified = True
        return response, "video_primary_gemini"
    monkeypatch.setattr(media, "analyze_videos_with_route_or_fallback", analyze)
    provider = {"name": "synthetic", "api_type": "gemini", "api_url": "https://gateway.test", "model": "test", "media_protocol": "gemini_native"}
    key = caps.RouteKey.from_config(provider="synthetic", api_type="gemini", api_url=provider["api_url"], model="test", media_protocol="gemini_native")
    runtime = SimpleNamespace(plugin_config=SimpleNamespace(personification_route_probe_item_timeout_seconds=5))
    result = asyncio.run(probes.run_route_probe(runtime, key, provider, "video_input"))
    assert result.detail_code == code
    assert result.transport_verified is transport
    assert result.content_verified is False
    assert result.capability_state == "unknown"
    store = service.RouteProbeStore(tmp_path / "probe.sqlite3")
    operation = store.create(route_fingerprint=key.fingerprint, capability="video_input")
    dto = store.finish(operation["operation_id"], result)
    assert dto["detail_code"] == code
    assert dto["transport_verified"] is transport
    assert dto["content_verified"] is False
    assert dto["status"] == ("failed" if response == "timeout" else "inconclusive")
    if transport:
        assert response not in json.dumps(dto)
    assert "private provider URL" not in json.dumps(dto)
    assert "gateway.test" not in json.dumps(dto)
