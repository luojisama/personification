from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from ._loader import load_personification_module


v2_routes = load_personification_module(
    "plugin.personification.webui.routes.v2_routes"
)
webui_app = load_personification_module("plugin.personification.webui.app")
recovery = load_personification_module(
    "plugin.personification.core.reply_recovery_queue"
)
route_capabilities = load_personification_module(
    "plugin.personification.core.route_capabilities"
)
v2_services = load_personification_module(
    "plugin.personification.webui.v2_services"
)


def test_v2_router_exposes_paged_trace_recovery_capability_and_sse_routes() -> None:
    router = v2_routes.build_v2_router(runtime=SimpleNamespace())
    paths = {route.path for route in router.routes}
    assert {
        "/api/v2/personas",
        "/api/v2/groups",
        "/api/v2/bots",
        "/api/v2/metrics/summary",
        "/api/v2/metrics/subscription-quotas",
        "/api/v2/runtime/agent",
        "/api/v2/health",
        "/api/v2/test-runs/prepare",
        "/api/v2/test-runs/{operation_id}/confirm",
        "/api/v2/test-runs/{operation_id}",
        "/api/v2/tests/video-route",
        "/api/v2/tests/video-turn",
        "/api/v2/routes/capabilities/{route_fingerprint}/probes/media",
        "/api/v2/stickers",
        "/api/v2/stickers/index/rebuild",
        "/api/v2/config",
        "/api/v2/config/values",
        "/api/v2/multimodal/routes",
        "/api/v2/traces",
        "/api/v2/traces/{trace_id}",
        "/api/v2/recovery",
        "/api/v2/recovery/counts",
        "/api/v2/model-routes/capabilities",
        "/api/v2/qzone/capabilities",
        "/api/v2/events",
        "/api/v2/plugin-knowledge",
        "/api/v2/mcp",
        "/api/v2/skills",
        "/api/v2/tool-tasks",
        "/api/v2/memories",
        "/api/v2/logs",
    } <= paths


def test_v2_trace_projection_recovers_safe_message_io_and_real_timing() -> None:
    trace = {
        "trace_id": "trace-message-fallback",
        "ts": 103.0,
        "session_type": "group",
        "group_id": "20001",
        "user_id": "10001",
        "outcome": "no_reply",
        "diagnosis_code": "policy_silence",
        "detail": {},
        "stages": [
            {"ts": 100.0, "key": "incoming_message", "status": "info", "detail": "收到的消息"},
            {"ts": 102.0, "key": "outgoing_message", "status": "ok", "detail": "实际回复"},
        ],
    }

    summary = v2_routes._trace_summary(trace)
    detail = v2_routes._trace_detail(trace)

    assert summary["input_summary"] == "收到的消息"
    assert detail["input_summary"] == "收到的消息"
    assert detail["final_reply"] == "实际回复"
    assert summary["started_at"] == "1970-01-01T00:01:40+00:00"
    assert summary["finished_at"] == "1970-01-01T00:01:43+00:00"
    assert summary["elapsed_ms"] == 3000


def test_v2_trace_projection_sanitizes_legacy_message_and_bad_timestamp() -> None:
    trace = {
        "trace_id": "trace-legacy-sensitive",
        "ts": 0,
        "outcome": "no_reply",
        "detail": {"incoming_text": "api_key=do-not-show-me"},
        "stages": [{"ts": "bad-ts", "key": "incoming_message", "detail": "fallback"}],
    }

    summary = v2_routes._trace_summary(trace)

    assert "do-not-show-me" not in summary["input_summary"]
    assert "***" in summary["input_summary"]
    assert summary["started_at"] is None
    assert summary["elapsed_ms"] is None
    assert v2_routes._iso(float("nan")) is None
    assert v2_routes._iso(float("inf")) is None

    non_finite = v2_routes._trace_summary(
        {
            **trace,
            "ts": float("nan"),
            "stages": [
                {"ts": float("inf"), "key": "incoming_message", "detail": "fallback"}
            ],
        }
    )
    assert non_finite["ts"] == 0.0
    assert non_finite["started_at"] is None

    legacy_media = v2_routes._trace_detail(
        {
            **trace,
            "detail": {
                "incoming_text": "普通消息",
                "outgoing_text": "[IMAGE_URL]https://example.invalid/private.png[/IMAGE_URL]",
            },
        }
    )
    assert legacy_media["final_reply"] == ""
    assert "private.png" not in str(legacy_media)


def test_functional_test_catalog_keeps_three_risk_levels_and_eighteen_categories() -> None:
    catalog = list(v2_routes._FUNCTIONAL_TEST_CATALOG)
    assert len(catalog) == 18
    assert {item["risk"] for item in catalog} == {"local_read", "external_read", "external_write"}
    assert {item["group"] for item in catalog} == {
        "核心运行",
        "模型与媒体",
        "存储与记忆",
        "QQ 与群聊",
        "QZone",
        "后台任务与权限",
    }
    assert {item["category"] for item in catalog} == {
        "核心",
        "模型调用",
        "LLM 子模型",
        "视觉能力",
        "视频理解",
        "存储",
        "记忆",
        "用户画像",
        "群聊",
        "表情包",
        "TTS 语音",
        "QQ 空间",
        "联网搜索",
        "Skill 扩展",
        "主动社交",
        "人设",
        "协议端",
        "WebUI 安全",
    }
    assert v2_routes._functional_test_definition("core")["risk"] == "local_read"
    assert v2_routes._functional_test_definition("model")["risk"] == "external_read"
    assert v2_routes._functional_test_definition("qzone")["risk"] == "external_write"


