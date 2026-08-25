from __future__ import annotations

import asyncio
from types import SimpleNamespace

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
        "/api/v2/runtime/agent",
        "/api/v2/health",
        "/api/v2/test-runs/prepare",
        "/api/v2/test-runs/{operation_id}/confirm",
        "/api/v2/test-runs/{operation_id}",
        "/api/v2/tests/video-route",
        "/api/v2/tests/video-turn",
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


def test_functional_test_catalog_keeps_three_risk_levels_and_eighteen_categories() -> None:
    catalog = list(v2_routes._FUNCTIONAL_TEST_CATALOG)
    assert len(catalog) == 18
    assert {item["risk"] for item in catalog} == {"local_read", "external_read", "external_write"}
    assert v2_routes._functional_test_definition("core")["risk"] == "local_read"
    assert v2_routes._functional_test_definition("model")["risk"] == "external_read"
    assert v2_routes._functional_test_definition("qzone")["risk"] == "external_write"


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


def test_react_production_build_is_served_with_spa_fallback() -> None:
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
            }
        ]
    )
    runtime = SimpleNamespace(plugin_config=config, runtime_bundle=None)
    rows = v2_routes._config_rows(runtime, search="API Provider 池", group="")
    target = next(row for row in rows if row["field_name"] == "personification_api_pools")
    provider = dict(target["value"][0])

    assert provider["api_key"] == "***"
    assert provider["_secret_ref"]
    assert "nested-secret-must-not-leak" not in repr(target)

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
    assert saved[0]["model"] == "after-model"


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
