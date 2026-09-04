from __future__ import annotations

import asyncio
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from ._loader import load_personification_module


config_mod = load_personification_module("plugin.personification.config")
config_routes = load_personification_module("plugin.personification.webui.routes.config_routes")
memory_routes = load_personification_module("plugin.personification.webui.routes.memory_routes")
persona_routes = load_personification_module("plugin.personification.webui.routes.persona_routes")
schemas = load_personification_module("plugin.personification.webui.schemas")


def _admin() -> Any:
    return schemas.AdminIdentity(qq="10001", device_id="dto-test", label="dto-test")


def _endpoint(router: Any, path: str, method: str) -> Any:
    for route in router.routes:
        if route.path == path and method in route.methods:
            return route.endpoint
    raise AssertionError(f"route not found: {method} {path}")


# ── 配置中心 DTO 合同 ───────────────────────────────────────────────────────


def test_config_entries_publish_registry_owned_ui_schemas_without_secret_echo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """前端只消费后端 schema，不能根据 field_name 猜控件类型。"""

    cfg = config_mod.Config(
        personification_api_pools=[
            {
                "name": "primary",
                "api_type": "openai",
                "api_key": "dto-contract-secret",
                "model": "test-model",
            }
        ]
    )
    monkeypatch.setattr(
        config_routes.env_writer,
        "resolve_value_sources",
        lambda field_name, plugin_config: {
            "current": getattr(plugin_config, field_name, None),
            "active_source": "default",
            "env_file": None,
            "env_json": None,
            "runtime_config": None,
            "default": getattr(type(plugin_config), field_name, None),
        },
    )
    endpoint = _endpoint(
        config_routes.build_config_router(runtime=SimpleNamespace(plugin_config=cfg)),
        "/api/config/entries",
        "GET",
    )

    response = asyncio.run(endpoint(_admin()))
    entries = {entry.field_name: entry for entry in response.entries}
    allowed_control_kinds = {
        "switch",
        "select",
        "number",
        "text",
        "textarea",
        "string_list",
        "key_value",
        "provider_list",
        "level_table",
        "behavior_band_table",
        "json_advanced",
    }

    assert all(entry.ui_schema["control_kind"] in allowed_control_kinds for entry in entries.values())

    api_pools = entries["personification_api_pools"]
    assert api_pools.ui_schema["control_kind"] == "provider_list"
    assert "api_key" in api_pools.ui_schema["sensitive_fields"]
    assert api_pools.ui_schema["item_schema"]
    assert api_pools.current[0]["api_key"] == "***"
    assert "dto-contract-secret" not in repr(response)

    model_overrides = entries["personification_model_overrides"].ui_schema
    assert model_overrides["control_kind"] == "key_value"
    assert {item["value"] for item in model_overrides["options"]} >= {
        "intent",
        "review",
        "agent",
        "sticker",
    }

    assert entries["personification_favorability_levels"].ui_schema["control_kind"] == "level_table"
    assert (
        entries["personification_favorability_behavior_bands"].ui_schema["control_kind"]
        == "behavior_band_table"
    )
    assert entries["personification_peer_bot_ids"].ui_schema["control_kind"] == "string_list"
    assert entries["personification_peer_bot_max_command_chars"].ui_schema["unit"] == "字符"


# ── 画像 DTO 合同 ───────────────────────────────────────────────────────────


@pytest.fixture
def _profile_memory_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    data_store = load_personification_module("plugin.personification.core.data_store")
    paths = load_personification_module("plugin.personification.core.paths")
    memory_store_mod = load_personification_module("plugin.personification.core.memory_store")
    profile_service_mod = load_personification_module("plugin.personification.core.profile_service")

    monkeypatch.setattr(paths, "get_data_dir", lambda _cfg=None: tmp_path)
    cfg = SimpleNamespace(
        personification_data_dir=str(tmp_path),
        personification_memory_enabled=True,
        personification_memory_palace_enabled=True,
    )
    data_store.init_data_store(cfg)
    store = memory_store_mod.MemoryStore(
        plugin_config=cfg,
        logger=SimpleNamespace(warning=lambda *_args, **_kwargs: None),
    )
    store.initialize()
    profiles = profile_service_mod.ProfileService(store)
    runtime = SimpleNamespace(
        plugin_config=cfg,
        logger=SimpleNamespace(warning=lambda *_args, **_kwargs: None),
        runtime_bundle=SimpleNamespace(
            memory_store=store,
            profile_service=profiles,
            get_bots=lambda: {},
        ),
    )
    return SimpleNamespace(runtime=runtime, store=store, profiles=profiles)