def test_functional_test_view_extends_existing_run_with_safe_diagnostic_and_delivery_contract() -> None:
    view = v2_routes._functional_test_view(
        {
            "id": "run-1",
            "test_id": "core",
            "label": "核心运行",
            "risk": "local_read",
            "state": "running",
            "created_at": 100.0,
            "started_at": 101.0,
            "finished_at": 0.0,
            "duration_ms": None,
            "trace_id": "trace-1",
            "diagnostic_code": "functional_test_running",
            "steps": [{"key": "local_check", "status": "running"}],
            "diagnostic": {"code": "functional_test_running", "message": "安全摘要"},
            "result_summary": {"overall": "running"},
            "delivery_status": "not_applicable",
        }
    )

    assert view["state"] == "running"
    assert view["started_at"] == "1970-01-01T00:01:41+00:00"
    assert view["finished_at"] is None
    assert view["duration_ms"] is None
    assert view["steps"] == [{"key": "local_check", "status": "running"}]
    assert view["diagnostic"]["code"] == "functional_test_running"
    assert view["result_summary"] == {"overall": "running"}
    assert view["trace_id"] == "trace-1"
    assert view["delivery_status"] == "not_applicable"


def test_qq_canary_plan_exposes_the_full_path_without_a_send_step() -> None:
    steps = v2_routes._functional_step_plan(
        {"execution_kind": "qq_canary"},
        status="skipped",
        message="不应在体检页执行。",
    )

    assert [step.key for step in steps] == ["rules", "buffer", "model", "review", "ledger", "send"]
    assert all(step.status == "skipped" for step in steps)
    assert "绝不调用 QQ 发送接口" in steps[-1].message


def test_paged_route_capabilities_exposes_per_capability_probe_catalog_and_verification_state(monkeypatch) -> None:  # noqa: ANN001
    registry = route_capabilities.RouteCapabilityRegistry()
    key = route_capabilities.RouteKey.from_config(
        provider="primary",
        api_type="openai",
        api_url="https://example.test/v1",
        model="test-model",
        media_protocol="openai",
    )
    registry.bind_route("primary", key)
    registry.record_observation(key, "image_input", "parse_error")
    monkeypatch.setattr(v2_routes, "DEFAULT_ROUTE_CAPABILITY_REGISTRY", registry)
    router = v2_routes.build_v2_router(runtime=SimpleNamespace())
    endpoint = next(route.endpoint for route in router.routes if route.path == "/api/v2/routes/capabilities")

    page = asyncio.run(endpoint(page=1, page_size=20, search="", _=None))
    item = page["items"][0]

    assert item["capabilities"]["image_input"]["state"] == "unknown"
    assert item["capabilities"]["image_input"]["verification_state"] == "inconclusive"
    assert item["probe_catalog"]["image_input"]["probe_id"] == "vision"
    assert item["probe_catalog"]["function_call"]["probe_id"] == "function_call_noop"
    assert item["probe_catalog"]["function_call"]["available"] is True
    assert item["probe_catalog"]["native_web_search"]["confirmation_required"] is True
    assert item["probe_catalog"]["native_web_search"]["available"] is True
    assert item["probe_catalog"]["reasoning"]["probe_id"] == "reasoning_minimal"
    assert item["probe_catalog"]["reasoning"]["available"] is True
    assert item["probe_catalog"]["audio_input"]["input_kind"] == "media_upload"
    assert item["probe_catalog"]["audio_input"]["max_upload_bytes"] > 0
    assert "audio/wav" in item["probe_catalog"]["audio_input"]["accepted_mime_types"]


def test_video_turn_capture_bot_never_calls_real_send_api() -> None:
    class _RealBot:
        self_id = "12345"

        async def send(self, *_args, **_kwargs):
            raise AssertionError("real send must not run")

        async def call_api(self, api, **_data):  # noqa: ANN001
            if api.startswith("send"):
                raise AssertionError("real send API must not run")
            return {"ok": True}

    bot = v2_routes._NoSendCaptureBot(_RealBot(), trace_id="missing-trace")
    result = asyncio.run(bot.send(None, "可见回复"))
    api_result = asyncio.run(bot.call_api("send_private_msg", message="第二段"))

    assert result["not_sent"] is True
    assert api_result["not_sent"] is True
    assert bot.captured == ["可见回复", "第二段"]


def test_whole_backup_router_exposes_step_up_and_fail_closed_restore_routes() -> None:
    routes = load_personification_module(
        "plugin.personification.webui.routes.whole_backup_routes"
    )
    router = routes.build_whole_backup_router(
        runtime=SimpleNamespace(get_bots=lambda: {})
    )
    paths = {route.path for route in router.routes}
    assert {
        "/api/v2/step-up/start",
        "/api/v2/step-up/verify",
        "/api/v2/backups/export/state",
        "/api/v2/backups/export/secret",
        "/api/v2/backups/inspect",
        "/api/v2/backups/{artifact_id}/dry-run",
        "/api/v2/backups/{artifact_id}/apply",
        "/api/v2/backups/rollback/{journal_id}",
        "/api/v2/backups/download/{artifact_id}",
    } <= paths


def test_frontend_production_build_is_served_with_spa_fallback() -> None:
    index = webui_app._serve_frontend_asset("")
    nested = webui_app._serve_frontend_asset("traces/example")
    assert str(index.path).replace("\\", "/").endswith("frontend_dist/index.html")
    assert nested.path == index.path
    assert index.headers["cache-control"] == "no-store, max-age=0"


def test_recovery_summary_does_not_expose_claims_or_media_references(tmp_path) -> None:
    queue = recovery.ReplyRecoveryQueue(tmp_path / "state.sqlite3")
    item = queue.record_failure(
        bot_id="bot",
        conversation_kind="private",
        conversation_id="user",
        original_message_id="message",
        normalized_text="x" * 400,
        media_refs=[
            {
                "kind": "image",
                "origin": "current",
                "ref": "https://example.test/token-secret",
                "media_id": "media-id",
                "safe_summary": "公开封面",
            }
        ],
        failure_stage="generation",
        failure_class="generation_failed_before_send",
    )
    summary = v2_routes._recovery_summary(item)

    assert len(summary["text_summary"]) == 240
    assert summary["media"] == [
        {"kind": "image", "origin": "current", "safe_summary": "公开封面"}
    ]
    assert "claim_token" not in summary
    assert "claimed_by" not in summary
    assert "ref" not in summary["media"][0]


