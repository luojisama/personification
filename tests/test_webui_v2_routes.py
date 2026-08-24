from __future__ import annotations

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


def test_v2_router_exposes_paged_trace_recovery_capability_and_sse_routes() -> None:
    router = v2_routes.build_v2_router(runtime=SimpleNamespace())
    paths = {route.path for route in router.routes}
    assert {
        "/api/v2/personas",
        "/api/v2/groups",
        "/api/v2/stickers",
        "/api/v2/stickers/index/rebuild",
        "/api/v2/config",
        "/api/v2/multimodal/routes",
        "/api/v2/traces",
        "/api/v2/traces/{trace_id}",
        "/api/v2/recovery",
        "/api/v2/recovery/counts",
        "/api/v2/model-routes/capabilities",
        "/api/v2/qzone/capabilities",
        "/api/v2/events",
    } <= paths


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