def test_persona_detail_projects_bounded_structured_fields_without_raw_profile_or_evidence(
    _profile_memory_runtime: SimpleNamespace,
) -> None:
    long_interest = "喜欢独立游戏和音乐" * 40
    _profile_memory_runtime.profiles.upsert_core_profile(
        user_id="10001",
        profile_text="【兴趣领域】：这是一段不应从详情接口泄露的完整模型输出\n【沟通风格】：轻松简短",
        profile_json={
            "structured": {
                "interests": long_interest,
                "communication_style": "轻松简短",
            },
            "user_corrections": {"称呼与昵称": "小白"},
            "scoped_profile": {
                "schema_version": 2,
                "revision": 1,
                "scope": {"kind": "global"},
                "base": {},
                "claims": [
                    {
                        "key": "interests",
                        "value": "模型生成但没有可用证据的兴趣结论",
                        "source": "global_generated",
                        "confidence": 0.83,
                        "evidence_refs": [],
                    },
                    {
                        "key": "communication_style",
                        "value": "有直接证据支持的简短表达偏好",
                        "source": "evidence_derived",
                        "confidence": 0.79,
                        "evidence_refs": [
                            {
                                "row_id": 1,
                                "message_id": "m-1",
                                "relation": "anchor",
                                "timestamp": time.time(),
                                "content_sha256": "a" * 64,
                            }
                        ],
                    },
                ],
                "evidence_windows": [
                    {"anchor": {"content": "不应回传的原始聊天证据"}}
                ],
                "generation": {"status": "success"},
            },
        },
    )
    endpoint = _endpoint(
        persona_routes.build_persona_router(runtime=_profile_memory_runtime.runtime),
        "/api/personas/{user_id}",
        "GET",
    )

    body = asyncio.run(endpoint("10001", _admin()))
    core = body["core_profile"]
    fields = {item["key"]: item for item in core["structured_fields"]}

    assert "structured" in core
    assert "profile_text" not in core
    assert "prompt_block" not in body
    assert "scoped_profile" not in core["profile_json"]
    assert "原始聊天证据" not in repr(body)
    assert fields
    assert all(
        {
            "key",
            "label",
            "category",
            "value_type",
            "value_summary",
            "source_state",
            "confidence",
            "evidence_count",
            "updated_at",
            "editable",
        }
        <= item.keys()
        for item in fields.values()
    )
    assert {item["source_state"] for item in fields.values()} <= {
        "user_correction",
        "self_claim",
        "system_observation",
        "model_inference",
        "legacy_unknown",
    }
    assert fields["interests"]["source_state"] == "legacy_unknown"
    assert fields["interests"]["evidence_count"] == 0
    assert fields["communication_style"]["source_state"] == "model_inference"
    assert fields["communication_style"]["evidence_count"] == 1
    assert fields["nickname_pref"]["source_state"] == "user_correction"
    assert fields["nickname_pref"]["editable"] is True
    assert all(len(str(item["value_summary"])) <= 160 for item in fields.values())


# ── 记忆宫殿 DTO 合同 ───────────────────────────────────────────────────────