def test_cached_persona_rows_filter_and_sort_without_onebot_calls() -> None:
    class _Profiles:
        def list_core_profiles(self):
            return [
                {
                    "user_id": "2",
                    "profile_text": "乙的画像",
                    "profile_json": {"qq_profile": {"nickname": "乙", "avatar_url": "https://avatar/2"}},
                    "source": "cache",
                    "updated_at": 20,
                },
                {
                    "user_id": "1",
                    "profile_text": "甲的画像",
                    "profile_json": {"qq_profile": {"nickname": "甲"}},
                    "source": "cache",
                    "updated_at": 10,
                },
            ]

        def list_local_profiles(self, group_id):  # noqa: ANN001
            assert group_id == "100"
            return [{"user_id": "2"}]

    class _Favorability:
        def snapshot_profiles(self):
            return {"1": {"favorability": 20}, "2": {"favorability": 80}}

        def get_level_name(self, score):  # noqa: ANN001
            return "亲近" if score >= 50 else "普通"

    runtime = SimpleNamespace(
        runtime_bundle=SimpleNamespace(
            profile_service=_Profiles(),
            favorability_service=_Favorability(),
        )
    )

    rows = v2_routes._cached_persona_rows(
        runtime,
        search="乙",
        group_id="100",
        favorability_level="亲近",
        sort_by="favorability",
        direction="desc",
    )

    assert [row["user_id"] for row in rows] == ["2"]
    assert rows[0]["cache_only"] is True
    assert rows[0]["favorability"] == {"score": 80.0, "level": "亲近"}
    assert rows[0]["qq_id"] == "2"
    assert rows[0]["avatar_url"] == "https://q1.qlogo.cn/g?b=qq&nk=2&s=640"
    assert "snippet" not in rows[0]
    assert "profile_text" not in repr(rows)


def test_webui_avatar_urls_accept_numeric_ids_only() -> None:
    assert v2_services.qq_avatar_url("12345") == "https://q1.qlogo.cn/g?b=qq&nk=12345&s=640"
    assert v2_services.group_avatar_url("67890") == "https://p.qlogo.cn/gh/67890/67890/640"
    assert v2_services.qq_avatar_url("https://attacker.invalid/x") is None
    assert v2_services.group_avatar_url("12/../../x") is None


def test_bot_identity_uses_controlled_avatar_and_connected_runtime() -> None:
    class _Bot:
        self_id = "12345"

        async def get_login_info(self):
            return {"user_id": 12345, "nickname": "测试 Bot"}

    runtime = SimpleNamespace(get_bots=lambda: {"12345": _Bot()})
    rows = asyncio.run(v2_services.list_bot_identities(runtime))

    assert rows == [
        {
            "bot_id": "12345",
            "nickname": "测试 Bot",
            "avatar_url": "https://q1.qlogo.cn/g?b=qq&nk=12345&s=640",
            "online": True,
            "is_default": True,
            "last_seen_at": rows[0]["last_seen_at"],
        }
    ]


def test_cached_group_rows_hide_unconfirmed_profile_candidates(monkeypatch) -> None:  # noqa: ANN001
    group_directory = load_personification_module("plugin.personification.core.group_directory")
    utils = load_personification_module("plugin.personification.utils")
    monkeypatch.setattr(
        group_directory,
        "list_cached_group_union",
        lambda _runtime: [
            {
                "group_id": "100",
                "group_name": "已配置群",
                "sources": ["group_config"],
                "memberships": [],
                "bot_self_ids": [],
                "freshness": 0,
            },
            {
                "group_id": "200",
                "group_name": "历史候选",
                "sources": ["profile_memory"],
                "memberships": [],
                "bot_self_ids": [],
                "freshness": 0,
            },
        ],
    )
    monkeypatch.setattr(utils, "is_group_whitelisted", lambda _group_id, _whitelist: False)
    runtime = SimpleNamespace(plugin_config=SimpleNamespace(personification_whitelist=[]))

    visible = v2_routes._cached_group_rows(
        runtime,
        search="",
        membership_state="",
        include_unconfirmed=False,
        enabled="",
        bot_id="",
        sort_by="group_id",
        direction="asc",
    )
    all_rows = v2_routes._cached_group_rows(
        runtime,
        search="",
        membership_state="",
        include_unconfirmed=True,
        enabled="",
        bot_id="",
        sort_by="group_id",
        direction="asc",
    )

    assert [(row["group_id"], row["membership_state"]) for row in visible] == [("100", "configured")]
    assert [(row["group_id"], row["membership_state"]) for row in all_rows] == [
        ("100", "configured"),
        ("200", "unconfirmed"),
    ]


def test_v2_config_rows_mask_secret_values() -> None:
    config = load_personification_module("plugin.personification.config").Config(
        personification_api_key="secret-must-not-leak"
    )
    runtime = SimpleNamespace(plugin_config=config)

    rows = v2_routes._config_rows(runtime, search="主模型 API 密钥", group="")
    target = next(row for row in rows if row["field_name"] == "personification_api_key")

    assert target["secret"] is True
    assert target["value"] == "***"
    assert "secret-must-not-leak" not in repr(rows)


