from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from types import SimpleNamespace

from ._loader import load_personification_module


def _store(tmp_path: Path):
    memory_store_mod = load_personification_module("plugin.personification.core.memory_store")
    cfg = SimpleNamespace(
        personification_data_dir=str(tmp_path),
        personification_memory_enabled=True,
        personification_memory_palace_enabled=True,
        personification_memory_recall_top_k=8,
        personification_memory_search_scan_limit=300,
    )
    store = memory_store_mod.MemoryStore(plugin_config=cfg, logger=SimpleNamespace(debug=lambda *_a, **_k: None))
    store.initialize()
    return store, cfg


def _write(store, *, memory_id: str, summary: str, group_id: str = "", user_id: str = "", permission_type: str = "public_preference"):
    store.write_memory_item(
        {
            "memory_id": memory_id,
            "memory_type": "fact",
            "summary": summary,
            "group_id": group_id,
            "user_id": user_id,
            "permission_type": permission_type,
            "confidence": 0.95,
            "salience": 0.9,
        }
    )


def test_group_recall_user_tool_never_uses_private_context(tmp_path: Path) -> None:
    runtime = load_personification_module("plugin.personification.core.services.agent_runtime")
    llm_context = load_personification_module("plugin.personification.core.llm_context")
    store, cfg = _store(tmp_path)
    _write(store, memory_id="public", summary="公开偏好：喜欢猫咪", group_id="g-current", user_id="u-current")
    _write(
        store,
        memory_id="private",
        summary="私人事实：住在测试街",
        group_id="g-current",
        user_id="u-current",
        permission_type="private_fact",
    )
    tool = runtime._build_recall_user_memory_tool(store, cfg, SimpleNamespace(debug=lambda *_a, **_k: None))
    token = llm_context.set_llm_context(group_id="g-current", user_id="u-current", purpose="reply")
    try:
        result = json.loads(asyncio.run(tool.handler(query="公开偏好 测试街")))
    finally:
        llm_context.reset_llm_context(token)

    summaries = [item["summary"] for item in result["memories"]]
    assert any("猫咪" in summary for summary in summaries)
    assert all("测试街" not in summary for summary in summaries)


def test_group_recall_includes_other_member_public_memory_only(tmp_path: Path) -> None:
    runtime = load_personification_module("plugin.personification.core.services.agent_runtime")
    llm_context = load_personification_module("plugin.personification.core.llm_context")
    store, cfg = _store(tmp_path)
    _write(store, memory_id="member-public", summary="PUBLICKEY group preference", group_id="g-current", user_id="u-other")
    _write(store, memory_id="member-private", summary="PRIVATEKEY member address", group_id="g-current", user_id="u-other", permission_type="private_fact")
    tool = runtime._build_recall_group_memory_tool(store, cfg, SimpleNamespace(debug=lambda *_a, **_k: None))
    token = llm_context.set_llm_context(group_id="g-current", user_id="u-current", purpose="reply")
    try:
        result = json.loads(asyncio.run(tool.handler(query="PUBLICKEY PRIVATEKEY")))
    finally:
        llm_context.reset_llm_context(token)
    summaries = [item["summary"] for item in result["memories"]]
    assert any("PUBLICKEY" in summary for summary in summaries), result
    assert all("PRIVATEKEY" not in summary for summary in summaries)


def test_memory_recall_ignores_model_selected_ids_and_empty_ids(tmp_path: Path) -> None:
    impl = load_personification_module("plugin.personification.skills.skillpacks.memory_palace.scripts.impl")
    llm_context = load_personification_module("plugin.personification.core.llm_context")
    store, cfg = _store(tmp_path)
    _write(store, memory_id="current", summary="当前用户喜欢蓝色列车", user_id="u-current", permission_type="private_fact")
    _write(store, memory_id="other", summary="其他用户的蓝色列车秘密", user_id="u-other", permission_type="private_fact")
    runtime = SimpleNamespace(memory_store=store, plugin_config=cfg, background_intelligence=None)
    token = llm_context.set_llm_context(user_id="u-current", purpose="reply")
    try:
        selected = json.loads(
            asyncio.run(
                impl.recall_memory(
                    runtime=runtime,
                    query="蓝色列车",
                    user_id="u-other",
                    group_id="attacker-group",
                )
            )
        )
    finally:
        llm_context.reset_llm_context(token)
    assert selected["memories"] == []
    assert "不一致" in selected["note"]

    token = llm_context.set_llm_context(purpose="reply")
    try:
        empty_identity = json.loads(
            asyncio.run(impl.recall_memory(runtime=runtime, query="蓝色列车", user_id="u-current"))
        )
    finally:
        llm_context.reset_llm_context(token)
    assert empty_identity["memories"] == []