def test_memory_palace_zones_aggregate_real_store_data_and_expose_capacity_gap(
    _profile_memory_runtime: SimpleNamespace,
) -> None:
    _profile_memory_runtime.store.write_memory_item(
        {
            "memory_id": "zone-recent-1",
            "memory_type": "episodic",
            "palace_zone": "recent_episode",
            "summary": "最近一次群聊的摘要",
            "access_count": 3,
            "last_accessed_at": 100.0,
        }
    )
    _profile_memory_runtime.store.write_memory_item(
        {
            "memory_id": "zone-recent-2",
            "memory_type": "fact",
            "palace_zone": "recent_episode",
            "summary": "刚确认的事实",
            "access_count": 5,
            "last_accessed_at": 200.0,
        }
    )
    _profile_memory_runtime.store.write_memory_item(
        {
            "memory_id": "zone-custom-1",
            "memory_type": "semantic",
            "palace_zone": "legacy_custom_zone",
            "summary": "旧版自定义分区",
            "access_count": 2,
            "last_accessed_at": 120.0,
        }
    )
    endpoint = _endpoint(
        memory_routes.build_memory_router(runtime=_profile_memory_runtime.runtime),
        "/api/memory/palace-zones",
        "GET",
    )

    body = asyncio.run(endpoint(_admin()))
    details = {item["zone_id"]: item for item in body["zone_details"]}

    assert body["schema_version"] == 2
    assert body["zones"] == ["legacy_custom_zone", "recent_episode"]
    assert {
        "zone_id",
        "name",
        "purpose",
        "status",
        "item_count",
        "tier_counts",
        "memory_type_counts",
        "total_access_count",
        "last_accessed_at",
        "last_updated_at",
        "capacity_mode",
        "capacity",
        "diagnostic_code",
    } <= details["recent_episode"].keys()
    assert details["recent_episode"]["item_count"] == 2
    assert details["recent_episode"]["memory_type_counts"] == {"episodic": 1, "fact": 1}
    assert details["recent_episode"]["total_access_count"] == 8
    assert details["recent_episode"]["last_accessed_at"] == 200.0
    assert sum(details["recent_episode"]["tier_counts"].values()) == 2
    assert details["recent_episode"]["capacity_mode"] == "not_configured"
    assert details["recent_episode"]["capacity"] is None
    assert details["recent_episode"]["status"] == "not_configured"
    assert details["recent_episode"]["diagnostic_code"] == "memory_zone_capacity_not_configured"
    assert details["legacy_custom_zone"]["name"] == "自定义分区"
    assert details["legacy_custom_zone"]["purpose"] == "未注册的历史或自定义分区。"


def test_memory_palace_zones_return_stable_unavailable_contract_without_store() -> None:
    endpoint = _endpoint(
        memory_routes.build_memory_router(
            runtime=SimpleNamespace(runtime_bundle=SimpleNamespace(memory_store=None))
        ),
        "/api/memory/palace-zones",
        "GET",
    )

    body = asyncio.run(endpoint(_admin()))

    assert body == {
        "zones": [],
        "zone_details": [],
        "schema_version": 2,
        "available": False,
        "diagnostic_code": "memory_store_unavailable",
    }


def test_memory_palace_zones_returns_empty_diagnostic_contract_when_aggregation_fails(
    tmp_path: Path,
) -> None:
    palace_dir = tmp_path / "memory_palace"
    palace_dir.mkdir()
    # An existing non-SQLite file simulates an interrupted/foreign palace
    # database.  The endpoint must report unavailable data, never fabricate
    # a zone or capacity state.
    (palace_dir / "memory_palace.db").write_bytes(b"not-a-sqlite-database")
    endpoint = _endpoint(
        memory_routes.build_memory_router(
            runtime=SimpleNamespace(
                logger=SimpleNamespace(warning=lambda *_args, **_kwargs: None),
                runtime_bundle=SimpleNamespace(
                    memory_store=SimpleNamespace(memory_palace_dir=palace_dir)
                ),
            )
        ),
        "/api/memory/palace-zones",
        "GET",
    )

    body = asyncio.run(endpoint(_admin()))

    assert body["zones"] == []
    assert body["zone_details"] == []
    assert body["schema_version"] == 2
    assert body["available"] is False
    assert body["diagnostic_code"] == "memory_zone_aggregation_unavailable"
    assert body["diagnostic"]["code"] == "memory_zones_read_failed"