def test_v2_config_rows_mask_nested_provider_secret_and_preserve_it_on_patch(monkeypatch) -> None:  # noqa: ANN001
    config = load_personification_module("plugin.personification.config").Config(
        personification_api_pools=[
            {
                "name": "primary",
                "provider": "gemini",
                "api_type": "gemini",
                "api_url": "https://example.test/v1beta",
                "api_key": "nested-secret-must-not-leak",
                "model": "before-model",
                "account_mode": "subscription_proxy",
                "subscription_quota_kind": "codex_wham_proxy",
                "subscription_management_url": "https://quota.example.test",
                "subscription_auth_index": "auth-1",
                "subscription_management_key": "nested-management-secret",
            }
        ]
    )
    runtime = SimpleNamespace(plugin_config=config, runtime_bundle=None)
    rows = v2_routes._config_rows(runtime, search="API Provider 池", group="")
    target = next(row for row in rows if row["field_name"] == "personification_api_pools")
    provider = dict(target["value"][0])

    assert provider["api_key"] == "***"
    assert provider["subscription_management_key"] == "***"
    assert provider["_secret_ref"]
    assert "nested-secret-must-not-leak" not in repr(target)
    assert "nested-management-secret" not in repr(target)
    assert target["ui_schema"]["control_kind"] == "provider_list"
    assert "subscription_management_key" in target["ui_schema"]["sensitive_fields"]

    provider["model"] = "after-model"
    env_writer = load_personification_module("plugin.personification.core.env_writer")
    captured: dict[str, object] = {}

    def _write_many(values, plugin_config):  # noqa: ANN001
        captured.update(values)
        return {"env_json_path": "test/env.json", "errors": []}

    monkeypatch.setattr(env_writer, "write_many", _write_many)
    asyncio.run(
        v2_services.apply_config_patch(
            runtime,
            revision=v2_services.config_revision(config),
            values={"personification_api_pools": [provider]},
        )
    )

    saved = captured["personification_api_pools"]
    assert isinstance(saved, list)
    assert saved[0]["api_key"] == "nested-secret-must-not-leak"
    assert saved[0]["subscription_management_key"] == "nested-management-secret"
    assert saved[0]["model"] == "after-model"


def test_v2_config_hides_legacy_monthly_quota_fields() -> None:
    config = load_personification_module("plugin.personification.config").Config(
        personification_quota_openai_monthly_tokens=999,
    )
    rows = v2_routes._config_rows(SimpleNamespace(plugin_config=config), search="", group="")
    fields = {row["field_name"] for row in rows}

    assert "personification_quota_openai_monthly_tokens" not in fields
    assert "personification_api_pools" in fields


def test_v2_config_patch_uses_revision_and_atomic_batch_writer(monkeypatch) -> None:  # noqa: ANN001
    config = load_personification_module("plugin.personification.config").Config()
    env_writer = load_personification_module("plugin.personification.core.env_writer")
    captured: dict[str, object] = {}

    def _write_many(values, plugin_config):  # noqa: ANN001
        captured["values"] = dict(values)
        captured["config"] = plugin_config
        return {"env_json_path": "test/env.json", "errors": []}

    monkeypatch.setattr(env_writer, "write_many", _write_many)
    runtime = SimpleNamespace(plugin_config=config, runtime_bundle=None)
    before = v2_services.config_revision(config)

    result = asyncio.run(
        v2_services.apply_config_patch(
            runtime,
            revision=before,
            values={"personification_global_enabled": False},
        )
    )

    assert captured["values"] == {"personification_global_enabled": False}
    assert config.personification_global_enabled is False
    assert result["updated_keys"] == ["personification_global_enabled"]
    assert result["revision"] != before


def test_v2_config_patch_rejects_stale_revision_before_writing(monkeypatch) -> None:  # noqa: ANN001
    config = load_personification_module("plugin.personification.config").Config()
    env_writer = load_personification_module("plugin.personification.core.env_writer")
    monkeypatch.setattr(
        env_writer,
        "write_many",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not persist")),
    )
    runtime = SimpleNamespace(plugin_config=config)

    try:
        asyncio.run(
            v2_services.apply_config_patch(
                runtime,
                revision="stale",
                values={"personification_global_enabled": False},
            )
        )
    except RuntimeError as exc:
        assert str(exc) == "config_revision_conflict"
    else:
        raise AssertionError("stale revision must be rejected")


def test_route_probe_target_matches_full_route_fingerprint(monkeypatch) -> None:  # noqa: ANN001
    registry = route_capabilities.RouteCapabilityRegistry()
    provider = {
        "name": "compatible-gateway",
        "api_type": "openai",
        "api_url": "https://example.test/v1",
        "api_key": "must-not-be-logged",
        "model": "vision-model",
        "media_protocol": "openai",
    }
    key = route_capabilities.RouteKey.from_config(
        provider=provider["name"],
        api_type=provider["api_type"],
        api_url=provider["api_url"],
        model=provider["model"],
        media_protocol=provider["media_protocol"],
    )
    registry.bind_route("0:FakeCaller", key)
    monkeypatch.setattr(v2_routes, "DEFAULT_ROUTE_CAPABILITY_REGISTRY", registry)
    runtime = SimpleNamespace(
        plugin_config=SimpleNamespace(),
        runtime_bundle=SimpleNamespace(get_configured_api_providers=lambda: [provider]),
    )

    target = v2_routes._route_probe_target(runtime, key.fingerprint)

    assert target is not None
    assert target[0] == "0:fakecaller"
    assert target[1] == key
    assert target[2]["model"] == "vision-model"
    assert v2_routes._route_probe_target(runtime, "unknown") is None


def test_visual_probe_timeout_stays_unknown_and_inconclusive(monkeypatch) -> None:  # noqa: ANN001
    registry = route_capabilities.RouteCapabilityRegistry()
    provider = {
        "name": "compatible-gateway",
        "api_type": "openai",
        "api_url": "https://example.test/v1",
        "api_key": "must-not-be-logged",
        "model": "vision-model",
        "media_protocol": "openai",
    }
    key = route_capabilities.RouteKey.from_config(
        provider=provider["name"],
        api_type=provider["api_type"],
        api_url=provider["api_url"],
        model=provider["model"],
        media_protocol=provider["media_protocol"],
    )
    registry.bind_route("vision-route", key)
    monkeypatch.setattr(v2_routes, "DEFAULT_ROUTE_CAPABILITY_REGISTRY", registry)
    ai_routes = load_personification_module("plugin.personification.core.ai_routes")
    visual_capabilities = load_personification_module("plugin.personification.core.visual_capabilities")

    class _Caller:
        pass

    async def _timeout(**_kwargs):  # noqa: ANN003, ANN202
        raise TimeoutError("probe timed out")

    monkeypatch.setattr(ai_routes, "build_single_provider_caller", lambda *_args, **_kwargs: _Caller())
    monkeypatch.setattr(visual_capabilities, "probe_tool_caller_vision", _timeout)
    runtime = SimpleNamespace(
        plugin_config=SimpleNamespace(personification_visual_probe_timeout_seconds=5),
        runtime_bundle=SimpleNamespace(get_configured_api_providers=lambda: [provider]),
    )

    state, code = asyncio.run(v2_routes._run_route_visual_probe(runtime, key.fingerprint))

    assert (state, code) == ("unknown", "probe_visual_timeout")
    assert registry.get(key, "image_input").to_dict()["state"] == "unknown"
    assert registry.get(key, "image_input").to_dict()["verification_state"] == "inconclusive"


