from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from ._loader import load_personification_module

service_module = load_personification_module("plugin.personification.core.route_probe_service")
runtime_module = load_personification_module("plugin.personification.core.route_probe_runtime")
scheduler_module = load_personification_module("plugin.personification.jobs.route_probe_schedule")


def test_store_keeps_verified_fact_when_later_attempt_is_inconclusive(tmp_path: Path) -> None:
    store = service_module.RouteProbeStore(tmp_path / "probes.sqlite3")
    first = store.create(route_fingerprint="route-a", capability="image_input")
    assert store.begin(first["operation_id"])
    store.finish(first["operation_id"], service_module.ProbeResult("supported", "verified", "image_ok"))
    second = store.create(route_fingerprint="route-a", capability="image_input")
    assert store.begin(second["operation_id"])
    store.finish(second["operation_id"], service_module.ProbeResult("unknown", "inconclusive", "probe_network_error"))
    facts = store.facts("route-a", "image_input")
    assert facts["last_verified"]["capability_state"] == "supported"
    assert facts["latest_attempt"]["detail_code"] == "probe_network_error"


def test_restart_marks_nonterminal_operations_interrupted_and_dto_is_safe(tmp_path: Path) -> None:
    store = service_module.RouteProbeStore(tmp_path / "probes.sqlite3")
    operation = store.create(route_fingerprint="route-a", capability="audio_input")
    assert store.recover_interrupted() == 1
    restored = store.get(operation["operation_id"])
    assert restored and restored["status"] == "interrupted"
    assert "api_url" not in restored and "api_key" not in restored and "content" not in restored
    assert store.facts("route-a", "audio_input")["latest_attempt"]["status"] == "interrupted"


def test_reloading_page_sees_active_operation_without_erasing_verified_fact(tmp_path: Path) -> None:
    store = service_module.RouteProbeStore(tmp_path / "reload.sqlite3")
    original = store.create(route_fingerprint="route", capability="image_input")
    store.finish(original["operation_id"], service_module.ProbeResult("supported", "verified", "probe_visual_succeeded"))
    queued = store.create(route_fingerprint="route", capability="image_input")
    assert store.facts("route", "image_input")["latest_attempt"]["status"] == "queued"
    store.begin(queued["operation_id"])
    facts = store.facts("route", "image_input")
    assert facts["latest_attempt"]["status"] == "running"
    assert facts["last_verified"]["capability_state"] == "supported"


def test_daily_dedupes_and_history_is_bounded_paged(tmp_path: Path) -> None:
    store = service_module.RouteProbeStore(tmp_path / "probes.sqlite3")
    one = store.create(route_fingerprint="r", capability="image_input", source="daily", day_key="2026-09-06", probe_version="v1")
    two = store.create(route_fingerprint="r", capability="image_input", source="daily", day_key="2026-09-06", probe_version="v1")
    assert one["operation_id"] == two["operation_id"]
    for index in range(4):
        store.create(route_fingerprint=f"r-{index}", capability="image_input")
    page = store.list(page=2, page_size=2)
    assert page["total"] == 5 and page["total_pages"] == 3 and len(page["items"]) == 2


def test_service_cancel_reaches_terminal_cancelled(tmp_path: Path) -> None:
    async def exercise() -> None:
        service = service_module.RouteProbeService(service_module.RouteProbeStore(tmp_path / "probes.sqlite3"))
        started = asyncio.Event()
        async def runner():
            started.set(); await asyncio.sleep(60)
            return service_module.ProbeResult("supported", "verified", "never")
        operation = await service.queue(route_fingerprint="r", capability="video_input", runner=runner)
        await asyncio.wait_for(started.wait(), timeout=1)
        await service.cancel(operation["operation_id"])
        await asyncio.sleep(0)
        assert service.store.get(operation["operation_id"])["status"] == "cancelled"
    asyncio.run(exercise())


def test_cancel_before_runner_starts_finishes_and_cleans_upload(tmp_path: Path) -> None:
    async def exercise() -> None:
        service = service_module.RouteProbeService(service_module.RouteProbeStore(tmp_path / "early.sqlite3"))
        calls = []
        cleaned = []
        async def runner():
            calls.append("unexpected")
            return service_module.ProbeResult()
        operation = await service.queue(route_fingerprint="r", capability="audio_input", runner=runner, on_done=lambda: cleaned.append(True))
        await service.cancel(operation["operation_id"])
        await service.wait(operation["operation_id"])
        assert service.store.get(operation["operation_id"])["status"] == "cancelled"
        assert cleaned == [True] and not calls and not service._tasks
    asyncio.run(exercise())