def test_known_platform_and_bot_identity_are_filtered_with_legacy_compatibility(tmp_path: Path) -> None:
    impl = load_personification_module("plugin.personification.skills.skillpacks.memory_palace.scripts.impl")
    llm_context = load_personification_module("plugin.personification.core.llm_context")
    store, cfg = _store(tmp_path)
    _write(store, memory_id="same", summary="同一 Bot 的蓝色列车", user_id="u-current", permission_type="private_fact")
    _write(store, memory_id="other-bot", summary="另一 Bot 的蓝色列车", user_id="u-current", permission_type="private_fact")
    # New records may carry a namespace; legacy records intentionally remain
    # visible until migrated, but a conflicting known identity must not leak.
    same = store.get_memory_item("same")
    same.update({"platform": "onebot", "bot_id": "bot-current"})
    store.write_memory_item(same)
    other = store.get_memory_item("other-bot")
    other.update({"platform": "onebot", "bot_id": "bot-other"})
    store.write_memory_item(other)
    runtime = SimpleNamespace(memory_store=store, plugin_config=cfg, background_intelligence=None)
    token = llm_context.set_llm_context(user_id="u-current", platform="onebot", bot_id="bot-current", purpose="reply")
    try:
        result = json.loads(asyncio.run(impl.recall_memory(runtime=runtime, query="蓝色列车")))
    finally:
        llm_context.reset_llm_context(token)
    assert [item["memory_id"] for item in result["memories"]] == ["same"]
    assert store._payload_identity_visible({"platform": "", "bot_id": ""}, platform="onebot", bot_id="bot-current")
    assert not store._payload_identity_visible({"platform": "", "bot_id": ""}, platform="satori", bot_id="bot-current")


def test_memory_recall_is_disabled_when_memory_or_palace_is_disabled(tmp_path: Path) -> None:
    impl = load_personification_module("plugin.personification.skills.skillpacks.memory_palace.scripts.impl")
    llm_context = load_personification_module("plugin.personification.core.llm_context")
    store, cfg = _store(tmp_path)
    _write(store, memory_id="private", summary="当前用户喜欢蓝色列车", user_id="u-current", permission_type="private_fact")
    runtime = SimpleNamespace(memory_store=store, plugin_config=cfg, background_intelligence=None)
    token = llm_context.set_llm_context(user_id="u-current", purpose="reply")
    try:
        cfg.personification_memory_enabled = False
        assert json.loads(asyncio.run(impl.recall_memory(runtime=runtime, query="蓝色列车")))["memories"] == []
        cfg.personification_memory_enabled = True
        cfg.personification_memory_palace_enabled = False
        assert json.loads(asyncio.run(impl.recall_memory(runtime=runtime, query="蓝色列车")))["memories"] == []
    finally:
        llm_context.reset_llm_context(token)


def test_memory_catalog_page_searches_and_filters_expiration_in_database(tmp_path: Path) -> None:
    store, _cfg = _store(tmp_path)
    _write(store, memory_id="catalog-active", summary="CATALOG_ACTIVE", user_id="u-current")
    _write(store, memory_id="catalog-expired", summary="CATALOG_EXPIRED", user_id="u-current")
    expired = store.get_memory_item("catalog-expired")
    expired["expires_at"] = time.time() - 1
    store.write_memory_item(expired)

    active, active_total, _hidden = store.list_recent_memories_page(
        user_id="u-current", search="CATALOG", status="active", limit=20
    )
    expired_rows, expired_total, _hidden = store.list_recent_memories_page(
        user_id="u-current", search="CATALOG", status="expired", limit=20
    )

    assert active_total == 1 and [item["memory_id"] for item in active] == ["catalog-active"]
    assert expired_total == 1 and [item["memory_id"] for item in expired_rows] == ["catalog-expired"]
    assert active[0]["updated_at"] > 0


def test_private_auto_hook_can_recall_tagged_own_bot_memory_only(tmp_path: Path) -> None:
    hooks = load_personification_module("plugin.personification.core.builtin_hooks")
    llm_context = load_personification_module("plugin.personification.core.llm_context")
    store, cfg = _store(tmp_path)
    for bot_id in ("own", "other"):
        token = llm_context.set_llm_context(platform="onebot", bot_id=bot_id, user_id="u")
        try:
            _write(store, memory_id=bot_id, summary="HOOKKEY " + bot_id, user_id="u", permission_type="private_fact")
        finally:
            llm_context.reset_llm_context(token)
    ctx = SimpleNamespace(
        is_private=True, runtime=SimpleNamespace(memory_store=store), plugin_config=cfg,
        message_text="HOOKKEY", user_id="u", bot=SimpleNamespace(self_id="own"),
    )
    token = llm_context.set_llm_context(platform="onebot", bot_id="own", user_id="u")
    try:
        rendered = asyncio.run(hooks._private_memory_recall_hook(ctx))
    finally:
        llm_context.reset_llm_context(token)
    assert "HOOKKEY own" in rendered
    assert "HOOKKEY other" not in rendered