def test_function_call_probe_accepts_only_a_structured_local_noop_tool_call(monkeypatch) -> None:  # noqa: ANN001
    registry = route_capabilities.RouteCapabilityRegistry()
    provider = {
        "name": "compatible-gateway",
        "api_type": "openai",
        "api_url": "https://example.test/v1",
        "api_key": "must-not-be-logged",
        "model": "tool-model",
        "media_protocol": "openai",
    }
    key = route_capabilities.RouteKey.from_config(
        provider=provider["name"],
        api_type=provider["api_type"],
        api_url=provider["api_url"],
        model=provider["model"],
        media_protocol=provider["media_protocol"],
    )
    registry.bind_route("tool-route", key)
    monkeypatch.setattr(v2_routes, "DEFAULT_ROUTE_CAPABILITY_REGISTRY", registry)
    captured: dict[str, object] = {}

    class _Caller:
        async def chat_with_tools(self, messages, tools, use_builtin_search):  # noqa: ANN001
            captured["messages"] = messages
            captured["tools"] = tools
            captured["use_builtin_search"] = use_builtin_search
            return SimpleNamespace(
                content="ignored",
                tool_calls=[
                    SimpleNamespace(
                        id="noop-1",
                        name="personification_capability_noop",
                        arguments={},
                    )
                ],
            )

    ai_routes = load_personification_module("plugin.personification.core.ai_routes")
    monkeypatch.setattr(ai_routes, "build_single_provider_caller", lambda *_args, **_kwargs: _Caller())
    runtime = SimpleNamespace(
        plugin_config=SimpleNamespace(personification_visual_probe_timeout_seconds=5),
        runtime_bundle=SimpleNamespace(get_configured_api_providers=lambda: [provider]),
    )

    state, code = asyncio.run(v2_routes._run_route_function_call_probe(runtime, key.fingerprint))

    assert (state, code) == ("supported", "function_call_noop_structured_tool_call")
    assert captured["use_builtin_search"] is False
    assert captured["tools"][0]["function"]["name"] == "personification_capability_noop"
    assert registry.get(key, "function_call").to_dict() == {
        "state": "supported",
        "verification_state": "verified",
        "source": "probe",
        "checked_at": registry.get(key, "function_call").checked_at,
        "expires_at": registry.get(key, "function_call").expires_at,
        "detail_code": "function_call_noop_structured_tool_call",
    }


def test_function_call_probe_5xx_stays_unknown_and_inconclusive(monkeypatch) -> None:  # noqa: ANN001
    registry = route_capabilities.RouteCapabilityRegistry()
    provider = {
        "name": "compatible-gateway",
        "api_type": "openai",
        "api_url": "https://example.test/v1",
        "api_key": "must-not-be-logged",
        "model": "tool-model",
        "media_protocol": "openai",
    }
    key = route_capabilities.RouteKey.from_config(
        provider=provider["name"],
        api_type=provider["api_type"],
        api_url=provider["api_url"],
        model=provider["model"],
        media_protocol=provider["media_protocol"],
    )
    registry.bind_route("tool-route", key)
    monkeypatch.setattr(v2_routes, "DEFAULT_ROUTE_CAPABILITY_REGISTRY", registry)

    class _ServerError(RuntimeError):
        response = SimpleNamespace(status_code=503)

    class _Caller:
        async def chat_with_tools(self, *_args, **_kwargs):  # noqa: ANN002, ANN003
            raise _ServerError("upstream unavailable")

    ai_routes = load_personification_module("plugin.personification.core.ai_routes")
    monkeypatch.setattr(ai_routes, "build_single_provider_caller", lambda *_args, **_kwargs: _Caller())
    runtime = SimpleNamespace(
        plugin_config=SimpleNamespace(personification_visual_probe_timeout_seconds=5),
        runtime_bundle=SimpleNamespace(get_configured_api_providers=lambda: [provider]),
    )

    state, code = asyncio.run(v2_routes._run_route_function_call_probe(runtime, key.fingerprint))

    assert (state, code) == ("unknown", "function_call_probe_server_error")
    assert registry.get(key, "function_call").to_dict()["state"] == "unknown"
    assert registry.get(key, "function_call").to_dict()["verification_state"] == "inconclusive"


def test_probe_without_a_configured_caller_is_reported_as_unavailable(monkeypatch) -> None:  # noqa: ANN001
    registry = route_capabilities.RouteCapabilityRegistry()
    key = route_capabilities.RouteKey.from_config(
        provider="primary",
        api_type="openai",
        api_url="https://example.test/v1",
        model="tool-model",
        media_protocol="openai",
    )
    registry.bind_route("tool-route", key)
    monkeypatch.setattr(v2_routes, "DEFAULT_ROUTE_CAPABILITY_REGISTRY", registry)

    state, code = asyncio.run(
        v2_routes._run_route_function_call_probe(
            SimpleNamespace(plugin_config=SimpleNamespace()),
            key.fingerprint,
        )
    )

    assert (state, code) == ("unknown", "probe_route_caller_unavailable")
    assert registry.get(key, "function_call").to_dict()["verification_state"] == "probe_unavailable"