def test_cancel_while_waiting_for_shared_lease_reaches_terminal(tmp_path: Path) -> None:
    async def exercise() -> None:
        store = service_module.RouteProbeStore(tmp_path / "probes.sqlite3")
        service = service_module.RouteProbeService(store)
        assert store.acquire_global_lease("other", seconds=60)
        operation = await service.queue(route_fingerprint="r", capability="audio_input", runner=lambda: asyncio.sleep(0))
        await asyncio.sleep(0)
        await service.cancel(operation["operation_id"])
        await asyncio.sleep(0)
        assert store.get(operation["operation_id"])["status"] == "cancelled"
        store.release_global_lease("other")
    asyncio.run(exercise())


def test_manual_and_daily_join_the_same_active_fingerprint_capability(tmp_path: Path) -> None:
    async def exercise() -> None:
        service = service_module.RouteProbeService(service_module.RouteProbeStore(tmp_path / "dedupe.sqlite3"))
        release = asyncio.Event()
        calls = 0
        async def runner():
            nonlocal calls
            calls += 1
            await release.wait()
            return service_module.ProbeResult("supported", "verified", "probe_visual_succeeded")
        manual = await service.queue(route_fingerprint="same", capability="image_input", runner=runner, source="manual")
        daily = await service.queue(route_fingerprint="same", capability="image_input", runner=runner, source="daily", day_key="2026-09-06")
        assert manual["operation_id"] == daily["operation_id"]
        release.set()
        assert (await service.wait(manual["operation_id"]))["status"] == "succeeded"
        assert calls == 1
    asyncio.run(exercise())


def test_shutdown_prevents_new_queue_and_uses_configured_retention(tmp_path: Path) -> None:
    async def exercise() -> None:
        service = service_module.RouteProbeService(service_module.RouteProbeStore(tmp_path / "shutdown.sqlite3"), retention_days=7)
        assert service._retention_days == 7
        await service.shutdown()
        operation = await service.queue(route_fingerprint="r", capability="image_input", runner=lambda: asyncio.sleep(0))
        assert operation["status"] == "cancelled"
        assert not service._tasks
    asyncio.run(exercise())


def test_reasoning_requires_official_wire_parameter_not_arithmetic_answer(monkeypatch) -> None:  # noqa: ANN001
    routes = load_personification_module("plugin.personification.core.ai_routes")
    capabilities = load_personification_module("plugin.personification.core.route_capabilities")
    provider = {"name": "official", "api_type": "openai", "api_url": "https://api.openai.com/v1", "model": "gpt-test"}
    key = capabilities.RouteKey.from_config(provider="official", api_type="openai", api_url=provider["api_url"], model="gpt-test", media_protocol="auto")

    class Caller:
        async def chat_with_tools(self, *_args):  # noqa: ANN202
            return SimpleNamespace(content="4", tool_calls=[])

    monkeypatch.setattr(routes, "build_single_provider_caller", lambda *_args, **_kwargs: Caller())
    result = asyncio.run(runtime_module.run_route_probe(SimpleNamespace(plugin_config=SimpleNamespace(personification_route_probe_item_timeout_seconds=5), logger=None), key, provider, "reasoning"))
    assert result.capability_state == "unknown"
    assert result.verification_state == "inconclusive"
    assert result.transport_verified is False and result.content_verified is False


def test_function_probe_requires_same_caller_noop_result_continuation(monkeypatch) -> None:  # noqa: ANN001
    routes = load_personification_module("plugin.personification.core.ai_routes")
    capabilities = load_personification_module("plugin.personification.core.route_capabilities")
    provider = {"name": "route", "api_type": "openai", "api_url": "https://gateway.test/v1", "model": "m"}
    key = capabilities.RouteKey.from_config(provider="route", api_type="openai", api_url=provider["api_url"], model="m", media_protocol="auto")
    calls: list[list[dict]] = []

    class Caller:
        async def chat_with_tools(self, messages, *_args):  # noqa: ANN001, ANN202
            calls.append(messages)
            if len(calls) == 1:
                return SimpleNamespace(content="", tool_calls=[SimpleNamespace(id="noop-1", name="personification_capability_noop")])
            return SimpleNamespace(content=json.loads(messages[-1]["content"])["probe_receipt"], tool_calls=[])
        def build_assistant_tool_calls_message(self, _response):
            return {"role": "assistant", "tool_calls": [{"id": "noop-1", "function": {"name": "personification_capability_noop", "arguments": "{}"}}]}
        def build_tool_result_message(self, call_id, name, result):
            return {"role": "tool", "tool_call_id": call_id, "name": name, "content": result}

    monkeypatch.setattr(routes, "build_single_provider_caller", lambda *_args, **_kwargs: Caller())
    result = asyncio.run(runtime_module.run_route_probe(SimpleNamespace(plugin_config=SimpleNamespace(personification_route_probe_item_timeout_seconds=5), logger=None), key, provider, "function_call"))
    assert result.capability_state == "supported" and result.transport_verified and result.content_verified
    assert len(calls) == 2
    assert calls[1][-1]["tool_call_id"] == "noop-1"


def test_operation_persists_only_safe_stage_http_and_input_evidence(tmp_path: Path) -> None:
    store = service_module.RouteProbeStore(tmp_path / "evidence.sqlite3")
    operation = store.create(route_fingerprint="r", capability="reasoning")
    assert store.begin(operation["operation_id"])
    finished = store.finish(
        operation["operation_id"],
        service_module.ProbeResult("supported", "verified", "reasoning_minimal_visible_answer", True, True, "reasoning", 200, 1),
    )
    assert finished["stage"] == "reasoning"
    assert finished["http_status"] == 200 and finished["input_count"] == 1
    assert "url" not in finished and "body" not in finished and "error" not in finished


def test_daily_target_enumeration_is_unique_and_never_needs_a_bot() -> None:
    providers = [
        {"name": "primary", "api_type": "openai", "api_url": "https://example.test/v1", "model": "m"},
        {"name": "alias", "api_type": "openai", "api_url": "https://example.test/v1", "model": "m"},
    ]
    runtime = SimpleNamespace(runtime_bundle=SimpleNamespace(get_configured_api_providers=lambda: providers))
    targets = runtime_module.configured_route_targets(runtime, capabilities=("function_call", "image_input"))
    assert len(targets) == 2
    assert {item[1] for item in targets} == {"function_call", "image_input"}


def test_restart_restores_verified_fact_but_keeps_auth_failure_as_latest(tmp_path: Path) -> None:
    capabilities = load_personification_module("plugin.personification.core.route_capabilities")
    registry = capabilities.RouteCapabilityRegistry()
    key = capabilities.RouteKey.from_config(provider="first", api_type="openai", api_url="https://example.test/v1", model="m", media_protocol="auto")
    registry.bind_route("renamed", key)
    store = service_module.RouteProbeStore(tmp_path / "probes.sqlite3")
    one = store.create(route_fingerprint=key.fingerprint, capability="image_input"); store.begin(one["operation_id"])
    store.finish(one["operation_id"], service_module.ProbeResult("supported", "verified", "probe_visual_succeeded"))
    two = store.create(route_fingerprint=key.fingerprint, capability="image_input"); store.begin(two["operation_id"])
    store.finish(two["operation_id"], service_module.ProbeResult("unknown", "inconclusive", "probe_network_error"))
    service = service_module.RouteProbeService(store)
    assert service_module.restore_route_probe_facts(registry, service) == 1
    assert registry.get(key, "image_input").state.value == "supported"
    facts = store.facts(key.fingerprint, "image_input")
    assert facts["latest_attempt"]["detail_code"] == "probe_network_error"
    renamed = capabilities.RouteKey.from_config(provider="other display", api_type="openai", api_url="https://example.test/v1", model="m", media_protocol="auto")
    assert renamed.fingerprint == key.fingerprint