def test_function_call_probe_marks_only_an_explicit_tool_refusal_unsupported(monkeypatch) -> None:  # noqa: ANN001
    registry = route_capabilities.RouteCapabilityRegistry()
    provider = {
        "name": "compatible-gateway",
        "api_type": "openai",
        "api_url": "https://example.test/v1",
        "api_key": "must-not-be-logged",
        "model": "tool-model",
        "media_protocol": "openai",
    }
    key = route_capabilities.RouteKey.from_config(
        provider=provider["name"],
        api_type=provider["api_type"],
        api_url=provider["api_url"],
        model=provider["model"],
        media_protocol=provider["media_protocol"],
    )
    registry.bind_route("tool-route", key)
    monkeypatch.setattr(v2_routes, "DEFAULT_ROUTE_CAPABILITY_REGISTRY", registry)

    class _Caller:
        async def chat_with_tools(self, *_args, **_kwargs):  # noqa: ANN002, ANN003
            raise RuntimeError("This model does not support tool calling")

    ai_routes = load_personification_module("plugin.personification.core.ai_routes")
    monkeypatch.setattr(ai_routes, "build_single_provider_caller", lambda *_args, **_kwargs: _Caller())
    runtime = SimpleNamespace(
        plugin_config=SimpleNamespace(personification_visual_probe_timeout_seconds=5),
        runtime_bundle=SimpleNamespace(get_configured_api_providers=lambda: [provider]),
    )

    state, code = asyncio.run(v2_routes._run_route_function_call_probe(runtime, key.fingerprint))

    assert (state, code) == ("unsupported", "function_call_probe_explicitly_unsupported")
    assert registry.get(key, "function_call").to_dict()["state"] == "unsupported"
    assert registry.get(key, "function_call").to_dict()["verification_state"] == "verified"


def test_native_search_probe_requires_confirmation_before_it_can_queue(monkeypatch) -> None:  # noqa: ANN001
    registry = route_capabilities.RouteCapabilityRegistry()
    key = route_capabilities.RouteKey.from_config(
        provider="primary",
        api_type="openai",
        api_url="https://example.test/v1",
        model="search-model",
        media_protocol="openai",
    )
    registry.bind_route("primary", key)
    monkeypatch.setattr(v2_routes, "DEFAULT_ROUTE_CAPABILITY_REGISTRY", registry)
    router = v2_routes.build_v2_router(runtime=SimpleNamespace())
    endpoint = next(
        route.endpoint
        for route in router.routes
        if route.path == "/api/v2/routes/capabilities/{route_fingerprint}/probes"
    )

    with pytest.raises(v2_routes.HTTPException) as confirmation_error:
        asyncio.run(endpoint(key.fingerprint, body={"capability": "native_web_search"}, _=None))
    assert confirmation_error.value.status_code == 409
    assert confirmation_error.value.detail["code"] == "route_probe_confirmation_required"
    assert registry.get(key, "native_web_search").verification_state.value == "not_run"

    result = asyncio.run(
        endpoint(
            key.fingerprint,
            body={"capability": "native_web_search", "confirmed": True},
            _=None,
        )
    )
    assert result["code"] == "route_probe_queued"


def _route_probe_runtime(
    monkeypatch,  # noqa: ANN001
    *,
    provider: dict[str, str],
    route_name: str = "probe-route",
) -> tuple[route_capabilities.RouteCapabilityRegistry, route_capabilities.RouteKey, SimpleNamespace]:
    registry = route_capabilities.RouteCapabilityRegistry()
    key = route_capabilities.RouteKey.from_config(
        provider=provider["name"],
        api_type=provider["api_type"],
        api_url=provider["api_url"],
        model=provider["model"],
        media_protocol=provider.get("media_protocol", "auto"),
    )
    registry.bind_route(route_name, key)
    monkeypatch.setattr(v2_routes, "DEFAULT_ROUTE_CAPABILITY_REGISTRY", registry)
    runtime = SimpleNamespace(
        plugin_config=SimpleNamespace(personification_visual_probe_timeout_seconds=5),
        runtime_bundle=SimpleNamespace(get_configured_api_providers=lambda: [provider]),
    )
    return registry, key, runtime


def test_native_search_probe_uses_builtin_search_and_keeps_answer_out_of_snapshot(monkeypatch) -> None:  # noqa: ANN001
    provider = {
        "name": "search-provider",
        "api_type": "openai",
        "api_url": "https://example.test/v1",
        "api_key": "must-not-be-logged",
        "model": "search-model",
        "media_protocol": "auto",
    }
    registry, key, runtime = _route_probe_runtime(monkeypatch, provider=provider)
    captured: dict[str, object] = {}

    class _Response:
        content = "Mercury"
        used_builtin_search = True

        @property
        def raw(self):  # noqa: ANN201
            raise AssertionError("native-search probe must not inspect raw output")

    class _Caller:
        async def chat_with_tools(self, messages, tools, use_builtin_search):  # noqa: ANN001
            captured["messages"] = messages
            captured["tools"] = tools
            captured["use_builtin_search"] = use_builtin_search
            return _Response()

    ai_routes = load_personification_module("plugin.personification.core.ai_routes")
    monkeypatch.setattr(ai_routes, "build_single_provider_caller", lambda *_args, **_kwargs: _Caller())

    state, code = asyncio.run(v2_routes._run_route_native_search_probe(runtime, key.fingerprint))

    assert (state, code) == ("supported", "native_search_readonly_visible_answer")
    assert captured["tools"] == []
    assert captured["use_builtin_search"] is True
    observation = registry.get(key, "native_web_search").to_dict()
    assert observation["state"] == "supported"
    assert observation["verification_state"] == "verified"
    assert "Mercury" not in repr(registry.snapshot())


def test_native_search_probe_5xx_and_missing_search_marker_stay_inconclusive(monkeypatch) -> None:  # noqa: ANN001
    provider = {
        "name": "search-provider",
        "api_type": "openai",
        "api_url": "https://example.test/v1",
        "api_key": "must-not-be-logged",
        "model": "search-model",
        "media_protocol": "auto",
    }
    registry, key, runtime = _route_probe_runtime(monkeypatch, provider=provider)

    class _ServerError(RuntimeError):
        response = SimpleNamespace(status_code=503)

    class _Caller:
        async def chat_with_tools(self, *_args, **_kwargs):  # noqa: ANN002, ANN003
            raise _ServerError("upstream unavailable")

    ai_routes = load_personification_module("plugin.personification.core.ai_routes")
    monkeypatch.setattr(ai_routes, "build_single_provider_caller", lambda *_args, **_kwargs: _Caller())

    state, code = asyncio.run(v2_routes._run_route_native_search_probe(runtime, key.fingerprint))

    assert (state, code) == ("unknown", "native_search_probe_server_error")
    assert registry.get(key, "native_web_search").verification_state.value == "inconclusive"

    class _NoSearchResponse:
        content = "A visible answer without a search marker"
        used_builtin_search = False

    class _NoSearchCaller:
        async def chat_with_tools(self, *_args, **_kwargs):  # noqa: ANN002, ANN003
            return _NoSearchResponse()

    monkeypatch.setattr(ai_routes, "build_single_provider_caller", lambda *_args, **_kwargs: _NoSearchCaller())
    state, code = asyncio.run(v2_routes._run_route_native_search_probe(runtime, key.fingerprint))
    assert (state, code) == ("unknown", "native_search_probe_inconclusive")
    assert registry.get(key, "native_web_search").verification_state.value == "inconclusive"


def test_native_search_probe_marks_only_explicit_rejection_unsupported(monkeypatch) -> None:  # noqa: ANN001
    provider = {
        "name": "search-provider",
        "api_type": "openai",
        "api_url": "https://example.test/v1",
        "api_key": "must-not-be-logged",
        "model": "search-model",
        "media_protocol": "auto",
    }
    registry, key, runtime = _route_probe_runtime(monkeypatch, provider=provider)

    class _Caller:
        async def chat_with_tools(self, *_args, **_kwargs):  # noqa: ANN002, ANN003
            raise RuntimeError("This model does not support web search")

    ai_routes = load_personification_module("plugin.personification.core.ai_routes")
    monkeypatch.setattr(ai_routes, "build_single_provider_caller", lambda *_args, **_kwargs: _Caller())

    state, code = asyncio.run(v2_routes._run_route_native_search_probe(runtime, key.fingerprint))

    assert (state, code) == ("unsupported", "native_search_probe_explicitly_unsupported")
    assert registry.get(key, "native_web_search").verification_state.value == "verified"


def test_reasoning_probe_uses_existing_low_effort_path_without_observing_raw_output(monkeypatch) -> None:  # noqa: ANN001
    provider = {
        "name": "reasoning-provider",
        "api_type": "openai",
        "api_url": "https://example.test/v1",
        "api_key": "must-not-be-logged",
        "model": "gpt-5-mini",
        "media_protocol": "auto",
    }
    registry, key, runtime = _route_probe_runtime(monkeypatch, provider=provider)
    captured: dict[str, object] = {}

    class _Response:
        content = "reasoning-visible-answer-should-not-persist"

        @property
        def raw(self):  # noqa: ANN201
            raise AssertionError("reasoning probe must not inspect raw output")

        @property
        def provider_history(self):  # noqa: ANN201
            raise AssertionError("reasoning probe must not inspect provider history")

    class _Caller:
        async def chat_with_tools(self, messages, tools, use_builtin_search):  # noqa: ANN001
            captured["messages"] = messages
            captured["tools"] = tools
            captured["use_builtin_search"] = use_builtin_search
            return _Response()

    def _build(_config, _provider, **kwargs):  # noqa: ANN001
        captured["thinking_mode_override"] = kwargs.get("thinking_mode_override")
        return _Caller()

    ai_routes = load_personification_module("plugin.personification.core.ai_routes")
    monkeypatch.setattr(ai_routes, "build_single_provider_caller", _build)

    state, code = asyncio.run(v2_routes._run_route_reasoning_probe(runtime, key.fingerprint))

    assert (state, code) == ("supported", "reasoning_minimal_visible_answer")
    assert captured["thinking_mode_override"] == "low"
    assert captured["tools"] == []
    assert captured["use_builtin_search"] is False
    assert "reasoning-visible-answer-should-not-persist" not in repr(registry.snapshot())


def test_reasoning_probe_without_an_official_parameter_path_is_unavailable(monkeypatch) -> None:  # noqa: ANN001
    provider = {
        "name": "plain-openai-provider",
        "api_type": "openai",
        "api_url": "https://example.test/v1",
        "api_key": "must-not-be-logged",
        "model": "gpt-4o-mini",
        "media_protocol": "auto",
    }
    registry, key, runtime = _route_probe_runtime(monkeypatch, provider=provider)
    ai_routes = load_personification_module("plugin.personification.core.ai_routes")
    monkeypatch.setattr(
        ai_routes,
        "build_single_provider_caller",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not call an unsupported path")),
    )

    state, code = asyncio.run(v2_routes._run_route_reasoning_probe(runtime, key.fingerprint))

    assert (state, code) == ("unknown", "reasoning_probe_official_path_unavailable")
    assert registry.get(key, "reasoning").verification_state.value == "probe_unavailable"


def test_reasoning_probe_records_explicit_caller_fallback_as_unsupported(monkeypatch) -> None:  # noqa: ANN001
    provider = {
        "name": "reasoning-provider",
        "api_type": "openai",
        "api_url": "https://example.test/v1",
        "api_key": "must-not-be-logged",
        "model": "gpt-5-mini",
        "media_protocol": "auto",
    }
    registry, key, runtime = _route_probe_runtime(monkeypatch, provider=provider)

    class _Caller:
        _supports_reasoning = False

        async def chat_with_tools(self, *_args, **_kwargs):  # noqa: ANN002, ANN003
            return SimpleNamespace(content="4")

    ai_routes = load_personification_module("plugin.personification.core.ai_routes")
    monkeypatch.setattr(ai_routes, "build_single_provider_caller", lambda *_args, **_kwargs: _Caller())

    state, code = asyncio.run(v2_routes._run_route_reasoning_probe(runtime, key.fingerprint))

    assert (state, code) == ("unsupported", "reasoning_probe_explicitly_unsupported")
    assert registry.get(key, "reasoning").verification_state.value == "verified"