def test_daily_scheduler_accepts_midnight_and_dynamic_disabled() -> None:
    class Scheduler:
        def __init__(self): self.calls=[]
        def remove_job(self, _id): raise LookupError
        def add_job(self, func, *args, **kwargs): self.calls.append((func,args,kwargs))
    scheduler=Scheduler(); enabled=False
    scheduler_module.schedule_daily_route_probes(scheduler=scheduler,service=SimpleNamespace(store=SimpleNamespace(prune=lambda:None)),targets=lambda:[],enabled=lambda:enabled,hour=0,minute=0)
    assert len(scheduler.calls) == 1  # A later runtime enable needs no restart.
    enabled=True
    scheduler_module.schedule_daily_route_probes(scheduler=scheduler,service=SimpleNamespace(store=SimpleNamespace(prune=lambda:None)),targets=lambda:[],enabled=lambda:enabled,hour=0,minute=0)
    assert scheduler.calls[0][2]["hour"] == 0 and scheduler.calls[0][2]["minute"] == 0


def test_shared_daily_audio_probe_uses_builtin_sample_and_scores_content(monkeypatch) -> None:  # noqa: ANN001
    adapters = load_personification_module("plugin.personification.core.media_provider_adapters")
    understanding = load_personification_module("plugin.personification.core.media_understanding")
    seen = {}
    monkeypatch.setattr(adapters, "resolve_media_provider_adapter", lambda _provider: SimpleNamespace(supports_audio=True, supports_video=True))
    async def fake_audio(*, runtime, prompt, audio_refs):  # noqa: ANN001
        seen["refs"] = audio_refs; seen["runtime"] = runtime
        return '{"segment_count":3,"pitch_trend":"ascending"}', "native"
    monkeypatch.setattr(understanding, "analyze_audios_with_route_or_fallback", fake_audio)
    provider = {"name":"native","api_type":"gemini_official","api_url":"https://example.test/v1","model":"m","media_protocol":"gemini_native"}
    key = load_personification_module("plugin.personification.core.route_capabilities").RouteKey.from_config(provider=provider["name"], api_type=provider["api_type"], api_url=provider["api_url"], model=provider["model"], media_protocol=provider["media_protocol"])
    result = asyncio.run(runtime_module.run_route_probe(SimpleNamespace(plugin_config=SimpleNamespace(personification_route_probe_item_timeout_seconds=45), logger=None), key, provider, "audio_input"))
    assert result.capability_state == "supported" and result.content_verified is True
    assert seen["refs"] and Path(seen["refs"][0]).is_file()


def test_old_dispatcher_and_core_share_video_upload_runner(monkeypatch, tmp_path: Path) -> None:  # noqa: ANN001
    """The legacy v2 dispatcher must reach the same core custom-media branch."""
    v2 = load_personification_module("plugin.personification.webui.routes.v2_routes")
    adapters = load_personification_module("plugin.personification.core.media_provider_adapters")
    understanding = load_personification_module("plugin.personification.core.media_understanding")
    capabilities = load_personification_module("plugin.personification.core.route_capabilities")
    provider = {"name":"native-video","api_type":"gemini_official","api_url":"https://example.test/v1","model":"m","media_protocol":"gemini_native"}
    key = capabilities.RouteKey.from_config(provider=provider["name"],api_type=provider["api_type"],api_url=provider["api_url"],model=provider["model"],media_protocol=provider["media_protocol"])
    capabilities.DEFAULT_ROUTE_CAPABILITY_REGISTRY.bind_route("native-video", key)
    monkeypatch.setattr(adapters, "resolve_media_provider_adapter", lambda _provider: SimpleNamespace(supports_audio=True, supports_video=True))
    async def fake_video(*, runtime, prompt, video_refs):  # noqa: ANN001
        assert Path(video_refs[0]).is_file()
        return '{"media_input_accepted":true}', "native"
    monkeypatch.setattr(understanding, "analyze_videos_with_route_or_fallback", fake_video)
    upload = tmp_path / "sample.mp4"; upload.write_bytes(b"\x00\x00\x00\x18ftypisom")
    runtime = SimpleNamespace(plugin_config=SimpleNamespace(personification_route_probe_item_timeout_seconds=45), logger=None, runtime_bundle=SimpleNamespace(get_configured_api_providers=lambda:[provider]))
    state, code = asyncio.run(v2._run_route_capability_probe(runtime,key.fingerprint,"video_input",media_path=upload,sample_mode="upload"))
    assert (state, code) == ("unknown", "video_input_custom_media_transport_verified")