def test_media_probe_uses_only_selected_native_route_and_does_not_save_answer(monkeypatch, tmp_path: Path) -> None:  # noqa: ANN001
    provider = {
        "name": "native-media-provider",
        "api_type": "gemini_official",
        "api_url": "https://generativelanguage.googleapis.com/v1beta",
        "api_key": "must-not-be-logged",
        "model": "gemini-2.0-flash",
        "media_protocol": "gemini_native",
    }
    registry, key, runtime = _route_probe_runtime(monkeypatch, provider=provider)
    sample = tmp_path / "sample.wav"
    sample.write_bytes(b"RIFF\x24\x00\x00\x00WAVEfmt ")
    captured: dict[str, object] = {}

    async def _fake_audio_probe(**kwargs):  # noqa: ANN003, ANN202
        probe_runtime = kwargs["runtime"]
        captured["providers"] = probe_runtime.get_configured_api_providers()
        captured["fallback_enabled"] = probe_runtime.plugin_config.personification_fallback_enabled
        captured["transcription_enabled"] = probe_runtime.plugin_config.personification_audio_transcription_enabled
        captured["sample_exists"] = Path(kwargs["audio_refs"][0]).is_file()
        return "private media answer must not be persisted", "audio_primary_native"

    media_understanding = load_personification_module("plugin.personification.core.media_understanding")
    monkeypatch.setattr(media_understanding, "analyze_audios_with_route_or_fallback", _fake_audio_probe)

    state, code = asyncio.run(
        v2_routes._run_route_media_probe(runtime, key.fingerprint, "audio_input", sample)
    )

    assert (state, code) == ("supported", "audio_input_native_media_visible_answer")
    assert captured["providers"] == [provider]
    assert captured["fallback_enabled"] is False
    assert captured["transcription_enabled"] is False
    assert captured["sample_exists"] is True
    assert "private media answer" not in repr(registry.snapshot())


def test_media_probe_without_a_declared_native_protocol_is_not_marked_unsupported(monkeypatch, tmp_path: Path) -> None:  # noqa: ANN001
    provider = {
        "name": "text-provider",
        "api_type": "openai",
        "api_url": "https://example.test/v1",
        "api_key": "must-not-be-logged",
        "model": "text-model",
        "media_protocol": "none",
    }
    registry, key, runtime = _route_probe_runtime(monkeypatch, provider=provider)
    sample = tmp_path / "sample.mp4"
    sample.write_bytes(b"\x00\x00\x00\x18ftypisom")

    state, code = asyncio.run(
        v2_routes._run_route_media_probe(runtime, key.fingerprint, "video_input", sample)
    )

    assert (state, code) == ("unknown", "media_probe_primary_route_unavailable")
    capability = registry.get(key, "video_input")
    assert capability.state.value == "unknown"
    assert capability.verification_state.value == "probe_unavailable"


def test_media_upload_probe_streams_a_bounded_sample_then_cleans_it(monkeypatch, tmp_path: Path) -> None:  # noqa: ANN001
    provider = {
        "name": "native-media-provider",
        "api_type": "gemini_official",
        "api_url": "https://generativelanguage.googleapis.com/v1beta",
        "api_key": "must-not-be-logged",
        "model": "gemini-2.0-flash",
        "media_protocol": "gemini_native",
    }
    registry, key, runtime = _route_probe_runtime(monkeypatch, provider=provider)
    probe_root = tmp_path / "health-probes"
    runtime.plugin_config.personification_health_probe_dir = str(probe_root)
    monkeypatch.setattr(v2_routes, "_ROUTE_PROBE_TASKS", {})
    captured: dict[str, object] = {}

    async def _fake_run(_runtime, route_fingerprint, capability, *, media_path=None):  # noqa: ANN001
        assert route_fingerprint == key.fingerprint
        assert capability == "audio_input"
        assert media_path is not None and media_path.is_file()
        captured["path"] = media_path
        v2_routes._record_route_probe_observation(
            key,
            capability,
            route_capabilities.CapabilityObservation.SUCCESS,
            "audio_input_native_media_visible_answer",
        )
        return "supported", "audio_input_native_media_visible_answer"

    monkeypatch.setattr(v2_routes, "_run_route_capability_probe", _fake_run)

    class _Request:
        headers = {
            "x-personification-media-filename": "admin-sample.wav",
            "content-type": "audio/wav",
            "content-length": "16",
        }

        async def stream(self):  # noqa: ANN201
            yield b"RIFF\x24\x00\x00\x00WAVEfmt "

    router = v2_routes.build_v2_router(runtime=runtime)
    endpoint = next(
        route.endpoint
        for route in router.routes
        if route.path == "/api/v2/routes/capabilities/{route_fingerprint}/probes/media"
    )

    async def _exercise() -> dict[str, object]:
        report = await endpoint(
            key.fingerprint,
            _Request(),
            capability="audio_input",
            confirmed=True,
            _=None,
        )
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        return report

    report = asyncio.run(_exercise())

    assert report["code"] == "route_probe_queued"
    assert captured["path"]
    assert not probe_root.exists()
    assert str(probe_root) not in repr(report)
    assert registry.get(key, "audio_input").verification_state.value == "verified"


def test_media_probe_helpers_require_matching_mime_suffix_and_signature() -> None:
    assert v2_routes._route_probe_media_suffix("audio_input", "sample.wav", "audio/wav") == ".wav"
    assert v2_routes._route_probe_media_suffix("audio_input", "sample.mp3", "audio/wav") is None
    assert v2_routes._route_probe_media_magic_matches(
        "audio_input", ".wav", b"RIFF\x24\x00\x00\x00WAVEfmt "
    )
    assert not v2_routes._route_probe_media_magic_matches("audio_input", ".wav", b"not-media")
